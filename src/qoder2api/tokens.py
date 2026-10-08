"""
Token 刷新与限额查询（openapi.qoder.sh）

- 刷新：POST /api/v1/deviceToken/refresh（drt-）或 /api/v1/jobToken/refresh（jrt-）
- 限额：GET /api/v2/quota/usage
- 后台定时刷新线程：每 6 小时刷新一次全部 enabled 账号的 token
"""
from __future__ import annotations

import threading
import time
from typing import Any

import httpx

from .database import get_db

OPENAPI = "https://openapi.qoder.sh"
UA = "qoder/1.1.16"
REFRESH_INTERVAL = 6 * 3600  # 6 小时


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": UA,
    }


def refresh_one_account(uid: str) -> dict[str, Any]:
    """用 refresh_token 刷新单个账号的 dt-/drt-，并回写数据库。"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT uid, name, refresh_token FROM accounts WHERE uid = ?", (uid,)
        ).fetchone()
    if not row:
        return {"ok": False, "uid": uid, "error": "账号不存在"}
    rt = (row["refresh_token"] or "").strip()
    if not rt:
        return {"ok": False, "uid": uid, "error": "无 refresh_token"}

    # drt- → deviceToken/refresh；jrt- → jobToken/refresh
    if rt.startswith("jrt-"):
        url = f"{OPENAPI}/api/v1/jobToken/refresh"
        token_key = "token"
    else:
        url = f"{OPENAPI}/api/v1/deviceToken/refresh"
        token_key = "device_token"

    try:
        r = httpx.post(url, json={"refresh_token": rt}, headers=_headers(), timeout=25)
    except httpx.HTTPError as e:
        return {"ok": False, "uid": uid, "error": f"网络错误: {e}"}

    if r.status_code != 200:
        return {"ok": False, "uid": uid, "error": f"HTTP {r.status_code}: {r.text[:160]}"}

    d = r.json()
    new_tok = str(d.get(token_key) or d.get("token") or "").strip()
    new_rt = str(d.get("refresh_token") or "").strip()
    if not new_tok:
        return {"ok": False, "uid": uid, "error": "响应缺少 token"}
    expires_at = d.get("expires_at") or ""

    with get_db() as conn:
        conn.execute(
            "UPDATE accounts SET security_oauth_token = ?, refresh_token = ?, "
            "token_expires_at = ?, last_status = 'ok', last_error = NULL WHERE uid = ?",
            (new_tok, new_rt, expires_at, uid),
        )
    return {"ok": True, "uid": uid, "name": row["name"], "expires_at": expires_at}


def refresh_all_account_tokens() -> dict[str, Any]:
    """刷新所有 enabled 且有 refresh_token 的账号。"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT uid FROM accounts WHERE enabled = 1 AND refresh_token IS NOT NULL AND refresh_token != ''"
        ).fetchall()
    results = [refresh_one_account(r["uid"]) for r in rows]
    ok = sum(1 for x in results if x.get("ok"))
    return {
        "ok": ok,
        "failed": len(results) - ok,
        "total": len(results),
        "results": results,
    }


def get_account_quota(uid: str) -> dict[str, Any]:
    """查询单个账号限额（GET /api/v2/quota/usage）。"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT uid, name, security_oauth_token FROM accounts WHERE uid = ?", (uid,)
        ).fetchone()
    if not row:
        return {"ok": False, "uid": uid, "error": "账号不存在"}
    tok = row["security_oauth_token"] or ""
    if not tok:
        return {"ok": False, "uid": uid, "error": "无 token"}
    try:
        r = httpx.get(
            f"{OPENAPI}/api/v2/quota/usage",
            headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"},
            timeout=20,
        )
    except httpx.HTTPError as e:
        return {"ok": False, "uid": uid, "error": f"网络错误: {e}"}
    if r.status_code != 200:
        return {"ok": False, "uid": uid, "error": f"HTTP {r.status_code}: {r.text[:160]}"}
    return {"ok": True, "uid": uid, "name": row["name"], "quota": r.json()}


def get_all_accounts_quota() -> dict[str, Any]:
    """查询所有 enabled 账号的限额。"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT uid, name FROM accounts WHERE enabled = 1 AND security_oauth_token IS NOT NULL AND security_oauth_token != ''"
        ).fetchall()
    quotas = [get_account_quota(r["uid"]) for r in rows]
    return {"total": len(quotas), "quotas": quotas}


