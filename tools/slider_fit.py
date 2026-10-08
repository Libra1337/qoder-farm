#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""slider_fit —— 从 slider_attempts.jsonl 拟合/校验滑块一致性门阈值与融合权重。

背景(2026-10):
  求解器把缺口定位分解为两个互补信号——掩码边缘 NCC(edge)与掩码 RGB ZNCC(zncc),
  各归一化到 [0,1] 后按权重相加取峰,并用「两信号独立峰距 <= AGREE_TOL」作一致性门,
  超门则判不可信、换图重试(slider.py::solve_distance)。
  门阈值与权重原先来自合成样本台,本工具用**真实尝试记录**做数据驱动的复核/再拟合。

记录字段(slider.py::_log_attempt 落盘):
  ncc_x / zncc_x  两信号各自峰横坐标(自然像素)
  x               融合峰横坐标(自然像素)
  agree           |ncc_x - zncc_x|
  conf            边缘 NCC 峰值(过门时)
  method          fused | reject
  drag_display    实际拖动距离(显示系,300 宽)
  pass            该次尝试是否通过(True/False;拒绝换图记录 reason=reject 无 pass)
  off             本次叠加的偏移扫描量

真值来源:
  - 若记录含 pass=True:通过的 drag 反推真值(自然系)x_true ≈ piece_center + drag_display*W/300。
    工具默认用记录的 x 与 drag_display 的差(piece_center 项)自行消去,只需 pass 标签即可
    评估「门阈值 → 精度/覆盖率」的权衡,不依赖任何外部真值。
  - 合成台可额外写入 x_true 字段;含 x_true 时直接按 |x - x_true| <= tol 判定对错。

用法:
  python tools/slider_fit.py slider_attempts.jsonl                # 门阈值扫描(默认)
  python tools/slider_fit.py slider_attempts.jsonl --sweep-weight # 融合权重网格
  python tools/slider_fit.py slider_attempts.jsonl --tol 4        # 命中容差(自然 px)
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

TOL_DEFAULT = 4.0


