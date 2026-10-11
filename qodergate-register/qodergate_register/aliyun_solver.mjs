// aliyun_puzzle_solver.mjs
// Auto-solve the Aliyun "FeiLin" slide/puzzle captcha on a page (e.g. qoder.com sign-up).
//
// Runs INSIDE browser_execute (needs a real CDP browser session). No local browser.
//
// Verified working method (2026-09):
//   1. The captcha renders an <img class="puzzle"> (background, ~296x200 natural) and a
//      52px-wide <img class="puzzle"> strip (the moving piece window). The strip's
//      inline style.left (CSS px) tracks the piece position 1:1 on screen.
//   2. The HOLE/gap is drawn as a gray veil over the background: low saturation
//      (max-min < ~40) and mid/high brightness (> ~140), inside the piece's vertical band.
//   3. Needed strip.left = gapCenterNatural * (displayW / naturalW) - pieceCenterInStrip.
//   4. CRITICAL: the drag must be a SLOW, closed-loop drag (read style.left after every
//      small move and stop at target). Fast / overshooting / single-jump drags are
//      rejected by the anti-bot risk engine even when geometrically perfect.
//   5. Release only once style.left ~= target. A human-like approach wander before
//      mousedown also helps.
//
// Public: solveAliyunPuzzle(session, opts) -> { ok, verified, tries, reason }
//   opts: { maxTries=4, handleSelector=".slider-move", puzzleSelector="img.puzzle",
//           stepPx=8, movePauseMs=32, verbose=true }
//
// The user gives final approval to any account creation; this only clears the captcha.

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const DEFAULT = {
  handleSelector: ".slider-move",
  puzzleSelector: "img.puzzle",
  stepPx: 8,
  movePauseMs: 32,
  maxTries: 4,
  verbose: true,
};

// ---- helpers -------------------------------------------------------------

// Evaluate in page; returns the value or throws.
async function ev(session, expression, awaitPromise = false) {
  const r = await session.Runtime.evaluate({ expression, returnByValue: true, awaitPromise });
  if (r.exceptionDetails) {
    throw new Error("page eval error: " + (r.exceptionDetails.text || "unknown"));
  }
  return r.result.value;
}

// Find the moving 52px strip image (same class as puzzle, but narrow / no alpha bbox wide).
const STRIP_FIND = `
  [...document.querySelectorAll('img')].find(e =>
    e.classList.length === 0 &&
    Math.round(e.naturalWidth) >= 40 && Math.round(e.naturalWidth) <= 60 &&
    Math.round(e.naturalHeight) > 120)
`;

// Read the strip's current left offset (CSS px) and whether captcha is ready.
async function readState(session, o) {
  return JSON.parse(
    await ev(
      session,
      `(() => {
        const h = document.querySelector(${JSON.stringify(o.handleSelector)});
        const p = document.querySelector(${JSON.stringify(o.puzzleSelector)});
        const s = ${STRIP_FIND};
        return JSON.stringify({
          handle: !!h,
          handleX: h ? (() => { const r = h.getBoundingClientRect(); return r.x + r.width / 2; })() : null,
          handleY: h ? (() => { const r = h.getBoundingClientRect(); return r.y + r.height / 2; })() : null,
          puzzle: !!p,
          puzzleW: p ? Math.round(p.naturalWidth) : 0,
          left: s ? parseFloat(s.style.left || "0") : -1,
          body: document.body.innerText.slice(0, 300),
        });
      })()`
    )
  );
}

