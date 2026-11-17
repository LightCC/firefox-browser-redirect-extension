#!/usr/bin/env python3
"""Firefox native messaging host. Standard output is reserved for framed JSON."""

import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import struct
import subprocess
import sys
from urllib.parse import urlsplit
from xml.parsers.expat import ExpatError


MAX_MESSAGE_BYTES = 128 * 1024
BROWSERS = (
    ("Microsoft Edge", ("microsoft-edge-stable", "microsoft-edge"), "Microsoft Edge"),
    ("Microsoft Edge Beta", ("microsoft-edge-beta",), "Microsoft Edge Beta"),
    ("Microsoft Edge Dev", ("microsoft-edge-dev",), "Microsoft Edge Dev"),
    ("Google Chrome", ("google-chrome-stable", "google-chrome"), "Google Chrome"),
    ("Google Chrome Beta", ("google-chrome-beta",), "Google Chrome Beta"),
    ("Chromium", ("chromium", "chromium-browser"), "Chromium"),
    ("Brave", ("brave-browser", "brave"), "Brave Browser"),
    ("Vivaldi", ("vivaldi-stable", "vivaldi"), "Vivaldi"),
    ("Opera", ("opera",), "Opera"),
)
WINDOWS_BROWSER_PATHS = (
    ("Microsoft Edge", "Microsoft/Edge/Application/msedge.exe"),
    ("Microsoft Edge Beta", "Microsoft/Edge Beta/Application/msedge.exe"),
    ("Microsoft Edge Dev", "Microsoft/Edge Dev/Application/msedge.exe"),
    ("Google Chrome", "Google/Chrome/Application/chrome.exe"),
    ("Google Chrome Beta", "Google/Chrome Beta/Application/chrome.exe"),
    ("Chromium", "Chromium/Application/chrome.exe"),
    ("Brave", "BraveSoftware/Brave-Browser/Application/brave.exe"),
    ("Vivaldi", "Vivaldi/Application/vivaldi.exe"),
    ("Opera", "Programs/Opera/opera.exe"),
)
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def read_exact(stream, size):
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            raise ValueError("Incomplete native message.")
        data.extend(chunk)
    return bytes(data)


def read_message(stream):
    first = stream.read(1)
    if not first:
        raise EOFError
    header = first + read_exact(stream, 3)
    size = struct.unpack("=I", header)[0]
    if size > MAX_MESSAGE_BYTES:
        raise ValueError("Native message exceeds the size limit.")
    return json.loads(read_exact(stream, size).decode("utf-8"))


def write_message(stream, message):
    payload = json.dumps(message).encode("utf-8")
    stream.write(struct.pack("=I", len(payload)))
    stream.write(payload)
    stream.flush()


def validate_browser_path(value):
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("The browser path must be an absolute executable file.")
    candidate = Path(value)
    if sys.platform == "darwin" and candidate.suffix == ".app" and candidate.is_dir():
        try:
            with (candidate / "Contents" / "Info.plist").open("rb") as source:
                metadata = plistlib.load(source)
        except (plistlib.InvalidFileException, ExpatError) as error:
            raise ValueError("The selected application has an invalid Info.plist.") from error
        executable = metadata.get("CFBundleExecutable") if isinstance(metadata, dict) else None
        if not isinstance(executable, str) or not executable or Path(executable).name != executable:
            raise ValueError("The selected application has no valid browser executable.")
        candidate = candidate / "Contents" / "MacOS" / executable
    if not candidate.is_absolute() or not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise ValueError("The browser path must be an absolute executable file.")
    if sys.platform == "win32" and candidate.suffix.lower() != ".exe":
        raise ValueError("Select the browser's .exe file, not a shortcut, script, or command line.")
    if any(path.name.lower().startswith("firefox") for path in (candidate, candidate.resolve())):
        raise ValueError("Choose a browser other than Firefox to prevent a routing loop.")
    return str(candidate)


def find_edge():
    if sys.platform == "win32":
        for browser in list_browsers():
            if Path(browser["path"]).name.lower() == "msedge.exe":
                return validate_browser_path(browser["path"])
    for name in ("microsoft-edge-stable", "microsoft-edge", "microsoft-edge-beta", "microsoft-edge-dev"):
        candidate = shutil.which(name)
        if candidate:
            return validate_browser_path(candidate)
    candidate = Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
    if sys.platform == "darwin" and candidate.is_file() and os.access(candidate, os.X_OK):
        return validate_browser_path(str(candidate))
    raise ValueError("Microsoft Edge was not found. Choose another browser in the extension settings.")


def windows_browser_candidates():
    import winreg

    candidates = []
    for name, relative_path in WINDOWS_BROWSER_PATHS:
        for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            directory = os.environ.get(variable)
            if directory:
                candidates.append((name, str(Path(directory) / relative_path)))
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(root, r"SOFTWARE\Clients\StartMenuInternet", 0, winreg.KEY_READ | view) as clients:
                    index = 0
                    while True:
                        try:
                            client = winreg.EnumKey(clients, index)
                        except OSError as error:
                            if getattr(error, "winerror", None) == 259:  # ERROR_NO_MORE_ITEMS
                                break
                            raise
                        index += 1
                        try:
                            with winreg.OpenKey(clients, client) as key:
                                try:
                                    name, _ = winreg.QueryValueEx(key, "")
                                except FileNotFoundError:
                                    name = client
                            with winreg.OpenKey(clients, client + r"\shell\open\command") as key:
                                command, _ = winreg.QueryValueEx(key, "")
                        except FileNotFoundError:
                            continue
                        if not isinstance(name, str) or not isinstance(command, str):
                            continue
                        command = os.path.expandvars(command).strip()
                        if command.startswith('"'):
                            executable, closing_quote, _ = command[1:].partition('"')
                            if not closing_quote:
                                continue
                        else:
                            executable = command.split(maxsplit=1)[0] if command else ""
                        if executable:
                            candidates.append((name, executable))
            except FileNotFoundError:
                continue
    return candidates


