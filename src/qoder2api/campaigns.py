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


def native_identity(uid: str) -> dict[str, Any] | None:
    """跑官方组件取真机器身份(machineToken/Type/Code);失败返回 None(不可用派生假身份——会被过滤)。"""
    binary = find_umid_binary()
    if not binary:
        return None
    try:
        out = subprocess.run([binary, "prod", "--account-stdin"],
                             input=json.dumps({"account": uid}), capture_output=True,
                             timeout=30)
        d = json.loads(out.stdout.decode())
        if d.get("vmInfo", {}).get("isVm"):
            return {"__vm__": True, **d}
        if d.get("machineToken"):
            return d
    except Exception:
        pass
    return None


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
        "cosy-machineos": "darwin_arm64",
        "cosy-machinehostname": "MacBook-Pro.local",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def list_campaigns(token: str, uid: str, ident: dict[str, Any]) -> list[dict[str, Any]]:
    H = desktop_headers(token, uid, ident)
    if not H:
        return []
    r = httpx.get(f"{OPENAPI}/sash/api/v1/me/campaigns", headers=H, timeout=25)
    if r.status_code != 200:
        return []
    return [c for c in (r.json().get("campaigns") or []) if isinstance(c, dict)]


def claim_daily(token: str, uid: str, ident: dict[str, Any] | None = None) -> dict[str, Any]:
    """找到「每天领 100 Credits」并领取。返回 {ok, status, amount, message}。"""
    ident = ident or native_identity(uid)
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
