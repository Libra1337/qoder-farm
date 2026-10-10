import argparse
import asyncio
import json
import collections
import hmac
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .auth import SessionContext, create_session, load_local_session
from .bridge import complete_openai_response, stream_openai_response
from .config import load_config, save_config
from .database import get_db
from .env import env_bool
from .accounts import (
    db_load_accounts,
    db_get_settings,
    db_set_settings,
    import_current_auth,
    get_active_session,
    rotate_next_account,
    batch_import_accounts,
)
from .registrar import get_registrar_status, start_registration, stop_registration
from .accounts import get_session_for_uid
from .bridge import conversation_fingerprint
from .fingerprint import redact
from .campaigns import claim_daily, claim_all, start_claim_loop
from .reqlog import init_reqlog_db, record_req, parse_usage_frame, query_req_logs, usage_stats
from .tokens import (
    update_account_quota,
    refresh_all_quotas,
    start_quota_refresh_loop,
    start_keepalive_loop,
    keepalive_once,
    refresh_all_account_tokens,
    refresh_one_account,
    get_account_quota,
    get_all_accounts_quota,
    start_refresh_loop,
)

BASE_DIR = os.path.dirname(__file__)
INDEX_HTML = Path(BASE_DIR) / "static" / "index.html"
CONSOLE_HTML = Path(BASE_DIR) / "static" / "console.html"
DOCS_HTML = Path(BASE_DIR) / "static" / "docs.html"

app = FastAPI(title="qoder2api-python")
app.mount("/assets", StaticFiles(directory=os.path.join(BASE_DIR, "static", "assets")), name="assets")

_session: SessionContext | None = None
_local_auth_error: str | None = None

logs_queue = collections.deque(maxlen=150)
init_reqlog_db()

_bg_started = False


def _start_background_loops() -> None:
    """启动后台线程(幂等):token 刷新 / 余额刷新 / 每日保活。

    放在 startup 事件里,使 `uvicorn qoder2api.app:app` 直跑也能起线程(旧版只在 main() 里起)。
    """
    global _bg_started
    if _bg_started:
        return
    _bg_started = True
    start_refresh_loop()
    start_quota_refresh_loop()
    start_keepalive_loop()
    start_claim_loop()  # 每日 100C 领取(需 QODER_UMID_* 身份或本机 umid 组件)


@app.on_event("startup")
async def _on_startup() -> None:
    _start_background_loops()


def add_log(msg: str, level: str = "INFO") -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    formatted = f"[{timestamp}] [{level}] {redact(str(msg))}"
    logs_queue.append(formatted)
    print(formatted)


# Add initial logs
add_log("Qoder2API Python Bridge initialized.")



def _ct_equal(a: str | None, b: str | None) -> bool:
    """恒时比较,防时序攻击(trae2api/Reso2api 同款纪律)。"""
    if not a or not b:
        return False
    return hmac.compare_digest(a.encode(), b.encode())


def check_gateway_token(x_gateway_token: str | None = Header(default=None)):
    config = load_config()
    gateway_token = config.get("gateway_token", "admin")
    if not _ct_equal(x_gateway_token, gateway_token):
        raise HTTPException(status_code=401, detail="Unauthorized gateway access")


@app.post("/ui/verify")
async def verify_gateway(payload: dict[str, Any]) -> dict[str, Any]:
    token = payload.get("token", "").strip()
    config = load_config()
    if token == config.get("gateway_token", "admin"):
        return {"status": "ok"}
    raise HTTPException(status_code=401, detail="Invalid Gateway Token")


