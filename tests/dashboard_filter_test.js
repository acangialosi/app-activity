const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const scripts = JSON.parse(fs.readFileSync(0, "utf8"));
assert.ok(
    Array.isArray(scripts) && scripts.length > 0 && scripts.every(script => typeof script === "string"),
    "Expected dashboard scripts extracted by the HTML parser",
);
const elements = new Map();
let appButtons = [];
function element(id) {
    if (!elements.has(id)) {
        elements.set(id, {
            _html: "",
            get innerHTML() {return this._html;},
            set innerHTML(value) {
                this._html = value;
                if (id === "apps") {
                    appButtons = [...value.matchAll(/data-app-index="(\d+)" aria-pressed="(true|false)"/g)]
                        .map(([, index, pressed]) => ({
                            dataset: {appIndex: index},
                            attributes: {"aria-pressed": pressed},
                            setAttribute(name, value) {this.attributes[name] = value;},
                        }));
                }
            },
            textContent: "",
            value: "",
            className: "",
            disabled: false,
            open: false,
            hidden: id === "settingsUpdate",
            dataset: {},
            options: [],
            listeners: {},
            addEventListener(name, callback) {this.listeners[name] = callback;},
            style: {setProperty() {}},
            appendChild(child) {this.options.push(child);},
            add(option) {this.options.push(option);},
            replaceChildren(...children) {this.options = children;},
            contains: () => true,
            focus() {context.document.activeElement = this;},
        });
    }
    return elements.get(id);
}

