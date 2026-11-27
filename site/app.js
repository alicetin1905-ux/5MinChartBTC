/* Interactief dashboard: live OKX-koersen in de browser + zichtbare berekeningen (zie engine.js). */
"use strict";
const E = window.BTC5M;
const OKX = "https://www.okx.com";
const INST = "BTC-USDT-SWAP";
const CT_VAL = 0.01; // BTC per contract
const STEP = 300000; // 5 min in ms
const $ = (id) => document.getElementById(id);
const NF = (d = 0) => new Intl.NumberFormat("nl-NL", { minimumFractionDigits: d, maximumFractionDigits: d });
const usd = (x, d = 0) => (Number.isFinite(x) ? NF(d).format(x) : "—");
const pct = (x, d = 1) => (Number.isFinite(x) ? NF(d).format(x * 100) + "%" : "—");
const signed = (x, d = 2) => (Number.isFinite(x) ? (x > 0 ? "+" : x < 0 ? "−" : "") + NF(d).format(Math.abs(x)) + "%" : "—");
const fmtTime = new Intl.DateTimeFormat("nl-NL", { hour: "2-digit", minute: "2-digit" });
const fmtTimeS = new Intl.DateTimeFormat("nl-NL", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const fmtDT = new Intl.DateTimeFormat("nl-NL", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const FEATURE_NL = {
  r1: "Rendement 5 min", r3: "Rendement 15 min", r6: "Rendement 30 min", r12: "Rendement 60 min",
  rsi: "RSI", macd: "MACD / ATR", pctb: "Bollinger %B", volz: "Volume z-score", d_ema21: "Afstand EMA21",
  ema_spread: "EMA9 − EMA21", d_vwap: "Afstand VWAP", bbw: "Bollinger-breedte", hour_sin: "Tijd v/d dag (sin)", hour_cos: "Tijd v/d dag (cos)",
};
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function el(tag, attrs = {}, text) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text !== undefined) n.textContent = text;
  return n;
}
function sv(tag, attrs = {}, text) {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text !== undefined) n.textContent = text;
  return n;
}

// ------------------------------------------------------------------ toestand
const S = {
  candles: [], model: null, latest: null, evaluated: [],
  ind: null, feat: null, dir: null, sc: null, levels: null, closedIdx: -1,
  view: 72, H: 12, nPaths: 2000, volScale: 1, evalMode: "all",
  overlays: { ema9: true, ema21: true, ema50: false, vwap: true, bb: false, levels: true, dots: true },
  sim: null, bands: null, anim: null, hover: null, dirty: true,
  price: null, conn: "connecting", demo: new URLSearchParams(location.search).has("demo"),
  tapeQueue: [], trades: 0, deriv: null, needsRecompute: true,
};
let C = {};
function readColors() {
  const cs = getComputedStyle(document.documentElement);
  for (const k of ["surface-1", "surface-2", "text-primary", "text-secondary", "grid", "axis", "series-1", "up", "down",
    "ema9", "ema21", "ema50", "vwap", "bb"]) C[k] = cs.getPropertyValue(`--${k}`).trim();
  S.dirty = true;
}
readColors();
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { readColors(); renderAll(); });

// ------------------------------------------------------------------ data
async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}
const bust = () => `?t=${Date.now()}`;
const toCandle = ([t, o, h, l, c, v]) => ({ t: +t, o: +o, h: +h, l: +l, c: +c, v: +v });

function mergeCandles(rows) {
  const map = new Map(S.candles.map((c) => [c.t, c]));
  for (const r of rows) map.set(r.t, r);
  S.candles = [...map.values()].sort((a, b) => a.t - b.t).slice(-1100);
  S.needsRecompute = true;
}

async function restCandles(limit = 300) {
  const j = await getJSON(`${OKX}/api/v5/market/candles?instId=${INST}&bar=5m&limit=${limit}`);
  if (j.code !== "0") throw new Error(j.msg || "OKX-fout");
  return j.data.map((r) => ({ t: +r[0], o: +r[1], h: +r[2], l: +r[3], c: +r[4], v: +r[6] }));
}

async function restDerivatives() {
  try {
    const [fr, oi] = await Promise.all([
      getJSON(`${OKX}/api/v5/public/funding-rate?instId=${INST}`),
      getJSON(`${OKX}/api/v5/public/open-interest?instType=SWAP&instId=${INST}`),
    ]);
    const f = fr.data[0], o = oi.data[0];
    S.deriv = { funding_rate: +f.fundingRate, premium: f.premium ? +f.premium : null,
      next_funding_time: new Date(+f.fundingTime).toISOString(), open_interest_btc: +o.oiCcy, open_interest_usd: +o.oiUsd, live: true };
  } catch { /* val terug op de waarden van de server-run */ }
  renderFutures();
}

async function loadStatic() {
  const [latest, candles] = await Promise.all([getJSON("latest.json" + bust()), getJSON("candles.json" + bust())]);
  S.latest = latest;
  S.candles = candles.map(toCandle);
  try { S.model = await getJSON("model.json" + bust()); } catch { S.model = null; }
  try { S.evaluated = await getJSON("evaluated.json" + bust()); } catch { S.evaluated = []; }
  S.volScale = 1; // extra handmatige factor bovenop de geleerde kalibratie
  $("volSlider").value = 1;
  updateVolLabel();
  S.deriv = latest.derivatives || null;
  S.needsRecompute = true;
}

// ------------------------------------------------------------------ live feed
let ws = null, wsTimer = null, pollTimer = null, backoff = 2000;
function setConn(state, text) {
  S.conn = state;
  const b = $("conn");
  b.className = "badge " + (state === "live" || state === "demo" ? "live" : state === "rest" ? "warn" : "");
  b.querySelector("span").textContent = text;
}

function onTrade(px, szBtc, side, ts) {
  const bucket = Math.floor(ts / STEP) * STEP;
  let last = S.candles[S.candles.length - 1];
  if (!last || bucket > last.t) {
    if (last) onCandleClose();
    last = { t: bucket, o: px, h: px, l: px, c: px, v: 0 };
    S.candles.push(last);
  } else if (bucket < last.t) return;
  last.h = Math.max(last.h, px); last.l = Math.min(last.l, px); last.c = px; last.v += szBtc;
  S.trades++;
  setPrice(px);
  S.tapeQueue.push({ px, szBtc, side, ts });
  S.needsRecompute = true;
}

function connectWS() {
  if (S.demo) return startDemo();
  try { ws = new WebSocket("wss://ws.okx.com:8443/ws/v5/public"); } catch { return wsFailed(); }
  ws.onopen = () => {
    backoff = 2000;
    ws.send(JSON.stringify({ op: "subscribe", args: [{ channel: "trades", instId: INST }] }));
    setConn("live", "LIVE · websocket");
    stopPolling();
    clearInterval(wsTimer);
    wsTimer = setInterval(() => ws.readyState === 1 && ws.send("ping"), 20000);
  };
  ws.onmessage = (e) => {
    if (e.data === "pong") return;
    const m = JSON.parse(e.data);
    if (m.arg?.channel === "trades" && m.data) for (const t of m.data) onTrade(+t.px, +t.sz * CT_VAL, t.side, +t.ts);
  };
  ws.onclose = wsFailed;
  ws.onerror = () => ws.close();
}
function wsFailed() {
  clearInterval(wsTimer);
  if (S.conn !== "rest" && S.conn !== "static") setConn("rest", "verbinden… (tussentijds via REST)");
  startPolling();
  setTimeout(connectWS, backoff);
  backoff = Math.min(backoff * 2, 30000);
}
function startPolling() {
  if (pollTimer) return;
  const poll = async () => {
    try {
      const rows = await restCandles(3);
      const before = S.candles[S.candles.length - 1]?.t;
      mergeCandles(rows);
      const last = S.candles[S.candles.length - 1];
      if (before && last.t > before) onCandleClose();
      setPrice(last.c);
      if (S.conn !== "live") setConn("rest", "live via REST (elke 3 s)");
    } catch {
      if (S.conn !== "live") setConn("static", "offline · laatste server-run");
    }
  };
  poll();
  pollTimer = setInterval(poll, 3000);
}
function stopPolling() { clearInterval(pollTimer); pollTimer = null; }

function startDemo() {
  setConn("demo", "DEMO · gesimuleerde feed");
  const last = S.candles[S.candles.length - 1];
  const shift = Math.floor(Date.now() / STEP) * STEP - last.t;
  for (const c of S.candles) c.t += shift;
  S.needsRecompute = true;
  const z = S.model?.z_sample || [0];
  let price = last.c;
  const rnd = E.mulberry32(Date.now() & 0xffff);
  const step = () => {
    const sig = (S.ind ? S.ind.sigma[S.closedIdx] : 0.0012) / Math.sqrt(400);
    price *= Math.exp(sig * z[(rnd() * z.length) | 0]);
    const size = Math.exp(-3 + 1.6 * z[(rnd() * z.length) | 0]) ;
    onTrade(Math.round(price * 10) / 10, Math.min(size, 25), rnd() < 0.5 ? "buy" : "sell", Date.now());
    setTimeout(step, 80 + rnd() * 420);
  };
  step();
}

function onCandleClose() {
  // nieuwe candle: alles opnieuw, met animatie, en exacte candle via REST
  setTimeout(() => { recompute(); runSimulation(true); }, 50);
  if (!S.demo) restCandles(5).then(mergeCandles).catch(() => {});
}

// ------------------------------------------------------------------ rekenen
function recompute() {
  const c = S.candles;
  if (c.length < 260) return;
  S.ind = E.indicators(c);
  S.sigDes = S.model?.season ? E.deseasonalizedSigma(c, S.model.season, macroEv()) : null;
  const last = c.length - 1;
  S.closedIdx = c[last].t + STEP > Date.now() ? last - 1 : last;
  S.feat = E.features(c, S.ind, last);
  const prm = S.model?.direction || S.latest?.direction_model?.params;
  const dirScale = S.model?.dir_scale ?? S.latest?.learning?.calibration?.dir_scale ?? 0.5;
  S.dir = prm ? E.direction(S.feat, prm, dirScale) : null;
  S.sc = E.scorecard(c, S.ind);
  S.levels = E.swingLevels(c, S.ind.atr14[last]);
  S.needsRecompute = false;
  S.dirty = true;
}

