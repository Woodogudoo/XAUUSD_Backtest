# ============================================================================
# bt/metrics.py — Dashboard metrics (pure functions)
# ----------------------------------------------------------------------------
# คำนวณสถิติจาก trades.csv (+ derive plans จาก trades) — ไม่ import engine/strategy
# ไม่แตะไฟล์ผลใดๆ · อ่านอย่างเดียว · ทุนตั้งต้นคงที่ $1000 ไม่ทบต้น
#
# unit: ใช้คอลัมน์ pnl_usd (USD) โดยตรง — ยืนยันแล้วว่า trades.csv มี pnl_usd
#
# scalar keys ↔ METRIC_DEFS:
#   - usd/pct rows  : net_usd, net_pct, max_dd_usd, ... (1 def = 1 key)
#   - biggest_*     : def key biggest_win → scalar biggest_win_usd + biggest_win_pct
#   - streak (8 ค่า): def key win_run → scalar win_run_order + win_run_plan (และอีก 3 คู่)
#
# sign convention (เพื่อ ★ ใช้ direction='max' ได้สม่ำเสมอ):
#   max_dd_usd/pct เก็บเป็น "ค่าติดลบ" (drawdown 300 → -300) · least-negative = ดีสุด = max
#   recovery_factor ใช้ |max_dd| (magnitude) ตาม data-calc "Net ÷ Max DD"
# ============================================================================
from __future__ import annotations

import csv
import os
from collections import OrderedDict
from datetime import datetime

INIT_CAPITAL = 1000.0


