(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  let CLOSED = [];
  const NCOLORS = 8;
  const col = (p) => `var(--p${((p.color ?? 0) % NCOLORS) + 1})`;
  // projects = all runs in all school years; the wheel and the table show the chosen school year (SY)
  let mode = "loading", projects = [], ledigeBom = [];
  let LOCAL = true; // false when the app runs on a server: then we can't open Excel or folders
  let BOM_WIN = null; // on the server: the BOM folder as the engineer's PC sees it (ARSHJUL_BOM_WINDOWS)
  let SYS = [], SY = null, TODAY = null, NEXT8 = [], SETUP = null;
  // the logged-in user; ENG = engineer (everything), otherwise lecturer (own projects, no BOM/orders)
  let ME = { role: document.body.dataset.userrole || "engineer" }, ENG = ME.role === "engineer";
  let CHANGES = [], DELETED = [], LECTURERS = [], ENGINEERS = [], COURSES = [], PROGRAM = {}, TYPES = {};
  // /hjul and /uker show one panel on its own; SHARE: the wheel shared with students (no login – the link is the key)
  const VIEW = document.body.dataset.view || "", SHARE = document.body.dataset.share || "";
  // ALL = everything the server sent, which is only what you're involved in (the server filters).
  let ALL = [];
  const visible = () => ALL;
  const hasBom = (p) => !!(p.bomStatus && p.bomStatus.exists && !p.bomStatus.error && p.bomStatus.lines > 0);
  const nf2 = new Intl.NumberFormat("nb-NO", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  // ---------- server ----------
  const CSRF = (document.querySelector('meta[name="csrf"]') || {}).content || "";
  async function api(method, url, body) {
    const headers = { "X-CSRF-Token": CSRF };
    if (body) headers["Content-Type"] = "application/json";
    const res = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined, credentials: "same-origin" });
    let data = {}; try { data = await res.json(); } catch (e) {}
    if (res.status === 401 && data.login) { location.href = "/login?next=" + encodeURIComponent(location.pathname); throw new Error(data.error); }
    if (!res.ok) throw new Error(data.error || "Noe gikk galt (" + res.status + ").");
    return data;
  }
  // error: a red status line – something didn't work, and you need to see it
  function setStatus(t, error) { $("status").textContent = t; $("status").classList.toggle("error", !!error); }

  let loading = null, again = false;
  function load() {
    if (loading) { again = true; return loading; }
    loading = doLoad().finally(() => { loading = null; if (again) { again = false; load(); } });
    return loading;
  }
  async function doLoad() {
    let st;
    try { st = await api("GET", SHARE ? `/hjul/${encodeURIComponent(SHARE)}/data` : "/api/state"); }
    catch (e) {
      setStatus(e instanceof TypeError ? "Får ikke kontakt med årshjulet. Kjører «Start årshjul»-vinduet fortsatt?" : e.message);
      return;
    }
    try {
      CLOSED = st.closed_periods; ledigeBom = st.freeBoms;
      SYS = st.school_year; TODAY = st.today; NEXT8 = st.next8; SETUP = st.setup;
      ME = st.me; ENG = ME.role === "engineer"; document.body.dataset.userrole = ME.role;
      CHANGES = st.changes; DELETED = st.deletedRuns; LECTURERS = st.lecturers;
      ENGINEERS = st.engineers; COURSES = st.courses; PROGRAM = st.program; TYPES = st.types || {};
      // in the summer the page opens on the coming school year (see deadlines.school_year_shown)
      if (SY === null || !SYS.some((s) => s.year === SY)) SY = st.schoolYearShown ?? st.schoolYearNow;
      LOCAL = st.mode !== "server"; BOM_WIN = LOCAL ? null : st.folders.bomWindows || null;
      $("openListDir").hidden = !LOCAL; $("openBomDir").hidden = !LOCAL && !BOM_WIN;
      $("bomDir").textContent = BOM_WIN || st.folders.bom || "";
      $("openBomDir").title = BOM_WIN ? "Kopierer stien til BOM-mappen" : "Åpner " + (st.folders.bom || "BOM-mappen");
      // keep values that are waiting to be saved
      st.projects.forEach((p) => { const pend = pending[p.id]; if (pend) Object.assign(p, pend.patch); });
      ALL = st.projects;
      const created = visible();
      const sameIds = created.length === projects.length && created.every((p) => projects.some((q) => q.id === p.id));
      projects = created; mode = "ready";
      const ae = document.activeElement;
      const typing = ae && ae.matches && ae.matches("#tbody input, #tbody select[data-role]");
      renderAll(sameIds && !!typing);
      drawClosed();
    } catch (e) {
      // an error in the app's own code, not in the connection to the server
      console.error(e);
      setStatus("Noe gikk galt da siden skulle tegnes: " + e.message + ". Last siden på nytt, og si fra hvis det skjer igjen.");
    }
  }

  const pending = {};
  const bomReason = new Map(); // project id → why the set count wasn't written to the BOM
  function queueUpdate(id, patch) {
    const pend = pending[id] || (pending[id] = { patch: {}, timer: 0, chain: Promise.resolve() });
    Object.assign(pend.patch, patch);
    clearTimeout(pend.timer);
    pend.timer = setTimeout(() => {
      const body = pend.patch; pend.patch = {};
      pend.chain = pend.chain.then(() => api("PATCH", P(id), body))
        .then((r) => {
          // a new set count is also written to the BOM (B10) – tell the engineer if that didn't work
          const b = ENG && r && r.bomSets;
          if (b && b.written) { bomReason.delete(id); setStatus(`Antall sett er også endret i BOM-en (${b.file}).`); }
          else if (b && b.reason) { if (b.error) bomReason.set(id, b.reason); setStatus(b.reason, b.error); }
          if (!Object.keys(pend.patch).length) { delete pending[id]; load(); }
        })
        .catch((e) => setStatus(e.message, true));
    }, 500);
  }

  // ---------- weeks and dates (computed on the server, only placed on the wheel here) ----------
  const WEEK = 7 * 864e5;
  const toD = (s) => Date.parse(s + "T00:00:00Z");
  const weeksBetween = (a, b) => Math.round((toD(b) - toD(a)) / WEEK);
  const dfmt = new Intl.DateTimeFormat("nb-NO", { day: "numeric", month: "short", timeZone: "UTC" });
  const dfmtY = new Intl.DateTimeFormat("nb-NO", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  const range = (date) => { const m = toD(date); return dfmt.format(m) + "–" + dfmt.format(m + 6 * 864e5); };
  const inRange = (w, a, b) => (a <= b ? w >= a && w <= b : w >= a || w <= b);
  const nf0 = new Intl.NumberFormat("nb-NO", { maximumFractionDigits: 0 });
  const curSY = () => SYS.find((s) => s.year === SY);
  const inSY = () => projects.filter((p) => p.school_year === SY);
  // Order status against the deadline:
  //   extra = BOM or sets changed after ordering · ok = everything ordered · late = deadline passed, not ordered
  //   soon = deadline within 2 weeks · pending = plenty of time · gone = the project started without a registered order
  function orderState(p) {
    const b = p.orders;
    if (!p.bom && p.orderedWithoutBom) return "ok"; // without a BOM: the engineer has marked it as ordered
    if (b && b.extra) return "extra";
    if (b && b.done) return "ok";
    if (!TODAY) return "pending";
    if (p.deadlines.start.date <= TODAY.date) return "gone";
    if (p.deadlines.deadline.date < TODAY.date) return "late";
    return weeksBetween(TODAY.date, p.deadlines.deadline.date) <= 2 ? "soon" : "pending";
  }
  const STATE_COLOR = { ok: "var(--ok)", late: "var(--warn)", soon: "var(--soon)", extra: "var(--soon)" };
  const sorted = () => inSY().sort((a, b) => toD(a.deadlines.start.date) - toD(b.deadlines.start.date) || String(a.name).localeCompare(b.name, "nb"));

  // ---------- hjul ----------
  const C = 300, R_OUT = 252, R_IN = 96;
  let N = 52, SY_START = "";
  const pos = (date) => weeksBetween(SY_START, date); // weeks from the start of the school year, may be < 0 or ≥ N
  const ang = (x) => (x / N) * Math.PI * 2 - Math.PI / 2;
  const pt = (r, a) => [C + r * Math.cos(a), C + r * Math.sin(a)];
  const f = (n) => n.toFixed(2);
  function sector(r0, r1, p0, p1) {
    const a0 = ang(p0), a1 = ang(p1), large = p1 - p0 > N / 2 ? 1 : 0;
    const [x0, y0] = pt(r1, a0), [x1, y1] = pt(r1, a1), [x2, y2] = pt(r0, a1), [x3, y3] = pt(r0, a0);
    return `M${f(x0)} ${f(y0)}A${r1} ${r1} 0 ${large} 1 ${f(x1)} ${f(y1)}L${f(x2)} ${f(y2)}A${r0} ${r0} 0 ${large} 0 ${f(x3)} ${f(y3)}Z`;
  }
  const useSpan = (p) => [pos(p.deadlines.start.date), pos(p.deadlines.finish.date) + 1];
  const leadSpan = (p) => [pos(p.deadlines.deadline.date), pos(p.deadlines.start.date)];
  let laneOf = new Map(), laneR = () => [0, 0, 0, 0];
  const MONTHS = ["jan", "feb", "mar", "apr", "mai", "jun", "jul", "aug", "sep", "okt", "nov", "des"];

  // The wheel shows exactly the school year's weeks: everything is clipped to them. Neighbouring school years are
  // drawn faintly where they reach in – next year's deadlines in June, last year's count in August – so they're seen where they happen.
  const clip = ([a, b]) => [Math.max(0, a), Math.min(N, b)];
  const inside = (x) => x >= 0 && x < N;
  const hasReturn = (p) => p.type === "semester_kit" || p.type === "project"; // returned after several weeks: its own marker
  function pieces() {
    const neighbours = projects.filter((p) => Math.abs(p.school_year - SY) === 1)
      .sort((a, b) => toD(a.deadlines.start.date) - toD(b.deadlines.start.date));
    return [...sorted(), ...neighbours].map((p) => ({
      p, neighbour: p.school_year !== SY, use: clip(useSpan(p)),
      // the lecturer only sees the period the project is in use – not ordering and delivery
      lead: ENG ? clip(leadSpan(p)) : [0, 0],
      deadline: ENG && inside(pos(p.deadlines.deadline.date)),
      returns: ENG && hasReturn(p) && inside(pos(p.deadlines.returns.date)),
    })).filter((b) => !b.neighbour || b.use[1] > b.use[0] || b.lead[1] > b.lead[0] || b.deadline || b.returns);
  }

  // Projects share a lane when neither their use periods nor their ordering periods overlap.
  // Use is drawn in the wide outer band, ordering and delivery in a narrow rail on the inside.
  const slots = ([a, b]) => { const out = []; for (let x = a; x < b; x++) out.push(x); return out; };
  function assignLanes(list) {
    const lanes = [], map = new Map();
    list.forEach((b) => {
      const use = slots(b.use), lead = slots(b.lead);
      if (b.deadline) lead.push(pos(b.p.deadlines.deadline.date));  // the markers need room even without a band
      if (b.returns) use.push(pos(b.p.deadlines.returns.date));
      let j = lanes.findIndex((L) => !use.some((w) => L.use[w]) && !lead.some((w) => L.lead[w]));
      if (j < 0) { lanes.push({ use: [], lead: [] }); j = lanes.length - 1; }
      use.forEach((w) => (lanes[j].use[w] = true)); lead.forEach((w) => (lanes[j].lead[w] = true));
      map.set(b.p.id, j);
    });
    return { map, count: lanes.length };
  }

  function drawWheel() {
    const cur = curSY();
    if (!cur) { $("wheel").innerHTML = ""; return; }
    N = cur.weeks.length; SY_START = cur.start;
    $("syName").textContent = cur.name; $("syTable").textContent = cur.name;
    $("syPrev").disabled = SYS[0].year === SY; $("syNext").disabled = SYS[SYS.length - 1].year === SY;
    const list = pieces(), own_runs = list.filter((b) => !b.neighbour).length;
    const L = assignLanes(list); laneOf = L.map;
    const n = Math.max(L.count, 1);
    const step = Math.min(34, (232 - R_IN) / n);
    const band = Math.max(8, step * 0.8);
    // [indre kant, ytre kant, skinne ytre kant, bruk indre kant]
    laneR = (j) => { const o = 232 - j * step, r0 = o - band; return [r0, o, r0 + band * 0.26, r0 + band * 0.36]; };
    const inner = L.count ? laneR(L.count - 1)[0] - 6 : R_IN;

    let s = `<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="var(--card)"/><rect width="3" height="6" fill="var(--closed)"/></pattern></defs>`;
    // the month ring starts in August
    const t0 = toD(cur.start);
    for (let m = 0; m < 12; m++) {
      const mi = (7 + m) % 12, y = SY + (7 + m >= 12 ? 1 : 0);
      const p0 = m === 0 ? 0 : (Date.UTC(y, mi, 1) - t0) / WEEK;
      const p1 = m === 11 ? N : (Date.UTC(mi === 11 ? y + 1 : y, (mi + 1) % 12, 1) - t0) / WEEK;
      s += `<path d="${sector(R_OUT, R_OUT + 20, p0, p1)}" fill="${m % 2 ? "var(--bg)" : "var(--card)"}" stroke="var(--line)"/>`;
      const [x, yy] = pt(R_OUT + 10, ang((p0 + p1) / 2));
      s += `<text x="${x.toFixed(1)}" y="${yy.toFixed(1)}" text-anchor="middle" dominant-baseline="central" font-size="11" font-weight="600">${MONTHS[mi]}</text>`;
    }
    cur.weeks.forEach((u, i) => {
      const a = ang(i), long = u.week % 4 === 0;
      const [x0, y0] = pt(R_OUT, a), [x1, y1] = pt(R_OUT - (long ? 9 : 4), a);
      s += `<line x1="${x0.toFixed(1)}" y1="${y0.toFixed(1)}" x2="${x1.toFixed(1)}" y2="${y1.toFixed(1)}" stroke="var(--muted)" stroke-width="${long ? 1 : .6}" opacity=".6"/>`;
      if ([1, 10, 20, 32, 40, 50].includes(u.week)) {
        const [x, y] = pt(R_OUT - 16, ang(i + .5));
        s += `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="middle" dominant-baseline="central" font-size="8.5" style="font-family:var(--mono)">${u.week}</text>`;
      }
    });
    // stengte perioder: sammenhengende stengte uker
    for (let i = 0; i < N; i++) {
      if (!cur.weeks[i].closed || (i > 0 && cur.weeks[i - 1].closed)) continue;
      let j = i; while (j + 1 < N && cur.weeks[j + 1].closed) j++;
      const a = cur.weeks[i].week, b = cur.weeks[j].week;
      const name = [...new Set(cur.weeks.slice(i, j + 1).map((u) => u.closed))].join(", ") || "Stengt"; // the name comes from the server (Easter moves)
      s += `<path d="${sector(inner, R_OUT - 20, i, j + 1)}" fill="url(#hatch)" opacity=".85"><title>${esc(name)} (uke ${a}${a !== b ? "–" + b : ""})</title></path>`;
    }
    for (let j = 0; j < L.count; j++) {
      const [r0, r1] = laneR(j);
      s += `<path d="${sector(r0, r1, 0, N - 0.001)}" fill="none" stroke="var(--line)" stroke-dasharray="2 3"/>`;
    }
    const mr = Math.max(3, Math.min(6, band * 0.2));
    // /hjul: a number on each use arc, the same as in the list beside the wheel – colours alone don't tell projects apart
    const numberOf = VIEW === "wheel" ? projectNumbers() : new Map();
    const numberMark = (p, use, lo, hi) => {
      if (!numberOf.has(p.id) || use[1] <= use[0]) return "";
      const [x, y] = pt((lo + hi) / 2, ang((use[0] + use[1]) / 2)), r = Math.max(7, Math.min(10, (hi - lo) / 2));
      return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${r.toFixed(1)}" fill="var(--card)" stroke="${col(p)}" stroke-width="2"/>`
        + `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="middle" dominant-baseline="central" font-size="${(r * 1.1).toFixed(1)}" font-weight="700" style="fill:var(--ink)">${numberOf.get(p.id)}</text>`;
    };
    list.forEach(({ p, neighbour, use, lead, deadline, returns }) => {
      const d = p.deadlines, [r0, r1, rl, ru] = laneR(laneOf.get(p.id)), c = col(p);
      const name = neighbour ? `${esc(p.name)} (${esc(syName(p.school_year))})` : esc(p.name);
      s += `<g class="proj${neighbour ? " neighbour" : ""}" data-id="${esc(p.id)}">`;
      const useTitle = `${name}: i bruk uke ${d.start.week}${d.start.week !== d.finish.week ? "–" + d.finish.week : ""}`;
      if (!ENG) {
        // lecturer: only the use period, across the whole lane
        if (use[1] > use[0]) s += `<path class="ring-hit" data-kind="use" d="${sector(r0, r1, use[0], use[1])}" fill="${c}"><title>${useTitle}</title></path>`;
        s += numberMark(p, use, r0, r1) + `</g>`;
        return;
      }
      if (lead[1] > lead[0]) {
        s += `<path class="ring-hit" data-kind="lead" d="${sector(r0, rl, lead[0], lead[1])}" fill="${c}" opacity=".45"><title>${name}: bestilling og levering fra uke ${d.deadline.week}${d.deadline.year !== d.start.year ? " " + d.deadline.year : ""}</title></path>`;
      }
      if (use[1] > use[0]) s += `<path class="ring-hit" data-kind="use" d="${sector(ru, r1, use[0], use[1])}" fill="${c}"><title>${useTitle}</title></path>`;
      s += numberMark(p, use, ru, r1);
      if (deadline) {
        const [fx, fy] = pt((r0 + rl) / 2, ang(pos(d.deadline.date) + .5));
        const ring = STATE_COLOR[orderState(p)]; // order status – also for next year's deadlines in June
        if (ring) s += `<circle class="statusring" cx="${fx.toFixed(1)}" cy="${fy.toFixed(1)}" r="${(mr + 3).toFixed(1)}" fill="none" stroke="${ring}" stroke-width="2.5"/>`;
        s += `<circle data-ev="deadline" cx="${fx.toFixed(1)}" cy="${fy.toFixed(1)}" r="${mr.toFixed(1)}" fill="${c}" stroke="var(--card)" stroke-width="2"><title>${name}: bestillingsfrist uke ${d.deadline.week}, ${dfmtY.format(toD(d.deadline.date))} · ${esc(stateText(p))}</title></circle>`;
      }
      if (returns) {
        const [qx, qy] = pt((ru + r1) / 2, ang(pos(d.returns.date) + .5)), q = mr * 0.85;
        s += `<rect data-ev="returns" x="${(qx - q).toFixed(1)}" y="${(qy - q).toFixed(1)}" width="${(2 * q).toFixed(1)}" height="${(2 * q).toFixed(1)}" fill="var(--card)" stroke="${c}" stroke-width="2.5"><title>${name}: innlevering og telling uke ${d.returns.week}</title></rect>`;
      }
      s += `</g>`;
    });
    const tpos = TODAY ? pos(TODAY.date) : -1, iAr = tpos >= 0 && tpos < N;
    if (iAr) {
      const [tx0, ty0] = pt(inner - 4, ang(tpos + .5)), [tx1, ty1] = pt(R_OUT + 20, ang(tpos + .5));
      s += `<line x1="${tx0.toFixed(1)}" y1="${ty0.toFixed(1)}" x2="${tx1.toFixed(1)}" y2="${ty1.toFixed(1)}" stroke="var(--accent)" stroke-width="2"/>`;
    }
    s += `<g id="hl" class="hlmark"></g>`;
    const next = nextDeadline();
    s += `<text x="${C}" y="${C - 26}" text-anchor="middle" font-size="11" font-weight="700" letter-spacing=".08em">${iAr ? "I DAG" : "SKOLEÅR"}</text>`;
    s += `<text x="${C}" y="${C + 2}" text-anchor="middle" font-size="26" font-weight="700" style="fill:var(--accent);font-family:var(--mono)">${iAr ? "uke " + TODAY.week : esc(cur.name)}</text>`;
    if (mode === "loading") {
      s += `<text x="${C}" y="${C + 26}" text-anchor="middle" font-size="11">Henter prosjekter …</text>`;
    } else if (!own_runs) {
      s += `<text x="${C}" y="${C + 26}" text-anchor="middle" font-size="11">Ingen prosjekter dette skoleåret</text>`;
    } else if (ENG && next && iAr) {
      s += `<text x="${C}" y="${C + 26}" text-anchor="middle" font-size="11">neste frist: uke ${next.week}</text>`;
      s += `<text x="${C}" y="${C + 41}" text-anchor="middle" font-size="11" font-weight="600" style="fill:var(--ink)">${esc(short(next.p.name))}</text>`;
    }
    $("wheel").innerHTML = s;
  }
  const short = (n) => (String(n).length > 22 ? String(n).slice(0, 20) + "…" : n);

  // /hjul (and the shared wheel): every project in the school year beside the wheel – name, type, weeks and dates
  const projectNumbers = () => new Map(sorted().map((p, i) => [p.id, i + 1]));
  function drawWheelList() {
    if (mode === "loading") { $("wheelList").innerHTML = `<li><span class="empty">Henter prosjekter …</span></li>`; return; }
    const list = sorted();
    $("wheelList").innerHTML = list.length ? list.map((p) => {
      const d = p.deadlines, weeks = `uke ${d.start.week}${d.start.week !== d.finish.week ? "–" + d.finish.week : ""}`;
      const dates = `${dfmt.format(toD(d.start.date))}–${dfmtY.format(toD(d.finish.date) + 4 * 864e5)}`; // Monday of the first week – Friday of the last
      return `<li data-id="${esc(p.id)}"><span class="nr" style="border-color:${col(p)}">${list.indexOf(p) + 1}</span><b>${esc(p.name)}</b>`
        + `<small>${esc(TYPES[p.type] || p.type)} · ${weeks} · ${dates}</small></li>`;
    }).join("") : `<li><span class="empty">Ingen prosjekter dette skoleåret.</span></li>`;
  }

  // highlight one event (week) on one project's lane
  function markEvent(id, kind, date, week) {
    const hl = document.getElementById("hl"); if (!hl) return;
    const p = projects.find((x) => x.id === id);
    if (!p || !laneOf.has(id)) { hl.innerHTML = ""; return; }
    const d = p.deadlines, [r0, r1, rl, ru] = laneR(laneOf.get(id)), x = pos(date);
    let a = x, b = x + 1, lo = ru, hi = r1;
    if (kind === "start") [a, b] = useSpan(p);
    else if (kind === "prep") { a = pos(d.prep.date); b = pos(d.deadline.date) + 1; lo = r0; hi = rl; }
    else if (kind === "deadline") { lo = r0; hi = rl; }
    let s = `<path d="${sector(lo - 3, hi + 3, a, b)}" fill="none" stroke="var(--ink)" stroke-width="2.5" stroke-linejoin="round"/>`;
    const [lx, ly] = pt(R_OUT - 30, ang(x + .5));
    const [wx0, wy0] = pt(hi + 3, ang(x + .5)), [wx1, wy1] = pt(R_OUT - 22, ang(x + .5));
    s += `<line x1="${wx0.toFixed(1)}" y1="${wy0.toFixed(1)}" x2="${wx1.toFixed(1)}" y2="${wy1.toFixed(1)}" stroke="var(--ink)" stroke-width="1.5" stroke-dasharray="3 3"/>`;
    s += `<rect x="${(lx - 22).toFixed(1)}" y="${(ly - 10).toFixed(1)}" width="44" height="20" rx="6" fill="var(--ink)"/>`;
    s += `<text x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="middle" dominant-baseline="central" font-size="11" font-weight="700" style="fill:var(--card);font-family:var(--mono)">uke ${week}</text>`;
    hl.innerHTML = s;
  }

  // events from every school year, so June deadlines for the autumn's projects are included
  function allEvents() {
    const out = [];
    projects.forEach((p) => p.events.forEach((e) => out.push({ ...e, p })));
    return out;
  }
  // the next deadline that still needs an order
  function nextDeadline() {
    if (!TODAY) return null;
    return allEvents().filter((e) => e.kind === "deadline" && e.date >= TODAY.date && orderState(e.p) !== "ok")
      .sort((a, b) => toD(a.date) - toD(b.date))[0];
  }
  function stateText(p) {
    const b = p.orders, os = orderState(p);
    if (!b) return hasBom(p) ? "" : "BOM mangler";
    if (os === "extra") return "Må bestille mer";
    if (b.status === "partially_ordered") return `${b.orderedCount} av ${b.suppliers} bestilt`;
    if (os === "late") return "Frist passert – " + b.statusName.toLowerCase();
    return b.statusName;
  }

  // ---------- neste 8 uker ----------
  const ORDER = { deadline: 0, prep: 1, start: 2, returns: 3 };
  function evTag(e) {
    if (!ENG) return "";
    if (e.kind === "returns") return e.p.count && e.p.count.saved ? ` <span class="tag ok">Telt</span>` : "";
    if (e.kind !== "prep" && e.kind !== "deadline") return "";
    if (!hasBom(e.p)) return ` <span class="tag">BOM mangler</span>`;
    const os = orderState(e.p);
    if (os === "ok") return ` <span class="tag ok">${esc(e.p.orders.statusName)}</span>`;
    if (os === "extra") return ` <span class="tag soon">Må bestille mer</span>`;
    if (e.p.orders && e.p.orders.status === "partially_ordered") return ` <span class="tag soon">${esc(stateText(e.p))}</span>`;
    return "";
  }
  function drawWeeks() {
    if (mode === "loading") { $("weeks").innerHTML = `<span class="empty">Henter prosjekter …</span>`; return; }
    const ev = allEvents();
    let h = "";
    NEXT8.forEach((w, i) => {
      const list = ev.filter((e) => e.date === w.date).sort((a, b) => ORDER[a.kind] - ORDER[b.kind]);
      h += `<div class="week${i === 0 ? " now" : ""}"><div class="wk"><b>Uke ${w.week}</b><small>${i === 0 ? "denne uken" : range(w.date)}</small></div><div class="evs">`;
      h += list.length ? list.map((e) =>
        `<div class="ev ${e.kind}" data-id="${esc(e.p.id)}" data-kind="${e.kind}" data-d="${e.date}" data-w="${e.week}"><span class="dot" style="background:${col(e.p)}"></span><span><span class="what">${esc(e.what)}</span> <span class="who">· ${esc(e.p.name)}</span>${evTag(e)}</span></div>`).join("")
        : `<span class="empty">${w.closed ? esc(w.closed) : "Ingenting planlagt"}</span>`;
      h += `</div></div>`;
    });
    $("weeks").innerHTML = h;
  }

  // ---------- toppfelt ----------
  function drawStats() {
    if (mode === "loading") { $("stats").innerHTML = ""; return; }
    const n = nextDeadline();
    const list = inSY(), missing = list.filter((p) => !hasBom(p)).length;
    const u = n ? weeksBetween(TODAY.date, n.date) : 0;
    // across school years: a passed deadline matters just as much whichever year you're looking at
    const late = ENG ? projects.filter((p) => orderState(p) === "late") : [];
    const more = ENG ? projects.filter((p) => orderState(p) === "extra") : [];
    const names = (list) => esc(list.map((p) => p.name).join(", "));
    $("stats").innerHTML =
      (late.length ? `<div class="stat warn"><small>Frist passert, ikke bestilt</small><b>${late.length} ${late.length === 1 ? "prosjekt" : "prosjekter"}</b><div class="muted" style="font-size:12px">${names(late)}</div></div>` : "") +
      (more.length ? `<div class="stat soon"><small>Må bestille mer</small><b>${more.length} ${more.length === 1 ? "prosjekt" : "prosjekter"}</b><div class="muted" style="font-size:12px">${names(more)}</div></div>` : "") +
      (n ?`<div class="stat${u <= 2 ? " warn" : ""}"><small>Neste bestillingsfrist</small><b>${esc(n.p.name)} · uke ${n.week}</b><div class="muted" style="font-size:12px">${u === 0 ? "denne uken" : "om " + u + (u === 1 ? " uke" : " uker")}</div></div>` : "") +
      (ENG && list.length ? `<div class="stat"><small>Med BOM ${esc(curSY().name)}</small><b>${list.length - missing} av ${list.length}</b><div class="muted" style="font-size:12px">${missing} mangler BOM</div></div>` : "");
  }


  // ---------- tabell ----------
  // the lecturer sees the consequence of moving a project: a deadline that is close or passed
  function deadlineWarning(p) {
    if (ENG || !p.own || !TODAY || p.deadlines.start.date <= TODAY.date) return "";
    const u = weeksBetween(TODAY.date, p.deadlines.deadline.date);
    if (u < 0) return `<span class="deadline-warning">Fristen er passert – ingeniøren varsles</span>`;
    if (u <= 2) return `<span class="deadline-warning">${u === 0 ? "Fristen er denne uken" : `Fristen er om ${u} ${u === 1 ? "uke" : "uker"}`} – ingeniøren varsles</span>`;
    return "";
  }
  const deadlineHtml = (p) => `uke ${p.deadlines.deadline.week}<small>${dfmtY.format(toD(p.deadlines.deadline.date))}</small>${deadlineWarning(p)}`;
  const winPath = (file) => BOM_WIN + "\\" + file.replace(/\//g, "\\");
  // Excel only finds the file from an ms-excel: link when the path is plain ASCII: the browser
  // percent-encodes æøå, and Excel doesn't decode them (tested October 2026, see VEIKART.md)
  function excelHref(path) {
    if (!/^[ -~]+$/.test(path) || /[#%?]/.test(path)) return null;
    const url = path.startsWith("\\\\") ? "file:" + path.replace(/\\/g, "/") : "file:///" + path.replace(/\\/g, "/");
    return "ms-excel:ofe|u|" + url.replace(/ /g, "%20");
  }
  function openButtons(p) {
    if (LOCAL) return `<button type="button" class="mini" data-act="open" data-id="${esc(p.id)}">Åpne i Excel</button>`;
    if (!BOM_WIN || !p.bom) return "";
    const path = winPath(p.bom), href = excelHref(path);
    return (href ? `<a class="mini" href="${esc(href)}">Åpne i Excel</a>` : "")
      + `<button type="button" class="mini" data-act="copypath" data-path="${esc(path)}" title="${esc(path)}">Kopier sti</button>`;
  }
  async function copyPath(path, okMsg) {
    try { await navigator.clipboard.writeText(path); setStatus(okMsg); }
    catch (e) { setStatus("Fikk ikke kopiert. Stien er: " + path); }
  }
  const bomSum = (b) => `${b.lines} linjer · ${b.to_order} å bestille · ${nf0.format(b.excl)} kr`;
  function bomCell(p) {
    const b = p.bomStatus, id = esc(p.id);
    const apne = openButtons(p);
    if (b && b.exists && !b.error && !b.lines) {
      return `<div class="bomcell"><span class="pill miss" title="${esc(b.file)}">Tom BOM – fyll ut i Excel</span>${apne}</div>`;
    }
    if (b && b.exists && !b.error) {
      return `<div class="bomcell"><span class="pill ok" title="${esc(b.file)} · sist lagret ${esc(b.changed)}">${bomSum(b)}</span>`
        + apne
        + `<button type="button" class="mini" data-act="list" data-id="${id}">Bestillingsliste</button></div>`;
    }
    const pick = ledigeBom.length
      ? `<select class="mini" data-act="link" data-id="${id}" aria-label="Koble til eksisterende BOM"><option value="">Koble til fil …</option>${ledigeBom.map((f) => `<option>${esc(f)}</option>`).join("")}</select>` : "";
    if (b && !b.exists) return `<div class="bomcell"><span class="pill miss" title="${esc(b.file)}">Finner ikke filen</span>${pick}<button type="button" class="mini quiet" data-act="unlink" data-id="${id}">Fjern kobling</button></div>`;
    if (b && b.error) return `<div class="bomcell"><span class="pill miss" title="${esc(b.error)}">Kan ikke leses</span>${apne}<button type="button" class="mini quiet" data-act="unlink" data-id="${id}">Fjern kobling</button></div>`;
    return `<div class="bomcell"><button type="button" class="mini" data-act="template" data-id="${id}">Lag BOM-mal</button>${pick}</div>`;
  }
  // ---------- ordering per supplier ----------
  const openOrders = new Set();
  const COLS = 9;
  function orderCell(p) {
    const b = p.orders, t = p.count, L = t && t.saved;
    if (!p.bom) { // no BOM to register per supplier: just «bestilt» or not
      return p.orderedWithoutBom
        ? `<span class="ostat ok">Bestilt ${dfmt.format(toD(p.orderedWithoutBom))}</span> <button type="button" class="mini quiet" data-act="withoutbom" data-id="${esc(p.id)}" data-v="0">Angre</button>`
        : `<button type="button" class="mini" data-act="withoutbom" data-id="${esc(p.id)}" data-v="1" title="Prosjektet har ingen BOM – marker at det som trengs, er bestilt">Marker som bestilt</button>`;
    }
    if (!b && !L) return `<span class="muted">${hasBom(p) ? "" : "—"}</span>`;
    const cls = b ? ({ ok: "ok", late: "late", soon: "soon", extra: "soon" }[orderState(p)] || "") : "";
    const open = openOrders.has(p.id);
    const venter = L && L.pending > 0;
    const sub = L ? (!venter ? "Telt · lager oppdatert" : L.inBom ? "Telt · endringer ikke skrevet" : "Telt · ikke skrevet til BOM")
      : t && returnReady(p) ? "Klar for telling" : "";
    return `<button type="button" class="ostat ${cls}" data-act="show" data-id="${esc(p.id)}" aria-expanded="${open}">${esc(b ? stateText(p) : "Telling")}<span class="chev" aria-hidden="true">${open ? "▴" : "▾"}</span></button>`
      + (sub ? `<small class="count-note${venter ? " soon" : ""}">${sub}</small>` : "");
  }
  const returnReady = (p) => !!TODAY && p.deadlines.returns.date <= TODAY.date;

  // ---------- counting after the project ----------
  const countDraft = {}, countOpen = new Map();
  const fv = (l, type) => (type === "consumable" ? Math.max(0, l.stockBefore + l.ordered - l.handed_out) : l.stockBefore + l.ordered);
  const signed = (x) => (Math.abs(x) < 1e-9 ? "0" : (x > 0 ? "+" : "−") + qty(Math.abs(x)));
  function countSection(p) {
    const t = p.count; if (!t) return "";
    const id = esc(p.id), L = t.saved, draft = countDraft[p.id] || {};
    const lines = L ? L.lines : t.basis, iBom = L ? L.written_values : {};
    const bomState = !L ? "" : Object.keys(L.changedInExcel).length ? " · «På lager» er endret i Excel etterpå"
      : !L.inBom ? " · ikke skrevet til BOM-en ennå"
      : L.pending ? ` · ${L.pending} ${L.pending === 1 ? "endring" : "endringer"} ikke skrevet til BOM-en`
      : ` · «På lager» oppdatert ${dfmtY.format(toD(L.written))}`;
    const summary = L
      ? `Telt ${dfmtY.format(toD(L.date))} · ${L.deviation} avvik${L.loss ? ` · svinn ${nf0.format(L.lossCost)} kr` : ""}${bomState}`
      : returnReady(p) ? "Klar for telling" : `Telles etter innlevering, uke ${p.deadlines.returns.week}`;
    const open = countOpen.has(p.id) ? countOpen.get(p.id) : !L && returnReady(p);
    const rows = lines.map((l) => {
      const d = draft[l.key] || {}, type = d.type || l.type, f = fv(l, type);
      const counted = d.counted !== undefined ? d.counted : (L ? l.counted : f);
      const k = `data-id="${id}" data-key="${esc(l.key)}"`;
      const typeCell = `<select class="mini" data-role="ctype" ${k} aria-label="Utstyr eller forbruk: ${esc(l.description)}">`
          + `<option value="equipment"${type === "equipment" ? " selected" : ""}>Utstyr</option><option value="consumable"${type === "consumable" ? " selected" : ""}>Forbruk</option></select>`;
      // what the BOM holds from this count, if it's something other than what's counted now
      const written = iBom[l.key], differs = L && L.inBom && (written === undefined || Math.abs(written - l.counted) > 1e-9);
      const excel = L && L.changedInExcel[l.key];
      const bomNote = excel !== undefined ? `<small class="excel">endret i Excel: ${qty(excel)}</small>`
        : !differs ? "" : written === undefined ? `<small>ikke i BOM-en</small>` : `<small>i BOM-en: ${qty(written)}</small>`;
      const countedCell = `<input type="number" min="0" step="any" data-role="counted" ${k} value="${esc(counted)}" aria-label="Telt: ${esc(l.description)}">${bomNote}`;
      const diff = (parseFloat(counted) || 0) - f;
      return `<tr data-key="${esc(l.key)}" data-stock="${l.stockBefore}" data-ordered="${l.ordered}" data-handed="${l.handed_out}">`
        + `<td>${esc(l.description)}${l.part_number ? `<small>${esc(l.part_number)}</small>` : ""}</td><td>${typeCell}</td>`
        + `<td class="num"${l.assumed ? ` title="Ingen registrert bestilling – antatt bestilt etter BOM-en"` : ""}>${qty(l.stockBefore + l.ordered)}${l.assumed ? "*" : ""}</td><td class="num">${qty(l.handed_out)}</td>`
        + `<td class="num tf">${qty(f)}</td><td class="num">${countedCell}</td>`
        + `<td class="num td ${diff < -1e-9 && type === "equipment" ? "loss" : ""}">${signed(diff)}</td></tr>`;
    }).join("");
    let actions = `<button type="button" class="mini" data-act="csave" data-id="${id}">${L ? "Lagre endringer" : "Lagre telling"}</button>`;
    if (L && L.pending) actions += `<button type="button" class="mini" data-act="cwrite" data-id="${id}">${L.inBom ? "Skriv endringene til BOM-en …" : "Oppdater «På lager» i BOM-en …"}</button>`;
    if (L && L.inBom) actions += `<button type="button" class="mini quiet" data-act="cundo" data-id="${id}">Angre – sett «På lager» tilbake …</button>`;
    else if (L) actions += `<button type="button" class="mini quiet" data-act="cdelete" data-id="${id}">Slett telling</button>`;
    let note = !L && t.assumedOrdered ? `<p class="muted">* Leverandøren er ikke registrert som bestilt, så «før» bruker det BOM-en sa skulle bestilles.</p>` : "";
    const nExcel = L ? Object.keys(L.changedInExcel).length : 0;
    if (nExcel) note += `<div class="excelnote"><span>«På lager» er endret i Excel for ${nExcel} ${nExcel === 1 ? "linje" : "linjer"} etter at tellingen ble skrevet. `
      + `Var det en retting av tellingen, kan tellingen bruke tallene fra BOM-en. Ellers kan du la den stå.</span>`
      + `<button type="button" class="mini" data-act="cfetch" data-id="${id}">Bruk tallene fra BOM-en i tellingen</button></div>`;
    const fmtTime = (s) => { const d = new Date(s); return isNaN(d) ? esc(s) : d.toLocaleString("nb-NO", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); };
    const log = L && L.log.length ? `<ul class="count-log">${L.log.map((x) =>
      `<li>${fmtTime(x.time)}: ${{ undone: "satt tilbake", fetched: "hentet fra BOM-en" }[x.action] || "skrev"} ${x.lines} ${x.lines === 1 ? "linje" : "linjer"}`
      + (x.conflicts ? `, ${x.conflicts} hoppet over (endret i Excel)` : "")
      + (x.backup ? ` · sikkerhetskopi: BOM/Sikkerhetskopi/${esc(x.backup)}` : "") + `</li>`).join("")}</ul>` : "";
    return `<details class="count-box" data-id="${id}"${open ? " open" : ""}><summary><b>Telling etter prosjektet</b> <span class="muted">· ${summary}</span></summary>`
      + `<p class="muted">Tell det du har igjen etter prosjektet. Utstyr skal komme tilbake; forbruk brukes opp. Det telte blir nytt «På lager» i BOM-en.</p>${note}`
      + `<div class="count-scroll"><table class="count"><thead><tr><th>Linje</th><th>Type</th><th class="num" title="På lager + bestilt som ikke er lagt på lager ennå">Før</th><th class="num">Delt ut</th><th class="num">Forventet</th><th class="num">Telt</th><th class="num">Avvik</th></tr></thead><tbody>${rows}</tbody></table></div>`
      + `<div class="supplier-act">${actions}</div>${log}</details>`;
  }
  // update "expected" and "deviation" on the row while you type, and remember the numbers until they're saved
  function countRow(tr, id) {
    const sel = tr.querySelector("[data-role=ctype]"), inp = tr.querySelector("[data-role=counted]");
    const type = sel ? sel.value : "equipment";
    const l = { stockBefore: +tr.dataset.stock, ordered: +tr.dataset.ordered, handed_out: +tr.dataset.handed };
    const f = fv(l, type), diff = (parseFloat(inp.value) || 0) - f;
    tr.querySelector(".tf").textContent = qty(f);
    const td = tr.querySelector(".td"); td.textContent = signed(diff); td.classList.toggle("loss", diff < -1e-9 && type === "equipment");
    (countDraft[id] || (countDraft[id] = {}))[tr.dataset.key] = { type, counted: inp.value };
  }
  function countEdit(el) { countRow(el.closest("tr"), el.dataset.id); }
  function writeMsg(r, verb) {
    const n = (x) => `${x} ${x === 1 ? "linje" : "linjer"}`;
    let s = r.written.length ? `${verb} ${n(r.written.length)} i BOM-en. Sikkerhetskopi: ${r.backup}.` : "Ingenting ble skrevet til BOM-en.";
    if (r.conflict.length) s += ` ${n(r.conflict.length)} ble ikke rørt fordi «På lager» er endret i Excel siden: `
      + r.conflict.map((k) => `${k.description} (står ${qty(k.actual)}, ventet ${qty(k.expected)})`).join(", ") + ".";
    if (r.skipped.length) s += ` ${n(r.skipped.length)} hoppet over (finnes ikke, eller står flere ganger i BOM-en).`;
    return s;
  }
  function countCollect(id) {
    const box = document.querySelector(`details.tellbox[data-id="${CSS.escape(id)}"]`);
    return [...box.querySelectorAll("tbody tr[data-key]")].map((tr) => ({
      key: tr.dataset.key, type: tr.querySelector("[data-role=ctype]").value, counted: tr.querySelector("[data-role=counted]").value }));
  }
  const qty = (x) => fmtNum(Math.round(x * 100) / 100);
  // ---------- receipts: add what has arrived to «På lager» in the BOM ----------
  const receiptOpen = new Set();
  function receiptHtml(p, g, k) {
    if (!g.receipt.length || g.status === "none" || g.status === "in_stock") return "";
    const added = g.receipt.filter((m) => m.added > 0), rest = g.receipt.filter((m) => m.remaining > 0);
    const fid = `${p.id}|${g.key}`, open = receiptOpen.has(fid) && rest.length;
    let s = `<div class="receipt">`;
    const sum = !added.length ? "Ingenting lagt på lager ennå."
      : !rest.length ? `Alt er lagt på lager (${g.receipt.length} ${g.receipt.length === 1 ? "linje" : "linjer"}).`
      : `${added.length} av ${g.receipt.length} linjer er lagt på lager.`;
    s += `<span class="muted">${sum}</span>`;
    if (rest.length && !open) s += `<button type="button" class="mini" data-act="receiptshow" ${k}>Registrer mottak …</button>`;
    if (added.length) s += `<button type="button" class="mini quiet" data-act="undoreceipt" ${k}>Angre mottak …</button>`;
    if (open) {
      s += `<div class="receipt-form"><p class="muted">Hvor mange kom? Antallet legges til «På lager» i BOM-en.</p>`
        + rest.map((m) => `<label><span>${esc(m.description)}${m.part_number ? ` <small>${esc(m.part_number)}</small>` : ""}</span>`
          + `<input type="number" min="0" step="any" data-role="receipt" data-key="${esc(m.key)}" value="${m.remaining}">`
          + `<small>av ${qty(m.remaining)}${m.added ? ` (${qty(m.added)} lagt på lager før)` : ""}</small></label>`).join("")
        + `<div class="supplier-act"><button type="button" class="mini" data-act="receipt" ${k}>Legg på lager i BOM-en …</button>`
        + `<button type="button" class="mini quiet" data-act="receipthide" ${k}>Avbryt</button></div></div>`;
    }
    if (g.stockLog.length) s += `<ul class="count-log">${g.stockLog.map((x) => {
      const n = Object.keys(x.lines).length;
      return `<li>${new Date(x.time).toLocaleString("nb-NO", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}: `
        + `${x.action === "undone" ? "trakk fra" : "la på lager"} ${n} ${n === 1 ? "linje" : "linjer"}${x.backup ? ` · sikkerhetskopi: BOM/Sikkerhetskopi/${esc(x.backup)}` : ""}</li>`;
    }).join("")}</ul>`;
    return s + `</div>`;
  }
  function receiptMsg(r, verb) {
    const n = (x) => `${x} ${x === 1 ? "linje" : "linjer"}`;
    let s = r.written.length ? `${verb} for ${n(r.written.length)} i BOM-en. Sikkerhetskopi: ${r.backup}.` : "Ingenting ble endret i BOM-en.";
    if (r.conflict.length) s += ` ${n(r.conflict.length)} ble ikke endret fordi «På lager» ville blitt negativt: `
      + r.conflict.map((k) => `${k.description} (står ${qty(k.actual)})`).join(", ") + ".";
    if (r.skipped.length) s += ` ${n(r.skipped.length)} hoppet over (finnes ikke, eller står flere ganger i BOM-en).`;
    return s;
  }

  function orderDetail(p) {
    const id = esc(p.id);
    const rows = (p.orders ? p.orders.groups : []).map((g) => {
      const k = `data-id="${id}" data-supplier="${esc(g.key)}"`;
      let act;
      if (g.status === "in_stock") act = `<span class="muted">Ingenting å bestille</span>`;
      else if (g.status === "none") act =
        `<input type="text" class="order-no" data-role="order_no" placeholder="Ordrenr (valgfritt)" aria-label="Ordrenummer for ${esc(g.name)}">`
        + `<button type="button" class="mini" data-act="ordered" ${k}>Registrer bestilt</button>`;
      else act =
        `<select class="mini" data-act="ostatus" ${k} aria-label="Status for ${esc(g.name)}">${["ordered", "partial", "received"].map((s) =>
          `<option value="${s}"${s === g.status ? " selected" : ""}>${{ ordered: "Bestilt", partial: "Delvis mottatt", received: "Mottatt" }[s]}</option>`).join("")}</select>`
        + `<input type="text" class="order-no" data-act="order_no" ${k} value="${esc(g.order_no)}" placeholder="Ordrenr" aria-label="Ordrenummer for ${esc(g.name)}">`
        + `<span class="muted when">bestilt ${dfmtY.format(toD(g.orderedDate))}${g.receivedDate && g.status !== "ordered" ? " · mottatt " + dfmtY.format(toD(g.receivedDate)) : ""}</span>`
        + `<button type="button" class="mini quiet" data-act="undo" ${k}>Angre</button>`;
      const liste = g.lines && !g.removed ? `<button type="button" class="mini" data-act="supplierlist" ${k}>Liste</button>` : "";
      const rec = receiptHtml(p, g, k);
      let notes = "";
      if (g.removed) notes += `<li class="muted">Leverandøren står ikke lenger i BOM-en.</li>`;
      if (g.extra.length) notes += g.extra.map((t) =>
        `<li class="more"><b>${esc(t.description)}</b>${t.part_number ? " · " + esc(t.part_number) : ""}: bestilt ${qty(t.ordered)}, trenger ${qty(t.needs)} → <b>${qty(t.missing)} til</b></li>`).join("")
        + `<li><button type="button" class="mini" data-act="extra" ${k}>Registrer tilleggsbestilling</button></li>`;
      if (g.surplus.length) notes += g.surplus.map((t) =>
        `<li class="muted">${esc(t.description)}: bestilt ${qty(t.ordered)}, trenger nå ${qty(t.needs)}</li>`).join("");
      return `<div class="supplier"><div class="supplier-head"><b>${esc(g.name)}</b><span class="muted">${g.lines} ${g.lines === 1 ? "linje" : "linjer"} · ${nf0.format(g.excl)} kr</span></div>`
        + `<div class="supplier-act" data-supplier="${esc(g.key)}">${act}${liste}</div>${rec}${notes ? `<ul class="deviation">${notes}</ul>` : ""}</div>`;
    }).join("");
    return `<tr class="detail" data-for="${id}"><td colspan="${COLS}">${rows ? `<div class="suppliers">${rows}</div>` : ""}${countSection(p)}</td></tr>`;
  }

  // a proposed course can be used by those on it straight away, but it's marked until the admin approves it
  const courseLabel = (e) => `${esc(e.code)}${e.name ? " – " + esc(e.name) : ""}${e.approved ? "" : " (venter på godkjenning)"}`;

  // «Emne: IELS1001 · Faglærer · Ingeniør» under the project name; the engineer chooses the course on their own projects
  function courseLine(p) {
    const people = [p.lecturerName && `faglærer ${esc(p.lecturerName)}${!ENG && p.own ? " (deg)" : ""}`,
                  p.engineerName && `ingeniør ${esc(p.engineerName)}${ENG && ME.id && p.engineerId === ME.id ? " (deg)" : ""}`]
      .filter(Boolean).join(" · ");
    if (ENG && p.own) {
      const choices = COURSES.filter((e) => e.active || e.id === p.courseId);
      return `<label class="owner${p.courseId ? "" : " missing"}">Emne: <select data-act="course" data-id="${esc(p.id)}" aria-label="Emne for ${esc(p.name)}">`
        + `<option value="">${p.courseId ? "uten emne" : "Velg emne …"}</option>`
        + choices.map((e) => `<option value="${e.id}"${e.id === p.courseId ? " selected" : ""}>${courseLabel(e)}</option>`).join("")
        + `</select>${people ? ` · ${people}` : ""}</label>`;
    }
    if (!p.courseCode) return ENG ? `<span class="owner missing">Uten emne</span>` : "";
    return `<span class="owner">${esc(p.courseCode)}${people ? ` · ${people}` : ""}</span>`;
  }

  const typeSelect = (p) => `<select data-act="type" data-id="${esc(p.id)}" aria-label="Type for ${esc(p.name)}">`
    + Object.entries(TYPES).map(([k, n]) => `<option value="${k}"${k === p.type ? " selected" : ""}>${esc(n)}</option>`).join("") + `</select>`;

  // name and note: «Endre» opens two fields in the cell (own projects, lecturers too)
  const nameEditing = new Map(); // project id → {name, note} as you've typed them
  function nameCell(p) {
    const u = nameEditing.get(p.id);
    if (u) return `<div class="name-edit" data-id="${esc(p.id)}">`
      + `<input type="text" data-name="name" value="${esc(u.name)}" aria-label="Navn på prosjekt" maxlength="120">`
      + `<input type="text" data-name="note" value="${esc(u.note)}" placeholder="Notat (valgfritt)" aria-label="Notat">`
      + `<span><button type="button" class="mini" data-act="namesave" data-id="${esc(p.id)}">Lagre</button> `
      + `<button type="button" class="mini quiet" data-act="namecancel" data-id="${esc(p.id)}">Avbryt</button></span></div>`;
    return `<b><span class="dot" style="background:${col(p)}"></span>${esc(p.name)}`
      + (p.own ? ` <button type="button" class="mini quiet edit" data-act="nameedit" data-id="${esc(p.id)}" aria-label="Endre navn og notat for ${esc(p.name)}">Endre</button>` : "")
      + `</b>${p.note ? `<small>${esc(p.note)}</small>` : ""}`;
  }

  // students ÷ group size + spare: edited on a line of its own under the project, so the table stays narrow
  const setsEditing = new Set(); // project ids
  const setsFormula = (p) => (p.students != null ? `${p.students} ÷ ${p.group_size} + ${p.spare}` : "Antall studenter?");
  const setsText = (p) => (p.sets != null ? `${p.sets} sett` : "Antall studenter?");
  function setsRow(p) {
    const id = esc(p.id);
    const field = (k, min, max, lbl, text) => `<label class="sf"><span>${text}</span>`
      + `<input type="number" id="in-${id}-${k}" data-id="${id}" data-k="${k}" min="${min}" max="${max}" value="${p[k] ?? ""}" aria-label="${lbl}"></label>`;
    return `<tr class="sets-row" data-for="${id}"><td colspan="${COLS}"><div class="sets-edit">`
      + field("students", 0, 2000, "Antall studenter", "Studenter") + `<span class="op">÷</span>`
      + field("group_size", 1, 20, "Studenter per gruppe", "Per gruppe") + `<span class="op">+</span>`
      + field("spare", 0, 99, "Ekstra sett", "Ekstra sett") + `<span class="op">=</span>`
      + `<b class="sets-sum" data-id="${id}">${setsText(p)}</b>`
      + `<button type="button" class="mini" data-act="setsdone" data-id="${id}">Ferdig</button>`
      + `<span class="muted">Lagres med en gang.</span></div></td></tr>`;
  }

  function drawTable() {
    if (mode === "loading") { $("tbody").innerHTML = `<tr><td colspan="${COLS}" class="blank">Henter prosjekter …</td></tr>`; return; }
    const list = sorted();
    if (!list.length) { $("tbody").innerHTML = `<tr><td colspan="${COLS}" class="blank">Ingen prosjekter dette skoleåret. Trykk «Legg til nytt prosjekt» for å legge inn det første.</td></tr>`; return; }
    $("tbody").innerHTML = list.map((p) => {
      // own projects can be changed; lecturers only weeks and students/groups, lead time and buffer are the engineer's
      const can = (k) => p.own && (ENG || ["start", "finish", "students", "group_size", "spare"].includes(k));
      const num = (k, min, max, lbl) => can(k)
        ? `<input type="number" id="in-${esc(p.id)}-${k}" data-id="${esc(p.id)}" data-k="${k}" min="${min}" max="${max}" value="${p[k] ?? ""}" aria-label="${lbl}">`
        : `<span class="ro">${p[k] ?? "–"}</span>`;
      // The BOM computes «Å bestille» from its own set count (B10) – it should equal the app's.
      // Only for the run B10 follows: the next one that hasn't started and hasn't been counted.
      const b = p.bomStatus, deviation = p.bomSetsActive && b && b.exists && !b.error && p.sets != null && (b.setsInFile ?? null) !== p.sets;
      if (!deviation) bomReason.delete(p.id);
      const warn = (deviation
        ? `<div class="bom-mismatch" title="Excel regner «Å bestille» fra antallet i BOM-en. Årshjulets tall brukes her.">`
          + `<b>BOM-en har ${b.setsInFile ?? "ikke noe"} sett</b>${bomReason.has(p.id) ? `<span>${esc(bomReason.get(p.id))}</span>` : ""}`
          + (p.own ? ` <button type="button" class="mini" data-act="bomsets" data-id="${esc(p.id)}">Skriv ${p.sets} til BOM-en</button>` : "") + `</div>` : "")
        + (p.courseStudents != null && p.students !== p.courseStudents
          ? `<div class="muted" style="font-size:11.5px" title="Dette prosjektet avviker fra antallet i emnet">Emnet: ${p.courseStudents} studenter</div>` : "");
      // just the number of sets; the calculation opens on its own line underneath (setsRow)
      const sets = `<div class="sets-n"><b>${p.sets ?? "–"}</b> sett</div>`
        + (can("students")
          ? `<button type="button" class="mini quiet sets-formula" data-act="setsedit" data-id="${esc(p.id)}" aria-expanded="${setsEditing.has(p.id)}" title="Studenter ÷ per gruppe + ekstra sett. Trykk for å endre.">${setsFormula(p)}</button>`
          : `<div class="muted sets-formula" title="Studenter ÷ per gruppe + ekstra sett">${setsFormula(p)}</div>`);
      const owner = courseLine(p);
      const remove = !p.own ? "" : ENG ? `<button type="button" class="del" data-id="${esc(p.id)}">Slett</button>`
        : `<button type="button" class="del" data-id="${esc(p.id)}" title="Ingeniøren får beskjed og kan gjenopprette">Fjern</button>`;
      // another engineer's project (the whole department): view only
      const others = ENG && !p.own ? `<span class="muted">${esc(p.engineerName || "")}</span>` : "";
      return `<tr data-id="${esc(p.id)}">
        <td class="name">${nameCell(p)}${owner}</td>
        <td>${p.own ? typeSelect(p) : esc(TYPES[p.type] || p.type)}<div class="muted" style="font-size:12px">${esc(p.semester)} ${p.deadlines.start.year}</div></td>
        <td><div class="pair">${num("start", 1, 53, "I bruk fra uke")}<span class="op">–</span>${num("finish", 1, 53, "Til uke")}</div></td>
        <td><div class="pair">${num("lead", 0, 20, "Leveringstid i uker")}<span class="op">+</span>${num("buf", 0, 20, "Buffer i uker")}</div></td>
        <td class="sets-cell">${sets}${warn}</td>
        <td class="deadline-cell">${deadlineHtml(p)}</td>
        <td class="ordercell eng">${ENG && p.own ? orderCell(p) : others}</td>
        <td class="eng">${ENG && p.own ? bomCell(p) : ""}</td>
        <td>${remove}</td></tr>`
        + (can("students") && setsEditing.has(p.id) ? setsRow(p) : "")
        + (ENG && p.own && openOrders.has(p.id) && (p.orders || p.count) ? orderDetail(p) : "");
    }).join("");
  }

  // ---------- endringer i BOM-filer ----------
  const fmtNum = (x) => (typeof x === "number" ? nf2.format(x).replace(/,00$/, "") : (String(x ?? "") || "tom"));
  function drawDiffs() {
    // the same BOM can belong to several school years – show it once
    const seen = new Set();
    const list = projects.filter((p) => p.bomStatus && p.bomStatus.changes && p.bomStatus.changes.length && !seen.has(p.bom) && seen.add(p.bom));
    const more = inSY().filter((p) => orderState(p) === "extra").map((p) => {
      const levs = p.orders.groups.filter((g) => g.extra.length).map((g) => esc(g.name)).join(", ");
      return `<div class="diffbox soon"><div class="row"><h3>${esc(p.name)}: trenger mer enn det som er bestilt hos ${levs}</h3>`
        + `<button type="button" class="mini" data-act="show" data-id="${esc(p.id)}">Vis bestillingen</button></div>`
        + `<p class="muted">BOM-en eller antall sett er endret etter at du bestilte.</p></div>`;
    }).join("");
    $("diffs").innerHTML = more + list.map((p) => {
      const b = p.bomStatus;
      const items = b.changes.map((e) => e.type === "new"
        ? `<li class="add"><span class="sym">+</span><span><b>${esc(e.description)}</b>${e.part_number ? " · " + esc(e.part_number) : ""} <span class="from">(ny linje, ${fmtNum(e.perSet)} per sett)</span></span></li>`
        : e.type === "removed"
        ? `<li class="del"><span class="sym">−</span><span><b>${esc(e.description)}</b>${e.part_number ? " · " + esc(e.part_number) : ""} <span class="from">(fjernet)</span></span></li>`
        : `<li class="chg"><span class="sym">~</span><span><b>${esc(e.description)}</b>: ${esc(e.field)} <span class="from">${esc(fmtNum(e.from))}</span> → ${esc(fmtNum(e.to))}</span></li>`).join("");
      return `<div class="diffbox"><div class="row"><h3>${esc(p.name)}: BOM-en er endret siden ${esc(b.lastRead)}</h3>`
        + `<button type="button" class="mini" data-act="read" data-id="${esc(p.id)}">OK, sett</button></div><ul>${items}</ul></div>`;
    }).join("");
  }

  // ---------- next school year: warning and copying ----------
  const syName = (year) => { const s = SYS.find((x) => x.year === year); return s ? s.name : `${year}/${String((year + 1) % 100).padStart(2, "0")}`; };
  // ---------- lecturers' changes (for the engineer only) ----------
  const timeFmt = (s) => { const d = new Date(s); return isNaN(d) ? esc(s) : d.toLocaleString("nb-NO", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); };
  const valueText = (fields, v) => (v === null || v === undefined || v === "" ? "tom" : fields === "type" ? (TYPES[v] || v) : String(v));
  const ACTIONS = { created: "La til prosjektet", restored: "Gjenopprettet", removed: "Fjernet prosjektet" };
  const RECIPIENT_TEXT = {
    engineer_assigned: (e) => `<b>${esc(e.user_name)} valgte deg som ansvarlig ingeniør for ${esc(e.project_name)}.</b> Prosjektene i emnet vises nå hos deg, og du får varslene om dem.`,
    engineer_removed: (e) => `<b>${esc(e.user_name)} tok deg av ${esc(e.project_name)}</b>${e.to_value ? ` – ${esc(e.to_value)} har det nå` : ""}. Prosjektene i emnet vises ikke lenger hos deg.`,
    course_approved: (e) => `<b>Emnet ${esc(e.project_name)} er godkjent</b> av ${esc(e.user_name)}.`,
    course_rejected: (e) => `<b>${esc(e.user_name)} avviste emnet ${esc(e.project_name)}</b>, som du foreslo.`
      + (e.to_value ? ` ${esc(e.to_value)} ${e.to_value === "1" ? "prosjekt står" : "prosjekter står"} nå uten emne – snakk med administratoren.` : ""),
  };
  function changeNotices() {
    let h = "";
    // notifications to you: a course you proposed was approved/rejected, you were chosen as (or taken off as) engineer
    // the admin approves proposed courses right here: on the notification about the course, or on a notice of its own
    const waiting = ME.admin ? COURSES.filter((c) => !c.approved) : [];
    const shown = new Set();
    CHANGES.filter((e) => e.recipient_id).forEach((e) => {
      const text = (RECIPIENT_TEXT[e.action] || ((x) => esc(x.action)))(e);
      const c = e.action === "engineer_assigned" && waiting.find((x) => x.id === e.course_id);
      if (c) shown.add(c.id);
      h += `<div class="notice info"><div>${text}${c ? " Emnet venter på godkjenning." : ""} <span class="muted">· ${timeFmt(e.time)}</span></div>`
        + `<span class="noticeact"><button type="button" class="mini" data-act="ackid" data-id="${e.id}">OK, sett</button>`
        + (c ? `<button type="button" class="mini" data-act="ackapprove" data-id="${e.id}" data-course="${c.id}">Sett og godkjent</button>` : "")
        + `</span></div>`;
    });
    waiting.filter((c) => !shown.has(c.id)).forEach((c) => {
      h += `<div class="notice info"><div><b>${esc(c.proposedByName || "Noen")} foreslo emnet ${esc(c.code)}</b>${c.name ? ` (${esc(c.name)})` : ""}. `
        + `Det venter på godkjenning.</div><span class="noticeact">`
        + `<button type="button" class="mini" data-act="approve" data-course="${c.id}">Godkjenn</button>`
        + `<a class="mini quiet" href="/admin#courses">Rett eller avvis i adminpanelet</a></span></div>`;
    });
    if (!ENG) return h;
    DELETED.forEach((s) => {
      h += `<div class="notice warn"><div><b>${esc(s.deletedBy || "En faglærer")} fjernet «${esc(s.name)}» (${esc(syName(s.school_year))})</b> ${timeFmt(s.deleted)}. `
        + `Det vises ikke i hjulet før du gjenoppretter det.</div><span class="noticeact">`
        + `<button type="button" class="mini" data-act="restore" data-id="${esc(s.id)}">Gjenopprett</button>`
        + `<button type="button" class="mini quiet" data-act="deleteconfirm" data-id="${esc(s.id)}">Bekreft sletting …</button></span></div>`;
    });
    const fjernet = new Set(DELETED.map((s) => String(s.id)));
    const per = new Map();
    CHANGES.filter((e) => !e.recipient_id).forEach((e) => {
      const gid = String(e.run_id);
      if (fjernet.has(gid)) return; // shown as "removed" above
      if (!per.has(gid)) per.set(gid, []);
      per.get(gid).push(e);
    });
    per.forEach((list, gid) => {
      const p = projects.find((x) => x.id === gid);
      const who = [...new Set(list.map((e) => e.user_name))].join(", ");
      const items = list.map((e) => `<li>${e.action === "changed"
        ? `${esc(e.field)}: <span class="from">${esc(valueText(e.field, e.from_value))}</span> → <b>${esc(valueText(e.field, e.to_value))}</b>`
        : esc(ACTIONS[e.action] || e.action)} <span class="muted">· ${timeFmt(e.time)}</span></li>`).join("");
      let consequence = "", strong = false;
      if (p && TODAY) {
        const os = orderState(p), u = weeksBetween(TODAY.date, p.deadlines.deadline.date);
        consequence = `Bestillingsfrist nå: uke ${p.deadlines.deadline.week}, ${dfmtY.format(toD(p.deadlines.deadline.date))}`;
        if (os === "late") { consequence += " – passert, og ikke bestilt"; strong = true; }
        else if (os === "soon") { consequence += u === 0 ? " – denne uken" : ` – om ${u} ${u === 1 ? "uke" : "uker"}`; strong = true; }
        else if (os === "extra") { consequence += " – du må bestille mer"; strong = true; }
        else if (os === "ok") consequence += " – alt er bestilt";
      } else if (!p) consequence = "Prosjektet er slettet.";
      h += `<div class="notice ${strong ? "warn" : "info"}"><div><b>${esc(who)} endret «${esc(p ? p.name : list[0].project_name)}» (${esc(syName(list[0].school_year))})</b>`
        + `<ul class="changed">${items}</ul>${consequence ? `<p>${consequence}</p>` : ""}</div>`
        + `<button type="button" class="mini" data-act="acknowledge" data-id="${esc(gid)}">OK, sett</button></div>`;
    });
    return h;
  }

  function drawNotices() {
    const o = SETUP, chg = mode === "loading" ? "" : changeNotices();
    if (!o || mode === "loading") { $("notices").innerHTML = chg; return; }
    const when = o.weeks < 0 ? "fristen er allerede passert" : o.weeks === 0 ? "denne uken" : `om ${o.weeks} ${o.weeks === 1 ? "uke" : "uker"}`;
    $("notices").innerHTML = chg + `<div class="notice ${o.weeks <= 4 ? "warn" : "soon"}"><div>`
      + `<b>Skoleåret ${esc(o.name)} er ikke satt opp.</b> ${o.missing} ${o.missing === 1 ? "prosjekt" : "prosjekter"} fra ${esc(o.fromName)} mangler. `
      + `Første bestillingsfrist blir uke ${o.deadline.week} for ${esc(o.project)} – ${when}.</div>`
      + `<button type="button" class="mini" id="setupGo">Sett opp ${esc(o.name)} …</button></div>`;
  }
  $("notices").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act]");
    if (b) {
      const id = b.dataset.id, a = b.dataset.act;
      if (a === "acknowledge") act(() => api("POST", `/api/changes/${encodeURIComponent(id)}/acknowledge`), null, b);
      if (a === "ackid") act(() => api("POST", "/api/changes/acknowledge", { ids: [+id] }), null, b);
      const approve = () => api("POST", `/api/admin/courses/${b.dataset.course}/approve`);
      if (a === "approve") act(approve, (r) => `${r.code} er godkjent og kan brukes av alle.`, b);
      if (a === "ackapprove") act(async () => { const r = await approve(); await api("POST", "/api/changes/acknowledge", { ids: [+id] }); return r; },
        (r) => `${r.code} er godkjent og kan brukes av alle.`, b);
      if (a === "restore") act(() => api("POST", P(id) + "/restore"), "Prosjektet er gjenopprettet.", b);
      if (a === "deleteconfirm") {
        if (!b.classList.contains("confirm")) { b.classList.add("confirm"); b.textContent = "Bekreft: slett for godt"; return; }
        act(() => api("DELETE", P(id)), "Prosjektet er slettet.", b);
      }
      return;
    }
    if (!e.target.closest("#setupGo") || !SETUP) return;
    goSY(SETUP.from); openCopy(true);
    $("copybox").scrollIntoView({ block: "center", behavior: "smooth" });
  });

  function drawCopyButton() {
    const has = inSY().some((p) => p.own);
    $("copyOpen").hidden = !has || mode === "loading";
    $("copyOpen").textContent = `Kopier til ${syName(SY + 1)} …`;
    if (!has) openCopy(false);
  }
  // ---------- sharing the wheel with students (only on /hjul) ----------
  function showShare(url) {
    $("shareUrl").value = url || "";
    $("shareMake").hidden = !!url;
    ["shareCopy", "shareOpen", "shareNew", "shareStop"].forEach((k) => { $(k).hidden = !url; });
    if (url) $("shareOpen").href = url;
  }
  if (VIEW === "wheel" && !SHARE && $("sharebox")) {
    const share = (body) => api(body ? "POST" : "GET", "/api/share", body).then((r) => { $("shareErr").textContent = ""; showShare(r.url); return r; })
      .catch((e) => { $("shareErr").textContent = e.message; });
    share();
    $("shareMake").addEventListener("click", () => share({}));
    $("shareCopy").addEventListener("click", async () => {
      const i = $("shareUrl"); i.select();
      try { await navigator.clipboard.writeText(i.value); $("shareErr").textContent = "Lenken er kopiert."; }
      catch (e) { $("shareErr").textContent = "Lenken er merket – trykk Ctrl+C for å kopiere den."; }
    });
    // dangerous buttons: the first press asks, the second press does it
    [["shareNew", { new: true }, "Bekreft: lag ny lenke"], ["shareStop", { stop: true }, "Bekreft: slutt å dele"]].forEach(([id, body, ask]) => {
      $(id).addEventListener("click", (e) => {
        const b = e.currentTarget;
        if (!b.classList.contains("confirm")) { b.dataset.text = b.textContent; b.classList.add("confirm"); b.textContent = ask; return; }
        b.classList.remove("confirm"); b.textContent = b.dataset.text;
        share(body);
      });
    });
  }

  // ---------- calendar feed: your private link for Outlook ----------
  async function openCalendar(open, fresh) {
    $("calbox").hidden = !open; $("calToggle").setAttribute("aria-expanded", String(open)); $("calErr").textContent = "";
    if (!open) return;
    try {
      const r = await api("POST", "/api/calendar", fresh ? { new: true } : undefined);
      $("calUrl").value = r.url; $("calDownload").href = r.url + "?download=1"; $("calLocal").hidden = !r.local;
      if (fresh) setStatus("Ny kalenderlenke laget. Den gamle virker ikke lenger – abonner på nytt i Outlook.");
    } catch (e) { $("calErr").textContent = e.message; }
  }
  $("calToggle").addEventListener("click", () => openCalendar($("calbox").hidden));
  $("calCopy").addEventListener("click", async () => {
    const i = $("calUrl"); i.select();
    try { await navigator.clipboard.writeText(i.value); setStatus("Kalenderlenken er kopiert."); }
    catch (e) { setStatus("Lenken er merket – trykk Ctrl+C for å kopiere den."); }
  });
  $("calNew").addEventListener("click", (e) => {
    const b = e.currentTarget;
    if (!b.classList.contains("confirm")) { b.classList.add("confirm"); b.textContent = "Bekreft: lag ny lenke"; return; }
    b.classList.remove("confirm"); b.textContent = "Lag ny lenke …";
    openCalendar(true, true);
  });

  function openCopy(open) {
    $("copybox").hidden = !open; $("copyOpen").setAttribute("aria-expanded", String(open)); $("copyErr").textContent = "";
    if (!open) return;
    const next = new Set(ALL.filter((p) => p.school_year === SY + 1).map((p) => p.projectId));
    $("copyTitle").textContent = `Kopier fra ${syName(SY)} til ${syName(SY + 1)}`;
    // one checklist row per project: weeks and students can be corrected before copying, and you see whether the BOM has been counted
    $("copyList").innerHTML = sorted().filter((p) => p.own).map((p) => { // only your own courses are copied
      const existing = next.has(p.projectId);
      const head = `<label class="copy-pick"><input type="checkbox" value="${esc(p.id)}"${existing ? " disabled" : " checked"}>`
        + `<span class="dot" style="background:${col(p)}"></span><b>${esc(p.name)}</b> <span class="muted">${esc(TYPES[p.type] || p.type)}</span></label>`;
      if (existing) return `<div class="copyrow done">${head}<span class="muted">finnes allerede i ${esc(syName(SY + 1))}</span></div>`;
      const field = (k, v, min, max, lbl) => `<input type="number" data-c="${k}" min="${min}" max="${max}" value="${v ?? ""}" aria-label="${lbl}">`;
      const course = p.courseStudents != null && p.courseStudents !== p.students
        ? `<button type="button" class="mini quiet" data-act="coursestudents" data-n="${p.courseStudents}" title="Antallet i emnet ${esc(p.courseCode || "")}">Bruk emnets ${p.courseStudents}</button>` : "";
      const [kl, bomText] = copyBom(p);
      return `<div class="copyrow" data-id="${esc(p.id)}">${head}`
        + `<div class="copy-fields"><span>uke ${field("start", p.start, 1, 53, "Fra uke")}–${field("finish", p.finish, 1, 53, "Til uke")} <small class="copy-date"></small></span>`
        + `<span>${field("students", p.students, 0, 2000, "Antall studenter")} studenter ÷ ${p.group_size} + ${p.spare} = <b class="copy-sets"></b> ${course}</span></div>`
        + `<div class="copybom ${kl}">${bomText}</div></div>`;
    }).join("");
    $("copyList").querySelectorAll(".copyrow[data-id]").forEach(copyUpdate);
  }
  // has the BOM been counted after last year? Otherwise «På lager» is wrong, and next year's order will be wrong
  function copyBom(p) {
    if (!p.bom) return ["muted", "Ingen BOM koblet til"];
    const L = p.count && p.count.saved;
    if (L && L.written) return ["ok", `Telt og skrevet til BOM-en ${dfmtY.format(toD(String(L.written).slice(0, 10)))}`];
    if (L) return ["warn", "Telt, men ikke skrevet til BOM-en – «På lager» er ikke oppdatert"];
    if (TODAY && p.deadlines.returns.date <= TODAY.date) return ["warn", "Ikke telt – «På lager» i BOM-en kan være feil"];
    return ["muted", "Ikke ferdig ennå – tell når prosjektet er over"];
  }
  // the date of the start week next year, and the set count as it will be
  function copyUpdate(row) {
    const p = projects.find((x) => x.id === row.dataset.id); if (!p) return;
    const v = (k) => row.querySelector(`[data-c=${k}]`).value;
    const week = parseInt(v("start"), 10), fresh = SYS.find((s) => s.year === SY + 1);
    const u = fresh && fresh.weeks.find((x) => x.week === week);
    row.querySelector(".copy-date").textContent = u ? `fra ${dfmtY.format(toD(u.date))}` : "";
    const n = setCount(v("students") === "" ? null : parseInt(v("students"), 10) || 0, p.group_size, p.spare);
    row.querySelector(".copy-sets").textContent = n != null ? `${n} sett` : "–";
  }
  $("copyList").addEventListener("input", (e) => { const row = e.target.closest(".copyrow[data-id]"); if (row) copyUpdate(row); });
  $("copyList").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act=coursestudents]"); if (!b) return;
    const row = b.closest(".copyrow"); row.querySelector("[data-c=students]").value = b.dataset.n; copyUpdate(row);
  });
  $("copyOpen").addEventListener("click", () => openCopy($("copybox").hidden));
  $("copyCancel").addEventListener("click", () => openCopy(false));
  $("copyGo").addEventListener("click", async () => {
    const rows = [...$("copyList").querySelectorAll(".copyrow[data-id]")].filter((r) => r.querySelector("input[type=checkbox]").checked);
    if (!rows.length) { $("copyErr").textContent = "Velg minst ett prosjekt."; return; }
    // only what you've changed is sent; the rest is copied as this year
    const changes = {};
    rows.forEach((r) => {
      const p = projects.find((x) => x.id === r.dataset.id), e = {};
      r.querySelectorAll("[data-c]").forEach((i) => {
        const v = i.value === "" ? null : parseInt(i.value, 10);
        if (v !== (p[i.dataset.c] ?? null)) e[i.dataset.c] = v;
      });
      if (Object.keys(e).length) changes[p.id] = e;
    });
    $("copyGo").disabled = true;
    try {
      const r = await api("POST", `/api/school-years/${SY}/copy`, { ids: rows.map((x) => x.dataset.id), changes });
      openCopy(false); await load(); goSY(r.to);
      setStatus(`Kopierte ${r.created.length} ${r.created.length === 1 ? "prosjekt" : "prosjekter"} til ${r.name}.`);
    } catch (err) { $("copyErr").textContent = err.message; }
    finally { $("copyGo").disabled = false; }
  });

  // ---------- history: what each project has cost per school year ----------
  function drawHistory() {
    const runs = projects.filter((p) => p.orderedCost != null || (p.count && p.count.saved && p.count.saved.lossCost));
    $("historySec").hidden = !runs.length;
    if (!runs.length) return;
    const years = [...new Set(runs.map((p) => p.school_year))].sort((a, b) => a - b);
    const rows = new Map();
    runs.forEach((p) => {
      const r = rows.get(p.projectId) || { name: p.name, p, cells: {} };
      r.cells[p.school_year] = p; rows.set(p.projectId, r);
    });
    const cell = (p) => {
      if (!p) return `<td class="num muted">—</td>`;
      const sets = p.sets || (p.bomStatus && p.bomStatus.setsInFile);
      const L = p.count && p.count.saved;
      return `<td class="num">${nf0.format(p.orderedCost)} kr${sets ? `<small>${nf0.format(p.orderedCost / sets)} kr/sett · ${sets} sett</small>` : ""}`
        + (L && L.lossCost ? `<small class="loss">svinn ${nf0.format(L.lossCost)} kr</small>` : "") + `</td>`;
    };
    const sum = (y) => runs.filter((p) => p.school_year === y).reduce((s, p) => s + p.orderedCost, 0);
    $("histTable").innerHTML = `<thead><tr><th>Prosjekt</th>${years.map((y) => `<th class="num">${esc(syName(y))}</th>`).join("")}</tr></thead><tbody>`
      + [...rows.values()].sort((a, b) => a.name.localeCompare(b.name, "nb")).map((r) =>
        `<tr><td class="name"><b><span class="dot" style="background:${col(r.p)}"></span>${esc(r.name)}</b></td>${years.map((y) => cell(r.cells[y])).join("")}</tr>`).join("")
      + `</tbody><tfoot><tr><td><b>Sum</b></td>${years.map((y) => `<td class="num"><b>${nf0.format(sum(y))} kr</b></td>`).join("")}</tr></tfoot>`;
  }

  // ---------- courses (engineers) ----------
  let newCourse = false;
  function drawCourses() {
    if (mode === "loading" || $("courseTable").contains(document.activeElement)) return;
    const pick = (name, liste, chosen, lbl) => `<select data-cf="${name}" aria-label="${lbl}"><option value="">–</option>`
      + liste.map((u) => `<option value="${u.id}"${u.id === chosen ? " selected" : ""}>${esc(u.name)}</option>`).join("") + `</select>`;
    const program = (chosen) => `<select data-cf="program" aria-label="Studieprogram">`
      + Object.entries(PROGRAM).map(([k, v]) => `<option value="${k}"${k === chosen ? " selected" : ""}>${esc(v)}</option>`).join("") + `</select>`;
    const row = (e) => `<tr data-course="${e ? e.id : "new"}"${e && !e.active ? ' class="inaktiv"' : ""}>`
      + `<td><input type="text" data-cf="code" value="${esc(e ? e.code : "")}" placeholder="IELS1001" aria-label="Emnekode" maxlength="20"></td>`
      + `<td><input type="text" data-cf="name" value="${esc(e ? e.name : "")}" placeholder="Emnenavn" aria-label="Emnenavn"></td>`
      + `<td>${program(e ? e.program : "bachelor")}</td>`
      + `<td><input type="number" data-cf="students" min="0" max="2000" value="${e && e.students != null ? e.students : ""}" placeholder="F.eks. 60" aria-label="Antall studenter i emnet"></td>`
      // the lecturer is always yourself on your own courses; engineers choose the lecturer
      + `<td>${ENG ? pick("lecturer", LECTURERS, e && e.lecturerId, "Faglærer") : "deg"}</td>`
      + `<td>${pick("engineer", ENGINEERS, e ? e.engineerId : (ENG ? ME.id : null), "Ansvarlig ingeniør")}</td>`
      + (e && !e.approved ? `<td class="num">${e.projects}</td><td><span class="tag soon" title="Foreslått av ${esc(e.proposedByName || "")}. Administratoren godkjenner nye emner; til da kan bare de som står på emnet bruke det.">venter på godkjenning</span></td>`
         : e ? `<td class="num">${e.projects}</td><td><label class="active"><input type="checkbox" data-cf="active"${e.active ? " checked" : ""}> aktiv</label></td>`
           : `<td></td><td><button type="button" class="mini" data-act="coursesave">${ME.admin ? "Lagre" : "Send forslag"}</button> <button type="button" class="mini quiet" data-act="coursecancel">Avbryt</button></td>`)
      + `</tr>`;
    $("courseTable").innerHTML = `<thead><tr><th>Kode</th><th>Navn</th><th>Program</th><th title="Standard for nye prosjekter i emnet">Studenter</th><th>Faglærer</th><th>Ansvarlig ingeniør</th><th class="num">Prosjekter</th><th></th></tr></thead><tbody>`
      + (newCourse ? row(null) : "") + COURSES.map(row).join("")
      + (!COURSES.length && !newCourse ? `<tr><td colspan="8" class="blank">Ingen emner ennå. Trykk «${newCourseText()}»${ENG ? "" : " for å foreslå ditt første"}.</td></tr>` : "") + `</tbody>`;
    $("courseNew").hidden = newCourse;
    $("courseNew").textContent = newCourseText();
  }
  // only the admin adds courses directly – everyone else proposes one
  const newCourseText = () => (ME.admin ? "+ Nytt emne" : "+ Foreslå emne");
  const courseValues = (tr) => {
    const v = {};
    tr.querySelectorAll("[data-cf]").forEach((el) => { v[el.dataset.cf] = el.type === "checkbox" ? el.checked : (el.value || null); });
    return v;
  };
  $("courseNew").addEventListener("click", () => { newCourse = true; drawCourses(); const k = $("courseTable").querySelector('tr[data-course="new"] [data-cf=code]'); if (k) k.focus(); });
  $("courseTable").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act]"); if (!b) return;
    if (b.dataset.act === "coursecancel") { newCourse = false; b.blur(); drawCourses(); return; }
    const v = courseValues(b.closest("tr"));
    act(async () => { const r = await api("POST", "/api/courses", v); newCourse = false; b.blur(); return r; },
      (r) => r.approved ? `Emnet ${r.code} er lagt til. Velg det under navnet på prosjektene som hører til.`
        : `Forslaget ${r.code} er sendt til administratoren. Du kan bruke emnet på dine prosjekter med en gang.`, $("courseTable"));
  });
  $("courseTable").addEventListener("change", (e) => {
    const tr = e.target.closest("tr[data-course]"); if (!tr || tr.dataset.course === "new") return;
    const fields = e.target.dataset.cf, v = courseValues(tr);
    e.target.blur(); // so the table can be redrawn after saving
    act(() => api("PATCH", `/api/courses/${tr.dataset.course}`, { [fields]: v[fields] }), "Emnet er lagret.", $("courseTable"));
  });

  function renderAll(keepTable) {
    if (VIEW === "wheel") { drawWheel(); drawWheelList(); return; }
    if (VIEW === "weeks") { drawWeeks(); return; }
    drawCourses();
    drawWheel(); drawWeeks(); drawStats(); drawDiffs(); drawNotices(); drawCopyButton(); drawHistory();
    if (!keepTable) drawTable();
    else inSY().forEach((p) => { const cell = document.querySelector(`tr[data-id="${CSS.escape(p.id)}"] .deadline-cell`); if (cell) cell.innerHTML = deadlineHtml(p);
      const oc = document.querySelector(`tr[data-id="${CSS.escape(p.id)}"] .ordercell`); if (oc) oc.innerHTML = orderCell(p);
      const pill = p.bomStatus && p.bomStatus.lines != null && document.querySelector(`tr[data-id="${CSS.escape(p.id)}"] .pill.ok`);
      if (pill) pill.textContent = bomSum(p.bomStatus); });
  }

  // ---------- school year ----------
  function goSY(year) { if (SYS.some((s) => s.year === year)) { SY = year; highlight(null); renderAll(false); } }
  $("syPrev").addEventListener("click", () => goSY(SY - 1));
  $("syNext").addEventListener("click", () => goSY(SY + 1));
  $("syNow").addEventListener("click", () => { const current = SYS.find((s) => TODAY && s.weeks.some((u) => u.date === TODAY.date)); if (current) goSY(current.year); });

  // ---------- redigering ----------
  const LIMITS = { start: [1, 53], finish: [1, 53], students: [0, 2000], group_size: [1, 20], spare: [0, 99] };  // ellers 0–20
  // the same rule as db.set_count: students / group size, rounded up, + spare
  const setCount = (students, group, spare) =>
    students == null ? null : Math.ceil(students / Math.max(group || 1, 1)) + (spare || 0);
  $("tbody").addEventListener("input", (e) => {
    const t = e.target;
    if (t.matches("input[data-role=counted]")) { countEdit(t); return; }
    if (t.matches("input[data-name]")) { const u = nameEditing.get(t.closest(".name-edit").dataset.id); if (u) u[t.dataset.name] = t.value; return; }
    if (!t.matches("input[type=number][data-k]")) return;
    const p = projects.find((x) => x.id === t.dataset.id); if (!p) return;
    const k = t.dataset.k;
    if (t.value === "" && k === "students") p.students = null;
    else {
      const v = parseInt(t.value, 10); if (!Number.isFinite(v)) return;
      const [lo, hi] = LIMITS[k] || [0, 20];
      p[k] = Math.min(hi, Math.max(lo, v));
    }
    if (["students", "group_size", "spare"].includes(k)) {
      // show the new number straight away; the server computes the same and is the authority
      p.sets = setCount(p.students, p.group_size, p.spare);
      const sum = document.querySelector(`.sets-sum[data-id="${CSS.escape(p.id)}"]`);
      if (sum) sum.textContent = setsText(p);
      const row = document.querySelector(`tr[data-id="${CSS.escape(p.id)}"]`);
      if (row) {
        const n = row.querySelector(".sets-n b"); if (n) n.textContent = p.sets ?? "–";
        const f = row.querySelector(".sets-formula"); if (f) f.textContent = setsFormula(p);
      }
    }
    queueUpdate(p.id, { [k]: p[k] });
  });

  // anchor: the element you clicked. An error is then also shown right next to it, not just at the top –
  // in the middle of a long list you can't see the status line, but you can see the button you pressed.
  async function act(fn, okMsg, anchor) {
    try { const r = await fn(); if (okMsg) setStatus(typeof okMsg === "function" ? okMsg(r) : okMsg); await load(); }
    catch (e) { setStatus(e.message, true); if (anchor) errorAt(anchor, e.message); }
  }
  function errorAt(el, msg) {
    const row = el.closest(".supplier-act, .receipt, .bomcell, .ordercell") || el.parentElement;
    if (!row || !row.isConnected) return;
    let p = row.nextElementSibling;
    if (!p || !p.classList.contains("inlineerr")) {
      p = document.createElement("p");
      p.className = "inlineerr"; p.setAttribute("role", "alert");
      row.after(p);
    }
    p.textContent = msg;
  }
  const P = (id) => "/api/runs/" + encodeURIComponent(id);

  const orderApi = (id, body) => api("POST", P(id) + "/order", body);
  $("tbody").addEventListener("toggle", (e) => {
    const d = e.target; if (d.matches && d.matches("details.tellbox")) countOpen.set(d.dataset.id, d.open);
  }, true); // «toggle» doesn't bubble
  const actAt = act;
  $("tbody").addEventListener("change", (e) => {
    const t = e.target;
    const act = (fn, msg) => actAt(fn, msg, t); // the error is shown next to the field you changed
    if (t.matches("select[data-role=ctype]")) { countEdit(t); return; }
    if (t.matches("select[data-act=course]")) {
      const e = COURSES.find((x) => String(x.id) === t.value);
      act(() => api("PATCH", P(t.dataset.id), { course: t.value || null }),
        !e ? "Prosjektet har ikke noe emne." : `Prosjektet hører nå til ${e.code}`
          + (e.engineerName ? ` (ingeniør: ${e.engineerName}${ME.id && e.engineerId !== ME.id ? " – du ser det ikke lenger under «Mine emner»" : ""})` : "") + ".");
      return;
    }
    if (t.matches("select[data-act=type]")) {
      act(() => api("PATCH", P(t.dataset.id), { type: t.value }), `Typen er endret til ${TYPES[t.value] || t.value}.`);
      return;
    }
    if (t.matches("select[data-act=ostatus]")) {
      act(() => orderApi(t.dataset.id, { action: "status", supplier: t.dataset.supplier, status: t.value }), "Status lagret.");
      return;
    }
    if (t.matches("input[data-act=order_no]")) {
      act(() => orderApi(t.dataset.id, { action: "order_no", supplier: t.dataset.supplier, order_no: t.value }), "Ordrenummer lagret.");
      return;
    }
    const s = t.closest("select[data-act=link]"); if (!s || !s.value) return;
    act(() => api("PATCH", P(s.dataset.id), { bom: s.value }), "BOM koblet: " + s.value);
  });
  // Enter saves, Escape cancels while you're editing name and note
  $("tbody").addEventListener("keydown", (e) => {
    if (e.target.closest(".sets-edit") && ["Enter", "Escape"].includes(e.key)) {
      e.preventDefault(); e.target.closest(".sets-edit").querySelector("[data-act=setsdone]").click(); return;
    }
    if (!e.target.matches("input[data-name]") || !["Enter", "Escape"].includes(e.key)) return;
    e.preventDefault();
    const b = e.target.closest(".name-edit").querySelector(`[data-act=${e.key === "Enter" ? "namesave" : "namecancel"}]`);
    if (b) b.click();
  });
  $("tbody").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-act]");
    if (b) {
      const act = (fn, msg) => actAt(fn, msg, b); // the error is shown next to the button you pressed
      const id = b.dataset.id, a = b.dataset.act, supplier = b.dataset.supplier;
      const order_no = () => { const i = b.closest(".supplier-act") && b.closest(".supplier-act").querySelector("[data-role=order_no]"); return i ? i.value : ""; };
      if (a === "show") { openOrders.has(id) ? openOrders.delete(id) : openOrders.add(id); drawTable(); return; }
      if (a === "nameedit") {
        const p = projects.find((x) => x.id === id); if (!p) return;
        nameEditing.set(id, { name: p.name, note: p.note || "" }); drawTable();
        const i = document.querySelector(`.name-edit[data-id="${CSS.escape(id)}"] input`); if (i) i.focus();
        return;
      }
      if (a === "namecancel") { nameEditing.delete(id); drawTable(); return; }
      if (a === "setsedit" || a === "setsdone") {
        if (a === "setsdone" || setsEditing.has(id)) setsEditing.delete(id); else setsEditing.add(id);
        drawTable();
        const i = setsEditing.has(id) && document.getElementById(`in-${id}-students`); if (i) i.focus();
        return;
      }
      if (a === "namesave") {
        const u = nameEditing.get(id); if (!u) return;
        if (!u.name.trim()) { errorAt(b, "Gi prosjektet et navn."); return; }
        act(async () => { const r = await api("PATCH", P(id), { name: u.name.trim(), note: u.note.trim() }); nameEditing.delete(id); return r; }, "Navn og notat er lagret.");
        return;
      }
      if (a === "withoutbom") act(() => api("POST", P(id) + "/ordered", { ordered: b.dataset.v === "1" }), b.dataset.v === "1" ? "Markert som bestilt." : "Ikke lenger markert som bestilt.");
      if (a === "bomsets") act(() => api("POST", P(id) + "/bom/sets"), (r) => r.written ? `Antall sett i BOM-en er nå ${r.file} (sikkerhetskopi i BOM/Sikkerhetskopi).` : "BOM-en hadde allerede riktig antall sett.");
      if (a === "ordered") act(() => orderApi(id, { action: "ordered", supplier: supplier, order_no: order_no() }), "Registrert som bestilt.");
      if (a === "extra") act(() => orderApi(id, { action: "extra", supplier: supplier }), "Tilleggsbestillingen er registrert.");
      if (a === "supplierlist") act(() => api("POST", P(id) + "/order-list", { supplier: supplier }), (r) => `Lagret «${r.file}» i Bestillingslister (${r.lines} linjer).`);
      if (a === "csave") act(async () => { const r = await api("POST", P(id) + "/count", { lines: countCollect(id) }); delete countDraft[id]; return r; },
        (r) => `Tellingen er lagret: ${r.deviation} avvik${r.loss ? `, svinn ${nf0.format(r.lossCost)} kr` : ""}. Oppdater «På lager» i BOM-en når du er fornøyd.`);
      if (a === "cwrite") {
        if (!b.classList.contains("confirm")) { b.classList.add("confirm"); b.textContent = "Bekreft: skriv telte tall inn i BOM-en"; return; }
        act(async () => {
          // unsaved numbers in the form are saved first, so what's written is what you see
          if (countDraft[id]) { await api("POST", P(id) + "/count", { lines: countCollect(id) }); delete countDraft[id]; }
          return api("POST", P(id) + "/count/write");
        }, (r) => writeMsg(r, "Oppdaterte «På lager» for"));
      }
      if (a === "receiptshow" || a === "receipthide") { a === "receiptshow" ? receiptOpen.add(`${id}|${supplier}`) : receiptOpen.delete(`${id}|${supplier}`); drawTable(); return; }
      if (a === "receipt") {
        if (!b.classList.contains("confirm")) { b.classList.add("confirm"); b.textContent = "Bekreft: legg til «På lager» i BOM-en"; return; }
        const lines = {};
        b.closest(".receipt-form").querySelectorAll("input[data-role=receipt]").forEach((i) => { lines[i.dataset.key] = i.value; });
        act(async () => { const r = await orderApi(id, { action: "receipt", supplier: supplier, lines }); receiptOpen.delete(`${id}|${supplier}`); return r; },
          (r) => receiptMsg(r, "La varene på lager"));
      }
      if (a === "undoreceipt") {
        if (!b.classList.contains("confirm")) { b.classList.add("confirm"); b.textContent = "Bekreft: trekk fra «På lager» igjen"; return; }
        act(() => orderApi(id, { action: "undo_receipt", supplier: supplier }), (r) => receiptMsg(r, "Trakk mottaket fra igjen"));
      }
      if (a === "cfetch") act(async () => { const r = await api("POST", P(id) + "/count/fetch"); delete countDraft[id]; return r; },
        (r) => `Tellingen bruker nå tallene fra BOM-en for ${r.fetched} ${r.fetched === 1 ? "linje" : "linjer"}${r.loss ? `. Svinn: ${nf0.format(r.lossCost)} kr` : ""}.`);
      if (a === "cundo") {
        if (!b.classList.contains("confirm")) { b.classList.add("confirm"); b.textContent = "Bekreft: sett «På lager» tilbake"; return; }
        act(() => api("POST", P(id) + "/count/undo"), (r) => writeMsg(r, "Satte «På lager» tilbake for"));
      }
      if (a === "cdelete") {
        if (!b.classList.contains("confirm")) { b.classList.add("confirm"); b.textContent = "Bekreft sletting"; return; }
        act(() => api("DELETE", P(id) + "/count"), "Tellingen er slettet.");
      }
      if (a === "undo") {
        if (!b.classList.contains("confirm")) { b.classList.add("confirm"); b.textContent = "Bekreft angre"; return; }
        act(() => orderApi(id, { action: "undo", supplier: supplier }), "Bestillingen er fjernet. Leverandøren står som ikke bestilt.");
      }
      if (a === "template") act(() => api("POST", P(id) + "/template"), (r) => LOCAL ? `Laget ${r.file} i BOM-mappen og åpnet den i Excel. Fyll den ut og lagre, så dukker den opp her.` : `Laget ${r.file} i BOM-mappen.`);
      if (a === "open") act(() => api("POST", P(id) + "/open"), "Åpnet i Excel. Lagre, og kom tilbake hit.");
      if (a === "copypath") copyPath(b.dataset.path, "Stien er kopiert. Lim den inn i Excel (Fil → Åpne) eller i Utforsker.");
      if (a === "list") act(() => api("POST", P(id) + "/order-list"), (r) => `Lagret «${r.file}» i Bestillingslister (${r.lines} linjer).`);
      if (a === "unlink") act(() => api("PATCH", P(id), { bom: null }), "Koblingen er fjernet. Filen er ikke slettet.");
      return;
    }
    const d = e.target.closest("button.del"); if (!d) return;
    if (!d.classList.contains("confirm")) {
      document.querySelectorAll("button.del.confirm").forEach((x) => { x.classList.remove("confirm"); x.textContent = ENG ? "Slett" : "Fjern"; });
      d.classList.add("confirm"); d.textContent = ENG ? "Bekreft sletting" : "Bekreft – fjern"; return;
    }
    act(() => api("DELETE", P(d.dataset.id)), ENG ? "Prosjektet er slettet fra dette skoleåret. BOM-filen ligger fortsatt i mappen."
      : "Prosjektet er fjernet. Ingeniøren får beskjed, og kan gjenopprette det.", d);
  });
  $("diffs").addEventListener("click", (e) => {
    const v = e.target.closest("button[data-act=show]");
    if (v) {
      openOrders.add(v.dataset.id); drawTable();
      const row = document.querySelector(`tr.detail[data-for="${CSS.escape(v.dataset.id)}"]`);
      if (row) row.scrollIntoView({ block: "center", behavior: "smooth" });
      return;
    }
    const b = e.target.closest("button[data-act=read]"); if (!b) return;
    act(() => api("POST", P(b.dataset.id) + "/seen"));
  });
  $("openBomDir").addEventListener("click", () => BOM_WIN ? copyPath(BOM_WIN, "Stien til BOM-mappen er kopiert. Lim den inn i Utforsker.")
    : act(() => api("POST", "/api/folder/bom")));
  $("openListDir").addEventListener("click", () => act(() => api("POST", "/api/folder/lists")));

  // ---------- stengte perioder ----------
  function drawClosed() {
    $("closedList").textContent = CLOSED.map((c) => c.easter ? `${c.name} (påskeuka – flytter seg)` : `${c.name} uke ${c.from_week}${c.from_week !== c.to_week ? "–" + c.to_week : ""}`).join(", ") || "ingen";
    if ($("closedEdit").contains(document.activeElement)) return;
    $("closedEdit").innerHTML = CLOSED.map((c, i) =>
      `<div class="closedrow" data-i="${i}"><input type="text" data-k="name" value="${esc(c.name)}" aria-label="Navn">`
      + `<span>uke</span><input type="number" data-k="from_week" min="1" max="53" value="${c.from_week}" aria-label="Fra uke"${c.easter ? " disabled" : ""}>`
      + `<span>–</span><input type="number" data-k="to_week" min="1" max="53" value="${c.to_week}" aria-label="Til uke"${c.easter ? " disabled" : ""}>`
      + `<label class="easter" title="Uka med skjærtorsdag, langfredag og påskedag – regnes ut for hvert år"><input type="checkbox" data-k="easter"${c.easter ? " checked" : ""}> følger påsken</label>`
      + `<button type="button" class="mini quiet" data-act="remove">Fjern</button></div>`).join("")
      + `<button type="button" class="mini" data-act="new">+ Legg til stengt periode</button>`;
  }
  function saveClosed(list) {
    act(() => api("PUT", "/api/closed-periods", list), "Stengte perioder er lagret. Fristene er regnet ut på nytt.");
  }
  function readClosed() {
    return [...$("closedEdit").querySelectorAll(".closedrow")].map((r) => ({
      name: r.querySelector("[data-k=name]").value.trim(), from_week: parseInt(r.querySelector("[data-k=from_week]").value, 10), to_week: parseInt(r.querySelector("[data-k=to_week]").value, 10),
      easter: r.querySelector("[data-k=easter]").checked }));
  }
  $("closedToggle").addEventListener("click", () => {
    const open = $("closedEdit").hidden; $("closedEdit").hidden = !open;
    $("closedToggle").textContent = open ? "Ferdig" : "Endre"; $("closedToggle").setAttribute("aria-expanded", String(open));
  });
  $("closedEdit").addEventListener("change", () => saveClosed(readClosed()));
  $("closedEdit").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act]"); if (!b) return;
    const list = readClosed();
    if (b.dataset.act === "remove") list.splice(+b.closest(".closedrow").dataset.i, 1);
    else list.push({ name: "Ny periode", from_week: 8, to_week: 8 });
    saveClosed(list);
  });

  // ---------- new project ----------
  function openAdd(open) {
    $("addbox").hidden = !open; $("addToggle").setAttribute("aria-expanded", String(open));
    $("f-err").textContent = "";
    if (open) {
      $("f-sy").textContent = curSY() ? curSY().name : "";
      // lecturer: choose among their own courses (required); engineer: every active course, or none
      const choices = COURSES.filter((e) => e.active);
      $("f-course").innerHTML = (ENG ? `<option value="">Uten emne</option>` : choices.length ? "" : `<option value="">Du har ingen emner – foreslå et under «Emner» nederst</option>`)
        + choices.map((e) => `<option value="${e.id}"${ENG && ME.id && e.engineerId === ME.id ? " data-mine" : ""}>${courseLabel(e)}</option>`).join("");
      const is_mine = $("f-course").querySelector("[data-mine]"); if (is_mine) is_mine.selected = true;
      fromCourse(); showDates();
      $("f-name").focus();
    }
  }
  $("addToggle").addEventListener("click", () => openAdd($("addbox").hidden));
  $("f-cancel").addEventListener("click", () => openAdd(false));
  const prefill = () => {
    const typ = $("f-type").value, isSpring = (parseInt($("f-start").value, 10) || 40) < 32; // the semester the start week is in
    if (typ === "semester_kit") { $("f-start").value = isSpring ? 3 : 34; $("f-finish").value = isSpring ? 23 : 50; }
    else if (typ === "hackathon" || typ === "lab") $("f-finish").value = $("f-start").value; // a few days: the same week
    showDates();
  };
  // the actual dates the weeks fall on in the school year, so week 3 isn't read as January this year
  function showDates() {
    const cur = curSY(), week = (id) => parseInt($(id).value, 10);
    const find = (n) => cur && (cur.weeks.find((u) => u.week === n) || (n === 53 ? cur.weeks.find((u) => u.week === 52) : null));
    const a = find(week("f-start")), b = find(week("f-finish"));
    if (!a || !b) { $("f-dates").textContent = ""; return; }
    const sem = week("f-start") >= 32 ? "Høst" : "Vår", finish = new Date(Math.max(toD(b.date), toD(a.date)) + 6 * 864e5);
    $("f-dates").textContent = `${sem} ${new Date(toD(a.date)).getUTCFullYear()}: ${dfmtY.format(toD(a.date))} – ${dfmtY.format(finish)}`;
  }
  $("f-type").addEventListener("change", prefill);
  $("f-start").addEventListener("input", () => { if (["hackathon", "lab"].includes($("f-type").value)) $("f-finish").value = $("f-start").value; showDates(); });
  $("f-finish").addEventListener("input", showDates);
  const showFormSets = () => {
    const s = $("f-students").value;
    const n = setCount(s === "" ? null : parseInt(s, 10) || 0, parseInt($("f-group").value, 10), parseInt($("f-spare").value, 10));
    $("f-setsum").textContent = n != null ? `${n} sett` : "Antall studenter?";
  };
  ["f-students", "f-group", "f-spare"].forEach((id) => $(id).addEventListener("input", showFormSets));
  // the number of students comes from the course; it can be changed for this project only
  function fromCourse() {
    const e = COURSES.find((x) => String(x.id) === $("f-course").value);
    $("f-students").value = e && e.students != null ? e.students : "";
    showFormSets();
  }
  $("f-course").addEventListener("change", fromCourse);
  $("addForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = $("f-name").value.trim();
    if (!name) { $("f-err").textContent = "Gi prosjektet et navn."; $("f-name").focus(); return; }
    const num = (id, min, max) => Math.min(max, Math.max(min, parseInt($(id).value, 10) || min));
    const used = new Set(projects.map((p) => p.color));
    let color = 0; while (used.has(color) && color < NCOLORS - 1) color++;
    if (used.has(color)) color = projects.length % NCOLORS;
    const body = { name, type: $("f-type").value, school_year: SY,
      start: num("f-start", 1, 53), finish: num("f-finish", 1, 53), lead: num("f-lead", 0, 20), buf: num("f-buf", 0, 20),
      note: $("f-note").value.trim(), color, course: $("f-course").value || null,
      students: $("f-students").value === "" ? null : num("f-students", 0, 2000),
      group_size: num("f-group", 1, 20), spare: num("f-spare", 0, 99) };
    if (!ENG && !body.course) { $("f-err").textContent = "Velg hvilket emne prosjektet hører til."; return; }
    $("f-save").disabled = true;
    try { await api("POST", "/api/runs", body); $("addForm").reset(); openAdd(false); setStatus("Lagt til: " + name); await load(); }
    catch (err) { $("f-err").textContent = err.message; }
    finally { $("f-save").disabled = false; }
  });

  // ---------- markering ----------
  function highlight(id, ev) {
    document.querySelectorAll("g.proj").forEach((g) => g.classList.toggle("dim", !!id && g.dataset.id !== id));
    document.querySelectorAll("#tbody tr, #wheelList li").forEach((tr) => tr.classList.toggle("hl", !!id && tr.dataset.id === id));
    document.querySelectorAll(".ev").forEach((x) => x.classList.toggle("on", !!ev && x === ev));
    if (ev) markEvent(id, ev.dataset.kind, ev.dataset.d, ev.dataset.w); else { const hl = document.getElementById("hl"); if (hl) hl.innerHTML = ""; }
  }
  $("wheel").addEventListener("mouseover", (e) => {
    const g = e.target.closest("g.proj"), marker = e.target.closest("[data-ev]");
    if (g && marker) { // the deadline or the return: only that event in «neste 8 uker»
      highlight(null);
      document.querySelectorAll(".ev").forEach((x) => x.classList.toggle("on", x.dataset.id === g.dataset.id && x.dataset.kind === marker.dataset.ev));
      return;
    }
    highlight(g ? g.dataset.id : null);
  });
  $("wheel").addEventListener("mouseleave", () => highlight(null));
  $("tbody").addEventListener("mouseover", (e) => { const tr = e.target.closest("tr[data-id]"); highlight(tr ? tr.dataset.id : null); });
  $("tbody").addEventListener("mouseleave", () => highlight(null));
  $("wheelList").addEventListener("mouseover", (e) => { const li = e.target.closest("li[data-id]"); highlight(li ? li.dataset.id : null); });
  $("wheelList").addEventListener("mouseleave", () => highlight(null));
  $("weeks").addEventListener("mouseover", (e) => { const ev = e.target.closest(".ev"); highlight(ev ? ev.dataset.id : null, ev); });
  $("weeks").addEventListener("mouseleave", () => highlight(null));
  $("weeks").addEventListener("click", (e) => { const ev = e.target.closest(".ev"); if (ev) highlight(ev.dataset.id, ev); });

  const t = new Date();
  $("today").textContent = dfmtY.format(Date.UTC(t.getFullYear(), t.getMonth(), t.getDate()));
  renderAll(false);
  load().then(() => { if (TODAY) $("today").textContent = "I dag: uke " + TODAY.week + ", " + dfmtY.format(Date.UTC(t.getFullYear(), t.getMonth(), t.getDate())); });
  // errors in the page's own code (e.g. in a click) should be visible, not just stop silently
  const showError = (e) => setStatus("Noe gikk galt: " + ((e && e.message) || e) + ". Last siden på nytt, og si fra hvis det skjer igjen.");
  window.addEventListener("error", (ev) => showError(ev.error || ev.message));
  window.addEventListener("unhandledrejection", (ev) => showError(ev.reason));
  // fetch again when you come back from Excel
  window.addEventListener("focus", load);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
})();
