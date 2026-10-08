#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""slider_synth_eval —— 合成样本台:离线量化 slider.solve_distance 的缺口定位精度。

用途(2026-10 引入):
  阿里 PUZZLE 滑块的「边缘 NCC + RGB ZNCC 融合」规则与一致性门阈值,原先是 5 个手抓样本的
  目检结论。本工具用合成样本(真实照片 + 边缘到边锯齿块,复刻阿里几何:缺口半透明暗罩、
  拼图条比底图亮且带 alpha)批量生成真值已知的样本,直接调用**生产** solve_distance 评测:
    - 接受率 / 接受集精度 / 单次成功率 / 期望尝试次数 / 接受集误差分位
  并可把每条样本写成 slider_attempts.jsonl(含 x_true),交给 tools/slider_fit.py 复核门阈值。

为什么不用真实截图当样本:真实样本没有缺口真值,只能靠「拖过去成不成」反推,噪声大且样本少。
合成样本几何可控、真值精确,适合回归对比;上线前仍以真实流量的 slider_attempts.jsonl 为准。

依赖:numpy、Pillow。

用法:
  python tools/slider_synth_eval.py --photos /path/to/photos          # 评测
  python tools/slider_synth_eval.py --photos ./pics --n 400 --write-log slider_attempts.jsonl
  python tools/slider_synth_eval.py --fetch --photos ./pics           # 先抓 8 张 picsum 照片
