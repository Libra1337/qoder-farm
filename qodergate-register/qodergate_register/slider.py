"""
Qoder 阿里滑块自动破解(2026-10-08 IAB 实测通过)

原理:阿里 Captcha(PUZZLE 类型)在 DOM 里渲染两张 <img>——300×200 底图(含缺口)
与 52×200 竖条拼图(块在条内,带 alpha 通道)。把竖条中不透明的拼图块抠出,
在底图上做「掩码边缘 NCC」模板匹配得缺口位置,换算成拖动距离,缓动轨迹拖动;
失败则点「刷新验证码」换新图重试(带 ±3/±6 偏移扫描)。

为什么不用 ddddocr / 视觉模型(同日对照实测,底图真值 x=202):
  - ddddocr slide_match:竖条直接喂 conf=0.08;裁出小块再喂 conf=0.16、偏 16px
  - ddddocr det:检测不到缺口(只找到左下角水印)
  - 视觉模型(4.5v + 像素标尺):中心答 185,偏 40px
  - 掩码 RGB SSD:答 185(被缺口半透明暗色带偏,同视觉模型)
  - 掩码边缘 NCC:答 202,±2px,实测一次通过(拖 203.2px 成功)
行为检测:同一合成轨迹,距离错必失败、距离对即通过——卡点是距离精度,不是轨迹。

依赖:numpy、Pillow(注册机环境额外安装:pip install numpy pillow)
"""
from __future__ import annotations

import base64
import io
import random
import time
from typing import Any

try:
    from PIL import Image
    import numpy as np
except ImportError as e:  # pragma: no cover
    raise ImportError("滑块自动破解需要 numpy 与 Pillow:pip install numpy pillow") from e

import httpx
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent

MIN_CONF = 0.30          # 边缘 NCC 置信度门槛(实测 0.279 判错、0.385 判对)
DISAGREE_FALLBACK = 25   # NCC 与亮度凹陷峰距超过此值时改信凹陷峰(离线 5 样本 5/5)
# 估计噪声约 ±6px、服务端容差约 ±4px:密集小步偏移扫描 + 刷新换图(每次新图重新匹配)
OFFSET_SWEEP = [0, -3, 3, -5, 5, -8, 8, -10, 10, -12, 12, -15, 15, 0]
SEARCH_LO = 60           # 缺口不可能出现在拼图条初始位置附近,排除左缘伪峰


def _subpixel(scores: np.ndarray, i: int, lo: int) -> float:
    """抛物线亚像素细化。"""
    if lo < i < len(scores) - 1:
        l, c, r = float(scores[i - 1]), float(scores[i]), float(scores[i + 1])
        den = l - 2 * c + r
        if abs(den) > 1e-9:
            return i + 0.5 * (l - r) / den
    return float(i)


# ---------------------------------------------------------------------------
# 求解:两张图 → 拖动距离
# ---------------------------------------------------------------------------
def solve_distance(bg_bytes: bytes, piece_bytes: bytes) -> dict[str, Any] | None:
    """双信号融合匹配(2026-10 离线 5 样本调优,目检+实测真值 5/5):
    A. 掩码边缘 NCC(纹理对齐,主判)——单用时会在个别图上错到远处(ds_3 错 90px)
    B. 亮度凹陷(缺口=半透明暗罩,邻侧参考)——单用时偏 10~13px
    融合:|A-B| ≤ 25 取 A;> 25 说明 A 误匹配,取 B。
    """
    bg_img = Image.open(io.BytesIO(bg_bytes)).convert("RGB")
    piece_img = Image.open(io.BytesIO(piece_bytes)).convert("RGBA")
    bg = np.array(bg_img, dtype=float)
    pa = np.array(piece_img, dtype=float)
    alpha = pa[:, :, 3]
    ys, xs = np.where(alpha > 60)
    if len(ys) < 50:
        return None
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    tpl = pa[y0:y1 + 1, x0:x1 + 1, :3]
    mask = alpha[y0:y1 + 1, x0:x1 + 1] > 60
    th, tw = tpl.shape[:2]
    W = bg.shape[1]
    if W <= tw + SEARCH_LO:
        return None

    def edges(img: np.ndarray) -> np.ndarray:
        g = img.mean(axis=2)
        gx = np.abs(np.diff(g, axis=1, prepend=g[:, :1]))
        gy = np.abs(np.diff(g, axis=0, prepend=g[:1, :]))
        return gx + gy

    ebg, etpl = edges(bg), edges(tpl)
    gray = bg.mean(axis=2)
    ncc = np.full(W - tw, -1.0)
    dip = np.full(W - tw, -1e9)
    for x in range(SEARCH_LO, W - tw):
        pe = ebg[y0:y0 + th, x:x + tw]
        num = float((pe * etpl * mask).sum())
        den = np.sqrt(float((pe ** 2 * mask).sum()) * float((etpl ** 2 * mask).sum())) + 1e-9
        ncc[x] = num / den
        pm = float(gray[y0:y0 + th, x:x + tw][mask].mean())
        refs = []
        if x - tw >= 0:
            refs.append(float(gray[y0:y0 + th, x - tw:x].mean()))
        if x + 2 * tw <= W:
            refs.append(float(gray[y0:y0 + th, x + tw:x + 2 * tw].mean()))
        if refs:
            dip[x] = float(np.mean(refs)) - pm

    ncc_i = int(np.argmax(ncc))
    dip_i = int(np.argmax(dip))
    ncc_x = _subpixel(ncc, ncc_i, SEARCH_LO)
    dip_x = _subpixel(dip, dip_i, SEARCH_LO)
    if abs(ncc_x - dip_x) > DISAGREE_FALLBACK:
        x_out, method = dip_x, "dip"
    else:
        x_out, method = ncc_x, "ncc"
    gap_center = x_out + tw / 2
    piece_center = (x0 + x1) / 2
    scale = 300.0 / W  # 显示宽 300 / 自然宽
    return {"x": round(x_out, 1), "conf": round(float(ncc[ncc_i]), 3),
            "ncc_x": round(ncc_x, 1), "dip_x": round(dip_x, 1), "method": method,
            "gap_center": gap_center, "drag_display": (gap_center - piece_center) * scale}


