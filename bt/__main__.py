# ============================================================================
# bt/__main__.py — CLI: python -m bt run ...
# ----------------------------------------------------------------------------
# python -m bt run --strategy mai_ruay --config configs/mairuay_v204.yaml \
#                  --data data/XAUUSD_M1_2026-06-09.csv --out runs/v204/
#   [--global configs/global.yaml]
# ============================================================================
from __future__ import annotations

import argparse
import sys

import yaml

import os

from bt import engine, report, viewer
from bt.data import load_csv
from bt.strategies.mai_ruay import MaiRuay

STRATEGIES = {"mai_ruay": MaiRuay}


def _run(args) -> int:
    if args.strategy not in STRATEGIES:
        print(f"❌ ไม่รู้จัก strategy: {args.strategy} (มี: {', '.join(STRATEGIES)})")
        return 1
    scfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    gcfg = yaml.safe_load(open(args.global_config, encoding="utf-8"))
    portfolio = gcfg.get("portfolio", 1000.0)
    guards = gcfg.get("guards")
    inject_compat = gcfg.get("engine", {}).get("inject_compat", False)

    bars = load_csv(args.data)
    strat = STRATEGIES[args.strategy](scfg, portfolio, pre_fill_cancel=inject_compat)
    result = engine.run(bars, strat, guards=guards, inject_compat=inject_compat)
    summary = report.write_reports(result, args.out, portfolio)

    # ไม้ที่ไม่ fill (analytics ล้วน) = ที่ strategy cancel (proximity/expiry)
    # + pending ค้างท้ายไฟล์ (วน strat._pend หลัง run → reason=end_of_data)
    unfilled = list(getattr(strat, "unfilled", []))
    last_i, last_t = len(bars) - 1, str(bars[-1].time)
    for tag, m in getattr(strat, "_pend", {}).items():
        unfilled.append({
            "plan_id": m.get("plan_id"), "tag": tag, "direction": m["dir"], "kind": "LIMIT",
            "limit_price": m.get("price"), "place_time": m.get("place_time"),
            "place_bar": m["placed"], "death_time": last_t, "death_bar": last_i,
            "reason": "end_of_data",
        })
    report.write_unfilled_csv(unfilled, os.path.join(args.out, "unfilled.csv"))
    if args.viewer:
        viewer.write_viewer(bars, result["trades"],
                            os.path.join(args.out, "viewer.html"),
                            title=args.strategy, unfilled=unfilled)

    mode = "inject_compat (v2.04)" if inject_compat else "correct"
    print(f"\n{'='*52}")
    print(f"  {args.strategy}  ·  bars={len(bars)}  ·  mode={mode}")
    print(f"{'='*52}")
    print(f"  ไม้ทั้งหมด   : {summary['n_trades']}  (TP {summary['n_wins']} / SL {summary['n_losses']})")
    print(f"  Win rate     : {summary['win_rate']:.2f}%")
    print(f"  จุดเข้า      : {summary['n_plans']}  (win {summary['plan_wins']} / {summary['plan_win_rate']:.2f}%)")
    print(f"  Net pip      : {summary['net_pip']:+.0f}")
    print(f"  Net USD      : {summary['net_usd']:+.2f}")
    print(f"  Portfolio    : {summary['init_portfolio']:.0f} → {summary['final_portfolio']:.2f}")
    print(f"  ไม้ไม่ fill   : {len(unfilled)}  (proximity/expiry/end_of_data)")
    files = "trades.csv · plans.csv · summary.json · unfilled.csv" + (" · viewer.html" if args.viewer else "")
    print(f"  → {args.out}  ({files})")
    print(f"{'='*52}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m bt")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="รัน backtest แล้วเขียนผลลง --out")
    r.add_argument("--strategy", required=True)
    r.add_argument("--config", required=True, help="config ต่อกลยุทธ์ (YAML)")
    r.add_argument("--data", required=True, help="CSV OHLC (Time,O,H,L,C[,V] ไม่มี header)")
    r.add_argument("--out", required=True, help="โฟลเดอร์ผลลัพธ์ runs/<name>/")
    r.add_argument("--global", dest="global_config", default="configs/global.yaml")
    r.add_argument("--viewer", action="store_true", help="เขียน viewer.html (chart แบบ MT5, offline)")
    args = p.parse_args(argv)
    if args.cmd == "run":
        return _run(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