function macroEv() {
  const m = S.model;
  return m?.events?.length ? { events: m.events, m0: m.event_m0 ?? 6, m1: m.event_m1 ?? 1.5 } : null;
}
function nextEvent() {
  const now = Date.now();
  return (S.model?.events || []).find((e) => e.t + 15 * 60000 > now) || null;
}

function zSample() {
  if (S.model?.z_sample?.length) return S.model.z_sample;
  const r = S.ind.logret, s = S.ind.sigma, z = [];
  for (let i = 2; i < r.length; i++) if (s[i - 1] > 0) z.push(r[i] / s[i - 1]);
  const m = z.reduce((a, b) => a + b, 0) / z.length, sd = Math.sqrt(z.reduce((a, b) => a + (b - m) ** 2, 0) / z.length);
  return z.map((v) => (v - m) / sd);
}

function runSimulation(animated) {
  if (!S.ind) return;
  const s0 = S.candles[S.candles.length - 1].c;
  const season = S.model?.season || null;
  const sigma = S.sigDes ? S.sigDes[S.closedIdx] : S.ind.sigma[S.closedIdx];
  const stepScale = E.stepScales(S.candles[S.closedIdx].t, S.H, season, macroEv());
  const targets = S.model?.c50 ? { c50: S.model.c50, c90: S.model.c90 } : null;
  const sim = E.simulate({ s0, sigma, volScale: S.volScale, z: zSample(), H: S.H, nPaths: S.nPaths,
    seed: (Date.now() / 1000) | 0, stepScale, targets });
  sim.sigma = sigma; sim.seasonNow = stepScale[0]; sim.sigEff = sigma * S.volScale * stepScale[0];
  const bands = E.bands(sim);
  if (animated) burstParticles();
  if (!animated || reduceMotion) {
    S.sim = sim; S.bands = bands; S.anim = null; S.dirty = true;
    renderMC(1); renderDist();
    return;
  }
  const nShow = Math.min(160, S.nPaths);
  S.anim = { start: performance.now(), dur: 2600, sim, bands, nShow, fadeUntil: 0 };
  $("simBtn").disabled = true;
}

// ------------------------------------------------------------------ prijs & teller
let flashTimer = null;
function setPrice(px) {
  const prev = S.price;
  S.price = px;
  const v = $("price");
  v.textContent = usd(px, 1);
  if (prev != null && px !== prev) {
    v.classList.remove("up", "down");
    void v.offsetWidth;
    v.classList.add(px > prev ? "up" : "down");
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => v.classList.remove("up", "down"), 250);
  }
  S.dirty = true;
}

function tickClock() {
  const now = Date.now();
  const next = Math.ceil(now / STEP) * STEP, left = next - now;
  $("cdCandle").textContent = `${Math.floor(left / 60000)}:${String(Math.floor((left % 60000) / 1000)).padStart(2, "0")}`;
  $("cdCandleBar").style.width = `${100 - (left / STEP) * 100}%`;
  if (S.latest) {
    const age = Math.round((now - new Date(S.latest.generated_at).getTime()) / 60000);
    const d = new Date(now), mins = [2, 17, 32, 47];
    const m = d.getUTCMinutes();
    const nm = mins.find((x) => x > m) ?? 62;
    const nextRun = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), d.getUTCHours(), nm));
    $("meta").textContent = `Server-analyse ${age} min geleden · volgende ≈ ${fmtTime.format(nextRun)} · `
      + `${S.trades ? NF(0).format(S.trades) + " trades ontvangen" : "wacht op trades"} · alle berekeningen draaien live in je browser`;
  }
}

// ------------------------------------------------------------------ hoofdgrafiek (canvas)
const chart = $("chart"), hist = $("hist");
let CH = { W: 0, H: 0 };
function sizeCanvas(cv, h) {
  const dpr = window.devicePixelRatio || 1, w = cv.parentElement.clientWidth;
  cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
  cv.style.height = h + "px";
  const ctx = cv.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { W: w, H: h, ctx };
}
function resize() {
  CH = sizeCanvas(chart, chart.parentElement.clientWidth < 600 ? 360 : 470);
  HI = sizeCanvas(hist, 150);
  S.dirty = true;
  renderMC(S.anim ? 0 : 1);
}
let HI = { W: 0, H: 0 };

function niceTicks(min, max, count) {
  const span = max - min, raw = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count) || 10 * mag;
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(v);
  return out;
}

function layout() {
  const { W, H } = CH, small = W < 600;
  const m = { left: 6, right: small ? 62 : 72, top: 10, bottom: 22 };
  const volH = small ? 44 : 60;
  const plotB = H - m.bottom - volH - 8;
  const c = S.candles, n = c.length;
  const start = Math.max(0, n - S.view);
  // het verwachtingsgebied krijgt een vast deel van de breedte, zodat de dots ruimte hebben
  const plotW = W - m.left - m.right;
  const futW = plotW * (small ? 0.36 : 0.3);
  const slotW = (plotW - futW) / (n - start);
  const fw = futW / (S.H + 0.6);
  const xs = (i) => m.left + (i - start + 0.5) * slotW;
  return { W, H, m, volH, plotB, start, n, slotW, fw, small, xs,
    xNow: xs(n - 1) + slotW / 2, xf: (step) => xs(n - 1) + step * fw };
}

