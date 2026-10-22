(function () {
  "use strict";
  // The admin panel. Fetches everything from /api/admin/data and draws one tab at a time (#overview, #projects …).
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const CSRF = (document.querySelector('meta[name="csrf"]') || {}).content || "";
  const ME = +document.body.dataset.me;
  const nf0 = new Intl.NumberFormat("nb-NO", { maximumFractionDigits: 0 });
  const fmtWhen = (s) => { if (!s) return "–"; const d = new Date(s); return isNaN(d) ? esc(s) : d.toLocaleString("nb-NO", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }); };
  const date = (s) => (s ? new Date(s + "T00:00:00").toLocaleDateString("nb-NO", { day: "numeric", month: "short", year: "numeric" }) : "–");
  const kb = (n) => (n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " kB");

  async function api(method, url, body) {
    const headers = { "X-CSRF-Token": CSRF };
    if (body) headers["Content-Type"] = "application/json";
    const res = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined, credentials: "same-origin" });
    let data = {}; try { data = await res.json(); } catch (e) {}
    if (res.status === 401 && data.login) { location.href = "/login?next=/admin"; throw new Error(data.error); }
    if (!res.ok) throw new Error(data.error || "Noe gikk galt (" + res.status + ").");
    return data;
  }
  const setStatus = (t, error) => { $("status").textContent = t || ""; $("status").classList.toggle("error", !!error); };
  // an action: run it, show a message, fetch again. Errors are shown at the top and next to the button.
  async function perform(fn, ok, el) {
    try { const r = await fn(); setStatus(typeof ok === "function" ? ok(r) : ok || "Lagret."); await loadData(); }
    catch (e) {
      setStatus(e.message, true);
      if (el && el.isConnected) {
        let p = el.closest("td, .card > div, section") ; p = p || el.parentElement;
        let m = p.querySelector(":scope > .inlineerr");
        if (!m) { m = document.createElement("p"); m.className = "inlineerr"; m.setAttribute("role", "alert"); p.appendChild(m); }
        m.textContent = e.message;
      }
    }
  }
  // dangerous buttons: the first press asks, the second press does it
  function confirmFirst(b, text) {
    if (b.classList.contains("confirm")) return true;
    document.querySelectorAll("button.confirm").forEach((x) => { x.classList.remove("confirm"); x.textContent = x.dataset.tekst || x.textContent; });
    b.dataset.tekst = b.textContent; b.classList.add("confirm"); b.textContent = text; return false;
  }

  let D = null;
  const TABS = ["overview", "projects", "courses", "users", "registrations", "log", "cleanup", "system"];
  const tab = () => (TABS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "overview");
  const syLabel = (sy) => (D && D.schoolYearName[sy]) || `${sy}/${String((+sy + 1) % 100).padStart(2, "0")}`;

  async function loadData() {
    try { D = await api("GET", "/api/admin/data"); draw(); }
    catch (e) { setStatus(e.message, true); }
  }
  function draw() {
    if (!D) return;
    const f = tab();
    const o = D.overview;
    const toClean = o.coursesPending.length + o.withoutCourse.length + o.removed.length + o.bomMissing.length + o.courseWithoutEngineer.length + o.courseWithInactive.length;
    $("tabs").querySelectorAll("a").forEach((a) => {
      const name = a.getAttribute("href").slice(1);
      a.toggleAttribute("aria-current", name === f); if (name === f) a.setAttribute("aria-current", "page");
      if (name === "overview") a.innerHTML = "Oversikt" + (toClean ? `<span class="figure">${toClean}</span>` : "");
    });
    const activeEl = document.activeElement;
    if (activeEl && $("content").contains(activeEl) && activeEl.matches("input[type=text], input[type=password], input[type=search]")) return; // don't redraw over someone who is typing
    ({ overview, projects, courses, users, registrations, log, cleanup, system })[f]();
  }
  window.addEventListener("hashchange", () => { setStatus(""); draw(); });

  // ---------- oversikt ----------
  function overview() {
    const o = D.overview;
    const brief = (heading, items, line, link, blankOut) => `<div class="${items.length ? "obs" : "ok"}"><h3>${heading}: ${items.length}</h3>`
      + (items.length ? `<ul>${items.slice(0, 8).map((x) => `<li>${line(x)}</li>`).join("")}${items.length > 8 ? `<li class="muted">… og ${items.length - 8} til</li>` : ""}</ul>`
        + (link ? `<p style="margin:6px 0 0"><a href="${link}">Rydd →</a></p>` : "") : `<p class="muted" style="margin:0">${blankOut}</p>`) + `</div>`;
    const pr = (r) => `${esc(r.name)} <span class="muted">(${esc(syLabel(r.school_year))})</span>`;
    $("content").innerHTML = `<section><h2>Det som bør ryddes</h2><div class="card">`
      + brief("Emner som venter på godkjenning", o.coursesPending, (e) => `${esc(e.code)} <span class="muted">– ${esc(e.proposedByName || "")}</span>`, "#courses", "Ingen forslag.")
      + brief("Prosjekter uten emne", o.withoutCourse, pr, "#projects", "Alle prosjekter har emne.")
      + brief("Fjernet av faglærer, venter på avgjørelse", o.removed, (r) => pr(r) + ` <span class="muted">– ${esc(r.deletedBy || "")}</span>`, "#projects", "Ingen.")
      + brief("BOM-filen finnes ikke", o.bomMissing, (r) => pr(r) + ` <span class="muted">– ${esc(r.bom)}</span>`, "#projects", "Alle koblede BOM-er finnes.")
      + brief("Aktive emner uten ingeniør", o.courseWithoutEngineer, (e) => esc(e.code), "#courses", "Alle aktive emner har ingeniør.")
      + brief("Emner med deaktivert faglærer eller ingeniør", o.courseWithInactive, (e) => esc(e.code), "#courses", "Ingen.")
      + brief("Aktive emner uten faglærer", o.courseWithoutLecturer, (e) => esc(e.code), "#courses", "Alle aktive emner har faglærer.")
      + `<div class="${o.unacknowledged ? "obs" : "ok"}"><h3>Varsler ingen har kvittert for: ${o.unacknowledged}</h3>`
      + (o.unacknowledged ? `<p style="margin:0" class="muted">Eldste: ${fmtWhen(o.oldestUnacknowledged)}</p><p style="margin:6px 0 0"><a href="#cleanup">Rydd →</a></p>` : `<p class="muted" style="margin:0">Ingen.</p>`) + `</div>`
      + brief("Brukere som aldri har logget inn", o.neverLoggedIn, (u) => `${esc(u.name)} <span class="muted">(${esc(u.username)})</span>`, "#users", "Alle har vært innom.")
      + brief("Låst etter feil passord", o.locked, (u) => esc(u.name), "#users", "Ingen.")
      + `</div></section><section style="margin-top:20px"><h2>Gjennomføringer per skoleår</h2><div class="card">`
      + Object.entries(o.perSchoolYear).map(([sy, n]) => `<div><h3>${esc(syLabel(sy))}</h3><p style="margin:0">${n} ${n === 1 ? "gjennomføring" : "gjennomføringer"}</p></div>`).join("")
      + `</div></section>`;
  }

  // ---------- prosjekter ----------
  const filter = { school_year: "", course: "", engineer: "", search: "", removedOnes: true };
  function projects() {
    const year = [...new Set(D.runs.map((r) => r.school_year))].sort();
    const eng = [...new Map(D.runs.filter((r) => r.engineerId).map((r) => [r.engineerId, r.engineerName])).entries()];
    const rows = D.runs.filter((r) => (!filter.school_year || String(r.school_year) === filter.school_year)
      && (!filter.course || (filter.course === "-" ? !r.courseId : String(r.courseId) === filter.course))
      && (!filter.engineer || (filter.engineer === "-" ? !r.engineerId : String(r.engineerId) === filter.engineer))
      && (!filter.search || r.name.toLowerCase().includes(filter.search.toLowerCase()))
      && (filter.removedOnes || !r.deleted))
      .sort((a, b) => a.school_year - b.school_year || a.start - b.start || a.name.localeCompare(b.name, "nb"));
    const opt = (v, t, chosen) => `<option value="${esc(v)}"${String(v) === String(chosen) ? " selected" : ""}>${esc(t)}</option>`;
    const courseSelect = (r) => `<select data-f="course" aria-label="Emne">${opt("", "uten emne", r.courseId || "")}`
      + D.courses.map((e) => opt(e.id, e.code + (e.active ? "" : " (inaktiv)"), r.courseId || "")).join("") + `</select>`;
    const num = (r, k, min, max, lbl) => `<input type="number" data-f="${k}" min="${min}" max="${max}" value="${r[k] ?? ""}" aria-label="${lbl || k}">`;
    $("content").innerHTML = `<section><div class="filters">`
      + `<label>Skoleår <select id="fSy">${opt("", "alle", filter.school_year)}${year.map((y) => opt(y, syLabel(y), filter.school_year)).join("")}</select></label>`
      + `<label>Emne <select id="fCourse">${opt("", "alle", filter.course)}${opt("-", "uten emne", filter.course)}${D.courses.map((e) => opt(e.id, e.code, filter.course)).join("")}</select></label>`
      + `<label>Ingeniør <select id="fEng">${opt("", "alle", filter.engineer)}${opt("-", "uten ingeniør", filter.engineer)}${eng.map(([id, n]) => opt(id, n, filter.engineer)).join("")}</select></label>`
      + `<label>Søk <input type="search" id="fSearch" value="${esc(filter.search)}" placeholder="prosjektnavn"></label>`
      + `<label style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" id="fRemoved"${filter.removedOnes ? " checked" : ""}> vis fjernede</label>`
      + `<span class="muted">${rows.length} av ${D.runs.length}</span></div>`
      + `<div class="tablebox"><table class="adm-tabell"><thead><tr><th>Skoleår</th><th>Prosjekt</th><th>Emne</th><th>Faglærer · ingeniør</th>`
      + `<th>Uke fra</th><th>til</th><th title="Studenter ÷ per gruppe + ekstra sett">Sett</th><th>Lev.</th><th>Buffer</th><th>BOM</th><th class="num">Bestilt</th><th>Telling</th><th></th></tr></thead><tbody>`
      + (rows.length ? rows.map((r) => `<tr data-id="${esc(r.id)}"${r.deleted ? ' class="fjernet"' : ""}>`
        + `<td><input type="number" data-f="school_year" min="2000" max="2100" value="${r.school_year}" aria-label="Skoleår (startår)"><small>${esc(syLabel(r.school_year))}</small></td>`
        + `<td><input type="text" class="name" data-f="name" value="${esc(r.name)}" aria-label="Navn">${r.deleted ? `<small>Fjernet av ${esc(r.deletedBy || "")} ${fmtWhen(r.deleted)}</small>` : ""}</td>`
        + `<td>${courseSelect(r)}</td><td>${esc(r.lecturerName || "–")}<small>${esc(r.engineerName || "ingen ingeniør")}</small></td>`
        + `<td>${num(r, "start", 1, 53)}</td><td>${num(r, "finish", 1, 53)}</td><td><div class="sets-calc"><span class="sf">${num(r, "students", 0, 2000, "Antall studenter")}<small>studenter</small></span><span class="op">÷</span><span class="sf">${num(r, "group_size", 1, 20, "Studenter per gruppe")}<small>per gruppe</small></span><span class="op">+</span><span class="sf">${num(r, "spare", 0, 99, "Ekstra sett")}<small>ekstra</small></span></div><small>${r.sets != null ? `= ${r.sets} sett` : "–"}</small></td><td>${num(r, "lead", 0, 20)}</td><td>${num(r, "buf", 0, 20)}</td>`
        + `<td>${r.bom ? `${esc(r.bom)}${r.bomExists ? "" : `<small style="color:var(--warn)">finnes ikke</small>`}<br><button type="button" class="mini quiet" data-act="unlink">Fjern kobling</button>` : `<span class="muted">–</span>`}</td>`
        + `<td class="num">${r.orders ? `${r.orders} lev.<small>${r.orderedCost != null ? nf0.format(r.orderedCost) + " kr" : ""}</small>` : "–"}</td>`
        + `<td>${r.count === "written" ? "skrevet til BOM" : r.count === "saved" ? "lagret" : "–"}</td>`
        + `<td>${r.deleted ? `<button type="button" class="mini" data-act="restore">Gjenopprett</button>` : ""}`
        + `<button type="button" class="del" data-act="delete">Slett …</button></td></tr>`).join("")
        : `<tr><td colspan="13" class="blank">Ingen gjennomføringer med dette filteret.</td></tr>`)
      + `</tbody></table></div></section>`;
    $("fSy").onchange = (e) => { filter.school_year = e.target.value; projects(); };
    $("fCourse").onchange = (e) => { filter.course = e.target.value; projects(); };
    $("fEng").onchange = (e) => { filter.engineer = e.target.value; projects(); };
    $("fSearch").oninput = (e) => { filter.search = e.target.value; const pos = e.target.selectionStart; projects(); const s = $("fSearch"); s.focus(); s.setSelectionRange(pos, pos); };
    $("fRemoved").onchange = (e) => { filter.removedOnes = e.target.checked; projects(); };
  }
  document.addEventListener("change", (e) => {
    const t = e.target, tr = t.closest("tr[data-id]");
    if (!tr || !t.dataset.f || !$("content").contains(t)) return;
    const v = t.type === "number" ? (t.value === "" ? null : +t.value) : (t.value || null);
    t.blur();
    perform(() => api("PATCH", `/api/admin/runs/${tr.dataset.id}`, { [t.dataset.f]: v }), "Lagret og logget.", t);
  });

  // ---------- emner ----------
  let newCourse = false;
  function courses() {
    const users = D.users.filter((u) => u.active);
    const pick = (name, role, chosen) => `<select data-cf="${name}" aria-label="${{ lecturer: "Faglærer", engineer: "Ingeniør" }[name] || name}"><option value="">–</option>`
      + users.filter((u) => u.role === role).map((u) => `<option value="${u.id}"${u.id === chosen ? " selected" : ""}>${esc(u.name)}</option>`).join("") + `</select>`;
    const program = (v) => `<select data-cf="program" aria-label="Program">${Object.entries(D.program).map(([k, n]) => `<option value="${k}"${k === v ? " selected" : ""}>${esc(n)}</option>`).join("")}</select>`;
    const row = (e) => `<tr data-course="${e ? e.id : "new"}"${e && !e.active ? ' class="fjernet"' : ""}>`
      + `<td><input type="text" data-cf="code" value="${esc(e ? e.code : "")}" placeholder="IELS1001" aria-label="Kode"></td>`
      + `<td><input type="text" class="name" data-cf="name" value="${esc(e ? e.name : "")}" aria-label="Navn"></td><td>${program(e ? e.program : "bachelor")}</td>`
      + `<td><input type="number" data-cf="students" min="0" max="2000" value="${e && e.students != null ? e.students : ""}" aria-label="Antall studenter i emnet"></td>`
      + `<td>${pick("lecturer", "lecturer", e && e.lecturerId)}</td><td>${pick("engineer", "engineer", e && e.engineerId)}</td>`
      + (e && !e.approved ? `<td class="num">${e.projects}</td><td><small>Foreslått av ${esc(e.proposedByName || "–")}</small>`
           + `<button type="button" class="mini" data-act="courseapprove">Godkjenn</button> <button type="button" class="del" data-act="coursereject">Avvis …</button></td>`
         : e ? `<td class="num">${e.projects}</td><td><label><input type="checkbox" data-cf="active"${e.active ? " checked" : ""}> aktiv</label></td>`
           : `<td></td><td><button type="button" class="mini" data-act="coursesave">Lagre</button> <button type="button" class="mini quiet" data-act="coursecancel">Avbryt</button></td>`) + `</tr>`;
    const head = `<thead><tr><th>Kode</th><th>Navn</th><th>Program</th><th title="Standard for nye prosjekter i emnet">Studenter</th><th>Faglærer</th><th>Ingeniør</th><th class="num">Prosjekter</th><th></th></tr></thead>`;
    const pending = D.courses.filter((e) => !e.approved);
    $("content").innerHTML = (pending.length ? `<section><h2>Forslag som venter på godkjenning (${pending.length})</h2>`
        + `<p class="muted">Rett gjerne kode, navn og personer før du godkjenner. Avviser du et forslag, slettes emnet, og prosjektene i det står uten emne.</p>`
        + `<div class="tablebox"><table class="adm-tabell">${head}<tbody>${pending.map(row).join("")}</tbody></table></div></section>` : "")
      + `<section${pending.length ? ' style="margin-top:20px"' : ""}><div class="row"><h2>Alle emner</h2>${newCourse ? "" : `<button class="tbtn" type="button" data-act="coursenew">+ Nytt emne</button>`}</div>`
      + `<div class="tablebox"><table class="adm-tabell">${head}<tbody>`
      + (newCourse ? row(null) : "") + D.courses.filter((e) => e.approved).map(row).join("") + `</tbody></table></div></section>`;
  }
  const values = (tr, attr) => { const v = {}; tr.querySelectorAll(`[${attr}]`).forEach((el) => { v[el.getAttribute(attr)] = el.type === "checkbox" ? el.checked : (el.value || null); }); return v; };
  document.addEventListener("change", (e) => {
    const t = e.target, tr = t.closest("tr[data-course]");
    if (!tr || tr.dataset.course === "new" || !t.dataset.cf) return;
    t.blur();
    perform(() => api("PATCH", `/api/courses/${tr.dataset.course}`, { [t.dataset.cf]: values(tr, "data-cf")[t.dataset.cf] }), "Emnet er lagret.", t);
  });

  // ---------- brukere ----------
  let newUser = false;
  const pwOpen = new Set();
  function users() {
    const ROLES = { engineer: "Ingeniør", lecturer: "Faglærer" };
    const role = (v) => `<select data-uf="role" aria-label="Rolle">${Object.entries(ROLES).map(([k, n]) => `<option value="${k}"${k === v ? " selected" : ""}>${n}</option>`).join("")}</select>`;
    const row = (u) => {
      if (!u) return `<tr data-user="new"><td><input type="text" data-uf="username" placeholder="brukernavn" aria-label="Brukernavn" autocomplete="off"></td>`
        + `<td><input type="text" class="name" data-uf="name" placeholder="Fullt navn" aria-label="Navn" autocomplete="off"></td><td>${role("lecturer")}</td>`
        + `<td><label><input type="checkbox" data-uf="admin"> admin</label></td>`
        + `<td colspan="2"><input type="password" data-uf="password" placeholder="Startpassord (min. 10 tegn)" aria-label="Startpassord" autocomplete="new-password"></td>`
        + `<td><button type="button" class="mini" data-act="usersave">Lagre</button> <button type="button" class="mini quiet" data-act="usercancel">Avbryt</button></td></tr>`;
      const pw = pwOpen.has(u.id)
        ? `<input type="password" data-pw placeholder="Nytt passord" aria-label="Nytt passord" autocomplete="new-password"> <button type="button" class="mini" data-act="pwsave">Lagre</button> <button type="button" class="mini quiet" data-act="pwcancel">Avbryt</button>`
        : `<button type="button" class="mini quiet" data-act="pwshow">Nytt passord …</button>`
          + (!u.last_login && u.id !== ME ? ` <button type="button" class="del" data-act="userdelete">Slett …</button>` : "");
      return `<tr data-user="${u.id}"${u.active ? "" : ' class="fjernet"'}><td><code>${esc(u.username)}</code>${u.id === ME ? " (deg)" : ""}</td>`
        + `<td><input type="text" class="name" data-uf="name" value="${esc(u.name)}" aria-label="Navn"></td><td>${role(u.role)}</td>`
        + `<td><label><input type="checkbox" data-uf="admin"${u.admin ? " checked" : ""}${u.role !== "engineer" ? " disabled" : ""}> admin</label></td>`
        + `<td><label><input type="checkbox" data-uf="active"${u.active ? " checked" : ""}> aktiv</label>${u.locked_until && u.locked_until > new Date().toISOString() ? ` <span class="tag">låst</span>` : ""}</td>`
        + `<td class="muted">${u.last_login ? fmtWhen(u.last_login) : "aldri"}</td><td>${pw}</td></tr>`;
    };
    $("content").innerHTML = `<section><div class="row"><h2>Brukere</h2>${newUser ? "" : `<button class="tbtn" type="button" data-act="usernew">+ Ny bruker</button>`}</div>`
      + `<p class="muted" style="margin:-4px 0 10px;font-size:12.5px">Deaktiverte brukere kan ikke logge inn, men navnet står i loggene. «Slett» finnes bare for brukere som aldri har logget inn (f.eks. testbrukere). Det må alltid finnes minst én aktiv administrator.</p>`
      + `<div class="tablebox"><table class="adm-tabell"><thead><tr><th>Brukernavn</th><th>Navn</th><th>Rolle</th><th></th><th></th><th>Sist inne</th><th></th></tr></thead><tbody>`
      + (newUser ? row(null) : "") + D.users.map(row).join("") + `</tbody></table></div></section>`;
  }
  document.addEventListener("change", (e) => {
    const t = e.target, tr = t.closest("tr[data-user]");
    if (!tr || tr.dataset.user === "new" || !t.dataset.uf) return;
    t.blur();
    perform(() => api("PATCH", `/api/users/${tr.dataset.user}`, { [t.dataset.uf]: values(tr, "data-bf")[t.dataset.uf] }), "Brukeren er lagret.", t);
  });

  // ---------- orders and counts ----------
  function registrations() {
    const b = D.orders, t = D.counts;
    $("content").innerHTML = `<section><h2>Bestillinger (${b.length})</h2>`
      + `<p class="muted" style="margin:-4px 0 10px;font-size:12.5px">Fjern en bestilling som er registrert ved en feil. Er noe lagt på lager, trekkes det fra «På lager» i BOM-en først («Angre mottak»).</p>`
      + `<div class="tablebox"><table class="adm-tabell"><thead><tr><th>Skoleår</th><th>Prosjekt</th><th>Leverandør</th><th>Status</th><th>Bestilt</th><th>Ordrenr</th><th>Av</th><th class="num">Linjer</th><th></th></tr></thead><tbody>`
      + (b.length ? b.map((r) => `<tr data-gid="${esc(r.gid)}" data-supplier="${esc(r.key)}"><td>${esc(syLabel(r.school_year))}</td><td>${esc(r.project)}</td><td>${esc(r.supplier)}</td>`
        + `<td>${esc(r.statusName)}${r.addedToStock ? `<small>${r.addedToStock} ${r.addedToStock === 1 ? "linje" : "linjer"} lagt på lager</small>` : ""}</td>`
        + `<td>${date(r.date)}</td><td>${esc(r.order_no || "–")}</td><td>${esc(r.by || "–")}</td><td class="num">${r.lines}</td>`
        + `<td>${r.addedToStock ? `<button type="button" class="mini" data-act="undoreceipt">Angre mottak …</button>` : `<button type="button" class="del" data-act="orderdelete">Fjern …</button>`}</td></tr>`).join("")
        : `<tr><td colspan="9" class="blank">Ingen bestillinger.</td></tr>`)
      + `</tbody></table></div></section><section style="margin-top:20px"><h2>Tellinger (${t.length})</h2>`
      + `<p class="muted" style="margin:-4px 0 10px;font-size:12.5px">En telling som står i BOM-en angres først (setter «På lager» tilbake), så kan den fjernes.</p>`
      + `<div class="tablebox"><table class="adm-tabell"><thead><tr><th>Skoleår</th><th>Prosjekt</th><th>Telt</th><th class="num">Linjer</th><th>I BOM-en</th><th></th></tr></thead><tbody>`
      + (t.length ? t.map((r) => `<tr data-gid="${esc(r.gid)}"><td>${esc(syLabel(r.school_year))}</td><td>${esc(r.project)}</td><td>${date(r.date)}</td><td class="num">${r.counted_lines}</td>`
        + `<td>${r.inBom ? `${r.inBom} ${r.inBom === 1 ? "linje" : "linjer"}${r.pending ? `<small>${r.pending} endret, ikke skrevet</small>` : ""}` : "nei"}</td>`
        + `<td>${r.inBom ? `<button type="button" class="mini" data-act="countundo">Angre (sett «På lager» tilbake) …</button>` : `<button type="button" class="del" data-act="countdelete">Fjern …</button>`}</td></tr>`).join("")
        : `<tr><td colspan="6" class="blank">Ingen tellinger.</td></tr>`)
      + `</tbody></table></div></section>`;
  }
  const stockMsg = (r) => (r.written.length ? `«På lager» endret for ${r.written.length} ${r.written.length === 1 ? "linje" : "linjer"}. Sikkerhetskopi: ${r.backup}.` : "Ingenting ble endret i BOM-en.")
    + (r.conflict.length ? ` ${r.conflict.length} ble ikke endret (ville blitt negativt eller er endret i Excel).` : "");

  // ---------- endringslogg ----------
  const lf = { user: "", project: "", role: "", from: "", to: "" };
  let logRows = null;
  function log() {
    const opt = (v, t) => `<option value="${v}"${lf.role === v ? " selected" : ""}>${t}</option>`;
    $("content").innerHTML = `<section><form class="filters" id="logForm">`
      + `<label>Hvem <input type="search" name="user" value="${esc(lf.user)}"></label>`
      + `<label>Prosjekt/emne <input type="search" name="project" value="${esc(lf.project)}"></label>`
      + `<label>Rolle <select name="role">${opt("", "alle")}${opt("lecturer", "faglærer")}${opt("engineer", "ingeniør")}${opt("admin", "admin")}</select></label>`
      + `<label>Fra <input type="date" name="from" value="${esc(lf.from)}"></label><label>Til <input type="date" name="to" value="${esc(lf.to)}"></label>`
      + `<button class="pbtn" type="submit">Søk</button></form><div id="logTable"><p class="muted">Henter …</p></div></section>`;
    $("logForm").onsubmit = (e) => { e.preventDefault(); new FormData(e.target).forEach((v, k) => { lf[k] = v; }); fetchLog(); };
    if (logRows) drawLog(); else fetchLog();
  }
  async function fetchLog() {
    const q = Object.entries({ ...lf, limit: 500 }).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
    try { logRows = await api("GET", "/api/admin/log?" + q); drawLog(); }
    catch (e) { setStatus(e.message, true); }
  }
  function drawLog() {
    const ACTIONS = { created: "la til", changed: "endret", removed: "fjernet", deleted: "slettet", restored: "gjenopprettet",
      engineer_assigned: "valgte ingeniør", engineer_removed: "tok av ingeniør",
      course_approved: "godkjente emnet", course_rejected: "avviste emnet" };
    if (!$("logTable")) return;
    $("logTable").innerHTML = `<p class="muted" style="font-size:12.5px">${logRows.length}${logRows.length === 500 ? "+ (de nyeste 500)" : ""} oppføringer</p>`
      + `<div class="tablebox"><table class="adm-tabell"><thead><tr><th>Når</th><th>Hvem</th><th>Rolle</th><th>Prosjekt/emne</th><th>Hva</th><th>Fra → til</th><th>Kvittert</th></tr></thead><tbody>`
      + (logRows.length ? logRows.map((e) => `<tr><td>${fmtWhen(e.time)}</td><td>${esc(e.user_name)}</td><td>${esc(e.role)}</td>`
        + `<td>${esc(e.project_name)}${e.school_year ? `<small>${esc(syLabel(e.school_year))}</small>` : ""}</td>`
        + `<td>${esc(ACTIONS[e.action] || e.action)}${e.field ? ` <span class="muted">${esc(e.field)}</span>` : ""}</td>`
        + `<td>${e.from_value != null || e.to_value != null ? `${esc(e.from_value ?? "–")} → <b>${esc(e.to_value ?? "–")}</b>` : ""}</td>`
        + `<td>${e.acknowledged ? fmtWhen(e.acknowledged) : (e.role === "lecturer" || e.recipient_id ? `<span class="tag soon">venter</span>` : "")}</td></tr>`).join("")
        : `<tr><td colspan="7" class="blank">Ingenting funnet.</td></tr>`)
      + `</tbody></table></div>`;
  }

  // ---------- opprydding ----------
  let syConsequence = null;
  function cleanup() {
    const o = D.overview, year = Object.keys(o.perSchoolYear);
    $("content").innerHTML = `<section class="card">`
      + `<div><h3>Kvitter alle varsler</h3><p class="muted" style="margin:0 0 8px">${o.unacknowledged} varsler venter på «OK, sett»${o.oldestUnacknowledged ? ` (eldste ${fmtWhen(o.oldestUnacknowledged)})` : ""}. Marker alle som sett, f.eks. etter at noen har sluttet.</p>`
      + `<button type="button" class="mini" data-act="ackall"${o.unacknowledged ? "" : " disabled"}>Kvitter alle …</button></div>`
      + `<div><h3>Slett et gammelt skoleår</h3><p class="muted" style="margin:0 0 8px">Sletter gjennomføringene, bestillingene og tellingene i skoleåret. Det som står i BOM-ene («På lager») blir stående – det er dagens lager. Ta en sikkerhetskopi først.</p>`
      + `<select id="deleteSy"><option value="">Velg skoleår …</option>${year.map((y) => `<option value="${y}">${esc(syLabel(y))}</option>`).join("")}</select>`
      + `<div id="syCons" style="margin-top:8px"></div></div>`
      + `<div><h3>Testbrukere</h3><p class="muted" style="margin:0">${o.neverLoggedIn.length} brukere har aldri logget inn. De kan slettes under <a href="#users">Brukere</a>; andre deaktiveres.</p></div>`
      + `</section>`;
    $("deleteSy").onchange = async (e) => {
      const sy = e.target.value; $("syCons").innerHTML = "";
      if (!sy) return;
      try {
        syConsequence = await api("GET", `/api/admin/school-years/${sy}/consequence`);
        const k = syConsequence;
        $("syCons").innerHTML = `<p style="margin:0 0 8px">${k.name}: ${k.runs} gjennomføringer, ${k.orders} bestillinger og ${k.counts} tellinger blir slettet.</p>`
          + `<button type="button" class="del" data-act="deletesy" data-sy="${sy}">Slett ${esc(k.name)} …</button>`;
      } catch (err) { setStatus(err.message, true); }
    };
  }

  // ---------- system ----------
  function system() {
    const s = D.system;
    $("content").innerHTML = `<section><h2>System</h2><div class="tablebox"><table class="info-table"><tbody>`
      + `<tr><th>Modus</th><td>${esc(s.mode)}</td></tr>`
      + `<tr><th>Database</th><td><code>${esc(s.dbFile)}</code> · ${kb(s.dbSize)} · versjon ${s.dbVersion}</td></tr>`
      + `<tr><th>Datamappe</th><td><code>${esc(s.dataFolder)}</code></td></tr>`
      + `<tr><th>BOM-mappe</th><td><code>${esc(s.bomFolder)}</code> · ${s.bomReachable ? `nåbar, ${s.bomCount} BOM-er` : `<b style="color:var(--warn)">ikke nåbar</b>`}</td></tr>`
      + `</tbody></table></div></section><section style="margin-top:20px"><div class="row"><h2>Sikkerhetskopi av databasen</h2>`
      + `<button class="pbtn" type="button" data-act="backup">Ta sikkerhetskopi nå</button></div>`
      + `<p class="muted" style="margin:-4px 0 10px;font-size:12.5px">Kopiene lagres i <code>${esc(s.backupFolder)}</code>. Trygt å ta mens årshjulet er i bruk. BOM-ene er Excel-filer på sin egen mappe og er ikke med.</p>`
      + (s.backups.length ? `<ul>${s.backups.map((k) => `<li><a href="/api/admin/backup/${encodeURIComponent(k.name)}">${esc(k.name)}</a> <span class="muted">· ${kb(k.size)}</span></li>`).join("")}</ul>` : `<p class="muted">Ingen kopier ennå.</p>`)
      + `</section>`;
  }

  // ---------- knapper ----------
  document.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act]"); if (!b || !$("content").contains(b) && !b.closest("#content")) return;
    const a = b.dataset.act, tr = b.closest("tr"), gid = tr && (tr.dataset.id || tr.dataset.gid);
    const G = (file_path) => `/api/admin/runs/${encodeURIComponent(gid)}${file_path}`;
    if (a === "unlink") perform(() => api("PATCH", G(""), { bom: null }), "BOM-koblingen er fjernet (filen er ikke slettet).", b);
    if (a === "restore") perform(() => api("POST", G("/restore")), "Prosjektet er gjenopprettet.", b);
    if (a === "delete") {
      if (!b.classList.contains("confirm")) {
        api("GET", G("/consequence")).then((k) => {
          const shares = [k.orders && `${k.orders} bestilling(er)`, k.count && "tellingen", k.lastOfProject && "selve prosjektet"].filter(Boolean);
          confirmFirst(b, `Slett ${k.name} ${k.school_year}${shares.length ? " + " + shares.join(", ") : ""}`);
          if (k.addedToStock || k.countInBom) setStatus("Merk: noe fra dette prosjektet står i en BOM («På lager»). Det blir stående i Excel-filen.");
        }).catch((err) => setStatus(err.message, true));
        return;
      }
      perform(() => api("DELETE", G("")), "Gjennomføringen er slettet.", b);
    }
    if (a === "undoreceipt" && confirmFirst(b, "Bekreft: trekk fra «På lager»")) perform(() => api("POST", G("/undo-receipt?supplier=" + encodeURIComponent(tr.dataset.supplier))), stockMsg, b);
    if (a === "orderdelete" && confirmFirst(b, "Bekreft: fjern bestillingen")) perform(() => api("DELETE", G("/order?supplier=" + encodeURIComponent(tr.dataset.supplier))), "Bestillingen er fjernet.", b);
    if (a === "countundo" && confirmFirst(b, "Bekreft: sett «På lager» tilbake")) perform(() => api("POST", G("/count/undo")), stockMsg, b);
    if (a === "countdelete" && confirmFirst(b, "Bekreft: fjern tellingen")) perform(() => api("DELETE", G("/count")), "Tellingen er fjernet.", b);
    if (a === "coursenew") { newCourse = true; courses(); }
    if (a === "coursecancel") { newCourse = false; courses(); }
    if (a === "courseapprove") perform(() => api("POST", `/api/admin/courses/${tr.dataset.course}/approve`), (r) => `${r.code} er godkjent og kan brukes av alle.`, b);
    if (a === "coursereject" && confirmFirst(b, "Bekreft: avvis og slett")) perform(() => api("DELETE", `/api/admin/courses/${tr.dataset.course}`),
      (r) => "Forslaget er avvist" + (r.projects ? ` – ${r.projects} ${r.projects === 1 ? "prosjekt står" : "prosjekter står"} nå uten emne.` : "."), b);
    if (a === "coursesave") perform(async () => { const r = await api("POST", "/api/courses", values(tr, "data-cf")); newCourse = false; return r; }, (r) => `Emnet ${r.code} er lagt til.`, b);
    if (a === "usernew") { newUser = true; users(); }
    if (a === "usercancel") { newUser = false; users(); }
    if (a === "usersave") perform(async () => { const r = await api("POST", "/api/users", values(tr, "data-bf")); newUser = false; return r; }, (r) => `Brukeren «${r.username}» er laget.`, b);
    if (a === "pwshow" || a === "pwcancel") { const id = +tr.dataset.user; a === "pwshow" ? pwOpen.add(id) : pwOpen.delete(id); users(); }
    if (a === "pwsave") { const id = +tr.dataset.user; perform(async () => { const r = await api("PATCH", `/api/users/${id}`, { password: tr.querySelector("[data-pw]").value }); pwOpen.delete(id); return r; }, (r) => `Nytt passord for «${r.username}».`, b); }
    if (a === "userdelete" && confirmFirst(b, "Bekreft: slett for godt")) perform(() => api("DELETE", `/api/admin/users/${tr.dataset.user}`), "Brukeren er slettet.", b);
    if (a === "ackall" && confirmFirst(b, "Bekreft: kvitter alle")) perform(() => api("POST", "/api/admin/acknowledge-all"), (r) => `${r.acknowledged} varsler er kvittert.`, b);
    if (a === "deletesy" && confirmFirst(b, "Bekreft: slett for godt")) perform(() => api("DELETE", `/api/admin/school-years/${b.dataset.sy}`), (r) => `Slettet ${r.deleted} gjennomføringer.`, b);
    if (a === "backup") perform(() => api("POST", "/api/admin/backup"), (r) => `Sikkerhetskopi lagret: ${r.name} (${kb(r.size)}).`, b);
  });

  window.addEventListener("error", (ev) => setStatus("Noe gikk galt: " + ((ev.error && ev.error.message) || ev.message)));
  window.addEventListener("unhandledrejection", (ev) => setStatus("Noe gikk galt: " + ((ev.reason && ev.reason.message) || ev.reason)));
  window.addEventListener("focus", () => { if (tab() !== "log") loadData(); });
  loadData();
})();
