"use strict";
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fileUrl = (p) => "/files?path=" + encodeURIComponent(p);

async function api(path, opts = {}) {
  const init = { method: opts.method || (opts.body || opts.form ? "POST" : "GET") };
  if (opts.form) init.body = opts.form;
  else if (opts.body) { init.body = JSON.stringify(opts.body); init.headers = { "Content-Type": "application/json" }; }
  const r = await fetch(path, init);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}
function say(el, text, kind = "") { el.textContent = text; el.className = "msg " + kind; }

/* ---------- tabs ---------- */
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(name) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + name));
  if (name === "found") loadFound();
  if (name === "applications") loadApplications();
  if (name === "setup") loadSetup();
}

/* ---------- status checks ---------- */
let statusData = null;
async function loadStatus() {
  statusData = await api("/api/status");
  const o = statusData.ollama;
  const pills = [
    [o.running && o.installed, o.running ? (o.installed ? `Ollama · ${o.model}` : `${o.model} not installed`) : "Ollama not running"],
    [statusData.profile, statusData.profile ? "Profile" : "No profile"],
    [statusData.cv_parsed, statusData.cv_parsed ? "CV ready" : (statusData.cv_file ? "CV not read" : "No CV")],
  ];
  $("#checks").innerHTML = pills.map(([ok, t]) => `<span class="pill ${ok ? "ok" : "bad"}">${esc(t)}</span>`).join("");
  return statusData;
}

/* ---------- setup ---------- */
async function loadSetup() {
  const s = await loadStatus();
  const o = s.ollama;
  $("#ollama").innerHTML = !o.running
    ? `<p class="msg err">Ollama isn't running.</p><p>Open the Ollama app (or run <code>ollama serve</code> in a terminal), then reload this page.</p>`
    : o.installed
      ? `<p class="msg ok">Running, and <b>${esc(o.model)}</b> is installed.</p>`
      : `<p class="msg err">Running, but <b>${esc(o.model)}</b> isn't installed.</p><p>In a terminal: <code>ollama pull ${esc(o.model)}</code>. Installed: ${esc(o.models.join(", ") || "none")}</p>`;
  const p = await api("/api/profile");
  $("#profile").value = p.text;
  say($("#profile-msg"), p.exists ? "" : "This is the example profile. Edit it and save to create yours.");
  if (s.profile_error) say($("#profile-msg"), s.profile_error, "err");
  $("#cv-status").textContent = s.cv_file
    ? `Current CV: ${s.cv_file}${s.cv_parsed ? "" : " (not read yet)"}` : "No CV uploaded yet.";
  $("#cvjson").value = (await api("/api/cvjson")).text;
}
$("#save-profile").onclick = async () => {
  try { await api("/api/profile", { body: { text: $("#profile").value } }); say($("#profile-msg"), "Saved.", "ok"); loadStatus(); }
  catch (e) { say($("#profile-msg"), e.message, "err"); }
};
$("#upload-cv").onclick = async () => {
  const f = $("#cvfile").files[0];
  if (!f) return say($("#cv-msg"), "Choose your CV file first.", "err");
  const form = new FormData(); form.append("file", f);
  try { await api("/api/cv", { form }); say($("#cv-msg"), "Uploaded. The model is reading it; watch progress on the Apply tab.", "ok"); }
  catch (e) { say($("#cv-msg"), e.message, "err"); }
};
$("#reparse-cv").onclick = async () => {
  try { await api("/api/cv/parse", { method: "POST" }); say($("#cv-msg"), "Reading your CV; watch progress on the Apply tab.", "ok"); }
  catch (e) { say($("#cv-msg"), e.message, "err"); }
};
$("#save-cvjson").onclick = async () => {
  try { await api("/api/cvjson", { body: { text: $("#cvjson").value } }); say($("#cvjson-msg"), "Saved.", "ok"); }
  catch (e) { say($("#cvjson-msg"), e.message, "err"); }
};

/* ---------- apply ---------- */
async function startApply(links, file) {
  const form = new FormData();
  form.append("links", links);
  if (file) form.append("file", file);
  form.append("dry_run", $("#dryrun").checked);
  form.append("auto", $("#auto").checked);
  const r = await api("/api/apply", { form });
  return r.count;
}
$("#start").onclick = async () => {
  const s = await loadStatus();
  if (!s.cv_parsed) return say($("#apply-msg"), "Set up your profile and CV first (Setup tab).", "err");
  try {
    const n = await startApply($("#links").value, $("#listfile").files[0]);
    say($("#apply-msg"), `Started: ${n} job(s).`, "ok");
  } catch (e) { say($("#apply-msg"), e.message, "err"); }
};
$("#stop").onclick = () => api("/api/stop", { method: "POST" });