# ---------- helpers ----------
def _to_float(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _iso(ts: str) -> str:
    """'2026-01-06 13:26:00' → ISO '2026-01-06T13:26:00' (parse ไม่ได้ → คืนเดิม)"""
    s = str(ts).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError:
            continue
    return s


def _sort_key(ts: str):
    """key เรียงเวลา — parse ได้ใช้ datetime, ไม่ได้ fallback string"""
    s = str(ts).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return (0, datetime.strptime(s, fmt))
        except ValueError:
            continue
    return (1, s)


def _round(x, n=2):
    return round(x, n) if x is not None else None


def load_trades(run_dir: str) -> list[dict]:
    """อ่าน trades.csv → list[{plan_id, pnl, exit_time}] (เรียงตามไฟล์)
    ไม่มีไฟล์ → FileNotFoundError"""
    path = os.path.join(run_dir, "trades.csv")
    out = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append({
                "plan_id": row.get("plan_id"),
                "pnl": _to_float(row.get("pnl_usd")),
                "result": row.get("result") or "",
                "exit_time": row.get("exit_time") or "",
            })
    return out


# ---------- equity ----------
def equity_by_trade(trades: list[dict]) -> tuple[list[dict], list[float]]:
    """ต่อไม้: เรียง exit_time · เริ่ม 1000 · สะสม pnl
    → (series[{exit_time, equity_usd, equity_pct}], equity_points พร้อม baseline 1000)"""
    ordered = sorted(trades, key=lambda t: _sort_key(t["exit_time"]))
    eq = INIT_CAPITAL
    series, points = [], [INIT_CAPITAL]
    for t in ordered:
        eq += t["pnl"]
        points.append(eq)
        series.append({
            "exit_time": _iso(t["exit_time"]),
            "equity_usd": round(eq, 2),
            "equity_pct": round((eq - INIT_CAPITAL) / INIT_CAPITAL * 100, 4),
        })
    return series, points


def plan_nets(trades: list[dict]) -> list[tuple[str, float]]:
    """group ตาม plan_id → (close_time = exit ล่าสุดในแผน, net = Σpnl) เรียงตาม close_time"""
    groups: "OrderedDict[str, dict]" = OrderedDict()
    for t in trades:
        pid = t["plan_id"]
        g = groups.get(pid)
        if g is None:
            groups[pid] = {"net": t["pnl"], "close": t["exit_time"]}
        else:
            g["net"] += t["pnl"]
            if _sort_key(t["exit_time"]) > _sort_key(g["close"]):
                g["close"] = t["exit_time"]
    rows = [(g["close"], g["net"]) for g in groups.values()]
    rows.sort(key=lambda r: _sort_key(r[0]))
    return rows


def equity_by_plan(trades: list[dict]) -> list[dict]:
    """ต่อแผน: net รวมต่อแผน เรียงตามเวลาแผนปิด → [{close_time, equity_usd, equity_pct}]"""
    eq = INIT_CAPITAL
    series = []
    for close, net in plan_nets(trades):
        eq += net
        series.append({
            "close_time": _iso(close),
            "equity_usd": round(eq, 2),
            "equity_pct": round((eq - INIT_CAPITAL) / INIT_CAPITAL * 100, 4),
        })
    return series


# ---------- streaks ----------
def win_run(vals: list[float]) -> int:
    """run ชนะล้วน: pnl>0 นับ, pnl≤0 รีเซ็ต"""
    best = cur = 0
    for v in vals:
        if v > 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def loss_run(vals: list[float]) -> int:
    """run แพ้ล้วน: pnl<0 นับ, pnl≥0 รีเซ็ต"""
    best = cur = 0
    for v in vals:
        if v < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def win_tally(vals: list[float]) -> int:
    """tally วิ่ง: win +1, loss −1, เสมอ(=0) ไม่เปลี่ยน, clamp 0 → max"""
    best = cur = 0
    for v in vals:
        if v > 0:
            cur += 1
        elif v < 0:
            cur -= 1
        if cur < 0:
            cur = 0
        best = max(best, cur)
    return best


def loss_tally(vals: list[float]) -> int:
    """tally วิ่ง: loss +1, win −1, เสมอ ไม่เปลี่ยน, clamp 0 → max"""
    best = cur = 0
    for v in vals:
        if v < 0:
            cur += 1
        elif v > 0:
            cur -= 1
        if cur < 0:
            cur = 0
        best = max(best, cur)
    return best


# ---------- drawdown / runup / peak (บนเส้นทุนต่อไม้ รวม baseline 1000) ----------
def _dd_runup_peak(points: list[float]) -> tuple[float, float, float]:
    peak = -1e18
    run_min = 1e18
    max_eq = -1e18
    dd_mag = 0.0
    runup = 0.0
    for e in points:
        peak = max(peak, e)
        run_min = min(run_min, e)
        max_eq = max(max_eq, e)
        dd_mag = max(dd_mag, peak - e)
        runup = max(runup, e - run_min)
    return dd_mag, runup, max_eq - INIT_CAPITAL


# ---------- main ----------
def compute_metrics(run_dir: str) -> dict:
    """คำนวณ metrics ทั้งหมดจาก trades.csv ใน run_dir (ไม่มีไฟล์ → FileNotFoundError)"""
    trades = load_trades(run_dir)
    pnls = [t["pnl"] for t in trades]
    n = len(pnls)

    by_trade, points = equity_by_trade(trades)
    by_plan = equity_by_plan(trades)
    plans = plan_nets(trades)
    n_plans = len(plans)

    net = round(sum(pnls), 2) if n else 0.0
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    # wr_order = hit-rate ของไม้ = result=='TP' (ตรงกับ summary.win_rate)
    #   หมายเหตุ: ไม้ TP ที่ lot ปัดเป็น 0 → pnl_usd=0 (นับเป็น win ของ hit-rate แต่ไม่กระทบ
    #   profit_factor/streak/biggest ที่อิง pnl ตาม spec)
    order_wins = sum(1 for t in trades if t["result"] == "TP")
    gross_win = sum(wins)
    gross_loss = sum(losses)

    # profit factor (ไม่มีไม้แพ้ → ∞ cap = None · ไม่มีไม้เลย → None)
    profit_factor = round(gross_win / abs(gross_loss), 3) if gross_loss != 0 else None
    expectancy = round(net / n, 2) if n else None

    dd_mag, runup, peak_profit = _dd_runup_peak(points)
    recovery = round(net / dd_mag, 3) if dd_mag > 0 else None

    # streak — ต่อไม้ (เรียง exit_time) และ ต่อแผน (เรียง close_time)
    order_pnls = [t["pnl"] for t in sorted(trades, key=lambda t: _sort_key(t["exit_time"]))]
    plan_pnls = [net_ for _, net_ in plans]

    plan_wins = sum(1 for _, net_ in plans if net_ > 0)

    def _pct(usd):
        return round(usd / INIT_CAPITAL * 100, 2) if usd is not None else None

    scalar = OrderedDict([
        # Win rate
        ("wr_order", round(order_wins / n * 100, 2) if n else 0.0),
        ("wr_plan", round(plan_wins / n_plans * 100, 2) if n_plans else 0.0),
        # ขนาด
        ("n_trades", n),
        ("n_plans", n_plans),
        # ผลตอบแทน
        ("net_usd", net),
        ("net_pct", _pct(net)),
        ("profit_factor", profit_factor),
        ("expectancy", expectancy),
        ("recovery_factor", recovery),
        # ความเสี่ยง/ผลตอบแทน (max_dd เก็บติดลบ — least-negative = ดีสุด)
        ("max_dd_usd", -round(dd_mag, 2)),
        ("max_dd_pct", -round(dd_mag / INIT_CAPITAL * 100, 2)),
        ("max_runup_usd", round(runup, 2)),
        ("max_runup_pct", round(runup / INIT_CAPITAL * 100, 2)),
        # จุดสูงสุดของทุน
        ("peak_profit_usd", round(peak_profit, 2)),
        ("peak_profit_pct", round(peak_profit / INIT_CAPITAL * 100, 2)),
        # ไม้เดี่ยว
        ("biggest_win_usd", round(max(pnls), 2) if n else None),
        ("biggest_win_pct", _pct(round(max(pnls), 2)) if n else None),
        ("biggest_loss_usd", round(min(pnls), 2) if n else None),
        ("biggest_loss_pct", _pct(round(min(pnls), 2)) if n else None),
        # สตรีค (8 ค่า: ต่อไม้ + ต่อแผน)
        ("win_run_order", win_run(order_pnls)),
        ("win_run_plan", win_run(plan_pnls)),
        ("loss_run_order", loss_run(order_pnls)),
        ("loss_run_plan", loss_run(plan_pnls)),
        ("win_tally_order", win_tally(order_pnls)),
        ("win_tally_plan", win_tally(plan_pnls)),
        ("loss_tally_order", loss_tally(order_pnls)),
        ("loss_tally_plan", loss_tally(plan_pnls)),
    ])
    scalar["equity_by_trade"] = by_trade
    scalar["equity_by_plan"] = by_plan
    return scalar


# ============================================================================
# METRIC_DEFS — single source of truth สำหรับ ★ (direction) และ ⓘ (calc_th)
# calc_th คัดลอกตรงจาก data-calc ใน docs/strategy_compare_mockup.html (ห้ามเขียนใหม่)
# fmt: 'usd'|'pct'|'ratio'|'count'|'streak' · direction: 'max'|'min'|'none'
# ============================================================================
def _def(label, calc, fmt, direction, group, order):
    return {"label_th": label, "calc_th": calc, "fmt": fmt,
            "direction": direction, "group": group, "order": order}


_G_WR = "Win rate"
_G_SIZE = "ขนาด"
_G_RET = "ผลตอบแทน"
_G_RISK = "ความเสี่ยง / ผลตอบแทน"
_G_PEAK = "จุดสูงสุดของทุน"
_G_STREAK = "สตรีค (ไม้ · แผน)"
_G_SINGLE = "ไม้เดี่ยว"

METRIC_DEFS = OrderedDict([
    ("wr_order", _def("WR ต่อ order",
        "ไม้ที่ pnl>0 ÷ จำนวนไม้ทั้งหมด.", "pct", "max", _G_WR, 1)),
    ("wr_plan", _def("WR ต่อ แผน",
        "แผนที่ net>0 ÷ จำนวนแผน. ชนะแผน = pnl รวมทุกไม้ในแผนนั้น > 0.", "pct", "max", _G_WR, 2)),

    ("n_trades", _def("จำนวนไม้",
        "นับ order ทั้งหมดใน trades.csv.", "count", "none", _G_SIZE, 3)),
    ("n_plans", _def("จำนวนแผน",
        "นับ plan_id ไม่ซ้ำใน plans.csv (1 แผน = 1 setup ที่ fill).", "count", "none", _G_SIZE, 4)),

    ("net_usd", _def("Net profit (USD)",
        "Σ pnl ของทุกไม้ใน trades.csv (หน่วย USD).", "usd", "max", _G_RET, 5)),
    ("net_pct", _def("Net %",
        "Net USD ÷ 1000 × 100. ทุนตั้งต้นคงที่ $1000 ไม่ทบต้น.", "pct", "max", _G_RET, 6)),
    ("profit_factor", _def("Profit factor",
        "Σกำไร(ไม้ที่ pnl>0) ÷ |Σขาดทุน(ไม้ที่ pnl<0)|. ถ้าไม่มีไม้แพ้เลย = ∞ (cap).",
        "ratio", "max", _G_RET, 7)),
    ("expectancy", _def("Expectancy",
        "Net ÷ จำนวนไม้ = กำไรคาดหวังต่อ 1 ไม้.", "usd", "max", _G_RET, 8)),
    ("recovery_factor", _def("Recovery factor",
        "Net ÷ Max DD (USD). ยิ่งสูง = ฟื้นจากจุดถอยได้คุ้ม. ถ้า DD=0 = ∞ (cap).",
        "ratio", "max", _G_RET, 9)),

    ("max_dd_usd", _def("Max drawdown (USD)",
        "ระยะถอยลงมากสุดจากยอด = max(peak − equity) ตลอดเส้นทุน.", "usd", "max", _G_RISK, 10)),
    ("max_dd_pct", _def("Max drawdown %",
        "Max DD USD ÷ 1000 × 100.", "pct", "max", _G_RISK, 11)),
    ("max_runup_usd", _def("Max runup (USD)",
        "ระยะวิ่งขึ้นมากสุดจากก้นล่าสุด = max(equity − ก้นต่ำสุดก่อนหน้า). เทียบกับ Max DD ที่เป็นฝั่งถอย.",
        "usd", "max", _G_RISK, 12)),
    ("max_runup_pct", _def("Max runup %",
        "Max runup USD ÷ 1000 × 100.", "pct", "max", _G_RISK, 13)),

    ("peak_profit_usd", _def("Peak profit (USD)",
        "ยอดสูงสุดที่เส้นทุนเคยไปถึง − 1000. = กำไรสูงสุดเทียบทุนตั้งต้น.", "usd", "max", _G_PEAK, 14)),
    ("peak_profit_pct", _def("Peak profit %",
        "Peak profit USD ÷ 1000 × 100.", "pct", "max", _G_PEAK, 15)),

    ("win_run", _def("ชนะติดกันสูงสุด",
        "run ชนะที่ยาวที่สุด. เจอไม้ที่ไม่ชนะ (pnl ≤ 0) ตัดสตรีคทันที. คิดทั้งต่อไม้ (order) และต่อแผน (net แผน).",
        "streak", "max", _G_STREAK, 16)),
    ("loss_run", _def("แพ้ติดกันสูงสุด",
        "run แพ้ที่ยาวที่สุด. เจอไม้ที่ไม่แพ้ (pnl ≥ 0) ตัดสตรีคทันที. คิดทั้งต่อไม้และต่อแผน.",
        "streak", "min", _G_STREAK, 17)),
    ("win_tally", _def("ชนะติดกันสะสม",
        "tally วิ่ง: ชนะ +1, แพ้ −1, เสมอ(pnl=0) ไม่เปลี่ยน, ไม่ต่ำกว่า 0 (แตะ 0 = รีเซ็ต) → เก็บค่าสูงสุดที่เคยขึ้นถึง. คิดทั้งต่อไม้และต่อแผน. (เวอร์ชันตัวเงิน = Max runup ด้านบน)",
        "streak", "max", _G_STREAK, 18)),
    ("loss_tally", _def("แพ้ติดกันสะสม",
        "tally วิ่ง: แพ้ +1, ชนะ −1, เสมอ(pnl=0) ไม่เปลี่ยน, ไม่ต่ำกว่า 0 (แตะ 0 = รีเซ็ต) → เก็บค่าสูงสุด. คิดทั้งต่อไม้และต่อแผน. (เวอร์ชันตัวเงิน = Max drawdown ด้านบน)",
        "streak", "min", _G_STREAK, 19)),

    ("biggest_win", _def("ไม้กำไรหนักสุด",
        "max(pnl) ของไม้เดียว. % = pnl ÷ 1000 × 100.", "usd", "max", _G_SINGLE, 20)),
    ("biggest_loss", _def("ไม้ขาดทุนหนักสุด",
        "min(pnl) ของไม้เดียว. % = pnl ÷ 1000 × 100.", "usd", "max", _G_SINGLE, 21)),
])
