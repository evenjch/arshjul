(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  let CLOSED = [];
  const NCOLORS = 8;
  const col = (p) => `var(--p${((p.color ?? 0) % NCOLORS) + 1})`;
  // projects = alle gjennomføringer i alle skoleår; hjulet og tabellen viser det valgte skoleåret (SY)
  let mode = "loading", projects = [], ledigeBom = [];
  let LOKAL = true; // false når årshjulet kjører på en server: da kan vi ikke åpne Excel eller mapper
  let SYS = [], SY = null, IDAG = null, NESTE8 = [], OPPSETT = null;
  // innlogget bruker; ING = ingeniør (alt), ellers faglærer (egne prosjekter, ingen BOM/bestilling)
  let MEG = { rolle: document.body.dataset.rolle || "ingenior" }, ING = MEG.rolle === "ingenior";
  let ENDRINGER = [], SLETTEDE = [], FAGLARERE = [], INGENIORER = [], EMNER = [], PROGRAM = {}, BRUKERE = [];
  // ALLE = alt serveren sendte; projects = det som vises. En ingeniør ser egne emner, eller hele instituttet.
  let ALLE = [];
  let VIS_ALLE = false;
  try { VIS_ALLE = localStorage.getItem("arshjul.visAlle") === "1"; } catch (e) {}
  const synlige = () => (ING && !VIS_ALLE ? ALLE.filter((p) => p.egen) : ALLE);
  const hasBom = (p) => !!(p.bomStatus && p.bomStatus.finnes && !p.bomStatus.feil && p.bomStatus.linjer > 0);
  const nf2 = new Intl.NumberFormat("nb-NO", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  // ---------- server ----------
  const CSRF = (document.querySelector('meta[name="csrf"]') || {}).content || "";
  async function api(method, url, body) {
    const headers = { "X-CSRF-Token": CSRF };
    if (body) headers["Content-Type"] = "application/json";
    const res = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined, credentials: "same-origin" });
    let data = {}; try { data = await res.json(); } catch (e) {}
    if (res.status === 401 && data.login) { location.href = "/login?neste=" + encodeURIComponent(location.pathname); throw new Error(data.feil); }
    if (!res.ok) throw new Error(data.feil || "Noe gikk galt (" + res.status + ").");
    return data;
  }
  function setStatus(t) { $("status").textContent = t; }

  let loading = null, again = false;
  function load() {
    if (loading) { again = true; return loading; }
    loading = doLoad().finally(() => { loading = null; if (again) { again = false; load(); } });
    return loading;
  }
  async function doLoad() {
    let st;
    try { st = await api("GET", "/api/state"); }
    catch (e) {
      setStatus(e instanceof TypeError ? "Får ikke kontakt med årshjulet. Kjører «Start årshjul»-vinduet fortsatt?" : e.message);
      return;
    }
    try {
      CLOSED = st.stengte; ledigeBom = st.ledigeBom;
      SYS = st.skolear; IDAG = st.idag; NESTE8 = st.neste8; OPPSETT = st.oppsett;
      MEG = st.meg; ING = MEG.rolle === "ingenior"; document.body.dataset.rolle = MEG.rolle;
      ENDRINGER = st.endringer; SLETTEDE = st.slettede; FAGLARERE = st.faglarere;
      INGENIORER = st.ingeniorer; EMNER = st.emner; PROGRAM = st.program; BRUKERE = st.brukere;
      document.body.dataset.admin = MEG.admin ? "1" : "0";
      if (SY === null || !SYS.some((s) => s.ar === SY)) SY = st.skolearNa;
      LOKAL = st.modus !== "server";
      $("openBomDir").hidden = $("openListDir").hidden = !LOKAL;
      $("bomDir").textContent = st.mapper.bom || ""; $("openBomDir").title = "Åpner " + (st.mapper.bom || "BOM-mappen");
      // behold verdier som venter på lagring
      st.prosjekter.forEach((p) => { const pend = pending[p.id]; if (pend) Object.assign(p, pend.patch); });
      ALLE = st.prosjekter;
      const nye = synlige();
      const sameIds = nye.length === projects.length && nye.every((p) => projects.some((q) => q.id === p.id));
      projects = nye; mode = "ready";
      const ae = document.activeElement;
      const typing = ae && ae.matches && ae.matches("#tbody input, #tbody select[data-role]");
      renderAll(sameIds && !!typing);
      drawClosed();
    } catch (e) {
      // en feil i årshjulets egen kode, ikke i kontakten med serveren
      console.error(e);
      setStatus("Noe gikk galt da siden skulle tegnes: " + e.message + ". Last siden på nytt, og si fra hvis det skjer igjen.");
    }
  }

  const pending = {};
  function queueUpdate(id, patch) {
    const pend = pending[id] || (pending[id] = { patch: {}, timer: 0, chain: Promise.resolve() });
    Object.assign(pend.patch, patch);
    clearTimeout(pend.timer);
    pend.timer = setTimeout(() => {
      const body = pend.patch; pend.patch = {};
      pend.chain = pend.chain.then(() => api("PATCH", P(id), body))
        .then(() => { if (!Object.keys(pend.patch).length) { delete pending[id]; load(); } })
        .catch((e) => setStatus(e.message));
    }, 500);
  }

  // ---------- uker og datoer (regnes ut på serveren, her bare plassert i hjulet) ----------
  const WEEK = 7 * 864e5;
  const toD = (s) => Date.parse(s + "T00:00:00Z");
  const weeksBetween = (a, b) => Math.round((toD(b) - toD(a)) / WEEK);
  const dfmt = new Intl.DateTimeFormat("nb-NO", { day: "numeric", month: "short", timeZone: "UTC" });
  const dfmtY = new Intl.DateTimeFormat("nb-NO", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  const range = (dato) => { const m = toD(dato); return dfmt.format(m) + "–" + dfmt.format(m + 6 * 864e5); };
  const inRange = (w, a, b) => (a <= b ? w >= a && w <= b : w >= a || w <= b);
  const nf0 = new Intl.NumberFormat("nb-NO", { maximumFractionDigits: 0 });
  const curSY = () => SYS.find((s) => s.ar === SY);
  const inSY = () => projects.filter((p) => p.skolear === SY);
  // Bestillingsstatus sett opp mot fristen:
  //   tillegg = BOM eller sett er endret etter bestilling · ok = alt bestilt · late = frist passert, ikke bestilt
  //   soon = frist innen 2 uker · pending = god tid · gone = prosjektet har startet uten registrert bestilling
  function orderState(p) {
    const b = p.bestilling;
    if (b && b.tillegg) return "tillegg";
    if (b && b.ferdig) return "ok";
    if (!IDAG) return "pending";
    if (p.frister.start.dato <= IDAG.dato) return "gone";
    if (p.frister.frist.dato < IDAG.dato) return "late";
    return weeksBetween(IDAG.dato, p.frister.frist.dato) <= 2 ? "soon" : "pending";
  }
  const STATE_COLOR = { ok: "var(--ok)", late: "var(--warn)", soon: "var(--soon)", tillegg: "var(--soon)" };
  const sorted = () => inSY().sort((a, b) => toD(a.frister.start.dato) - toD(b.frister.start.dato) || String(a.navn).localeCompare(b.navn, "nb"));

  // ---------- hjul ----------
  const C = 300, R_OUT = 252, R_IN = 96;
  let N = 52, SY_START = "";
  const pos = (dato) => weeksBetween(SY_START, dato); // uker fra skoleårets start, kan være < 0 eller ≥ N
  const wrapPos = (x) => ((x % N) + N) % N;
  const ang = (x) => (x / N) * Math.PI * 2 - Math.PI / 2;
  const pt = (r, a) => [C + r * Math.cos(a), C + r * Math.sin(a)];
  const f = (n) => n.toFixed(2);
  function sector(r0, r1, p0, p1) {
    const a0 = ang(p0), a1 = ang(p1), large = p1 - p0 > N / 2 ? 1 : 0;
    const [x0, y0] = pt(r1, a0), [x1, y1] = pt(r1, a1), [x2, y2] = pt(r0, a1), [x3, y3] = pt(r0, a0);
    return `M${f(x0)} ${f(y0)}A${r1} ${r1} 0 ${large} 1 ${f(x1)} ${f(y1)}L${f(x2)} ${f(y2)}A${r0} ${r0} 0 ${large} 0 ${f(x3)} ${f(y3)}Z`;
  }
  const useSpan = (p) => [pos(p.frister.start.dato), pos(p.frister.slutt.dato) + 1];
  const leadSpan = (p) => [pos(p.frister.frist.dato), pos(p.frister.start.dato)];
  let laneOf = new Map(), laneR = () => [0, 0, 0, 0];
  const MONTHS = ["jan", "feb", "mar", "apr", "mai", "jun", "jul", "aug", "sep", "okt", "nov", "des"];

  // Prosjekter deler nivå når verken bruksperiodene eller bestillingsperiodene overlapper.
  // Bruk tegnes i det brede ytre båndet, bestilling og levering i en smal skinne innerst.
  const slots = ([a, b]) => { const out = []; for (let x = a; x < Math.max(b, a + 1); x++) out.push(wrapPos(x)); return out; };
  function assignLanes(list) {
    const lanes = [], map = new Map();
    list.forEach((p) => {
      const use = slots(useSpan(p)), lead = slots(leadSpan(p));
      let j = lanes.findIndex((L) => !use.some((w) => L.use[w]) && !lead.some((w) => L.lead[w]));
      if (j < 0) { lanes.push({ use: [], lead: [] }); j = lanes.length - 1; }
      use.forEach((w) => (lanes[j].use[w] = true)); lead.forEach((w) => (lanes[j].lead[w] = true));
      map.set(p.id, j);
    });
    return { map, count: lanes.length };
  }

  function drawWheel() {
    const cur = curSY();
    if (!cur) { $("wheel").innerHTML = ""; return; }
    N = cur.uker.length; SY_START = cur.start;
    $("syName").textContent = cur.navn; $("syTable").textContent = cur.navn;
    $("syPrev").disabled = SYS[0].ar === SY; $("syNext").disabled = SYS[SYS.length - 1].ar === SY;
    const list = sorted();
    const L = assignLanes(list); laneOf = L.map;
    const n = Math.max(L.count, 1);
    const step = Math.min(34, (232 - R_IN) / n);
    const band = Math.max(8, step * 0.8);
    // [indre kant, ytre kant, skinne ytre kant, bruk indre kant]
    laneR = (j) => { const o = 232 - j * step, r0 = o - band; return [r0, o, r0 + band * 0.26, r0 + band * 0.36]; };
    const inner = L.count ? laneR(L.count - 1)[0] - 6 : R_IN;

    let s = `<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="var(--card)"/><rect width="3" height="6" fill="var(--closed)"/></pattern></defs>`;
    // månedsringen starter i august
    const t0 = toD(cur.start);
    for (let m = 0; m < 12; m++) {
      const mi = (7 + m) % 12, y = SY + (7 + m >= 12 ? 1 : 0);
      const p0 = m === 0 ? 0 : (Date.UTC(y, mi, 1) - t0) / WEEK;
      const p1 = m === 11 ? N : (Date.UTC(mi === 11 ? y + 1 : y, (mi + 1) % 12, 1) - t0) / WEEK;
      s += `<path d="${sector(R_OUT, R_OUT + 20, p0, p1)}" fill="${m % 2 ? "var(--bg)" : "var(--card)"}" stroke="var(--line)"/>`;
      const [x, yy] = pt(R_OUT + 10, ang((p0 + p1) / 2));
      s += `<text x="${x.toFixed(1)}" y="${yy.toFixed(1)}" text-anchor="middle" dominant-baseline="central" font-size="11" font-weight="600">${MONTHS[mi]}</text>`;
    }
    cur.uker.forEach((u, i) => {
      const a = ang(i), long = u.uke % 4 === 0;
      const [x0, y0] = pt(R_OUT, a), [x1, y1] = pt(R_OUT - (long ? 9 : 4), a);
      s += `<line x1="${x0.toFixed(1)}" y1="${y0.toFixed(1)}" x2="${x1.toFixed(1)}" y2="${y1.toFixed(1)}" stroke="var(--muted)" stroke-width="${long ? 1 : .6}" opacity=".6"/>`;
      if ([1, 10, 20, 32, 40, 50].includes(u.uke)) {
        const [x, y] = pt(R_OUT - 16, ang(i + .5));
        s += `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="middle" dominant-baseline="central" font-size="8.5" style="font-family:var(--mono)">${u.uke}</text>`;
      }
    });
    // stengte perioder: sammenhengende stengte uker
    for (let i = 0; i < N; i++) {
      if (!cur.uker[i].stengt || (i > 0 && cur.uker[i - 1].stengt)) continue;
      let j = i; while (j + 1 < N && cur.uker[j + 1].stengt) j++;
      const a = cur.uker[i].uke, b = cur.uker[j].uke;
      const navn = CLOSED.filter((c) => inRange(a, c.fra, c.til)).map((c) => c.navn).join(", ") || "Stengt";
      s += `<path d="${sector(inner, R_OUT - 20, i, j + 1)}" fill="url(#hatch)" opacity=".85"><title>${esc(navn)} (uke ${a}${a !== b ? "–" + b : ""})</title></path>`;
    }
    for (let j = 0; j < L.count; j++) {
      const [r0, r1] = laneR(j);
      s += `<path d="${sector(r0, r1, 0, N - 0.001)}" fill="none" stroke="var(--line)" stroke-dasharray="2 3"/>`;
    }
    const mr = Math.max(3, Math.min(6, band * 0.2));
    list.forEach((p) => {
      const d = p.frister, [r0, r1, rl, ru] = laneR(laneOf.get(p.id)), c = col(p);
      s += `<g class="proj" data-id="${esc(p.id)}">`;
      const lead = leadSpan(p);
      if (lead[1] > lead[0]) {
        s += `<path class="ring-hit" data-kind="lead" d="${sector(r0, rl, lead[0], lead[1])}" fill="${c}" opacity=".45"><title>${esc(p.navn)}: bestilling og levering fra uke ${d.frist.uke}${d.frist.ar !== d.start.ar ? " " + d.frist.ar : ""}</title></path>`;
      }
      const use = useSpan(p);
      s += `<path class="ring-hit" data-kind="use" d="${sector(ru, r1, use[0], use[1])}" fill="${c}"><title>${esc(p.navn)}: i bruk uke ${d.start.uke}${d.start.uke !== d.slutt.uke ? "–" + d.slutt.uke : ""}</title></path>`;
      const [fx, fy] = pt((r0 + rl) / 2, ang(pos(d.frist.dato) + .5));
      const os = orderState(p), ring = ING ? STATE_COLOR[os] : null; // bestillingsstatus er ingeniørens sak
      if (ring) s += `<circle cx="${fx.toFixed(1)}" cy="${fy.toFixed(1)}" r="${(mr + 3).toFixed(1)}" fill="none" stroke="${ring}" stroke-width="2.5"/>`;
      s += `<circle cx="${fx.toFixed(1)}" cy="${fy.toFixed(1)}" r="${mr.toFixed(1)}" fill="${c}" stroke="var(--card)" stroke-width="2"><title>${esc(p.navn)}: bestillingsfrist uke ${d.frist.uke}, ${dfmtY.format(toD(d.frist.dato))} · ${esc(stateText(p))}</title></circle>`;
      if (p.type === "sett") {
        const [qx, qy] = pt((ru + r1) / 2, ang(pos(d.retur.dato) + .5)), q = mr * 0.85;
        s += `<rect x="${(qx - q).toFixed(1)}" y="${(qy - q).toFixed(1)}" width="${(2 * q).toFixed(1)}" height="${(2 * q).toFixed(1)}" fill="var(--card)" stroke="${c}" stroke-width="2.5"><title>${esc(p.navn)}: innlevering og telling uke ${d.retur.uke}</title></rect>`;
      }
      s += `</g>`;
    });
    const tpos = IDAG ? pos(IDAG.dato) : -1, iAr = tpos >= 0 && tpos < N;
    if (iAr) {
      const [tx0, ty0] = pt(inner - 4, ang(tpos + .5)), [tx1, ty1] = pt(R_OUT + 20, ang(tpos + .5));
      s += `<line x1="${tx0.toFixed(1)}" y1="${ty0.toFixed(1)}" x2="${tx1.toFixed(1)}" y2="${ty1.toFixed(1)}" stroke="var(--accent)" stroke-width="2"/>`;
    }
    s += `<g id="hl" class="hlmark"></g>`;
    const next = nextFrist();
    s += `<text x="${C}" y="${C - 26}" text-anchor="middle" font-size="11" font-weight="700" letter-spacing=".08em">${iAr ? "I DAG" : "SKOLEÅR"}</text>`;
    s += `<text x="${C}" y="${C + 2}" text-anchor="middle" font-size="26" font-weight="700" style="fill:var(--accent);font-family:var(--mono)">${iAr ? "uke " + IDAG.uke : esc(cur.navn)}</text>`;
    if (mode === "loading") {
      s += `<text x="${C}" y="${C + 26}" text-anchor="middle" font-size="11">Henter prosjekter …</text>`;
    } else if (!list.length) {
      s += `<text x="${C}" y="${C + 26}" text-anchor="middle" font-size="11">Ingen prosjekter dette skoleåret</text>`;
    } else if (next && iAr) {
      s += `<text x="${C}" y="${C + 26}" text-anchor="middle" font-size="11">neste frist: uke ${next.uke}</text>`;
      s += `<text x="${C}" y="${C + 41}" text-anchor="middle" font-size="11" font-weight="600" style="fill:var(--ink)">${esc(short(next.p.navn))}</text>`;
    }
    $("wheel").innerHTML = s;
  }
  const short = (n) => (String(n).length > 22 ? String(n).slice(0, 20) + "…" : n);

  // fremhev én hendelse (uke) på ett prosjekts nivå
  function markEvent(id, kind, dato, uke) {
    const hl = document.getElementById("hl"); if (!hl) return;
    const p = projects.find((x) => x.id === id);
    if (!p || !laneOf.has(id)) { hl.innerHTML = ""; return; }
    const d = p.frister, [r0, r1, rl, ru] = laneR(laneOf.get(id)), x = pos(dato);
    let a = x, b = x + 1, lo = ru, hi = r1;
    if (kind === "start") [a, b] = useSpan(p);
    else if (kind === "prep") { a = pos(d.prep.dato); b = pos(d.frist.dato) + 1; lo = r0; hi = rl; }
    else if (kind === "frist") { lo = r0; hi = rl; }
    let s = `<path d="${sector(lo - 3, hi + 3, a, b)}" fill="none" stroke="var(--ink)" stroke-width="2.5" stroke-linejoin="round"/>`;
    const [lx, ly] = pt(R_OUT - 30, ang(x + .5));
    const [wx0, wy0] = pt(hi + 3, ang(x + .5)), [wx1, wy1] = pt(R_OUT - 22, ang(x + .5));
    s += `<line x1="${wx0.toFixed(1)}" y1="${wy0.toFixed(1)}" x2="${wx1.toFixed(1)}" y2="${wy1.toFixed(1)}" stroke="var(--ink)" stroke-width="1.5" stroke-dasharray="3 3"/>`;
    s += `<rect x="${(lx - 22).toFixed(1)}" y="${(ly - 10).toFixed(1)}" width="44" height="20" rx="6" fill="var(--ink)"/>`;
    s += `<text x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="middle" dominant-baseline="central" font-size="11" font-weight="700" style="fill:var(--card);font-family:var(--mono)">uke ${uke}</text>`;
    hl.innerHTML = s;
  }

  // hendelser fra alle skoleår, så frister i juni for høstens prosjekter kommer med
  function allEvents() {
    const out = [];
    projects.forEach((p) => p.hendelser.forEach((e) => out.push({ ...e, p })));
    return out;
  }
  // neste frist som fortsatt krever en bestilling
  function nextFrist() {
    if (!IDAG) return null;
    return allEvents().filter((e) => e.kind === "frist" && e.dato >= IDAG.dato && orderState(e.p) !== "ok")
      .sort((a, b) => toD(a.dato) - toD(b.dato))[0];
  }
  function stateText(p) {
    const b = p.bestilling, os = orderState(p);
    if (!b) return hasBom(p) ? "" : "BOM mangler";
    if (os === "tillegg") return "Må bestille mer";
    if (b.status === "delvis_bestilt") return `${b.bestilte} av ${b.leverandorer} bestilt`;
    if (os === "late") return "Frist passert – " + b.statusNavn.toLowerCase();
    return b.statusNavn;
  }

  // ---------- neste 8 uker ----------
  const ORDER = { frist: 0, prep: 1, start: 2, retur: 3 };
  function evTag(e) {
    if (!ING) return "";
    if (e.kind === "retur") return e.p.telling && e.p.telling.lagret ? ` <span class="tag ok">Telt</span>` : "";
    if (e.kind !== "prep" && e.kind !== "frist") return "";
    if (!hasBom(e.p)) return ` <span class="tag">BOM mangler</span>`;
    const os = orderState(e.p);
    if (os === "ok") return ` <span class="tag ok">${esc(e.p.bestilling.statusNavn)}</span>`;
    if (os === "tillegg") return ` <span class="tag soon">Må bestille mer</span>`;
    if (e.p.bestilling && e.p.bestilling.status === "delvis_bestilt") return ` <span class="tag soon">${esc(stateText(e.p))}</span>`;
    return "";
  }
  function drawWeeks() {
    if (mode === "loading") { $("weeks").innerHTML = `<span class="empty">Henter prosjekter …</span>`; return; }
    const ev = allEvents();
    let h = "";
    NESTE8.forEach((w, i) => {
      const list = ev.filter((e) => e.dato === w.dato).sort((a, b) => ORDER[a.kind] - ORDER[b.kind]);
      h += `<div class="week${i === 0 ? " now" : ""}"><div class="wk"><b>Uke ${w.uke}</b><small>${i === 0 ? "denne uken" : range(w.dato)}</small></div><div class="evs">`;
      h += list.length ? list.map((e) =>
        `<div class="ev ${e.kind}" data-id="${esc(e.p.id)}" data-kind="${e.kind}" data-d="${e.dato}" data-w="${e.uke}"><span class="dot" style="background:${col(e.p)}"></span><span><span class="what">${esc(e.what)}</span> <span class="who">· ${esc(e.p.navn)}</span>${evTag(e)}</span></div>`).join("")
        : `<span class="empty">${w.stengt ? "Stengt periode" : "Ingenting planlagt"}</span>`;
      h += `</div></div>`;
    });
    $("weeks").innerHTML = h;
  }

  // ---------- toppfelt ----------
  function drawStats() {
    if (mode === "loading") { $("stats").innerHTML = ""; return; }
    const n = nextFrist();
    const list = inSY(), missing = list.filter((p) => !hasBom(p)).length;
    const u = n ? weeksBetween(IDAG.dato, n.dato) : 0;
    // på tvers av skoleår: en passert frist er like viktig uansett hvilket år du ser på
    const late = ING ? projects.filter((p) => orderState(p) === "late") : [];
    const more = ING ? projects.filter((p) => orderState(p) === "tillegg") : [];
    const names = (list) => esc(list.map((p) => p.navn).join(", "));
    $("stats").innerHTML =
      (late.length ? `<div class="stat warn"><small>Frist passert, ikke bestilt</small><b>${late.length} ${late.length === 1 ? "prosjekt" : "prosjekter"}</b><div class="muted" style="font-size:12px">${names(late)}</div></div>` : "") +
      (more.length ? `<div class="stat soon"><small>Må bestille mer</small><b>${more.length} ${more.length === 1 ? "prosjekt" : "prosjekter"}</b><div class="muted" style="font-size:12px">${names(more)}</div></div>` : "") +
      (n ?`<div class="stat${u <= 2 ? " warn" : ""}"><small>Neste bestillingsfrist</small><b>${esc(n.p.navn)} · uke ${n.uke}</b><div class="muted" style="font-size:12px">${u === 0 ? "denne uken" : "om " + u + (u === 1 ? " uke" : " uker")}</div></div>` : "") +
      (ING && list.length ? `<div class="stat"><small>Med BOM ${esc(curSY().navn)}</small><b>${list.length - missing} av ${list.length}</b><div class="muted" style="font-size:12px">${missing} mangler BOM</div></div>` : "");
  }


  // ---------- tabell ----------
  // faglæreren ser konsekvensen av å flytte et prosjekt: en frist som er nær eller passert
  function fristVarsel(p) {
    if (ING || !p.egen || !IDAG || p.frister.start.dato <= IDAG.dato) return "";
    const u = weeksBetween(IDAG.dato, p.frister.frist.dato);
    if (u < 0) return `<span class="fristvarsel">Fristen er passert – ingeniøren varsles</span>`;
    if (u <= 2) return `<span class="fristvarsel">${u === 0 ? "Fristen er denne uken" : `Fristen er om ${u} ${u === 1 ? "uke" : "uker"}`} – ingeniøren varsles</span>`;
    return "";
  }
  const fristHtml = (p) => `uke ${p.frister.frist.uke}<small>${dfmtY.format(toD(p.frister.frist.dato))}</small>${fristVarsel(p)}`;
  const bomSum = (b) => `${b.linjer} linjer · ${b.bestill} å bestille · ${nf0.format(b.eks)} kr`;
  function bomCell(p) {
    const b = p.bomStatus, id = esc(p.id);
    const apne = LOKAL ? `<button type="button" class="mini" data-act="apne" data-id="${id}">Åpne i Excel</button>` : "";
    if (b && b.finnes && !b.feil && !b.linjer) {
      return `<div class="bomcell"><span class="pill miss" title="${esc(b.fil)}">Tom BOM – fyll ut i Excel</span>${apne}</div>`;
    }
    if (b && b.finnes && !b.feil) {
      return `<div class="bomcell"><span class="pill ok" title="${esc(b.fil)} · sist lagret ${esc(b.endret)}">${bomSum(b)}</span>`
        + apne
        + `<button type="button" class="mini" data-act="liste" data-id="${id}">Bestillingsliste</button></div>`;
    }
    const velg = ledigeBom.length
      ? `<select class="mini" data-act="koble" data-id="${id}" aria-label="Koble til eksisterende BOM"><option value="">Koble til fil …</option>${ledigeBom.map((f) => `<option>${esc(f)}</option>`).join("")}</select>` : "";
    if (b && !b.finnes) return `<div class="bomcell"><span class="pill miss" title="${esc(b.fil)}">Finner ikke filen</span>${velg}<button type="button" class="mini quiet" data-act="frakoble" data-id="${id}">Fjern kobling</button></div>`;
    if (b && b.feil) return `<div class="bomcell"><span class="pill miss" title="${esc(b.feil)}">Kan ikke leses</span>${apne}<button type="button" class="mini quiet" data-act="frakoble" data-id="${id}">Fjern kobling</button></div>`;
    return `<div class="bomcell"><button type="button" class="mini" data-act="mal" data-id="${id}">Lag BOM-mal</button>${velg}</div>`;
  }
  // ---------- bestilling per leverandør ----------
  const openOrders = new Set();
  const COLS = 11;
  function orderCell(p) {
    const b = p.bestilling, t = p.telling, L = t && t.lagret;
    if (!b && !L) return `<span class="muted">${hasBom(p) ? "" : "—"}</span>`;
    const cls = b ? ({ ok: "ok", late: "late", soon: "soon", tillegg: "soon" }[orderState(p)] || "") : "";
    const open = openOrders.has(p.id);
    const venter = L && L.ventende > 0;
    const sub = L ? (!venter ? "Telt · lager oppdatert" : L.iBom ? "Telt · endringer ikke skrevet" : "Telt · ikke skrevet til BOM")
      : t && returnReady(p) ? "Klar for telling" : "";
    return `<button type="button" class="ostat ${cls}" data-act="vis" data-id="${esc(p.id)}" aria-expanded="${open}">${esc(b ? stateText(p) : "Telling")}<span class="chev" aria-hidden="true">${open ? "▴" : "▾"}</span></button>`
      + (sub ? `<small class="tellnote${venter ? " soon" : ""}">${sub}</small>` : "");
  }
  const returnReady = (p) => !!IDAG && p.frister.retur.dato <= IDAG.dato;

  // ---------- telling etter prosjektet ----------
  const tellDraft = {}, tellOpen = new Map();
  const fv = (l, type) => (type === "forbruk" ? Math.max(0, l.lagerFor + l.bestilt - l.utdelt) : l.lagerFor + l.bestilt);
  const signed = (x) => (Math.abs(x) < 1e-9 ? "0" : (x > 0 ? "+" : "−") + qty(Math.abs(x)));
  function tellSection(p) {
    const t = p.telling; if (!t) return "";
    const id = esc(p.id), L = t.lagret, draft = tellDraft[p.id] || {};
    const lines = L ? L.linjer : t.grunnlag, iBom = L ? L.skrevet_verdier : {};
    const bomState = !L ? "" : Object.keys(L.endretIExcel).length ? " · «På lager» er endret i Excel etterpå"
      : !L.iBom ? " · ikke skrevet til BOM-en ennå"
      : L.ventende ? ` · ${L.ventende} ${L.ventende === 1 ? "endring" : "endringer"} ikke skrevet til BOM-en`
      : ` · «På lager» oppdatert ${dfmtY.format(toD(L.skrevet))}`;
    const summary = L
      ? `Telt ${dfmtY.format(toD(L.dato))} · ${L.avvik} avvik${L.svinn ? ` · svinn ${nf0.format(L.svinnKr)} kr` : ""}${bomState}`
      : returnReady(p) ? "Klar for telling" : `Telles etter innlevering, uke ${p.frister.retur.uke}`;
    const open = tellOpen.has(p.id) ? tellOpen.get(p.id) : !L && returnReady(p);
    const rows = lines.map((l) => {
      const d = draft[l.key] || {}, type = d.type || l.type, f = fv(l, type);
      const telt = d.telt !== undefined ? d.telt : (L ? l.telt : f);
      const k = `data-id="${id}" data-key="${esc(l.key)}"`;
      const typeCell = `<select class="mini" data-role="ttype" ${k} aria-label="Utstyr eller forbruk: ${esc(l.beskrivelse)}">`
          + `<option value="utstyr"${type === "utstyr" ? " selected" : ""}>Utstyr</option><option value="forbruk"${type === "forbruk" ? " selected" : ""}>Forbruk</option></select>`;
      // hva står i BOM-en fra denne tellingen, hvis det er noe annet enn det som er telt nå
      const skrevet = iBom[l.key], ulik = L && L.iBom && (skrevet === undefined || Math.abs(skrevet - l.telt) > 1e-9);
      const excel = L && L.endretIExcel[l.key];
      const bomNote = excel !== undefined ? `<small class="excel">endret i Excel: ${qty(excel)}</small>`
        : !ulik ? "" : skrevet === undefined ? `<small>ikke i BOM-en</small>` : `<small>i BOM-en: ${qty(skrevet)}</small>`;
      const teltCell = `<input type="number" min="0" step="any" data-role="telt" ${k} value="${esc(telt)}" aria-label="Telt: ${esc(l.beskrivelse)}">${bomNote}`;
      const diff = (parseFloat(telt) || 0) - f;
      return `<tr data-key="${esc(l.key)}" data-lager="${l.lagerFor}" data-bestilt="${l.bestilt}" data-utdelt="${l.utdelt}">`
        + `<td>${esc(l.beskrivelse)}${l.varenummer ? `<small>${esc(l.varenummer)}</small>` : ""}</td><td>${typeCell}</td>`
        + `<td class="num"${l.antatt ? ` title="Ingen registrert bestilling – antatt bestilt etter BOM-en"` : ""}>${qty(l.lagerFor + l.bestilt)}${l.antatt ? "*" : ""}</td><td class="num">${qty(l.utdelt)}</td>`
        + `<td class="num tf">${qty(f)}</td><td class="num">${teltCell}</td>`
        + `<td class="num td ${diff < -1e-9 && type === "utstyr" ? "loss" : ""}">${signed(diff)}</td></tr>`;
    }).join("");
    let actions = `<button type="button" class="mini" data-act="tlagre" data-id="${id}">${L ? "Lagre endringer" : "Lagre telling"}</button>`;
    if (L && L.ventende) actions += `<button type="button" class="mini" data-act="tskriv" data-id="${id}">${L.iBom ? "Skriv endringene til BOM-en …" : "Oppdater «På lager» i BOM-en …"}</button>`;
    if (L && L.iBom) actions += `<button type="button" class="mini quiet" data-act="tangre" data-id="${id}">Angre – sett «På lager» tilbake …</button>`;
    else if (L) actions += `<button type="button" class="mini quiet" data-act="tslett" data-id="${id}">Slett telling</button>`;
    let note = !L && t.antattBestilt ? `<p class="muted">* Leverandøren er ikke registrert som bestilt, så «før» bruker det BOM-en sa skulle bestilles.</p>` : "";
    const nExcel = L ? Object.keys(L.endretIExcel).length : 0;
    if (nExcel) note += `<div class="excelnote"><span>«På lager» er endret i Excel for ${nExcel} ${nExcel === 1 ? "linje" : "linjer"} etter at tellingen ble skrevet. `
      + `Var det en retting av tellingen, kan tellingen bruke tallene fra BOM-en. Ellers kan du la den stå.</span>`
      + `<button type="button" class="mini" data-act="thent" data-id="${id}">Bruk tallene fra BOM-en i tellingen</button></div>`;
    const fmtTid = (s) => { const d = new Date(s); return isNaN(d) ? esc(s) : d.toLocaleString("nb-NO", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); };
    const logg = L && L.logg.length ? `<ul class="tlogg">${L.logg.map((x) =>
      `<li>${fmtTid(x.tid)}: ${{ angret: "satt tilbake", hentet: "hentet fra BOM-en" }[x.handling] || "skrev"} ${x.linjer} ${x.linjer === 1 ? "linje" : "linjer"}`
      + (x.konflikter ? `, ${x.konflikter} hoppet over (endret i Excel)` : "")
      + (x.backup ? ` · sikkerhetskopi: BOM/Sikkerhetskopi/${esc(x.backup)}` : "") + `</li>`).join("")}</ul>` : "";
    return `<details class="tellbox" data-id="${id}"${open ? " open" : ""}><summary><b>Telling etter prosjektet</b> <span class="muted">· ${summary}</span></summary>`
      + `<p class="muted">Tell det du har igjen etter prosjektet. Utstyr skal komme tilbake; forbruk brukes opp. Det telte blir nytt «På lager» i BOM-en.</p>${note}`
      + `<div class="tellscroll"><table class="tell"><thead><tr><th>Linje</th><th>Type</th><th class="num" title="På lager + bestilt som ikke er lagt på lager ennå">Før</th><th class="num">Delt ut</th><th class="num">Forventet</th><th class="num">Telt</th><th class="num">Avvik</th></tr></thead><tbody>${rows}</tbody></table></div>`
      + `<div class="levact">${actions}</div>${logg}</details>`;
  }
  // oppdater «forventet» og «avvik» på raden mens du skriver, og husk tallene til de er lagret
  function tellRow(tr, id) {
    const sel = tr.querySelector("[data-role=ttype]"), inp = tr.querySelector("[data-role=telt]");
    const type = sel ? sel.value : "utstyr";
    const l = { lagerFor: +tr.dataset.lager, bestilt: +tr.dataset.bestilt, utdelt: +tr.dataset.utdelt };
    const f = fv(l, type), diff = (parseFloat(inp.value) || 0) - f;
    tr.querySelector(".tf").textContent = qty(f);
    const td = tr.querySelector(".td"); td.textContent = signed(diff); td.classList.toggle("loss", diff < -1e-9 && type === "utstyr");
    (tellDraft[id] || (tellDraft[id] = {}))[tr.dataset.key] = { type, telt: inp.value };
  }
  function tellEdit(el) { tellRow(el.closest("tr"), el.dataset.id); }
  function writeMsg(r, verb) {
    const n = (x) => `${x} ${x === 1 ? "linje" : "linjer"}`;
    let s = r.skrevet.length ? `${verb} ${n(r.skrevet.length)} i BOM-en. Sikkerhetskopi: ${r.backup}.` : "Ingenting ble skrevet til BOM-en.";
    if (r.konflikt.length) s += ` ${n(r.konflikt.length)} ble ikke rørt fordi «På lager» er endret i Excel siden: `
      + r.konflikt.map((k) => `${k.beskrivelse} (står ${qty(k.faktisk)}, ventet ${qty(k.forventet)})`).join(", ") + ".";
    if (r.hoppet.length) s += ` ${n(r.hoppet.length)} hoppet over (finnes ikke, eller står flere ganger i BOM-en).`;
    return s;
  }
  function tellCollect(id) {
    const box = document.querySelector(`details.tellbox[data-id="${CSS.escape(id)}"]`);
    return [...box.querySelectorAll("tbody tr[data-key]")].map((tr) => ({
      key: tr.dataset.key, type: tr.querySelector("[data-role=ttype]").value, telt: tr.querySelector("[data-role=telt]").value }));
  }
  const qty = (x) => fmtNum(Math.round(x * 100) / 100);
  // ---------- varemottak: legg det som har kommet til «På lager» i BOM-en ----------
  const mottakOpen = new Set();
  function mottakHtml(p, g, k) {
    if (!g.mottak.length || g.status === "ikke" || g.status === "lager") return "";
    const lagt = g.mottak.filter((m) => m.lagt > 0), rest = g.mottak.filter((m) => m.gjenstar > 0);
    const fid = `${p.id}|${g.key}`, open = mottakOpen.has(fid) && rest.length;
    let s = `<div class="mottak">`;
    const sum = !lagt.length ? "Ingenting lagt på lager ennå."
      : !rest.length ? `Alt er lagt på lager (${g.mottak.length} ${g.mottak.length === 1 ? "linje" : "linjer"}).`
      : `${lagt.length} av ${g.mottak.length} linjer er lagt på lager.`;
    s += `<span class="muted">${sum}</span>`;
    if (rest.length && !open) s += `<button type="button" class="mini" data-act="mottakvis" ${k}>Registrer mottak …</button>`;
    if (lagt.length) s += `<button type="button" class="mini quiet" data-act="angremottak" ${k}>Angre mottak …</button>`;
    if (open) {
      s += `<div class="mottakform"><p class="muted">Hvor mange kom? Antallet legges til «På lager» i BOM-en.</p>`
        + rest.map((m) => `<label><span>${esc(m.beskrivelse)}${m.varenummer ? ` <small>${esc(m.varenummer)}</small>` : ""}</span>`
          + `<input type="number" min="0" step="any" data-role="mottak" data-key="${esc(m.key)}" value="${m.gjenstar}">`
          + `<small>av ${qty(m.gjenstar)}${m.lagt ? ` (${qty(m.lagt)} lagt på lager før)` : ""}</small></label>`).join("")
        + `<div class="levact"><button type="button" class="mini" data-act="mottak" ${k}>Legg på lager i BOM-en …</button>`
        + `<button type="button" class="mini quiet" data-act="mottakskjul" ${k}>Avbryt</button></div></div>`;
    }
    if (g.lagerLogg.length) s += `<ul class="tlogg">${g.lagerLogg.map((x) => {
      const n = Object.keys(x.linjer).length;
      return `<li>${new Date(x.tid).toLocaleString("nb-NO", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}: `
        + `${x.handling === "angret" ? "trakk fra" : "la på lager"} ${n} ${n === 1 ? "linje" : "linjer"}${x.backup ? ` · sikkerhetskopi: BOM/Sikkerhetskopi/${esc(x.backup)}` : ""}</li>`;
    }).join("")}</ul>`;
    return s + `</div>`;
  }
  function mottakMsg(r, verb) {
    const n = (x) => `${x} ${x === 1 ? "linje" : "linjer"}`;
    let s = r.skrevet.length ? `${verb} for ${n(r.skrevet.length)} i BOM-en. Sikkerhetskopi: ${r.backup}.` : "Ingenting ble endret i BOM-en.";
    if (r.konflikt.length) s += ` ${n(r.konflikt.length)} ble ikke endret fordi «På lager» ville blitt negativt: `
      + r.konflikt.map((k) => `${k.beskrivelse} (står ${qty(k.faktisk)})`).join(", ") + ".";
    if (r.hoppet.length) s += ` ${n(r.hoppet.length)} hoppet over (finnes ikke, eller står flere ganger i BOM-en).`;
    return s;
  }

  function orderDetail(p) {
    const id = esc(p.id);
    const rows = (p.bestilling ? p.bestilling.grupper : []).map((g) => {
      const k = `data-id="${id}" data-lev="${esc(g.key)}"`;
      let act;
      if (g.status === "lager") act = `<span class="muted">Ingenting å bestille</span>`;
      else if (g.status === "ikke") act =
        `<input type="text" class="ordrenr" data-role="ordrenr" placeholder="Ordrenr (valgfritt)" aria-label="Ordrenummer for ${esc(g.navn)}">`
        + `<button type="button" class="mini" data-act="bestilt" ${k}>Registrer bestilt</button>`;
      else act =
        `<select class="mini" data-act="ostatus" ${k} aria-label="Status for ${esc(g.navn)}">${["bestilt", "delvis", "mottatt"].map((s) =>
          `<option value="${s}"${s === g.status ? " selected" : ""}>${{ bestilt: "Bestilt", delvis: "Delvis mottatt", mottatt: "Mottatt" }[s]}</option>`).join("")}</select>`
        + `<input type="text" class="ordrenr" data-act="ordrenr" ${k} value="${esc(g.ordrenr)}" placeholder="Ordrenr" aria-label="Ordrenummer for ${esc(g.navn)}">`
        + `<span class="muted when">bestilt ${dfmtY.format(toD(g.bestiltDato))}${g.mottattDato && g.status !== "bestilt" ? " · mottatt " + dfmtY.format(toD(g.mottattDato)) : ""}</span>`
        + `<button type="button" class="mini quiet" data-act="angre" ${k}>Angre</button>`;
      const liste = g.linjer && !g.fjernet ? `<button type="button" class="mini" data-act="lliste" ${k}>Liste</button>` : "";
      const mot = mottakHtml(p, g, k);
      let notes = "";
      if (g.fjernet) notes += `<li class="muted">Leverandøren står ikke lenger i BOM-en.</li>`;
      if (g.tillegg.length) notes += g.tillegg.map((t) =>
        `<li class="more"><b>${esc(t.beskrivelse)}</b>${t.varenummer ? " · " + esc(t.varenummer) : ""}: bestilt ${qty(t.bestilt)}, trenger ${qty(t.trenger)} → <b>${qty(t.mangler)} til</b></li>`).join("")
        + `<li><button type="button" class="mini" data-act="tillegg" ${k}>Registrer tilleggsbestilling</button></li>`;
      if (g.overskudd.length) notes += g.overskudd.map((t) =>
        `<li class="muted">${esc(t.beskrivelse)}: bestilt ${qty(t.bestilt)}, trenger nå ${qty(t.trenger)}</li>`).join("");
      return `<div class="lev"><div class="levhead"><b>${esc(g.navn)}</b><span class="muted">${g.linjer} ${g.linjer === 1 ? "linje" : "linjer"} · ${nf0.format(g.eks)} kr</span></div>`
        + `<div class="levact" data-lev="${esc(g.key)}">${act}${liste}</div>${mot}${notes ? `<ul class="avvik">${notes}</ul>` : ""}</div>`;
    }).join("");
    return `<tr class="detail" data-for="${id}"><td colspan="${COLS}">${rows ? `<div class="levs">${rows}</div>` : ""}${tellSection(p)}</td></tr>`;
  }

  // «Emne: IELS1001 · Faglærer · Ingeniør» under prosjektnavnet; ingeniøren velger emne på egne prosjekter
  function emneLinje(p) {
    const folk = [p.faglarerNavn && `faglærer ${esc(p.faglarerNavn)}${!ING && p.egen ? " (deg)" : ""}`,
                  p.ingeniorNavn && `ingeniør ${esc(p.ingeniorNavn)}${ING && MEG.id && p.ingeniorId === MEG.id ? " (deg)" : ""}`]
      .filter(Boolean).join(" · ");
    if (ING && p.egen) {
      const valg = EMNER.filter((e) => e.aktiv || e.id === p.emneId);
      return `<label class="eier${p.emneId ? "" : " mangler"}">Emne: <select data-act="emne" data-id="${esc(p.id)}" aria-label="Emne for ${esc(p.navn)}">`
        + `<option value="">${p.emneId ? "uten emne" : "Velg emne …"}</option>`
        + valg.map((e) => `<option value="${e.id}"${e.id === p.emneId ? " selected" : ""}>${esc(e.kode)}${e.navn ? " – " + esc(e.navn) : ""}</option>`).join("")
        + `</select>${folk ? ` · ${folk}` : ""}</label>`;
    }
    if (!p.emneKode) return ING ? `<span class="eier mangler">Uten emne</span>` : "";
    return `<span class="eier">${esc(p.emneKode)}${folk ? ` · ${folk}` : ""}</span>`;
  }

  function drawTable() {
    if (mode === "loading") { $("tbody").innerHTML = `<tr><td colspan="${COLS}" class="blank">Henter prosjekter …</td></tr>`; return; }
    const list = sorted();
    if (!list.length) { $("tbody").innerHTML = `<tr><td colspan="${COLS}" class="blank">Ingen prosjekter dette skoleåret. Trykk «Legg til nytt prosjekt» for å legge inn det første.</td></tr>`; return; }
    $("tbody").innerHTML = list.map((p) => {
      // egne prosjekter kan endres; faglærer bare uker og sett, leveringstid og buffer er ingeniørens
      const kan = (k) => p.egen && (ING || k === "start" || k === "slutt" || k === "sett");
      const num = (k, min, max, lbl) => kan(k)
        ? `<input type="number" id="in-${esc(p.id)}-${k}" data-id="${esc(p.id)}" data-k="${k}" min="${min}" max="${max}" value="${p[k] ?? ""}" aria-label="${lbl}">`
        : `<span class="ro">${p[k] ?? "–"}</span>`;
      const warn = p.bomStatus && p.bomStatus.settFil && p.sett && p.bomStatus.settFil !== p.sett
        ? `<div class="muted" style="font-size:11.5px" title="Årshjulets tall brukes">BOM-filen: ${p.bomStatus.settFil}</div>` : "";
      const eier = emneLinje(p);
      const slett = !p.egen ? "" : ING ? `<button type="button" class="del" data-id="${esc(p.id)}">Slett</button>`
        : `<button type="button" class="del" data-id="${esc(p.id)}" title="Ingeniøren får beskjed og kan gjenopprette">Fjern</button>`;
      // en annen ingeniørs prosjekt (hele instituttet): bare til å se på
      const andres = ING && !p.egen ? `<span class="muted">${esc(p.ingeniorNavn || "")}</span>` : "";
      return `<tr data-id="${esc(p.id)}">
        <td class="name"><b><span class="dot" style="background:${col(p)}"></span>${esc(p.navn)}</b>${p.notat ? `<small>${esc(p.notat)}</small>` : ""}${eier}</td>
        <td>${p.type === "sett" ? "Semestersett" : "Hackathon / lab"}<div class="muted" style="font-size:12px">${esc(p.semester)} ${p.frister.start.ar}</div></td>
        <td>${num("start", 1, 53, "I bruk fra uke")}</td><td>${num("slutt", 1, 53, "Til uke")}</td><td>${num("lev", 0, 20, "Leveringstid i uker")}</td><td>${num("buf", 0, 20, "Buffer i uker")}</td>
        <td>${num("sett", 0, 999, "Antall sett")}${warn}</td>
        <td class="fristcell">${fristHtml(p)}</td>
        <td class="ordercell ing">${ING && p.egen ? orderCell(p) : andres}</td>
        <td class="ing">${ING && p.egen ? bomCell(p) : ""}</td>
        <td>${slett}</td></tr>`
        + (ING && p.egen && openOrders.has(p.id) && (p.bestilling || p.telling) ? orderDetail(p) : "");
    }).join("");
  }

  // ---------- endringer i BOM-filer ----------
  const fmtNum = (x) => (typeof x === "number" ? nf2.format(x).replace(/,00$/, "") : (String(x ?? "") || "tom"));
  function drawDiffs() {
    // samme BOM kan høre til flere skoleår – vis den én gang
    const seen = new Set();
    const list = projects.filter((p) => p.bomStatus && p.bomStatus.endringer && p.bomStatus.endringer.length && !seen.has(p.bom) && seen.add(p.bom));
    const more = inSY().filter((p) => orderState(p) === "tillegg").map((p) => {
      const levs = p.bestilling.grupper.filter((g) => g.tillegg.length).map((g) => esc(g.navn)).join(", ");
      return `<div class="diffbox soon"><div class="row"><h3>${esc(p.navn)}: trenger mer enn det som er bestilt hos ${levs}</h3>`
        + `<button type="button" class="mini" data-act="vis" data-id="${esc(p.id)}">Vis bestillingen</button></div>`
        + `<p class="muted">BOM-en eller antall sett er endret etter at du bestilte.</p></div>`;
    }).join("");
    $("diffs").innerHTML = more + list.map((p) => {
      const b = p.bomStatus;
      const items = b.endringer.map((e) => e.type === "ny"
        ? `<li class="add"><span class="sym">+</span><span><b>${esc(e.beskrivelse)}</b>${e.varenummer ? " · " + esc(e.varenummer) : ""} <span class="from">(ny linje, ${fmtNum(e.prSett)} per sett)</span></span></li>`
        : e.type === "fjernet"
        ? `<li class="del"><span class="sym">−</span><span><b>${esc(e.beskrivelse)}</b>${e.varenummer ? " · " + esc(e.varenummer) : ""} <span class="from">(fjernet)</span></span></li>`
        : `<li class="chg"><span class="sym">~</span><span><b>${esc(e.beskrivelse)}</b>: ${esc(e.felt)} <span class="from">${esc(fmtNum(e.fra))}</span> → ${esc(fmtNum(e.til))}</span></li>`).join("");
      return `<div class="diffbox"><div class="row"><h3>${esc(p.navn)}: BOM-en er endret siden ${esc(b.sistLest)}</h3>`
        + `<button type="button" class="mini" data-act="lest" data-id="${esc(p.id)}">OK, sett</button></div><ul>${items}</ul></div>`;
    }).join("");
  }

  // ---------- neste skoleår: varsel og kopiering ----------
  const syName = (ar) => { const s = SYS.find((x) => x.ar === ar); return s ? s.navn : `${ar}/${String((ar + 1) % 100).padStart(2, "0")}`; };
  // ---------- faglærernes endringer (bare for ingeniøren) ----------
  const tidFmt = (s) => { const d = new Date(s); return isNaN(d) ? esc(s) : d.toLocaleString("nb-NO", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); };
  const TYPENAVN = { hack: "Hackathon / lab", sett: "Semestersett" };
  const verdi = (felt, v) => (v === null || v === undefined || v === "" ? "tom" : felt === "type" ? (TYPENAVN[v] || v) : String(v));
  const HANDLING = { opprettet: "La til prosjektet", gjenopprettet: "Gjenopprettet", fjernet: "Fjernet prosjektet" };
  function changeNotices() {
    if (!ING) return "";
    let h = "";
    SLETTEDE.forEach((s) => {
      h += `<div class="notice warn"><div><b>${esc(s.slettetAv || "En faglærer")} fjernet «${esc(s.navn)}» (${esc(syName(s.skolear))})</b> ${tidFmt(s.slettet)}. `
        + `Det vises ikke i hjulet før du gjenoppretter det.</div><span class="noticeact">`
        + `<button type="button" class="mini" data-act="gjenopprett" data-id="${esc(s.id)}">Gjenopprett</button>`
        + `<button type="button" class="mini quiet" data-act="slettbekreft" data-id="${esc(s.id)}">Bekreft sletting …</button></span></div>`;
    });
    const fjernet = new Set(SLETTEDE.map((s) => String(s.id)));
    const per = new Map();
    ENDRINGER.forEach((e) => {
      const gid = String(e.gjennomforing_id);
      if (fjernet.has(gid)) return; // vises som «fjernet» over
      if (!per.has(gid)) per.set(gid, []);
      per.get(gid).push(e);
    });
    per.forEach((list, gid) => {
      const p = projects.find((x) => x.id === gid);
      const hvem = [...new Set(list.map((e) => e.bruker_navn))].join(", ");
      const items = list.map((e) => `<li>${e.handling === "endret"
        ? `${esc(e.felt)}: <span class="from">${esc(verdi(e.felt, e.fra))}</span> → <b>${esc(verdi(e.felt, e.til))}</b>`
        : esc(HANDLING[e.handling] || e.handling)} <span class="muted">· ${tidFmt(e.tid)}</span></li>`).join("");
      let konsekvens = "", sterk = false;
      if (p && IDAG) {
        const os = orderState(p), u = weeksBetween(IDAG.dato, p.frister.frist.dato);
        konsekvens = `Bestillingsfrist nå: uke ${p.frister.frist.uke}, ${dfmtY.format(toD(p.frister.frist.dato))}`;
        if (os === "late") { konsekvens += " – passert, og ikke bestilt"; sterk = true; }
        else if (os === "soon") { konsekvens += u === 0 ? " – denne uken" : ` – om ${u} ${u === 1 ? "uke" : "uker"}`; sterk = true; }
        else if (os === "tillegg") { konsekvens += " – du må bestille mer"; sterk = true; }
        else if (os === "ok") konsekvens += " – alt er bestilt";
      } else if (!p) konsekvens = "Prosjektet er slettet.";
      h += `<div class="notice ${sterk ? "warn" : "info"}"><div><b>${esc(hvem)} endret «${esc(p ? p.navn : list[0].prosjekt_navn)}» (${esc(syName(list[0].skolear))})</b>`
        + `<ul class="endr">${items}</ul>${konsekvens ? `<p>${konsekvens}</p>` : ""}</div>`
        + `<button type="button" class="mini" data-act="kvitter" data-id="${esc(gid)}">OK, sett</button></div>`;
    });
    return h;
  }

  function drawNotices() {
    const o = OPPSETT, endr = mode === "loading" ? "" : changeNotices();
    if (!o || mode === "loading") { $("notices").innerHTML = endr; return; }
    const naar = o.uker < 0 ? "fristen er allerede passert" : o.uker === 0 ? "denne uken" : `om ${o.uker} ${o.uker === 1 ? "uke" : "uker"}`;
    $("notices").innerHTML = endr + `<div class="notice ${o.uker <= 4 ? "warn" : "soon"}"><div>`
      + `<b>Skoleåret ${esc(o.navn)} er ikke satt opp.</b> ${o.mangler} ${o.mangler === 1 ? "prosjekt" : "prosjekter"} fra ${esc(o.fraNavn)} mangler. `
      + `Første bestillingsfrist blir uke ${o.frist.uke} for ${esc(o.prosjekt)} – ${naar}.</div>`
      + `<button type="button" class="mini" id="oppsettGo">Sett opp ${esc(o.navn)} …</button></div>`;
  }
  $("notices").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act]");
    if (b) {
      const id = b.dataset.id, a = b.dataset.act;
      if (a === "kvitter") act(() => api("POST", `/api/endringer/${encodeURIComponent(id)}/kvitter`), null, b);
      if (a === "gjenopprett") act(() => api("POST", P(id) + "/gjenopprett"), "Prosjektet er gjenopprettet.", b);
      if (a === "slettbekreft") {
        if (!b.classList.contains("confirm")) { b.classList.add("confirm"); b.textContent = "Bekreft: slett for godt"; return; }
        act(() => api("DELETE", P(id)), "Prosjektet er slettet.", b);
      }
      return;
    }
    if (!e.target.closest("#oppsettGo") || !OPPSETT) return;
    goSY(OPPSETT.fra); openCopy(true);
    $("copybox").scrollIntoView({ block: "center", behavior: "smooth" });
  });

  function drawCopyButton() {
    const has = inSY().some((p) => p.egen);
    $("copyOpen").hidden = !has || mode === "loading";
    $("copyOpen").textContent = `Kopier til ${syName(SY + 1)} …`;
    if (!has) openCopy(false);
  }
  function openCopy(open) {
    $("copybox").hidden = !open; $("copyOpen").setAttribute("aria-expanded", String(open)); $("copyErr").textContent = "";
    if (!open) return;
    const neste = new Set(ALLE.filter((p) => p.skolear === SY + 1).map((p) => p.prosjektId));
    $("copyTitle").textContent = `Kopier fra ${syName(SY)} til ${syName(SY + 1)}`;
    $("copyList").innerHTML = sorted().filter((p) => p.egen).map((p) => { // bare egne emner kopieres
      const finnes = neste.has(p.prosjektId);
      return `<label class="copyrow${finnes ? " done" : ""}"><input type="checkbox" value="${esc(p.id)}"${finnes ? " disabled" : " checked"}>`
        + `<span class="dot" style="background:${col(p)}"></span><span><b>${esc(p.navn)}</b> <span class="muted">· uke ${p.start}${p.start !== p.slutt ? "–" + p.slutt : ""}${p.sett ? " · " + p.sett + " sett" : ""}${finnes ? " · finnes allerede" : ""}</span></span></label>`;
    }).join("");
  }
  $("copyOpen").addEventListener("click", () => openCopy($("copybox").hidden));
  $("copyCancel").addEventListener("click", () => openCopy(false));
  $("copyGo").addEventListener("click", async () => {
    const ids = [...$("copyList").querySelectorAll("input:checked")].map((i) => i.value);
    if (!ids.length) { $("copyErr").textContent = "Velg minst ett prosjekt."; return; }
    $("copyGo").disabled = true;
    try {
      const r = await api("POST", `/api/skolear/${SY}/kopier`, { ids });
      openCopy(false); await load(); goSY(r.til);
      setStatus(`Kopierte ${r.nye.length} ${r.nye.length === 1 ? "prosjekt" : "prosjekter"} til ${r.navn}. Sjekk ukene og antall sett.`);
    } catch (err) { $("copyErr").textContent = err.message; }
    finally { $("copyGo").disabled = false; }
  });

  // ---------- historikk: hva hvert prosjekt har kostet per skoleår ----------
  function drawHistory() {
    const runs = projects.filter((p) => p.bestiltKr != null || (p.telling && p.telling.lagret && p.telling.lagret.svinnKr));
    $("historySec").hidden = !runs.length;
    if (!runs.length) return;
    const years = [...new Set(runs.map((p) => p.skolear))].sort((a, b) => a - b);
    const rows = new Map();
    runs.forEach((p) => {
      const r = rows.get(p.prosjektId) || { navn: p.navn, p, cells: {} };
      r.cells[p.skolear] = p; rows.set(p.prosjektId, r);
    });
    const cell = (p) => {
      if (!p) return `<td class="num muted">—</td>`;
      const sett = p.sett || (p.bomStatus && p.bomStatus.settFil);
      const L = p.telling && p.telling.lagret;
      return `<td class="num">${nf0.format(p.bestiltKr)} kr${sett ? `<small>${nf0.format(p.bestiltKr / sett)} kr/sett · ${sett} sett</small>` : ""}`
        + (L && L.svinnKr ? `<small class="loss">svinn ${nf0.format(L.svinnKr)} kr</small>` : "") + `</td>`;
    };
    const sum = (y) => runs.filter((p) => p.skolear === y).reduce((s, p) => s + p.bestiltKr, 0);
    $("histTable").innerHTML = `<thead><tr><th>Prosjekt</th>${years.map((y) => `<th class="num">${esc(syName(y))}</th>`).join("")}</tr></thead><tbody>`
      + [...rows.values()].sort((a, b) => a.navn.localeCompare(b.navn, "nb")).map((r) =>
        `<tr><td class="name"><b><span class="dot" style="background:${col(r.p)}"></span>${esc(r.navn)}</b></td>${years.map((y) => cell(r.cells[y])).join("")}</tr>`).join("")
      + `</tbody><tfoot><tr><td><b>Sum</b></td>${years.map((y) => `<td class="num"><b>${nf0.format(sum(y))} kr</b></td>`).join("")}</tr></tfoot>`;
  }

  // ---------- «Mine emner / Hele instituttet» (ingeniør) ----------
  function drawScope() {
    const andre = ING && ALLE.some((p) => !p.egen);
    $("scope").hidden = !andre;
    $("scope").textContent = VIS_ALLE ? "Viser hele instituttet" : "Viser mine emner";
    $("scope").setAttribute("aria-pressed", String(VIS_ALLE));
    $("scope").title = VIS_ALLE ? "Trykk for bare dine egne emner" : "Trykk for å se alle emner (andres kan bare ses, ikke endres)";
  }
  $("scope").addEventListener("click", () => {
    VIS_ALLE = !VIS_ALLE;
    try { localStorage.setItem("arshjul.visAlle", VIS_ALLE ? "1" : "0"); } catch (e) {}
    projects = synlige(); highlight(null); renderAll(false);
  });

  // ---------- emner (ingeniører) ----------
  let nyttEmne = false;
  function drawEmner() {
    if (!ING || mode === "loading" || $("emneTable").contains(document.activeElement)) return;
    const brukt = (id) => new Set(ALLE.filter((p) => p.emneId === id).map((p) => p.prosjektId)).size;
    const velg = (navn, liste, valgt, lbl) => `<select data-ef="${navn}" aria-label="${lbl}"><option value="">–</option>`
      + liste.map((u) => `<option value="${u.id}"${u.id === valgt ? " selected" : ""}>${esc(u.navn)}</option>`).join("") + `</select>`;
    const program = (valgt) => `<select data-ef="program" aria-label="Studieprogram">`
      + Object.entries(PROGRAM).map(([k, v]) => `<option value="${k}"${k === valgt ? " selected" : ""}>${esc(v)}</option>`).join("") + `</select>`;
    const rad = (e) => `<tr data-emne="${e ? e.id : "ny"}"${e && !e.aktiv ? ' class="inaktiv"' : ""}>`
      + `<td><input type="text" data-ef="kode" value="${esc(e ? e.kode : "")}" placeholder="IELS1001" aria-label="Emnekode" maxlength="20"></td>`
      + `<td><input type="text" data-ef="navn" value="${esc(e ? e.navn : "")}" placeholder="Emnenavn" aria-label="Emnenavn"></td>`
      + `<td>${program(e ? e.program : "bachelor")}</td>`
      + `<td>${velg("faglarer", FAGLARERE, e && e.faglarerId, "Faglærer")}</td>`
      + `<td>${velg("ingenior", INGENIORER, e ? e.ingeniorId : MEG.id, "Ansvarlig ingeniør")}</td>`
      + (e ? `<td class="num">${brukt(e.id)}</td><td><label class="aktiv"><input type="checkbox" data-ef="aktiv"${e.aktiv ? " checked" : ""}> aktiv</label></td>`
           : `<td></td><td><button type="button" class="mini" data-act="emnelagre">Lagre</button> <button type="button" class="mini quiet" data-act="emneavbryt">Avbryt</button></td>`)
      + `</tr>`;
    $("emneTable").innerHTML = `<thead><tr><th>Kode</th><th>Navn</th><th>Program</th><th>Faglærer</th><th>Ansvarlig ingeniør</th><th class="num">Prosjekter</th><th></th></tr></thead><tbody>`
      + (nyttEmne ? rad(null) : "") + EMNER.map(rad).join("")
      + (!EMNER.length && !nyttEmne ? `<tr><td colspan="7" class="blank">Ingen emner ennå. Trykk «+ Nytt emne».</td></tr>` : "") + `</tbody>`;
    $("emneNy").hidden = nyttEmne;
  }
  const emneVerdier = (tr) => {
    const v = {};
    tr.querySelectorAll("[data-ef]").forEach((el) => { v[el.dataset.ef] = el.type === "checkbox" ? el.checked : (el.value || null); });
    return v;
  };
  $("emneNy").addEventListener("click", () => { nyttEmne = true; drawEmner(); const k = $("emneTable").querySelector('tr[data-emne="ny"] [data-ef=kode]'); if (k) k.focus(); });
  $("emneTable").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act]"); if (!b) return;
    if (b.dataset.act === "emneavbryt") { nyttEmne = false; b.blur(); drawEmner(); return; }
    const v = emneVerdier(b.closest("tr"));
    act(async () => { const r = await api("POST", "/api/emner", v); nyttEmne = false; b.blur(); return r; },
      (r) => `Emnet ${r.kode} er lagt til. Velg det under navnet på prosjektene som hører til.`, $("emneTable"));
  });
  $("emneTable").addEventListener("change", (e) => {
    const tr = e.target.closest("tr[data-emne]"); if (!tr || tr.dataset.emne === "ny") return;
    const felt = e.target.dataset.ef, v = emneVerdier(tr);
    e.target.blur(); // så tabellen kan tegnes på nytt etter lagring
    act(() => api("PATCH", `/api/emner/${tr.dataset.emne}`, { [felt]: v[felt] }), "Emnet er lagret.", $("emneTable"));
  });

  // ---------- brukere (administrator) ----------
  let nyBruker = false;
  const pwOpen = new Set();
  function drawBrukere() {
    if (!MEG.admin || mode === "loading" || $("brukerTable").contains(document.activeElement)) return;
    const ROLLE = { ingenior: "Ingeniør", faglarer: "Faglærer" };
    const rolle = (v) => `<select data-bf="rolle" aria-label="Rolle">${Object.entries(ROLLE).map(([k, n]) =>
      `<option value="${k}"${k === v ? " selected" : ""}>${n}</option>`).join("")}</select>`;
    const rad = (u) => {
      if (!u) return `<tr data-bruker="ny"><td><input type="text" data-bf="brukernavn" placeholder="brukernavn" aria-label="Brukernavn" autocomplete="off"></td>`
        + `<td><input type="text" data-bf="navn" placeholder="Fullt navn" aria-label="Navn" autocomplete="off"></td><td>${rolle("faglarer")}</td>`
        + `<td><label class="aktiv"><input type="checkbox" data-bf="admin"> admin</label></td>`
        + `<td colspan="2"><input type="password" data-bf="passord" placeholder="Startpassord (min. 10 tegn)" aria-label="Startpassord" autocomplete="new-password"></td>`
        + `<td><button type="button" class="mini" data-act="brukerlagre">Lagre</button> <button type="button" class="mini quiet" data-act="brukeravbryt">Avbryt</button></td></tr>`;
      const deg = u.id === MEG.id;
      const pw = pwOpen.has(u.id)
        ? `<span class="pwrow"><input type="password" data-pw placeholder="Nytt passord" aria-label="Nytt passord for ${esc(u.navn)}" autocomplete="new-password">`
          + `<button type="button" class="mini" data-act="pwlagre">Lagre</button><button type="button" class="mini quiet" data-act="pwavbryt">Avbryt</button></span>`
        : `<button type="button" class="mini quiet" data-act="pwvis">Nytt passord …</button>`;
      return `<tr data-bruker="${u.id}"${u.aktiv ? "" : ' class="inaktiv"'}>`
        + `<td><code>${esc(u.brukernavn)}</code>${deg ? " (deg)" : ""}</td>`
        + `<td><input type="text" data-bf="navn" value="${esc(u.navn)}" aria-label="Navn"></td><td>${rolle(u.rolle)}</td>`
        + `<td><label class="aktiv"><input type="checkbox" data-bf="admin"${u.admin ? " checked" : ""}${u.rolle !== "ingenior" ? " disabled" : ""}> admin</label></td>`
        + `<td><label class="aktiv"><input type="checkbox" data-bf="aktiv"${u.aktiv ? " checked" : ""}> aktiv</label>`
        + `${u.last_til ? ` <span class="tag">låst</span>` : ""}</td>`
        + `<td class="muted">${u.sist_inne ? tidFmt(u.sist_inne) : "aldri"}</td><td>${pw}</td></tr>`;
    };
    $("brukerTable").innerHTML = `<thead><tr><th>Brukernavn</th><th>Navn</th><th>Rolle</th><th></th><th></th><th>Sist inne</th><th></th></tr></thead><tbody>`
      + (nyBruker ? rad(null) : "") + BRUKERE.map(rad).join("") + `</tbody>`;
    $("brukerNy").hidden = nyBruker;
  }
  const brukerVerdier = (tr) => {
    const v = {};
    tr.querySelectorAll("[data-bf]").forEach((el) => { v[el.dataset.bf] = el.type === "checkbox" ? el.checked : el.value; });
    return v;
  };
  $("brukerNy").addEventListener("click", () => { nyBruker = true; drawBrukere(); const k = $("brukerTable").querySelector("[data-bf=brukernavn]"); if (k) k.focus(); });
  $("brukerTable").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act]"); if (!b) return;
    const tr = b.closest("tr[data-bruker]"), id = tr.dataset.bruker, a = b.dataset.act;
    if (a === "brukeravbryt") { nyBruker = false; b.blur(); drawBrukere(); return; }
    if (a === "pwvis" || a === "pwavbryt") { a === "pwvis" ? pwOpen.add(+id) : pwOpen.delete(+id); b.blur(); drawBrukere(); return; }
    if (a === "brukerlagre") act(async () => { const r = await api("POST", "/api/brukere", brukerVerdier(tr)); nyBruker = false; b.blur(); return r; },
      (r) => `Brukeren «${r.brukernavn}» er laget. Gi dem brukernavn og startpassord, og velg dem på emnene.`, $("brukerTable"));
    if (a === "pwlagre") {
      const pw = tr.querySelector("[data-pw]").value;
      act(async () => { const r = await api("PATCH", `/api/brukere/${id}`, { passord: pw }); pwOpen.delete(+id); b.blur(); return r; },
        (r) => `Nytt passord for «${r.brukernavn}»${+id === MEG.id ? ". Logg inn på nytt med det." : "."}`, $("brukerTable"));
    }
  });
  $("brukerTable").addEventListener("change", (e) => {
    const tr = e.target.closest("tr[data-bruker]"); if (!tr || tr.dataset.bruker === "ny" || !e.target.dataset.bf) return;
    const felt = e.target.dataset.bf, v = brukerVerdier(tr);
    e.target.blur();
    act(() => api("PATCH", `/api/brukere/${tr.dataset.bruker}`, { [felt]: v[felt] }), "Brukeren er lagret.", $("brukerTable"));
  });

  function renderAll(keepTable) {
    drawScope(); drawEmner(); drawBrukere();
    drawWheel(); drawWeeks(); drawStats(); drawDiffs(); drawNotices(); drawCopyButton(); drawHistory();
    if (!keepTable) drawTable();
    else inSY().forEach((p) => { const cell = document.querySelector(`tr[data-id="${CSS.escape(p.id)}"] .fristcell`); if (cell) cell.innerHTML = fristHtml(p);
      const oc = document.querySelector(`tr[data-id="${CSS.escape(p.id)}"] .ordercell`); if (oc) oc.innerHTML = orderCell(p);
      const pill = p.bomStatus && p.bomStatus.linjer != null && document.querySelector(`tr[data-id="${CSS.escape(p.id)}"] .pill.ok`);
      if (pill) pill.textContent = bomSum(p.bomStatus); });
  }

  // ---------- skoleår ----------
  function goSY(ar) { if (SYS.some((s) => s.ar === ar)) { SY = ar; highlight(null); renderAll(false); } }
  $("syPrev").addEventListener("click", () => goSY(SY - 1));
  $("syNext").addEventListener("click", () => goSY(SY + 1));
  $("syNow").addEventListener("click", () => { const na = SYS.find((s) => IDAG && s.uker.some((u) => u.dato === IDAG.dato)); if (na) goSY(na.ar); });

  // ---------- redigering ----------
  $("tbody").addEventListener("input", (e) => {
    const t = e.target;
    if (t.matches("input[data-role=telt]")) { tellEdit(t); return; }
    if (!t.matches("input[type=number][data-k]")) return;
    const p = projects.find((x) => x.id === t.dataset.id); if (!p) return;
    const k = t.dataset.k;
    if (t.value === "" && k === "sett") { p.sett = null; queueUpdate(p.id, { sett: null }); return; }
    const v = parseInt(t.value, 10); if (!Number.isFinite(v)) return;
    p[k] = k === "start" || k === "slutt" ? Math.min(53, Math.max(1, v)) : k === "sett" ? Math.min(999, Math.max(0, v)) : Math.min(20, Math.max(0, v));
    queueUpdate(p.id, { [k]: p[k] });
  });

  // anker: elementet du klikket på. En feil vises da også rett ved det, ikke bare øverst –
  // midt i en lang liste ser du ikke statuslinjen, men du ser knappen du trykket på.
  async function act(fn, okMsg, anker) {
    try { const r = await fn(); if (okMsg) setStatus(typeof okMsg === "function" ? okMsg(r) : okMsg); await load(); }
    catch (e) { setStatus(e.message); if (anker) feilVed(anker, e.message); }
  }
  function feilVed(el, msg) {
    const rad = el.closest(".levact, .mottak, .bomcell, .ordercell") || el.parentElement;
    if (!rad || !rad.isConnected) return;
    let p = rad.nextElementSibling;
    if (!p || !p.classList.contains("inlineerr")) {
      p = document.createElement("p");
      p.className = "inlineerr"; p.setAttribute("role", "alert");
      rad.after(p);
    }
    p.textContent = msg;
  }
  const P = (id) => "/api/gjennomforinger/" + encodeURIComponent(id);

  const orderApi = (id, body) => api("POST", P(id) + "/bestilling", body);
  $("tbody").addEventListener("toggle", (e) => {
    const d = e.target; if (d.matches && d.matches("details.tellbox")) tellOpen.set(d.dataset.id, d.open);
  }, true); // «toggle» bobler ikke
  const actVed = act;
  $("tbody").addEventListener("change", (e) => {
    const t = e.target;
    const act = (fn, msg) => actVed(fn, msg, t); // feil vises ved feltet du endret
    if (t.matches("select[data-role=ttype]")) { tellEdit(t); return; }
    if (t.matches("select[data-act=emne]")) {
      const e = EMNER.find((x) => String(x.id) === t.value);
      act(() => api("PATCH", P(t.dataset.id), { emne: t.value || null }),
        !e ? "Prosjektet har ikke noe emne." : `Prosjektet hører nå til ${e.kode}`
          + (e.ingeniorNavn ? ` (ingeniør: ${e.ingeniorNavn}${MEG.id && e.ingeniorId !== MEG.id ? " – du ser det ikke lenger under «Mine emner»" : ""})` : "") + ".");
      return;
    }
    if (t.matches("select[data-act=ostatus]")) {
      act(() => orderApi(t.dataset.id, { handling: "status", leverandor: t.dataset.lev, status: t.value }), "Status lagret.");
      return;
    }
    if (t.matches("input[data-act=ordrenr]")) {
      act(() => orderApi(t.dataset.id, { handling: "ordrenr", leverandor: t.dataset.lev, ordrenr: t.value }), "Ordrenummer lagret.");
      return;
    }
    const s = t.closest("select[data-act=koble]"); if (!s || !s.value) return;
    act(() => api("PATCH", P(s.dataset.id), { bom: s.value }), "BOM koblet: " + s.value);
  });
  $("tbody").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-act]");
    if (b) {
      const act = (fn, msg) => actVed(fn, msg, b); // feil vises ved knappen du trykket på
      const id = b.dataset.id, a = b.dataset.act, lev = b.dataset.lev;
      const ordrenr = () => { const i = b.closest(".levact") && b.closest(".levact").querySelector("[data-role=ordrenr]"); return i ? i.value : ""; };
      if (a === "vis") { openOrders.has(id) ? openOrders.delete(id) : openOrders.add(id); drawTable(); return; }
      if (a === "bestilt") act(() => orderApi(id, { handling: "bestilt", leverandor: lev, ordrenr: ordrenr() }), "Registrert som bestilt.");
      if (a === "tillegg") act(() => orderApi(id, { handling: "tillegg", leverandor: lev }), "Tilleggsbestillingen er registrert.");
      if (a === "lliste") act(() => api("POST", P(id) + "/liste", { leverandor: lev }), (r) => `Lagret «${r.fil}» i Bestillingslister (${r.linjer} linjer).`);
      if (a === "tlagre") act(async () => { const r = await api("POST", P(id) + "/telling", { linjer: tellCollect(id) }); delete tellDraft[id]; return r; },
        (r) => `Tellingen er lagret: ${r.avvik} avvik${r.svinn ? `, svinn ${nf0.format(r.svinnKr)} kr` : ""}. Oppdater «På lager» i BOM-en når du er fornøyd.`);
      if (a === "tskriv") {
        if (!b.classList.contains("confirm")) { b.classList.add("confirm"); b.textContent = "Bekreft: skriv telte tall inn i BOM-en"; return; }
        act(async () => {
          // ulagrede tall i skjemaet lagres først, så det som skrives er det du ser
          if (tellDraft[id]) { await api("POST", P(id) + "/telling", { linjer: tellCollect(id) }); delete tellDraft[id]; }
          return api("POST", P(id) + "/telling/skriv");
        }, (r) => writeMsg(r, "Oppdaterte «På lager» for"));
      }
      if (a === "mottakvis" || a === "mottakskjul") { a === "mottakvis" ? mottakOpen.add(`${id}|${lev}`) : mottakOpen.delete(`${id}|${lev}`); drawTable(); return; }
      if (a === "mottak") {
        if (!b.classList.contains("confirm")) { b.classList.add("confirm"); b.textContent = "Bekreft: legg til «På lager» i BOM-en"; return; }
        const linjer = {};
        b.closest(".mottakform").querySelectorAll("input[data-role=mottak]").forEach((i) => { linjer[i.dataset.key] = i.value; });
        act(async () => { const r = await orderApi(id, { handling: "mottak", leverandor: lev, linjer }); mottakOpen.delete(`${id}|${lev}`); return r; },
          (r) => mottakMsg(r, "La varene på lager"));
      }
      if (a === "angremottak") {
        if (!b.classList.contains("confirm")) { b.classList.add("confirm"); b.textContent = "Bekreft: trekk fra «På lager» igjen"; return; }
        act(() => orderApi(id, { handling: "angre_mottak", leverandor: lev }), (r) => mottakMsg(r, "Trakk mottaket fra igjen"));
      }
      if (a === "thent") act(async () => { const r = await api("POST", P(id) + "/telling/hent"); delete tellDraft[id]; return r; },
        (r) => `Tellingen bruker nå tallene fra BOM-en for ${r.hentet} ${r.hentet === 1 ? "linje" : "linjer"}${r.svinn ? `. Svinn: ${nf0.format(r.svinnKr)} kr` : ""}.`);
      if (a === "tangre") {
        if (!b.classList.contains("confirm")) { b.classList.add("confirm"); b.textContent = "Bekreft: sett «På lager» tilbake"; return; }
        act(() => api("POST", P(id) + "/telling/angre"), (r) => writeMsg(r, "Satte «På lager» tilbake for"));
      }
      if (a === "tslett") {
        if (!b.classList.contains("confirm")) { b.classList.add("confirm"); b.textContent = "Bekreft sletting"; return; }
        act(() => api("DELETE", P(id) + "/telling"), "Tellingen er slettet.");
      }
      if (a === "angre") {
        if (!b.classList.contains("confirm")) { b.classList.add("confirm"); b.textContent = "Bekreft angre"; return; }
        act(() => orderApi(id, { handling: "angre", leverandor: lev }), "Bestillingen er fjernet. Leverandøren står som ikke bestilt.");
      }
      if (a === "mal") act(() => api("POST", P(id) + "/mal"), (r) => LOKAL ? `Laget ${r.fil} i BOM-mappen og åpnet den i Excel. Fyll den ut og lagre, så dukker den opp her.` : `Laget ${r.fil} i BOM-mappen.`);
      if (a === "apne") act(() => api("POST", P(id) + "/apne"), "Åpnet i Excel. Lagre, og kom tilbake hit.");
      if (a === "liste") act(() => api("POST", P(id) + "/liste"), (r) => `Lagret «${r.fil}» i Bestillingslister (${r.linjer} linjer).`);
      if (a === "frakoble") act(() => api("PATCH", P(id), { bom: null }), "Koblingen er fjernet. Filen er ikke slettet.");
      return;
    }
    const d = e.target.closest("button.del"); if (!d) return;
    if (!d.classList.contains("confirm")) {
      document.querySelectorAll("button.del.confirm").forEach((x) => { x.classList.remove("confirm"); x.textContent = ING ? "Slett" : "Fjern"; });
      d.classList.add("confirm"); d.textContent = ING ? "Bekreft sletting" : "Bekreft – fjern"; return;
    }
    act(() => api("DELETE", P(d.dataset.id)), ING ? "Prosjektet er slettet fra dette skoleåret. BOM-filen ligger fortsatt i mappen."
      : "Prosjektet er fjernet. Ingeniøren får beskjed, og kan gjenopprette det.", d);
  });
  $("diffs").addEventListener("click", (e) => {
    const v = e.target.closest("button[data-act=vis]");
    if (v) {
      openOrders.add(v.dataset.id); drawTable();
      const row = document.querySelector(`tr.detail[data-for="${CSS.escape(v.dataset.id)}"]`);
      if (row) row.scrollIntoView({ block: "center", behavior: "smooth" });
      return;
    }
    const b = e.target.closest("button[data-act=lest]"); if (!b) return;
    act(() => api("POST", P(b.dataset.id) + "/sett"));
  });
  $("openBomDir").addEventListener("click", () => act(() => api("POST", "/api/mappe/bom")));
  $("openListDir").addEventListener("click", () => act(() => api("POST", "/api/mappe/lister")));

  // ---------- stengte perioder ----------
  function drawClosed() {
    $("closedList").textContent = CLOSED.map((c) => `${c.navn} uke ${c.fra}${c.fra !== c.til ? "–" + c.til : ""}`).join(", ") || "ingen";
    if ($("closedEdit").contains(document.activeElement)) return;
    $("closedEdit").innerHTML = CLOSED.map((c, i) =>
      `<div class="closedrow" data-i="${i}"><input type="text" data-k="navn" value="${esc(c.navn)}" aria-label="Navn">`
      + `<span>uke</span><input type="number" data-k="fra" min="1" max="53" value="${c.fra}" aria-label="Fra uke">`
      + `<span>–</span><input type="number" data-k="til" min="1" max="53" value="${c.til}" aria-label="Til uke">`
      + `<button type="button" class="mini quiet" data-act="fjern">Fjern</button></div>`).join("")
      + `<button type="button" class="mini" data-act="ny">+ Legg til stengt periode</button>`;
  }
  function saveClosed(list) {
    act(() => api("PUT", "/api/stengte", list), "Stengte perioder er lagret. Fristene er regnet ut på nytt.");
  }
  function readClosed() {
    return [...$("closedEdit").querySelectorAll(".closedrow")].map((r) => ({
      navn: r.querySelector("[data-k=navn]").value.trim(), fra: parseInt(r.querySelector("[data-k=fra]").value, 10), til: parseInt(r.querySelector("[data-k=til]").value, 10) }));
  }
  $("closedToggle").addEventListener("click", () => {
    const open = $("closedEdit").hidden; $("closedEdit").hidden = !open;
    $("closedToggle").textContent = open ? "Ferdig" : "Endre"; $("closedToggle").setAttribute("aria-expanded", String(open));
  });
  $("closedEdit").addEventListener("change", () => saveClosed(readClosed()));
  $("closedEdit").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act]"); if (!b) return;
    const list = readClosed();
    if (b.dataset.act === "fjern") list.splice(+b.closest(".closedrow").dataset.i, 1);
    else list.push({ navn: "Ny periode", fra: 8, til: 8 });
    saveClosed(list);
  });

  // ---------- nytt prosjekt ----------
  function openAdd(open) {
    $("addbox").hidden = !open; $("addToggle").setAttribute("aria-expanded", String(open));
    $("f-err").textContent = "";
    if (open) {
      $("f-sy").textContent = curSY() ? curSY().navn : "";
      // faglærer: velg blant egne emner (påkrevd); ingeniør: alle aktive emner, eller uten emne
      const valg = EMNER.filter((e) => e.aktiv);
      $("f-emne").innerHTML = (ING ? `<option value="">Uten emne</option>` : valg.length ? "" : `<option value="">Du har ingen emner – spør ingeniøren</option>`)
        + valg.map((e) => `<option value="${e.id}"${ING && MEG.id && e.ingeniorId === MEG.id ? " data-mitt" : ""}>${esc(e.kode)}${e.navn ? " – " + esc(e.navn) : ""}</option>`).join("");
      const mitt = $("f-emne").querySelector("[data-mitt]"); if (mitt) mitt.selected = true;
      $("f-navn").focus();
    }
  }
  $("addToggle").addEventListener("click", () => openAdd($("addbox").hidden));
  $("f-cancel").addEventListener("click", () => openAdd(false));
  const prefill = () => {
    const sem = $("f-semester").value;
    if ($("f-type").value === "sett") { $("f-start").value = sem === "Vår" ? 3 : 34; $("f-slutt").value = sem === "Vår" ? 23 : 50; }
  };
  $("f-type").addEventListener("change", prefill);
  $("f-semester").addEventListener("change", prefill);
  $("addForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const navn = $("f-navn").value.trim();
    if (!navn) { $("f-err").textContent = "Gi prosjektet et navn."; $("f-navn").focus(); return; }
    const num = (id, min, max) => Math.min(max, Math.max(min, parseInt($(id).value, 10) || min));
    const used = new Set(projects.map((p) => p.color));
    let color = 0; while (used.has(color) && color < NCOLORS - 1) color++;
    if (used.has(color)) color = projects.length % NCOLORS;
    const body = { navn, type: $("f-type").value, skolear: SY,
      start: num("f-start", 1, 53), slutt: num("f-slutt", 1, 53), lev: num("f-lev", 0, 20), buf: num("f-buf", 0, 20),
      notat: $("f-notat").value.trim(), color, sett: parseInt($("f-sett").value, 10) || null, emne: $("f-emne").value || null };
    if (!ING && !body.emne) { $("f-err").textContent = "Velg hvilket emne prosjektet hører til."; return; }
    $("f-save").disabled = true;
    try { await api("POST", "/api/gjennomforinger", body); $("addForm").reset(); openAdd(false); setStatus("Lagt til: " + navn); await load(); }
    catch (err) { $("f-err").textContent = err.message; }
    finally { $("f-save").disabled = false; }
  });

  // ---------- markering ----------
  function highlight(id, ev) {
    document.querySelectorAll("g.proj").forEach((g) => g.classList.toggle("dim", !!id && g.dataset.id !== id));
    document.querySelectorAll("#tbody tr").forEach((tr) => tr.classList.toggle("hl", !!id && tr.dataset.id === id));
    document.querySelectorAll(".ev").forEach((x) => x.classList.toggle("on", !!ev && x === ev));
    if (ev) markEvent(id, ev.dataset.kind, ev.dataset.d, ev.dataset.w); else { const hl = document.getElementById("hl"); if (hl) hl.innerHTML = ""; }
  }
  $("wheel").addEventListener("mouseover", (e) => { const g = e.target.closest("g.proj"); highlight(g ? g.dataset.id : null); });
  $("wheel").addEventListener("mouseleave", () => highlight(null));
  $("tbody").addEventListener("mouseover", (e) => { const tr = e.target.closest("tr[data-id]"); highlight(tr ? tr.dataset.id : null); });
  $("tbody").addEventListener("mouseleave", () => highlight(null));
  $("weeks").addEventListener("mouseover", (e) => { const ev = e.target.closest(".ev"); highlight(ev ? ev.dataset.id : null, ev); });
  $("weeks").addEventListener("mouseleave", () => highlight(null));
  $("weeks").addEventListener("click", (e) => { const ev = e.target.closest(".ev"); if (ev) highlight(ev.dataset.id, ev); });

  const t = new Date();
  $("today").textContent = dfmtY.format(Date.UTC(t.getFullYear(), t.getMonth(), t.getDate()));
  renderAll(false);
  load().then(() => { if (IDAG) $("today").textContent = "I dag: uke " + IDAG.uke + ", " + dfmtY.format(Date.UTC(t.getFullYear(), t.getMonth(), t.getDate())); });
  // feil i sidens egen kode (f.eks. i et klikk) skal synes, ikke bare stoppe stille
  const showError = (e) => setStatus("Noe gikk galt: " + ((e && e.message) || e) + ". Last siden på nytt, og si fra hvis det skjer igjen.");
  window.addEventListener("error", (ev) => showError(ev.error || ev.message));
  window.addEventListener("unhandledrejection", (ev) => showError(ev.reason));
  // hent på nytt når du kommer tilbake fra Excel
  window.addEventListener("focus", load);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
})();