function drawChart(now) {
  const ctx = CH.ctx; if (!ctx || !S.ind) return;
  const L = layout(), { W, H, m, plotB, start, n, slotW } = L, c = S.candles, ind = S.ind;
  ctx.clearRect(0, 0, W, H);

  // animatie / banden
  let bands = S.bands, sim = S.sim, prog = 1;
  if (S.anim) {
    prog = Math.max(0, Math.min(1, (now - S.anim.start) / S.anim.dur));
    sim = S.anim.sim; bands = prog >= 1 ? S.anim.bands : null;
  }
  const s0 = c[n - 1].c;
  // y-domein
  let lo = Infinity, hi = -Infinity;
  for (let i = start; i < n; i++) { lo = Math.min(lo, c[i].l); hi = Math.max(hi, c[i].h); }
  const fb = S.anim ? S.anim.bands : bands;
  if (fb) for (const b of fb) { lo = Math.min(lo, b.p05); hi = Math.max(hi, b.p95); }
  for (const k of ["ema9", "ema21", "ema50", "vwap"]) if (S.overlays[k]) for (let i = start; i < n; i++) { lo = Math.min(lo, ind[k][i]); hi = Math.max(hi, ind[k][i]); }
  if (S.overlays.bb) for (let i = start; i < n; i++) if (Number.isFinite(ind.bb_up[i])) { lo = Math.min(lo, ind.bb_low[i]); hi = Math.max(hi, ind.bb_up[i]); }
  const pad = (hi - lo) * 0.06; lo -= pad; hi += pad;
  const y = (v) => m.top + (1 - (v - lo) / (hi - lo)) * (plotB - m.top);
  const xs = L.xs;
  L.y = y; L.lo = lo; L.hi = hi; S.layout = L;

  // grid + prijsas
  ctx.font = "12px system-ui, sans-serif"; ctx.textBaseline = "middle";
  for (const v of niceTicks(lo, hi, L.small ? 5 : 7)) {
    ctx.strokeStyle = C.grid; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(m.left, Math.round(y(v)) + .5); ctx.lineTo(W - m.right, Math.round(y(v)) + .5); ctx.stroke();
    ctx.fillStyle = C["text-secondary"]; ctx.textAlign = "left";
    ctx.fillText(usd(v), W - m.right + 6, y(v));
  }
  // tijdas
  const stepMin = (S.view <= 36 ? 30 : S.view <= 72 ? 60 : S.view <= 144 ? 120 : 240) * (L.small ? 2 : 1);
  ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
  for (let i = start; i < n; i++) {
    const d = new Date(c[i].t);
    if ((d.getHours() * 60 + d.getMinutes()) % stepMin === 0) {
      ctx.fillStyle = C["text-secondary"]; ctx.fillText(fmtTime.format(d), xs(i), H - 6);
    }
  }
  // "nu"-lijn
  const xNow = xs(n - 1) + slotW / 2;
  ctx.strokeStyle = C.axis; ctx.beginPath(); ctx.moveTo(xNow + .5, m.top); ctx.lineTo(xNow + .5, H - m.bottom); ctx.stroke();

  // Bollinger
  if (S.overlays.bb) {
    ctx.beginPath(); let first = true;
    for (let i = start; i < n; i++) if (Number.isFinite(ind.bb_up[i])) { first ? ctx.moveTo(xs(i), y(ind.bb_up[i])) : ctx.lineTo(xs(i), y(ind.bb_up[i])); first = false; }
    for (let i = n - 1; i >= start; i--) if (Number.isFinite(ind.bb_low[i])) ctx.lineTo(xs(i), y(ind.bb_low[i]));
    ctx.closePath(); ctx.globalAlpha = .08; ctx.fillStyle = C.bb; ctx.fill(); ctx.globalAlpha = 1;
    for (const k of ["bb_up", "bb_low", "bb_mid"]) line(ctx, start, n, xs, (i) => y(ind[k][i]), C.bb, 1, k === "bb_mid" ? .6 : 1);
  }
  // steun / weerstand
  if (S.overlays.levels && S.levels) {
    ctx.font = "11px system-ui, sans-serif"; ctx.textAlign = "left"; ctx.textBaseline = "bottom";
    for (const [arr, name] of [[S.levels.resistance, "weerstand"], [S.levels.support, "steun"]]) {
      for (const lv of arr.slice(0, L.small ? 1 : 2)) {
        if (lv.v < lo || lv.v > hi) continue;
        ctx.strokeStyle = C["text-secondary"]; ctx.globalAlpha = .45;
        ctx.beginPath(); ctx.moveTo(m.left, Math.round(y(lv.v)) + .5); ctx.lineTo(xNow, Math.round(y(lv.v)) + .5); ctx.stroke();
        ctx.globalAlpha = 1; ctx.fillStyle = C["text-secondary"];
        ctx.strokeStyle = C["surface-1"]; ctx.lineWidth = 3; ctx.strokeText(`${name} ${usd(lv.v)} (${lv.n}×)`, m.left + 4, y(lv.v) - 2);
        ctx.fillText(`${name} ${usd(lv.v)} (${lv.n}×)`, m.left + 4, y(lv.v) - 2); ctx.lineWidth = 1;
      }
    }
  }
  // volume
  const vmax = Math.max(...c.slice(start).map((d) => d.v)) || 1;
  const vy0 = H - m.bottom, vh = L.volH;
  for (let i = start; i < n; i++) {
    const h = (c[i].v / vmax) * vh, up = c[i].c >= c[i].o;
    ctx.fillStyle = up ? C.up : C.down; ctx.globalAlpha = .35;
    ctx.fillRect(xs(i) - Math.max(1, slotW * .3), vy0 - h, Math.max(1, slotW * .6), h);
  }
  ctx.globalAlpha = 1;
  ctx.fillStyle = C["text-secondary"]; ctx.font = "11px system-ui, sans-serif"; ctx.textAlign = "left"; ctx.textBaseline = "top";
  ctx.fillText("volume", m.left + 2, vy0 - vh);

  // candles
  const cw = Math.max(1, Math.min(14, slotW * .62));
  for (let i = start; i < n; i++) {
    const d = c[i], up = d.c >= d.o, col = up ? C.up : C.down, x = xs(i);
    ctx.strokeStyle = col; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(Math.round(x) + .5, y(d.h)); ctx.lineTo(Math.round(x) + .5, y(d.l)); ctx.stroke();
    const top = y(Math.max(d.o, d.c)), bot = y(Math.min(d.o, d.c)), h = Math.max(1, bot - top);
    if (up && cw >= 4) { ctx.fillStyle = C["surface-1"]; ctx.fillRect(x - cw / 2, top, cw, h); ctx.lineWidth = 1.5; ctx.strokeRect(x - cw / 2 + .75, top + .75, cw - 1.5, Math.max(0, h - 1.5)); }
    else { ctx.fillStyle = col; ctx.fillRect(x - cw / 2, top, cw, h); }
  }
  // lijnen
  for (const k of ["ema50", "ema21", "ema9", "vwap"]) if (S.overlays[k]) line(ctx, start, n, xs, (i) => y(ind[k][i]), C[k], 1.5, 1);

  // Monte Carlo: paden (animatie) en banden
  const xf = L.xf;
  if (sim) {
    ctx.save(); ctx.beginPath(); ctx.rect(m.left, m.top, W - m.left - m.right, plotB - m.top); ctx.clip();
    const nShow = S.anim ? S.anim.nShow : 0;
    if (S.anim) {
      const revealed = Math.min(sim.H, prog * sim.H * 1.15);
      ctx.strokeStyle = C["series-1"]; ctx.lineWidth = 1;
      ctx.globalAlpha = prog < 1 ? .22 : Math.max(.05, .22 - (now - S.anim.start - S.anim.dur) / 6000);
      for (let k = 0; k < nShow; k++) {
        ctx.beginPath(); ctx.moveTo(xf(0), y(sim.paths[0][k]));
        const full = Math.floor(revealed);
        for (let t = 1; t <= full; t++) ctx.lineTo(xf(t), y(sim.paths[t][k]));
        if (full < sim.H && revealed > full) {
          const f = revealed - full, a = sim.paths[full][k], b = sim.paths[full + 1][k];
          ctx.lineTo(xf(full + f), y(a + (b - a) * f));
        }
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
      if (prog >= 1 && now - S.anim.start - S.anim.dur > 6000) { S.sim = S.anim.sim; S.bands = S.anim.bands; S.anim = null; }
    }
    if (bands) {
      const fadeIn = S.anim ? Math.min(1, (now - S.anim.start - S.anim.dur) / 600) : 1;
      const poly = (kl, kh, a) => {
        ctx.beginPath();
        bands.forEach((b, t) => (t ? ctx.lineTo(xf(t), y(b[kh])) : ctx.moveTo(xf(t), y(b[kh]))));
        for (let t = bands.length - 1; t >= 0; t--) ctx.lineTo(xf(t), y(bands[t][kl]));
        ctx.closePath(); ctx.globalAlpha = a * fadeIn; ctx.fillStyle = C["series-1"]; ctx.fill();
      };
      poly("p05", "p95", .14); poly("p25", "p75", .2);
      ctx.globalAlpha = fadeIn; ctx.strokeStyle = C["series-1"]; ctx.lineWidth = 2; ctx.lineJoin = "round";
      ctx.beginPath(); bands.forEach((b, t) => (t ? ctx.lineTo(xf(t), y(b.p50)) : ctx.moveTo(xf(t), y(b.p50)))); ctx.stroke();
      ctx.globalAlpha = 1;
      const e = bands[bands.length - 1];
      ctx.fillStyle = C["text-secondary"]; ctx.font = "11px system-ui, sans-serif"; ctx.textAlign = "right"; ctx.textBaseline = "middle";
      ctx.globalAlpha = fadeIn;
      ctx.fillText(`95% ${usd(e.p95)}`, W - m.right - 4, y(e.p95) - 8);
      ctx.fillText(`5% ${usd(e.p05)}`, W - m.right - 4, y(e.p05) + 8);
      ctx.globalAlpha = 1;
    }
    // macro-moment binnen de horizon: verticale markering
    const ne = nextEvent();
    if (ne) {
      const step = (ne.t - S.candles[S.closedIdx].t) / STEP;
      if (step >= 1 && step <= sim.H) {
        const xe = xf(step - 0.5);
        ctx.strokeStyle = C["text-primary"]; ctx.globalAlpha = .5; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(Math.round(xe) + .5, m.top); ctx.lineTo(Math.round(xe) + .5, plotB); ctx.stroke();
        ctx.globalAlpha = 1; ctx.font = "600 11px system-ui, sans-serif"; ctx.textAlign = "left"; ctx.textBaseline = "top";
        ctx.lineWidth = 3; ctx.strokeStyle = C["surface-1"]; ctx.strokeText(`⚡ ${ne.name}`, xe + 4, m.top + 2);
        ctx.fillStyle = C["text-primary"]; ctx.fillText(`⚡ ${ne.name}`, xe + 4, m.top + 2);
      }
    }
    if (S.overlays.dots && !reduceMotion) drawParticles(ctx, now, sim, xf, y, L);
    ctx.restore();
  }

  // live prijslijn + label op de as
  const up = c[n - 1].c >= c[n - 1].o;
  ctx.strokeStyle = up ? C.up : C.down; ctx.globalAlpha = .7; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(m.left, Math.round(y(s0)) + .5); ctx.lineTo(W - m.right, Math.round(y(s0)) + .5); ctx.stroke();
  ctx.globalAlpha = 1;
  ctx.fillStyle = up ? C.up : C.down;
  roundRect(ctx, W - m.right + 2, y(s0) - 10, m.right - 4, 20, 4); ctx.fill();
  ctx.fillStyle = "#ffffff"; ctx.font = "600 12px system-ui, sans-serif"; ctx.textAlign = "left"; ctx.textBaseline = "middle";
  ctx.fillText(usd(s0, 1), W - m.right + 6, y(s0));
  // pulserende stip op de laatste prijs
  const pulse = (Math.sin(now / 300) + 1) / 2;
  ctx.beginPath(); ctx.arc(xs(n - 1), y(s0), 4 + pulse * 4, 0, Math.PI * 2); ctx.fillStyle = up ? C.up : C.down; ctx.globalAlpha = .18 * (1 - pulse) + .05; ctx.fill();
  ctx.globalAlpha = 1; ctx.beginPath(); ctx.arc(xs(n - 1), y(s0), 4, 0, Math.PI * 2); ctx.fill(); ctx.lineWidth = 2; ctx.strokeStyle = C["surface-1"]; ctx.stroke();

  // crosshair
  if (S.hover) {
    const hx = S.hover.slot < n ? xs(S.hover.slot) : xf(S.hover.slot - n + 1);
    ctx.strokeStyle = C["text-secondary"]; ctx.lineWidth = 1; ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(Math.round(hx) + .5, m.top); ctx.lineTo(Math.round(hx) + .5, H - m.bottom); ctx.stroke();
    if (S.hover.y != null && S.hover.y < plotB) {
      ctx.beginPath(); ctx.moveTo(m.left, Math.round(S.hover.y) + .5); ctx.lineTo(W - m.right, Math.round(S.hover.y) + .5); ctx.stroke();
      const v = lo + (1 - (S.hover.y - m.top) / (plotB - m.top)) * (hi - lo);
      ctx.fillStyle = C["text-primary"]; roundRect(ctx, W - m.right + 2, S.hover.y - 10, m.right - 4, 20, 4); ctx.fill();
      ctx.fillStyle = C["surface-1"]; ctx.font = "12px system-ui, sans-serif"; ctx.fillText(usd(v), W - m.right + 6, S.hover.y);
    }
  }
}
// ------------------------------------------------------------------ bewegende dots (Monte Carlo-deeltjes)
// Elke dot volgt één gesimuleerd pad van "nu" naar de horizon en landt daar. De landingen stapelen
// zich rechts op tot een liggend histogram: zo zie je de kansverdeling letterlijk ontstaan.
const PART = { list: [], land: new Float64Array(44), rings: [] };
const ZR = 3.5; // histogram dekt ±3,5 standaarddeviaties
function spawnParticle(p, now, sim, mode) {
  p.k = (Math.random() * sim.nPaths) | 0;
  p.dur = 2300 + Math.random() * 1900;
  p.born = mode === "initial" ? now - Math.random() * p.dur : mode === "burst" ? now + Math.random() * 900 : now;
}
function burstParticles() {
  PART.land.fill(0);
  PART.burst = true;
}
function drawParticles(ctx, now, sim, xf, y, L) {
  const want = L.small ? 70 : 150, NB = PART.land.length;
  while (PART.list.length < want) { const p = {}; spawnParticle(p, now, sim, "initial"); PART.list.push(p); }
  PART.list.length = want;
  if (PART.burst) { for (const p of PART.list) spawnParticle(p, now, sim, "burst"); PART.burst = false; }
  const H = sim.H, s0 = sim.s0, sd = sim.sdSteps ? sim.sdSteps[H - 1] : (sim.sigEff || 0.0015) * Math.sqrt(H);
  const edge = (j) => s0 * Math.exp(((j / NB) * 2 - 1) * ZR * sd);
  const xEnd = xf(H) + L.fw * 0.5, maxW = Math.max(28, (xEnd - xf(0)) * 0.42);

  // liggend histogram van de landingen
  let maxC = 0; for (let j = 0; j < NB; j++) maxC = Math.max(maxC, PART.land[j]);
  if (maxC > 0) {
    for (let j = 0; j < NB; j++) {
      if (PART.land[j] < 0.3) continue;
      const yt = y(edge(j + 1)), yb = y(edge(j)), w = (PART.land[j] / maxC) * maxW;
      ctx.fillStyle = edge(j + 0.5) >= s0 ? C.up : C.down; ctx.globalAlpha = .38;
      ctx.fillRect(xEnd - w, yt + 1, w, Math.max(1, yb - yt - 2));
    }
    ctx.globalAlpha = 1;
  }

  // dots met een kort spoor
  const priceAt = (k, st) => {
    const a = Math.min(H, Math.floor(st)), b = Math.min(H, a + 1), f = st - a;
    return sim.paths[a][k] + (sim.paths[b][k] - sim.paths[a][k]) * f;
  };
  for (const p of PART.list) {
    if (p.k >= sim.nPaths) spawnParticle(p, now, sim, "now");
    let u = (now - p.born) / p.dur;
    if (u >= 1) {
      const end = sim.paths[H][p.k];
      const j = Math.floor(((Math.log(end / s0) / sd / ZR + 1) / 2) * NB);
      if (j >= 0 && j < NB) { for (let q = 0; q < NB; q++) PART.land[q] *= 0.997; PART.land[j] += 1; }
      PART.rings.push({ x: xEnd, y: y(end), t: now, up: end >= s0 });
      spawnParticle(p, now, sim, "now"); u = 0;
    }
    if (u < 0) continue;
    const ease = 1 - Math.pow(1 - u, 1.6); // vlot vertrekken, rustig landen
    const st = ease * H, end = sim.paths[H][p.k], col = end >= s0 ? C.up : C.down;
    const tail = Math.max(0, st - 1.6);
    ctx.strokeStyle = col; ctx.lineWidth = 1.5; ctx.globalAlpha = .3;
    ctx.beginPath(); ctx.moveTo(xf(tail), y(priceAt(p.k, tail)));
    for (let t = Math.ceil(tail); t < st; t++) ctx.lineTo(xf(t), y(sim.paths[t][p.k]));
    const px = xf(st), py = y(priceAt(p.k, st));
    ctx.lineTo(px, py); ctx.stroke();
    ctx.globalAlpha = .92; ctx.fillStyle = col;
    ctx.beginPath(); ctx.arc(px, py, 2.6, 0, Math.PI * 2); ctx.fill();
  }
  // landingsringetjes
  PART.rings = PART.rings.filter((r) => now - r.t < 550).slice(-80);
  for (const r of PART.rings) {
    const a = (now - r.t) / 550;
    ctx.globalAlpha = .4 * (1 - a); ctx.strokeStyle = r.up ? C.up : C.down; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(r.x, r.y, 2 + a * 5, 0, Math.PI * 2); ctx.stroke();
  }
  ctx.globalAlpha = 1;

  // exacte kansen uit de volledige simulatie
  if (sim.pUp === undefined) { let u = 0; const e = sim.paths[H]; for (let k = 0; k < e.length; k++) if (e[k] >= s0) u++; sim.pUp = u / e.length; }
  ctx.font = "600 12px system-ui, sans-serif"; ctx.textAlign = "right"; ctx.lineWidth = 3; ctx.strokeStyle = C["surface-1"];
  const lbl = (txt, yy, base) => { ctx.textBaseline = base; ctx.strokeText(txt, xEnd - 4, yy); ctx.fillStyle = C["text-primary"]; ctx.fillText(txt, xEnd - 4, yy); };
  lbl(`▲ ${pct(sim.pUp, 0)} hoger`, y(s0) - 6, "bottom");
  lbl(`▼ ${pct(1 - sim.pUp, 0)} lager`, y(s0) + 6, "top");
}

function line(ctx, start, n, xs, yf, color, w, alpha) {
  ctx.strokeStyle = color; ctx.lineWidth = w; ctx.globalAlpha = alpha; ctx.lineJoin = "round";
  ctx.beginPath(); let first = true;
  for (let i = start; i < n; i++) { const yy = yf(i); if (!Number.isFinite(yy)) continue; first ? ctx.moveTo(xs(i), yy) : ctx.lineTo(xs(i), yy); first = false; }
  ctx.stroke(); ctx.globalAlpha = 1;
}
function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}

