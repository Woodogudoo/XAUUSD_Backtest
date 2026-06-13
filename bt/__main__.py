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
from bt.strategies.registry import STRATEGIES


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
            "limit_price": m.get("price"), "sl": m.get("sl"), "tp": m.get("tp_sel"),
            "place_time": m.get("place_time"), "place_bar": m["placed"],
            "death_time": last_t, "death_bar": last_i, "reason": "end_of_data",
        })
    report.write_unfilled_csv(unfilled, os.path.join(args.out, "unfilled.csv"))

    # plan_meta (analytics ล้วน): พ่อ/แม่/%/เหตุผล ต่อ plan_id — ไฟล์ใหม่ ไม่แตะ plans.csv
    pm = getattr(strat, "plan_meta", {})
    plan_meta = [pm[pid] for pid in sorted(pm)]
    report.write_plan_meta_csv(plan_meta, os.path.join(args.out, "plan_meta.csv"))

    # config snapshot (additive logging-only · ไฟล์ใหม่ ไม่กระทบ trades/plans/summary — md5 นิ่ง)
    # เก็บ "config ที่ resolve แล้ว" = strategy + runtime (global+mode/lot ที่ใช้จริง) → config diff หน้าเทียบ
    config_used = dict(scfg)
    config_used["_run"] = {
        "strategy": args.strategy,
        "data": args.data,             # path data CSV ที่ใช้ — bt viewer regenerate อ่านจากนี่
        "config": args.config,
        "mode": "compat" if inject_compat else "correct",
        "lot": "flat" if gcfg.get("winrate_mode") else "risk",
        "portfolio": portfolio,
        "inject_compat": inject_compat,
        "winrate_mode": bool(gcfg.get("winrate_mode", False)),
        "guards": gcfg.get("guards"),
    }
    with open(os.path.join(args.out, "config_used.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(config_used, f, allow_unicode=True, sort_keys=False)

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
    files = "trades.csv · plans.csv · summary.json · unfilled.csv · plan_meta.csv" + (" · viewer.html" if args.viewer else "")
    print(f"  → {args.out}  ({files})")
    print(f"{'='*52}")
    return 0


def _read_run_csv(path, int_fields, float_fields) -> list[dict]:
    """อ่าน CSV ของ run กลับเป็น list[dict] — แปลงชนิดให้ตรงกับตอน bt run (int/float/str)
    เพื่อให้ payload ที่ viewer ฝัง == ตอน bt run --viewer เป๊ะ (str→float round-trip ตรง)"""
    import csv
    rows = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            for k in int_fields:
                v = row.get(k)
                row[k] = int(v) if v not in (None, "") else None
            for k in float_fields:
                v = row.get(k)
                row[k] = float(v) if v not in (None, "") else None
            rows.append(row)
    return rows


def _viewer(args) -> int:
    """render viewer.html ใหม่จาก run folder — ไม่รัน engine · อ่าน trades/unfilled/plan_meta อย่างเดียว"""
    run_dir = args.run
    if not os.path.isdir(run_dir):
        print(f"❌ ไม่พบ run folder: {run_dir}")
        return 1
    trades_path = os.path.join(run_dir, "trades.csv")
    if not os.path.isfile(trades_path):
        print(f"❌ ไม่พบ trades.csv ใน {run_dir} (ไม่ใช่ run folder?)")
        return 1

    # หา data CSV: จาก config_used.yaml (_run.data) ก่อน · ไม่มี → --data
    data_path, strategy = None, "mai_ruay"
    cu = os.path.join(run_dir, "config_used.yaml")
    if os.path.isfile(cu):
        c = yaml.safe_load(open(cu, encoding="utf-8")) or {}
        rm = c.get("_run", {}) if isinstance(c, dict) else {}
        if isinstance(rm, dict):
            data_path = rm.get("data")
            strategy = rm.get("strategy", strategy)
    if not (data_path and os.path.isfile(data_path)):
        data_path = args.data
    if not data_path or not os.path.isfile(data_path):
        print("❌ หา data CSV ไม่เจอ — ระบุด้วย --data <path> "
              "(config_used.yaml ไม่มี _run.data หรือ path ไม่อยู่จริง)")
        return 1

    bars = load_csv(data_path)
    trades = _read_run_csv(trades_path,
                           ["plan_id", "entry_bar", "exit_bar"],
                           ["entry", "sl", "tp", "exit_price", "lot", "pnl_pips", "pnl_usd"])
    unfilled = []
    uf_path = os.path.join(run_dir, "unfilled.csv")
    if os.path.isfile(uf_path):
        unfilled = _read_run_csv(uf_path, ["plan_id", "place_bar", "death_bar"],
                                 ["limit_price", "sl", "tp"])

    out_path = os.path.join(run_dir, "viewer.html")   # write_viewer อ่าน plan_meta.csv จาก dir นี้เอง
    viewer.write_viewer(bars, trades, out_path, title=strategy, unfilled=unfilled)
    print(f"✅ regenerate viewer → {out_path}")
    print(f"   data={os.path.basename(data_path)} · bars={len(bars)} · trades={len(trades)} "
          f"· unfilled={len(unfilled)} · ไม่รัน engine (อ่านอย่างเดียว)")
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

    s = sub.add_parser("serve", help="เปิด Backtest Console web (local, อ่านอย่างเดียว — สเต็ป 1)")
    s.add_argument("--port", type=int, default=8000, help="พอร์ต (default 8000)")
    s.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1 — local เท่านั้น)")

    v = sub.add_parser("viewer", help="render viewer.html ใหม่จาก run folder (ไม่รัน engine)")
    v.add_argument("--run", required=True, help="โฟลเดอร์ runs/<name>/ (มี trades.csv)")
    v.add_argument("--data", help="data CSV (ใช้เมื่อ config_used.yaml ไม่มี _run.data)")

    args = p.parse_args(argv)
    if args.cmd == "run":
        return _run(args)
    if args.cmd == "serve":
        from bt import server   # lazy: bt run ไม่ต้องมี deps ของ server
        return server.serve(host=args.host, port=args.port)
    if args.cmd == "viewer":
        return _viewer(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