"""
from __future__ import annotations

import argparse
import io
import json
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

# 允许直接从仓库根运行(未安装时也能 import 生产求解器)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
try:
    from qoder2api.slider import solve_distance, MIN_CONF, AGREE_TOL, SEARCH_LO
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"无法导入 qoder2api.slider({exc});请先 uv sync") from exc


def puzzle_mask(w: int, h: int, rng: random.Random) -> Image.Image:
    """边缘到边的锯齿块(bbox == 整块),复刻阿里 52px 竖条拼图。"""
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=max(2, min(w, h) // 12), fill=255)
    r = max(4, int(min(w, h) * 0.16))
    if rng.random() < 0.5:
        cx, cy = w - 1, h // 2      # 右侧凸起
    else:
        cx, cy = w // 2, h - 1      # 下侧凸起
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    return m.filter(ImageFilter.GaussianBlur(1.0))


def make_sample(photos, rng, tw=None, th=None, dark=None, bright=None, blur=None):
    """返回 (底图 PNG, 竖条 PNG, 真值 gx, y0)。"""
    photo = rng.choice(photos)
    W, H = photo.size
    tw = tw or rng.randint(48, 110)
    th = th or tw
    dark = dark if dark is not None else rng.uniform(0.35, 0.62)
    bright = bright if bright is not None else rng.uniform(1.02, 1.18)
    blur = blur if blur is not None else rng.choice([0, 0, 0, 0.6, 1.2])
    y0 = rng.randint(50, max(51, H - th - 50))
    gx = rng.randint(SEARCH_LO + 15, W - tw - 15)
    mask = puzzle_mask(tw, th, rng)
    a = np.array(mask, float) / 255.0
    bg = np.array(photo.convert("RGB"), float)
    out = bg.copy()
    out[y0:y0 + th, gx:gx + tw] *= (1.0 - dark * a[..., None])   # 缺口:半透明暗罩
    bg_img = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
    if blur:
        bg_img = bg_img.filter(ImageFilter.GaussianBlur(blur))
    strip = Image.new("RGBA", (tw, H), (0, 0, 0, 0))
    piece = np.clip(bg[y0:y0 + th, gx:gx + tw] * bright + 6, 0, 255)  # 拼图条更亮
    pimg = Image.fromarray(piece.astype(np.uint8)).convert("RGBA")
    pimg.putalpha(mask)
    strip.paste(pimg, (0, y0), pimg)
    return _png(bg_img), _png(strip), gx, y0


def _png(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


REGIMES = {
    "normal": {},
    "lowcontrast": dict(dark=0.22, bright=1.03),
    "heavyblur": dict(blur=2.0),
    "small_tile": dict(tw=52, th=52),
    "big_tile": dict(tw=120, th=120),
}


def evaluate(photos, n, seed, tol, log_path=None, regimes=None):
    rng = random.Random(seed)
    accepted = rejected = wrong = 0
    errs: list[float] = []
    records: list[dict] = []
    for _ in range(n):
        bg, strip, gx, _ = make_sample(photos, rng, **(regimes or {}))
        sol = solve_distance(bg, strip)
        if sol is None or sol["method"] == "reject" or sol["conf"] < MIN_CONF:
            rejected += 1
            records.append({"reason": "reject" if sol else "no_solution",
                            "x_true": gx, **(sol or {})})
            continue
        accepted += 1
        e = abs(sol["x"] - gx)
        errs.append(e)
        if e > tol:
            wrong += 1
        records.append({"x": sol["x"], "x_true": gx, "conf": sol["conf"], "method": sol["method"],
                        "ncc_x": sol["ncc_x"], "zncc_x": sol["zncc_x"], "agree": sol["agree"],
                        "drag_display": sol["drag_display"], "pass": e <= tol})
    cov = accepted / n
    prec = (accepted - wrong) / accepted if accepted else 0.0
    per_attempt = (accepted - wrong) / n
    tries = (1 / per_attempt) if per_attempt > 0 else float("inf")
    e = np.array(errs) if errs else np.array([0.0])
    print(f"  accept {100*cov:5.1f}%  precision {100*prec:6.2f}%  "
          f"per-attempt {100*per_attempt:5.1f}%  exp.tries {tries:5.2f}  "
          f"err med {np.median(e):4.1f} p90 {np.percentile(e,90):5.1f}")
    if log_path:
        with Path(log_path).open("a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return cov, prec, per_attempt


def fetch_photos(dir_: Path, n=8):
    import httpx
    dir_.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        seed = f"synth{i}"
        try:
            r = httpx.get(f"https://picsum.photos/seed/{seed}/600/400", timeout=25, follow_redirects=True)
            r.raise_for_status()
            (dir_ / f"{seed}.jpg").write_bytes(r.content)
            print(f"  fetched {seed}.jpg ({len(r.content)}B)")
        except Exception as exc:
            print(f"  fetch {seed} failed: {exc}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="合成样本台:离线量化 slider.solve_distance")
    ap.add_argument("--photos", default="tools/synth_photos", help="样本照片目录")
    ap.add_argument("--fetch", action="store_true", help="先从 picsum 抓 8 张照片到 --photos")
    ap.add_argument("--n", type=int, default=200, help="每档样本数")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--tol", type=float, default=4.0, help="命中容差(自然 px)")
    ap.add_argument("--write-log", default=None, help="把样本记录(含 x_true)追加到该 jsonl")
    args = ap.parse_args(argv)

    photos_dir = Path(args.photos)
    if args.fetch:
        fetch_photos(photos_dir)
    if not photos_dir.is_dir():
        print(f"照片目录不存在: {photos_dir}(先 --fetch 或用 --photos 指定)")
        return 1
    files = sorted(p for p in photos_dir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
    if not files:
        print(f"{photos_dir} 下没有图片(先 --fetch 或放入 jpg/png)")
        return 1
    photos = [Image.open(p) for p in files]
    print(f"photos={len(photos)}  n/regime={args.n}  tol={args.tol}px  "
          f"MIN_CONF={MIN_CONF} AGREE_TOL={AGREE_TOL}")
    for name, kw in REGIMES.items():
        print(f"[{name}]")
        evaluate(photos, args.n, args.seed, args.tol, args.write_log, kw)
    if args.write_log:
        print(f"\n记录已写入 {args.write_log};用 tools/slider_fit.py {args.write_log} 复核门阈值")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())