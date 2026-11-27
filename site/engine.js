/* btc5m rekenmotor voor de browser: dezelfde formules als de Python-code (btc5m/*.py),
 * zodat het dashboard indicatoren, richtingskans en Monte Carlo live kan narekenen.
 * Werkt ook in Node (voor de pariteitstest tegen Python). */
(function (root) {
  "use strict";
  const LAMBDA = 0.94;
  const nan = NaN;

  // ---- basisreeksen -------------------------------------------------------
  function ema(x, n) { // pandas ewm(span=n, adjust=False)
    const a = 2 / (n + 1), out = new Array(x.length);
    let y = nan;
    for (let i = 0; i < x.length; i++) {
      const v = x[i];
      if (Number.isNaN(y)) y = v; else if (!Number.isNaN(v)) y = a * v + (1 - a) * y;
      out[i] = y;
    }
    return out;
  }
  function ewmAlpha(x, a) { // ewm(alpha=a, adjust=False), start bij eerste niet-NaN
    const out = new Array(x.length); let y = nan;
    for (let i = 0; i < x.length; i++) {
      const v = x[i];
      if (Number.isNaN(y)) y = v; else if (!Number.isNaN(v)) y = a * v + (1 - a) * y;
      out[i] = y;
    }
    return out;
  }
  function rolling(x, n, fn) {
    const out = new Array(x.length).fill(nan);
    for (let i = n - 1; i < x.length; i++) out[i] = fn(x.slice(i - n + 1, i + 1));
    return out;
  }
  const mean = (a) => a.reduce((s, v) => s + v, 0) / a.length;
  const std1 = (a) => { const m = mean(a); return Math.sqrt(a.reduce((s, v) => s + (v - m) ** 2, 0) / (a.length - 1)); };

  // ---- indicatoren (zie indicators.py) -------------------------------------
  function indicators(c) { // c: [{t,o,h,l,c,v}]
    const close = c.map((d) => d.c), n = c.length;
    const out = { close };
    for (const k of [9, 21, 50, 200]) out[`ema${k}`] = ema(close, k);
    const delta = close.map((v, i) => (i ? v - close[i - 1] : nan));
    const gain = ewmAlpha(delta.map((d) => (Number.isNaN(d) ? nan : Math.max(d, 0))), 1 / 14);
    const loss = ewmAlpha(delta.map((d) => (Number.isNaN(d) ? nan : Math.max(-d, 0))), 1 / 14);
    out.rsi14 = gain.map((g, i) => { const l = loss[i]; if (!(l > 0)) return 100; return 100 - 100 / (1 + g / l); });
    const e12 = ema(close, 12), e26 = ema(close, 26);
    out.macd = close.map((_, i) => e12[i] - e26[i]);
    out.macd_signal = ema(out.macd, 9);
    out.macd_hist = out.macd.map((m, i) => m - out.macd_signal[i]);
    out.bb_mid = rolling(close, 20, mean);
    const sd = rolling(close, 20, std1);
    out.bb_up = out.bb_mid.map((m, i) => m + 2 * sd[i]);
    out.bb_low = out.bb_mid.map((m, i) => m - 2 * sd[i]);
    out.bb_pctb = close.map((v, i) => (v - out.bb_low[i]) / (out.bb_up[i] - out.bb_low[i]));
    out.bb_width = out.bb_mid.map((m, i) => (out.bb_up[i] - out.bb_low[i]) / m);
    const tr = c.map((d, i) => (i ? Math.max(d.h - d.l, Math.abs(d.h - c[i - 1].c), Math.abs(d.l - c[i - 1].c)) : d.h - d.l));
    out.atr14 = ewmAlpha(tr, 1 / 14);
    // dag-VWAP (UTC)
    out.vwap = new Array(n); let day = null, pv = 0, vv = 0;
    for (let i = 0; i < n; i++) {
      const d = c[i], dd = Math.floor(d.t / 86400000), typ = (d.h + d.l + d.c) / 3;
      if (dd !== day) { day = dd; pv = 0; vv = 0; }
      pv += typ * d.v; vv += d.v;
      out.vwap[i] = vv > 0 ? pv / vv : typ;
    }
    const vol = c.map((d) => d.v);
    const vm = rolling(vol, 48, mean), vs = rolling(vol, 48, std1);
    out.vol_z = vol.map((v, i) => (vs[i] > 0 ? (v - vm[i]) / vs[i] : nan));
    out.logret = close.map((v, i) => (i ? Math.log(v / close[i - 1]) : nan));
    out.sigma = ewmaSigma(out.logret);
    return out;
  }
  function ewmaSigma(logret, lam = LAMBDA) {
    const out = new Array(logret.length); let v = nan;
    for (let i = 0; i < logret.length; i++) {
      const r = Number.isNaN(logret[i]) ? 0 : logret[i], r2 = r * r;
      v = Number.isNaN(v) ? r2 : lam * v + (1 - lam) * r2;
      out[i] = Math.sqrt(v);
    }
    return out;
  }

  // ---- features richtingsmodel (zie forecast.build_features) ----------------
  const clip = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  function features(c, ind, i = c.length - 1) {
    const sig = ind.sigma[i] || nan, lc = (j) => Math.log(c[j].c);
    const date = new Date(c[i].t), hour = date.getUTCHours() + date.getUTCMinutes() / 60;
    const f = {};
    for (const k of [1, 3, 6, 12]) f[`r${k}`] = i >= k ? (lc(i) - lc(i - k)) / (sig * Math.sqrt(k)) : nan;
    f.rsi = (ind.rsi14[i] - 50) / 50;
    f.macd = ind.macd_hist[i] / ind.atr14[i];
    f.pctb = ind.bb_pctb[i] - 0.5;
    f.volz = clip(ind.vol_z[i], -4, 4);
    f.d_ema21 = (c[i].c - ind.ema21[i]) / ind.atr14[i];
    f.ema_spread = (ind.ema9[i] - ind.ema21[i]) / ind.atr14[i];
    f.d_vwap = clip((c[i].c - ind.vwap[i]) / ind.atr14[i], -10, 10);
    f.bbw = Math.log(ind.bb_width[i]);
    f.hour_sin = Math.sin(2 * Math.PI * hour / 24);
    f.hour_cos = Math.cos(2 * Math.PI * hour / 24);
    return f;
  }
  function direction(f, params, dirScale) {
    const w = params.w; let z = w[0];
    const contrib = params.features.map((name, k) => {
      const xs = (f[name] - params.mu[k]) / params.sd[k];
      const cval = w[k + 1] * xs; z += cval;
      return { name, x: f[name], xs, w: w[k + 1], c: cval };
    });
    const pRaw = 1 / (1 + Math.exp(-z));
    return { z, pRaw, p: 0.5 + dirScale * (pRaw - 0.5), contrib, intercept: w[0] };
  }

  // ---- Monte Carlo (zie forecast.monte_carlo) -----------------------------
  function mulberry32(seed) {
    return function () {
      seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  /** Simuleer `nPaths` paden van `H` stappen. Geeft Float64Array per stap met prijzen. */
  function simulate({ s0, sigma, volScale = 1, z, H, nPaths, seed = 7, lam = LAMBDA }) {
    const rnd = mulberry32(seed), var0 = (sigma * volScale) ** 2;
    const paths = Array.from({ length: H + 1 }, () => new Float64Array(nPaths));
    const hi = new Float64Array(nPaths).fill(s0), lo = new Float64Array(nPaths).fill(s0);
    const hiAt = [], loAt = [];
    paths[0].fill(s0);
    const varT = new Float64Array(nPaths).fill(var0), logp = new Float64Array(nPaths);
    for (let t = 1; t <= H; t++) {
      for (let k = 0; k < nPaths; k++) {
        const shock = z[(rnd() * z.length) | 0] * Math.sqrt(varT[k]);
        logp[k] += shock - 0.5 * varT[k];
        varT[k] = lam * varT[k] + (1 - lam) * shock * shock;
        const p = s0 * Math.exp(logp[k]);
        paths[t][k] = p;
        if (p > hi[k]) hi[k] = p;
        if (p < lo[k]) lo[k] = p;
      }
      hiAt.push(Float64Array.from(hi)); loAt.push(Float64Array.from(lo));
    }
    return { paths, hiAt, loAt, s0, H, nPaths };
  }
  function quantiles(arr, qs) {
    const s = Float64Array.from(arr).sort();
    return qs.map((q) => { // lineaire interpolatie zoals numpy.percentile
      const pos = q * (s.length - 1), lo = Math.floor(pos), hi = Math.ceil(pos);
      return s[lo] + (s[hi] - s[lo]) * (pos - lo);
    });
  }
  function bands(sim) {
    const out = [];
    for (let t = 0; t <= sim.H; t++) {
      const [p05, p25, p50, p75, p95] = quantiles(sim.paths[t], [0.05, 0.25, 0.5, 0.75, 0.95]);
      out.push({ step: t, p05, p25, p50, p75, p95 });
    }
    return out;
  }
  function touchProb(sim, step, level) {
    const arr = level >= sim.s0 ? sim.hiAt[step - 1] : sim.loAt[step - 1];
    let n = 0;
    for (let k = 0; k < arr.length; k++) if (level >= sim.s0 ? arr[k] >= level : arr[k] <= level) n++;
    return n / arr.length;
  }

  // ---- scorekaart & niveaus (zie signals.py) ------------------------------
  function resample(c, minutes) {
    const ms = minutes * 60000, out = [];
    for (const d of c) {
      const b = Math.floor(d.t / ms) * ms, last = out[out.length - 1];
      if (last && last.t === b) { last.h = Math.max(last.h, d.h); last.l = Math.min(last.l, d.l); last.c = d.c; last.v += d.v; }
      else out.push({ t: b, o: d.o, h: d.h, l: d.l, c: d.c, v: d.v });
    }
    return out;
  }
  function trendLabel(close, f, m, s) {
    if (close > f && f > m && m > s) return "sterk stijgend";
    if (close < f && f < m && m < s) return "sterk dalend";
    if (close > m && f > m) return "stijgend";
    if (close < m && f < m) return "dalend";
    return "zijwaarts/gemengd";
  }
  function htfTrends(c) {
    const res = {};
    for (const [label, min] of [["15m", 15], ["1u", 60]]) {
      const h = resample(c, min);
      if (h.length < 60) continue;
      const cl = h.map((d) => d.c), last = cl.length - 1;
      res[label] = trendLabel(cl[last], ema(cl, 9)[last], ema(cl, 21)[last], ema(cl, 50)[last]);
    }
    return res;
  }
  function scorecard(c, ind) {
    const i = c.length - 1, p = c[i].c, votes = [];
    const vote = (up, down, upTxt, downTxt) => { if (up) votes.push({ v: 1, txt: upTxt }); else if (down) votes.push({ v: -1, txt: downTxt }); else votes.push({ v: 0, txt: upTxt.replace(/boven|positief/, "≈") }); };
    vote(ind.ema9[i] > ind.ema21[i], ind.ema9[i] < ind.ema21[i], "EMA9 boven EMA21", "EMA9 onder EMA21");
    vote(p > ind.ema50[i], p < ind.ema50[i], "prijs boven EMA50", "prijs onder EMA50");
    vote(p > ind.ema200[i], p < ind.ema200[i], "prijs boven EMA200", "prijs onder EMA200");
    vote(ind.macd_hist[i] > 0, ind.macd_hist[i] < 0, "MACD-histogram positief", "MACD-histogram negatief");
    vote(p > ind.vwap[i], p < ind.vwap[i], "prijs boven dag-VWAP", "prijs onder dag-VWAP");
    const r = ind.rsi14[i];
    vote(r >= 55 && r < 70, r > 30 && r <= 45, "RSI bullish zone", "RSI bearish zone");
    vote(r <= 30, r >= 70, "RSI oversold", "RSI overbought");
    const htf = htfTrends(c);
    for (const [k, t] of Object.entries(htf)) vote(t.includes("stijgend"), t.includes("dalend"), `${k}-trend ${t}`, `${k}-trend ${t}`);
    const score = votes.reduce((s, v) => s + v.v, 0);
    const bias = score >= 4 ? "bullish" : score >= 2 ? "licht bullish" : score <= -4 ? "bearish" : score <= -2 ? "licht bearish" : "neutraal";
    return { votes, score, bias, htf, trend5m: trendLabel(p, ind.ema9[i], ind.ema21[i], ind.ema50[i]) };
  }
  function swingLevels(c, atr, lookback = 288, width = 6) {
    const w = c.slice(-lookback), price = w[w.length - 1].c, piv = [];
    for (let i = width; i < w.length - width; i++) {
      const seg = w.slice(i - width, i + width + 1);
      if (w[i].h === Math.max(...seg.map((d) => d.h))) piv.push(w[i].h);
      if (w[i].l === Math.min(...seg.map((d) => d.l))) piv.push(w[i].l);
    }
    piv.sort((a, b) => a - b);
    const clusters = [];
    for (const x of piv) {
      const last = clusters[clusters.length - 1];
      if (last && x - last[last.length - 1] <= 0.5 * atr) last.push(x); else clusters.push([x]);
    }
    const lv = clusters.map((cl) => ({ v: mean(cl), n: cl.length }));
    return {
      resistance: lv.filter((l) => l.v > price + 0.3 * atr).sort((a, b) => a.v - b.v).slice(0, 3),
      support: lv.filter((l) => l.v < price - 0.3 * atr).sort((a, b) => b.v - a.v).slice(0, 3),
    };
  }

  const api = { LAMBDA, ema, indicators, ewmaSigma, features, direction, simulate, quantiles, bands, touchProb,
    scorecard, swingLevels, resample, mulberry32 };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.BTC5M = api;
})(typeof self !== "undefined" ? self : this);
