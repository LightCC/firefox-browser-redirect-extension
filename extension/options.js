"use strict";

const form = document.getElementById("settings");
const controls = document.getElementById("controls");
const enabled = document.getElementById("enabled");
const rules = document.getElementById("rules");
const status = document.getElementById("status");
const browserPath = document.getElementById("browser-path");
const browsers = document.getElementById("browsers");
const detect = document.getElementById("detect");
const browse = document.getElementById("browse");

async function nativeMessage(message) {
  const response = await browser.runtime.sendNativeMessage("firefox_redirect_url", message);
  if (response?.ok !== true) {
    throw new Error(response?.error || "Unexpected response from the native helper. Reinstall the helper if it is out of date.");
  }
  return response;
}

async function checkBrowser() {
  const response = await nativeMessage({
    action: "ping",
    browserPath: browserPath.value.trim(),
  });
  if (typeof response.browser !== "string" || !response.browser) {
    throw new Error("The helper is out of date. Reinstall it from native/install.py.");
  }
  return response.browser;
}

function showStatus(message, isError = false) {
  status.textContent = message;
  status.dataset.error = String(isError);
}

async function loadSettings() {
  try {
    const settings = RedirectRules.settingsFromStorage(
      await browser.storage.local.get(["enabled", "rules", "browserPath"]),
    );
    enabled.checked = settings.enabled;
    rules.value = settings.rules.join("\n");
    browserPath.value = settings.browserPath;
    controls.disabled = false;
  } catch (error) {
    // Keep recovery available without silently overwriting corrupt saved settings.
    controls.disabled = false;
    showStatus(`Could not load settings: ${error.message}`, true);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const parsedRules = RedirectRules.parseRules(rules.value);
    const selectedPath = browserPath.value.trim();
    if (selectedPath && enabled.checked) {
      await checkBrowser();
    }
    await browser.storage.local.set({
      enabled: enabled.checked, rules: parsedRules, browserPath: selectedPath,
    });
    showStatus("Settings saved.");
  } catch (error) {
    showStatus(`Could not save settings: ${error.message}`, true);
  }
});

document.getElementById("defaults").addEventListener("click", () => {
  rules.value = RedirectRules.DEFAULT_RULES.join("\n");
  showStatus("Default allowlist restored in the editor. Select Save settings to apply.");
});

document.getElementById("check").addEventListener("click", async () => {
  showStatus("Checking the local helper and target browser...");
  try {
    const path = await checkBrowser();
    showStatus(`Helper connected. Browser found at ${path}`);
  } catch (error) {
    showStatus(`Connection failed: ${error.message}. See the README installation steps.`, true);
  }
});

detect.addEventListener("click", async () => {
  detect.disabled = true;
  browsers.disabled = true;
  browsers.replaceChildren(new Option("Searching...", ""));
  showStatus("Looking for installed browsers...");
  try {
    const response = await nativeMessage({ action: "list_browsers" });
    if (
      !Array.isArray(response.browsers) ||
      !response.browsers.every((browser) =>
        typeof browser?.name === "string" && typeof browser?.path === "string" && browser.path,
      )
    ) {
      throw new Error("Invalid browser list from the helper.");
    }
    browsers.replaceChildren(new Option("Select a detected browser", ""));
    for (const browser of response.browsers) {
      browsers.add(new Option(`${browser.name} - ${browser.path}`, browser.path));
    }
    browsers.disabled = response.browsers.length === 0;
    showStatus(response.browsers.length
      ? "Select a browser from the list, then save settings."
      : "No common browsers detected. Browse for an executable or enter its absolute path.");
  } catch (error) {
    browsers.replaceChildren(new Option("Browser detection failed", ""));
    showStatus(`Could not find browsers: ${error.message}`, true);
  } finally {
    detect.disabled = false;
  }
});

browsers.addEventListener("change", () => {
  if (browsers.value) {
    browserPath.value = browsers.value;
    showStatus("Browser selected. Select Save settings to apply.");
  }
});

browse.addEventListener("click", async () => {
  browse.disabled = true;
  showStatus("Choose the target browser in the system file picker...");
  try {
    const response = await nativeMessage({ action: "pick_browser" });
    if (response.cancelled === true) {
      showStatus("Selection cancelled. The browser path was not changed.");
    } else if (typeof response.path === "string" && response.path) {
      browserPath.value = response.path;
      showStatus("Executable selected. Select Save settings to apply.");
    } else {
      throw new Error("Invalid executable selection from the helper.");
    }
  } catch (error) {
    showStatus(`Could not select executable: ${error.message}. You can enter the path manually.`, true);
  } finally {
    browse.disabled = false;
  }
});

loadSettings();
