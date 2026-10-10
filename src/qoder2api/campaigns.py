"""每日 100 Credits 活动领取(2026-10-10 实测打通,逆向自 shuishuipingan/qoder2api-hub)

链路(全部挂 openapi.qoder.sh,纯 Bearer):
  GET  /sash/api/v1/me/campaigns             列活动(需桌面端头+原生机器身份)
  POST /sash/api/v1/me/campaigns/{id}/claim  领取(幂等:replayed=true 表示今日已领)

两道门(缺一活动列表里就没有"每天领 100 Credits"):
  1. 桌面端请求头:User-Agent: Qoder + cosy-clienttype: 10(桌面端;CLI=5/Work=6)
     + cosy-version: 0.4.3。派生的"半套/全套假"机器头会被整条过滤(issue #10)。
  2. 原生机器身份:官方 umid 组件(runtime-info)产出 machineToken/machineType/
     machineCode。macOS 位置 ~/.qoder/.bin/runtime-info-darwin-arm64-*(或
     ~/Library/Application Support/com.qoder.app.stable/.bin/)。调用:
       echo '{"account": "<uid>"}' | <binary> prod --account-stdin
     身份是机器级的(所有账号同一份),vmInfo.isVm 必须为 false(虚拟机不行)。

按"人"去重(实测):同一机器身份下,第二个账号的活动列表会**直接隐藏**该活动
(等价 BLOCKED/SAME_PERSON_ALREADY_CLAIMED)。换身份:Windows/Linux 清
$HOME/.config/.locale_cfg 种子重跑组件即换;**macOS 种子位置未知(非该路径,
疑在 Keychain),轮换未破解**——当前每台 Mac 每天只能给一个账号领 100C。

领取成功后:CREDITS kind + amount=100 + ALL_MODELS + 30 天有效;isQuotaExceeded
立即翻 false(userQuota 仍显示 0,credits 走资源包通道)。生效模型(api2-v2
档位名,2026-10 实测):lite(免费常态)、gpt-5 ✅、ultimate ✅(=Claude 系,
系数 2.0);auto/performance/efficient 预计同开。COSY 老协议(api1-3)的具体
模型 key(qmodel_38max/qfmodel 等)见 ssp 仓库 qoder_catalog_intl.json。

用法:
  # 单号
  python -m qoder2api.campaigns --token dt-xxx --uid 01xxx
  # 全池(读网关数据库,给每个号试领)
  python -m qoder2api.campaigns --all
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import shutil
from pathlib import Path
from typing import Any

import httpx

OPENAPI = "https://openapi.qoder.sh"
DESKTOP_VERSION = os.getenv("QD_DESKTOP_VERSION", "0.4.3")
# 免责:仅供学习研究,遵守 Qoder 服务条款。


def find_umid_binary() -> str | None:
    """定位官方 umid 组件(runtime-info)。"""
    cands = []
    for d in (Path.home() / ".qoder" / ".bin",
              Path.home() / "Library/Application Support/com.qoder.app.stable/.bin",
              Path.home() / "Library/Application Support/QoderWork"):
        if d.is_dir():
            cands += [p for p in d.iterdir() if p.name.startswith("runtime-info")]
    return str(cands[0]) if cands else None


# 身份环境池(实测 2026-10-11:runtime-info 第一个参数即"风控环境",不同值产出
# 完全不同的 machineToken/Type/Code = 一台物理机自带多套设备身份,按号轮换)
IDENTITY_ENVS = [int(e) for e in os.getenv("QODER_UMID_ENVS", "0,3,4,5,6,7").split(",") if e.strip()]
_identity_cache: dict[int, dict[str, Any]] = {}


def _run_umid(env_num: int, uid: str) -> dict[str, Any] | None:
    binary = find_umid_binary()
    if not binary:
        return None
    try:
        out = subprocess.run([binary, str(env_num), "--account-stdin"],
                             input=json.dumps({"account": uid}).encode(), capture_output=True, timeout=30)
        d = json.loads(out.stdout.decode().splitlines()[0])
        if d.get("machineToken") and not d.get("vmInfo", {}).get("isVm"):
            return d
    except Exception:
        pass
    return None


def native_identity(uid: str, slot: int = 0) -> dict[str, Any] | None:
    """取真机器身份(按 slot 轮换身份池)。

    优先级:
      1. 环境变量单身份(QODER_UMID_TOKEN/TYPE/CODE,服务器无组件时用)
      2. 环境变量身份池(QODER_UMID_POOL="token:type:code;token:type:code;...")
      3. 本机组件按 env 参数(slot→IDENTITY_ENVS[slot])现算并缓存
    """
    env_tok = os.getenv("QODER_UMID_TOKEN", "").strip()
    if env_tok:
        return {"machineToken": env_tok,
                "machineType": os.getenv("QODER_UMID_TYPE", ""),
                "machineCode": os.getenv("QODER_UMID_CODE", "")}
    pool = os.getenv("QODER_UMID_POOL", "").strip()
    if pool:
        entries = [e for e in pool.split(";") if e.count(":") >= 2]
        if entries:
            slot %= len(entries)
            tok, mtype, mcode = entries[slot].split(":", 2)
            return {"machineToken": tok, "machineType": mtype, "machineCode": mcode}
    env_num = IDENTITY_ENVS[slot % len(IDENTITY_ENVS)]
    if env_num not in _identity_cache:
        _identity_cache[env_num] = _run_umid(env_num, uid) or {}
    return _identity_cache[env_num] or None


def _uid_slot(uid: str) -> int:
    """账号稳定派生身份槽位(同号恒定,异号尽量分散)。"""
    return int(hashlib.sha256(uid.encode()).hexdigest()[:2], 16)


def desktop_headers(token: str, uid: str, ident: dict[str, Any]) -> dict[str, str] | None:
    """桌面端 0.4.3 同款出站头;无原生身份时返回 None(领不到,别发假头)。"""
    if not ident or ident.get("__vm__") or not ident.get("machineToken"):
        return None
    import hashlib
    machine_id = hashlib.sha256(f"q2a:machine:{uid}".encode()).hexdigest()[:32]
    return {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Qoder",
        "cosy-clienttype": "10",
        "cosy-version": DESKTOP_VERSION,
        "cosy-machineid": machine_id,
        "cosy-machinetoken": ident["machineToken"],
        "cosy-machinetype": ident.get("machineType", ""),
        "cosy-machinecode": ident.get("machineCode", ""),
        "cosy-machineos": os.getenv("QODER_UMID_OS", "darwin_arm64"),
        "cosy-machinehostname": os.getenv("QODER_UMID_HOSTNAME", "MacBook-Pro.local"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def list_campaigns(token: str, uid: str, ident: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    ident = ident or native_identity(uid, slot=_uid_slot(uid))
    H = desktop_headers(token, uid, ident) if ident else None
    if not H:
        return []
    r = httpx.get(f"{OPENAPI}/sash/api/v1/me/campaigns", headers=H, timeout=25)
    if r.status_code != 200:
        return []
    return [c for c in (r.json().get("campaigns") or []) if isinstance(c, dict)]


def claim_daily(token: str, uid: str, ident: dict[str, Any] | None = None) -> dict[str, Any]:
    """找到「每天领 100 Credits」并领取。返回 {ok, status, amount, message}。"""
    if ident is None:
        ident = native_identity(uid, slot=_uid_slot(uid))
    camps = list_campaigns(token, uid, ident or {})
    daily = next((c for c in camps if c.get("actionType") == "CLAIM_BENEFIT"
                  and (c.get("benefit") or {}).get("kind") == "CREDITS"), None)
    if not daily:
        # 两种可能:今日该"人"已领(列表隐藏)或身份/头不对
        return {"ok": False, "status": "NOT_FOUND",
                "message": "活动未出现(同机器身份今日已领,或原生身份不可用)"}
    H = desktop_headers(token, uid, ident)
    r = httpx.post(f"{OPENAPI}/sash/api/v1/me/campaigns/{daily['campaignId']}/claim",
                   headers=H, content=b"{}", timeout=25)
    if r.status_code == 409:
        return {"ok": True, "status": "CLAIMED", "replayed": True, "message": "今日已领取(幂等)"}
    d = r.json()
    status = str(d.get("status") or "").upper()
    if status == "CLAIMED":
        amt = (d.get("benefit") or {}).get("amount") or 100
        # 本地记账(上游 Bearer 接口不暴露活动余额,面板靠这个 + quota_exceeded 展示)
        try:
            from .database import get_db
            with get_db() as _conn:
                _conn.execute("UPDATE accounts SET claimed_credits = COALESCE(claimed_credits, 0) + ?, quota_exceeded = 0 WHERE uid = ?", (amt, uid))
        except Exception:
            pass
        return {"ok": True, "status": "CLAIMED", "replayed": bool(d.get("replayed")),
                "amount": amt, "grant_id": d.get("grantId"), "message": f"领取成功 +{amt} Credits"}
    return {"ok": False, "status": status or f"HTTP{r.status_code}",
            "message": str(d.get("failureCode") or r.text[:120])}


def claim_all() -> dict[str, Any]:
    """网关全池逐号试领(读 SQLite)。"""
    from .database import get_db
    with get_db() as conn:
        rows = conn.execute("SELECT uid, name, security_oauth_token FROM accounts WHERE enabled = 1").fetchall()
    results = {}
    for r in rows:
        uid, name, tok = r["uid"], r["name"], r["security_oauth_token"]
        try:
            results[name] = claim_daily(tok, uid)
        except Exception as e:
            results[name] = {"ok": False, "message": str(e)[:120]}
    return results


def main() -> None:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--token"), p.add_argument("--uid")
    p.add_argument("--all", action="store_true")
    a = p.parse_args()
    if a.all:
        print(json.dumps(claim_all(), ensure_ascii=False, indent=2))
    elif a.token and a.uid:
        print(json.dumps(claim_daily(a.token, a.uid), ensure_ascii=False, indent=2))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# 每日定时领取(默认 09:05;领取目标 = QODER_CLAIM_TARGET 邮箱前缀,默认池中第一个账号)
# ---------------------------------------------------------------------------
CLAIM_SLOT_MIN = int(os.getenv("QODER_CLAIM_SLOT", str(9 * 60 + 5)))


def _claim_loop() -> None:
    import datetime as _dt
    import time as _time
    last = None
    while True:
        now = _dt.datetime.now()
        cur = now.hour * 60 + now.minute
        slot = (now.strftime("%Y%m%d"), CLAIM_SLOT_MIN if CLAIM_SLOT_MIN <= cur else None)
        if slot[1] is not None and slot != last:
            last = slot
            target = os.getenv("QODER_CLAIM_TARGET", "").strip()
            try:
                if target:
                    from .database import get_db
                    with get_db() as conn:
                        row = conn.execute("SELECT uid, name, security_oauth_token FROM accounts WHERE enabled=1 AND name LIKE ? LIMIT 1", (target + "%",)).fetchone()
                    res = claim_daily(row["security_oauth_token"], row["uid"]) if row else {}
                    print(f"[daily-claim] {row['name'] if row else '?'}: {res}", flush=True)
                else:
                    # 全池逐号领取(每号每窗口一次;身份池按号派生保证活动可见)
                    from .database import get_db
                    with get_db() as conn:
                        rows = conn.execute("SELECT uid, name, security_oauth_token FROM accounts WHERE enabled=1").fetchall()
                    ok = fail = 0
                    for r in rows:
                        try:
                            res = claim_daily(r["security_oauth_token"], r["uid"])
                            ok += bool(res.get("ok"))
                            fail += not res.get("ok")
                            print(f"[daily-claim] {r['name']}: {res.get('status')} {res.get('amount') or ''}", flush=True)
                        except Exception as e:
                            fail += 1
                            print(f"[daily-claim] {r['name']}: ERR {e}", flush=True)
                    print(f"[daily-claim] batch ok={ok} fail={fail}", flush=True)
            except Exception as e:
                print(f"[daily-claim] error: {e}", flush=True)
        _time.sleep(30)


def start_claim_loop() -> None:
    if os.getenv("QODER_CLAIM_DISABLED", "") == "1":
        return
    import threading
    threading.Thread(target=_claim_loop, daemon=True).start()