/* ---------- live state ---------- */
let logCount = 0, pendingId = null, lastTask = null;
async function poll() {
  try {
    const s = await api("/api/state?since=" + logCount);
    if (s.logs.length) {
      const log = $("#log"), atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
      log.textContent += s.logs.join("\n") + "\n";
      if (atBottom) log.scrollTop = log.scrollHeight;
    }
    logCount = s.log_count;
    $("#stop").hidden = !s.task;
    $("#start").disabled = !!s.task;
    renderCurrent(s);
    if ((s.pending && s.pending.id) !== pendingId) {
      pendingId = s.pending && s.pending.id;
      renderPending(s.pending);
      if (s.pending) { showTab("apply"); document.title = "● Job Agent: needs you"; }
      else document.title = "Job Agent";
    }
    if (lastTask && !s.task) { loadStatus(); if ($("#tab-found").classList.contains("active")) loadFound(); }
    lastTask = s.task;
  } catch (e) { /* server restarting; try again */ }
  setTimeout(poll, 1000);
}
function renderCurrent(s) {
  const el = $("#current"), j = s.job || {};
  if (!s.task) { el.innerHTML = `<span class="muted">Nothing running.</span>`; return; }
  if (s.task !== "apply" || !j.url) { el.innerHTML = `<span class="muted">Running: ${esc(s.task)}…</span>`; return; }
  const fit = j.fit != null ? `<span class="fit ${j.fit >= 65 ? "good" : "low"}">${esc(j.fit)}/100</span>` : "";
  el.innerHTML = `<div class="muted">Job ${esc(j.index)} of ${esc(j.total)}${j.tool ? " · " + esc(j.tool) : ""}</div>
    <div class="title">${esc(j.title || "Loading…")} ${j.company ? "· " + esc(j.company) : ""} ${fit}</div>
    ${j.fit_reason ? `<div class="muted">${esc(j.fit_reason)}</div>` : ""}
    <div><a href="${esc(j.url)}" target="_blank" rel="noopener">${esc(j.url)}</a></div>`;
}

function respond(payload) { return api("/api/respond", { body: { id: pendingId, ...payload } }); }

function answerInput(a, name) {
  const val = Array.isArray(a.value) ? a.value[0] : (a.value ?? "");
  if (a.options && a.options.length) {
    return `<div class="choices">${a.options.map((o) =>
      `<label><input type="radio" name="${name}" value="${esc(o)}" ${o === val ? "checked" : ""}> <span>${esc(o)}</span></label>`).join("")}</div>`;
  }
  const rows = a.kind === "textarea" ? 6 : 2;
  return `<textarea name="${name}" rows="${rows}">${esc(val)}</textarea>`;
}
function readInput(root, name, a) {
  const radio = root.querySelector(`input[name="${name}"]:checked`);
  let v = radio ? radio.value : (root.querySelector(`textarea[name="${name}"]`)?.value ?? "");
  v = v.trim();
  if (!v) return null;
  return a.kind === "multiselect" ? [v] : v;
}

function renderPending(p) {
  const box = $("#pending");
  if (!p) { box.innerHTML = ""; return; }
  if (p.kind === "question") return renderQuestion(box, p);
  if (p.kind === "review") return renderReview(box, p);
  if (p.kind === "confirm") {
    box.innerHTML = `<div class="prompt"><h3>${esc(p.message)}</h3>
      <div class="row"><button class="primary" data-yes="1">Yes</button><button data-yes="0">No</button></div></div>`;
    box.querySelectorAll("button").forEach((b) => (b.onclick = () => respond({ yes: b.dataset.yes === "1" })));
  } else if (p.kind === "wait") {
    box.innerHTML = `<div class="prompt"><h3>Your turn in the browser window</h3><p>${esc(p.message)}</p>
      <div class="row"><button class="primary">Done, continue</button></div></div>`;
    box.querySelector("button").onclick = () => respond({});
  } else if (p.kind === "text") {
    box.innerHTML = `<div class="prompt"><h3>${esc(p.message)}</h3><input type="text" id="ptext">
      <div class="row"><button class="primary">Continue</button></div></div>`;
    box.querySelector("button").onclick = () => respond({ value: $("#ptext").value });
  }
}

function renderQuestion(box, p) {
  const a = p.answer;
  box.innerHTML = `<div class="prompt">
    <div class="muted">${esc(p.company)} asks:</div>
    <h3>${esc(a.label)}${a.required ? " *" : ""}</h3>
    <div class="reason">${esc(a.reason)}</div>
    ${a.description ? `<details><summary>Full text</summary><div>${esc(a.description)}</div></details>` : ""}
    ${answerInput(a, "q")}
    <label class="check"><input type="checkbox" id="remember"> Remember this answer for future applications</label>
    <div class="row"><button class="primary" id="q-save">Use this answer</button><button id="q-blank">Leave blank</button></div>
  </div>`;
  $("#q-save").onclick = () => respond({ value: readInput(box, "q", a), remember: $("#remember").checked });
  $("#q-blank").onclick = () => respond({ value: null, remember: false });
}

