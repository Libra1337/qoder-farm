#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qodergate-register —— Qoder 独立注册机

无限循环注册：每母线程每批 3 个子任务并发，浏览器隐藏后台，
人机验证时置顶等你滑滑块，划完自动轮到下一个；成功注册即导出 accounts.json。

用法：
  python -m qodergate_register --parents 2            # 2 个母线程
  python -m qodergate_register --parents 1 --output ./out.json
  python -m qodergate_register --check               # 检查邮箱提供方配置
  Ctrl+C 停止（当前批次完成后停止并打印统计）

环境变量（项目根 .env 或系统环境）：
  MAIL_PROVIDER=shiro|yyds       # 邮箱提供方，默认 shiro（mail.futile.page 自建邮局）
  SHIRO_API_KEY=sk_live-...      # shiro 必填
  SHIRO_BASE_URL=https://mail.futile.page
  SHIRO_DOMAIN_ID=2              # futile.page 域名 id
  YYDS_API_KEY=AC-...            # yyds 必填（MAIL_PROVIDER=yyds 时）
"""
from __future__ import annotations

import argparse
import json
import sys
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from .core import (
    _env,
    _yyds_key,
    mail_provider,
    get_registrar_status,
    start_registration,
    stop_registration,
)


def _check() -> int:
    provider = mail_provider()
    print(f"[check] MAIL_PROVIDER = {provider}")
    if provider == "yyds":
        key = _yyds_key()
        if key:
            print(f"[check] YYDS_API_KEY 已配置: {key[:3]}...{key[-4:]}")
            return 0
        print("[check] YYDS_API_KEY 未配置：请设置环境变量或在项目根 .env 添加 YYDS_API_KEY=AC-...")
        return 1
    key = _env("SHIRO_API_KEY")
    if not key:
        print("[check] SHIRO_API_KEY 未配置：请设置环境变量或在项目根 .env 添加 SHIRO_API_KEY=sk_live-...")
        return 1
    print(f"[check] SHIRO_API_KEY 已配置: {key[:8]}...{key[-4:]}")
    base = _env("SHIRO_BASE_URL") or "https://mail.futile.page"
    domain_id = _env("SHIRO_DOMAIN_ID") or "2"
    print(f"[check] ShiroMail: {base} domainId={domain_id}")
    # 连通性实测：列域名
    try:
        import httpx
        r = httpx.get(f"{base.rstrip('/')}/api/v1/domains", headers={"Authorization": f"Bearer {key}"}, timeout=15)
        if r.status_code == 200:
            items = r.json().get("items") or []
            doms = ", ".join(f"{d.get('domain')}(id={d.get('id')})" for d in items)
            print(f"[check] 域名列表: {doms or '(空)'}")
            if not any(str(d.get("id")) == str(domain_id) for d in items):
                print(f"[check] 警告: SHIRO_DOMAIN_ID={domain_id} 不在域名列表中，建邮箱会 forbidden")
                return 1
            return 0
        print(f"[check] 域名列表请求失败: HTTP {r.status_code}")
        return 1
    except Exception as e:
        print(f"[check] 连通性测试失败: {e}")
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Qoder 独立注册机（无限循环）")
    parser.add_argument("--parents", type=int, default=2, help="母线程数（1-6，每母线程 3 子任务并发）")
    parser.add_argument("--output", default=None, help="JSON 导出文件路径（默认项目根 accounts.json）")
    parser.add_argument("--check", action="store_true", help="检查配置后退出")
    args = parser.parse_args(argv)

    if args.check:
        return _check()

    if mail_provider() == "yyds":
        if not _yyds_key():
            print("[error] YYDS_API_KEY 未配置：请设置环境变量或在项目根 .env 添加 YYDS_API_KEY=AC-...")
            return 1
    elif not _env("SHIRO_API_KEY"):
        print("[error] SHIRO_API_KEY 未配置：请设置环境变量或在项目根 .env 添加 SHIRO_API_KEY=sk_live-...")
        return 1

    r = start_registration(parents=args.parents)
    if not r.get("ok"):
        print(f"[error] {r.get('error')}")
        return 1

    print(f"[main] 已启动 {args.parents} 个母线程，无限循环注册中。浏览器隐藏后台，滑块置顶时请操作。Ctrl+C 停止。")
    try:
        while True:
            time.sleep(3)
            st = get_registrar_status()
            stats = st["stats"]
            active = [f"{k}:{v['stage']}" for k, v in st["active"].items()]
            print(
                f"[main] 统计 成功{stats['success']} 失败{stats['failed']} 总计{stats['total']}"
                f" | 运行中 {len(active)} | 验证 {st.get('verification') or '-'}"
            )
    except KeyboardInterrupt:
        print("\n[main] 收到停止请求，当前批次完成后停止...")
        stop_registration()
        while True:
            time.sleep(2)
            st = get_registrar_status()
            if not st["running"]:
                break
        stats = st["stats"]
        print(f"[main] 已停止。本次共注册 {stats['success']} 个账户（失败 {stats['failed']}）")
        print(f"[main] 导出文件: {args.output or 'accounts.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
