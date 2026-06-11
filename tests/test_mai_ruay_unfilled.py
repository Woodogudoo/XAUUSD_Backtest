# ============================================================================
# tests/test_mai_ruay_unfilled.py — acceptance: unfilled log + plan_id mirror
#   - strategy เก็บไม้ที่ไม่ fill (proximity/expiry) ครบ field
#   - sanity: mirror plan_id ตรง engine บนไม้ fill "ทุกตัว" (mismatch ต้อง = 0)
#   รันเอง (engine+strategy) ไม่ต้องใช้ reference · default slice 40k
# ============================================================================
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA = os.path.join(_ROOT, "data", "XAUUSD_M1_2026-06-09.csv")
_BARS_LIMIT = int(os.environ.get("MAIRUAY_VERIFY_BARS", "40000"))  # 0 = เต็ม
_FIELDS = {"plan_id", "tag", "direction", "kind", "limit_price",
           "place_time", "place_bar", "death_time", "death_bar", "reason"}


def _run():
    if not os.path.exists(_DATA):
        return None
    import yaml
    from bt.data import load_csv
    from bt import engine
    from bt.strategies.mai_ruay import MaiRuay
    mcfg = yaml.safe_load(open(os.path.join(_ROOT, "configs", "mairuay_v204.yaml")))
    gcfg = yaml.safe_load(open(os.path.join(_ROOT, "configs", "global.yaml")))
    bars = load_csv(_DATA)
    if _BARS_LIMIT:
        bars = bars[:_BARS_LIMIT]
    strat = MaiRuay(mcfg, 1000.0)
    trades = engine.run(bars, strat, guards=gcfg["guards"], inject_compat=False)["trades"]
    return strat, trades


def test_unfilled_fields():
    r = _run()
    if r is None:
        print("SKIP: ไม่พบ data"); return
    strat, _ = r
    assert len(strat.unfilled) > 0, "ควรมีไม้ที่ไม่ fill"
    for u in strat.unfilled:
        assert set(u) >= _FIELDS, f"field ขาด: {_FIELDS - set(u)}"
        assert u["kind"] == "LIMIT"
        assert u["reason"] in ("proximity", "expiry")


def test_plan_id_mirror_matches_engine():
    # sanity: filled trade ทุกตัวต้องมี placement (mirror plan_id) ที่ tag ตรง + วางก่อนเข้า
    r = _run()
    if r is None:
        print("SKIP: ไม่พบ data"); return
    strat, trades = r
    pmap = {p["plan_id"]: p for p in strat._placements}
    bad = 0
    for t in trades:
        p = pmap.get(t["plan_id"])
        if p is None or t["tag"] not in p["tags"] or p["bar"] > t["entry_bar"]:
            bad += 1
    assert bad == 0, f"mirror plan_id ไม่ตรง engine: {bad}/{len(trades)} ไม้"


if __name__ == "__main__":
    r = _run()
    if r is None:
        print("SKIP: ไม่พบ data"); sys.exit(0)
    test_unfilled_fields(); test_plan_id_mirror_matches_engine()
    strat, trades = r
    print(f"unfilled={len(strat.unfilled)} · filled={len(trades)} · plan_id mirror=OK → PASS")