# ---------------------------------------------------------------------------
# 后台定时刷新
# ---------------------------------------------------------------------------
_refresh_thread: threading.Thread | None = None
_refresh_lock = threading.Lock()


def _refresh_loop() -> None:
    while True:
        time.sleep(REFRESH_INTERVAL)
        try:
            refresh_all_account_tokens()
        except Exception:
            pass


def start_refresh_loop() -> None:
    """启动后台定时刷新线程（幂等）。"""
    global _refresh_thread
    with _refresh_lock:
        if _refresh_thread is None or not _refresh_thread.is_alive():
            _refresh_thread = threading.Thread(target=_refresh_loop, daemon=True)
            _refresh_thread.start()


# ---------------------------------------------------------------------------
# 账号余额(quota/usage)回写 + 定时刷新(Qoder 无签到端点;对应 Reso2api 的签到槽位机制)
# ---------------------------------------------------------------------------
QUOTA_REFRESH_SLOTS = (9 * 60, 21 * 60)  # 每天 09:00 / 21:00 本地时间刷新


def update_account_quota(uid: str) -> dict:
    """拉取 quota/usage 并回写 accounts 表,返回摘要。"""
    with get_db() as conn:
        row = conn.execute("SELECT security_oauth_token FROM accounts WHERE uid = ?", (uid,)).fetchone()
    if not row:
        return {"ok": False, "error": "账号不存在"}
    tok = (row["security_oauth_token"] or "").strip()
    try:
        r = httpx.get(f"{OPENAPI}/api/v2/quota/usage",
                      headers={**_headers(), "Authorization": f"Bearer {tok}"}, timeout=25)
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        d = r.json()
        q = d.get("userQuota") or {}
        with get_db() as conn:
            conn.execute(
                "UPDATE accounts SET quota_total=?, quota_used=?, quota_remaining=?, quota_exceeded=?, quota_updated_at=?, user_type2=? WHERE uid=?",
                (q.get("total", 0), q.get("used", 0), q.get("remaining", 0),
                 1 if d.get("isQuotaExceeded") else 0,
                 time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), d.get("userType", ""), uid),
            )
        return {"ok": True, "remaining": q.get("remaining", 0), "total": q.get("total", 0)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


def refresh_all_quotas() -> dict:
    with get_db() as conn:
        uids = [r[0] for r in conn.execute("SELECT uid FROM accounts WHERE enabled = 1").fetchall()]
    ok = failed = 0
    for uid in uids:
        r = update_account_quota(uid)
        ok += bool(r.get("ok"))
        failed += not r.get("ok")
    return {"ok": ok, "failed": failed, "total": len(uids)}


def _quota_refresh_loop() -> None:
    """每天 09:00/21:00 刷新全部账号余额(精确分钟槽位,不轮询空转)。"""
    import datetime as _dt
    last_slot = None
    while True:
        now = _dt.datetime.now()
        cur = now.hour * 60 + now.minute
        slots_today = [sm for sm in QUOTA_REFRESH_SLOTS if sm <= cur]
        cur_slot = (now.strftime("%Y%m%d"), max(slots_today) if slots_today else None)
        if cur_slot[1] is not None and cur_slot != last_slot:
            last_slot = cur_slot
            try:
                res = refresh_all_quotas()
                print(f"[quota-refresh] ok={res['ok']} failed={res['failed']}", flush=True)
            except Exception as e:
                print(f"[quota-refresh] error: {e}", flush=True)
        time.sleep(30)


def start_quota_refresh_loop() -> None:
    threading.Thread(target=_quota_refresh_loop, daemon=True).start()
    try:
        refresh_all_quotas()
    except Exception:
        pass