def load(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            rows.append(rec)
    return rows


def _hit(rec: dict, tol: float) -> bool | None:
    """判定该记录命中与否;无真值信息返回 None。"""
    if "x_true" in rec and rec.get("x") is not None:
        try:
            return abs(float(rec["x"]) - float(rec["x_true"])) <= tol
        except (TypeError, ValueError):
            return None
    if rec.get("pass") is True:
        return True
    if rec.get("pass") is False:
        # 拖动后明确被判失败:融合峰不可信(可能真的偏,也可能服务端容差)
        return False
    return None


def sweep_gate(rows: list[dict], tol: float) -> None:
    """按 agree 阈值扫描:接受率 / 接受集精度 / 单次成功率。

    成本模型(重要):被拒绝的尝试同样要重刷换图,故目标是最大化**单次成功率**
    (= 接受率 × 精度),而非只看精度。end2end@1 即单次成功率;expected tries 为其倒数。
    """
    usable = [r for r in rows if r.get("agree") is not None]
    if not usable:
        print("无可用的 agree 字段记录(需要新版本 slider.py 落盘的记录)。")
        return
    print(f"records={len(usable)}  tol={tol}px")
    print(f"{'AGREE_TOL':>10s} {'accept%':>8s} {'precision':>10s} {'per-attempt':>12s} {'exp.tries':>10s}")
    best = None
    for thr in (2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 1e9):
        acc = [r for r in usable if float(r["agree"]) <= thr]
        label = "off" if thr > 1e8 else f"{thr:g}"
        if not acc:
            print(f"{label:>10s} {0:7.1f}% {'-':>10s} {0:11.1f}% {'-':>10s}")
            continue
        hits = sum(1 for r in acc if _hit(r, tol) is True)
        judged = sum(1 for r in acc if _hit(r, tol) is not None)
        prec = (hits / judged) if judged else float("nan")
        cov = len(acc) / len(usable)
        per_attempt = cov * (prec if judged else 0)
        tries = (1 / per_attempt) if per_attempt > 0 else float("inf")
        print(f"{label:>10s} {100*cov:7.1f}% {100*prec:9.2f}% {100*per_attempt:11.1f}% {tries:10.2f}")
        if per_attempt and (best is None or per_attempt > best[1]):
            best = (label, per_attempt, cov, prec)
    if best:
        print(f"\n推荐 AGREE_TOL={best[0]}  (单次成功率={100*best[1]:.1f}%, "
              f"accept={100*best[2]:.1f}%, precision={100*best[3]:.2f}%)")
        print("注:推荐值基于『单次成功率』;若风控优先(绝不上交不一致答案)选更小的 AGREE_TOL。")


def sweep_weight(rows: list[dict], tol: float) -> None:
    """融合权重网格:用 ncc_x/zncc_x 与真值,评估各权重下「一致性加权融合」的精度。

    无法从日志重算完整融合峰(只有两信号各自峰),故用可复现的近似:
      融合峰 ≈ (w_edge*ncc_x + w_zncc*zncc_x) / (w_edge + w_zncc)
    在一致性(agree<=AGREE 代理)成立时评估该组合的命中率,扫描权重看哪个组合最稳。
    仅统计含 x_true 的记录(合成台可写入)。
    """
    usable = [r for r in rows if r.get("x_true") is not None
              and r.get("ncc_x") is not None and r.get("zncc_x") is not None]
    if not usable:
        print("无可用记录(需 x_true + ncc_x + zncc_x;合成台生成或含真值标注的真实记录)。")
        return
    print(f"records={len(usable)}  tol={tol}px  融合峰 ≈ (we*ncc + wz*zncc)/(we+wz)")
    print(f"{'w_edge':>7s} {'w_zncc':>7s} {'hit%':>7s} {'median_err':>11s}")
    grid = [(1, 0), (3, 1), (2, 1), (1, 1), (1, 2), (1, 3), (0, 1)]
    best = None
    for we, wz in grid:
        errs = []
        for r in usable:
            x = (we * float(r["ncc_x"]) + wz * float(r["zncc_x"])) / (we + wz)
            errs.append(abs(x - float(r["x_true"])))
        errs.sort()
        hit = sum(1 for e in errs if e <= tol) / len(errs)
        med = errs[len(errs) // 2]
        print(f"{we:>7d} {wz:>7d} {100*hit:6.1f}% {med:11.1f}")
        if best is None or hit > best[1]:
            best = ((we, wz), hit, med)
    print(f"\n推荐 w_edge={best[0][0]} w_zncc={best[0][1]}  (hit={100*best[1]:.1f}%, median_err={best[2]:.1f}px)")
    print("注:这是两信号峰的线性融合近似,仅作粗筛;生产 solve_distance 用的是归一化相关图"
          "逐像素相加后取峰,非线性、更准,故合成台上等权(1,1)的实际表现好于此处估计。")


def summarize(rows: list[dict]) -> None:
    n = len(rows)
    by_method = defaultdict(int)
    rej = 0
    for r in rows:
        by_method[r.get("method") or r.get("reason") or "?"] += 1
        if r.get("method") == "reject" or r.get("reason") == "reject":
            rej += 1
    passed = sum(1 for r in rows if r.get("pass") is True)
    print(f"总记录 {n};method 分布 {dict(by_method)};拒绝 {rej};通过 {passed}")
    agrees = [float(r["agree"]) for r in rows if r.get("agree") is not None]
    if agrees:
        agrees.sort()
        q = lambda p: agrees[min(len(agrees) - 1, int(len(agrees) * p))]
        print(f"agree 分位: p50={q(.5):.1f} p90={q(.9):.1f} p99={q(.99):.1f} max={agrees[-1]:.1f}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="从 slider_attempts.jsonl 拟合/校验滑块一致性门")
    ap.add_argument("path", nargs="?", default="slider_attempts.jsonl", help="尝试记录文件")
    ap.add_argument("--tol", type=float, default=TOL_DEFAULT, help="命中容差(自然 px,默认 4)")
    ap.add_argument("--sweep-weight", action="store_true", help="改为扫描融合权重")
    args = ap.parse_args(argv)

    p = Path(args.path)
    if not p.exists():
        print(f"记录文件不存在: {p}\n(先在注册机运行中积累尝试记录,或用合成台生成带 x_true 的记录)")
        return 1
    rows = load(p)
    if not rows:
        print("记录为空。")
        return 1
    summarize(rows)
    print()
    if args.sweep_weight:
        sweep_weight(rows, args.tol)
    else:
        sweep_gate(rows, args.tol)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())