# ============================================================================
# bt/report.py
# ----------------------------------------------------------------------------
# Report layer — เขียนผลจาก engine.run() เป็น 3 ไฟล์ใน runs/<name>/
#   trades.csv   : ราย "ไม้" (per Order/tag) — field ครบสำหรับ viewer
#   plans.csv    : ตาม "จุดเข้า" (group by plan_id, แพ้/ชนะ = net pnl รวม)
#   summary.json : สกอร์บอร์ด
# ============================================================================
from __future__ import annotations

import csv
import json
import os
from collections import OrderedDict, defaultdict

TRADE_FIELDS = [
    "plan_id", "tag", "direction", "kind",
    "entry_time", "entry", "sl", "tp",
    "exit_time", "exit_price", "result", "lot",
    "pnl_pips", "pnl_usd", "entry_bar", "exit_bar",
]
PLAN_FIELDS = [
    "plan_id", "direction", "entry_time", "n_trades",
    "net_pnl_pips", "net_pnl_usd", "result",
]
UNFILLED_FIELDS = [
    "plan_id", "tag", "direction", "kind", "limit_price", "sl", "tp",
    "place_time", "place_bar", "death_time", "death_bar", "reason",
]
PLAN_META_FIELDS = [
    "plan_id", "father_start_bar", "father_end_bar", "father_pct",
    "mother_bar", "mother_pct", "reasons",
]


def _price(v):
    return round(v, 3) if isinstance(v, float) else v


def write_trades_csv(trades: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TRADE_FIELDS)
        w.writeheader()
        for t in trades:
            row = {k: t.get(k) for k in TRADE_FIELDS}
            for k in ("entry", "sl", "tp", "exit_price"):
                row[k] = _price(row[k])
            for k in ("entry_time", "exit_time"):
                row[k] = str(row[k])
            w.writerow(row)


def build_plans(trades: list[dict]) -> list[dict]:
    """รวมไม้ตาม plan_id → 1 แถวต่อจุดเข้า · แพ้/ชนะ = net pnl_usd รวม"""
    groups = defaultdict(list)
    for t in trades:
        groups[t["plan_id"]].append(t)
    plans = []
    for pid in sorted(groups):
        g = groups[pid]
        net_pip = round(sum(t["pnl_pips"] for t in g), 1)
        net_usd = round(sum(t["pnl_usd"] for t in g), 2)
        plans.append({
            "plan_id": pid,
            "direction": g[0]["direction"],
            "entry_time": min(str(t["entry_time"]) for t in g),
            "n_trades": len(g),
            "net_pnl_pips": net_pip,
            "net_pnl_usd": net_usd,
            "result": "WIN" if net_usd > 0 else "LOSS",
        })
    return plans


def write_plans_csv(plans: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PLAN_FIELDS)
        w.writeheader()
        w.writerows(plans)


def write_unfilled_csv(unfilled: list[dict], path: str) -> None:
    """ไม้ที่ไม่ fill (pending ที่ cancel/expire/end_of_data) — analytics สำหรับ viewer"""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=UNFILLED_FIELDS)
        w.writeheader()
        for u in unfilled:
            row = {k: u.get(k) for k in UNFILLED_FIELDS}
            for k in ("limit_price", "sl", "tp"):
                row[k] = _price(row[k])
            w.writerow(row)


def write_plan_meta_csv(plan_meta: list[dict], path: str) -> None:
    """พ่อ/แม่/%/เหตุผล ต่อ plan_id (analytics ล้วน) — ไฟล์ใหม่ ไม่ยุ่ง plans.csv"""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PLAN_META_FIELDS)
        w.writeheader()
        for m in plan_meta:
            row = {k: m.get(k) for k in PLAN_META_FIELDS}
            if isinstance(row["reasons"], (list, tuple)):
                row["reasons"] = " · ".join(row["reasons"])
            w.writerow(row)


def build_summary(trades: list[dict], plans: list[dict], portfolio: float) -> dict:
    wins = [t for t in trades if t["result"] == "TP"]
    net_pip = round(sum(t["pnl_pips"] for t in trades), 1)
    net_usd = round(sum(t["pnl_usd"] for t in trades), 2)
    pwins = [p for p in plans if p["result"] == "WIN"]
    n = len(trades)
    np_ = len(plans)
    return OrderedDict([
        ("n_trades",       n),
        ("n_wins",         len(wins)),
        ("n_losses",       n - len(wins)),
        ("win_rate",       round(100 * len(wins) / n, 2) if n else 0.0),
        ("net_pip",        net_pip),
        ("net_usd",        net_usd),
        ("init_portfolio", portfolio),
        ("final_portfolio", round(portfolio + net_usd, 2)),
        ("n_plans",        np_),
        ("plan_wins",      len(pwins)),
        ("plan_win_rate",  round(100 * len(pwins) / np_, 2) if np_ else 0.0),
    ])


def write_reports(result: dict, out_dir: str, portfolio: float) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    trades = result["trades"]
    plans = build_plans(trades)
    summary = build_summary(trades, plans, portfolio)
    write_trades_csv(trades, os.path.join(out_dir, "trades.csv"))
    write_plans_csv(plans, os.path.join(out_dir, "plans.csv"))
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    return summary
