"""模型目录(快照自官方客户端 catalog,含价格系数/上下文/能力)。

来源:qoder 客户端模型目录(catalog-v6 同源数据,经 qoder2api-hub 快照)。
更新:替换 src/qoder2api/model_catalog.json 后重启;或 QODER_MODELS_FILE 指向新文件。
"""
from __future__ import annotations
import json, os
from pathlib import Path
from typing import Any

_DEFAULT = Path(__file__).parent / "model_catalog.json"
# 档位 → api2-v2 可用名(实测);catalog 的 key 是 COSY 协议名
_TIER_MAP = {
    "lite": "lite", "auto": "auto", "efficient": "efficient",
    "performance": "performance", "ultimate": "ultimate",
    "smodel": "smodel", "cmodel": "cmodel",
}
_EXTRA = [{"key": "gpt-5", "display_name": "GPT-5", "price_factor": 1.0, "is_vl": True,
           "is_reasoning": True, "max_input_tokens": 200000, "enable": True}]


def load_catalog() -> list[dict[str, Any]]:
    path = os.getenv("QODER_MODELS_FILE") or str(_DEFAULT)
    try:
        data = json.load(open(path))
        items = data if isinstance(data, list) else data.get("models", [])
        return [m for m in items if isinstance(m, dict)] + _EXTRA
    except Exception:
        return list(_EXTRA)


_CACHE: tuple[float, list] = (0.0, [])
_TTL = 300.0


def catalog() -> list[dict[str, Any]]:
    import time
    global _CACHE
    now = time.time()
    if now - _CACHE[0] > _TTL:
        _CACHE = (now, load_catalog())
    return _CACHE[1]


def api_models() -> list[dict[str, Any]]:
    """OpenAI /v1/models 格式,附 qoder_* 元数据(价格系数/免费标记/上下文/能力)。"""
    out = []
    for m in catalog():
        key = m.get("key") or ""
        out.append({
            "id": key, "object": "model",
            "owned_by": "qoder",
            "qoder_display_name": m.get("display_name"),
            "qoder_price_factor": m.get("price_factor"),
            "qoder_free": bool(m.get("is_free") or (m.get("price_factor") == 0)),
            "qoder_enabled": m.get("enable"),
            "qoder_vision": m.get("is_vl"),
            "qoder_reasoning": m.get("is_reasoning"),
            "context_window": m.get("max_input_tokens"),
        })
    return out
