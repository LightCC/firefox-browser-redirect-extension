const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const extension = path.join(__dirname, "..", "extension");

function createHarness({ stored = {}, response = { ok: true }, nativeError, storageError } = {}) {
  const calls = { native: [], notifications: [], badges: [], errors: [] };
  let handler;
  const context = vm.createContext({
    URL,
    console: { error: (...args) => calls.errors.push(args) },
    browser: {
      storage: {
        local: {
          get: async () => {
            if (storageError) throw storageError;
            return stored;
          },
        },
      },
      runtime: {
        sendNativeMessage: async (...args) => {
          calls.native.push(args);
          if (nativeError) throw nativeError;
          return response;
        },
        getURL: (file) => `moz-extension://test/${file}`,
        openOptionsPage: async () => {},
      },
      browserAction: {
        setBadgeText: async (value) => calls.badges.push(value),
        setBadgeBackgroundColor: async () => {},
        onClicked: { addListener() {} },
      },
      notifications: {
        create: async (...args) => calls.notifications.push(args),
      },
      webRequest: {
        onBeforeRequest: {
          addListener: (listener, filter, flags) => {
            handler = { listener, filter, flags };
          },
        },
      },
    },
  });
  for (const file of ["rules.js", "background.js"]) {
    vm.runInContext(fs.readFileSync(path.join(extension, file), "utf8"), context);
  }
  return {
    rules: context.RedirectRules,
    calls,
    navigate: async (overrides = {}) => {
      const details = {
        url: "https://tenant.sharepoint.com/sites/team?file=hello%20world",
        method: "GET",
        type: "main_frame",
        tabId: 7,
        ...overrides,
      };
      if (!handler.filter.types.includes(details.type)) return {};
      assert.equal(handler.flags.includes("blocking"), true);
      return handler.listener(details);
    },
  };
}

test("hostname rules respect label boundaries, exact hosts, and URL schemes", () => {
  const { rules } = createHarness();
  const allowlist = rules.parseRules(" # comment\n*.SharePoint.com\nonedrive.live.com\n");
  for (const url of [
    "https://tenant.sharepoint.com/sites/a",
    "https://nested.tenant.sharepoint.com:8443/path",
    "HTTP://TENANT.SHAREPOINT.COM./a",
    "https://onedrive.live.com/?id=123",
  ]) {
    assert.equal(rules.matches(url, allowlist), true, url);
  }
  for (const url of [
    "https://sharepoint.com/",
    "https://evilsharepoint.com/",
    "https://tenant.sharepoint.com.attacker.example/",
    "https://onedrive.live.com@attacker.example/",
    "https://sub.onedrive.live.com/",
    "file:///tenant.sharepoint.com",
    "javascript:alert(1)",
    "not a URL",
  ]) {
    assert.equal(rules.matches(url, allowlist), false, url);
  }
});

test("allowlist parsing rejects malformed hostnames and reports the line", () => {
  const { rules } = createHarness();
  for (const invalid of [
    "https://tenant.sharepoint.com",
    "tenant.sharepoint.com/path",
    "tenant.sharepoint.com:443",
    "*sharepoint.com",
    "*.*.sharepoint.com",
    "bad_label.example",
    "-bad.example",
    "bad-.example",
    "localhost",
    "127.0.0.1",
    `${"a".repeat(64)}.example`,
  ]) {
    assert.throws(() => rules.parseRules(`# comment\n${invalid}`), /Line 2:/, invalid);
  }
  assert.equal(rules.parseRules("A.EXAMPLE\na.example\n").length, 1);
  assert.equal(rules.parseRules("# nothing\n").length, 0);
});

test("a successful handoff cancels Firefox and preserves the exact URL", async () => {
  const { navigate, calls } = createHarness();
  const url = "https://tenant.sharepoint.com/:x:/r/sites/a?file=A%20B&web=1#section";
  assert.equal((await navigate({ url })).cancel, true);
  assert.equal(calls.native.length, 1);
  assert.equal(calls.native[0][0], "firefox_redirect_url");
  assert.equal(calls.native[0][1].url, url);
  assert.equal(calls.native[0][1].action, "open");
  assert.equal(calls.notifications.length, 0);
});

test("unmatched URLs, frames, POSTs, and background requests never launch Edge", async () => {
  const { navigate, calls } = createHarness();
  for (const overrides of [
    { url: "https://example.com/" },
    { type: "sub_frame" },
    { type: "xmlhttprequest" },
    { method: "POST" },
    { tabId: -1 },
  ]) {
    assert.equal((await navigate(overrides)).cancel, undefined);
  }
  assert.equal(calls.native.length, 0);
});

test("disabled routing and an empty allowlist do not use defaults", async () => {
  for (const stored of [{ enabled: false }, { rules: [] }]) {
    const { navigate, calls } = createHarness({ stored });
    assert.equal((await navigate()).cancel, undefined);
    assert.equal(calls.native.length, 0);
  }
});

test("custom hosts replace defaults", async () => {
  const { navigate, calls } = createHarness({ stored: { rules: ["work.example"] } });
  assert.equal((await navigate()).cancel, undefined);
  assert.equal((await navigate({ url: "https://work.example/document" })).cancel, true);
  assert.equal(calls.native.length, 1);
});

test("custom browser paths are included in URL handoffs", async () => {
  const { navigate, calls } = createHarness({
    stored: { browserPath: "/opt/alternate browser/browser" },
  });
  assert.equal((await navigate()).cancel, true);
  assert.equal(calls.native[0][1].browserPath, "/opt/alternate browser/browser");
});

test("helper failures are visible and leave the navigation in Firefox", async () => {
  for (const options of [
    { nativeError: new Error("No such native application") },
    { response: { ok: false, error: "Microsoft Edge was not found." } },
    { response: null },
    { response: { ok: "true" } },
  ]) {
    const { navigate, calls } = createHarness(options);
    assert.equal((await navigate()).cancel, undefined);
    assert.equal(calls.notifications.length, 1);
    assert.match(calls.notifications[0][1].message, /stay in Firefox/);
    assert.equal(calls.badges[0].text, "!");
    assert.equal(calls.errors.length, 1);
  }
});

test("storage errors and invalid persisted settings fail visibly without launching", async () => {
  for (const options of [
    { storageError: new Error("Storage unavailable") },
    { stored: { enabled: "yes" } },
    { stored: { browserPath: 123 } },
    { stored: { rules: ["https://example.com"] } },
    { stored: { rules: "example.com" } },
    { stored: { rules: ["example.com\nother.example"] } },
  ]) {
    const { navigate, calls } = createHarness(options);
    assert.equal((await navigate()).cancel, undefined);
    assert.equal(calls.native.length, 0);
    assert.equal(calls.notifications.length, 1);
  }
});

test("Firefox waits for helper confirmation before cancelling the request", async () => {
  let confirmLaunch;
  const response = new Promise((resolve) => { confirmLaunch = resolve; });
  const { navigate, calls } = createHarness({ response });
  let settled = false;
  const pending = navigate().then((result) => {
    settled = true;
    return result;
  });
  await new Promise(setImmediate);
  assert.equal(calls.native.length, 1);
  assert.equal(settled, false);
  confirmLaunch({ ok: true });
  assert.equal((await pending).cancel, true);
});
