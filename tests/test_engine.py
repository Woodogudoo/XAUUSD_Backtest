# ============================================================================
# tests/test_engine.py
# ----------------------------------------------------------------------------
# Acceptance test ของ Step 3 (engine แช่แข็ง)
# strategy จิ๋ว: BUY MARKET ทุก 50 แท่ง · TP/SL ระยะคงที่ · ไม่เปิดซ้ำถ้ามี position
# bars สังเคราะห์ที่ไล่ผลด้วยมือได้ทุกไม้
#
# รัน: python3 tests/test_engine.py   (หรือ pytest)
# ============================================================================
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bt.contract import Bar, Decision, Order
from bt import engine

PIP      = 0.01
TP_DIST  = 2.0    # 200 pip
SL_DIST  = 1.0    # 100 pip
LOT      = 1.0
_BASE    = datetime.datetime(2026, 1, 5, 0, 0, 0)


def _bar(i, o, h, l, c):
    return Bar(time=_BASE + datetime.timedelta(minutes=5 * i),
               open=o, high=h, low=l, close=c)


def _make_bars():
    """150 แท่ง · ไล่มือได้:
      bar0  BUY → bar5  TP@2002 (+200)
      bar50 BUY → bar55 SL@1999 (−100)
      bar100 BUY → bar101 SL+TP แท่งเดียว → SL@1999 (−100, conservative)
    filler = (1999.5, 2000.5) ไม่แตะ SL(1999)/TP(2002)
    """
    bars = []
    for i in range(150):
        if   i == 0:   bars.append(_bar(i, 2000.0, 2000.5, 1999.5, 2000.2))  # เปิด ไม่ชน
        elif i == 5:   bars.append(_bar(i, 2000.5, 2002.5, 2000.0, 2002.2))  # high≥2002 → TP
        elif i == 50:  bars.append(_bar(i, 2000.0, 2000.5, 1999.5, 2000.2))  # เปิด ไม่ชน
        elif i == 55:  bars.append(_bar(i, 1999.8, 2000.0, 1998.5, 1999.0))  # low≤1999 → SL
        elif i == 100: bars.append(_bar(i, 2000.0, 2000.5, 1999.5, 2000.2))  # เปิด ไม่ชน
        elif i == 101: bars.append(_bar(i, 2000.0, 2003.0, 1998.0, 2000.0))  # SL+TP แท่งเดียว
        else:          bars.append(_bar(i, 2000.0, 2000.5, 1999.5, 2000.0))  # filler
    return bars


class TinyEveryFifty:
    """BUY MARKET ทุก 50 แท่ง · ไม่เปิดซ้ำถ้ามี position อยู่"""
    def on_bar(self, ctx):
        if ctx.bar_index % 50 == 0 and not ctx.positions:
            o = ctx.bar.open
            order = Order(kind="MARKET", price=0.0, lot=LOT,
                          sl=o - SL_DIST, tp=o + TP_DIST, tag=f"b{ctx.bar_index}")
            return Decision(place=[order], cancel=[], modify=[])
        return Decision(place=[], cancel=[], modify=[])


def _run():
    bars = _make_bars()
    return bars, engine.run(bars, TinyEveryFifty())["trades"]


# ---- expected (ไล่มือ) -----------------------------------------------------
#  (tag, entry_bar, exit_bar, result, exit_price, pnl_pips)
_EXPECTED = [
    ("b0",   0,   5, "TP", 2002.0, +200.0),
    ("b50",  50, 55, "SL", 1999.0, -100.0),
    ("b100", 100, 101, "SL", 1999.0, -100.0),
]


def test_three_trades():
    _, trades = _run()
    assert len(trades) == 3, f"ต้องได้ 3 ไม้ แต่ได้ {len(trades)}"


def test_entry_is_bar_open():
    # (1) entry = bar.open ของแท่งที่วาง market
    bars, trades = _run()
    for t in trades:
        assert abs(t["entry"] - bars[t["entry_bar"]].open) < 1e-9, \
            f"entry {t['entry']} ≠ open ของ bar {t['entry_bar']}"
        assert t["kind"] == "MARKET"