async def get_session() -> SessionContext:
    global _local_auth_error
    data = db_load_accounts()
    if not data["accounts"]:
        # Try importing environment PAT if available
        pat = os.getenv("QODER_PAT", "").strip()
        if pat:
            add_log("No accounts stored. Importing QODER_PAT from environment...")
            try:
                sess = await create_session(pat)
                with get_db() as conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO accounts (
                            uid, name, user_type, security_oauth_token, refresh_token, machine_id,
                            enabled, last_status, last_error
                        ) VALUES (?, ?, ?, ?, ?, ?, 1, 'ok', NULL)
                        """,
                        (sess.identity.uid, sess.identity.name or "Environment PAT", sess.identity.user_type,
                         sess.identity.security_oauth_token, sess.identity.refresh_token, sess.machine_id)
                    )
                db_set_settings("active_uid", sess.identity.uid)
                add_log(f"Imported environment PAT as account: {sess.identity.name}")
                _local_auth_error = None
            except Exception as exc:
                add_log(f"Failed to import environment PAT: {exc}", "ERROR")

        data = db_load_accounts()
        if not data["accounts"]:
            add_log("No accounts stored. Attempting to auto-import current local Qoder auth session...")
            try:
                await import_current_auth()
                add_log("Auto-imported current local Qoder session successfully.")
                _local_auth_error = None
            except Exception as exc:
                _local_auth_error = str(exc)
                add_log(f"Auto-import of local session failed: {exc}", "WARNING")

    try:
        return get_active_session()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"No active session available: {exc}. Please configure/import an account first."
        )


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    if not env_bool("QODER_ENABLE_LANDING", True):
        raise HTTPException(status_code=404, detail="Landing page is disabled")
    return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))


@app.get("/console", response_class=HTMLResponse)
async def console() -> HTMLResponse:
    return HTMLResponse(CONSOLE_HTML.read_text(encoding="utf-8"))


@app.get("/documents", response_class=HTMLResponse)
async def documents() -> HTMLResponse:
    if not env_bool("QODER_ENABLE_DOCUMENTS", True):
        raise HTTPException(status_code=404, detail="Documents page is disabled")
    return HTMLResponse(DOCS_HTML.read_text(encoding="utf-8"))


@app.get("/ui/status")
async def status(verify: None = Depends(check_gateway_token)) -> dict[str, Any]:
    global _local_auth_error
    try:
        await get_session()
    except Exception:
        pass

    data = db_load_accounts()
    active_uid = data.get("active_uid")
    active_acc = None
    for acc in data["accounts"]:
        if acc["uid"] == active_uid:
            active_acc = acc
            break

    if active_acc is not None:
        return {
            "ready": True,
            "mode": "accounts",
            "username": active_acc["name"],
            "uid": active_acc["uid"],
            "user_type": active_acc["user_type"],
            "error": None,
            "accounts_count": len(data["accounts"])
        }
    return {
        "ready": False,
        "mode": "none",
        "username": None,
        "uid": None,
        "user_type": None,
        "error": _local_auth_error,
        "accounts_count": len(data["accounts"])
    }


@app.get("/ui/accounts")
async def get_accounts(verify: None = Depends(check_gateway_token)) -> dict[str, Any]:
    return db_load_accounts()


@app.post("/ui/accounts/import")
async def import_account(verify: None = Depends(check_gateway_token)) -> dict[str, Any]:
    try:
        acc = await import_current_auth()
        add_log(f"Imported local Qoder session account: {acc['name']}")
        return {"status": "ok", "account": acc}
    except Exception as exc:
        add_log(f"Failed to import local session account: {exc}", "ERROR")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/ui/accounts/batch-import")
async def batch_import(payload: dict[str, Any], verify: None = Depends(check_gateway_token)) -> dict[str, Any]:
    """批量导入注册机导出的 JSON：{"accounts": [{user_id, token, refresh_token, ...}]}。"""
    records = payload.get("accounts") or payload.get("records") or []
    if not isinstance(records, list) or not records:
        raise HTTPException(status_code=400, detail="accounts 数组为空")
    result = batch_import_accounts(records)
    add_log(f"Batch imported {result['imported']} accounts (skipped {result['skipped']})")
    return {"status": "ok", **result}


@app.post("/ui/accounts/select")
async def select_account(payload: dict[str, Any], verify: None = Depends(check_gateway_token)) -> dict[str, Any]:
    uid = payload.get("uid")
    if not uid:
        raise HTTPException(status_code=400, detail="uid is required")
    with get_db() as conn:
        res = conn.execute("SELECT uid FROM accounts WHERE uid = ?", (uid,)).fetchone()
        if not res:
            raise HTTPException(status_code=404, detail="Account not found")
    db_set_settings("active_uid", uid)
    add_log(f"Selected active account UID: {uid}")
    return {"status": "ok"}


@app.post("/ui/accounts/toggle")
async def toggle_account(payload: dict[str, Any], verify: None = Depends(check_gateway_token)) -> dict[str, Any]:
    uid = payload.get("uid")
    enabled = bool(payload.get("enabled", True))
    if not uid:
        raise HTTPException(status_code=400, detail="uid is required")
    enabled_val = 1 if enabled else 0
    with get_db() as conn:
        res = conn.execute("UPDATE accounts SET enabled = ? WHERE uid = ?", (enabled_val, uid))
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Account not found")
    add_log(f"Account toggle enabled={enabled} for UID: {uid}")
    return {"status": "ok"}


@app.post("/ui/accounts/refresh-tokens")
async def refresh_account_tokens(verify: None = Depends(check_gateway_token)) -> dict[str, Any]:
    """手动触发：刷新所有账号的 token（drt- → deviceToken/refresh）。"""
    result = refresh_all_account_tokens()
    add_log(f"Token refresh: ok={result['ok']} failed={result['failed']} total={result['total']}")
    return {"status": "ok", **result}


@app.get("/ui/accounts/quota")
async def accounts_quota(verify: None = Depends(check_gateway_token)) -> dict[str, Any]:
    """查看所有启用账号的限额（GET /api/v2/quota/usage）。"""
    return get_all_accounts_quota()


@app.delete("/ui/accounts/{uid}")
async def delete_account(uid: str, verify: None = Depends(check_gateway_token)) -> dict[str, Any]:
    with get_db() as conn:
        res = conn.execute("DELETE FROM accounts WHERE uid = ?", (uid,))
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Account not found")
            
    active_uid = db_get_settings("active_uid")
    if active_uid == uid:
        data = db_load_accounts()
        new_active = data["accounts"][0]["uid"] if data["accounts"] else None
        if new_active:
            db_set_settings("active_uid", new_active)
        else:
            with get_db() as conn:
                conn.execute("DELETE FROM settings WHERE key = 'active_uid'")
    add_log(f"Deleted account UID: {uid}")
    return {"status": "ok"}


@app.get("/v1/models")
async def v1_models():
    """模型列表(lite 免费档;premium 档需账号有 credits,0 额度号 402)。"""
    premium = [m.strip() for m in os.getenv("QODER_PREMIUM_MODELS", "plus,pro,max,ultra").split(",") if m.strip()]
    data = [{"id": "lite", "object": "model", "owned_by": "qoder", "note": "free tier, unlimited for free accounts"}]
    data += [{"id": m, "object": "model", "owned_by": "qoder", "note": "requires credits (402 on free accounts)"} for m in premium]
    return {"object": "list", "data": data}


@app.get("/ui/requests")
async def ui_requests(verify: None = Depends(check_gateway_token), page: int = 1, size: int = 50,
                      model: str | None = None, uid: str | None = None, ok: str | None = None):
    """调用日志(分页;page/size=0 返回内存窗口最近记录)。"""
    ok_only = None if ok is None else (ok == "1")
    return query_req_logs(page=page, size=size, model=model, uid=uid, ok_only=ok_only)


@app.get("/ui/usage/stats")
async def ui_usage_stats(verify: None = Depends(check_gateway_token), days: int = 7):
    """用量统计:按日/按模型/按账号(tokens+credits),来自本地调用日志。"""
    return usage_stats(days=min(max(days, 1), 31))


@app.post("/ui/accounts/refresh-quota")
async def ui_refresh_quota(verify: None = Depends(check_gateway_token)):
    """手动刷新全部账号余额(quota/usage)。"""
    res = refresh_all_quotas()
    add_log(f"Quota refresh: ok={res['ok']} failed={res['failed']} total={res['total']}")
    return {"status": "ok", **res}


@app.post("/ui/accounts/keepalive")
async def ui_keepalive(verify: None = Depends(check_gateway_token)):
    """手动触发一轮账号保活(探活 + 失效即刷新)。"""
    res = keepalive_once()
    add_log(f"Keepalive: ok={res['ok']} failed={res['failed']} total={res['total']}")
    return {"status": "ok", **res}


@app.post("/ui/accounts/claim-daily")
async def ui_claim_daily(verify: None = Depends(check_gateway_token), target: str | None = None):
    """手动触发每日 100C 领取(target=邮箱前缀;不填=池中第一个号;all=逐号试)。"""
    from .database import get_db
    if target == "all":
        res = claim_all()
    else:
        with get_db() as conn:
            if target:
                row = conn.execute("SELECT uid, name, security_oauth_token FROM accounts WHERE enabled=1 AND name LIKE ? LIMIT 1", (target + "%",)).fetchone()
            else:
                row = conn.execute("SELECT uid, name, security_oauth_token FROM accounts WHERE enabled=1 LIMIT 1").fetchone()
        res = claim_daily(row["security_oauth_token"], row["uid"]) if row else {"ok": False, "message": "无账号"}
    add_log(f"Claim: {json.dumps(res, ensure_ascii=False)[:120]}")
    return {"status": "ok", "result": res}


@app.get("/ui/logs")
async def get_logs(verify: None = Depends(check_gateway_token)) -> list[str]:
    return list(logs_queue)


@app.post("/ui/registrar/start")
async def registrar_start(payload: dict[str, Any] | None = None, verify: None = Depends(check_gateway_token)) -> dict[str, Any]:
    """启动注册机（无限循环：parents 个母线程 × 每批 3 个子任务，直到调用 stop）。

    body 可选：{"parents": 2}  —— 母线程数（1-6），每母线程 3 子任务并发。
    """
    payload = payload or {}
    try:
        parents = int(payload.get("parents", 2))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="parents 参数无效")
    return start_registration(parents=parents)


@app.post("/ui/registrar/stop")
async def registrar_stop(verify: None = Depends(check_gateway_token)) -> dict[str, Any]:
    """请求停止：当前批次完成后停止，返回本次注册统计。"""
    return stop_registration()


@app.get("/ui/registrar/status")
async def registrar_status(verify: None = Depends(check_gateway_token)) -> dict[str, Any]:
    """查询注册机任务状态（stage / logs / result）。"""
    return get_registrar_status()


@app.get("/ui/config")
async def get_ui_config(verify: None = Depends(check_gateway_token)) -> dict[str, Any]:
    return load_config()


@app.post("/ui/config")
async def post_ui_config(payload: dict[str, Any], verify: None = Depends(check_gateway_token)) -> dict[str, Any]:
    save_config(payload)
    add_log("API Key configuration updated.")
    return {"status": "ok"}


@app.post("/ui/session")
async def set_session(payload: dict[str, Any], verify: None = Depends(check_gateway_token)) -> dict[str, Any]:
    global _local_auth_error
    pat = str(payload.get("pat") or os.getenv("QODER_PAT", "")).strip()
    if not pat:
        raise HTTPException(status_code=400, detail="PAT is required")
    try:
        add_log("Attempting to save session from PAT...")
        sess = await create_session(pat)
        
        # Insert or update in SQLite
        with get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO accounts (
                    uid, name, user_type, security_oauth_token, refresh_token, machine_id,
                    enabled, last_status, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, 1, 'ok', ?)
                """,
                (sess.identity.uid, sess.identity.name or "PAT Account", sess.identity.user_type,
                 sess.identity.security_oauth_token, sess.identity.refresh_token, sess.machine_id, None)
            )
            
        db_set_settings("active_uid", sess.identity.uid)
        
        add_log(f"Session saved from PAT. User: {sess.identity.name}")
        _local_auth_error = None
        return {"ready": True, "id": sess.identity.uid, "name": sess.identity.name, "user_type": sess.identity.user_type}
    except Exception as exc:
        msg = f"Failed to authenticate with provided PAT: {exc}"
        add_log(msg, "ERROR")
        raise HTTPException(status_code=502, detail=msg) from exc


