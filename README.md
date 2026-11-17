# Firefox Redirect URL

A sideloadable Firefox extension that opens allowlisted HTTP(S) navigations in
your selected alternate browser (Microsoft Edge by default). Default hosts cover SharePoint Online, OneDrive, and `1drv.ms`
short links. You can replace that allowlist with your specific Microsoft tenant
hosts or add other sites.

## Install on your Windows PC

Requirements: a standard desktop Firefox 142 or newer, an alternate browser, and
Python 3.9 or newer. Use the normal [Windows Python installer](https://www.python.org/downloads/windows/)
with the Python launcher and Tcl/Tk components enabled. Node.js, npm, compilation,
administrator access, and changes to Firefox security settings are not required.

Run these steps **on Windows**, not on the Linux build computer. Having the repo
available on Windows provides the source files, but does not register the helper
with Windows Firefox.

1. Open PowerShell in your Windows copy of this repository. Confirm Python is
   installed, then install the current-user native helper:

   ```powershell
   py -3 --version
   py -3 .\native\install.py
   ```

   If your Python installation does not provide `py`, use `python` instead.
   The helper uses this interpreter's absolute path, so Firefox does not need
   Python on its own `PATH`. Keep this Python installation available.

   An optional helper-level default can be set during installation:

   ```powershell
   py -3 .\native\install.py --browser-path "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
   ```

2. In **Windows Firefox**, open `about:debugging#/runtime/this-firefox`, select
   **Load Temporary Add-on**, and choose `extension\manifest.json` from your
   Windows checkout. No `.xpi` build is needed for this developer-mode install.
3. Open **Firefox Redirect URL** from the extensions menu. Use **Find available
   browsers**, **Browse for executable**, or the **Target browser executable**
   path field, then **Save settings**. Select **Check browser connection** to
   validate the selection without opening a browser window. Edge is the default
   when no target is selected; paths here must refer to Windows `.exe` files.
4. Open a link such as `https://your-tenant.sharepoint.com/` in a new tab.
   The helper launches the selected browser and Firefox cancels its own navigation.

The installer copies the helper into `%LOCALAPPDATA%\FirefoxRedirectURL\` and
registers its manifest under
`HKEY_CURRENT_USER\Software\Mozilla\NativeMessagingHosts\firefox_redirect_url`.
It does not change your default browser. The helper stays installed across
Firefox restarts.

**Temporary add-ons are removed when Firefox restarts.** Repeat step 2 after each
restart and keep the extension source folder available. For persistent use in
standard Firefox, the next distribution step is a signed `.xpi` (unlisted signing
can keep it out of the public add-on catalog). Developer Edition or Nightly can
also support unsigned add-ons with the appropriate configuration; none of those
security preference changes are needed for the temporary install above.

The `--edge-path` installer option remains an alias for `--browser-path`.
After changing helper source, re-run the Windows installer and reload the add-on.
Reloading only the add-on does not update the installed helper.

If **Check browser connection** reports a missing native application, confirm
that you ran the installer under the same Windows account as Firefox. If Python
was moved or uninstalled, reinstall Python and rerun the helper installer.
If the picker reports that Tcl/Tk is missing, modify the Python installation to
include it or use the manual executable path field.

### Linux and macOS

These remain supported for optional local development. Run
`python3 native/install.py` on that machine, then load `extension/manifest.json`
through its local Firefox `about:debugging` page. Sandboxed Firefox packages
(such as Snap or Flatpak) are not supported by this installer. Installing the
helper on Linux does not register anything on your Windows PC.

## Configure routing

### Target browser

**Find available browsers** fills a drop-down from Windows browser registrations
in the current-user and machine `StartMenuInternet` registry keys (both 32-bit
and 64-bit views), plus common install locations under Program Files and local
application data. Stale entries and Firefox are excluded. Portable or unregistered
browsers may need to be selected manually.

On Linux, detection uses Firefox's `PATH`; on macOS, it also checks
`/Applications` and `~/Applications`. Common-browser detection covers Edge,
Chrome, Chromium, Brave, Vivaldi, and Opera, including some release channels.
Selecting an entry fills the path field but does not save it.

**Browse for executable** opens a native file picker through the local helper.
Windows uses Python's Tcl/Tk native dialog, filtered to `.exe` files; select the
actual browser executable, not a Start menu shortcut (`.lnk`) or a batch script.
Linux uses `zenity` or `kdialog` (one must be installed). macOS uses the system
picker and accepts `.app` bundles as well as executable files. Cancelling leaves
the current choice unchanged. Picker failures are shown in the settings page;
the manual path field is always available.

You can always enter the absolute executable path yourself, including paths
with spaces. Do not surround it with quotes or add command-line arguments.
Select only a trusted browser: the selected program will execute locally.
Nonempty paths are validated through the helper before saving with routing enabled.
Routing can still be disabled if the helper or selected browser is unavailable.
Firefox executables and symlinks to them are rejected to prevent routing loops.
Custom wrapper scripts must not route links back into this Firefox profile.

The saved selection overrides the installer's default. Leave the field blank to
use that default, or automatic Edge discovery if no installer default was set.
Browser selection, file picking, and connection checks do not launch the browser.
Only **Save settings** applies an edited selection to subsequent navigations.

### Allowlist

Open the extension settings and select **Save settings** after editing.
Routing can also be disabled there without uninstalling the helper.

```text
# Route just these tenant hosts:
your-tenant.sharepoint.com
your-tenant-my.sharepoint.com
onedrive.live.com
1drv.ms
```

Rules are **hostnames**, not full URLs. `*.sharepoint.com` matches subdomains such
as `tenant.sharepoint.com`, but not `sharepoint.com` itself and not
`sharepoint.com.attacker.example`. Exact host rules do not match subdomains.
Matching is case-insensitive, applies to HTTP and HTTPS on any port, and ignores
the path and query when deciding whether to route. The URL supplied by Firefox is
passed to the target browser unchanged, including its path, query, and any supplied fragment.
An empty allowlist routes nothing. Default settings apply immediately after load.

The default wildcard covers **all** SharePoint tenants. Use exact tenant hosts
instead if only your organization should be handed to Edge. Short links at
`1drv.ms` are handed to the target browser before their final destination is known.

## Behavior and limitations

- Intercepts top-level GET navigations in new **and existing** tabs, including
  HTTP redirects to allowlisted hosts. It does not scan tabs already loaded when
  the add-on is enabled. Same-document changes that make no network request
  (such as a fragment-only change) do not trigger interception.
- Does not route subframes, images, API calls, POST forms, or other non-GET
  requests. Transferring a URL cannot transfer a submitted form body.
- Cancels Firefox's navigation only after the helper reports a successful
  process launch. The Firefox tab is not closed: a new tab may remain blank or
  show a cancelled-navigation page; an existing tab retains its previous page.
- If the helper is missing, the target browser cannot be found, or launch fails immediately,
  the link loads in Firefox, an error notification appears, and the extension
  shows a `!` badge. This is a convenience router, **not an access-control tool**.
- A successful launch does not prove that the target browser loaded the page or signed in.
  The target browser uses its own profile, cookies, and sign-in session. Firefox's
  containers and private-browsing state are not transferred. If you grant the
  extension private-window access, matching private links also open in the
  target browser's normal profile.
- Some Microsoft desktop apps open their configured browser directly. This
  extension only sees links that actually reach Firefox.

## Components and permissions

`extension/` contains the add-on, settings page, and default allowlist.
`native/host.py` is the Python native messaging helper; `native/install.py`
installs a copy and a launcher under `%LOCALAPPDATA%\FirefoxRedirectURL\` on
Windows or `~/.local/share/firefox-redirect-url/` on Linux/macOS.
Re-run the installer after changing the helper source.

On Windows, `launch-host.bat` invokes Python in unbuffered mode for native
messaging. Browser processes use `CREATE_BREAKAWAY_FROM_JOB` so they survive
Firefox closing the helper. This follows Mozilla's
[Windows native messaging setup](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Native_messaging#windows_setup).

The native host identifier is `firefox_redirect_url`. Only the add-on ID
`firefox-redirect-url@msl.local` is allowed to connect. The host validates HTTP(S)
URLs, rejects embedded credentials, and launches an executable with an argument
array, never a shell command. No URL history, telemetry, or remote service is
used by the extension or helper.

Firefox asks for access to all HTTP(S) sites so custom host rules and redirected
navigations can be checked locally, blocking web-request access to stop matching
loads, native messaging to select and launch the target browser, local storage for settings, and
notifications for launch failures. Only allowlisted top-level URLs are handed
to the native helper. Firefox's data-consent prompt declares required
`browsingActivity` because those URLs leave Firefox for the local helper and
the target browser. This declaration does not enable telemetry or a remote routing service.

Firefox discovers the installed host manifest at:

- Windows: the current-user registry key above points to
  `%LOCALAPPDATA%\FirefoxRedirectURL\firefox_redirect_url.json`
- Linux: `~/.mozilla/native-messaging-hosts/firefox_redirect_url.json`
- macOS: `~/Library/Application Support/Mozilla/NativeMessagingHosts/firefox_redirect_url.json`

To uninstall on Windows, remove the add-on from Firefox and run this as your
normal user in PowerShell to remove its registry registration:

```powershell
Remove-Item -LiteralPath 'HKCU:\Software\Mozilla\NativeMessagingHosts\firefox_redirect_url'
```

Then delete only the installed files `host.py`, `launch-host.bat`, and
`firefox_redirect_url.json` in `%LOCALAPPDATA%\FirefoxRedirectURL\`.
On Linux/macOS, remove the add-on, the host manifest above, and the installed
`host.py` and `launch-host` files under `~/.local/share/firefox-redirect-url/`.

## Development

There are no third-party runtime or test dependencies.

```sh
npm test
```

For Python tests directly on Windows:

```powershell
py -3 -m unittest discover -s tests -p "test_*.py"
```

After editing add-on files, select **Reload** in `about:debugging`. Unit tests
cover host matching, interception decisions and failure fallback, the native
messaging protocol, browser discovery and selection, executable argument handling,
and isolated helper installation. Windows integration unit tests cover registry
registration/discovery, `.exe` validation, launch flags, and file-picker behavior.
A Windows-only batch-launcher protocol test runs when the Python suite is run on
Windows; it is skipped on Linux. A real browser-to-browser smoke test and
interactive system picker check still require the target Windows desktop session.

## License

[MIT](LICENSE). Copyright (c) 2026 Michael Scott Leiter.