def test_tp_sl_correct_and_conservative():
    # (2) ปิด TP/SL ถูกต้อง · SL+TP โดนแท่งเดียว → SL
    _, trades = _run()
    for t, e in zip(trades, _EXPECTED):
        assert t["result"] == e[3], f"{t['tag']}: result {t['result']} ≠ {e[3]}"
        assert abs(t["exit_price"] - e[4]) < 1e-9, f"{t['tag']}: exit {t['exit_price']} ≠ {e[4]}"
        assert t["exit_bar"] == e[2], f"{t['tag']}: exit_bar {t['exit_bar']} ≠ {e[2]}"
    # ไม้สุดท้ายคือเคส SL+TP แท่งเดียว → ต้องเป็น SL (conservative)
    assert trades[2]["result"] == "SL"


def test_plan_id_one_per_batch():
    # (3) plan_id ออก 1 ต่อ 1 batch ของ place (3 batch → 1,2,3)
    _, trades = _run()
    assert [t["plan_id"] for t in trades] == [1, 2, 3]


def test_pnl_matches_hand_trace():
    # (4) pnl ตรงกับที่ไล่มือ
    _, trades = _run()
    for t, e in zip(trades, _EXPECTED):
        assert abs(t["pnl_pips"] - e[5]) < 1e-9, f"{t['tag']}: pnl {t['pnl_pips']} ≠ {e[5]}"
        assert abs(t["pnl_usd"] - e[5] * LOT) < 1e-9


def test_no_reopen_while_open():
    # ไม่เปิดซ้ำถ้ามี position อยู่ → tag/plan ไม่ซ้ำ, ไม่มีไม้เกิน 3
    _, trades = _run()
    tags = [t["tag"] for t in trades]
    assert tags == ["b0", "b50", "b100"]
    assert len(set(t["plan_id"] for t in trades)) == 3


# ---- same-bar limit fill (engine step 5a, ตาม v2.04 child-bar) -------------
def test_same_bar_limit_fill_entry_price():
    # ยืนยัน entry = level เป๊ะ + entry_bar = แท่งที่วาง โดยให้ปิด TP แท่งถัดไป
    class Lim:
        def on_bar(self, ctx):
            if ctx.bar_index == 0:
                return Decision(place=[Order("LIMIT", 1999.5, 1.0, 1998.0, 2001.0, "L")],
                                cancel=[], modify=[])
            return Decision(place=[], cancel=[], modify=[])
    bars = [_bar(0, 2000.0, 2000.4, 1999.0, 2000.0),   # low 1999 ≤ 1999.5 → fill@1999.5 bar0
            _bar(1, 2000.0, 2001.5, 2000.0, 2001.2)]    # high ≥ 2001 → TP
    trades = engine.run(bars, Lim())["trades"]
    assert len(trades) == 1
    assert abs(trades[0]["entry"] - 1999.5) < 1e-9   # level เป๊ะ ไม่ใช่ open
    assert trades[0]["entry_bar"] == 0               # fill บนแท่งที่วาง
    assert trades[0]["kind"] == "LIMIT"
    assert trades[0]["result"] == "TP"


def test_limit_pending_next_bar_when_not_reached():
    # ไม่แตะแท่งที่วาง → เข้าคิว pending → fill แท่งถัดไป (path pending ยังทำงาน)
    class Lim:
        def on_bar(self, ctx):
            if ctx.bar_index == 0:
                return Decision(place=[Order("LIMIT", 1995.0, 1.0, 1990.0, 2000.0, "L")],
                                cancel=[], modify=[])
            return Decision(place=[], cancel=[], modify=[])
    bars = [_bar(0, 2000.0, 2000.4, 1999.0, 2000.0),   # low 1999 > 1995 → ยังไม่ fill
            _bar(1, 1996.0, 1996.5, 1994.5, 1995.5),    # low 1994.5 ≤ 1995 → fill@1995 bar1
            _bar(2, 1995.0, 2000.5, 1994.0, 2000.0)]    # high ≥ 2000 → TP
    trades = engine.run(bars, Lim())["trades"]
    assert len(trades) == 1
    assert trades[0]["entry_bar"] == 1               # fill แท่งถัดไป ไม่ใช่แท่งที่วาง
    assert abs(trades[0]["entry"] - 1995.0) < 1e-9


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in tests:
        fn()
        print(f"  ✓ {fn.__name__}")
        passed += 1
    _, trades = _run()
    print(f"\n{passed}/{len(tests)} tests PASS · trades:")
    for t in trades:
        print(f"  plan={t['plan_id']} {t['tag']:5} entry_bar={t['entry_bar']:>3} "
              f"entry={t['entry']:.2f} → exit_bar={t['exit_bar']:>3} "
              f"{t['result']}@{t['exit_price']:.2f} pnl={t['pnl_pips']:+.0f}")
