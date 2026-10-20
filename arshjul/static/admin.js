(function () {
  "use strict";
  // Administratorpanelet. Henter alt fra /api/admin/data og tegner én fane om gangen (#oversikt, #prosjekter …).
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const CSRF = (document.querySelector('meta[name="csrf"]') || {}).content || "";
  const MEG = +document.body.dataset.meg;
  const nf0 = new Intl.NumberFormat("nb-NO", { maximumFractionDigits: 0 });
  const tid = (s) => { if (!s) return "–"; const d = new Date(s); return isNaN(d) ? esc(s) : d.toLocaleString("nb-NO", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }); };
  const dato = (s) => (s ? new Date(s + "T00:00:00").toLocaleDateString("nb-NO", { day: "numeric", month: "short", year: "numeric" }) : "–");
  const kb = (n) => (n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " kB");

  async function api(method, url, body) {
    const headers = { "X-CSRF-Token": CSRF };
    if (body) headers["Content-Type"] = "application/json";
    const res = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined, credentials: "same-origin" });
    let data = {}; try { data = await res.json(); } catch (e) {}
    if (res.status === 401 && data.login) { location.href = "/login?neste=/admin"; throw new Error(data.feil); }
    if (!res.ok) throw new Error(data.feil || "Noe gikk galt (" + res.status + ").");
    return data;
  }
  const setStatus = (t, feil) => { $("status").textContent = t || ""; $("status").classList.toggle("feil", !!feil); };
  // en handling: kjør, vis melding, hent på nytt. Feil vises øverst og ved knappen.
  async function gjor(fn, ok, el) {
    try { const r = await fn(); setStatus(typeof ok === "function" ? ok(r) : ok || "Lagret."); await last(); }
    catch (e) {
      setStatus(e.message, true);
      if (el && el.isConnected) {
        let p = el.closest("td, .kort > div, section") ; p = p || el.parentElement;
        let m = p.querySelector(":scope > .inlineerr");
        if (!m) { m = document.createElement("p"); m.className = "inlineerr"; m.setAttribute("role", "alert"); p.appendChild(m); }
        m.textContent = e.message;
      }
    }
  }
  // farlige knapper: første trykk spør, andre trykk gjør det
  function bekreft(b, tekst) {
    if (b.classList.contains("confirm")) return true;
    document.querySelectorAll("button.confirm").forEach((x) => { x.classList.remove("confirm"); x.textContent = x.dataset.tekst || x.textContent; });
    b.dataset.tekst = b.textContent; b.classList.add("confirm"); b.textContent = tekst; return false;
  }

  let D = null;
  const FANER = ["oversikt", "prosjekter", "emner", "brukere", "registreringer", "logg", "opprydding", "system"];
  const fane = () => (FANER.includes(location.hash.slice(1)) ? location.hash.slice(1) : "oversikt");
  const syNavn = (sy) => (D && D.skolearNavn[sy]) || `${sy}/${String((+sy + 1) % 100).padStart(2, "0")}`;

  async function last() {
    try { D = await api("GET", "/api/admin/data"); tegn(); }
    catch (e) { setStatus(e.message, true); }
  }
  function tegn() {
    if (!D) return;
    const f = fane();
    const o = D.oversikt;
    const ryddes = o.utenEmne.length + o.fjernet.length + o.bomMangler.length + o.emneUtenIngenior.length + o.emneMedInaktiv.length;
    $("faner").querySelectorAll("a").forEach((a) => {
      const navn = a.getAttribute("href").slice(1);
      a.toggleAttribute("aria-current", navn === f); if (navn === f) a.setAttribute("aria-current", "page");
      if (navn === "oversikt") a.innerHTML = "Oversikt" + (ryddes ? `<span class="tall">${ryddes}</span>` : "");
    });
    const aktivt = document.activeElement;
    if (aktivt && $("innhold").contains(aktivt) && aktivt.matches("input[type=text], input[type=password], input[type=search]")) return; // ikke tegn over noen som skriver
    ({ oversikt, prosjekter, emner, brukere, registreringer, logg, opprydding, system })[f]();
  }
  window.addEventListener("hashchange", () => { setStatus(""); tegn(); });

  // ---------- oversikt ----------
  function oversikt() {
    const o = D.oversikt;
    const kort = (tittel, liste, linje, lenke, tom) => `<div class="${liste.length ? "obs" : "ok"}"><h3>${tittel}: ${liste.length}</h3>`
      + (liste.length ? `<ul>${liste.slice(0, 8).map((x) => `<li>${linje(x)}</li>`).join("")}${liste.length > 8 ? `<li class="muted">… og ${liste.length - 8} til</li>` : ""}</ul>`
        + (lenke ? `<p style="margin:6px 0 0"><a href="${lenke}">Rydd →</a></p>` : "") : `<p class="muted" style="margin:0">${tom}</p>`) + `</div>`;
    const pr = (r) => `${esc(r.navn)} <span class="muted">(${esc(syNavn(r.skolear))})</span>`;
    $("innhold").innerHTML = `<section><h2>Det som bør ryddes</h2><div class="kort">`
      + kort("Prosjekter uten emne", o.utenEmne, pr, "#prosjekter", "Alle prosjekter har emne.")
      + kort("Fjernet av faglærer, venter på avgjørelse", o.fjernet, (r) => pr(r) + ` <span class="muted">– ${esc(r.slettetAv || "")}</span>`, "#prosjekter", "Ingen.")
      + kort("BOM-filen finnes ikke", o.bomMangler, (r) => pr(r) + ` <span class="muted">– ${esc(r.bom)}</span>`, "#prosjekter", "Alle koblede BOM-er finnes.")
      + kort("Aktive emner uten ingeniør", o.emneUtenIngenior, (e) => esc(e.kode), "#emner", "Alle aktive emner har ingeniør.")
      + kort("Emner med deaktivert faglærer eller ingeniør", o.emneMedInaktiv, (e) => esc(e.kode), "#emner", "Ingen.")
      + kort("Aktive emner uten faglærer", o.emneUtenFaglarer, (e) => esc(e.kode), "#emner", "Alle aktive emner har faglærer.")
      + `<div class="${o.ukvitterte ? "obs" : "ok"}"><h3>Varsler ingen har kvittert for: ${o.ukvitterte}</h3>`
      + (o.ukvitterte ? `<p style="margin:0" class="muted">Eldste: ${tid(o.eldsteUkvitterte)}</p><p style="margin:6px 0 0"><a href="#opprydding">Rydd →</a></p>` : `<p class="muted" style="margin:0">Ingen.</p>`) + `</div>`
      + kort("Brukere som aldri har logget inn", o.aldriInne, (u) => `${esc(u.navn)} <span class="muted">(${esc(u.brukernavn)})</span>`, "#brukere", "Alle har vært innom.")
      + kort("Låst etter feil passord", o.last, (u) => esc(u.navn), "#brukere", "Ingen.")
      + `</div></section><section style="margin-top:20px"><h2>Gjennomføringer per skoleår</h2><div class="kort">`
      + Object.entries(o.perSkolear).map(([sy, n]) => `<div><h3>${esc(syNavn(sy))}</h3><p style="margin:0">${n} ${n === 1 ? "gjennomføring" : "gjennomføringer"}</p></div>`).join("")
      + `</div></section>`;
  }

  // ---------- prosjekter ----------
  const filter = { skolear: "", emne: "", ingenior: "", sok: "", fjernede: true };
  function prosjekter() {
    const ar = [...new Set(D.gjennomforinger.map((r) => r.skolear))].sort();
    const ing = [...new Map(D.gjennomforinger.filter((r) => r.ingeniorId).map((r) => [r.ingeniorId, r.ingeniorNavn])).entries()];
    const rader = D.gjennomforinger.filter((r) => (!filter.skolear || String(r.skolear) === filter.skolear)
      && (!filter.emne || (filter.emne === "-" ? !r.emneId : String(r.emneId) === filter.emne))
      && (!filter.ingenior || (filter.ingenior === "-" ? !r.ingeniorId : String(r.ingeniorId) === filter.ingenior))
      && (!filter.sok || r.navn.toLowerCase().includes(filter.sok.toLowerCase()))
      && (filter.fjernede || !r.slettet))
      .sort((a, b) => a.skolear - b.skolear || a.start - b.start || a.navn.localeCompare(b.navn, "nb"));
    const opt = (v, t, valgt) => `<option value="${esc(v)}"${String(v) === String(valgt) ? " selected" : ""}>${esc(t)}</option>`;
    const emneValg = (r) => `<select data-f="emne" aria-label="Emne">${opt("", "uten emne", r.emneId || "")}`
      + D.emner.map((e) => opt(e.id, e.kode + (e.aktiv ? "" : " (inaktiv)"), r.emneId || "")).join("") + `</select>`;
    const num = (r, k, min, max, lbl) => `<input type="number" data-f="${k}" min="${min}" max="${max}" value="${r[k] ?? ""}" aria-label="${lbl || k}">`;
    $("innhold").innerHTML = `<section><div class="filtre">`
      + `<label>Skoleår <select id="fSy">${opt("", "alle", filter.skolear)}${ar.map((y) => opt(y, syNavn(y), filter.skolear)).join("")}</select></label>`
      + `<label>Emne <select id="fEmne">${opt("", "alle", filter.emne)}${opt("-", "uten emne", filter.emne)}${D.emner.map((e) => opt(e.id, e.kode, filter.emne)).join("")}</select></label>`
      + `<label>Ingeniør <select id="fIng">${opt("", "alle", filter.ingenior)}${opt("-", "uten ingeniør", filter.ingenior)}${ing.map(([id, n]) => opt(id, n, filter.ingenior)).join("")}</select></label>`
      + `<label>Søk <input type="search" id="fSok" value="${esc(filter.sok)}" placeholder="prosjektnavn"></label>`
      + `<label style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" id="fFj"${filter.fjernede ? " checked" : ""}> vis fjernede</label>`
      + `<span class="muted">${rader.length} av ${D.gjennomforinger.length}</span></div>`
      + `<div class="tablebox"><table class="adm-tabell"><thead><tr><th>Skoleår</th><th>Prosjekt</th><th>Emne</th><th>Faglærer · ingeniør</th>`
      + `<th>Uke fra</th><th>til</th><th title="Studenter ÷ per gruppe + ekstra sett">Sett</th><th>Lev.</th><th>Buffer</th><th>BOM</th><th class="num">Bestilt</th><th>Telling</th><th></th></tr></thead><tbody>`
      + (rader.length ? rader.map((r) => `<tr data-id="${esc(r.id)}"${r.slettet ? ' class="fjernet"' : ""}>`
        + `<td><input type="number" data-f="skolear" min="2000" max="2100" value="${r.skolear}" aria-label="Skoleår (startår)"><small>${esc(syNavn(r.skolear))}</small></td>`
        + `<td><input type="text" class="navn" data-f="navn" value="${esc(r.navn)}" aria-label="Navn">${r.slettet ? `<small>Fjernet av ${esc(r.slettetAv || "")} ${tid(r.slettet)}</small>` : ""}</td>`
        + `<td>${emneValg(r)}</td><td>${esc(r.faglarerNavn || "–")}<small>${esc(r.ingeniorNavn || "ingen ingeniør")}</small></td>`
        + `<td>${num(r, "start", 1, 53)}</td><td>${num(r, "slutt", 1, 53)}</td><td><div class="settcalc"><span class="sf">${num(r, "studenter", 0, 2000, "Antall studenter")}<small>studenter</small></span><span class="op">÷</span><span class="sf">${num(r, "gruppe", 1, 20, "Studenter per gruppe")}<small>per gruppe</small></span><span class="op">+</span><span class="sf">${num(r, "ekstra", 0, 99, "Ekstra sett")}<small>ekstra</small></span></div><small>${r.sett != null ? `= ${r.sett} sett` : "–"}</small></td><td>${num(r, "lev", 0, 20)}</td><td>${num(r, "buf", 0, 20)}</td>`
        + `<td>${r.bom ? `${esc(r.bom)}${r.bomFinnes ? "" : `<small style="color:var(--warn)">finnes ikke</small>`}<br><button type="button" class="mini quiet" data-act="frakoble">Fjern kobling</button>` : `<span class="muted">–</span>`}</td>`
        + `<td class="num">${r.bestillinger ? `${r.bestillinger} lev.<small>${r.bestiltKr != null ? nf0.format(r.bestiltKr) + " kr" : ""}</small>` : "–"}</td>`
        + `<td>${r.telling === "skrevet" ? "skrevet til BOM" : r.telling === "lagret" ? "lagret" : "–"}</td>`
        + `<td>${r.slettet ? `<button type="button" class="mini" data-act="gjenopprett">Gjenopprett</button>` : ""}`
        + `<button type="button" class="del" data-act="slett">Slett …</button></td></tr>`).join("")
        : `<tr><td colspan="13" class="blank">Ingen gjennomføringer med dette filteret.</td></tr>`)
      + `</tbody></table></div></section>`;
    $("fSy").onchange = (e) => { filter.skolear = e.target.value; prosjekter(); };
    $("fEmne").onchange = (e) => { filter.emne = e.target.value; prosjekter(); };
    $("fIng").onchange = (e) => { filter.ingenior = e.target.value; prosjekter(); };
    $("fSok").oninput = (e) => { filter.sok = e.target.value; const pos = e.target.selectionStart; prosjekter(); const s = $("fSok"); s.focus(); s.setSelectionRange(pos, pos); };
    $("fFj").onchange = (e) => { filter.fjernede = e.target.checked; prosjekter(); };
  }
  document.addEventListener("change", (e) => {
    const t = e.target, tr = t.closest("tr[data-id]");
    if (!tr || !t.dataset.f || !$("innhold").contains(t)) return;
    const v = t.type === "number" ? (t.value === "" ? null : +t.value) : (t.value || null);
    t.blur();
    gjor(() => api("PATCH", `/api/admin/gjennomforinger/${tr.dataset.id}`, { [t.dataset.f]: v }), "Lagret og logget.", t);
  });

  // ---------- emner ----------
  let nyttEmne = false;
  function emner() {
    const brukere = D.brukere.filter((u) => u.aktiv);
    const velg = (navn, rolle, valgt) => `<select data-ef="${navn}" aria-label="${navn}"><option value="">–</option>`
      + brukere.filter((u) => u.rolle === rolle).map((u) => `<option value="${u.id}"${u.id === valgt ? " selected" : ""}>${esc(u.navn)}</option>`).join("") + `</select>`;
    const program = (v) => `<select data-ef="program" aria-label="Program">${Object.entries(D.program).map(([k, n]) => `<option value="${k}"${k === v ? " selected" : ""}>${esc(n)}</option>`).join("")}</select>`;
    const rad = (e) => `<tr data-emne="${e ? e.id : "ny"}"${e && !e.aktiv ? ' class="fjernet"' : ""}>`
      + `<td><input type="text" data-ef="kode" value="${esc(e ? e.kode : "")}" placeholder="IELS1001" aria-label="Kode"></td>`
      + `<td><input type="text" class="navn" data-ef="navn" value="${esc(e ? e.navn : "")}" aria-label="Navn"></td><td>${program(e ? e.program : "bachelor")}</td>`
      + `<td><input type="number" data-ef="studenter" min="0" max="2000" value="${e && e.studenter != null ? e.studenter : ""}" aria-label="Antall studenter i emnet"></td>`
      + `<td>${velg("faglarer", "faglarer", e && e.faglarerId)}</td><td>${velg("ingenior", "ingenior", e && e.ingeniorId)}</td>`
      + (e ? `<td class="num">${e.prosjekter}</td><td><label><input type="checkbox" data-ef="aktiv"${e.aktiv ? " checked" : ""}> aktiv</label></td>`
           : `<td></td><td><button type="button" class="mini" data-act="emnelagre">Lagre</button> <button type="button" class="mini quiet" data-act="emneavbryt">Avbryt</button></td>`) + `</tr>`;
    $("innhold").innerHTML = `<section><div class="row"><h2>Alle emner</h2>${nyttEmne ? "" : `<button class="tbtn" type="button" data-act="emneny">+ Nytt emne</button>`}</div>`
      + `<div class="tablebox"><table class="adm-tabell"><thead><tr><th>Kode</th><th>Navn</th><th>Program</th><th title="Standard for nye prosjekter i emnet">Studenter</th><th>Faglærer</th><th>Ingeniør</th><th class="num">Prosjekter</th><th></th></tr></thead><tbody>`
      + (nyttEmne ? rad(null) : "") + D.emner.map(rad).join("") + `</tbody></table></div></section>`;
  }
  const verdier = (tr, attr) => { const v = {}; tr.querySelectorAll(`[${attr}]`).forEach((el) => { v[el.getAttribute(attr)] = el.type === "checkbox" ? el.checked : (el.value || null); }); return v; };
  document.addEventListener("change", (e) => {
    const t = e.target, tr = t.closest("tr[data-emne]");
    if (!tr || tr.dataset.emne === "ny" || !t.dataset.ef) return;
    t.blur();
    gjor(() => api("PATCH", `/api/emner/${tr.dataset.emne}`, { [t.dataset.ef]: verdier(tr, "data-ef")[t.dataset.ef] }), "Emnet er lagret.", t);
  });

  // ---------- brukere ----------
  let nyBruker = false;
  const pwApen = new Set();
  function brukere() {
    const ROLLE = { ingenior: "Ingeniør", faglarer: "Faglærer" };
    const rolle = (v) => `<select data-bf="rolle" aria-label="Rolle">${Object.entries(ROLLE).map(([k, n]) => `<option value="${k}"${k === v ? " selected" : ""}>${n}</option>`).join("")}</select>`;
    const rad = (u) => {
      if (!u) return `<tr data-bruker="ny"><td><input type="text" data-bf="brukernavn" placeholder="brukernavn" aria-label="Brukernavn" autocomplete="off"></td>`
        + `<td><input type="text" class="navn" data-bf="navn" placeholder="Fullt navn" aria-label="Navn" autocomplete="off"></td><td>${rolle("faglarer")}</td>`
        + `<td><label><input type="checkbox" data-bf="admin"> admin</label></td>`
        + `<td colspan="2"><input type="password" data-bf="passord" placeholder="Startpassord (min. 10 tegn)" aria-label="Startpassord" autocomplete="new-password"></td>`
        + `<td><button type="button" class="mini" data-act="brukerlagre">Lagre</button> <button type="button" class="mini quiet" data-act="brukeravbryt">Avbryt</button></td></tr>`;
      const pw = pwApen.has(u.id)
        ? `<input type="password" data-pw placeholder="Nytt passord" aria-label="Nytt passord" autocomplete="new-password"> <button type="button" class="mini" data-act="pwlagre">Lagre</button> <button type="button" class="mini quiet" data-act="pwavbryt">Avbryt</button>`
        : `<button type="button" class="mini quiet" data-act="pwvis">Nytt passord …</button>`
          + (!u.sist_inne && u.id !== MEG ? ` <button type="button" class="del" data-act="brukerslett">Slett …</button>` : "");
      return `<tr data-bruker="${u.id}"${u.aktiv ? "" : ' class="fjernet"'}><td><code>${esc(u.brukernavn)}</code>${u.id === MEG ? " (deg)" : ""}</td>`
        + `<td><input type="text" class="navn" data-bf="navn" value="${esc(u.navn)}" aria-label="Navn"></td><td>${rolle(u.rolle)}</td>`
        + `<td><label><input type="checkbox" data-bf="admin"${u.admin ? " checked" : ""}${u.rolle !== "ingenior" ? " disabled" : ""}> admin</label></td>`
        + `<td><label><input type="checkbox" data-bf="aktiv"${u.aktiv ? " checked" : ""}> aktiv</label>${u.last_til && u.last_til > new Date().toISOString() ? ` <span class="tag">låst</span>` : ""}</td>`
        + `<td class="muted">${u.sist_inne ? tid(u.sist_inne) : "aldri"}</td><td>${pw}</td></tr>`;
    };
    $("innhold").innerHTML = `<section><div class="row"><h2>Brukere</h2>${nyBruker ? "" : `<button class="tbtn" type="button" data-act="brukerny">+ Ny bruker</button>`}</div>`
      + `<p class="muted" style="margin:-4px 0 10px;font-size:12.5px">Deaktiverte brukere kan ikke logge inn, men navnet står i loggene. «Slett» finnes bare for brukere som aldri har logget inn (f.eks. testbrukere). Det må alltid finnes minst én aktiv administrator.</p>`
      + `<div class="tablebox"><table class="adm-tabell"><thead><tr><th>Brukernavn</th><th>Navn</th><th>Rolle</th><th></th><th></th><th>Sist inne</th><th></th></tr></thead><tbody>`
      + (nyBruker ? rad(null) : "") + D.brukere.map(rad).join("") + `</tbody></table></div></section>`;
  }
  document.addEventListener("change", (e) => {
    const t = e.target, tr = t.closest("tr[data-bruker]");
    if (!tr || tr.dataset.bruker === "ny" || !t.dataset.bf) return;
    t.blur();
    gjor(() => api("PATCH", `/api/brukere/${tr.dataset.bruker}`, { [t.dataset.bf]: verdier(tr, "data-bf")[t.dataset.bf] }), "Brukeren er lagret.", t);
  });

  // ---------- bestillinger og tellinger ----------
  function registreringer() {
    const b = D.bestillinger, t = D.tellinger;
    $("innhold").innerHTML = `<section><h2>Bestillinger (${b.length})</h2>`
      + `<p class="muted" style="margin:-4px 0 10px;font-size:12.5px">Fjern en bestilling som er registrert ved en feil. Er noe lagt på lager, trekkes det fra «På lager» i BOM-en først («Angre mottak»).</p>`
      + `<div class="tablebox"><table class="adm-tabell"><thead><tr><th>Skoleår</th><th>Prosjekt</th><th>Leverandør</th><th>Status</th><th>Bestilt</th><th>Ordrenr</th><th>Av</th><th class="num">Linjer</th><th></th></tr></thead><tbody>`
      + (b.length ? b.map((r) => `<tr data-gid="${esc(r.gid)}" data-lev="${esc(r.key)}"><td>${esc(syNavn(r.skolear))}</td><td>${esc(r.prosjekt)}</td><td>${esc(r.leverandor)}</td>`
        + `<td>${esc(r.statusNavn)}${r.lagtPaaLager ? `<small>${r.lagtPaaLager} ${r.lagtPaaLager === 1 ? "linje" : "linjer"} lagt på lager</small>` : ""}</td>`
        + `<td>${dato(r.dato)}</td><td>${esc(r.ordrenr || "–")}</td><td>${esc(r.av || "–")}</td><td class="num">${r.linjer}</td>`
        + `<td>${r.lagtPaaLager ? `<button type="button" class="mini" data-act="angremottak">Angre mottak …</button>` : `<button type="button" class="del" data-act="bestslett">Fjern …</button>`}</td></tr>`).join("")
        : `<tr><td colspan="9" class="blank">Ingen bestillinger.</td></tr>`)
      + `</tbody></table></div></section><section style="margin-top:20px"><h2>Tellinger (${t.length})</h2>`
      + `<p class="muted" style="margin:-4px 0 10px;font-size:12.5px">En telling som står i BOM-en angres først (setter «På lager» tilbake), så kan den fjernes.</p>`
      + `<div class="tablebox"><table class="adm-tabell"><thead><tr><th>Skoleår</th><th>Prosjekt</th><th>Telt</th><th class="num">Linjer</th><th>I BOM-en</th><th></th></tr></thead><tbody>`
      + (t.length ? t.map((r) => `<tr data-gid="${esc(r.gid)}"><td>${esc(syNavn(r.skolear))}</td><td>${esc(r.prosjekt)}</td><td>${dato(r.dato)}</td><td class="num">${r.telte}</td>`
        + `<td>${r.iBom ? `${r.iBom} ${r.iBom === 1 ? "linje" : "linjer"}${r.ventende ? `<small>${r.ventende} endret, ikke skrevet</small>` : ""}` : "nei"}</td>`
        + `<td>${r.iBom ? `<button type="button" class="mini" data-act="tellangre">Angre (sett «På lager» tilbake) …</button>` : `<button type="button" class="del" data-act="tellslett">Fjern …</button>`}</td></tr>`).join("")
        : `<tr><td colspan="6" class="blank">Ingen tellinger.</td></tr>`)
      + `</tbody></table></div></section>`;
  }
  const lagerMsg = (r) => (r.skrevet.length ? `«På lager» endret for ${r.skrevet.length} ${r.skrevet.length === 1 ? "linje" : "linjer"}. Sikkerhetskopi: ${r.backup}.` : "Ingenting ble endret i BOM-en.")
    + (r.konflikt.length ? ` ${r.konflikt.length} ble ikke endret (ville blitt negativt eller er endret i Excel).` : "");

  // ---------- endringslogg ----------
  const lf = { bruker: "", prosjekt: "", rolle: "", fra: "", til: "" };
  let loggRader = null;
  function logg() {
    const opt = (v, t) => `<option value="${v}"${lf.rolle === v ? " selected" : ""}>${t}</option>`;
    $("innhold").innerHTML = `<section><form class="filtre" id="loggForm">`
      + `<label>Hvem <input type="search" name="bruker" value="${esc(lf.bruker)}"></label>`
      + `<label>Prosjekt/emne <input type="search" name="prosjekt" value="${esc(lf.prosjekt)}"></label>`
      + `<label>Rolle <select name="rolle">${opt("", "alle")}${opt("faglarer", "faglærer")}${opt("ingenior", "ingeniør")}${opt("admin", "admin")}</select></label>`
      + `<label>Fra <input type="date" name="fra" value="${esc(lf.fra)}"></label><label>Til <input type="date" name="til" value="${esc(lf.til)}"></label>`
      + `<button class="pbtn" type="submit">Søk</button></form><div id="loggTabell"><p class="muted">Henter …</p></div></section>`;
    $("loggForm").onsubmit = (e) => { e.preventDefault(); new FormData(e.target).forEach((v, k) => { lf[k] = v; }); hentLogg(); };
    if (loggRader) tegnLogg(); else hentLogg();
  }
  async function hentLogg() {
    const q = Object.entries({ ...lf, grense: 500 }).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
    try { loggRader = await api("GET", "/api/admin/logg?" + q); tegnLogg(); }
    catch (e) { setStatus(e.message, true); }
  }
  function tegnLogg() {
    const HANDLING = { opprettet: "la til", endret: "endret", fjernet: "fjernet", slettet: "slettet", gjenopprettet: "gjenopprettet",
      valgt_ingenior: "valgte ingeniør", fjernet_ingenior: "tok av ingeniør" };
    if (!$("loggTabell")) return;
    $("loggTabell").innerHTML = `<p class="muted" style="font-size:12.5px">${loggRader.length}${loggRader.length === 500 ? "+ (de nyeste 500)" : ""} oppføringer</p>`
      + `<div class="tablebox"><table class="adm-tabell"><thead><tr><th>Når</th><th>Hvem</th><th>Rolle</th><th>Prosjekt/emne</th><th>Hva</th><th>Fra → til</th><th>Kvittert</th></tr></thead><tbody>`
      + (loggRader.length ? loggRader.map((e) => `<tr><td>${tid(e.tid)}</td><td>${esc(e.bruker_navn)}</td><td>${esc(e.rolle)}</td>`
        + `<td>${esc(e.prosjekt_navn)}${e.skolear ? `<small>${esc(syNavn(e.skolear))}</small>` : ""}</td>`
        + `<td>${esc(HANDLING[e.handling] || e.handling)}${e.felt ? ` <span class="muted">${esc(e.felt)}</span>` : ""}</td>`
        + `<td>${e.fra != null || e.til != null ? `${esc(e.fra ?? "–")} → <b>${esc(e.til ?? "–")}</b>` : ""}</td>`
        + `<td>${e.kvittert ? tid(e.kvittert) : (e.rolle === "faglarer" || e.mottaker_id ? `<span class="tag soon">venter</span>` : "")}</td></tr>`).join("")
        : `<tr><td colspan="7" class="blank">Ingenting funnet.</td></tr>`)
      + `</tbody></table></div>`;
  }

  // ---------- opprydding ----------
  let syKonsekvens = null;
  function opprydding() {
    const o = D.oversikt, ar = Object.keys(o.perSkolear);
    $("innhold").innerHTML = `<section class="kort">`
      + `<div><h3>Kvitter alle varsler</h3><p class="muted" style="margin:0 0 8px">${o.ukvitterte} varsler venter på «OK, sett»${o.eldsteUkvitterte ? ` (eldste ${tid(o.eldsteUkvitterte)})` : ""}. Marker alle som sett, f.eks. etter at noen har sluttet.</p>`
      + `<button type="button" class="mini" data-act="kvitteralle"${o.ukvitterte ? "" : " disabled"}>Kvitter alle …</button></div>`
      + `<div><h3>Slett et gammelt skoleår</h3><p class="muted" style="margin:0 0 8px">Sletter gjennomføringene, bestillingene og tellingene i skoleåret. Det som står i BOM-ene («På lager») blir stående – det er dagens lager. Ta en sikkerhetskopi først.</p>`
      + `<select id="slettSy"><option value="">Velg skoleår …</option>${ar.map((y) => `<option value="${y}">${esc(syNavn(y))}</option>`).join("")}</select>`
      + `<div id="syKons" style="margin-top:8px"></div></div>`
      + `<div><h3>Testbrukere</h3><p class="muted" style="margin:0">${o.aldriInne.length} brukere har aldri logget inn. De kan slettes under <a href="#brukere">Brukere</a>; andre deaktiveres.</p></div>`
      + `</section>`;
    $("slettSy").onchange = async (e) => {
      const sy = e.target.value; $("syKons").innerHTML = "";
      if (!sy) return;
      try {
        syKonsekvens = await api("GET", `/api/admin/skolear/${sy}/konsekvens`);
        const k = syKonsekvens;
        $("syKons").innerHTML = `<p style="margin:0 0 8px">${k.navn}: ${k.gjennomforinger} gjennomføringer, ${k.bestillinger} bestillinger og ${k.tellinger} tellinger blir slettet.</p>`
          + `<button type="button" class="del" data-act="slettsy" data-sy="${sy}">Slett ${esc(k.navn)} …</button>`;
      } catch (err) { setStatus(err.message, true); }
    };
  }

  // ---------- system ----------
  function system() {
    const s = D.system;
    $("innhold").innerHTML = `<section><h2>System</h2><div class="tablebox"><table class="infotabell"><tbody>`
      + `<tr><th>Modus</th><td>${esc(s.modus)}</td></tr>`
      + `<tr><th>Database</th><td><code>${esc(s.dbFil)}</code> · ${kb(s.dbStorrelse)} · versjon ${s.dbVersjon}</td></tr>`
      + `<tr><th>Datamappe</th><td><code>${esc(s.datamappe)}</code></td></tr>`
      + `<tr><th>BOM-mappe</th><td><code>${esc(s.bomMappe)}</code> · ${s.bomNaabar ? `nåbar, ${s.antallBom} BOM-er` : `<b style="color:var(--warn)">ikke nåbar</b>`}</td></tr>`
      + `</tbody></table></div></section><section style="margin-top:20px"><div class="row"><h2>Sikkerhetskopi av databasen</h2>`
      + `<button class="pbtn" type="button" data-act="kopi">Ta sikkerhetskopi nå</button></div>`
      + `<p class="muted" style="margin:-4px 0 10px;font-size:12.5px">Kopiene lagres i <code>${esc(s.kopiMappe)}</code>. Trygt å ta mens årshjulet er i bruk. BOM-ene er Excel-filer på sin egen mappe og er ikke med.</p>`
      + (s.kopier.length ? `<ul>${s.kopier.map((k) => `<li><a href="/api/admin/sikkerhetskopi/${encodeURIComponent(k.navn)}">${esc(k.navn)}</a> <span class="muted">· ${kb(k.storrelse)}</span></li>`).join("")}</ul>` : `<p class="muted">Ingen kopier ennå.</p>`)
      + `</section>`;
  }

  // ---------- knapper ----------
  document.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act]"); if (!b || !$("innhold").contains(b) && !b.closest("#innhold")) return;
    const a = b.dataset.act, tr = b.closest("tr"), gid = tr && (tr.dataset.id || tr.dataset.gid);
    const G = (sti) => `/api/admin/gjennomforinger/${encodeURIComponent(gid)}${sti}`;
    if (a === "frakoble") gjor(() => api("PATCH", G(""), { bom: null }), "BOM-koblingen er fjernet (filen er ikke slettet).", b);
    if (a === "gjenopprett") gjor(() => api("POST", G("/gjenopprett")), "Prosjektet er gjenopprettet.", b);
    if (a === "slett") {
      if (!b.classList.contains("confirm")) {
        api("GET", G("/konsekvens")).then((k) => {
          const deler = [k.bestillinger && `${k.bestillinger} bestilling(er)`, k.telling && "tellingen", k.sisteAvProsjektet && "selve prosjektet"].filter(Boolean);
          bekreft(b, `Slett ${k.navn} ${k.skolear}${deler.length ? " + " + deler.join(", ") : ""}`);
          if (k.lagtPaaLager || k.tellingIBom) setStatus("Merk: noe fra dette prosjektet står i en BOM («På lager»). Det blir stående i Excel-filen.");
        }).catch((err) => setStatus(err.message, true));
        return;
      }
      gjor(() => api("DELETE", G("")), "Gjennomføringen er slettet.", b);
    }
    if (a === "angremottak" && bekreft(b, "Bekreft: trekk fra «På lager»")) gjor(() => api("POST", G("/angre-mottak?leverandor=" + encodeURIComponent(tr.dataset.lev))), lagerMsg, b);
    if (a === "bestslett" && bekreft(b, "Bekreft: fjern bestillingen")) gjor(() => api("DELETE", G("/bestilling?leverandor=" + encodeURIComponent(tr.dataset.lev))), "Bestillingen er fjernet.", b);
    if (a === "tellangre" && bekreft(b, "Bekreft: sett «På lager» tilbake")) gjor(() => api("POST", G("/telling/angre")), lagerMsg, b);
    if (a === "tellslett" && bekreft(b, "Bekreft: fjern tellingen")) gjor(() => api("DELETE", G("/telling")), "Tellingen er fjernet.", b);
    if (a === "emneny") { nyttEmne = true; emner(); }
    if (a === "emneavbryt") { nyttEmne = false; emner(); }
    if (a === "emnelagre") gjor(async () => { const r = await api("POST", "/api/emner", verdier(tr, "data-ef")); nyttEmne = false; return r; }, (r) => `Emnet ${r.kode} er lagt til.`, b);
    if (a === "brukerny") { nyBruker = true; brukere(); }
    if (a === "brukeravbryt") { nyBruker = false; brukere(); }
    if (a === "brukerlagre") gjor(async () => { const r = await api("POST", "/api/brukere", verdier(tr, "data-bf")); nyBruker = false; return r; }, (r) => `Brukeren «${r.brukernavn}» er laget.`, b);
    if (a === "pwvis" || a === "pwavbryt") { const id = +tr.dataset.bruker; a === "pwvis" ? pwApen.add(id) : pwApen.delete(id); brukere(); }
    if (a === "pwlagre") { const id = +tr.dataset.bruker; gjor(async () => { const r = await api("PATCH", `/api/brukere/${id}`, { passord: tr.querySelector("[data-pw]").value }); pwApen.delete(id); return r; }, (r) => `Nytt passord for «${r.brukernavn}».`, b); }
    if (a === "brukerslett" && bekreft(b, "Bekreft: slett for godt")) gjor(() => api("DELETE", `/api/admin/brukere/${tr.dataset.bruker}`), "Brukeren er slettet.", b);
    if (a === "kvitteralle" && bekreft(b, "Bekreft: kvitter alle")) gjor(() => api("POST", "/api/admin/kvitter-alle"), (r) => `${r.kvittert} varsler er kvittert.`, b);
    if (a === "slettsy" && bekreft(b, "Bekreft: slett for godt")) gjor(() => api("DELETE", `/api/admin/skolear/${b.dataset.sy}`), (r) => `Slettet ${r.slettet} gjennomføringer.`, b);
    if (a === "kopi") gjor(() => api("POST", "/api/admin/sikkerhetskopi"), (r) => `Sikkerhetskopi lagret: ${r.navn} (${kb(r.storrelse)}).`, b);
  });

  window.addEventListener("error", (ev) => setStatus("Noe gikk galt: " + ((ev.error && ev.error.message) || ev.message)));
  window.addEventListener("unhandledrejection", (ev) => setStatus("Noe gikk galt: " + ((ev.reason && ev.reason.message) || ev.reason)));
  window.addEventListener("focus", () => { if (fane() !== "logg") last(); });
  last();
})();
