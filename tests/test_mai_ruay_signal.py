# ============================================================================
# tests/test_mai_ruay_signal.py
# ----------------------------------------------------------------------------
# Acceptance ของ Step 5.1 — signal core ต้องตรง reference.analyze_bar เป๊ะ
# diff signal ทุกแท่งบน data จริง (default 40k แท่ง · ตั้ง MAIRUAY_VERIFY_BARS=0 = เต็ม)
#
# เต็ม 150,063 แท่ง verify แล้ว: ref=308 mine=308 · mismatch=0 (รันมือ ~45s)
# รัน: python3 tests/test_mai_ruay_signal.py   (หรือ pytest)
# ============================================================================
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA = os.path.join(_ROOT, "data", "XAUUSD_M1_2026-06-09.csv")
_REF  = os.path.join(_ROOT, "reference", "mairuay_v204_reference.py")
_CFG  = os.path.join(_ROOT, "configs", "mairuay_v204.yaml")
_BARS_LIMIT = int(os.environ.get("MAIRUAY_VERIFY_BARS", "40000"))  # 0 = เต็ม

_FLOATS = ["father_body_pips", "father_pct_r55", "mother_pct_r55", "mother_body_pips",
           "tech_point", "buffer_pips", "entry", "sl", "sl_pips", "tp1", "tp2", "tp3",
           "tp_selected", "tp_pips", "range55", "rr", "lot", "father_first_pct", "vol_ratio"]
_INTS = ["father_start", "father_end", "father_bars", "mother_idx"]


def _load_ref():
    spec = importlib.util.spec_from_file_location("ref", _REF)
    ref = importlib.util.module_from_spec(spec)
    sys.modules["ref"] = ref
    spec.loader.exec_module(ref)
    return ref


def _diff(rs, ms):
    d = []
    if rs.direction != ms.direction:        d.append(("direction", rs.direction, ms.direction))
    if bool(rs.is_round2) != bool(ms.is_round2): d.append(("is_round2", rs.is_round2, ms.is_round2))
    for f in _INTS:
        if getattr(rs, f) != getattr(ms, f):  d.append((f, getattr(rs, f), getattr(ms, f)))
    for f in _FLOATS:
        if abs(getattr(rs, f) - getattr(ms, f)) > 1e-6:
            d.append((f, getattr(rs, f), getattr(ms, f)))
    if len(rs.entries) != len(ms.entries):
        d.append(("n_entries", len(rs.entries), len(ms.entries)))
    else:
        for i, (r, m) in enumerate(zip(rs.entries, ms.entries)):
            if abs(r[0] - m[0]) > 1e-6: d.append((f"entry{i}.price", r[0], m[0]))
            if abs(r[2] - m[2]) > 1e-6: d.append((f"entry{i}.lot",   r[2], m[2]))
            if bool(r[3]) != bool(m[3]): d.append((f"entry{i}.mkt",   r[3], m[3]))
    return d


def _run():
    if not (os.path.exists(_DATA) and os.path.exists(_REF)):
        return None
    import yaml
    from bt.data import load_csv
    from bt.strategies.mai_ruay import analyze_bar
    ref = _load_ref()
    cfg = yaml.safe_load(open(_CFG))
    df = ref.load_data(_DATA)
    bars = load_csv(_DATA)
    n = len(df) if _BARS_LIMIT == 0 else min(_BARS_LIMIT, len(df))
    res = {"n_ref": 0, "n_mine": 0, "presence": 0, "field": 0, "bad": []}
    for i in range(54, n):
        rs = ref.analyze_bar(df, i, 1000.0)
        ms = analyze_bar(bars, i, cfg, 1000.0)
        res["n_ref"]  += rs is not None
        res["n_mine"] += ms is not None
        if (rs is None) != (ms is None):
            res["presence"] += 1
            res["bad"].append((i, "presence", rs is not None, ms is not None))
            continue
        if rs is None:
            continue
        d = _diff(rs, ms)
        if d:
            res["field"] += 1
            res["bad"].append((i, "field", d[:4]))
    res["n"] = n
    return res


def test_signal_parity_with_reference():
    r = _run()
    if r is None:
        print("SKIP: ไม่พบ data/reference"); return
    assert r["presence"] == 0, f"presence mismatch: {r['bad'][:5]}"
    assert r["field"] == 0,    f"field mismatch: {r['bad'][:5]}"
    assert r["n_ref"] == r["n_mine"] > 0


if __name__ == "__main__":
    r = _run()
    if r is None:
        print("SKIP: ไม่พบ data/reference"); sys.exit(0)
    print(f"verify [54,{r['n']}) · signals ref={r['n_ref']} mine={r['n_mine']} "
          f"· presence-mismatch={r['presence']} · field-mismatch={r['field']}")
    for b in r["bad"][:8]:
        print("  ", b)
    print("PASS" if r["presence"] == 0 and r["field"] == 0 else "FAIL")