function renderReview(box, p) {
  const issues = [...p.problems, ...p.warnings];
  box.innerHTML = `<div class="prompt">
    <h3>${p.dry_run ? "Dry run: the form is filled (nothing will be submitted)" : "Review before submitting"}</h3>
    <p class="muted">Check the browser window too; you can change anything there directly.</p>
    <div class="docs"><a href="${fileUrl(p.cv_pdf)}" target="_blank">Tailored CV (PDF)</a>
      <a href="${fileUrl(p.cover_pdf)}" target="_blank">Cover letter (PDF)</a></div>
    <details><summary>Cover letter text</summary><div>${esc(p.cover_text)}</div></details>
    ${issues.length ? `<ul class="issues">${issues.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : ""}
    <table class="answers">${p.answers.map((a) => `<tr data-i="${a.index}">
      <td class="q">${esc(a.label)}</td>
      <td class="v"><span class="badge ${esc(a.origin)}">${esc(a.origin)}</span>${esc(Array.isArray(a.value) ? a.value.join(", ") : (a.value ?? "(blank)"))}</td>
      <td class="a">${a.kind === "file" ? "" : `<button class="small" data-edit="${a.index}">Edit</button>`}</td></tr>`).join("")}
    </table>
    <div class="row">
      ${p.dry_run ? `<button class="primary" id="r-done">Next job</button>`
                  : `<button class="primary" id="r-submit">Submit application</button><button id="r-skip">Skip this job</button>`}
    </div></div>`;
  box.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => {
    const a = p.answers[+b.dataset.edit], row = b.closest("tr");
    row.querySelector(".v").innerHTML = answerInput(a, "e" + a.index) +
      `<div class="row"><button class="primary small" data-save>Save</button></div>`;
    b.remove();
    row.querySelector("[data-save]").onclick = () =>
      respond({ action: "edit", index: a.index, value: readInput(row, "e" + a.index, a) });
  }));
  if (p.dry_run) $("#r-done").onclick = () => respond({ action: "done" });
  else {
    $("#r-submit").onclick = () => respond({ action: "submit" });
    $("#r-skip").onclick = () => respond({ action: "skip" });
  }
}

/* ---------- found ---------- */
async function loadFound() {
  const rows = await api("/api/found");
  $("#found-table").className = "list";
  $("#found-table").innerHTML = rows.length
    ? `<tr><th></th><th>Fit</th><th>Company</th><th>Role</th><th>Location</th><th>Why</th></tr>` +
      rows.map((r) => `<tr><td><input type="checkbox" value="${esc(r.url)}"></td>
        <td><span class="fit ${r.fit >= 65 ? "good" : "low"}">${esc(r.fit)}</span></td>
        <td>${esc(r.company)}</td><td><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a></td>
        <td>${esc(r.location)}</td><td class="muted">${esc(r.reason)}</td></tr>`).join("")
    : `<tr><td class="muted">No found jobs yet. Add Greenhouse boards under <code>search</code> in your profile and press Search now.</td></tr>`;
}
$("#search").onclick = async () => {
  try { await api("/api/search", { method: "POST" }); showTab("apply"); }
  catch (e) { alert(e.message); }
};
$("#apply-selected").onclick = async () => {
  const urls = [...document.querySelectorAll("#found-table input:checked")].map((c) => c.value);
  if (!urls.length) return alert("Tick the jobs you want to apply for.");
  try { await startApply(urls.join("\n")); showTab("apply"); }
  catch (e) { alert(e.message); }
};

/* ---------- applications ---------- */
async function loadApplications() {
  const apps = await api("/api/applications");
  const by = {};
  apps.forEach((a) => (by[a.company] = by[a.company] || []).push(a));
  $("#applied").innerHTML = apps.length ? Object.entries(by).map(([c, list]) => `<div class="company">
      <h3>${esc(c)} <small>${list.length}</small></h3><ul>${list.map((a) => `<li>
      <a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title || a.url)}</a>
      <span class="muted">· ${esc(a.applied.slice(0, 10))}</span></li>`).join("")}</ul></div>`).join("")
    : `<p class="muted">No submitted applications yet.</p>`;
  const jobs = await api("/api/jobs");
  $("#jobs-table").className = "list";
  $("#jobs-table").innerHTML = jobs.length
    ? `<tr><th>When</th><th>Status</th><th>Fit</th><th>Company</th><th>Role</th></tr>` + jobs.map((j) => `<tr>
        <td class="muted">${esc((j.updated || "").replace("T", " ").slice(0, 16))}</td>
        <td><span class="status ${esc(j.status)}">${esc(j.status)}</span></td><td>${esc(j.fit ?? "")}</td>
        <td>${esc(j.company)}</td><td><a href="${esc(j.url)}" target="_blank" rel="noopener">${esc(j.title || j.url)}</a></td></tr>`).join("")
    : `<tr><td class="muted">Nothing yet.</td></tr>`;
}

/* ---------- boot ---------- */
loadStatus().then((s) => { if (!s.profile || !s.cv_parsed) showTab("setup"); });
poll();