# ---------------- 会话粘性 / 冷却 / 每账号并发(Reso2api pool.go 纪律) ----------------
_STICKY_MAX_USES = 50       # 每会话最多粘同一账号 50 次,期满重摊
_STICKY_IDLE_S = 2 * 3600   # 粘性 2h 空闲过期
_STICKY_CAP = 4096          # LRU 上限
_sticky: collections.OrderedDict[str, tuple[str, int, float]] = collections.OrderedDict()
_cooldowns: dict[str, float] = {}  # uid -> 冷却截止(unix ts)
_acct_sems: dict[str, asyncio.Semaphore] = {}
_ACCT_CONCURRENCY = int(os.getenv("QODER_ACCOUNT_CONCURRENCY", "2"))
_FIRST_TOKEN_BUDGET = int(os.getenv("QODER_FIRST_TOKEN_TIMEOUT", "45"))


def _sem_for(uid: str) -> asyncio.Semaphore:
    sem = _acct_sems.get(uid)
    if sem is None:
        sem = asyncio.Semaphore(_ACCT_CONCURRENCY)
        _acct_sems[uid] = sem
    return sem


def _cooldown(uid: str, seconds: float) -> None:
    _cooldowns[uid] = time.time() + seconds


def _cooling(uid: str) -> bool:
    until = _cooldowns.get(uid)
    if until and until > time.time():
        return True
    _cooldowns.pop(uid, None)
    return False


