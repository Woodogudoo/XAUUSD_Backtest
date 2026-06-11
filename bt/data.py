# ============================================================================
# bt/data.py
# ----------------------------------------------------------------------------
# Data layer — โหลด CSV -> list[Bar] + คำนวณ R55
# พอร์ตจาก v2.04 (load_data / calc_range55) ให้ผลตรงเป๊ะ
#
# *** ไม่มี logic เทรด — แค่โหลดข้อมูล + util R55 ***
# ============================================================================
from __future__ import annotations

import pandas as pd

from bt.contract import Bar

PIP          = 0.01    # 1 pip = 0.01 USD (XAUUSD)
RANGE_WINDOW = 55      # จำนวนแท่งสำหรับ R55


def load_csv(path: str) -> list[Bar]:
    """โหลด CSV format: Time,Open,High,Low,Close[,Volume] (ไม่มี header, UTC)"""
    try:
        df = pd.read_csv(
            path, header=None,
            names=['time', 'open', 'high', 'low', 'close', 'volume'],
        )
    except Exception:
        df = pd.read_csv(
            path, header=None,
            names=['time', 'open', 'high', 'low', 'close'],
        )
    df['time'] = pd.to_datetime(df['time'])

    has_vol = 'volume' in df.columns
    bars: list[Bar] = []
    for row in df.itertuples(index=False):
        vol = float(row.volume) if has_vol and not pd.isna(row.volume) else None
        bars.append(Bar(
            time=row.time,
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=vol,
        ))
    return bars


def calc_r55(bars: list[Bar], end_idx: int) -> float:
    """R55 = (max high − min low) ของ 55 แท่งจบที่ end_idx หน่วยเป็น pip
    mirror v2.04 calc_range55: start = max(0, end_idx − 55 + 1)
    """
    start = max(0, end_idx - RANGE_WINDOW + 1)
    window = bars[start:end_idx + 1]
    if not window:
        return 0.0
    hi = max(b.high for b in window)
    lo = min(b.low for b in window)
    return (hi - lo) / PIP