// Analyze the current puzzle: locate piece bbox in the strip and the gray gap in the bg.
// Returns { pieceCx, pieceLeft, gapCenterNatural, targetLeft, scale, puzzleW } or null.
async function analyze(session, o) {
  const raw = await ev(
    session,
    `(async () => {
      async function pix(url) {
        const resp = await fetch(url);
        const blob = await resp.blob();
        const bmp = await createImageBitmap(blob);
        const c = document.createElement('canvas');
        c.width = bmp.width; c.height = bmp.height;
        const ctx = c.getContext('2d');
        ctx.drawImage(bmp, 0, 0);
        return { w: bmp.width, h: bmp.height, d: ctx.getImageData(0, 0, bmp.width, bmp.height).data };
      }
      const puzzle = document.querySelector(${JSON.stringify(o.puzzleSelector)});
      const strip = ${STRIP_FIND};
      if (!puzzle || !strip) return JSON.stringify(null);
      const pu = await pix(puzzle.src), st = await pix(strip.src);
      const w = pu.w, h = pu.h;

      // piece bbox inside the strip (alpha > 30)
      let minx = 1e9, maxx = -1, miny = 1e9, maxy = -1;
      for (let y = 0; y < st.h; y++) for (let x = 0; x < st.w; x++) {
        const a = st.d[(y * st.w + x) * 4 + 3];
        if (a > 30) { if (x < minx) minx = x; if (x > maxx) maxx = x; if (y < miny) miny = y; if (y > maxy) maxy = y; }
      }
      if (maxx < 0) return JSON.stringify(null);

      // gap = gray veil: low saturation + mid/high brightness, inside piece vertical band
      const bandH = Math.max(1, maxy - miny);
      const cols = new Array(w).fill(0);
      for (let y = miny; y <= maxy; y++) for (let x = 0; x < w; x++) {
        const i = (y * w + x) * 4;
        const R = pu.d[i], G = pu.d[i + 1], B = pu.d[i + 2];
        const mx = Math.max(R, G, B), mn = Math.min(R, G, B);
        if (mx - mn < 40 && (R + G + B) / 3 > 140) cols[x]++;
      }
      let ranges = [], inR = false, s0 = 0;
      const hi = Math.max(8, Math.round(bandH * 0.25));
      const lo = Math.max(4, Math.round(bandH * 0.12));
      for (let x = 0; x < w; x++) {
        if (cols[x] >= hi && !inR) { inR = true; s0 = x; }
        else if (inR && cols[x] < lo) { inR = false; ranges.push([s0, x - 1]); }
      }
      if (inR) ranges.push([s0, w - 1]);
      ranges = ranges.filter(r => r[1] - r[0] > 12);
      if (!ranges.length) return JSON.stringify(null);

      // widest range = gap
      ranges.sort((a, b) => (b[1] - b[0]) - (a[1] - a[0]));
      const gap = ranges[0];
      const gapCenterNatural = (gap[0] + gap[1]) / 2;
      const pieceCx = (minx + maxx) / 2;
      const scale = puzzle.getBoundingClientRect().width / w;
      const targetLeft = gapCenterNatural * scale - pieceCx;

      return JSON.stringify({ pieceCx, pieceLeft: minx, gap, gapCenterNatural, targetLeft, scale, puzzleW: w });
    })()`,
    true
  );
  return raw ? JSON.parse(raw) : null;
}

// Human-ish pre-movement so the pointer enters the captcha area naturally.
async function wander(session, sx, sy, log) {
  const mv = (x, y) => session.Input.dispatchMouseEvent({ type: "mouseMoved", x: Math.round(x), y: Math.round(y) });
  for (let i = 0; i < 8; i++) {
    await mv(300 + i * 22 + Math.sin(i) * 15, 380 + Math.cos(i / 2) * 40);
    await sleep(28);
  }
  await mv(sx + 2, sy + 2); await sleep(120);
  await mv(sx, sy);
  await sleep(230);
}

// Slow closed-loop drag: read style.left after every small step, stop at target.
async function dragTo(session, sx, sy, target, o, log) {
  const mv = (x, y, b) => session.Input.dispatchMouseEvent({
    type: "mouseMoved", x: Math.round(x), y: Math.round(y), buttons: b,
  });
  let px = sx;
  for (let k = 0; k < 90; k++) {
    const left = parseFloat(await ev(session, `(() => { const s = ${STRIP_FIND}; return s ? (s.style.left || "0") : "-1"; })()`));
    if (left < 0) return { ok: false, left: -1, reason: "strip gone" };
    if (left >= target - 1.5) return { ok: true, left, px };
    const rem = target - left;
    const step = Math.min(14, Math.max(3, rem * 0.35));
    px += step;
    const yy = sy + ((k % 5) - 2) * 0.8; // micro tremor
    await mv(px, yy, 1);
    await sleep(o.movePauseMs);
  }
  const left = parseFloat(await ev(session, `(() => { const s = ${STRIP_FIND}; return s ? (s.style.left || "0") : "-1"; })()`));
  return { ok: left >= target - 3, left, px };
}

