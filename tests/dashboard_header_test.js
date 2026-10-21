const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const {scripts, reports} = JSON.parse(fs.readFileSync(0, "utf8"));
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
        const report = reports[query.get("period")][query.get("offset")];
        assert.ok(report, `Unexpected report request: ${url}`);
        return {json: async () => report};
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
    assert.equal(element("label").textContent, reports.day[0].label);
    assert.ok(reports.day[0].label.length >= 29, "Exercise a long real day label");
    assert.ok(reports.hour[0].label.length >= 48, "Exercise a long real hour label");
    assert.ok(reports.week[0].label.length >= 21, "Exercise a real week range label");
    const labels = ["This hour", "Today", "This week", "This month", "This year"];
    for (const [index, button] of buttons.entries()) {
        button.onclick();
        await flush();
        const period = button.textContent.toLowerCase();
        assert.equal(requests.at(-1), `/api/report?period=${period}&offset=0`);
        assert.equal(element("currentPeriod").textContent, labels[index]);
        assert.equal(element("label").textContent, reports[period][0].label);
        assert.equal(element("nextPeriod").disabled, true);
        assert.equal(element("currentPeriod").disabled, true);
        assert.deepEqual(buttons.map(item => item.ariaPressed), buttons.map((_, i) => String(i === index)));
        assert.deepEqual(buttons.map(item => item.className), buttons.map((_, i) => i === index ? "active" : ""));
        for (let offset = -1; offset >= -2; offset--) {
            element("previousPeriod").onclick();
            assert.equal(element("nextPeriod").disabled, false, "State changes before the response");
            assert.equal(element("currentPeriod").disabled, false);
            await flush();
            assert.equal(requests.at(-1), `/api/report?period=${period}&offset=${offset}`);
            assert.equal(element("label").textContent, reports[period][offset].label);
        }
        element("nextPeriod").onclick();
        await flush();
        assert.equal(requests.at(-1), `/api/report?period=${period}&offset=-1`);
        assert.equal(element("label").textContent, reports[period][-1].label);
        element("currentPeriod").onclick();
        await flush();
        assert.equal(requests.at(-1), `/api/report?period=${period}&offset=0`);
        assert.equal(element("label").textContent, reports[period][0].label);
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