def list_browsers():
    browsers = []
    seen = set()
    if sys.platform == "win32":
        candidates = windows_browser_candidates()
    else:
        candidates = []
        for name, commands, application in BROWSERS:
            candidates.extend((name, shutil.which(command)) for command in commands)
            if sys.platform == "darwin":
                candidates.extend(
                    (name, str(directory / f"{application}.app" / "Contents" / "MacOS" / application))
                    for directory in (Path("/Applications"), Path.home() / "Applications")
                )
    for name, value in candidates:
        if not value:
            continue
        candidate = Path(value)
        if not candidate.is_absolute() or not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue
        if sys.platform == "win32" and candidate.suffix.lower() != ".exe":
            continue
        resolved = candidate.resolve()
        if resolved in seen or any(path.name.lower().startswith("firefox") for path in (candidate, resolved)):
            continue
        seen.add(resolved)
        browsers.append({"name": name, "path": str(candidate)})
    return browsers


def pick_windows_browser():
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError as error:
        raise ValueError("The file picker needs Python's Tcl/Tk component. Install it or enter the .exe path manually.") from error
    try:
        root = tkinter.Tk()
        try:
            root.withdraw()
            root.attributes("-topmost", True)
            return filedialog.askopenfilename(
                parent=root,
                title="Choose target browser executable",
                initialdir=os.environ.get("ProgramFiles", "C:\\"),
                filetypes=[("Windows executables", "*.exe")],
            )
        finally:
            root.destroy()
    except tkinter.TclError as error:
        raise ValueError(f"Could not open the Windows executable picker: {error}") from error


def pick_browser():
    if sys.platform == "win32":
        selected = pick_windows_browser()
        return validate_browser_path(selected) if selected else None
    if sys.platform == "darwin":
        command = [
            "/usr/bin/osascript", "-e",
            'try\n'
            'return POSIX path of (choose file with prompt "Choose a browser application or executable" '
            'with showing package contents)\n'
            'on error number -128\nreturn ""\nend try',
        ]
    elif sys.platform == "linux":
        if picker := shutil.which("zenity"):
            command = [picker, "--file-selection", "--title=Choose target browser executable", "--filename=/usr/bin/"]
        elif picker := shutil.which("kdialog"):
            command = [picker, "--getopenfilename", "/usr/bin/", "*", "--title", "Choose target browser executable"]
        else:
            raise ValueError("No file picker found. Install zenity or kdialog, or enter the executable path manually.")
    else:
        raise ValueError("The executable picker supports Windows, Linux, and macOS only. Enter the path manually.")
    result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)
    if sys.platform == "linux" and result.returncode == 1 and not result.stderr.strip():
        return None
    if result.returncode != 0:
        raise ValueError(f"Could not open the executable picker: {result.stderr.strip()[:500]}")
    selected = result.stdout.rstrip("\r\n")
    return validate_browser_path(selected) if selected else None


def validate_url(url):
    if not isinstance(url, str) or not url or len(url) > 65536:
        raise ValueError("Expected an HTTP(S) URL of at most 65536 characters.")
    if any(ord(character) < 32 or ord(character) == 127 for character in url):
        raise ValueError("Control characters are not permitted in URLs.")
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Only absolute HTTP(S) URLs can be opened.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs containing embedded credentials cannot be handed to another browser.")
    # Accessing port also validates malformed or out-of-range ports.
    _ = parsed.port
    return url


def handle_message(message, browser_path=None):
    if not isinstance(message, dict):
        raise ValueError("Expected a JSON object.")
    action = message.get("action")
    if action == "list_browsers":
        return {"ok": True, "browsers": list_browsers()}
    if action == "pick_browser":
        selected = pick_browser()
        return {"ok": True, "path": selected, "cancelled": selected is None}
    if action not in ("ping", "open"):
        raise ValueError("Unknown action. Expected ping, open, list_browsers, or pick_browser.")
    url = validate_url(message.get("url")) if action == "open" else None
    selected = message.get("browserPath", "")
    if not isinstance(selected, str):
        raise ValueError("The browser path must be a string.")
    selected = selected or browser_path
    browser = validate_browser_path(selected) if selected else find_edge()
    if action == "ping":
        return {"ok": True, "browser": browser}
    # Never use a shell, and do not let the browser inherit native messaging pipes.
    # Firefox kills the native host's Windows job after receiving its response.
    launch_options = (
        {"creationflags": CREATE_BREAKAWAY_FROM_JOB}
        if sys.platform == "win32"
        else {"start_new_session": True}
    )
    process = subprocess.Popen(
        [browser, url],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **launch_options,
    )
    try:
        returncode = process.wait(timeout=0.3)
    except subprocess.TimeoutExpired:
        return {"ok": True}
    if returncode != 0:
        raise ValueError(f"The target browser exited with status {returncode}. Check its installation.")
    return {"ok": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser-path", "--edge-path", dest="browser_path")
    # Firefox supplies the native manifest and extension ID as positional arguments.
    parser.add_argument("browser_arguments", nargs="*")
    args = parser.parse_args()
    try:
        message = read_message(sys.stdin.buffer)
        response = handle_message(message, args.browser_path)
    except EOFError:
        return
    except (ValueError, OSError, UnicodeError, RecursionError) as error:
        print(f"Firefox Redirect URL: {error}", file=sys.stderr)
        response = {"ok": False, "error": str(error)}
    write_message(sys.stdout.buffer, response)


if __name__ == "__main__":
    main()
