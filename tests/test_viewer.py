# ============================================================================
# tests/test_viewer.py — acceptance ของ Step 7 (HTML viewer)
#   write_viewer → ไฟล์ self-contained · ฝัง ohlc/trades/plans ครบ · ไม่มี placeholder ค้าง
# ============================================================================
import datetime
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bt.contract import Bar
from bt import viewer

_BASE = datetime.datetime(2026, 1, 5, 0, 0, 0)
_BARS = [Bar(time=_BASE + datetime.timedelta(minutes=i),
             open=2000 + i * 0.1, high=2001 + i * 0.1,
             low=1999 + i * 0.1, close=2000.5 + i * 0.1) for i in range(62)]
_TRADES = [
    {"plan_id": 1, "tag": "จุด1", "direction": "BUY", "kind": "MARKET",
     "entry_time": "2026-01-05 00:00:00", "entry": 2000.0, "sl": 1999.0, "tp": 2002.0,
     "exit_time": "2026-01-05 00:01:00", "exit_price": 2002.0, "result": "TP",
     "lot": 1.0, "pnl_pips": 200.0, "pnl_usd": 200.0, "entry_bar": 0, "exit_bar": 1},
    {"plan_id": 2, "tag": "จุด1", "direction": "SELL", "kind": "LIMIT",
     "entry_time": "2026-01-05 01:00:00", "entry": 2010.0, "sl": 2011.0, "tp": 2008.0,
     "exit_time": "2026-01-05 01:01:00", "exit_price": 2011.0, "result": "SL",
     "lot": 1.0, "pnl_pips": -100.0, "pnl_usd": -100.0, "entry_bar": 60, "exit_bar": 61},
]


_UNFILLED = [
    {"plan_id": 2, "tag": "จุด3", "direction": "BUY", "kind": "LIMIT",
     "limit_price": 1998.5, "place_time": "2026-01-05 00:30:00", "place_bar": 30,
     "death_time": "2026-01-05 00:35:00", "death_bar": 35, "reason": "proximity"},
]


def _gen():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "viewer.html")
    viewer.write_viewer(_BARS, _TRADES, path, title="test", unfilled=_UNFILLED)
    return open(path, encoding="utf-8").read()


def test_self_contained_no_placeholder():
    html = _gen()
    assert html.strip().endswith("</html>")
    assert '<canvas id="cv">' in html
    assert "__DATA__" not in html and "__TITLE__" not in html
    assert "src=" not in html  # offline: ไม่มี external resource
    assert "function detailHTML" in html and "function setOpen" in html  # accordion inline
    assert 'id="panel"' not in html  # ไม่มี popup ลอยทับกราฟแล้ว


def test_embedded_data():
    html = _gen()
    m = re.search(r"const DATA=(\{.*?\}), O=DATA", html)
    assert m, "ไม่พบ DATA"
    d = json.loads(m.group(1))
    assert len(d["ohlc"]["t"]) == 62 and len(d["ohlc"]["c"]) == 62
    assert len(d["trades"]) == 2
    assert len(d["plans"]) == 2                       # group ตาม plan_id
    assert d["plans"][0]["result"] == "WIN" and d["plans"][1]["result"] == "LOSS"
    assert d["plans"][0]["eb"] == 0                   # มี entry_bar ให้กระโดด
    assert d["trades"][0]["dir"] == "BUY" and d["trades"][0]["result"] == "TP"
    assert "lot" in d["trades"][0]                     # panel ใช้ lot
    assert len(d["unfilled"]) == 1                    # ไม้ไม่ fill ฝังครบ
    assert d["unfilled"][0]["reason"] == "proximity" and d["unfilled"][0]["pb"] == 30


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn(); print(f"  ✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} tests PASS")
