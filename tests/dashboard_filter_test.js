const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const html = fs.readFileSync(0, "utf8");
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
const elements = new Map();
function element(id) {
    if (!elements.has(id)) {
        elements.set(id, {
            innerHTML: "",
            textContent: "",
            value: "",
            className: "",
            disabled: false,
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
    fetch: async () => ({json: async () => response}),
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
    run(scripts.at(-1)[1]);
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
}
test().catch(error => {console.error(error); process.exitCode = 1;});