def _sticky_get(conv: str) -> str | None:
    hit = _sticky.get(conv)
    if not hit:
        return None
    uid, uses, ts = hit
    if time.time() - ts > _STICKY_IDLE_S or uses >= _STICKY_MAX_USES:
        _sticky.pop(conv, None)
        return None
    _sticky[conv] = (uid, uses + 1, time.time())
    _sticky.move_to_end(conv)
    return uid


def _sticky_put(conv: str, uid: str) -> None:
    _sticky[conv] = (uid, 1, time.time())
    _sticky.move_to_end(conv)
    while len(_sticky) > _STICKY_CAP:
        _sticky.popitem(last=False)


def _sticky_drop(conv: str) -> None:
    _sticky.pop(conv, None)


def _premium_uids() -> list[str]:
    """有 credits 的账号(quota 刷新后 quota_exceeded=0;领取 100C 后翻 0)。"""
    try:
        data = db_load_accounts()
        return [a["uid"] for a in data["accounts"]
                if a.get("enabled", True) and not a.get("quota_exceeded", 1)]
    except Exception:
        return []


def _is_premium_model(model: str) -> bool:
    """lite 免费;其余(gpt-5/ultimate/auto/performance/efficient...)都要 credits。"""
    premium = {m.strip() for m in os.getenv("QODER_PREMIUM_MODELS", "").split(",") if m.strip()}
    if premium:
        return model in premium
    return model != "lite"