// hover / toetsenbord
function slotFromX(clientX) {
  const L = S.layout; if (!L) return null;
  const r = chart.getBoundingClientRect(), x = clientX - r.left;
  if (x > L.xNow) return L.n - 1 + Math.max(1, Math.min(S.H, Math.round((x - L.xs(L.n - 1)) / L.fw)));
  const i = Math.floor((x - L.m.left) / L.slotW) + L.start;
  return Math.max(L.start, Math.min(L.n - 1, i));
}
function showTip(clientX, clientY) {
  const L = S.layout, tip = $("tip"); if (!L || !S.hover) return;
  tip.replaceChildren();
  const row = (label, val, color) => {
    const r = el("div", { class: "r" }), sp = el("span");
    if (color) { const i = el("i"); i.style.background = color; sp.append(i); }
    sp.append(document.createTextNode(label)); r.append(sp, el("b", {}, val)); tip.append(r);
  };
  const i = S.hover.slot, c = S.candles, ind = S.ind;
  if (i < L.n) {
    const d = c[i];
    tip.append(el("div", { class: "t" }, fmtDT.format(new Date(d.t)) + (i === L.n - 1 && d.t + STEP > Date.now() ? " · lopende candle" : "")));
    row("open / slot", `${usd(d.o, 1)} / ${usd(d.c, 1)}`); row("hoog / laag", `${usd(d.h, 1)} / ${usd(d.l, 1)}`);
    row("verandering", signed((d.c / d.o - 1) * 100, 3)); row("volume", `${NF(1).format(d.v)} BTC`);
    if (S.overlays.ema9) row("EMA 9", usd(ind.ema9[i]), C.ema9);
    if (S.overlays.ema21) row("EMA 21", usd(ind.ema21[i]), C.ema21);
    if (S.overlays.ema50) row("EMA 50", usd(ind.ema50[i]), C.ema50);
    if (S.overlays.vwap) row("VWAP", usd(ind.vwap[i]), C.vwap);
    row("RSI 14", NF(1).format(ind.rsi14[i]));
  } else {
    const step = i - L.n + 1, b = (S.anim?.bands || S.bands)?.[step];
    tip.append(el("div", { class: "t" }, `Over ${step * 5} min (${fmtTime.format(new Date(c[L.n - 1].t + step * STEP + STEP))})`));
    if (b) { row("mediaan", usd(b.p50)); row("50% kans", `${usd(b.p25)} – ${usd(b.p75)}`); row("90% kans", `${usd(b.p05)} – ${usd(b.p95)}`); }
    const sim = S.anim?.sim || S.sim;
    if (sim && S.hover.y != null && S.hover.y < L.plotB && step >= 1) {
      const v = L.lo + (1 - (S.hover.y - L.m.top) / (L.plotB - L.m.top)) * (L.hi - L.lo);
      const arr = sim.paths[step]; let above = 0;
      for (let k = 0; k < arr.length; k++) if (arr[k] > v) above++;
      tip.append(el("div", { class: "t" }, `Prijsniveau ${usd(v)}`));
      row("kans erboven", pct(above / arr.length, 0));
      row(`kans geraakt binnen ${step * 5} min`, pct(E.touchProb(sim, step, v), 0));
    }
  }
  tip.hidden = false;
  const tw = tip.offsetWidth, th = tip.offsetHeight;
  tip.style.left = Math.min(innerWidth - tw - 8, Math.max(8, clientX + 16)) + "px";
  tip.style.top = Math.max(8, Math.min(innerHeight - th - 8, clientY - th / 2)) + "px";
}
chart.addEventListener("pointermove", (e) => {
  const r = chart.getBoundingClientRect();
  S.hover = { slot: slotFromX(e.clientX), y: e.clientY - r.top }; S.dirty = true; showTip(e.clientX, e.clientY);
});
chart.addEventListener("pointerleave", () => { S.hover = null; S.dirty = true; $("tip").hidden = true; });
chart.addEventListener("keydown", (e) => {
  if (!["ArrowLeft", "ArrowRight", "Escape"].includes(e.key)) return;
  e.preventDefault();
  const L = S.layout; if (!L) return;
  if (e.key === "Escape") { S.hover = null; $("tip").hidden = true; S.dirty = true; return; }
  const cur = S.hover?.slot ?? L.n - 1;
  S.hover = { slot: Math.max(L.start, Math.min(L.n - 1 + S.H, cur + (e.key === "ArrowRight" ? 1 : -1))), y: null };
  S.dirty = true;
  const r = chart.getBoundingClientRect();
  const x = S.hover.slot < L.n ? L.xs(S.hover.slot) : L.xf(S.hover.slot - L.n + 1);
  showTip(r.left + x, r.top + r.height / 3);
});
chart.addEventListener("blur", () => { S.hover = null; $("tip").hidden = true; S.dirty = true; });