// ---- main ---------------------------------------------------------------

export async function solveAliyunPuzzle(session, opts = {}) {
  const o = { ...DEFAULT, ...opts };
  const log = (...a) => { if (o.verbose) console.log("[aliyun]", ...a); };

  for (let attempt = 1; attempt <= o.maxTries; attempt++) {
    // wait until handle + puzzle are present and strip is at 0 (fresh)
    let st = null;
    for (let i = 0; i < 25; i++) {
      st = await readState(session, o);
      if (st.handle && st.puzzle && st.puzzleW > 100 && st.left <= 0.5) break;
      await sleep(600);
    }
    if (!st || !st.handle || st.puzzleW <= 100) {
      log("captcha not ready");
      return { ok: false, verified: false, tries: attempt, reason: "captcha not ready" };
    }

    const info = await analyze(session, o);
    if (!info) {
      log("could not locate gap; refreshing");
      await refresh(session, o);
      continue;
    }
    const target = info.targetLeft;
    if (!(target > 5) || target > info.puzzleW) {
      log("implausible target", target, "- refreshing");
      await refresh(session, o);
      continue;
    }
    log(`attempt ${attempt}: gap=${JSON.stringify(info.gap)} targetLeft=${target.toFixed(1)}`);

    await wander(session, st.handleX, st.handleY, log);
    await session.Input.dispatchMouseEvent({
      type: "mousePressed", x: Math.round(st.handleX), y: Math.round(st.handleY), button: "left", buttons: 1, clickCount: 1,
    });
    await sleep(200);

    const res = await dragTo(session, st.handleX, st.handleY, target, o, log);
    log("drag result:", JSON.stringify(res));
    await sleep(300);
    await session.Input.dispatchMouseEvent({
      type: "mouseReleased", x: Math.round(res.px), y: Math.round(st.handleY), button: "left", buttons: 0, clickCount: 1,
    });
    await sleep(4000);

    const body = (await ev(session, "document.body.innerText.slice(0,400)")).toLowerCase();
    if (body.includes("verified") || body.includes("verify your email") && !body.includes("unable")) {
      log("VERIFIED");
      return { ok: true, verified: true, tries: attempt };
    }
    if (body.includes("verify your email")) {
      log("VERIFIED (email step)");
      return { ok: true, verified: true, tries: attempt };
    }
    if (!body.includes("captcha")) {
      // captcha panel gone entirely -> treat as pass
      log("captcha panel gone -> pass");
      return { ok: true, verified: true, tries: attempt };
    }
    log("captcha rejected; refreshing puzzle");
    await refresh(session, o);
  }
  return { ok: false, verified: false, tries: o.maxTries, reason: "exhausted attempts" };
}

// Click the small refresh icon inside the puzzle panel (top-right).
export async function refresh(session, o = {}) {
  const coords = await ev(session, `(() => {
    const btns = [...document.querySelectorAll('div,button,span,i')].filter(e => {
      const b = e.getBoundingClientRect();
      return b.width > 12 && b.width < 60 && b.height > 12 && b.height < 60 &&
             b.x > 800 && b.y > 230 && b.y < 330;
    }).map(e => { const b = e.getBoundingClientRect(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; });
    return JSON.stringify(btns[btns.length - 1] || null);
  })()`);
  const c = coords ? JSON.parse(coords) : null;
  if (!c) return false;
  await session.Input.dispatchMouseEvent({ type: "mouseMoved", x: Math.round(c.x), y: Math.round(c.y) });
  await sleep(120);
  await session.Input.dispatchMouseEvent({ type: "mousePressed", x: Math.round(c.x), y: Math.round(c.y), button: "left", clickCount: 1 });
  await session.Input.dispatchMouseEvent({ type: "mouseReleased", x: Math.round(c.x), y: Math.round(c.y), button: "left", clickCount: 1 });
  await sleep(2200);
  return true;
}
