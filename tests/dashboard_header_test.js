const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const scripts = JSON.parse(fs.readFileSync(0, "utf8"));
const elements = new Map();
function element(id) {
    if (!elements.has(id)) elements.set(id, {
        textContent: "", innerHTML: "", dataset: {}, children: [],
        style: {setProperty() {}},
        appendChild(child) {this.children.push(child);},
        contains() {return true;},
        focus() {context.document.activeElement = this;},
    });
    return elements.get(id);
}
const requests = [];
const listeners = {};
const refreshes = [];
const context = vm.createContext({
    ...Object.fromEntries([
        "previousPeriod", "nextPeriod", "currentPeriod", "label", "active", "idle",
        "tracked", "exportStart", "exportEnd", "apps", "timeline", "yAxis",
        "timeMode", "percentMode", "showAllApplications", "filterStatus", "themeSelect",
    ].map(id => [id, element(id)])),
    document: {
        documentElement: {dataset: {}},
        getElementById: element,
        createElement: () => ({}),
        querySelectorAll: selector => selector === "#periods button" ? element("periods").children : [],
        addEventListener: (name, callback) => {listeners[name] = callback;},
    },
    localStorage: {getItem() {return null;}, setItem() {}},
    addEventListener() {},
    requestAnimationFrame: callback => callback(),
    setInterval: callback => refreshes.push(callback),
    setTimeout() {},
    clearTimeout() {},
    fetch: async url => {
        if (url === "/api/update") return {ok: true, json: async () => ({
            status: "source", version: "test", message: "Source", release_url: "#",
        })};
        requests.push(url);
        const query = new URL(url, "http://localhost").searchParams;
        return {json: async () => ({
            period: query.get("period"), label: `${query.get("period")} ${query.get("offset")}`,
            start: "2026-10-19T00:00:00+00:00", end: "2026-10-20T00:00:00+00:00",
            active_seconds: 0, idle_seconds: 0, tracked_seconds: 0, apps: [], timeline: [],
        })};
    },
});
const flush = () => new Promise(resolve => setImmediate(resolve));
async function test() {
    scripts.forEach(script => vm.runInContext(script, context));
    await flush();
    const buttons = element("periods").children;
    assert.deepEqual(buttons.map(button => button.textContent), ["Hour", "Day", "Week", "Month", "Year"]);
    assert.ok(buttons.every(button => button.type === "button"));
    assert.equal(element("currentPeriod").textContent, "Today");
    assert.equal(element("nextPeriod").disabled, true);
    assert.equal(element("currentPeriod").disabled, true);
    const labels = ["This hour", "Today", "This week", "This month", "This year"];
    for (const [index, button] of buttons.entries()) {
        button.onclick();
        await flush();
        const period = button.textContent.toLowerCase();
        assert.equal(requests.at(-1), `/api/report?period=${period}&offset=0`);
        assert.equal(element("currentPeriod").textContent, labels[index]);
        assert.deepEqual(buttons.map(item => item.ariaPressed), buttons.map((_, i) => String(i === index)));
        assert.deepEqual(buttons.map(item => item.className), buttons.map((_, i) => i === index ? "active" : ""));
        for (let offset = -1; offset >= -2; offset--) {
            element("previousPeriod").onclick();
            assert.equal(element("nextPeriod").disabled, false, "State changes before the response");
            assert.equal(element("currentPeriod").disabled, false);
            await flush();
            assert.equal(requests.at(-1), `/api/report?period=${period}&offset=${offset}`);
            assert.equal(element("label").textContent, `${period} ${offset}`);
        }
        element("nextPeriod").onclick();
        await flush();
        assert.equal(requests.at(-1), `/api/report?period=${period}&offset=-1`);
        element("currentPeriod").onclick();
        await flush();
        assert.equal(requests.at(-1), `/api/report?period=${period}&offset=0`);
        assert.equal(element("currentPeriod").disabled, true);
        assert.equal(element("nextPeriod").disabled, true);
        const count = requests.length;
        element("nextPeriod").onclick();
        element("currentPeriod").onclick();
        await flush();
        assert.equal(requests.length, count, "Current-period controls cannot navigate into the future");
    }
    element("previousPeriod").onclick();
    await flush();
    buttons[1].onclick();
    await flush();
    assert.equal(requests.at(-1), "/api/report?period=day&offset=0", "Changing period resets history offset");
    const settings = element("settings");
    settings.open = true;
    let prevented = false;
    settings.onkeydown({key: "Escape", preventDefault() {prevented = true;}});
    assert.equal(prevented, true);
    assert.equal(settings.open, false);
    assert.equal(context.document.activeElement, element("settingsSummary"));
    refreshes.at(-1)();
    await flush();
    assert.equal(requests.at(-1), "/api/report?period=day&offset=0");
}
test().catch(error => {console.error(error); process.exitCode = 1;});