async def pick_session(payload: dict[str, Any]) -> tuple[SessionContext, str]:
    """粘性优先(同会话同账号 → 上游前缀缓存命中),冷却/失效则回退轮转。
    premium 模型(非 lite)强制路由到有 credits 的账号(每日 100C 领取后 quota_exceeded=0)。"""
    conv = conversation_fingerprint(payload)
    # premium 定向:粘性命中且该号有 credits → 直用;否则挑一个有 credits 的号
    if _is_premium_model(str(payload.get("model") or "lite")):
        uid = _sticky_get(conv)
        premium = _premium_uids()
        if uid and uid in premium and not _cooling(uid):
            try:
                return get_session_for_uid(uid), conv
            except Exception:
                _sticky_drop(conv)
        for cand in premium:
            if not _cooling(cand):
                try:
                    sess_p = get_session_for_uid(cand)
                    _sticky_put(conv, cand)
                    return sess_p, conv
                except Exception:
                    continue
        # 无 credits 号可用 → 走常规轮转(上游会 402,由既有错误路径兜底提示)
    uid = _sticky_get(conv)
    if uid and not _cooling(uid):
        try:
            return get_session_for_uid(uid), conv
        except Exception:
            _sticky_drop(conv)
    sess = await get_session()  # 现行 active 账号逻辑
    return sess, conv


