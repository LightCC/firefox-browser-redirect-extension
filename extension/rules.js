"use strict";

globalThis.RedirectRules = (() => {
  const DEFAULT_RULES = [
    "sharepoint.com",
    "*.sharepoint.com",
    "onedrive.live.com",
    "onedrive.com",
    "*.onedrive.com",
    "1drv.ms",
  ];

  function parseRules(text) {
    const rules = [];
    for (const [index, raw] of text.split(/\r?\n/).entries()) {
      const line = raw.trim().toLowerCase();
      if (!line || line.startsWith("#")) {
        continue;
      }
      const hostname = line.startsWith("*.") ? line.slice(2) : line;
      if (
        hostname.length > 253 ||
        !hostname.includes(".") ||
        !hostname.split(".").every(
          (label) =>
            label.length <= 63 &&
            /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(label),
        ) ||
        /^\d+(?:\.\d+){3}$/.test(hostname)
      ) {
        throw new Error(
          `Line ${index + 1}: use a hostname such as tenant.sharepoint.com or *.sharepoint.com, not a full URL.`,
        );
      }
      rules.push(line);
    }
    return [...new Set(rules)];
  }

  function matches(url, rules) {
    let parsed;
    try {
      parsed = new URL(url);
    } catch {
      return false;
    }
    if (!["http:", "https:"].includes(parsed.protocol)) {
      return false;
    }
    const hostname = parsed.hostname.toLowerCase().replace(/\.$/, "");
    return rules.some((rule) =>
      rule.startsWith("*.")
        ? hostname.endsWith(rule.slice(1))
        : hostname === rule,
    );
  }

  function settingsFromStorage(stored) {
    if (stored.enabled !== undefined && typeof stored.enabled !== "boolean") {
      throw new Error("Invalid saved enabled setting. Save the settings again.");
    }
    if (stored.browserPath !== undefined && typeof stored.browserPath !== "string") {
      throw new Error("Invalid saved browser path. Save the settings again.");
    }
    if (
      stored.rules !== undefined &&
      (!Array.isArray(stored.rules) ||
        !stored.rules.every((rule) => typeof rule === "string" && !/[\r\n]/.test(rule)))
    ) {
      throw new Error("Invalid saved allowlist. Save the settings again.");
    }
    return {
      enabled: stored.enabled ?? true,
      browserPath: stored.browserPath ?? "",
      rules: parseRules((stored.rules ?? DEFAULT_RULES).join("\n")),
    };
  }

  return Object.freeze({ DEFAULT_RULES, parseRules, matches, settingsFromStorage });
})();
