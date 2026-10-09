"""
Qoder 阿里滑块自动破解(2026-10-08 合成样本台 + IAB 实测)

原理:阿里 Captcha(PUZZLE 类型)在 DOM 里渲染两张 <img>——300×200 底图(含缺口)
与 52×200 竖条拼图(块在条内,带 alpha 通道)。把竖条中不透明的拼图块抠出,
在底图上做「掩码边缘 NCC + 掩码 RGB ZNCC 双信号融合」模板匹配得缺口位置,
换算成拖动距离,缓动轨迹拖动;可选严格一致性门(默认关,SLIDER_AGREE_TOL 开启)
在两信号不一致时判不可信,点「刷新验证码」换新图重试。

为什么不用 ddddocr / 视觉模型(2026-10-08 实测,底图真值 x=202):
  - ddddocr slide_match:竖条直接喂 conf=0.08;裁出小块再喂 conf=0.16、偏 16px
  - ddddocr det:检测不到缺口(只找到左下角水印)
  - 视觉模型(4.5v + 像素标尺):中心答 185,偏 40px
  - 掩码 RGB SSD:答 185(被缺口半透明暗色带偏,同视觉模型)
  - 掩码边缘 NCC:答 202,±2px,实测一次通过(拖 203.2px 成功)
行为检测:同一合成轨迹,距离错必失败、距离对即通过——卡点是距离精度,不是轨迹。

融合规则与一致性门经 7 档 × 200~400 合成样本离线量化(见 solve_distance 注释)。

依赖:numpy、Pillow(注册机环境额外安装:pip install numpy pillow)
"""
from __future__ import annotations

import base64
import io
import os
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

# 项目根:两种布局下都是 parents[2](src/qoder2api/ 与 qodergate_register/ 深度相同);
# 向上找最近的含 .git 目录,装成 wheel 时退化为 parents[2]。
_here = Path(__file__).resolve()
APP_DIR = next((c for c in (_here.parents[2], _here.parents[1], _here.parents[0])
                if (c / ".git").exists()), _here.parents[2])

