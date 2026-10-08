"""请求/调用日志(移植自 Reso2api reqlog.go,2026-10)

- 每次 /v1/chat/completions 结束记录:时间/模型/账号/状态/是否流式/TTFB/总耗时/输入输出 tokens/credits
- 双层存储:内存环形窗口(最近 1000 条,面板秒开)+ SQLite request_logs 表(持久,分页查询)
- 流式请求在转发时顺带解析最后一个 usage 帧取 tokens(include_usage 时上游会发)
"""
from __future__ import annotations

import collections
import json
import sqlite3
import threading
import time
from typing import Any

from .database import DB_PATH, get_db

_RING: collections.deque = collections.deque(maxlen=1000)
_LOCK = threading.Lock()


def init_reqlog_db() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS request_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                model TEXT,
                uid TEXT,
                status INTEGER,
                stream INTEGER,
                ttfb_ms INTEGER,
                total_ms INTEGER,
                in_tokens INTEGER DEFAULT 0,
                out_tokens INTEGER DEFAULT 0,
                credits REAL DEFAULT 0
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_ts ON request_logs(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_uid ON request_logs(uid)")


def record_req(model: str, uid: str, status: int, stream: bool,
               ttfb_ms: int | None, total_ms: int | None,
               in_tokens: int = 0, out_tokens: int = 0, credits: float = 0.0) -> dict[str, Any]:
    rec = {
        "ts": time.time(), "model": model, "uid": uid, "status": status,
        "stream": bool(stream), "ttfb_ms": ttfb_ms, "total_ms": total_ms,
        "in_tokens": in_tokens, "out_tokens": out_tokens, "credits": credits,
    }
    with _LOCK:
        _RING.append(rec)
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO request_logs (ts, model, uid, status, stream, ttfb_ms, total_ms, in_tokens, out_tokens, credits) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (rec["ts"], model, uid, status, int(stream), ttfb_ms, total_ms, in_tokens, out_tokens, credits),
            )
    except sqlite3.Error:
        pass
    return rec


def parse_usage_frame(line: str) -> dict[str, int] | None:
    """从 SSE data 行提取 usage(prompt/completion tokens)。保留最后出现的 usage。"""
    if not line.startswith("data:"):
        return None
    try:
        payload = json.loads(line[5:].strip())
    except (ValueError, TypeError):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    return {
        "in_tokens": int(usage.get("prompt_tokens") or 0),
        "out_tokens": int(usage.get("completion_tokens") or 0),
        "credits": float(usage.get("credit") or usage.get("credits") or 0.0),
    }


def query_req_logs(page: int = 1, size: int = 50, model: str | None = None,
                   uid: str | None = None, ok_only: bool | None = None) -> dict[str, Any]:
    """分页查询(新→旧)。size≤0 或 page=0 时返回内存窗口。"""
    if size <= 0 or page <= 0:
        with _LOCK:
            items = list(_RING)[-200:]
        items.reverse()
        return {"items": items, "total": len(items), "page": 0}
    where, args = [], []
    if model:
        where.append("model = ?"); args.append(model)
    if uid:
        where.append("uid = ?"); args.append(uid)
    if ok_only is True:
        where.append("status = 200")
    elif ok_only is False:
        where.append("status != 200")
    cond = ("WHERE " + " AND ".join(where)) if where else ""
    with get_db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM request_logs {cond}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT ts, model, uid, status, stream, ttfb_ms, total_ms, in_tokens, out_tokens, credits FROM request_logs {cond} ORDER BY ts DESC LIMIT ? OFFSET ?",
            (*args, size, (page - 1) * size),
        ).fetchall()
    keys = ("ts", "model", "uid", "status", "stream", "ttfb_ms", "total_ms", "in_tokens", "out_tokens", "credits")
    items = [dict(zip(keys, r)) for r in rows]
    for it in items:
        it["stream"] = bool(it["stream"])
    return {"items": items, "total": total, "page": page}


def usage_stats(days: int = 7) -> dict[str, Any]:
    """本地聚合:按日/按模型/按账号(credits 与 tokens)。近 N 天。"""
    since = time.time() - days * 86400
    with get_db() as conn:
        rows = conn.execute(
            "SELECT ts, model, uid, status, in_tokens, out_tokens, credits FROM request_logs WHERE ts >= ?",
            (since,),
        ).fetchall()
    daily: dict[str, dict[str, float]] = {}
    by_model: dict[str, dict[str, float]] = {}
    by_uid: dict[str, dict[str, float]] = {}
    n_ok = n_err = 0
    for ts, model, uid, status, it_, ot, cr in rows:
        day = time.strftime("%m-%d", time.localtime(ts))
        d = daily.setdefault(day, {"credits": 0.0, "requests": 0, "tokens": 0})
        m = by_model.setdefault(model or "?", {"credits": 0.0, "requests": 0, "tokens": 0})
        a = by_uid.setdefault(uid or "?", {"credits": 0.0, "requests": 0, "tokens": 0})
        for bucket in (d, m, a):
            bucket["credits"] += cr or 0
            bucket["requests"] += 1
            bucket["tokens"] += (it_ or 0) + (ot or 0)
        if status == 200:
            n_ok += 1
        else:
            n_err += 1
    ttfbs = [r for r in rows]
    return {
        "days": days, "total_requests": len(rows), "ok": n_ok, "err": n_err,
        "daily": [{"day": k, **v} for k, v in sorted(daily.items())],
        "models": [{"model": k, **v} for k, v in sorted(by_model.items(), key=lambda kv: -kv[1]["requests"])],
        "accounts": [{"uid": k, **v} for k, v in sorted(by_uid.items(), key=lambda kv: -kv[1]["requests"])],
    }