// ------------------------------------------------------------------ Monte Carlo-paneel
function renderMC(prog) {
  const ctx = HI.ctx; if (!ctx) return;
  const src = S.anim || (S.sim ? { sim: S.sim, bands: S.bands } : null);
  ctx.clearRect(0, 0, HI.W, HI.H);
  if (!src) return;
  const sim = src.sim, ends = sim.paths[sim.H];
  const count = Math.max(1, Math.floor(ends.length * Math.min(1, prog)));
  const sub = ends.subarray(0, count);
  const [a, b] = E.quantiles(ends, [0.005, 0.995]);
  const bins = 46, bw = (b - a) / bins, hcount = new Array(bins).fill(0);
  for (let k = 0; k < count; k++) { const j = Math.floor((sub[k] - a) / bw); if (j >= 0 && j < bins) hcount[j]++; }
  const full = new Array(bins).fill(0);
  for (let k = 0; k < ends.length; k++) { const j = Math.floor((ends[k] - a) / bw); if (j >= 0 && j < bins) full[j]++; }
  const ymax = Math.max(...full) || 1;
  const m = { l: 4, r: 4, t: 32, b: 18 }, W = HI.W, H = HI.H;
  const x = (v) => m.l + ((v - a) / (b - a)) * (W - m.l - m.r);
  const colW = (W - m.l - m.r) / bins;
  ctx.fillStyle = C["series-1"];
  for (let j = 0; j < bins; j++) {
    const h = (hcount[j] / ymax) * (H - m.t - m.b);
    ctx.globalAlpha = .75; ctx.fillRect(m.l + j * colW + 1, H - m.b - h, Math.max(1, colW - 2), h);
  }
  ctx.globalAlpha = 1;
  ctx.strokeStyle = C.axis; ctx.beginPath(); ctx.moveTo(m.l, H - m.b + .5); ctx.lineTo(W - m.r, H - m.b + .5); ctx.stroke();
  const [q05, q50, q95] = E.quantiles(sub, [0.05, 0.5, 0.95]);
  ctx.font = "11px system-ui, sans-serif"; ctx.textBaseline = "alphabetic";
  for (const [v, lbl] of [[q05, "5%"], [q50, "mediaan"], [q95, "95%"]]) {
    ctx.strokeStyle = C["text-primary"]; ctx.globalAlpha = .55;
    ctx.beginPath(); ctx.moveTo(Math.round(x(v)) + .5, m.t); ctx.lineTo(Math.round(x(v)) + .5, H - m.b); ctx.stroke(); ctx.globalAlpha = 1;
    ctx.fillStyle = C["text-secondary"];
    const isMid = v === q50;
    ctx.textAlign = isMid ? "center" : v === q05 ? "right" : "left";
    // 5% en 95% op de bovenste regel naar buiten, mediaan eronder: zo overlappen ze nooit
    ctx.fillText(`${lbl} ${usd(v)}`, Math.min(W - 40, Math.max(40, x(v) + (isMid ? 0 : v === q05 ? -3 : 3))), isMid ? m.t - 4 : m.t - 18);
  }
  ctx.fillStyle = C["text-secondary"]; ctx.textAlign = "left";
  ctx.fillText(usd(a), m.l, H - 4); ctx.textAlign = "right"; ctx.fillText(usd(b), W - m.r, H - 4);
  ctx.textAlign = "center"; ctx.fillText(`${NF(0).format(count)} van ${NF(0).format(ends.length)} paden`, W / 2, H - 4);

  // stappen
  const steps = [...document.querySelectorAll("#mcSteps li")];
  const active = prog < .1 ? 1 : prog < .2 ? 2 : prog < 1 ? 3 : 4;
  steps.forEach((li, k) => li.classList.toggle("active", S.anim ? k + 1 === active : false));
  const sigma = sim.sigma ?? S.ind.sigma[S.closedIdx], sn = sim.seasonNow ?? 1;
  $("mcSigma").textContent = S.model?.season
    ? `σ̃ = ${NF(3).format(sigma * 100)}% (zonder tijdseffect) × ${NF(2).format(sn)} (dit uur) = ${NF(3).format(sigma * sn * 100)}% per candle`
    : `σ = ${NF(3).format(sigma * 100)}% per candle (λ = 0,94)`;
  renderSeason();
  const ne = nextEvent(), box = $("macroNext");
  if (box) {
    if (!ne) box.textContent = "Geen macro-moment in de agenda voor de komende 30 dagen.";
    else {
      const m0 = S.model?.event_m0 ?? 6;
      const mins = Math.round((ne.t - Date.now()) / 60000);
      const when = mins < 0 ? "nu bezig" : mins < 120 ? `over ${mins} min` : fmtDT.format(new Date(ne.t));
      box.textContent = `⚡ Volgende macro-moment: ${ne.name} · ${when}. In die candle rekent het model met ×${NF(1).format(m0)} zoveel variantie (geleerd uit eerdere publicaties).`;
    }
  }
  $("mcScale").textContent = sim.k50 !== undefined && S.model?.c50
    ? `50%-band ×${NF(2).format(sim.k50)} · 90%-band ×${NF(2).format(sim.k90)} (doel: ${NF(2).format(S.model.c50)}σ / ${NF(2).format(S.model.c90)}σ)`
    : "nog geen geleerde kalibratie (volgende server-run)";
  $("mcHz").textContent = `over ${S.H * 5} min`;
  $("mcResult").textContent = `90%: ${usd(q05)} – ${usd(q95)} · mediaan ${usd(q50)}`;
  const status = S.anim && prog < 1
    ? `Simuleren: pad ${NF(0).format(count)} / ${NF(0).format(ends.length)} · stap ${Math.min(sim.H, Math.ceil(prog * sim.H * 1.15))} / ${sim.H}`
    : `${NF(0).format(ends.length)} paden · ${sim.H * 5} min vooruit · tijdseffect nu ×${NF(2).format(sim.seasonNow || 1)}${S.volScale !== 1 ? ` · extra ×${NF(2).format(S.volScale)}` : ""}`;
  $("simStatus").textContent = status;
}

// ------------------------------------------------------------------ volatiliteit per uur
let seasonKey = "";
function renderSeason() {
  const svgEl = $("seasonChart"), season = S.model?.season;
  if (!svgEl) return;
  const now = new Date(), key = `${now.getHours()}-${now.getDay()}-${!!season}`;
  if (key === seasonKey) return;
  seasonKey = key;
  svgEl.replaceChildren();
  if (!season) { svgEl.append(sv("text", { x: 4, y: 30, "font-size": 11, fill: "var(--text-secondary)" }, "Beschikbaar na de volgende server-run.")); return; }
  const W = svgEl.clientWidth || 300, H = 74, m = { l: 4, r: 4, t: 14, b: 16 };
  svgEl.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const vals = [];
  for (let h = 0; h < 24; h++) {
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, 30);
    vals.push(season[E.seasonBucket(d.getTime())]);
  }
  const vmax = Math.max(...vals, 1.2), bw = (W - m.l - m.r) / 24;
  const y = (v) => m.t + (1 - v / vmax) * (H - m.t - m.b);
  svgEl.append(sv("line", { x1: m.l, x2: W - m.r, y1: y(1), y2: y(1), stroke: "var(--axis)" }));
  svgEl.append(sv("text", { x: W - m.r, y: y(1) - 3, "text-anchor": "end", "font-size": 10, fill: "var(--text-secondary)" }, "gemiddeld = 1"));
  vals.forEach((v, h) => {
    const cur = h === now.getHours();
    const g = sv("g", { tabindex: "0" });
    g.append(sv("rect", { x: m.l + h * bw, y: m.t, width: bw, height: H - m.t - m.b, fill: "transparent" }));
    g.append(sv("rect", { x: m.l + h * bw + 1, y: y(v), width: Math.max(1, bw - 2), height: H - m.b - y(v), rx: 2,
      fill: "var(--series-1)", opacity: cur ? 1 : 0.45 }));
    attachTip(g, [`${String(h).padStart(2, "0")}:00–${String((h + 1) % 24).padStart(2, "0")}:00 (lokale tijd)${cur ? " · nu" : ""}`,
      ["volatiliteit t.o.v. gemiddeld", `×${NF(2).format(v)}`]]);
    svgEl.append(g);
    if (h % 6 === 0) svgEl.append(sv("text", { x: m.l + h * bw + 1, y: H - 3, "font-size": 10, fill: "var(--text-secondary)" }, `${h}:00`));
  });
  const dayType = [0, 6].includes(now.getUTCDay()) ? "weekend" : "werkdag";
  svgEl.append(sv("text", { x: m.l, y: 10, "font-size": 10, fill: "var(--text-secondary)" }, `vandaag (${dayType}) · nu ×${NF(2).format(vals[now.getHours()])}`));
}

