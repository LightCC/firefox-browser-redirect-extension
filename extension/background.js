"use strict";

const HOST_NAME = "firefox_redirect_url";

async function reportError(error) {
  console.error("Firefox Redirect URL:", error);
  try {
    await browser.browserAction.setBadgeText({ text: "!" });
    await browser.browserAction.setBadgeBackgroundColor({ color: "#b42318" });
    await browser.notifications.create("firefox-redirect-url-error", {
      type: "basic",
      iconUrl: browser.runtime.getURL("icon.svg"),
      title: "Could not open target browser",
      message: `${error.message || String(error)} The link will stay in Firefox. Open the extension settings to check the helper.`,
    });
  } catch (notificationError) {
    console.error("Could not display routing error:", notificationError);
  }
}

async function routeRequest(details) {
  // A URL handoff cannot preserve a form body or replay a POST safely.
  if (details.tabId < 0 || details.method !== "GET") {
    return {};
  }
  try {
    const settings = RedirectRules.settingsFromStorage(
      await browser.storage.local.get(["enabled", "rules", "browserPath"]),
    );
    if (!settings.enabled || !RedirectRules.matches(details.url, settings.rules)) {
      return {};
    }
    const response = await browser.runtime.sendNativeMessage(HOST_NAME, {
      action: "open",
      url: details.url,
      browserPath: settings.browserPath,
    });
    if (response?.ok !== true) {
      throw new Error(response?.error || "The native helper did not confirm the launch.");
    }
    await browser.browserAction.setBadgeText({ text: "" }).catch(console.error);
    return { cancel: true };
  } catch (error) {
    await reportError(error);
    return {};
  }
}

browser.webRequest.onBeforeRequest.addListener(
  routeRequest,
  { urls: ["http://*/*", "https://*/*"], types: ["main_frame"] },
  ["blocking"],
);

browser.browserAction.onClicked.addListener(() => browser.runtime.openOptionsPage());
