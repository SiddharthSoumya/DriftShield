const $ = (id) => document.getElementById(id);
let selected;
let refreshInProgress = false;
let mutationInProgress = false;
let detailSignature;

function element(tag, value, className) {
  const node = document.createElement(tag);
  if (value !== undefined) node.textContent = value;
  if (className) node.className = className;
  return node;
}

function button(label, action, primary = false) {
  const node = element("button", label, primary ? "primary" : "");
  node.type = "button";
  node.onclick = () => mutate(action, node);
  return node;
}

async function api(path, payload) {
  const response = await fetch(path, payload === undefined ? {cache: "no-store"} : {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-DriftShield-Request": "1"},
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "The request could not be completed.");
  return result;
}

async function mutate(action, node) {
  if (mutationInProgress) return;
  mutationInProgress = true;
  node.disabled = true;
  $("notice").textContent = "";
  try {
    await action();
  } catch (error) {
    $("notice").textContent = error.message;
  } finally {
    mutationInProgress = false;
    node.disabled = false;
  }
}

function time(value) {
  return value ? new Date(value).toLocaleString() : "Awaiting scan";
}

function value(input) {
  if (input === undefined) return "—";
  return typeof input === "boolean" ? (input ? "Enabled" : "Disabled") : input;
}

function badge(label, tone = label) {
  return element("span", label, "status " + tone);
}

function table(target, headings, rows, empty) {
  if (!rows.length) {
    $(target).replaceChildren(element("p", empty));
    return;
  }
  const grid = element("table");
  const header = grid.createTHead().insertRow();
  for (const heading of headings) {
    const cell = element("th", heading);
    cell.scope = "col";
    header.append(cell);
  }
  const body = grid.createTBody();
  for (const row of rows) {
    const tr = body.insertRow();
    for (const item of row) tr.insertCell().append(item instanceof Node ? item : String(item));
  }
  $(target).replaceChildren(grid);
}

function log(target, rows, render, empty) {
  $(target).replaceChildren(...(rows.length ? rows.map(render) : [element("p", empty)]));
}

function showToken(token) {
  $("credential").hidden = !token;
  $("token").textContent = token || "";
}

function renderDetail(data) {
  const {monitor, fields, baselines, incidents, scans, audit, stale_after_seconds} = data;
  const stale = Date.now() - Date.parse(monitor.last_success) > stale_after_seconds * 1000;
  const signature = JSON.stringify([data, stale]);
  if (signature === detailSignature) return;
  detailSignature = signature;
  const id = monitor.id;
  const path = "/api/monitors/" + id;
  $("empty").hidden = true;
  $("detail").hidden = false;
  $("title").textContent = monitor.repository;
  $("summary").textContent = monitor.mode === "runner" ? "GitHub Actions runner" : "Public GitHub API";
  $("error").hidden = !monitor.last_error;
  $("error").textContent = monitor.last_error || "";
  $("runner").hidden = monitor.mode !== "runner";
  $("runner").textContent = "Ingest endpoint: " + window.location.origin + "/api/ingest/" + id;
  $("actions").replaceChildren();
  if (monitor.mode === "public") {
    $("actions").append(button("Scan now", async () => {
      await api(path + "/scan", {});
      await refresh();
    }, true));
  } else {
    $("actions").append(button("Rotate token", async () => {
      if (window.confirm("Rotate this runner token? The previous credential stops working immediately.")) {
        showToken((await api(path + "/rotate", {})).token);
      }
    }));
  }
  const baseline = baselines[0];
  const changed = baseline && Object.keys(fields).some((field) => baseline.values[field] !== monitor.latest?.[field]);
  if (monitor.latest && !stale && !monitor.last_error && (!baseline || changed)) {
    $("actions").append(button(baseline ? "Approve new baseline" : "Approve baseline", async () => {
      if (window.confirm("Approve these observed settings as the expected configuration?")) {
        await api(path + "/baseline", {observed_at: monitor.last_success});
        await refresh();
      }
    }));
  }
  const open = incidents.filter((incident) => !incident.closed_at).length;
  const state = monitor.last_error ? "Scan failed" : !monitor.last_success ? "Awaiting scan"
    : stale ? "Observation overdue"
    : !baseline ? "Awaiting baseline" : changed ? "Drift detected" : "Matches baseline";
  $("metrics").replaceChildren(...[
    ["Status", state], ["Last successful scan", time(monitor.last_success)], ["Open incidents", open],
  ].map(([label, content]) => {
    const node = element("div");
    node.append(element("span", label, "muted"), element("strong", content));
    return node;
  }));

  table("comparison", ["Setting", "Expected", "Observed", "Result"], Object.entries(fields).map(([field, [label]]) => {
    const expected = baseline?.values[field];
    const actual = monitor.latest?.[field];
    const result = expected === undefined || actual === undefined ? "Pending" : expected === actual ? "Match" : "Drift";
    return [label, value(expected), value(actual), badge(result, result.toLowerCase())];
  }), "No settings collected.");
  table("incidents", ["Setting", "Expected → observed", "Severity", "Last seen", "Status"], incidents.map((incident) => {
    let status = incident.resolution?.replaceAll("_", " ") || "Open";
    if (!incident.closed_at) {
      status = incident.acknowledged_at ? "Acknowledged" : button("Acknowledge", async () => {
        await api(path + "/incidents/" + incident.id + "/ack", {});
        await refresh();
      });
    }
    return [fields[incident.field][0], value(incident.expected) + " → " + value(incident.actual),
      badge(incident.severity), time(incident.last_seen) + " · " + incident.occurrences + "×", status];
  }), "No drift incidents recorded.");
  log("scans", scans, (scan) => element("div", time(scan.observed_at) + " · " + (scan.error || scan.status), "log"), "No scans recorded.");
  log("audit", audit, (entry) => element("div", time(entry.occurred_at) + " · " + entry.detail, "log"), "No audit events.");
  log("baselines", baselines, (version) => {
    const item = element("details", undefined, "log");
    item.append(element("summary", "Version " + version.version + " · " + time(version.created_at)),
      element("pre", JSON.stringify(version.values, null, 2)));
    return item;
  }, "No approved baselines.");
}

async function refresh() {
  const all = await api("/api/monitors");
  selected ??= all[0]?.id;
  $("monitors").replaceChildren(...all.map((monitor) => {
    const node = button(monitor.repository, async () => {
      selected = monitor.id;
      showToken(null);
      await refresh();
    });
    node.setAttribute("aria-current", String(selected === monitor.id));
    return node;
  }));
  if (selected !== undefined) {
    const id = selected;
    const detail = await api("/api/monitors/" + id);
    if (selected === id) renderDetail(detail);
  }
}

$("dismiss").onclick = () => showToken(null);
$("add").onsubmit = (event) => {
  event.preventDefault();
  mutate(async () => {
    const form = new FormData(event.target);
    const result = await api("/api/monitors", Object.fromEntries(form));
    selected = result.id;
    event.target.reset();
    showToken(result.token);
    await refresh();
  }, event.target.querySelector("button"));
};

async function refreshInBackground() {
  if (mutationInProgress || refreshInProgress || document.hidden) return;
  refreshInProgress = true;
  try {
    await refresh();
  } catch (error) {
    $("notice").textContent = error.message;
  } finally {
    refreshInProgress = false;
  }
}
refreshInBackground();
setInterval(refreshInBackground, 30000);