// ------------------------------------------------------------------ richtingsmodel-paneel
let contribRows = null;
function renderContrib() {
  const box = $("contrib"), d = S.dir;
  if (!d) { box.textContent = "Modelparameters ontbreken nog (volgende server-run)."; return; }
  if (!contribRows) {
    const order = [...d.contrib].sort((p, q) => Math.abs(q.w) - Math.abs(p.w)).slice(0, 9).map((c) => c.name);
    box.replaceChildren();
    contribRows = order.map((name) => {
      const r = el("div", { class: "r" }), nm = el("div", { class: "nm" }, FEATURE_NL[name] || name);
      const track = el("div", { class: "track" }), fill = el("div", { class: "fill" }), val = el("div", { class: "val" });
      track.append(fill); r.append(nm, track, val); box.append(r);
      return { name, fill, val, nm };
    });
    const r = el("div", { class: "r" }), nm = el("div", { class: "nm" }, "Overige features");
    const track = el("div", { class: "track" }), fill = el("div", { class: "fill" }), val = el("div", { class: "val" });
    track.append(fill); r.append(nm, track, val); box.append(r);
    contribRows.push({ name: "__rest", fill, val, nm });
  }
  const byName = Object.fromEntries(d.contrib.map((c) => [c.name, c]));
  const shown = new Set(contribRows.map((r) => r.name));
  const rest = d.contrib.filter((c) => !shown.has(c.name)).reduce((s, c) => s + c.c, 0);
  const vals = contribRows.map((r) => (r.name === "__rest" ? rest : byName[r.name].c));
  const maxAbs = Math.max(0.02, ...vals.map(Math.abs));
  contribRows.forEach((r, k) => {
    const v = vals[k], w = (Math.abs(v) / maxAbs) * 50;
    r.fill.style.left = (v >= 0 ? 50 : 50 - w) + "%";
    r.fill.style.width = w + "%";
    r.fill.style.backgroundColor = v >= 0 ? "var(--up)" : "var(--down)";
    r.val.textContent = (v >= 0 ? "+" : "−") + NF(3).format(Math.abs(v));
    if (r.name !== "__rest") r.nm.title = `${FEATURE_NL[r.name] || r.name}: waarde ${NF(3).format(byName[r.name].x)}, gestandaardiseerd ${NF(2).format(byName[r.name].xs)}, gewicht ${NF(3).format(byName[r.name].w)}`;
  });
  renderSigmoid();
  const dirScale = S.model?.dir_scale ?? S.latest?.learning?.calibration?.dir_scale ?? 0.5;
  $("dirFormula").textContent = `z = ${NF(3).format(d.intercept)} (basis) + Σ bijdragen = ${NF(3).format(d.z)}  →  σ(z) = ${pct(d.pRaw)}  →  × vertrouwen ${NF(2).format(dirScale)}  =  ${pct(d.p)}`;
  $("pLive").textContent = pct(d.p);
  $("pLiveSub").textContent = `ruw model ${pct(d.pRaw)} · ${S.latest?.model_verdict?.split(" – ")[0] || ""}`;
}
let sigBuilt = false;
function renderSigmoid() {
  const svgEl = $("sigmoid"), d = S.dir; if (!d) return;
  const W = 300, H = 92, m = { l: 34, r: 10, t: 8, b: 20 }, zMax = 2;
  const x = (z) => m.l + ((z + zMax) / (2 * zMax)) * (W - m.l - m.r);
  const y = (p) => m.t + (1 - p) * (H - m.t - m.b);
  if (!sigBuilt) {
    svgEl.replaceChildren();
    for (const p of [0, 0.5, 1]) {
      svgEl.append(sv("line", { x1: m.l, x2: W - m.r, y1: y(p), y2: y(p), stroke: "var(--grid)" }));
      svgEl.append(sv("text", { x: m.l - 4, y: y(p) + 4, "text-anchor": "end", "font-size": 10, fill: "var(--text-secondary)" }, `${p * 100}%`));
    }
    let dd = "";
    for (let z = -zMax; z <= zMax + 1e-9; z += 0.05) dd += `${dd ? "L" : "M"}${x(z).toFixed(1)},${y(1 / (1 + Math.exp(-z))).toFixed(1)}`;
    svgEl.append(sv("path", { d: dd, fill: "none", stroke: "var(--series-1)", "stroke-width": 2 }));
    svgEl.append(sv("text", { x: W - m.r, y: H - 4, "text-anchor": "end", "font-size": 10, fill: "var(--text-secondary)" }, "z (som van bijdragen) →"));
    svgEl.append(sv("text", { x: m.l, y: H - 4, "font-size": 10, fill: "var(--text-secondary)" }, "−2"));
    const g = sv("g", { id: "sigDot", style: "transition: transform .6s" });
    g.append(sv("line", { x1: 0, x2: 0, y1: 0, y2: 0, id: "sigDrop", stroke: "var(--text-secondary)", "stroke-dasharray": "0" }));
    g.append(sv("circle", { r: 5, fill: "var(--series-1)", stroke: "var(--surface-1)", "stroke-width": 2 }));
    svgEl.append(g);
    sigBuilt = true;
  }
  const zc = Math.max(-zMax, Math.min(zMax, d.z));
  $("sigDot").style.transform = `translate(${x(zc)}px, ${y(d.pRaw)}px)`;
}

// ------------------------------------------------------------------ indicatoren-paneel
let lastVotes = [];
function renderIndicators() {
  if (!S.ind || !S.sc) return;
  const ind = S.ind, i = S.candles.length - 1;
  const rsi = ind.rsi14[i];
  $("rsiNeedle").style.transform = `rotate(${-90 + (rsi / 100) * 180}deg)`;
  $("rsiVal").textContent = `${NF(1).format(rsi)} ${rsi >= 70 ? "· overbought" : rsi <= 30 ? "· oversold" : ""}`;
  // MACD sparkline
  const mh = ind.macd_hist.slice(-48), mx = Math.max(...mh.map(Math.abs)) || 1, sp = $("macdSpark");
  sp.replaceChildren(sv("line", { x1: 0, x2: 150, y1: 29, y2: 29, stroke: "var(--axis)", "stroke-width": .5 }));
  mh.forEach((v, k) => {
    const h = (Math.abs(v) / mx) * 27, bw = 150 / mh.length;
    sp.append(sv("rect", { x: k * bw + .4, y: v >= 0 ? 29 - h : 29, width: Math.max(.6, bw - .8), height: Math.max(.5, h), fill: v >= 0 ? "var(--up)" : "var(--down)", opacity: k === mh.length - 1 ? 1 : .7 }));
  });
  $("macdVal").textContent = `${NF(2).format(mh[mh.length - 1])} ${mh[mh.length - 1] > 0 ? "· positief" : "· negatief"}`;
  const pb = ind.bb_pctb[i];
  $("pctbMark").style.left = `${Math.max(0, Math.min(100, ((pb + 0.1) / 1.2) * 100))}%`;
  $("pctbVal").textContent = `${NF(2).format(pb)} ${pb > 1 ? "· boven bovenband" : pb < 0 ? "· onder onderband" : ""}`;
  const sg = ind.sigma.slice(-72).map((v) => v * 100), smin = Math.min(...sg), smax = Math.max(...sg);
  const ss = $("sigmaSpark");
  const pts = sg.map((v, k) => `${(k / (sg.length - 1)) * 150},${38 - ((v - smin) / (smax - smin || 1)) * 34}`).join(" ");
  ss.replaceChildren(sv("polyline", { points: pts, fill: "none", stroke: "var(--series-1)", "stroke-width": 1.5, "vector-effect": "non-scaling-stroke" }));
  $("sigmaVal").textContent = `${NF(3).format(sg[sg.length - 1])}% per candle`;
  // scorekaart
  const sc = S.sc, ul = $("votes");
  const prev = lastVotes;
  ul.replaceChildren();
  sc.votes.forEach((v, k) => {
    const li = el("li"); li.append(el("span", {}, v.txt), el("span", { class: "v " + (v.v > 0 ? "ok" : v.v < 0 ? "no" : "") }, v.v > 0 ? "▲ +1" : v.v < 0 ? "▼ −1" : "0"));
    if (prev[k] !== undefined && prev[k] !== v.v) { li.classList.add("flash"); setTimeout(() => li.classList.remove("flash"), 900); }
    ul.append(li);
  });
  lastVotes = sc.votes.map((v) => v.v);
  $("scoreTotal").textContent = `· totaal ${sc.score > 0 ? "+" : ""}${sc.score} → ${sc.bias}`;
  $("bias").textContent = sc.bias;
  $("biasSub").textContent = `score ${sc.score > 0 ? "+" : ""}${sc.score} · trend 5m ${sc.trend5m}`;
  // prijs-delta
  const c = S.candles, last = c[c.length - 1], d24 = c[Math.max(0, c.length - 289)];
  $("priceDelta").textContent = `candle ${signed((last.c / last.o - 1) * 100, 3)} · 24u ${signed((last.c / d24.c - 1) * 100)} · RSI ${NF(0).format(rsi)}`;
}

// ------------------------------------------------------------------ tabellen
function renderDist() {
  const b = S.bands, sim = S.sim; if (!b || !sim) return;
  const t = $("dist"); t.replaceChildren();
  const head = el("tr");
  ["Horizon", "5%", "25%", "mediaan", "75%", "95%"].forEach((h, k) => head.append(el("th", { class: k ? "n" : "" }, h)));
  t.append(head);
  for (const step of [1, 3, 6, 12, 24].filter((s) => s <= sim.H)) {
    const r = el("tr"), q = b[step];
    r.append(el("td", {}, `${step * 5} min`));
    [q.p05, q.p25, q.p50, q.p75, q.p95].forEach((v) => r.append(el("td", { class: "n" }, usd(v))));
    t.append(r);
  }
  const h60 = b[Math.min(12, sim.H)];
  $("band60").textContent = `${usd(h60.p05)} – ${usd(h60.p95)}`;
  $("band60Sub").textContent = `${Math.min(12, sim.H) * 5} min · ${NF(0).format(sim.nPaths)} paden · gekalibreerd per band`;
  renderFutures();
}
function renderFutures() {
  const dl = $("futures"); if (!dl) return;
  dl.replaceChildren();
  const add = (k, v) => dl.append(el("dt", {}, k), el("dd", {}, v));
  const sim = S.sim;
  if (sim) {
    const step = sim.H, s0 = sim.s0;
    for (const p of [0.25, 0.5, 1]) add(`Kans ±${NF(2).format(p)}% te raken (${step * 5} min)`,
      `↑ ${pct(E.touchProb(sim, step, s0 * (1 + p / 100)), 0)} · ↓ ${pct(E.touchProb(sim, step, s0 * (1 - p / 100)), 0)}`);
    if (S.levels?.resistance[0]) add(`Kans weerstand ${usd(S.levels.resistance[0].v)}`, pct(E.touchProb(sim, step, S.levels.resistance[0].v), 0));
    if (S.levels?.support[0]) add(`Kans steun ${usd(S.levels.support[0].v)}`, pct(E.touchProb(sim, step, S.levels.support[0].v), 0));
  }
  const d = S.deriv;
  if (d && d.open_interest_usd != null) {
    if (d.funding_rate != null) add(`Funding rate${d.live ? " (live)" : ""}`, `${signed(d.funding_rate * 100, 4)} per 8 uur`);
    if (d.premium != null) add("Premium t.o.v. index", signed(d.premium * 100, 4));
    add("Open interest", `${NF(0).format(d.open_interest_btc)} BTC ≈ $${NF(2).format(d.open_interest_usd / 1e9)} mld`);
  }
}

