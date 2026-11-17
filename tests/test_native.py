import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "native"))
import host
import install


def frame(message):
    payload = json.dumps(message).encode("utf-8")
    return struct.pack("=I", len(payload)) + payload


class NativeHostTests(unittest.TestCase):
    def test_protocol_handles_partial_reads_and_unicode(self):
        class ShortReader(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 2))

        message = {"action": "open", "url": "https://work.example/file?q=\u00e9"}
        self.assertEqual(host.read_message(ShortReader(frame(message))), message)
        output = io.BytesIO()
        host.write_message(output, message)
        self.assertEqual(host.read_message(io.BytesIO(output.getvalue())), message)

    def test_protocol_rejects_truncation_oversize_and_invalid_json(self):
        for data in [
            b"\x01",
            struct.pack("=I", 10) + b"{}",
            struct.pack("=I", host.MAX_MESSAGE_BYTES + 1),
            struct.pack("=I", 1) + b"{",
            struct.pack("=I", 1) + b"\xff",
        ]:
            with self.subTest(data=data), self.assertRaises((ValueError, UnicodeError)):
                host.read_message(io.BytesIO(data))
        with self.assertRaises(EOFError):
            host.read_message(io.BytesIO())

    def test_only_valid_http_urls_are_accepted(self):
        url = "https://tenant.sharepoint.com:8443/a%20b?x=1&y=2#section"
        self.assertEqual(host.validate_url(url), url)
        for value in [
            None, 123, "", "file:///etc/passwd", "javascript:alert(1)",
            "--new-window", "https:///missing-host", "https://user:pass@work.example/",
            "https://work.example/\ncommand", "https://work.example:invalid/",
            "https://work.example:99999/", "https://[broken/", "https://work.example/" + "a" * 65536,
        ]:
            with self.subTest(value=str(value)[:80]), self.assertRaises(ValueError):
                host.validate_url(value)

    def test_unknown_messages_do_not_launch(self):
        for message in [None, [], "open", {}, {"action": "run"}]:
            with self.subTest(message=message), patch.object(host.subprocess, "Popen") as launch:
                with self.assertRaises(ValueError):
                    host.handle_message(message)
                launch.assert_not_called()

    def test_missing_edge_and_bad_explicit_paths_are_errors(self):
        with patch.object(host.shutil, "which", return_value=None), patch.object(host.sys, "platform", "linux"):
            with self.assertRaisesRegex(ValueError, "not found"):
                host.find_edge()
        for value in ["relative-edge", "/no/such/microsoft-edge"]:
            with self.assertRaisesRegex(ValueError, "absolute executable"):
                host.validate_browser_path(value)

    def test_ping_does_not_launch_and_immediate_launch_failure_is_reported(self):
        with patch.object(host, "find_edge", return_value="/usr/bin/edge"):
            with patch.object(host.subprocess, "Popen") as launch:
                self.assertEqual(host.handle_message({"action": "ping"})["browser"], "/usr/bin/edge")
                launch.assert_not_called()
                launch.return_value.wait.return_value = 1
                with self.assertRaisesRegex(ValueError, "exited with status 1"):
                    host.handle_message({"action": "open", "url": "https://work.example"})
            with patch.object(host.subprocess, "Popen", side_effect=OSError("Launch denied")):
                with self.assertRaisesRegex(OSError, "Launch denied"):
                    host.handle_message({"action": "open", "url": "https://work.example"})

    def test_running_edge_is_a_successful_handoff(self):
        with patch.object(host, "find_edge", return_value="/usr/bin/edge"):
            with patch.object(host.subprocess, "Popen") as launch:
                launch.return_value.wait.side_effect = subprocess.TimeoutExpired("edge", 0.3)
                self.assertEqual(
                    host.handle_message({"action": "open", "url": "https://work.example"}),
                    {"ok": True},
                )
                self.assertFalse(launch.call_args.kwargs.get("shell", False))
                self.assertEqual(launch.call_args.kwargs["stdin"], subprocess.DEVNULL)

    @unittest.skipIf(os.name == "nt", "Exercises the POSIX executable launcher")
    def test_installed_launcher_end_to_end_preserves_arguments_without_shell_execution(self):
        with tempfile.TemporaryDirectory(prefix="firefox redirect ' ") as temporary:
            home = Path(temporary)
            capture = home / "captured.json"
            unwanted = home / "must-not-exist"
            edge = home / "fake edge"
            edge.write_text(
                f"#!{sys.executable}\n"
                "import json, sys\n"
                "from pathlib import Path\n"
                f"Path({str(capture)!r}).write_text(json.dumps(sys.argv[1:]))\n",
                encoding="utf-8",
            )
            edge.chmod(0o700)
            with patch.object(install.Path, "home", return_value=home), patch.object(install.sys, "platform", "linux"):
                manifest_path = install.install(str(edge))
            self.assertEqual(manifest_path.parent, home / ".mozilla" / "native-messaging-hosts")
            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(manifest["allowed_extensions"], ["firefox-redirect-url@msl.local"])
            launcher = manifest["path"]
            url = f"https://work.example/document?a=1&b=$(touch {unwanted})#section"
            result = subprocess.run(
                [launcher, str(manifest_path), install.EXTENSION_ID],
                input=frame({"action": "open", "url": url}),
                capture_output=True,
                timeout=5,
                check=True,
            )
            self.assertEqual(host.read_message(io.BytesIO(result.stdout)), {"ok": True})
            self.assertEqual(json.loads(capture.read_text()), [url])
            self.assertFalse(unwanted.exists())
            for message in [{"action": "ping"}, None, {"action": "open", "url": "file:///tmp/x"}]:
                result = subprocess.run(
                    [launcher], input=frame(message), capture_output=True, timeout=5, check=True,
                )
                response = host.read_message(io.BytesIO(result.stdout))
                self.assertEqual(response["ok"], isinstance(message, dict) and message.get("action") == "ping")
                if not response["ok"]:
                    self.assertTrue(response["error"])
                    self.assertTrue(result.stderr)
            alternate = home / "alternate browser"
            alternate.write_text(edge.read_text())
            alternate.chmod(0o700)
            edge.unlink()
            capture.unlink()
            result = subprocess.run(
                [launcher],
                input=frame({"action": "open", "url": url, "browserPath": str(alternate)}),
                capture_output=True, timeout=5, check=True,
            )
            self.assertEqual(host.read_message(io.BytesIO(result.stdout)), {"ok": True})
            self.assertEqual(json.loads(capture.read_text()), [url])

    def test_installer_rejects_invalid_edge_path_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            with patch.object(install.Path, "home", return_value=home):
                with self.assertRaises(ValueError):
                    install.install("/no/such/edge")
            self.assertEqual(list(home.iterdir()), [])

    def test_macos_install_uses_firefox_manifest_location(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            with patch.object(install.Path, "home", return_value=home), patch.object(install.sys, "platform", "darwin"):
                manifest = install.install()
            self.assertEqual(
                manifest.parent,
                home / "Library" / "Application Support" / "Mozilla" / "NativeMessagingHosts",
            )

    def test_selected_browser_overrides_installer_default(self):
        with patch.object(host, "validate_browser_path", return_value="/opt/selected browser") as validate:
            with patch.object(host, "find_edge") as default, patch.object(host.subprocess, "Popen") as launch:
                launch.return_value.wait.return_value = 0
                message = {
                    "action": "open", "url": "https://work.example",
                    "browserPath": "/opt/selected browser",
                }
                self.assertEqual(host.handle_message(message, "/opt/installer-default"), {"ok": True})
                validate.assert_called_once_with("/opt/selected browser")
                default.assert_not_called()
                self.assertEqual(launch.call_args.args[0], ["/opt/selected browser", "https://work.example"])
        with patch.object(host, "validate_browser_path", return_value="/opt/installer-default") as validate:
            self.assertEqual(
                host.handle_message({"action": "ping", "browserPath": ""}, "/opt/installer-default"),
                {"ok": True, "browser": "/opt/installer-default"},
            )
            validate.assert_called_once_with("/opt/installer-default")
        for invalid in [None, 42, []]:
            with self.subTest(path=invalid), self.assertRaisesRegex(ValueError, "must be a string"):
                host.handle_message({"action": "ping", "browserPath": invalid})

    def test_browser_discovery_deduplicates_command_aliases_without_launching(self):
        with tempfile.TemporaryDirectory() as temporary:
            browser = Path(temporary) / "chromium"
            browser.write_text("#!/bin/sh\nexit 0\n")
            browser.chmod(0o700)
            with patch.object(host.shutil, "which", side_effect=lambda name: str(browser) if name in ("chromium", "chromium-browser") else None):
                with patch.object(host.sys, "platform", "linux"), patch.object(host.subprocess, "Popen") as launch:
                    response = host.handle_message({"action": "list_browsers"})
                    self.assertEqual(response, {"ok": True, "browsers": [{"name": "Chromium", "path": str(browser)}]})
                    launch.assert_not_called()
        with patch.object(host.shutil, "which", return_value=None), patch.object(host.sys, "platform", "linux"):
            self.assertEqual(host.list_browsers(), [])

    def test_linux_picker_validates_selection_and_distinguishes_cancel_from_failure(self):
        with patch.object(host.sys, "platform", "linux"), patch.object(host.shutil, "which", return_value="/usr/bin/zenity"):
            with patch.object(host.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, "/opt/browser with spaces\n", "")
                with patch.object(host, "validate_browser_path", return_value="/opt/browser with spaces") as validate:
                    self.assertEqual(
                        host.handle_message({"action": "pick_browser"}),
                        {"ok": True, "path": "/opt/browser with spaces", "cancelled": False},
                    )
                    validate.assert_called_once_with("/opt/browser with spaces")
                    self.assertEqual(run.call_args.args[0][0], "/usr/bin/zenity")
                run.return_value = subprocess.CompletedProcess([], 1, "", "")
                self.assertEqual(
                    host.handle_message({"action": "pick_browser"}),
                    {"ok": True, "path": None, "cancelled": True},
                )
                run.return_value = subprocess.CompletedProcess([], 2, "", "Cannot open display")
                with self.assertRaisesRegex(ValueError, "Cannot open display"):
                    host.pick_browser()
                run.return_value = subprocess.CompletedProcess([], 1, "", "Cannot open display")
                with self.assertRaisesRegex(ValueError, "Cannot open display"):
                    host.pick_browser()
                run.return_value = subprocess.CompletedProcess([], 0, "/no/such/browser\n", "")
                with self.assertRaisesRegex(ValueError, "absolute executable"):
                    host.pick_browser()
        with patch.object(host.sys, "platform", "linux"), patch.object(host.shutil, "which", return_value=None):
            with self.assertRaisesRegex(ValueError, "enter the executable path manually"):
                host.pick_browser()

    def test_macos_application_bundles_and_firefox_loop_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            application = Path(temporary) / "Example Browser.app"
            executable = application / "Contents" / "MacOS" / "Example"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o700)
            (application / "Contents" / "Info.plist").write_bytes(
                host.plistlib.dumps({"CFBundleExecutable": "Example"})
            )
            with patch.object(host.sys, "platform", "darwin"):
                self.assertEqual(host.validate_browser_path(str(application)), str(executable))
            firefox = Path(temporary) / ("firefox.exe" if os.name == "nt" else "firefox")
            firefox.write_text("#!/bin/sh\nexit 0\n")
            firefox.chmod(0o700)
            candidates = [firefox]
            if os.name != "nt":
                alias = Path(temporary) / "alias-browser"
                alias.symlink_to(firefox)
                candidates.append(alias)
            for candidate in candidates:
                with self.subTest(candidate=candidate), self.assertRaisesRegex(ValueError, "routing loop"):
                    host.validate_browser_path(str(candidate))


