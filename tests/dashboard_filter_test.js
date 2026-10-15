const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const scripts = JSON.parse(fs.readFileSync(0, "utf8"));
assert.ok(
    Array.isArray(scripts) && scripts.length > 0 && scripts.every(script => typeof script === "string"),
    "Expected dashboard scripts extracted by the HTML parser",
);
const elements = new Map();
function element(id) {
    if (!elements.has(id)) {
        elements.set(id, {
            innerHTML: "",
            textContent: "",
            value: "",
            className: "",
            disabled: false,
            dataset: {},
            options: [],
            style: {setProperty() {}},
            appendChild(child) {this.options.push(child);},
            add(option) {this.options.push(option);},
            replaceChildren(...children) {this.options = children;},
            contains: () => true,
        });
    }
    return elements.get(id);
}

const report = {
    period: "day",
    label: "Today",
    active_seconds: 120,
    idle_seconds: 60,
    tracked_seconds: 180,
    apps: [
        {name: "Editor", seconds: 90, percent: 75, windows: []},
        {name: "Browser", seconds: 30, percent: 25, windows: []},
        ...Array.from({length: 16}, (_, i) => ({
            name: `Other ${i}`, seconds: 0, percent: 0, windows: [],
        })),
        {name: '<App & "name">', seconds: 0, percent: 0, windows: []},
    ],
    timeline: [
        {label: "00", capacity_seconds: 3600, seconds: 120, apps: [
            {name: "Editor", seconds: 90},
            {name: "Browser", seconds: 30},
        ]},
        {label: "01", capacity_seconds: 3600, seconds: 0, apps: []},
    ],
};
let response = report;
let updateResponse = {
    status: "current", version: "0.1.0", channel: "nightly",
    message: "You're up to date.",
    release_url: "https://github.com/acangialosi/app-activity/releases/tag/nightly",
};
let updateFailure = false;
let updateHttpOk = true;
let retryUpdate;
let refresh;
const context = vm.createContext({
    ...Object.fromEntries([
        "showAllApplications", "filterStatus", "timeline", "yAxis", "timeMode", "percentMode",
        "themeSelect", "previousPeriod", "nextPeriod", "currentPeriod",
        "label", "active", "idle", "tracked", "apps",
    ].map(id => [id, element(id)])),
    document: {
        documentElement: {dataset: {}},
        getElementById: element,
        querySelectorAll: () => [],
        createElement: () => ({
            textContent: "",
            get innerHTML() {
                return this.textContent.replace(/&/g, "&amp;")
                    .replace(/</g, "&lt;").replace(/>/g, "&gt;")
                    .replace(/"/g, "&quot;");
            },
        }),
    },
    localStorage: {getItem: () => null, setItem() {}},
    addEventListener() {},
    requestAnimationFrame: callback => callback(),
    setInterval: callback => {refresh = callback;},
    setTimeout: callback => {retryUpdate = callback;},
    fetch: async url => {
        if (url === "/api/update") {
            if (updateFailure) throw new Error("Offline");
            return {ok: updateHttpOk, json: async () => updateResponse};
        }
        return {json: async () => response};
    },
});
const run = code => vm.runInContext(code, context);
const choose = name => {
    if (!name) {
        element("showAllApplications").onclick();
        return;
    }
    const index = response.apps.findIndex(app => app.name === name);
    assert.notEqual(index, -1);
    element("apps").onclick({target: {closest: () => ({dataset: {appIndex: index}})}});
};

async function test() {
    run(scripts.at(-1));
    await run("load()");
    const original = JSON.stringify(report);
    const allChart = element("timeline").innerHTML;
    const allApps = element("apps").innerHTML;
    const editorColor = /title="Editor: 1m" style="height:75%;background:([^"]+)"/
        .exec(allChart)[1];
    assert.equal((allApps.match(/class="app-select"/g) || []).length, report.apps.length);
    assert.equal(element("filterStatus").textContent, "Chart: All applications");
    assert.equal(element("showAllApplications").disabled, true);
    assert.ok(allApps.includes("Other 15"));
    assert.ok(allApps.includes("&lt;App &amp; &quot;name&quot;&gt;"));

    choose("Browser");
    let chart = element("timeline").innerHTML;
    assert.ok(chart.includes('title="Browser: 30s"'));
    assert.ok(!chart.includes("Editor:"));
    assert.ok(chart.includes('title="00: 30s"'));
    assert.ok(chart.includes('class="bar-stack" style="height:1%"'));
    assert.equal(element("apps").innerHTML, allApps);
    assert.equal(element("active").textContent, "2m");
    assert.equal(element("idle").textContent, "1m");
    assert.equal(element("tracked").textContent, "3m");
    assert.equal(JSON.stringify(report), original);
    assert.equal(run("chartBuckets(currentData).length"), 2);
    assert.equal(run("chartBuckets(currentData)[0].seconds"), 30);
    assert.equal(run("chartBuckets(currentData)[0].total_seconds"), 120);
    assert.equal(element("filterStatus").textContent, "Chart: Browser");
    assert.equal(element("showAllApplications").disabled, false);
    choose("Browser");
    assert.equal(element("timeline").innerHTML, allChart);
    assert.equal(element("showAllApplications").disabled, true);
    choose("Browser");
    element("apps").onclick({target: {closest: () => null}});
    assert.equal(run("selectedApplication"), "Browser");

    element("percentMode").onclick();
    chart = element("timeline").innerHTML;
    assert.ok(chart.includes('title="Browser: 25.0%"'));
    assert.ok(chart.includes('class="bar-stack" style="height:25%"'));
    assert.ok(element("yAxis").innerHTML.includes("100%"));
    assert.ok(!chart.includes("NaN") && !chart.includes("Infinity"));
    choose("Editor");
    assert.ok(element("timeline").innerHTML.includes('title="Editor: 75.0%"'));
    assert.ok(element("timeline").innerHTML.includes(`background:${editorColor}`));
    choose("");
    assert.ok(element("timeline").innerHTML.includes('title="Browser: 25.0%"'));
    assert.ok(element("timeline").innerHTML.includes('class="bar-stack" style="height:100%"'));
    element("timeMode").onclick();
    assert.equal(element("timeline").innerHTML, allChart);

    choose("Browser");
    await refresh();
    assert.equal(run("selectedApplication"), "Browser");
    assert.ok(!element("timeline").innerHTML.includes("Editor:"));

    response = {...report, period: "week", apps: [report.apps[0]], timeline: [
        {...report.timeline[0], seconds: 90, apps: [{name: "Editor", seconds: 90}]},
        report.timeline[1],
    ]};
    element("previousPeriod").onclick();
    await run("load()");
    assert.equal(run("selectedApplication"), "Browser");
    assert.equal(element("filterStatus").textContent, "Chart: Browser (no activity)");
    assert.equal(element("showAllApplications").disabled, false);
    assert.equal((element("timeline").innerHTML.match(/bar-segment/g) || []).length, 0);
    assert.ok(element("timeline").innerHTML.includes('title="00: 0s"'));
    element("percentMode").onclick();
    assert.ok(!element("timeline").innerHTML.includes("NaN"));
    choose("");
    assert.ok(element("timeline").innerHTML.includes("Editor: 100.0%"));

    response = report;
    await run("load()");
    choose("Browser");
    for (const period of ["hour", "day", "week", "month", "year"]) {
        response = {...report, period};
        await run("load()");
        assert.equal(run("selectedApplication"), "Browser");
        assert.ok(element("timeline").innerHTML.includes("Browser: 25.0%"));
        element("timeMode").onclick();
        assert.ok(element("timeline").innerHTML.includes("Browser: 30s"));
        element("percentMode").onclick();
    }

    response = {...report, apps: [], timeline: [
        {label: "00", capacity_seconds: 3600, seconds: 0, apps: []},
    ]};
    await run("load()");
    assert.equal(run("selectedApplication"), "Browser");
    assert.ok(!element("timeline").innerHTML.includes("NaN"));
    choose("");
    assert.equal(run("selectedApplication"), "");
    assert.ok(element("timeline").innerHTML.includes('class="bar-stack" style="height:1%"'));

    await run("loadUpdate()");
    assert.equal(element("updateStatus").dataset.status, "current");
    assert.ok(element("updateMessage").textContent.includes("You're up to date."));
    assert.equal(element("updateLink").textContent, "View releases");
    updateResponse = {...updateResponse, status: "available", message: "Update available: <new build>"};
    await run("loadUpdate()");
    assert.equal(element("updateStatus").dataset.status, "available");
    assert.ok(element("updateMessage").textContent.includes("<new build>"));
    assert.equal(element("updateMessage").innerHTML, "");
    assert.equal(element("updateLink").textContent, "Download update");
    assert.equal(element("updateLink").href, updateResponse.release_url);
    updateResponse = {...updateResponse, status: "pending", message: "Checking for updates..."};
    await run("loadUpdate()");
    assert.equal(typeof retryUpdate, "function");
    updateResponse = {...updateResponse, status: "error", message: "Could not check for updates"};
    await retryUpdate();
    assert.equal(element("updateStatus").dataset.status, "error");
    assert.equal(element("updateLink").textContent, "View releases");
    updateResponse = {...updateResponse, status: "source", channel: undefined, message: "Running from source"};
    await run("loadUpdate()");
    assert.equal(element("updateStatus").dataset.status, "source");
    assert.ok(!element("updateMessage").textContent.includes("undefined"));
    updateHttpOk = false;
    await run("loadUpdate()");
    assert.equal(element("updateStatus").dataset.status, "error");
    assert.ok(element("updateMessage").textContent.includes("Check GitHub releases manually"));
    updateFailure = true;
    await run("loadUpdate()");
    assert.equal(element("updateStatus").dataset.status, "error");
    assert.equal(element("updateLink").href, updateResponse.release_url);
}
test().catch(error => {console.error(error); process.exitCode = 1;});