let tapeLast = 0;
function renderTape(now) {
  if (!S.tapeQueue.length || now - tapeLast < 120) return;
  tapeLast = now;
  const ul = $("tape");
  const batch = S.tapeQueue.splice(0, S.tapeQueue.length).slice(-4);
  for (const t of batch) {
    const li = el("li", { class: t.szBtc >= 1 ? "big-trade" : "" });
    li.append(el("span", { class: "t" }, fmtTimeS.format(new Date(t.ts))),
      el("span", { class: t.side === "buy" ? "ok" : "no" }, t.side === "buy" ? "▲" : "▼"),
      el("span", {}, `${usd(t.px, 1)} ${t.side === "buy" ? "koop" : "verkoop"}`),
      el("span", { class: "sz" }, `${NF(t.szBtc < 0.1 ? 3 : 2).format(t.szBtc)} BTC`));
    ul.prepend(li);
  }
  while (ul.children.length > 12) ul.lastChild.remove();
  $("tapeSub").textContent = `${S.demo ? "Gesimuleerde" : "Elke"} trade op OKX ${INST}, zodra hij plaatsvindt · ${NF(0).format(S.trades)} ontvangen · vetgedrukt = ≥ 1 BTC`;
}

// ------------------------------------------------------------------ zelflerend: grafieken
function renderEval() {
  const all = S.evaluated || [];
  const rows = (S.evalMode === "live" ? all.filter((r) => r.live) : all).slice(-96);
  const tr = S.latest?.learning?.track_record || {}, cal = S.latest?.learning?.calibration || {};
  const inside = rows.filter((r) => r.realized_60m >= r.p05_60m && r.realized_60m <= r.p95_60m).length;
  $("evalSub").textContent = rows.length
    ? `${rows.length} recente voorspellingen (${S.evalMode === "live" ? "alleen live" : "live + teruggespeeld"}): de 90%-band bevatte de werkelijke prijs na 60 min in ${pct(inside / rows.length, 0)} van de gevallen (doel 90%).`
    : "Nog geen getoetste voorspellingen in deze selectie; live voorspellingen verschijnen hier zodra hun uitkomst bekend is.";
  drawBandChart(rows);
  drawCalChart((S.evalMode === "live" ? all.filter((r) => r.live) : all).filter((r) => r.realized_dir != null));
  const box = $("learnTiles"); box.replaceChildren();
  const tile = (label, value, sub) => { const t = el("div", { class: "tile" }); t.style.flex = "1 1 170px"; t.append(el("div", { class: "label" }, label), el("div", { class: "value" }, value), el("div", { class: "delta" }, sub)); box.append(t); };
  if (tr.n_direction) tile("Richting goed voorspeld", pct(tr.hit_rate), `${NF(0).format(tr.n_direction)} getoetst · ${tr.n_live} live`);
  if (tr.n_range) tile("Koers binnen 90%-band", pct(tr.coverage_90, 0), "doel 90%");
  if (cal.k50 !== undefined) tile("Kalibratie 50%- / 90%-band", `×${NF(2).format(cal.k50)} / ×${NF(2).format(cal.k90)}`,
    `geleerde afstanden ${NF(2).format(cal.c50)}σ / ${NF(2).format(cal.c90)}σ`);
  else if (cal.vol_scale) tile("Geleerde bandbreedtefactor", `×${NF(2).format(cal.vol_scale)}`, "oude kalibratie");
  if (cal.dir_scale !== undefined) tile("Vertrouwen in richtingskans", NF(2).format(cal.dir_scale), "0 = negeren · 1 = volledig");
  const sb = S.latest?.learning?.scoreboard;
  if (sb?.last7) tile("Vaardigheid laatste 7 dagen", `${sb.last7.skill >= 0 ? "+" : "−"}${pct(Math.abs(sb.last7.skill))}`,
    `t.o.v. naïeve band · ${NF(0).format(sb.last7.n)} voorspellingen`);
  drawSkillChart(sb?.daily || []);
  const of = S.latest?.learning?.orderflow;
  if (of?.status === "verzamelen") tile("Orderflow-data verzameld", `${NF(1).format(of.days || 0)} van ${NF(0).format((of.needed || 2000) / 288)} dagen`,
    "eerlijke test volgt zodra er genoeg data is");
  else if (of?.status === "getest") tile("Orderflow-test", `${pct(of.base.accuracy)} → ${pct(of.with_orderflow.accuracy)}`,
    `${of.verdict} (${NF(1).format(of.days)} dagen)`);
}

function attachTip(node, lines) {
  const show = (e) => {
    const tip = $("tip"); tip.replaceChildren();
    tip.append(el("div", { class: "t" }, lines[0]));
    for (const [k, v] of lines.slice(1)) { const r = el("div", { class: "r" }); r.append(el("span", {}, k), el("b", {}, v)); tip.append(r); }
    tip.hidden = false;
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    const cx = e.clientX ?? node.getBoundingClientRect().left, cy = e.clientY ?? node.getBoundingClientRect().top;
    tip.style.left = Math.min(innerWidth - tw - 8, Math.max(8, cx + 14)) + "px";
    tip.style.top = Math.max(8, Math.min(innerHeight - th - 8, cy - th - 10)) + "px";
  };
  node.addEventListener("pointermove", show);
  node.addEventListener("focus", show);
  node.addEventListener("pointerleave", () => ($("tip").hidden = true));
  node.addEventListener("blur", () => ($("tip").hidden = true));
}

function drawBandChart(rows) {
  const svgEl = $("bandChart"); svgEl.replaceChildren();
  const W = svgEl.clientWidth || 500, H = 240, m = { l: 44, r: 8, t: 10, b: 22 };
  svgEl.setAttribute("viewBox", `0 0 ${W} ${H}`);
  if (!rows.length) return;
  const rel = (v, r) => (v / r.price - 1) * 100;
  let lo = 0, hi = 0;
  for (const r of rows) { lo = Math.min(lo, rel(r.p05_60m, r), rel(r.realized_60m, r)); hi = Math.max(hi, rel(r.p95_60m, r), rel(r.realized_60m, r)); }
  const pad = (hi - lo) * .06; lo -= pad; hi += pad;
  const y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b);
  const slot = (W - m.l - m.r) / rows.length, x = (k) => m.l + (k + .5) * slot;
  for (const v of niceTicks(lo, hi, 5)) {
    svgEl.append(sv("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: v === 0 ? "var(--axis)" : "var(--grid)" }));
    svgEl.append(sv("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end", "font-size": 11, fill: "var(--text-secondary)" }, `${v > 0 ? "+" : ""}${NF(1).format(v)}%`));
  }
  const bw = Math.max(2, Math.min(6, slot * .55));
  rows.forEach((r, k) => {
    const ins = r.realized_60m >= r.p05_60m && r.realized_60m <= r.p95_60m;
    const g = sv("g", { tabindex: "0" });
    g.append(sv("rect", { x: x(k) - slot / 2, y: m.t, width: slot, height: H - m.t - m.b, fill: "transparent" }));
    g.append(sv("rect", { x: x(k) - bw / 2, y: y(rel(r.p95_60m, r)), width: bw, height: Math.max(1, y(rel(r.p05_60m, r)) - y(rel(r.p95_60m, r))), rx: 1.5, fill: "var(--series-1)", opacity: .25 }));
    if (r.p25_60m != null) g.append(sv("rect", { x: x(k) - bw / 2, y: y(rel(r.p75_60m, r)), width: bw, height: Math.max(1, y(rel(r.p25_60m, r)) - y(rel(r.p75_60m, r))), rx: 1.5, fill: "var(--series-1)", opacity: .55 }));
    g.append(sv("circle", { cx: x(k), cy: y(rel(r.realized_60m, r)), r: ins ? 3 : 4, fill: ins ? "var(--text-secondary)" : "var(--down)", stroke: "var(--surface-1)", "stroke-width": 1.5 }));
    attachTip(g, [fmtDT.format(new Date(r.t)) + (r.live ? " · live" : " · teruggespeeld"),
      ["prijs toen", usd(r.price)], ["90%-band", `${usd(r.p05_60m)} – ${usd(r.p95_60m)}`],
      ["na 60 min", usd(r.realized_60m)], ["binnen band", ins ? "✓ ja" : "✗ nee"]]);
    svgEl.append(g);
  });
  const first = new Date(rows[0].t), last = new Date(rows[rows.length - 1].t);
  svgEl.append(sv("text", { x: m.l, y: H - 5, "font-size": 11, fill: "var(--text-secondary)" }, fmtDT.format(first)));
  svgEl.append(sv("text", { x: W - m.r, y: H - 5, "text-anchor": "end", "font-size": 11, fill: "var(--text-secondary)" }, fmtDT.format(last)));
}

function drawSkillChart(daily) {
  const svgEl = $("skillChart"); if (!svgEl) return;
  svgEl.replaceChildren();
  const W = svgEl.clientWidth || 600, H = 200, m = { l: 44, r: 12, t: 12, b: 24 };
  svgEl.setAttribute("viewBox", `0 0 ${W} ${H}`);
  if (!daily.length) {
    svgEl.append(sv("text", { x: W / 2, y: H / 2, "text-anchor": "middle", "font-size": 12, fill: "var(--text-secondary)" }, "Nog geen dagscores; die verschijnen na de eerste getoetste voorspellingen."));
    return;
  }
  const vals = daily.map((d) => d.skill * 100);
  let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  const pad = Math.max(2, (hi - lo) * 0.12); lo -= pad; hi += pad;
  const x = (k) => m.l + (daily.length === 1 ? (W - m.l - m.r) / 2 : (k / (daily.length - 1)) * (W - m.l - m.r));
  const y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b);
  for (const raw of niceTicks(lo, hi, 4)) {
    const v = Math.abs(raw) < 1e-9 ? 0 : raw;
    svgEl.append(sv("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: v === 0 ? "var(--axis)" : "var(--grid)" }));
    svgEl.append(sv("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end", "font-size": 11, fill: "var(--text-secondary)" }, `${v > 0 ? "+" : v < 0 ? "−" : ""}${NF(0).format(Math.abs(v))}%`));
  }
  // modelversie-wissels
  daily.forEach((d, k) => {
    if (k && d.version !== daily[k - 1].version) {
      svgEl.append(sv("line", { x1: x(k - 0.5), x2: x(k - 0.5), y1: m.t, y2: H - m.b, stroke: "var(--text-secondary)", opacity: .5 }));
      svgEl.append(sv("text", { x: x(k - 0.5) + 4, y: m.t + 10, "font-size": 10, fill: "var(--text-secondary)" }, `model ${d.version}`));
    }
  });
  svgEl.append(sv("polyline", { points: daily.map((d, k) => `${x(k)},${y(vals[k])}`).join(" "), fill: "none", stroke: "var(--series-1)", "stroke-width": 2, "stroke-linejoin": "round" }));
  daily.forEach((d, k) => {
    const g = sv("g", { tabindex: "0" });
    g.append(sv("circle", { cx: x(k), cy: y(vals[k]), r: 12, fill: "transparent" }));
    g.append(sv("circle", { cx: x(k), cy: y(vals[k]), r: 4, fill: "var(--series-1)", stroke: "var(--surface-1)", "stroke-width": 2 }));
    attachTip(g, [`${d.date} · model ${d.version}${d.live_share > 0 ? ` · ${pct(d.live_share, 0)} live` : " · teruggespeeld"}`,
      ["vaardigheid", `${d.skill >= 0 ? "+" : "−"}${pct(Math.abs(d.skill))}`], ["voorspellingen", NF(0).format(d.n)],
      ["50%- / 90%-band raak", `${pct(d.cov50, 0)} / ${pct(d.cov90, 0)}`]]);
    svgEl.append(g);
  });
  svgEl.append(sv("text", { x: m.l, y: H - 5, "font-size": 11, fill: "var(--text-secondary)" }, daily[0].date));
  svgEl.append(sv("text", { x: W - m.r, y: H - 5, "text-anchor": "end", "font-size": 11, fill: "var(--text-secondary)" }, daily[daily.length - 1].date));
}

