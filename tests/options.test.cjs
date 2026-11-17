const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function createOptions({ stored = {}, saveError, response = { ok: true, browser: "/usr/bin/edge" }, nativeError } = {}) {
  const elements = new Map();
  const saved = [];
  const messages = [];
  for (const id of [
    "settings", "controls", "enabled", "rules", "status", "defaults", "check",
    "browser-path", "browsers", "detect", "browse",
  ]) {
    elements.set(id, {
      value: "",
      checked: false,
      dataset: {},
      events: {},
      options: [],
      addEventListener(event, callback) { this.events[event] = callback; },
      replaceChildren(...options) { this.options = options; },
      add(option) { this.options.push(option); },
    });
  }
  const context = vm.createContext({
    URL,
    Option: function(text, value) { this.text = text; this.value = value; },
    document: { getElementById: (id) => elements.get(id) },
    browser: {
      storage: {
        local: {
          get: async () => stored,
          set: async (value) => {
            if (saveError) throw saveError;
            saved.push(value);
          },
        },
      },
      runtime: {
        sendNativeMessage: async (host, message) => {
          messages.push(message);
          if (nativeError) throw nativeError;
          return response;
        },
      },
    },
  });
  for (const file of ["rules.js", "options.js"]) {
    vm.runInContext(
      fs.readFileSync(path.join(__dirname, "..", "extension", file), "utf8"),
      context,
    );
  }
  return {
    elements,
    saved,
    messages,
    click: (id) => elements.get(id).events.click(),
    save: () => elements.get("settings").events.submit({ preventDefault() {} }),
  };
}

test("settings save normalized custom rules and preserve the enabled toggle", async () => {
  const { elements, saved, save } = createOptions({ stored: { enabled: false } });
  await new Promise(setImmediate);
  assert.equal(elements.get("enabled").checked, false);
  elements.get("rules").value = "Work.Example\nwork.example\n";
  await save();
  assert.equal(saved.length, 1);
  assert.equal(saved[0].enabled, false);
  assert.equal(saved[0].rules.join("\n"), "work.example");
  assert.match(elements.get("status").textContent, /saved/);
});

test("invalid rules and storage failures are displayed without reporting success", async () => {
  for (const saveError of [undefined, new Error("Storage is full")]) {
    const { elements, saved, save } = createOptions({ saveError });
    await new Promise(setImmediate);
    elements.get("rules").value = saveError ? "work.example" : "https://work.example";
    await save();
    assert.equal(saved.length, 0);
    assert.equal(elements.get("status").dataset.error, "true");
    assert.match(elements.get("status").textContent, /Could not save/);
  }
});

test("restoring defaults does not persist until Save is selected", async () => {
  const { elements, saved, click } = createOptions({ stored: { rules: [] } });
  await new Promise(setImmediate);
  assert.equal(elements.get("rules").value, "");
  click("defaults");
  assert.match(elements.get("rules").value, /\*\.sharepoint\.com/);
  assert.equal(saved.length, 0);
  assert.match(elements.get("status").textContent, /Save settings to apply/);
});

test("connection check validates the edited browser path and displays errors", async () => {
  for (const ok of [true, false]) {
    const { elements, click, messages } = createOptions({
      response: ok ? { ok: true, browser: "/usr/bin/chromium" } : { ok: false, error: "Browser was not found" },
    });
    await new Promise(setImmediate);
    elements.get("browser-path").value = "/usr/bin/chromium";
    await click("check");
    assert.equal(messages[0].browserPath, "/usr/bin/chromium");
    assert.equal(elements.get("status").dataset.error, String(!ok));
    assert.match(elements.get("status").textContent, ok ? /Browser found at/ : /Browser was not found/);
  }
});

test("saved target browser is restored and manual paths are validated before saving", async () => {
  const { elements, saved, save, messages } = createOptions({
    stored: { browserPath: "/opt/old browser/browser" },
    response: { ok: true, browser: "/opt/new browser/browser" },
  });
  await new Promise(setImmediate);
  assert.equal(elements.get("browser-path").value, "/opt/old browser/browser");
  elements.get("browser-path").value = " /opt/new browser/browser ";
  await save();
  assert.equal(messages[0].action, "ping");
  assert.equal(messages[0].browserPath, "/opt/new browser/browser");
  assert.equal(saved[0].browserPath, "/opt/new browser/browser");
});

test("browser validation failures do not replace saved settings", async () => {
  const { elements, saved, save } = createOptions({
    response: { ok: false, error: "Choose an absolute executable file" },
  });
  await new Promise(setImmediate);
  elements.get("browser-path").value = "/missing/browser";
  await save();
  assert.equal(saved.length, 0);
  assert.match(elements.get("status").textContent, /absolute executable/);
  assert.equal(elements.get("status").dataset.error, "true");
});

test("routing can be disabled even when the saved browser is no longer available", async () => {
  const { elements, saved, save, messages } = createOptions({
    stored: { browserPath: "/missing/browser" },
    nativeError: new Error("Helper unavailable"),
  });
  await new Promise(setImmediate);
  elements.get("enabled").checked = false;
  await save();
  assert.equal(messages.length, 0);
  assert.equal(saved[0].enabled, false);
  assert.equal(saved[0].browserPath, "/missing/browser");
});

test("detected browser list populates the editor without saving", async () => {
  const { elements, saved, click, messages } = createOptions({
    response: { ok: true, browsers: [{ name: "Chromium", path: "/usr/bin/chromium" }] },
  });
  await new Promise(setImmediate);
  await click("detect");
  assert.equal(messages[0].action, "list_browsers");
  const list = elements.get("browsers");
  assert.equal(list.disabled, false);
  assert.equal(list.options[1].value, "/usr/bin/chromium");
  list.value = list.options[1].value;
  list.events.change();
  assert.equal(elements.get("browser-path").value, "/usr/bin/chromium");
  assert.equal(saved.length, 0);
});

test("no detected browsers leaves manual entry and browsing available", async () => {
  const { elements, click } = createOptions({ response: { ok: true, browsers: [] } });
  await new Promise(setImmediate);
  await click("detect");
  assert.equal(elements.get("browsers").disabled, true);
  assert.equal(elements.get("detect").disabled, false);
  assert.match(elements.get("status").textContent, /No common browsers detected/);
});

test("picker populates the path, while cancellation preserves the current selection", async () => {
  for (const cancelled of [true, false]) {
    const { elements, saved, click, messages } = createOptions({
      stored: { browserPath: "/opt/old-browser" },
      response: cancelled ? { ok: true, cancelled: true, path: null } : { ok: true, path: "/opt/new-browser" },
    });
    await new Promise(setImmediate);
    await click("browse");
    assert.equal(messages[0].action, "pick_browser");
    assert.equal(elements.get("browser-path").value, cancelled ? "/opt/old-browser" : "/opt/new-browser");
    assert.equal(elements.get("browse").disabled, false);
    assert.equal(saved.length, 0);
  }
});

test("detection and picker errors are visible without clearing the selected path", async () => {
  for (const action of ["detect", "browse"]) {
    const { elements, click } = createOptions({
      stored: { browserPath: "/opt/browser" },
      nativeError: new Error("Helper unavailable"),
    });
    await new Promise(setImmediate);
    await click(action);
    assert.equal(elements.get(action).disabled, false);
    assert.equal(elements.get("browser-path").value, "/opt/browser");
    assert.equal(elements.get("status").dataset.error, "true");
    assert.match(elements.get("status").textContent, /Helper unavailable/);
  }
});
