# ============================================================================
# tests/test_mai_ruay_full.py
# ----------------------------------------------------------------------------
# Acceptance ของ Step 5.3 — ไม้รวยครบ (รวมรอบ 2)
# พิสูจน์: engine inject_compat=True ตรงกับ reference.run_backtest "เต็ม" (round2 เปิด) เป๊ะ
#         → port ไม้รวยถูกต้องทุกขั้น · ความต่าง engine สะอาด ↔ v2.04 = inject + cancel-before-fill
#
# full 150,063 แท่ง verify แล้ว: ref=compat=561 (round2=32) · matched=561 เป๊ะ (รันมือ)
# default slice 40k แท่ง · ตั้ง MAIRUAY_VERIFY_BARS=0 = เต็ม
# ============================================================================
import importlib.util
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA = os.path.join(_ROOT, "data", "XAUUSD_M1_2026-06-09.csv")
_REF  = os.path.join(_ROOT, "reference", "mairuay_v204_reference.py")
_BARS_LIMIT = int(os.environ.get("MAIRUAY_VERIFY_BARS", "40000"))  # 0 = เต็ม
_RES = {"WIN": "TP", "LOSS": "SL"}


def _match_count(rref, compat):
    buckets = defaultdict(list)
    for c in compat:
        buckets[str(c["entry_time"])[:19]].append(c)
    matched = 0
    for r in rref:
        key = r["open_time"][:19]
        for c in buckets[key]:
            if (abs(r["entry"] - c["entry"]) < 1e-3
                    and _RES[r["result"]] == c["result"]
                    and abs(r["actual_exit"] - c["exit_price"]) < 1e-3):
                buckets[key].remove(c)
                matched += 1
                break
    return matched


def _run():
    if not (os.path.exists(_DATA) and os.path.exists(_REF)):
        return None
    import yaml
    from bt.data import load_csv
    from bt import engine
    from bt.strategies.mai_ruay import MaiRuay

    spec = importlib.util.spec_from_file_location("ref", _REF)
    ref = importlib.util.module_from_spec(spec)
    sys.modules["ref"] = ref
    spec.loader.exec_module(ref)

    mcfg = yaml.safe_load(open(os.path.join(_ROOT, "configs", "mairuay_v204.yaml")))
    gcfg = yaml.safe_load(open(os.path.join(_ROOT, "configs", "global.yaml")))

    df = ref.load_data(_DATA)
    bars = load_csv(_DATA)
    if _BARS_LIMIT:
        df = df.iloc[:_BARS_LIMIT].reset_index(drop=True)
        bars = bars[:_BARS_LIMIT]

    rref = ref.run_backtest(df, 1000.0, verbose=False)["trades"]   # เต็ม (round2 เปิด)
    compat = engine.run(bars, MaiRuay(mcfg, 1000.0, pre_fill_cancel=True),
                        guards=gcfg["guards"], inject_compat=True)["trades"]
    r2 = sum(1 for t in rref if "R2" in str(t.get("father_pass", "")))
    return {"ref": len(rref), "compat": len(compat),
            "matched": _match_count(rref, compat), "round2": r2}


def test_compat_matches_reference_full():
    r = _run()
    if r is None:
        print("SKIP: ไม่พบ data/reference"); return
    assert r["ref"] == r["compat"], f"count: ref={r['ref']} compat={r['compat']}"
    assert r["matched"] == r["ref"] > 0, f"matched={r['matched']} / {r['ref']}"
    assert r["round2"] > 0, "ควรมีไม้รอบ 2 ในช่วงที่ทดสอบ"


if __name__ == "__main__":
    r = _run()
    if r is None:
        print("SKIP: ไม่พบ data/reference"); sys.exit(0)
    ok = r["ref"] == r["compat"] == r["matched"]
    print(f"ref(full)={r['ref']} (round2={r['round2']}) compat={r['compat']} "
          f"matched={r['matched']} → {'EXACT PASS' if ok else 'FAIL'}")
