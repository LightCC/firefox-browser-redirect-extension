#!/usr/bin/env python3
"""Install the native helper for the current user's standard Firefox installation."""

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import sys

from host import validate_browser_path


HOST_NAME = "firefox_redirect_url"
EXTENSION_ID = "firefox-redirect-url@msl.local"


def windows_launcher(command):
    quoted = []
    for argument in command:
        if any(character in argument for character in ('"', "\r", "\n", "\0")):
            raise ValueError("The installation path contains unsupported command characters.")
        quoted.append('"' + argument.replace("%", "%%") + '"')
    return (
        "@echo off\n"
        "setlocal DisableDelayedExpansion\n"
        "chcp 65001 >nul\n"
        + " ".join(quoted) + "\n"
    )


def install(browser_path=None):
    if sys.platform not in ("win32", "linux", "darwin"):
        raise ValueError("This installer supports Windows, Linux, and macOS.")
    if browser_path:
        browser_path = validate_browser_path(browser_path)
    if sys.platform == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA")
        if not local_appdata or not Path(local_appdata).is_absolute():
            raise ValueError("LOCALAPPDATA must contain an absolute current-user application-data path.")
        destination = Path(local_appdata) / "FirefoxRedirectURL"
        manifest_directory = destination
    else:
        home = Path.home()
        destination = home / ".local" / "share" / "firefox-redirect-url"
        manifest_directory = (
            home / "Library" / "Application Support" / "Mozilla" / "NativeMessagingHosts"
            if sys.platform == "darwin"
            else home / ".mozilla" / "native-messaging-hosts"
        )
    destination.mkdir(parents=True, exist_ok=True)
    manifest_directory.mkdir(parents=True, exist_ok=True)
    host = destination / "host.py"
    shutil.copyfile(Path(__file__).with_name("host.py"), host)
    launcher = destination / ("launch-host.bat" if sys.platform == "win32" else "launch-host")
    command = [sys.executable, "-u", str(host)]
    if browser_path:
        command.extend(["--browser-path", browser_path])
    if sys.platform == "win32":
        with launcher.open("w", encoding="utf-8", newline="\r\n") as output:
            output.write(windows_launcher(command))
    else:
        launcher.write_text(f'#!/bin/sh\nexec {shlex.join(command)} "$@"\n', encoding="utf-8")
        launcher.chmod(0o700)
    manifest = manifest_directory / f"{HOST_NAME}.json"
    manifest.write_text(
        json.dumps(
            {
                "name": HOST_NAME,
                "description": "Launch allowlisted Firefox links in a selected browser",
                "path": str(launcher),
                "type": "stdio",
                "allowed_extensions": [EXTENSION_ID],
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    if sys.platform == "win32":
        import winreg

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            rf"Software\Mozilla\NativeMessagingHosts\{HOST_NAME}",
            0, winreg.KEY_WRITE,
        ) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--browser-path", "--edge-path", dest="browser_path",
        help="Default browser executable path (can be overridden in extension settings)",
    )
    args = parser.parse_args()
    try:
        manifest = install(args.browser_path)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Installation failed: {error}\n")
    print(f"Installed native messaging manifest: {manifest}")
    print("Load extension/manifest.json in Firefox, then choose a target browser in its settings.")


if __name__ == "__main__":
    main()