# ---------------------------------------------------------------------------
# DrissionPage 页面操作
# ---------------------------------------------------------------------------
def _captcha_imgs(page) -> list[dict]:
    """取验证码两张图(按宽度降序:底图在前,拼图条在后),兼容 data: 与 https 源。"""
    out: list[dict] = []
    for el in page.eles('css:img'):
        try:
            w, h = el.rect.size
            if h > 100 and w > 40:
                src = el.attr("src") or ""
                if src.startswith("data:image") or "aliyuncs" in src or "captcha" in src:
                    out.append({"w": w, "h": h, "src": src})
        except Exception:
            continue
    out.sort(key=lambda i: -i["w"])
    return out[:2]


def _fetch_img(src: str) -> bytes:
    if src.startswith("data:image"):
        return base64.b64decode(src.split(",", 1)[1])
    r = httpx.get(src, timeout=20)
    r.raise_for_status()
    return r.content


def _slider_handle(page):
    return page.ele('css:[class*="slider-move"]', timeout=5)



def build_drag_track(dx: float, dy: float = 0.0, duration_secs: float = 0.0) -> list[tuple[float, float, float]]:
    """minimum-jerk 拟人轨迹(drission-rs human_drag_track 移植):
    10t³-15t⁴+6t⁵ 钟形速度 + 密集采样 + 手抖 + 纵向漂移 + 末段过冲回拉 + 偶发迟疑。
    返回 [(相对dx, 相对dy, 停顿秒)]。
    """
    dist = abs(dx)
    dur = duration_secs if duration_secs > 0 else min(max(dist * 3.5 / 1000 + 0.32, 0.35), 1.6)
    n = max(24, min(160, int(dur / 0.013)))
    base = max(dur / n, 0.004)
    overshoot = 1.02 + random.random() * 0.04 if dist > 40 else 1.0
    fwd = int(n * 0.82)
    back = max(n - fwd, 3)
    drift_y = (random.random() - 0.5) * 6.0
    mj = lambda t: 10*t**3 - 15*t**4 + 6*t**5
    track = []
    for i in range(1, fwd + 1):
        t = i / fwd
        frac = overshoot * mj(t)
        track.append((dx * frac + (random.random() - 0.5),
                      dy * frac + drift_y * mj(t) + (random.random() - 0.5) * 1.6,
                      base * (1 + (random.random() - 0.5) * 0.8)))
    for i in range(1, back + 1):
        t = i / back
        frac = overshoot - (overshoot - 1.0) * mj(t)
        track.append((dx * frac + (random.random() - 0.5) * 0.8,
                      dy * frac + drift_y + (random.random() - 0.5) * 1.2,
                      base + 0.002))
    track.append((dx, dy, base))
    # 偶发迟疑
    for _ in range(1 + int(random.random() * 2)):
        if fwd > 4:
            idx = 2 + int(random.random() * (fwd - 4))
            if 0 <= idx < len(track):
                x, y, d = track[idx]
                track[idx] = (x, y, d + random.uniform(0.025, 0.075))
    return track


POINTER_STEALTH_JS = """(function(){try{var p=window.PointerEvent&&window.PointerEvent.prototype;if(!p)return;
var d=Object.getOwnPropertyDescriptor(p,'pointerType');if(!d||!d.get)return;var o=d.get;
Object.defineProperty(p,'pointerType',{configurable:true,enumerable:d.enumerable,
get:function(){var v=o.call(this);return(v===''||v==null)?'mouse':v}})}catch(e){}})()"""