def is_quota_error(exc: Exception) -> bool:
    """判断是否为 quota/限流类错误（429 / quota / rate limit）。
    这类错误需先查询真实限额确认，不能直接跳过账户。"""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429
    if isinstance(exc, RuntimeError):
        msg = str(exc).lower()
        return any(k in msg for k in ("http 429", "quota", "rate limit", "insufficient"))
    return False


def is_account_error(exc: Exception) -> bool:
    """判断是否'账号级'错误（token 无效/限额/服务端拒绝）。只有这类才应跳过账户。

    网络/流中断/超时（如 httpx.ReadError 的 incomplete chunk read）是临时性问题，
    换账户也无效，不应触发 rotate。
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (401, 403, 429)
    if isinstance(exc, httpx.HTTPError):
        return False  # 连接/超时/读错误等网络问题
    if isinstance(exc, RuntimeError):
        msg = str(exc).lower()
        if any(code in msg for code in ("http 401", "http 403", "http 429")):
            return True
        for kw in ("unauthorized", "invalid token", "quota", "rate limit",
                   "insufficient", "personal token", "credit"):
            if kw in msg:
                return True
    return False


@app.post("/v1/chat/completions")
async def chat_completions(payload: dict[str, Any], authorization: str | None = Header(default=None)):
    config = load_config()
    if config.get("auth_required", False):
        allowed_keys = config.get("allowed_keys", [])
        incoming_key = None
        if authorization and authorization.startswith("Bearer "):
            incoming_key = authorization[len("Bearer "):].strip()
        
        if not incoming_key or not any(_ct_equal(incoming_key, k) for k in allowed_keys):
            add_log("Access denied: Invalid or missing API Key in request header.", "WARNING")
            raise HTTPException(status_code=401, detail="Invalid or missing API Key")

    model = payload.get("model", "lite")
    stream = bool(payload.get("stream", False))
    messages_count = len(payload.get("messages", []))
    add_log(f"Incoming completion request: model={model}, stream={stream}, messages={messages_count}")
    
    accounts_data = db_load_accounts()
    enabled_count = sum(1 for acc in accounts_data["accounts"] if acc.get("enabled", True))
    max_retries = max(1, enabled_count)
    
    import time as _time
    _t0 = _time.perf_counter()
    for attempt in range(max_retries):
        conv = None
        try:
            sess, conv = await pick_session(payload)
            sem = _sem_for(sess.identity.uid)
            add_log(f"Request routing via account: {sess.identity.name} ({sess.identity.uid[:13]}...) sticky={bool(_sticky.get(conv or ''))}")
            if stream:
                await sem.acquire()  # 整个流期间占用;wrapper 结束时释放
                try:
                    gen = stream_openai_response(payload, sess)
                    try:
                        first_item = await asyncio.wait_for(gen.__anext__(), timeout=_FIRST_TOKEN_BUDGET)
                    except StopAsyncIteration:
                        first_item = None
                except BaseException:
                    sem.release()
                    raise

                _ttfb = int(( _time.perf_counter() - _t0) * 1000)
                _usage = {"in_tokens": 0, "out_tokens": 0, "credits": 0.0}
                _req_uid, _req_model = sess.identity.uid, model

                async def stream_success_wrapper(first, g, held_sem):
                    _t_start = _time.perf_counter()
                    try:
                        if first is not None:
                            yield first
                        async for chunk in g:
                            u = parse_usage_frame(chunk)
                            if u:
                                _usage.update(u)
                            yield chunk
                    finally:
                        held_sem.release()
                        record_req(_req_model, _req_uid, 200, True, _ttfb,
                                   int((_time.perf_counter() - _t_start) * 1000),
                                   _usage["in_tokens"], _usage["out_tokens"], _usage["credits"])

                add_log(f"Streaming response initiated (Attempt {attempt+1}/{max_retries}).")
                _sticky_put(conv, sess.identity.uid)
                return StreamingResponse(
                    stream_success_wrapper(first_item, gen, sem),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
                )
            else:
                async with sem:
                    add_log(f"Generating full completion response (Attempt {attempt+1}/{max_retries})...")
                    resp = await complete_openai_response(payload, sess)
                add_log("Completion request finished successfully.")
                _sticky_put(conv, sess.identity.uid)
                _u = (resp or {}).get("usage") or {}
                record_req(model, sess.identity.uid, 200, False,
                           int((_time.perf_counter() - _t0) * 1000), int((_time.perf_counter() - _t0) * 1000),
                           _u.get("prompt_tokens", 0), _u.get("completion_tokens", 0), _u.get("credit", 0) or 0)
                return resp
        except Exception as exc:
            current_uid = sess.identity.uid if 'sess' in locals() else "unknown"
            if is_account_error(exc):
                if is_quota_error(exc):
                    # quota 类错误：先发一次请求确认是否真正 exceeded，而不是直接跳过
                    q = get_account_quota(current_uid)
                    if q.get("ok"):
                        quota = q["quota"]
                        truly_exceeded = bool(quota.get("isQuotaExceeded")) or (quota.get("userQuota") or {}).get("remaining", 1) <= 0
                        if not truly_exceeded:
                            add_log(f"Quota check on {current_uid}: NOT exceeded (remaining={quota.get('userQuota', {}).get('remaining')}), not rotating.", "WARNING")
                            raise HTTPException(status_code=502, detail=f"{exc}")
                        add_log(f"Quota confirmed exceeded for {current_uid}: {exc}. Rotating...", "WARNING")
                    else:
                        # 限额查询失败：无法确认，保守不跳过账户
                        add_log(f"Quota check failed for {current_uid} ({q.get('error')}), not rotating.", "WARNING")
                        raise HTTPException(status_code=502, detail=f"{exc}")
                else:
                    add_log(f"Account-level error on {current_uid}: {exc}. Rotating to next account...", "WARNING")
                if conv:
                    _sticky_drop(conv)
                if is_quota_error(exc):
                    _cooldown(current_uid, 60.0)
                    # premium credits 耗尽:快速刷新该号超额位,避免后续继续命中
                    with get_db() as _conn:
                        _conn.execute("UPDATE accounts SET quota_exceeded = 1 WHERE uid = ?", (current_uid,))
                try:
                    rotate_next_account(current_uid, str(exc))
                except Exception as e:
                    add_log(f"Failed to rotate account: {e}", "ERROR")
                    raise HTTPException(status_code=502, detail=f"Request failed and no other account is available. Error: {exc}")
            else:
                add_log(f"Transient error on account {current_uid}: {exc}. Not rotating account.", "WARNING")
                raise HTTPException(status_code=502, detail=str(exc))
                
    raise HTTPException(status_code=502, detail="Request failed on all available accounts.")


def main() -> None:
    import uvicorn

    _start_background_loops()  # token 刷新(6h)/余额刷新(09:00,21:00)/每日保活(10:00)

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("QODER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("QODER_PORT", "5050")))
    args = parser.parse_args()
    uvicorn.run("qoder2api.app:app", host=args.host, port=args.port, reload=False)

if __name__ == "__main__":
    main()