class WindowsSupportTests(unittest.TestCase):
    def test_installer_registers_current_user_manifest_and_quoted_batch_launcher(self):
        registry = MagicMock()
        with tempfile.TemporaryDirectory(prefix="browser helper ") as temporary:
            with patch.dict(os.environ, {"LOCALAPPDATA": temporary}), patch.dict(sys.modules, {"winreg": registry}):
                with patch.object(install.sys, "platform", "win32"):
                    manifest_path = install.install()
            destination = Path(temporary) / "FirefoxRedirectURL"
            self.assertEqual(manifest_path, destination / "firefox_redirect_url.json")
            manifest = json.loads(manifest_path.read_text())
            launcher = destination / "launch-host.bat"
            self.assertEqual(manifest["path"], str(launcher))
            self.assertEqual(manifest["allowed_extensions"], [install.EXTENSION_ID])
            self.assertEqual((destination / "host.py").read_bytes(), (ROOT / "native" / "host.py").read_bytes())
            batch = launcher.read_bytes()
            self.assertIn(b"\r\n", batch)
            self.assertIn(b"setlocal DisableDelayedExpansion", batch)
            self.assertIn(f'"{sys.executable}" "-u" "{destination / "host.py"}"'.encode(), batch)
            registry.CreateKeyEx.assert_called_once_with(
                registry.HKEY_CURRENT_USER,
                r"Software\Mozilla\NativeMessagingHosts\firefox_redirect_url",
                0, registry.KEY_WRITE,
            )
            registry.SetValueEx.assert_called_once_with(
                registry.CreateKeyEx.return_value.__enter__.return_value,
                "", 0, registry.REG_SZ, str(manifest_path),
            )

    def test_batch_launcher_quotes_paths_and_does_not_expand_environment_characters(self):
        command = [
            r"C:\Python & Tools\python.exe", "-u",
            r"C:\Users\100%real!\FirefoxRedirectURL\host.py",
        ]
        batch = install.windows_launcher(command)
        self.assertIn('"C:\\Python & Tools\\python.exe"', batch)
        self.assertIn('"C:\\Users\\100%%real!\\FirefoxRedirectURL\\host.py"', batch)
        self.assertIn("DisableDelayedExpansion", batch)
        for invalid in ['bad"path', "bad\npath", "bad\rpath", "bad\0path"]:
            with self.subTest(path=invalid), self.assertRaises(ValueError):
                install.windows_launcher([invalid])

    def test_missing_local_appdata_and_registration_errors_are_not_reported_as_success(self):
        with patch.object(install.sys, "platform", "win32"), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "LOCALAPPDATA"):
                install.install()
        registry = MagicMock()
        registry.CreateKeyEx.side_effect = PermissionError("Registry access denied")
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {"LOCALAPPDATA": temporary}), patch.dict(sys.modules, {"winreg": registry}):
                with patch.object(install.sys, "platform", "win32"):
                    with self.assertRaisesRegex(PermissionError, "Registry access denied"):
                        install.install()

    def test_windows_discovery_uses_standard_paths_and_registered_browsers(self):
        registry = MagicMock()
        exhausted = OSError("No more items")
        exhausted.winerror = 259
        registry.EnumKey.side_effect = ["CustomBrowser", exhausted] * 4
        registry.QueryValueEx.side_effect = [
            ("Custom Browser", 1), ('"C:\\Custom Browser\\browser.exe" --flag "%1"', 1),
        ] * 4
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(sys.modules, {"winreg": registry}), patch.dict(os.environ, {"ProgramFiles": temporary}, clear=True):
                candidates = host.windows_browser_candidates()
            self.assertIn(("Custom Browser", r"C:\Custom Browser\browser.exe"), candidates)
            edge_path = Path(temporary) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            self.assertIn(("Microsoft Edge", str(edge_path)), candidates)
            edge_path.parent.mkdir(parents=True)
            edge_path.write_bytes(b"fake executable")
            edge_path.chmod(0o700)
            with patch.object(host.sys, "platform", "win32"), patch.object(host, "windows_browser_candidates", return_value=[
                ("Microsoft Edge", str(edge_path)), ("Duplicate Edge", str(edge_path)), ("Missing browser", "/missing/browser.exe"),
            ]):
                self.assertEqual(host.list_browsers(), [{"name": "Microsoft Edge", "path": str(edge_path)}])
                self.assertEqual(host.find_edge(), str(edge_path))

    def test_registry_discovery_tolerates_absent_keys_but_surfaces_access_denied(self):
        registry = MagicMock()
        registry.OpenKey.side_effect = FileNotFoundError
        with patch.dict(sys.modules, {"winreg": registry}), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(host.windows_browser_candidates(), [])
            registry.OpenKey.side_effect = PermissionError("Registry access denied")
            with self.assertRaisesRegex(PermissionError, "Registry access denied"):
                host.windows_browser_candidates()

    def test_windows_executable_validation_rejects_shortcuts_and_scripts(self):
        with tempfile.TemporaryDirectory() as temporary:
            for name in ("browser.exe", "browser.bat", "browser.cmd", "browser.lnk"):
                path = Path(temporary) / name
                path.write_bytes(b"fake executable")
                path.chmod(0o700)
                with patch.object(host.sys, "platform", "win32"):
                    if name.endswith(".exe"):
                        self.assertEqual(host.validate_browser_path(str(path)), str(path))
                    else:
                        with self.assertRaisesRegex(ValueError, r"\.exe"):
                            host.validate_browser_path(str(path))

    def test_windows_browser_breaks_away_from_firefox_job(self):
        with patch.object(host.sys, "platform", "win32"), patch.object(host, "find_edge", return_value=r"C:\Browser\browser.exe"):
            with patch.object(host.subprocess, "Popen") as launch:
                launch.return_value.wait.return_value = 0
                self.assertEqual(host.handle_message({"action": "open", "url": "https://work.example"}), {"ok": True})
                self.assertEqual(launch.call_args.kwargs["creationflags"], 0x01000000)
                self.assertNotIn("start_new_session", launch.call_args.kwargs)
                self.assertFalse(launch.call_args.kwargs.get("shell", False))

    def test_windows_file_picker_validates_selection_and_preserves_cancellation(self):
        with patch.object(host.sys, "platform", "win32"):
            with patch.object(host, "pick_windows_browser", return_value=r"C:\Browser\browser.exe"):
                with patch.object(host, "validate_browser_path", return_value=r"C:\Browser\browser.exe") as validate:
                    self.assertEqual(host.pick_browser(), r"C:\Browser\browser.exe")
                    validate.assert_called_once_with(r"C:\Browser\browser.exe")
            with patch.object(host, "pick_windows_browser", return_value=""):
                self.assertEqual(
                    host.handle_message({"action": "pick_browser"}),
                    {"ok": True, "path": None, "cancelled": True},
                )

    def test_windows_native_dialog_uses_executable_filter_and_destroys_hidden_window(self):
        tkinter = MagicMock()
        tkinter.TclError = type("TclError", (Exception,), {})
        tkinter.filedialog.askopenfilename.return_value = r"C:\Browser\browser.exe"
        with patch.dict(sys.modules, {"tkinter": tkinter, "tkinter.filedialog": tkinter.filedialog}):
            self.assertEqual(host.pick_windows_browser(), r"C:\Browser\browser.exe")
            root = tkinter.Tk.return_value
            root.withdraw.assert_called_once()
            root.destroy.assert_called_once()
            self.assertEqual(
                tkinter.filedialog.askopenfilename.call_args.kwargs["filetypes"],
                [("Windows executables", "*.exe")],
            )
            root.reset_mock()
            tkinter.filedialog.askopenfilename.side_effect = tkinter.TclError("Dialog failed")
            with self.assertRaisesRegex(ValueError, "Dialog failed"):
                host.pick_windows_browser()
            root.destroy.assert_called_once()

    @unittest.skipUnless(os.name == "nt", "Requires Windows cmd.exe and Python")
    def test_windows_batch_launcher_protocol_round_trip(self):
        registry = MagicMock()
        with tempfile.TemporaryDirectory(prefix="Firefox helper \u00e9 & 100%! ") as temporary:
            with patch.dict(os.environ, {"LOCALAPPDATA": temporary}), patch.dict(sys.modules, {"winreg": registry}):
                manifest_path = install.install()
            launcher = json.loads(manifest_path.read_text())["path"]
            command = f'"{os.environ["COMSPEC"]}" /d /s /c ""{launcher}""'
            result = subprocess.run(
                command,
                input=frame({"action": "ping", "browserPath": sys.executable}),
                capture_output=True, timeout=10, check=True,
            )
            self.assertEqual(
                host.read_message(io.BytesIO(result.stdout)),
                {"ok": True, "browser": sys.executable},
            )


if __name__ == "__main__":
    unittest.main()
