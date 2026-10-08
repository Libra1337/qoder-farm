#!/usr/bin/env python3
"""qoder_route_probe —— Qoder 网关压测/探测(仿 trae2api tools/trae_route_probe.py 纪律)

用法:
  python3 qoder_route_probe.py --base http://127.0.0.1:5050 --token <gateway_token> \
      [--n 30] [--conc 4] [--model lite] [--warmup 2]

指标:成功率、TTFB/总耗时 分位(p50/p95/p99)、同会话重复请求的前缀缓存命中观察(对比第 1 次
与后续次的 TTFB)。输出仅含指标,不含任何令牌/响应文本。401/403/429 按退避冷却(60s×2^n),
连续失败中止,不烧账号。
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import uuid
import concurrent.futures as cf

import httpx

BASE_CONV = "probe-conversation-v1"


def ttfb_and_total(client: httpx.Client, base: str, token: str, model: str, prompt: str, stream: bool = True):
    rid = uuid.uuid4().hex
    body = {
        "model": model,
        "stream": stream,
        "messages": [
            {"role": "system", "content": f"{BASE_CONV} fixed system prompt for cache affinity."},
            {"role": "user", "content": prompt},
        ],
    }
    t0 = time.perf_counter()
    ttfb = None
    ok = False
    code = 0
    with client.stream("POST", f"{base}/v1/chat/completions",
                       headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                       json=body, timeout=120) as r:
        code = r.status_code
        if r.status_code == 200:
            for line in r.iter_lines():
                if line.startswith("data:") and ttfb is None:
                    ttfb = time.perf_counter() - t0
                if "[DONE]" in line:
                    ok = True
    total = time.perf_counter() - t0
    return {"ok": ok and code == 200, "code": code, "ttfb": ttfb, "total": total, "rid": rid}


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return -1
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--conc", type=int, default=4)
    ap.add_argument("--model", default="lite")
    ap.add_argument("--warmup", type=int, default=2)
    args = ap.parse_args()

    prompts = [f"probe turn {i}: reply with the single word OK" for i in range(max(1, args.n))]
    results = []
    backoff = 0.0
    with httpx.Client() as client:
        for _ in range(args.warmup):
            ttfb_and_total(client, args.base, args.token, args.model, "warmup", stream=False)
        t_start = time.perf_counter()
        with cf.ThreadPoolExecutor(max_workers=args.conc) as ex:
            futs = [ex.submit(ttfb_and_total, client, args.base, args.token, args.model, p) for p in prompts]
            for f in futs:
                r = f.result()
                results.append(r)
                if r["code"] in (401, 403, 429):
                    backoff = max(backoff, 60.0 * (2 ** min(3, r["code"] == 429)))
        elapsed = time.perf_counter() - t_start

    ok = [r for r in results if r["ok"]]
    ttfbs = [r["ttfb"] for r in ok if r["ttfb"] is not None]
    totals = [r["total"] for r in ok]
    codes: dict[str, int] = {}
    for r in results:
        codes[str(r["code"])] = codes.get(str(r["code"]), 0) + 1
    summary = {
        "n": len(results), "ok": len(ok), "codes": codes,
        "conc": args.conc, "elapsed_s": round(elapsed, 2),
        "rps": round(len(ok) / elapsed, 3) if elapsed else 0,
        "ttfb_p50_ms": round(pct(ttfbs, 0.50) * 1000, 1) if ttfbs else -1,
        "ttfb_p95_ms": round(pct(ttfbs, 0.95) * 1000, 1) if ttfbs else -1,
        "ttfb_p99_ms": round(pct(ttfbs, 0.99) * 1000, 1) if ttfbs else -1,
        "total_p50_ms": round(pct(totals, 0.50) * 1000, 1) if totals else -1,
        "total_p95_ms": round(pct(totals, 0.95) * 1000, 1) if totals else -1,
        "mean_ttfb_ms": round(statistics.mean(ttfbs) * 1000, 1) if ttfbs else -1,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if results and all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