function drawCalChart(rows) {
  const svgEl = $("calChart"); svgEl.replaceChildren();
  const W = svgEl.clientWidth || 500, H = 240, m = { l: 44, r: 12, t: 10, b: 30 };
  svgEl.setAttribute("viewBox", `0 0 ${W} ${H}`);
  if (rows.length < 20) {
    svgEl.append(sv("text", { x: W / 2, y: H / 2, "text-anchor": "middle", "font-size": 12, fill: "var(--text-secondary)" }, "Te weinig getoetste voorspellingen voor een kalibratiegrafiek."));
    return;
  }
  const sorted = [...rows].sort((a, b) => a.p_up - b.p_up), nb = 6, bins = [];
  for (let k = 0; k < nb; k++) {
    const part = sorted.slice(Math.floor((k * sorted.length) / nb), Math.floor(((k + 1) * sorted.length) / nb));
    if (!part.length) continue;
    bins.push({ p: part.reduce((s, r) => s + r.p_up, 0) / part.length, f: part.reduce((s, r) => s + r.realized_dir, 0) / part.length, n: part.length });
  }
  const span = Math.max(0.06, ...bins.map((b) => Math.max(Math.abs(b.p - .5), Math.abs(b.f - .5)))) + 0.02;
  const lo = 0.5 - span, hi = 0.5 + span;
  const x = (v) => m.l + ((v - lo) / (hi - lo)) * (W - m.l - m.r), y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b);
  for (const v of niceTicks(lo, hi, 4)) {
    svgEl.append(sv("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: "var(--grid)" }));
    svgEl.append(sv("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end", "font-size": 11, fill: "var(--text-secondary)" }, pct(v, 0)));
    svgEl.append(sv("text", { x: x(v), y: H - 14, "text-anchor": "middle", "font-size": 11, fill: "var(--text-secondary)" }, pct(v, 0)));
  }
  svgEl.append(sv("text", { x: (W + m.l) / 2, y: H - 1, "text-anchor": "middle", "font-size": 11, fill: "var(--text-secondary)" }, "voorspelde kans op stijging →"));
  svgEl.append(sv("line", { x1: x(lo), y1: y(lo), x2: x(hi), y2: y(hi), stroke: "var(--axis)" }));
  svgEl.append(sv("text", { x: x(hi) - 4, y: y(hi) + 14, "text-anchor": "end", "font-size": 10, fill: "var(--text-secondary)" }, "perfect gekalibreerd"));
  svgEl.append(sv("polyline", { points: bins.map((b) => `${x(b.p)},${y(b.f)}`).join(" "), fill: "none", stroke: "var(--series-1)", "stroke-width": 2 }));
  for (const b of bins) {
    const g = sv("g", { tabindex: "0" });
    g.append(sv("circle", { cx: x(b.p), cy: y(b.f), r: 12, fill: "transparent" }));
    g.append(sv("circle", { cx: x(b.p), cy: y(b.f), r: 5, fill: "var(--series-1)", stroke: "var(--surface-1)", "stroke-width": 2 }));
    attachTip(g, [`${b.n} voorspellingen`, ["gemiddeld voorspeld", pct(b.p)], ["werkelijk gestegen", pct(b.f)]]);
    svgEl.append(g);
  }
}

function renderRecent() {
  const t = $("recent"); t.replaceChildren();
  const rows = S.latest?.learning?.recent_live || [];
  if (!rows.length) { const r = el("tr"); r.append(el("td", {}, "Nog geen live voorspellingen met bekende uitkomst.")); t.append(r); return; }
  const head = el("tr");
  ["Tijd", "Prijs", "Kans hoger", "Uitkomst 15 min", "Prijs na 60 min", "Binnen 90%-band"].forEach((h, k) => head.append(el("th", { class: [1, 2, 4].includes(k) ? "n" : "" }, h)));
  t.append(head);
  const mark = (ok, y, n) => el("span", { class: ok ? "ok" : "no" }, ok ? `✓ ${y}` : `✗ ${n}`);
  for (const r of rows) {
    const tr = el("tr");
    tr.append(el("td", {}, fmtDT.format(new Date(r.time))), el("td", { class: "n" }, usd(r.price)), el("td", { class: "n" }, pct(r.p_up)));
    const o = el("td"); o.append(document.createTextNode(r.went_up ? "▲ hoger · " : "▼ lager · "), mark(r.correct, "goed", "fout")); tr.append(o);
    tr.append(el("td", { class: "n" }, r.price_60m == null ? "—" : usd(r.price_60m)));
    const ins = el("td"); if (r.inside_90 == null) ins.textContent = "—"; else ins.append(mark(r.inside_90, "ja", "nee")); tr.append(ins);
    t.append(tr);
  }
  const agent = S.latest?.agent_summary;
  $("agentCard").hidden = !agent;
  if (agent) $("agent").textContent = agent;
}

// ------------------------------------------------------------------ bediening
function seg(id, onPick) {
  const box = $(id);
  box.addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    box.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b ? "true" : "false"));
    onPick(b.dataset.v);
  });
}
seg("rangeSeg", (v) => { S.view = +v; S.dirty = true; });
seg("horizonSeg", (v) => { S.H = +v; runSimulation(true); });
seg("pathsSeg", (v) => { S.nPaths = +v; runSimulation(true); });
seg("evalSeg", (v) => { S.evalMode = v; renderEval(); });
$("overlays").addEventListener("click", (e) => {
  const b = e.target.closest(".chip"); if (!b) return;
  const on = b.getAttribute("aria-pressed") !== "true";
  b.setAttribute("aria-pressed", on); S.overlays[b.dataset.k] = on; S.dirty = true;
});
function updateVolLabel() {
  $("volVal").textContent = `×${NF(2).format(S.volScale)}${Math.abs(S.volScale - 1) < 0.005 ? " (geen extra)" : " extra"}`;
}
let volTimer = null;
$("volSlider").addEventListener("input", (e) => {
  S.volScale = +e.target.value; updateVolLabel();
  clearTimeout(volTimer); volTimer = setTimeout(() => runSimulation(false), 60);
});
$("simBtn").addEventListener("click", () => runSimulation(true));

// ------------------------------------------------------------------ hoofdlus
let lastPanel = 0, lastQuickSim = 0;
function loop(now) {
  try { frame(now); } catch (err) { console.error(err); }
  requestAnimationFrame(loop);
}
function frame(now) {
  if (S.needsRecompute && now - lastPanel > 400) {
    recompute();
    lastPanel = now;
    renderContrib(); renderIndicators();
  }
  if (!S.anim && S.ind && now - lastQuickSim > 10000) { lastQuickSim = now; runSimulation(false); }
  if (S.anim) {
    const prog = Math.max(0, Math.min(1, (now - S.anim.start) / S.anim.dur));
    renderMC(prog);
    if (prog >= 1 && !S.anim.done) { S.anim.done = true; S.sim = S.anim.sim; S.bands = S.anim.bands; renderDist(); $("simBtn").disabled = false; lastQuickSim = now; }
    S.dirty = true;
  }
  renderTape(now);
  // pulserende stip: elk frame tekenen zolang het tabblad zichtbaar is
  drawChart(now);
}

function renderAll() {
  recompute(); renderContrib(); renderIndicators(); renderEval(); renderRecent(); renderFutures();
  S.dirty = true;
}

async function main() {
  if (chart.parentElement.clientWidth < 600) { // mobiel: standaard 3 uur
    S.view = 36;
    $("rangeSeg").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.v === "36" ? "true" : "false"));
  }
  try {
    await loadStatic();
  } catch (err) {
    $("meta").textContent = `Kon de serverdata niet laden (${err.message}).`;
    return;
  }
  resize();
  new ResizeObserver(() => { resize(); renderEval(); }).observe(chart.parentElement);
  if (!S.demo) {
    try { mergeCandles(await restCandles(300)); } catch { /* alleen data van de server-run */ }
  }
  renderAll();
  setPrice(S.candles[S.candles.length - 1].c);
  runSimulation(true);
  connectWS();
  if (!S.demo) { restDerivatives(); setInterval(restDerivatives, 60000); setInterval(() => restCandles(300).then(mergeCandles).catch(() => {}), 60000); }
  // ververs servergegevens (model, track record) zodra er een nieuwe run is
  setInterval(async () => {
    try {
      const l = await getJSON("latest.json" + bust());
      if (l.generated_at !== S.latest.generated_at) {
        S.latest = l;
        try { S.model = await getJSON("model.json" + bust()); S.evaluated = await getJSON("evaluated.json" + bust()); } catch { /* */ }
        contribRows = null; renderAll();
      }
    } catch { /* */ }
  }, 120000);
  setInterval(tickClock, 250); tickClock();
  requestAnimationFrame(loop);
}
main();