MIN_CONF = 0.30          # 边缘 NCC 峰值置信度门槛
# 严格一致性门(默认关闭):|ncc-zncc| > AGREE_TOL 判不可信、换图重试。
# 合成台实测(7 档 × 200~400 样本):关闭时单次成功率 86~97%(最高),开启(<=8)时
# 接受集精度 100% 但单次成功率降到 47~81%——被拒的尝试同样要重刷换图,净耗时更长。
# 追求风控保守(绝不上交不一致答案)时置 SLIDER_AGREE_TOL=8。
AGREE_TOL = float(os.getenv("SLIDER_AGREE_TOL", "0"))  # 0 = 关闭严格门
# 融合求解已亚像素级,失败主因是误匹配(换图即解决)而非小偏移,故扫描收敛到 0 附近
OFFSET_SWEEP = [0, 0, 0, 0, 0, 0, 0, 1, -1, 2, -2, 3, -3, 0]
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
    """融合匹配:两张图 → 拖动距离(2026-10 合成样本台调优,详见模块头注释)。

    两个互补信号(互补性经离线量化验证,非拍脑袋):
      A. 掩码边缘 NCC——梯度域纹理对齐,主判
      B. 掩码 RGB ZNCC——原图三通道零均值归一化相关
    融合:各归一化到 [0,1] 后等权相加取峰。默认直接采用融合峰;若设 SLIDER_AGREE_TOL>0,
    再叠加一致性门:两信号独立峰横坐标距离 > AGREE_TOL → 判不可信(conf=0),换图重试。
    旧版"|A-B|>25 改信亮度凹陷(dip)"已移除:dip 单独仅 45% 命中,融合规则反被其拖到 57%。
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
    etpl2 = float(((etpl * mask) ** 2).sum())
    # 模板零均值化(掩码内),供 RGB ZNCC 使用
    tplz = np.empty_like(tpl)
    for c in range(3):
        v = tpl[:, :, c][mask]
        tplz[:, :, c] = (tpl[:, :, c] - v.mean()) * mask
    tplz2 = float((tplz ** 2).sum())
    gmask = mask.astype(float)

    n = W - tw
    ncc = np.full(n, -1e9)
    zncc = np.full(n, -1e9)
    for x in range(SEARCH_LO, n):
        pe = ebg[y0:y0 + th, x:x + tw]
        ncc[x] = float((pe * etpl * mask).sum()) / (
            np.sqrt(float((pe ** 2 * mask).sum()) * etpl2) + 1e-9)
        pr = bg[y0:y0 + th, x:x + tw, :]
        zncc[x] = float((pr * tplz).sum()) / (
            np.sqrt(float((pr ** 2 * gmask[..., None]).sum()) * tplz2) + 1e-9)

    def norm(a: np.ndarray) -> np.ndarray:
        lo, hi = a.min(), a.max()
        return (a - lo) / (hi - lo + 1e-9)

    fused = norm(ncc) + norm(zncc)
    f_i = int(np.argmax(fused))
    ncc_i = int(np.argmax(ncc))
    zncc_i = int(np.argmax(zncc))
    ncc_x = _subpixel(ncc, ncc_i, SEARCH_LO)
    zncc_x = _subpixel(zncc, zncc_i, SEARCH_LO)
    x_out = _subpixel(fused, f_i, SEARCH_LO)
    agree = abs(ncc_x - zncc_x)
    gated = AGREE_TOL > 0 and agree > AGREE_TOL
    conf = 0.0 if gated else float(ncc[ncc_i])
    gap_center = x_out + tw / 2
    piece_center = (x0 + x1) / 2
    scale = 300.0 / W  # 显示宽 300 / 自然宽
    return {"x": round(x_out, 1), "conf": round(conf, 3),
            "ncc_x": round(ncc_x, 1), "zncc_x": round(zncc_x, 1), "agree": round(agree, 1),
            "method": "reject" if gated else "fused",
            "gap_center": gap_center, "drag_display": (gap_center - piece_center) * scale}


# ---------------------------------------------------------------------------
# 页面操作(浏览器适配层,见 browser.py)
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


def _human_drag(page, handle, distance: float) -> None:
    """缓动 + 抖动轨迹拖动(与实测通过的 IAB 轨迹同构)。"""
    w, h = handle.rect.size
    hx, hy = handle.rect.location
    cx, cy = hx + w / 2, hy + h / 2
    d = distance
    marks = [0, 2, 5, 9, 15, 23, 33, 42, 49, 54, d * 0.97, d, d - 1, d]
    marks = [m for m in marks if m <= d] if d < 54 else marks
    acts = page.actions
    acts.move_to(handle)
    acts.hold()
    prev_x, prev_y = 0.0, 0.0
    for i, m in enumerate(marks[1:], start=1):
        jy = random.uniform(-1.2, 1.2)
        tx, ty = m, jy
        acts.move(tx - prev_x, ty - prev_y, duration=random.uniform(0.02, 0.06))
        prev_x, prev_y = tx, ty
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
    """尝试记录追加到 slider_attempts.jsonl(默认项目根;SLIDER_ATTEMPTS_PATH 可覆盖)。

    记录含 ncc_x/zncc_x/agree/conf/method/pass,离线拟合工具据此评估一致性门与融合权重
    (drag_display 是显示系拖距,底图自然宽 = drag_display*W/300,可反推求解器 x)。
    """
    try:
        import json as _json
        path = Path(os.getenv("SLIDER_ATTEMPTS_PATH") or (APP_DIR / "slider_attempts.jsonl"))
        with path.open("a", encoding="utf-8") as f:
            f.write(_json.dumps({**rec, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n")
    except Exception:
        pass


def _rec_fields(sol: dict, attempt: int, off: int, passed: bool | None) -> dict:
    """归一化尝试记录字段(供拟合工具消费)。"""
    rec = {"attempt": attempt, "off": off, "pass": passed}
    for k in ("x", "conf", "method", "ncc_x", "zncc_x", "agree", "drag_display"):
        if k in sol:
            rec[k] = sol[k]
    return rec


def auto_solve_slider(page, log=print, max_attempts: int = 14) -> bool:
    """自动过滑块;成功返回 True。每次尝试:展开→取图→匹配→拖动→验证,失败刷新换图。"""
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
        if sol is None or sol["method"] == "reject" or sol["conf"] < MIN_CONF:
            _log_attempt({"attempt": attempt, "reason": "reject" if sol else "no_solution",
                          **(sol or {})})
            log(f"[slider] attempt {attempt}: low confidence {sol and sol['conf']}, refresh")
            _refresh_captcha(page)
            time.sleep(1.5)
            continue
        off = OFFSET_SWEEP[min(attempt - 1, len(OFFSET_SWEEP) - 1)]
        d = sol["drag_display"] + off
        log(f"[slider] attempt {attempt}: match x={sol['x']} conf={sol['conf']} agree={sol['agree']} drag={d:.1f}px (off {off:+d})")
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
            _log_attempt(_rec_fields(sol, attempt, off, True))
            return True
        if _failed_banner(page):
            log(f"[slider] attempt {attempt}: rejected, refreshing")
            _log_attempt(_rec_fields(sol, attempt, off, False))
        _expand_captcha(page)
        _refresh_captcha(page)
        time.sleep(1.4)
    return False
