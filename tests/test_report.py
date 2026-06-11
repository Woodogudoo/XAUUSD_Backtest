# ============================================================================
# tests/test_report.py — acceptance ของ Step 6 (report layer)
#   build_plans (group ตามจุดเข้า) · build_summary (สกอร์บอร์ด) · write_reports (3 ไฟล์)
# รัน: python3 tests/test_report.py  (หรือ pytest)
# ============================================================================
import csv
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bt import report

# trades สังเคราะห์: plan 1 = 2 ไม้ (net +) , plan 2 = 1 ไม้ (net -)
_TRADES = [
    {"plan_id": 1, "tag": "จุด1", "direction": "BUY", "kind": "MARKET",
     "entry_time": "2026-01-01 00:00:00", "entry": 2000.0, "sl": 1999.0, "tp": 2002.0,
     "exit_time": "2026-01-01 00:05:00", "exit_price": 2002.0, "result": "TP",
     "lot": 1.0, "pnl_pips": 200.0, "pnl_usd": 200.0, "entry_bar": 0, "exit_bar": 1},
    {"plan_id": 1, "tag": "จุด2", "direction": "BUY", "kind": "LIMIT",
     "entry_time": "2026-01-01 00:00:00", "entry": 1999.5, "sl": 1999.0, "tp": 2001.0,
     "exit_time": "2026-01-01 00:10:00", "exit_price": 1999.0, "result": "SL",
     "lot": 1.0, "pnl_pips": -50.0, "pnl_usd": -50.0, "entry_bar": 0, "exit_bar": 2},
    {"plan_id": 2, "tag": "จุด1", "direction": "SELL", "kind": "MARKET",
     "entry_time": "2026-01-01 01:00:00", "entry": 2010.0, "sl": 2011.0, "tp": 2008.0,
     "exit_time": "2026-01-01 01:05:00", "exit_price": 2011.0, "result": "SL",
     "lot": 1.0, "pnl_pips": -100.0, "pnl_usd": -100.0, "entry_bar": 60, "exit_bar": 61},
]


def test_build_plans():
    plans = report.build_plans(_TRADES)
    assert len(plans) == 2
    p1, p2 = plans
    assert p1["plan_id"] == 1 and p1["n_trades"] == 2
    assert abs(p1["net_pnl_usd"] - 150.0) < 1e-9 and p1["result"] == "WIN"   # 200-50
    assert p2["plan_id"] == 2 and p2["result"] == "LOSS"                     # -100


def test_build_summary():
    plans = report.build_plans(_TRADES)
    s = report.build_summary(_TRADES, plans, portfolio=1000.0)
    assert s["n_trades"] == 3 and s["n_wins"] == 1 and s["n_losses"] == 2
    assert abs(s["win_rate"] - round(100 / 3, 2)) < 1e-9
    assert abs(s["net_usd"] - 50.0) < 1e-9            # 200-50-100
    assert abs(s["final_portfolio"] - 1050.0) < 1e-9
    assert s["n_plans"] == 2 and s["plan_wins"] == 1


def test_write_reports():
    with tempfile.TemporaryDirectory() as d:
        s = report.write_reports({"trades": _TRADES}, d, portfolio=1000.0)
        for fn in ("trades.csv", "plans.csv", "summary.json"):
            assert os.path.exists(os.path.join(d, fn)), f"ไม่มี {fn}"
        with open(os.path.join(d, "trades.csv"), encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 3 and rows[0]["tag"] == "จุด1"   # Thai/UTF-8 ผ่าน
        with open(os.path.join(d, "summary.json"), encoding="utf-8") as f:
            assert json.load(f)["n_trades"] == 3


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn(); print(f"  ✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} tests PASS")