def _human_drag(page, handle, distance: float) -> None:
    """拟人拖动(minimum-jerk 轨迹版,替代旧 marks 缓动)。"""
    w, h = handle.rect.size
    hx, hy = handle.rect.location
    cx, cy = hx + w / 2, hy + h / 2
    track = build_drag_track(distance)
    acts = page.actions
    acts.move_to(handle)
    acts.hold()
    px, py = 0.0, 0.0
    for dx, dy, dur in track:
        acts.move(dx - px, dy - py, duration=max(dur, 0.001))
        px, py = dx, dy
        time.sleep(max(dur, 0.001))
    acts.release()


def _passed(page) -> bool:
    if "/download" in page.url:
        return True
    try:
        el = page.ele('css:input.ant-input', timeout=1)
        return bool(el and el.states.is_displayed)
    except Exception:
        return False


def _failed_banner(page) -> bool:
    html = page.html or ""
    return any(k in html for k in ("Unable to verify", "验证失败", "无法验证"))


def _expand_captcha(page) -> None:
    """初始态是折叠的「点击开始验证 / Click to verify」,点它滑块与图片才出现(双语兼容)。"""
    for text in ("点击开始验证", "Click to verify"):
        try:
            el = page.ele(f'text:{text}', timeout=1)
            if el:
                el.click()
                time.sleep(2)
                return
        except Exception:
            continue


def _refresh_captcha(page) -> None:
    try:
        for b in page.eles('css:button'):
            if "刷新" in (b.attr("aria-label") or ""):
                b.click()
                return
    except Exception:
        pass


def _log_attempt(rec: dict) -> None:
    """尝试记录追加到项目根 slider_attempts.jsonl,供后续离线调优。"""
    try:
        import json as _json
        path = APP_DIR / "slider_attempts.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(_json.dumps({**rec, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n")
    except Exception:
        pass


def _ensure_pointer_stealth(page) -> None:
    """修补合成 PointerEvent 的空 pointerType(行为风控识别自动化的破绽,drission-rs 移植)。"""
    try:
        page.run_js(f"if(!window.__ptrStealth){{{POINTER_STEALTH_JS};window.__ptrStealth=1}}")
    except Exception:
        pass


def auto_solve_slider(page, log=print, max_attempts: int = 14) -> bool:
    """自动过滑块;成功返回 True。每次尝试:展开→取图→匹配→拖动→验证,失败刷新换图。"""
    _ensure_pointer_stealth(page)
    _expand_captcha(page)
    for attempt in range(1, max_attempts + 1):
        imgs = _captcha_imgs(page)
        if len(imgs) < 2:
            if _passed(page):
                log(f"[slider] attempt {attempt}: already passed")
                return True
            log(f"[slider] attempt {attempt}: captcha images not found, expand/refresh")
            _expand_captcha(page)
            _refresh_captcha(page)
            time.sleep(1.6)
            continue
        try:
            bg = _fetch_img(imgs[0]["src"])
            piece = _fetch_img(imgs[1]["src"])
        except Exception as e:
            log(f"[slider] attempt {attempt}: fetch img error {e}")
            _refresh_captcha(page)
            time.sleep(1.5)
            continue
        sol = solve_distance(bg, piece)
        if sol is None or sol["conf"] < MIN_CONF:
            log(f"[slider] attempt {attempt}: low confidence {sol and sol['conf']}, refresh")
            _refresh_captcha(page)
            time.sleep(1.5)
            continue
        off = OFFSET_SWEEP[min(attempt - 1, len(OFFSET_SWEEP) - 1)]
        d = sol["drag_display"] + off
        log(f"[slider] attempt {attempt}: match x={sol['x']} conf={sol['conf']} drag={d:.1f}px (off {off:+d})")
        handle = _slider_handle(page)
        if handle is None:
            log(f"[slider] attempt {attempt}: handle missing, refresh")
            _refresh_captcha(page)
            time.sleep(1.5)
            continue
        try:
            _human_drag(page, handle, d)
        except Exception as e:
            log(f"[slider] attempt {attempt}: drag error {e}")
        time.sleep(1.6)
        if _passed(page):
            log(f"[slider] PASSED at attempt {attempt}")
            _log_attempt({"attempt": attempt, "off": off, **{k: sol[k] for k in ("x", "conf", "method", "drag_display") if k in sol}, "pass": True})
            return True
        if _failed_banner(page):
            log(f"[slider] attempt {attempt}: rejected, refreshing")
            _log_attempt({"attempt": attempt, "off": off, **{k: sol[k] for k in ("x", "conf", "method", "drag_display") if k in sol}, "pass": False})
        _expand_captcha(page)
        _refresh_captcha(page)
        time.sleep(1.4)
    return False