const report = {
    period: "day",
    start: "2026-06-01T00:00:00+00:00",
    end: "2026-06-02T00:00:00+00:00",
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
const updateRequests = [];
let holdUpdate;
let retryUpdate;
let refresh;
const documentListeners = {};
const savedPreferences = new Map([["appActivityTheme", "dark"]]);
let storageUnavailable = false;
const context = vm.createContext({
    ...Object.fromEntries([
        "showAllApplications", "filterStatus", "timeline", "yAxis", "timeMode", "percentMode",
        "themeSelect", "previousPeriod", "nextPeriod", "currentPeriod",
        "exportStart", "exportEnd",
        "label", "active", "idle", "tracked", "apps",
    ].map(id => [id, element(id)])),
    document: {
        documentElement: {dataset: {}},
        addEventListener: (name, callback) => {documentListeners[name] = callback;},
        getElementById: element,
        querySelectorAll: selector => selector === ".app-select" ? appButtons : [],
        createElement: () => ({
            textContent: "",
            get innerHTML() {
                return this.textContent.replace(/&/g, "&amp;")
                    .replace(/</g, "&lt;").replace(/>/g, "&gt;");
            },
        }),
    },
    localStorage: {
        getItem(key) {
            if (storageUnavailable) throw new Error("Storage unavailable");
            return savedPreferences.get(key) ?? null;
        },
        setItem(key, value) {
            if (storageUnavailable) throw new Error("Storage unavailable");
            savedPreferences.set(key, value);
        },
    },
    addEventListener() {},
    requestAnimationFrame: callback => callback(),
    setInterval: callback => {refresh = callback;},
    setTimeout: callback => {retryUpdate = callback; return callback;},
    clearTimeout: timer => {if (retryUpdate === timer) retryUpdate = undefined;},
    fetch: async (url, options) => {
        if (url === "/api/update") {
            updateRequests.push(options.method);
            if (holdUpdate) await holdUpdate;
            if (updateFailure) throw new Error("Offline");
            return {ok: updateHttpOk, json: async () => updateResponse};
        }
        return {json: async () => response};
    },
});
const run = code => vm.runInContext(code, context);
const choose = (name, ctrlKey = false) => {
    if (!name) {
        element("showAllApplications").onclick();
        return;
    }
    const index = response.apps.findIndex(app => app.name === name);
    assert.notEqual(index, -1);
    element("apps").onclick({ctrlKey, target: {closest: () => appButtons[index]}});
};
const selection = () => Array.from(run("[...selectedApplications]"));
function assertPressed(names) {
    assert.equal(appButtons.length, response.apps.length);
    for (const button of appButtons) {
        assert.equal(button.attributes["aria-pressed"],
            String(names.includes(response.apps[Number(button.dataset.appIndex)].name)));
    }
}

async function test() {
    run(scripts[0]);
    assert.equal(context.document.documentElement.dataset.theme, "dark");
    run(scripts.at(-1));
    await run("load()");
    assert.equal(element("themeSelect").value, "dark");
    for (const theme of ["light", "dark", "system"]) {
        element("themeSelect").value = theme;
        element("themeSelect").onchange();
        assert.equal(context.document.documentElement.dataset.theme, theme === "system" ? undefined : theme);
        assert.equal(savedPreferences.get("appActivityTheme"), theme);
        assert.equal(run("storedTheme()"), theme);
    }
    savedPreferences.set("appActivityTheme", "unknown");
    assert.equal(run("storedTheme()"), "system");
    storageUnavailable = true;
    assert.equal(run("storedTheme()"), "system");
    run('setTheme("dark")');
    assert.equal(context.document.documentElement.dataset.theme, "dark");
    storageUnavailable = false;

    const settings = element("settings");
    const summary = element("settingsSummary");
    const outside = element("previousPeriod");
    settings.contains = target => [settings, summary, element("themeSelect"), element("checkUpdates"),
        element("exportStart"), element("exportEnd"), element("exportForm")].includes(target);
    let prevented = false;
    const escape = {key: "Escape", preventDefault() {prevented = true;}};
    settings.onkeydown(escape);
    assert.equal(prevented, false);
    settings.open = true;
    settings.onkeydown({key: "Tab"});
    assert.equal(settings.open, true);
    settings.onkeydown({...escape, defaultPrevented: true});
    assert.equal(settings.open, true);
    element("themeSelect").focus();
    settings.onkeydown(escape);
    assert.equal(settings.open, false);
    assert.equal(prevented, true);
    assert.equal(context.document.activeElement, summary);
    settings.open = true;
    element("themeSelect").focus();
    documentListeners.keyup({key: "Tab"});
    assert.equal(settings.open, true);
    outside.focus();
    documentListeners.keyup({key: "ArrowDown"});
    assert.equal(settings.open, true);
    documentListeners.keyup({key: "Tab"});
    assert.equal(settings.open, false);
    assert.equal(context.document.activeElement, outside);
    settings.open = true;
    documentListeners.click({target: element("themeSelect")});
    assert.equal(settings.open, true);
    element("themeSelect").focus();
    outside.focus();
    settings.listeners.focusout?.({relatedTarget: outside});
    assert.equal(settings.open, true, "Pointer focus transfer must not move an outside click target");
    outside.onclick();
    assert.equal(run("periodOffset"), -1, "Outside control activates before Settings collapses");
    documentListeners.click({target: outside});
    assert.equal(settings.open, false);
    assert.equal(context.document.activeElement, outside);
    element("currentPeriod").onclick();

    settings.open = true;
    for (const id of ["exportStart", "exportEnd", "exportForm"]) {
        element(id).focus();
        documentListeners.keyup({key: "Tab"});
        documentListeners.click({target: element(id)});
        assert.equal(settings.open, true, "Export controls remain inside Settings");
    }
    settings.onkeydown({...escape, defaultPrevented: true});
    assert.equal(settings.open, true, "Do not override Escape already handled by a native control");
    settings.onkeydown(escape);
    assert.equal(settings.open, false);
    assert.equal(context.document.activeElement, summary);

    assert.equal(element("exportStart").value, "2026-06-01");
    assert.equal(element("exportEnd").value, "2026-06-01");
    element("exportStart").value = "2026-05-01";
    element("exportEnd").value = "2026-05-31";
    await run("load()");
    assert.equal(element("exportStart").value, "2026-05-01");
    assert.equal(element("exportEnd").value, "2026-05-31");
    settings.open = true;
    await run("load()");
    assert.equal(element("exportStart").value, "2026-05-01");
    assert.equal(element("exportEnd").value, "2026-05-31");
    settings.open = false;
    outside.focus();
    for (const [start, end, expectedStart, expectedEnd] of [
        ["2026-01-01T00:00:00-04:00", "2027-01-01T00:00:00-04:00", "2026-01-01", "2026-12-31"],
        ["2026-03-01T00:00:00-07:00", "2026-04-01T00:00:00-07:00", "2026-03-01", "2026-03-31"],
        ["2024-02-01T00:00:00+05:30", "2024-03-01T00:00:00+05:30", "2024-02-01", "2024-02-29"],
        ["2026-06-01T12:00:00+00:00", "2026-06-01T13:00:00+00:00", "2026-06-01", "2026-06-01"],
        ["2026-06-01T23:00:00-07:00", "2026-06-02T00:00:00-07:00", "2026-06-01", "2026-06-01"],
    ]) {
        response = {...report, start, end};
        await run("load()");
        assert.equal(element("exportStart").value, expectedStart);
        assert.equal(element("exportEnd").value, expectedEnd);
    }
    response = report;
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
    assert.equal(run(`escapeHtml('<App & "name">')`), "&lt;App &amp; &quot;name&quot;&gt;",
        "DOM text serialization alone does not escape quotes used in title attributes");

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
    assert.deepEqual(selection(), ["Browser"]);

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

    choose("Editor", true);
    assert.deepEqual(selection(), ["Editor"]);
    assertPressed(["Editor"]);
    choose("Browser", true);
    assert.deepEqual(selection(), ["Editor", "Browser"]);
    assertPressed(["Editor", "Browser"]);
    assert.equal(element("filterStatus").textContent, "Chart: Editor, Browser");
    assert.equal(element("showAllApplications").disabled, false);
    assert.equal(run("chartBuckets(currentData)[0].seconds"), 120);
    assert.equal(run("chartBuckets(currentData)[0].apps.length"), 2);
    assert.equal(JSON.stringify(report), original);
    choose("Editor", true);
    assert.deepEqual(selection(), ["Browser"]);
    assertPressed(["Browser"]);
    choose("Browser", true);
    assert.deepEqual(selection(), []);
    assertPressed([]);
    assert.equal(element("showAllApplications").disabled, true);
    assert.equal(element("timeline").innerHTML, allChart);

    choose("Editor", true);
    choose("Browser", true);
    choose("Browser");
    assert.deepEqual(selection(), ["Browser"], "Normal click from a subset selects only clicked app");
    choose("Editor", true);
    choose("Other 0");
    assert.deepEqual(selection(), ["Other 0"], "Normal click can replace the subset with an unselected app");
    choose("Editor", true);
    choose("");
    assert.deepEqual(selection(), []);
    assertPressed([]);
    assert.equal(element("timeline").innerHTML, allChart);

    const third = {name: "Third", seconds: 120, percent: 50, windows: []};
    response = {...report, active_seconds: 240, tracked_seconds: 300,
        apps: [...report.apps, third], timeline: [
            {...report.timeline[0], seconds: 240, apps: [...report.timeline[0].apps, third]},
            report.timeline[1],
        ]};
    await run("load()");
    const threeAppsOriginal = JSON.stringify(response);
    choose("Editor", true);
    choose("Browser", true);
    element("percentMode").onclick();
    chart = element("timeline").innerHTML;
    assert.ok(chart.includes('title="Editor: 37.5%" style="height:75%;'));
    assert.ok(chart.includes('title="Browser: 12.5%" style="height:25%;'));
    assert.ok(chart.includes('class="bar-stack" style="height:50%"'));
    assert.ok(!chart.includes('title="Third:'));
    assert.equal(run("chartBuckets(currentData)[0].seconds"), 120);
    assert.equal(run("chartBuckets(currentData)[0].total_seconds"), 240);
    assert.equal(element("active").textContent, "4m");
    assert.equal(JSON.stringify(response), threeAppsOriginal);
    const stableColors = run("JSON.stringify([...colors])");
    response = {...response, apps: [...response.apps].reverse()};
    await refresh();
    assert.deepEqual(selection(), ["Editor", "Browser"]);
    assertPressed(["Editor", "Browser"]);
    assert.equal(run("JSON.stringify([...colors])"), stableColors);
    assert.equal(element("timeline").innerHTML, chart);
    choose("Browser", true);
    assert.deepEqual(selection(), ["Editor"], "Delegated row click uses the refreshed name, not old index");
    choose("Browser", true);

    response = {...report, period: "week", apps: [report.apps[0]], timeline: [
        {...report.timeline[0], seconds: 90, apps: [{name: "Editor", seconds: 90}]},
    ]};
    element("previousPeriod").onclick();
    await run("load()");
    assert.deepEqual(selection(), ["Editor", "Browser"]);
    assertPressed(["Editor"]);
    assert.equal(element("filterStatus").textContent, "Chart: Editor, Browser (no activity)");
    assert.ok(element("timeline").innerHTML.includes("Editor: 100.0%"));
    response = {...response, apps: [], timeline: [{...report.timeline[1]}]};
    await refresh();
    assert.deepEqual(selection(), ["Editor", "Browser"]);
    assert.equal(element("filterStatus").textContent, "Chart: Editor (no activity), Browser (no activity)");
    assert.equal(element("showAllApplications").disabled, false);
    assert.ok(!element("timeline").innerHTML.includes("bar-segment"));
    response = report;
    element("currentPeriod").onclick();
    await run("load()");
    assert.deepEqual(selection(), ["Editor", "Browser"]);
    assertPressed(["Editor", "Browser"]);
    assert.ok(element("timeline").innerHTML.includes("Browser: 25.0%"));
    assert.equal(run("colors.get('Editor')"), editorColor);
    choose("");
    choose('<App & "name">', true);
    choose("Editor", true);
    assert.equal(element("filterStatus").textContent, 'Chart: <App & "name">, Editor');
    assert.equal(element("filterStatus").innerHTML, "");
    assertPressed(['<App & "name">', "Editor"]);
    element("apps").onclick({ctrlKey: true, target: {closest: () => null}});
    assert.deepEqual(selection(), ['<App & "name">', "Editor"], "Window detail clicks leave the selection unchanged");
    choose("");
    for (const seconds of [5, 6]) {
        response = {...report, apps: report.apps.slice(0, 2), timeline: [
            {...report.timeline[0], seconds: 11, apps: [
                {name: "Editor", seconds}, {name: "Browser", seconds},
            ]},
        ]};
        await run("load()");
        const roundedOriginal = JSON.stringify(response);
        const roundedAll = element("timeline").innerHTML;
        choose("Editor", true);
        assert.equal(run("chartBuckets(currentData)[0].seconds"), seconds);
        assert.equal(run("chartBuckets(currentData)[0].total_seconds"), 11);
        choose("Browser", true);
        assert.equal(run("chartBuckets(currentData)[0].seconds"), 11);
        assert.equal(element("timeline").innerHTML, roundedAll);
        for (const name of ["Editor", "Browser"]) {
            assert.ok(roundedAll.includes(`title="${name}: 50.0%" style="height:50%;`));
        }
        assert.ok(roundedAll.includes('class="bar-stack" style="height:100%"'));
        element("timeMode").onclick();
        assert.ok(element("timeline").innerHTML.includes('title="00: 11s"'));
        assert.ok(element("timeline").innerHTML.includes(`title="Editor: ${seconds}s"`));
        assert.equal(JSON.stringify(response), roundedOriginal);
        element("percentMode").onclick();
        choose("");
    }
    response = {...report, apps: report.apps.slice(0, 3), timeline: [
        {...report.timeline[0], seconds: 11, apps: [
            {name: "Editor", seconds: 6}, {name: "Browser", seconds: 6},
            {name: "Other 0", seconds: 0},
        ]},
    ]};
    await run("load()");
    choose("Editor", true);
    choose("Browser", true);
    assert.equal(run("chartBuckets(currentData)[0].seconds"), 11,
        "A proper subset cannot exceed the independently rounded original total");
    assert.ok(element("timeline").innerHTML.includes('class="bar-stack" style="height:100%"'));
    assert.ok(element("timeline").innerHTML.includes('title="Editor: 50.0%" style="height:50%;'));
    choose("");
    response = report;
    await run("load()");
    element("timeMode").onclick();

    choose("Browser");
    await refresh();
    assert.deepEqual(selection(), ["Browser"]);
    assert.ok(!element("timeline").innerHTML.includes("Editor:"));

    response = {...report, period: "week", apps: [report.apps[0]], timeline: [
        {...report.timeline[0], seconds: 90, apps: [{name: "Editor", seconds: 90}]},
        report.timeline[1],
    ]};
    element("previousPeriod").onclick();
    await run("load()");
    assert.deepEqual(selection(), ["Browser"]);
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
        assert.deepEqual(selection(), ["Browser"]);
        assert.ok(element("timeline").innerHTML.includes("Browser: 25.0%"));
        element("timeMode").onclick();
        assert.ok(element("timeline").innerHTML.includes("Browser: 30s"));
        element("percentMode").onclick();
    }

    response = {...report, apps: [], timeline: [
        {label: "00", capacity_seconds: 3600, seconds: 0, apps: []},
    ]};
    await run("load()");
    assert.deepEqual(selection(), ["Browser"]);
    assert.ok(!element("timeline").innerHTML.includes("NaN"));
    choose("");
    assert.deepEqual(selection(), []);
    assert.ok(element("timeline").innerHTML.includes('class="bar-stack" style="height:1%"'));

    await run("loadUpdate()");
    assert.equal(element("updateStatus").dataset.status, "current");
    assert.ok(element("updateMessage").textContent.includes("You're up to date."));
    assert.equal(element("updateLink").textContent, "View releases");
    assert.equal(element("checkUpdates").disabled, false);
    assert.equal(updateRequests.at(-1), "GET");
    assert.equal(element("settingsUpdate").hidden, true);
    assert.equal(element("settingsUpdate").textContent, "");
    let releaseUpdate;
    holdUpdate = new Promise(resolve => {releaseUpdate = resolve;});
    updateResponse = {...updateResponse, status: "pending", message: "Checking for updates..."};
    const requestCount = updateRequests.length;
    const manualCheck = element("checkUpdates").onclick();
    assert.equal(element("checkUpdates").disabled, true);
    assert.equal(element("updateMessage").textContent, "Checking for updates...");
    assert.equal(element("updateStatus").dataset.status, "pending");
    assert.equal(updateRequests.at(-1), "POST");
    await element("checkUpdates").onclick();
    await run("loadUpdate()");
    assert.equal(updateRequests.length, requestCount + 1);
    releaseUpdate();
    await manualCheck;
    holdUpdate = undefined;
    assert.equal(element("checkUpdates").disabled, true);
    assert.equal(typeof retryUpdate, "function");
    updateResponse = {...updateResponse, status: "available", message: "Update available: <new build>"};
    await retryUpdate();
    assert.equal(updateRequests.at(-1), "GET");
    assert.equal(retryUpdate, undefined);
    assert.equal(element("checkUpdates").disabled, false);
    assert.equal(element("updateStatus").dataset.status, "available");
    assert.ok(element("updateMessage").textContent.includes("<new build>"));
    assert.equal(element("updateMessage").innerHTML, "");
    assert.equal(element("updateLink").textContent, "Download update");
    assert.equal(element("updateLink").href, updateResponse.release_url);
    assert.equal(settings.open, false);
    assert.equal(element("settingsUpdate").hidden, false);
    assert.equal(element("settingsUpdate").textContent, "Update available");
    assert.equal(context.document.activeElement, outside);
    updateResponse = {...updateResponse, status: "pending", message: "Checking for updates..."};
    await run("loadUpdate()");
    assert.equal(typeof retryUpdate, "function");
    assert.equal(element("checkUpdates").disabled, true);
    assert.equal(element("settingsUpdate").hidden, false);
    updateResponse = {...updateResponse, status: "error", message: "Could not check for updates"};
    await retryUpdate();
    assert.equal(element("updateStatus").dataset.status, "error");
    assert.equal(element("updateLink").textContent, "View releases");
    assert.equal(element("checkUpdates").disabled, false);
    assert.equal(element("settingsUpdate").hidden, false);
    await element("checkUpdates").onclick();
    assert.equal(updateRequests.at(-1), "POST");
    updateResponse = {...updateResponse, status: "current", message: "You're up to date."};
    await element("checkUpdates").onclick();
    assert.equal(element("updateStatus").dataset.status, "current");
    assert.equal(element("checkUpdates").disabled, false);
    assert.equal(element("settingsUpdate").hidden, true);
    assert.equal(element("settingsUpdate").textContent, "");
    updateResponse = {...updateResponse, status: "source", channel: undefined, message: "Running from source"};
    await run("loadUpdate()");
    assert.equal(element("updateStatus").dataset.status, "source");
    assert.ok(!element("updateMessage").textContent.includes("undefined"));
    assert.equal(element("checkUpdates").disabled, true);
    assert.equal(element("settingsUpdate").hidden, true);
    updateResponse = {...updateResponse, status: "error", message: "Build information is unavailable"};
    await run("loadUpdate()");
    assert.equal(element("checkUpdates").disabled, true);
    updateHttpOk = false;
    await element("checkUpdates").onclick();
    assert.equal(element("updateStatus").dataset.status, "error");
    assert.ok(element("updateMessage").textContent.includes("Check GitHub releases manually"));
    assert.equal(element("checkUpdates").disabled, false);
    updateFailure = true;
    await run("loadUpdate()");
    assert.equal(element("updateStatus").dataset.status, "error");
    assert.equal(element("updateLink").href, updateResponse.release_url);
    assert.equal(element("checkUpdates").disabled, false);
}
test().catch(error => {console.error(error); process.exitCode = 1;});
