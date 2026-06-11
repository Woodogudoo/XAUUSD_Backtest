# ============================================================================
# mairuay_v204_reference.py
# ----------------------------------------------------------------------------
# โค้ดอ้างอิง (READ-ONLY) — export มาจาก Cell 1 ของ notebook:
#   Mairuay_Basic_Father_V2_04_Bugfix1.ipynb
#
# วัตถุประสงค์: ใช้เป็น "ของจริง" (golden source) สำหรับ
#   (1) ดึงค่า threshold จริงตอนพอร์ตเป็น strategies/mai_ruay.py
#       find_father / find_father_r2 / validate_mother / calc_sl / calc_tp / analyze_bar
#   (2) validate ระบบใหม่ให้ผลตรง v2.04 เป๊ะ
#
# *** อย่าแก้ไฟล์นี้ *** เป็นเอกสารอ้างอิงเท่านั้น (logic ตรงตาม notebook ต้นฉบับ)
# ============================================================================



# +==================================================================+
# |   XAUUSD Backtest — ไม้รวย (Branch F)                          |
# |   Cell 1 — Signal Engine + Backtest Engine + GSheet Export      |
# +==================================================================+
from __future__ import annotations

import sys, os, re
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional, List, Tuple
from collections import defaultdict
from datetime import datetime, timedelta

# -- Constants ----------------------------------------------------
PIP           = 0.01    # 1 pip = 0.01 USD (XAUUSD)
RANGE_WINDOW  = 55      # จำนวนแท่งสำหรับ Range55
MAX_FATHER    = 8       # แท่งพ่อมากสุด 8 แท่ง
MAX_BUFFER    = 300.0   # pip cap ของ buffer
RISK_PCT      = 0.10    # 10% ต่อไม้

# -- Time & Gap Config --------------------------------------------
MARKET_CLOSE_HOUR  = 21
MARKET_CLOSE_MIN   = 0
NO_TRADE_MINS      = 120
GAP_THRESHOLD_PCT  = 0.20
GAP_WAIT_BARS      = 80

# ====================================================================
# DATA CLASS
# ====================================================================
@dataclass
class MaiRuaySignal:
    bar_index:         int
    time:              str
    direction:         str       # 'BUY' | 'SELL'
    trend:             str       # 'UP' | 'DOWN' | 'NONE'
    father_start:      int
    father_end:        int
    father_start_time: str
    father_end_time:   str
    father_bars:       int
    father_body_pips:  float
    father_pct_r55:    float
    mother_idx:        int
    mother_body_pips:  float
    mother_pct_r55: float
    mother_quality:    str
    tech_point:        float
    buffer_pips:       float
    entry:             float
    entry_rule:        str
    sl:                float
    sl_label:          str
    sl_pips:           float
    tp1:               float
    tp2:               float
    tp3:               float
    tp_selected:       float
    tp_label:          str
    tp_pips:           float
    range55:           float
    rr:                float
    lot:               float
    portfolio:         float
    anti_trend:        bool      = False
    is_round2:         bool      = False
    father_first_pct:  float     = 0.0   # body แท่งพ่อแท่งแรก %R55
    father_pass:       str       = ''    # เงื่อนไขพ่อ (Pass 0/0.5/1/1.5/2/R2)
    vol_ratio:         float     = 0.0   # Volatility Ratio (father avg body ÷ avg body 20 แท่งก่อน)
    entries:           list      = None


# ====================================================================
# UTILITY
# ====================================================================
def load_data(filepath: str) -> pd.DataFrame:
    """โหลด CSV  format: Time,Open,High,Low,Close[,Volume]"""
    try:
        df = pd.read_csv(
            filepath, header=None,
            names=['time','open','high','low','close','volume'],
        )
    except Exception:
        df = pd.read_csv(
            filepath, header=None,
            names=['time','open','high','low','close'],
        )
    df['time'] = pd.to_datetime(df['time'])
    return df.reset_index(drop=True)


def calc_range55(df: pd.DataFrame, end_idx: int) -> float:
    start = max(0, end_idx - RANGE_WINDOW + 1)
    w = df.iloc[start:end_idx + 1]
    return (w['high'].max() - w['low'].min()) / PIP


def body_pips(row: pd.Series) -> float:
    return abs(row['open'] - row['close']) / PIP


def is_bull(row: pd.Series) -> bool:
    return row['close'] > row['open']


def is_bear(row: pd.Series) -> bool:
    return row['close'] < row['open']


# ====================================================================
# TREND DETECTION (simplified — slope of close 20 bars)
# ====================================================================


def find_father(df, father_end, r55):
    """
    หาแท่งพ่อ — Pass 1 เท่านั้น
    สีเดียวกัน 1-10 แท่ง, body รวม >60%R55, แต่ละแท่ง >4%R55
    ห้ามมีแทรก, Vol Ratio >1
    """
    if father_end < 0 or father_end >= len(df): return None
    eb = df.iloc[father_end]
    if   is_bull(eb): run_dir = 'UP'
    elif is_bear(eb): run_dir = 'DOWN'
    else: return None

    # แท่งถัดไป (แม่) ต้องสวนทิศ
    if father_end + 1 < len(df):
        nb_bar = df.iloc[father_end + 1]
        if (run_dir == 'UP' and not is_bear(nb_bar)) or \
           (run_dir == 'DOWN' and not is_bull(nb_bar)):
            return None

    # สแกนสีเดียว 1-10 แท่ง ห้ามแทรก + หยุดเมื่อเจอ Doji (body ≤ 2%R55)
    run_start = father_end
    for k in range(father_end - 1, max(-1, father_end - 10), -1):
        b = df.iloc[k]
        same = (run_dir == 'UP' and is_bull(b)) or (run_dir == 'DOWN' and is_bear(b))
        if same and body_pips(b) > r55 * 0.02:
            run_start = k
        else:
            break

    length = father_end - run_start + 1
    if length < 1: return None

    bars = df.iloc[run_start:father_end + 1]
    tb   = abs(bars.iloc[0]['open'] - bars.iloc[-1]['close']) / PIP
    pct  = tb / r55 * 100 if r55 > 0 else 0

    # body รวม > 60%R55
    if pct <= 60: return None

    # แต่ละแท่ง body > 4%R55
    # อนุโลม: ถ้าส่วนใหญ่ >12%R55 → อนุโลม 2 แท่ง (แต่ต้อง >2%R55)
    _big_count = sum(1 for idx in range(length) if body_pips(bars.iloc[idx]) > r55 * 0.10)
    _big_majority = _big_count > length / 2  # ส่วนใหญ่ >10%
    _fail_count = 0
    for idx in range(length):
        _bp = body_pips(bars.iloc[idx])
        if _bp <= r55 * 0.04:
            if _big_majority and _bp > r55 * 0.02:
                _fail_count += 1  # อนุโลม (>2% แต่ ≤3%)
            else:
                return None  # <2% หรือไม่ได้ majority → ไม่ผ่าน
    if _fail_count > 2:
        return None  # อนุโลมได้สูงสุด 2 แท่ง

    # Volatility Ratio > 1
    _f_avg_body = tb / length
    _pre20_start = max(0, run_start - 20)
    _pre20 = df.iloc[_pre20_start:run_start]
    if len(_pre20) > 0:
        _pre20_avg = sum(abs(r['open'] - r['close']) / PIP for _, r in _pre20.iterrows()) / len(_pre20)
        _vol_ratio = _f_avg_body / _pre20_avg if _pre20_avg > 0 else 0
    else:
        _vol_ratio = 0
    if _vol_ratio <= 1: return None

    return (run_start, father_end, run_dir, tb, 'Pass1 สีเดียว 1-10แท่ง >60%R55')



def find_father_r2(df, father_end, r55):
    """
    ไม้รวยรอบ 2: พ่อ 1-5 แท่ง สีเดียว >35%R55
    อนุญาตแทรก 1 ตัว (body ≤10%R55 + next bar close check)
    """
    if father_end < 0 or father_end >= len(df): return None
    eb = df.iloc[father_end]
    if   is_bull(eb): run_dir = 'UP'
    elif is_bear(eb): run_dir = 'DOWN'
    else: return None
    if father_end + 1 < len(df):
        nb_bar = df.iloc[father_end + 1]
        if (run_dir == 'UP' and not is_bear(nb_bar)) or \
           (run_dir == 'DOWN' and not is_bull(nb_bar)):
            return None

    # สแกนย้อนหลัง อนุญาตแทรก 1 ตัว
    run_start = father_end
    opp_count = 0
    opp_bar_k = -1
    for k in range(father_end - 1, max(-1, father_end - MAX_FATHER), -1):
        b = df.iloc[k]
        same = (run_dir == 'UP' and is_bull(b)) or (run_dir == 'DOWN' and is_bear(b))
        if same:
            run_start = k
        else:
            if opp_count < 1:
                opp_count = 1
                opp_bar_k = k
                run_start = k
            else:
                break
    length = father_end - run_start + 1
    if length < 1: return None
    bars = df.iloc[run_start:father_end + 1]
    tb   = abs(bars.iloc[0]['open'] - bars.iloc[-1]['close']) / PIP
    pct  = tb / r55 * 100 if r55 > 0 else 0
    if pct <= 35: return None

    # ตรวจแท่งแทรก (ถ้ามี)
    if opp_count == 1:
        # แทรก body ≤ 10%R55
        if body_pips(df.iloc[opp_bar_k]) > r55 * 0.10:
            return None
        # แท่งต่อจากแทรก Close ต้องกลับมาตามทิศ
        if opp_bar_k + 1 <= father_end:
            _next = df.iloc[opp_bar_k + 1]
            _opp  = df.iloc[opp_bar_k]
            if run_dir == 'UP' and _next['close'] <= _opp['open']:
                return None
            if run_dir == 'DOWN' and _next['close'] >= _opp['open']:
                return None
    # แต่ละแท่ง body > 6%R55 (อนุโลม 1 แท่ง + ยกเว้นแทรก)
    # แท่งสุดท้ายอนุโลมใช้ >4%R55
    _fail6 = 0
    for _bi in range(run_start, father_end + 1):
        if opp_count == 1 and _bi == opp_bar_k:
            continue  # ข้ามแท่งแทรก
        _min_thresh = r55 * 0.04 if _bi == father_end else r55 * 0.06
        if body_pips(df.iloc[_bi]) <= _min_thresh:
            _fail6 += 1
    if _fail6 > 1:
        return None
    return (run_start, father_end, run_dir, tb, 'R2 ไม้รวยรอบ2')


def validate_mother(
    df: pd.DataFrame,
    mother_idx: int,
    father_direction: str,
    father_body: float,
    r55: float = 0,        # R55 สำหรับวัดขนาดแม่
    min_pct: float = 3.0,  # ขั้นต่ำ % แม่ (รอบ 2 = 2%)
) -> Optional[Tuple[float, float, str]]:
    """
    แท่งแม่: สวนทิศพ่อ, body 3-25% ของ R55
    Returns: (mother_body_pips, mother_pct_r55, quality_label) หรือ None
    """
    if mother_idx >= len(df):
        return None
    m      = df.iloc[mother_idx]
    m_body = body_pips(m)
    if father_direction == 'UP'   and not is_bear(m):
        return None
    if father_direction == 'DOWN' and not is_bull(m):
        return None
    pct = m_body / r55 * 100 if r55 > 0 else 0  # วัดจาก R55
    if pct < min_pct or pct > 25:
        return None
    quality = (
        f"สวยที่สุด ({pct:.1f}%) ✅✅" if pct <= 10 else
        f"ใช้ได้     ({pct:.1f}%) ✅"
    )
    return (m_body, pct, quality)


# ====================================================================
# STEP 3 — SL / TP
# ====================================================================
def calc_sl(tech_point: float, direction: str, father_body_pips: float) -> Tuple[float, str]:
    """
    SL = 70% ของพ่อ วัดจาก tech_point
    BUY  → SL = tech_point − father_body_pips × 70%
    SELL → SL = tech_point + father_body_pips × 70%
    """
    offset = father_body_pips * 0.70 * PIP
    if direction == 'BUY':
        sl = tech_point - offset
        return (sl, f"tech({tech_point:.3f}) − พ่อ×70%({father_body_pips*0.70:.0f}pip) = {sl:.3f}")
    else:
        sl = tech_point + offset
        return (sl, f"tech({tech_point:.3f}) + พ่อ×70%({father_body_pips*0.70:.0f}pip) = {sl:.3f}")


def calc_tp(tech_point: float, father_body_pips: float, direction: str) -> Tuple[float, float, float]:
    """TP1=33%, TP2=50%, TP3=75% ของแท่งพ่อ (วัดจาก tech_point)"""
    sign = 1 if direction == 'BUY' else -1
    f    = father_body_pips * PIP
    return (tech_point + sign*(f*0.33),
            tech_point + sign*(f*0.50),
            tech_point + sign*(f*0.75))


def _swing_bsize(o, c): return abs(o - c) / PIP
def _swing_bhi(o, c):   return max(o, c)
def _swing_blo(o, c):   return min(o, c)

def _swing_check_high(bars_arr, li, rj, mid, thick_exc=False):
    left_range  = [li-1] if thick_exc else [li-3, li-2, li-1]
    right_range = [rj+1] if thick_exc else [rj+1, rj+2]
    for k in left_range:
        if k < 0: continue
        if bars_arr[k][0] >= mid or bars_arr[k][1] >= mid: return False
    for k in right_range:
        if k >= len(bars_arr): return True
        if bars_arr[k][0] >= mid or bars_arr[k][1] >= mid: return False
    return True

def _swing_check_low(bars_arr, li, rj, mid, thick_exc=False):
    left_range  = [li-1] if thick_exc else [li-3, li-2, li-1]
    right_range = [rj+1] if thick_exc else [rj+1, rj+2]
    for k in left_range:
        if k < 0: continue
        if bars_arr[k][0] <= mid or bars_arr[k][1] <= mid: return False
    for k in right_range:
        if k >= len(bars_arr): return True
        if bars_arr[k][0] <= mid or bars_arr[k][1] <= mid: return False
    return True


def calc_lot(portfolio: float, sl_pips: float) -> float:
    if sl_pips <= 0:
        return 0.0
    return round((portfolio * RISK_PCT) / sl_pips, 2)


# ====================================================================
# CORE ANALYZER — วิเคราะห์ 1 แท่ง
# ====================================================================
def analyze_bar(
    df: pd.DataFrame,
    bar_idx: int,
    portfolio: float = 1000.0,
    round2_info: dict = None,  # {'tech': float, 'f_open_r1': float}
) -> Optional[MaiRuaySignal]:
    """
    โครงสร้าง: [...][พ่อ 1-5 แท่ง][แม่ 1 แท่ง][ลูก=bar_idx]
    """
    if bar_idx < RANGE_WINDOW - 1:
        return None

    anti_trend = False
    mother_idx = bar_idx - 1
    father_end = bar_idx - 2
    if father_end < 0:
        return None

    # 1. Range55
    r55 = calc_range55(df, bar_idx - 1)  # R55 ณ จบแท่งแม่
    if r55 <= 0:
        return None

    # 2. แท่งพ่อ
    if round2_info:
        fr = find_father_r2(df, father_end, r55)
    else:
        fr = find_father(df, father_end, r55)
    if fr is None:
        return None
    f_start, f_end, f_dir, f_body, f_pass = fr
    _f_first_body = abs(df.iloc[f_start]['open'] - df.iloc[f_start]['close']) / PIP
    _f_first_pct  = _f_first_body / r55 * 100 if r55 > 0 else 0
    # Volatility Ratio: father avg body per bar ÷ avg body 20 แท่งก่อนพ่อ
    _f_bar_count = f_end - f_start + 1
    _f_avg_body  = f_body / _f_bar_count if _f_bar_count > 0 else 0
    _pre20_start = max(0, f_start - 20)
    _pre20 = df.iloc[_pre20_start:f_start]
    if len(_pre20) > 0:
        _pre20_avg = sum(abs(r['open'] - r['close']) / PIP for _, r in _pre20.iterrows()) / len(_pre20)
        _vol_ratio = round(_f_avg_body / _pre20_avg, 2) if _pre20_avg > 0 else 0
    else:
        _vol_ratio = 0

    # Round 2: พ่อ R2 เริ่มต้องอยู่ใน 10 แท่งจาก child bar R1
    if round2_info and (f_start - round2_info['entry_bar']) > 6:
        return None

    trend = 'NONE'
    f_pct_r55 = f_body / r55 * 100

    # 3. แท่งแม่
    _min_m_pct = 2.0 if round2_info else 2.0
    mr = validate_mother(df, mother_idx, f_dir, f_body, r55=r55, min_pct=_min_m_pct)
    if mr is None:
        return None
    m_body, m_pct, m_quality = mr

    # 4. ทิศทางไม้รวย (ไม่มี anti-trend)
    trend   = 'NONE'
    mai_dir = 'BUY' if f_dir == 'DOWN' else 'SELL'

    # 5. Entry price
    m_row        = df.iloc[mother_idx]
    father_close = df.iloc[f_end]['close']
    mother_open  = m_row['open']
    tech_point   = (father_close + mother_open) / 2
    buffer_pips  = min(100.0, f_body * 0.05)  # ต่ำสุดระหว่าง 100pip และ 5%R55

    mother_close = m_row['close']
    mid_mother   = (mother_open  + mother_close) / 2
    entry_rule  = f"แม่ {m_pct:.1f}% → 3 จุดเข้า"

    # 6. คำนวณ 3 จุดเข้า + ตรวจ child bar
    child      = df.iloc[bar_idx]
    # จุดเข้า 3 ไม้ แบ่งตาม % แม่
    _e3_offset = f_body * 0.10 * PIP  # 10% พ่อ
    _e_tech  = tech_point
    _e_far   = tech_point - _e3_offset if mai_dir == 'BUY' \
              else tech_point + _e3_offset  # ห่างออก 10%
    _entries = []
    _obs_override = False

    if 2 <= m_pct < 10:
        # แม่ 2-10%: ไม้1=market, ไม้2=tech, ไม้3=tech±10%
        _e1 = child['open']
        _e2 = _e_tech
        _e3 = _e_far
        _e1_label = 'จุด1:market(open_child)'
        _e2_label = 'จุด2:tech_point'
        _e3_label = 'จุด3:tech±10%พ่อ'
    else:
        # แม่ 10-25%: ไม้1=mid แม่, ไม้2=tech, ไม้3=tech±10%
        _e1 = mid_mother
        _e2 = _e_tech
        _e3 = _e_far
        _e1_label = 'จุด1:mid_แม่'
        _e2_label = 'จุด2:tech_point'
        _e3_label = 'จุด3:tech±10%พ่อ'

    if mai_dir == 'BUY':
        # จุด 1
        if 2 <= m_pct < 10:
            _entries.append((_e1, _e1_label, True))   # market fill
        else:
            _entries.append((_e1, _e1_label, False))  # limit (ต้องตรวจ fill)
        # จุด 2,3: limit เสมอ
        _entries.append((_e2, _e2_label, False))
        _entries.append((_e3, _e3_label, False))
    else:
        # จุด 1
        if 2 <= m_pct < 10:
            _entries.append((_e1, _e1_label, True))   # market fill
        else:
            _entries.append((_e1, _e1_label, False))  # limit (ต้องตรวจ fill)
        # จุด 2,3: limit เสมอ
        _entries.append((_e2, _e2_label, False))
        _entries.append((_e3, _e3_label, False))

    if not _entries: return None
    # entry หลัก = จุดแรก (ใช้ใน TP/SL/Lot calculation)
    entry       = _e1
    entry_rule  = f"แม่ {m_pct:.1f}% → 3 จุด ({len(_entries)} fill)"

    # 7. TP
    tp1, tp2, tp3 = calc_tp(tech_point, f_body, mai_dir)

    # 8. SL = 40%R55 จาก tech_point (ทุกกรณี)
    _sl_offset = f_body * 0.40 * PIP
    if mai_dir == 'BUY':
        sl = tech_point - _sl_offset
    else:
        sl = tech_point + _sl_offset
    sl_label = f'SL 40%พ่อ ({f_body*0.40:.0f}pip) = {sl:.3f}'
    sl_pips  = abs(entry - sl) / PIP
    if sl_pips < 1:
        return None

    # 9. TP = 45%R55 จาก tech_point (ทุกกรณี)
    _tp_offset = f_body * 0.45 * PIP
    if mai_dir == 'BUY':
        tp_sel = tech_point + _tp_offset
    else:
        tp_sel = tech_point - _tp_offset
    tp_lbl = f'TP 45%พ่อ ({f_body*0.45:.0f}pip) = {tp_sel:.3f}'


    # ── Round 2: TP/SL จากพ่อรวม (R1+R2) ──
    if round2_info:
        _f_open_r1  = round2_info['f_open_r1']
        _f_close_r2 = df.iloc[f_end]['close']
        _combined   = abs(_f_open_r1 - _f_close_r2) / PIP  # พ่อรวม pip
        tp_sel = tech_point + _combined * 0.45 * PIP if mai_dir == 'BUY' \
                 else tech_point - _combined * 0.45 * PIP
        sl     = tech_point - _combined * 0.30 * PIP if mai_dir == 'BUY' \
                 else tech_point + _combined * 0.30 * PIP
        tp_lbl   = f'TP R2 45%พ่อรวม ({_combined*0.45:.0f}pip) = {tp_sel:.3f}'
        sl_label = f'SL R2 30%พ่อรวม ({_combined*0.30:.0f}pip) = {sl:.3f}'
        sl_pips  = abs(entry - sl) / PIP

    # ── เงื่อนไข TP30%/SL45% จาก 50 แท่งก่อนพ่อ ─────────────────
    if not round2_info:  # ไม่ใช้กับรอบ 2
        _pre50_start = max(0, f_start - 50)
        _pre50       = df.iloc[_pre50_start:f_start]
        _r55_window  = df.iloc[max(0, bar_idx - RANGE_WINDOW):bar_idx]
        _low55  = _r55_window['low'].min()
        _level60 = _low55 + r55 * 0.60 * PIP
        _level40 = _low55 + r55 * 0.40 * PIP
        _tp30_trigger = False
        if mai_dir == 'BUY' and len(_pre50) > 0:
            if _pre50['low'].min() >= _level60:
                _tp30_trigger = True
        elif mai_dir == 'SELL' and len(_pre50) > 0:
            if _pre50['high'].max() <= _level40:
                _tp30_trigger = True
        if _tp30_trigger:
            tp_sel = tech_point + f_body * 0.30 * PIP if mai_dir == 'BUY' \
                     else tech_point - f_body * 0.30 * PIP
            sl     = tech_point - f_body * 0.45 * PIP if mai_dir == 'BUY' \
                     else tech_point + f_body * 0.45 * PIP
            tp_lbl   = 'TP 30% (50แท่งก่อนพ่อ ไม่มี Low<60%R55)' if mai_dir == 'BUY' \
                       else 'TP 30% (50แท่งก่อนพ่อ ไม่มี High>40%R55)'
            sl_label = f'SL 40%พ่อ ({f_body*0.40:.0f}pip) = {sl:.3f}'
            sl_pips  = abs(entry - sl) / PIP

    tp_pips = abs(tp_sel - entry) / PIP
    if tp_pips < 200:  # TP < 200 pip → ไม่เข้า
        return None
    rr      = tp_pips / sl_pips if sl_pips > 0 else 0.0
    lot     = calc_lot(portfolio, sl_pips)
    # แม่ 20-30% → ลด lot ลงครึ่งหนึ่ง
    if 17 <= m_pct <= 25:
        lot = round(lot / 2, 2)
    # พ่อ > 90% R55 → เพิ่ม lot ×2
    _f_pct = f_body / r55 * 100  # คำนวณ % R55 ของพ่อ
    if _f_pct > 90:  # พ่อ > 90% R55 → เพิ่ม lot ×2
        lot = round(lot * 2, 2)

    # สร้าง entries list พร้อม lot
    _n = len(_entries)
    _lot_each = round(lot / _n, 2) if _n > 0 else lot
    _entries_full = [(ep, er, _lot_each, mkt) for ep, er, mkt in _entries]

    return MaiRuaySignal(
        bar_index=bar_idx, time=str(df.iloc[bar_idx]['time']),
        direction=mai_dir, trend=trend,
        father_start=f_start, father_end=f_end,
        father_start_time=str(df.iloc[f_start]['time']),
        father_end_time=str(df.iloc[f_end]['time']),
        father_bars=f_end - f_start + 1,
        father_body_pips=f_body, father_pct_r55=f_body/r55*100,
        mother_idx=mother_idx, mother_body_pips=m_body,
        mother_pct_r55=m_pct, mother_quality=m_quality,
        tech_point=tech_point, buffer_pips=buffer_pips,
        entry=entry, entry_rule=entry_rule,
        sl=sl, sl_label=sl_label, sl_pips=sl_pips,
        tp1=tp1, tp2=tp2, tp3=tp3,
        tp_selected=tp_sel, tp_label=tp_lbl, tp_pips=tp_pips,
        range55=r55, rr=rr, lot=lot, portfolio=portfolio,
        anti_trend=anti_trend,
        entries=_entries_full,
        is_round2=bool(round2_info),
        father_first_pct=round(_f_first_pct, 1),
        father_pass=f_pass,
        vol_ratio=_vol_ratio,
    )


# ====================================================================
# TIME & GAP RULES
# ====================================================================
def _is_friday_close(time_val,
                     close_hour=MARKET_CLOSE_HOUR,
                     close_min=MARKET_CLOSE_MIN,
                     buffer_min=NO_TRADE_MINS) -> bool:
    """True = ห้ามเปิด Order (วันศุกร์ใกล้ปิด)"""
    try:
        dt = pd.to_datetime(time_val)
        if dt.weekday() != 4:
            return False
        close_dt = dt.replace(hour=close_hour, minute=close_min, second=0, microsecond=0)
        diff = (close_dt - dt).total_seconds() / 60
        return 0 <= diff <= buffer_min
    except Exception:
        return False


def _find_gap_start(df: pd.DataFrame,
                    gap_pct=GAP_THRESHOLD_PCT,
                    wait=GAP_WAIT_BARS) -> int:
    """คืน bar index ที่เริ่มวิเคราะห์ได้หลัง Gap"""
    if len(df) < 2:
        return 0
    first55 = df.iloc[:min(55, len(df))]
    r55_init = (first55['high'].max() - first55['low'].min()) / PIP
    gap_threshold = r55_init * gap_pct / 100  # USD

    for i in range(1, len(df)):
        try:
            prev_date = pd.to_datetime(df.iloc[i-1]['time']).date()
            curr_date = pd.to_datetime(df.iloc[i]['time']).date()
        except Exception:
            continue
        if curr_date != prev_date:
            gap = abs(df.iloc[i]['open'] - df.iloc[i-1]['close'])
            if gap > gap_threshold:
                return i + wait
    return 0


# ====================================================================
# BACKTEST ENGINE
# ====================================================================
def run_backtest(df: pd.DataFrame,
                 portfolio: float = 1000.0,
                 verbose: bool    = True) -> dict:
    """
    Bar-by-bar simulation ครบวงจรสำหรับ ไม้รวย
    รวมกฎ: Gap, Friday close, Loss limit (3 ไม้ติด / -30%)
    """
    results      : List[dict]              = []
    open_positions : List[MaiRuaySignal]    = []
    post_sl_scans : List[dict]               = []     # queue สแกนหลัง SL
    post_tp_scans : List[dict]               = []     # queue สแกนหลัง TP1 → TP2/TP3

    # -- Gap rule --------------------------------------------------
    gap_start  = _find_gap_start(df)
    start_bar  = max(RANGE_WINDOW - 1, gap_start)
    if gap_start > RANGE_WINDOW and verbose:
        print(f"⚠️  Gap พบ → รอ {GAP_WAIT_BARS} แท่ง เริ่มเทรดที่ bar {gap_start}")

    _inject_hit    = None
    _inject_bar    = None
    _just_closed   = False
    pending_limits = []
    _r1_info       = None
    for i in range(start_bar, len(df)):
        bar = df.iloc[i]
        _just_closed = False

        # -- ตรวจ Pending Limit Orders (ไม้ 2,3) ─────────────────
        _expired = []
        for _pl_idx, (_pl_sig, _pl_expiry) in enumerate(pending_limits):
            if i > _pl_expiry:
                _expired.append(_pl_idx)  # หมดอายุ
                continue
            # ตรวจเฉียด TP: ถ้าราคาเข้าใกล้ TP ของ signal แล้ว → ยกเลิก limit
            _tp_buf = _pl_sig.range55 * 0.10 * PIP  # 10%R55
            if _pl_sig.direction == 'BUY' and bar['high'] >= _pl_sig.tp_selected - _tp_buf:
                _expired.append(_pl_idx)  # ราคาขึ้นเกือบถึง TP → ยกเลิก
                continue
            elif _pl_sig.direction == 'SELL' and bar['low'] <= _pl_sig.tp_selected + _tp_buf:
                _expired.append(_pl_idx)  # ราคาลงเกือบถึง TP → ยกเลิก
                continue
            _filled = False
            if _pl_sig.direction == 'BUY':
                if bar['low'] <= _pl_sig.entry:
                    _filled = True
            else:
                if bar['high'] >= _pl_sig.entry:
                    _filled = True
            if _filled:
                _pl_sig.time = str(bar['time'])  # อัพเดทเวลา fill จริง
                _pl_sig.bar_index = i
                open_positions.append(_pl_sig)
                _expired.append(_pl_idx)
        for _rm in sorted(_expired, reverse=True):
            pending_limits.pop(_rm)

        # -- Loss limit --------------------------------------------
        # -- ตรวจ SL / TP ------------------------------------------
        # ใช้ inject จาก child bar check ถ้ามี (ใช้กับทุก position)
        _use_inject = _inject_hit is not None and _inject_bar is not None
        for _pos_idx in range(len(open_positions)-1, -1, -1):
            sig = open_positions[_pos_idx]
            if _use_inject:
                hit = _inject_hit; bar = _inject_bar
            else:
                hit = None
            if hit is None:
                if sig.direction == 'BUY':
                    if   bar['low']  <= sig.sl:          hit = 'SL'
                    elif bar['high'] >= sig.tp_selected:  hit = 'TP'
                else:
                    if   bar['high'] >= sig.sl:          hit = 'SL'
                    elif bar['low']  <= sig.tp_selected:  hit = 'TP'

            if hit:
                actual_exit = sig.tp_selected if hit == 'TP' else sig.sl
                pnl_pips    = sig.tp_pips if hit == 'TP' else -sig.sl_pips
                pnl_usd     = pnl_pips * sig.lot


                ot  = str(sig.time)
                ct  = str(bar['time'])
                rr_actual = round(abs(pnl_pips) / sig.sl_pips, 2) if sig.sl_pips > 0 else 0

                results.append({
                    # -- เวลา --------------------------------------
                    'open_time'         : ot,
                    'close_time'        : ct,
                    'date'              : ot[:10] if len(ot) >= 10 else ot,
                    # -- ผล ----------------------------------------
                    'direction'         : sig.direction,
                    'pattern'           : 'MAI_RUAY',
                    'result'            : 'WIN' if hit == 'TP' else 'LOSS',
                    'entry'             : sig.entry,
                    'sl'                : sig.sl,
                    'tp'                : sig.tp_selected,
                    'actual_exit'       : actual_exit,
                    'lot'               : sig.lot,
                    'pnl'               : round(pnl_pips, 1),
                    'pnl_usd'           : round(pnl_usd, 2),
                    'rr'                : rr_actual,
                    # -- Signal details ----------------------------
                    'range55'           : round(sig.range55, 0),
                    'trend'             : sig.trend,
                    'father_bars'       : sig.father_bars,
                    'father_start_time' : sig.father_start_time,
                    'father_end_time'   : sig.father_end_time,
                    'father_body_pips'  : round(sig.father_body_pips, 0),
                    'father_first_pct'  : round(sig.father_first_pct, 1),
                    'father_pass'       : sig.father_pass,
                    'vol_ratio'         : sig.vol_ratio,
                    'signal_id'         : f'{sig.tech_point:.3f}_{sig.direction}',
                    'father_pct_r55'    : round(sig.father_pct_r55, 1),
                    'mother_body_pips'  : round(sig.mother_body_pips, 0),
                    'mother_pct_r55' : round(sig.mother_pct_r55, 1),
                    'mother_quality'    : sig.mother_quality,
                    'tech_point'        : sig.tech_point,
                    'tech_price'        : sig.tech_point,
                    'tech_time'         : '',
                    'entry_rule'        : sig.entry_rule,
                    # -- TP/SL -------------------------------------
                    'tp1'               : round(sig.tp1, 3),
                    'tp2_base'          : round(sig.tp2, 3),
                    'tp2_adj'           : None,
                    'tp3'               : round(sig.tp3, 3),
                    'tp_name'           : sig.tp_label,
                    'sl_pips'           : round(sig.sl_pips, 0),
                    'tp_pips'           : round(sig.tp_pips, 0),
                    'sl_label'          : sig.sl_label,
                    'sl_name'           : 'SL1',
                    'sl_all'            : {'SL1': round(sig.sl, 3)},
                    # -- compat fields (Mountain keys) -------------
                    'same_bar'          : False,
                    'trail_log'         : '',
                    'post_sl_depth_pip'       : None,
                    'post_sl_depth_pct'       : None,
                    'post_sl_scan_complete'   : None,
                    'tp_level_hit'            : None,
                    'mae_h_pct'         : None,
                    'post_sl_h_pct'     : None,
                    'sl_adjusted'       : None,
                    'reached_tp2'       : False,
                    'reached_tp3'       : False,
                })

                # ระบุ TP level ที่โดน (เช็คบนแท่งนี้ก่อน)
                if hit == 'TP':
                    _d   = sig.direction
                    _tp1 = sig.tp1
                    _tp2 = sig.tp2
                    _tp3 = sig.tp3
                    _need_scan = False
                    if _d == 'BUY':
                        if   bar['high'] >= _tp3: results[-1]['tp_level_hit'] = 'TP3'
                        elif bar['high'] >= _tp2:
                            results[-1]['tp_level_hit'] = 'TP2'
                            _need_scan = True  # ยังไม่ถึง TP3 → สแกนต่อ
                        else:
                            results[-1]['tp_level_hit'] = 'TP1'
                            _need_scan = True
                    else:
                        if   bar['low'] <= _tp3:  results[-1]['tp_level_hit'] = 'TP3'
                        elif bar['low'] <= _tp2:
                            results[-1]['tp_level_hit'] = 'TP2'
                            _need_scan = True
                        else:
                            results[-1]['tp_level_hit'] = 'TP1'
                            _need_scan = True
                    if _need_scan:
                        post_tp_scans.append({
                            'direction'  : _d,
                            'tp1'        : _tp1,
                            'tp2'        : _tp2,
                            'tp3'        : _tp3,
                            'tech_point' : sig.tech_point,
                            'sl'         : sig.sl,
                            'trade'      : results[-1],
                        })

                if verbose:
                    icon = '✅' if hit == 'TP' else '❌'
                    print(f"   {icon} {hit} @ {bar['time']}  "
                          f"PnL={pnl_pips:+.0f}pip ({pnl_usd:+.2f}$)  "
)
                # เริ่มสแกน post-SL depth (results[-1] = trade ปัจจุบัน ✅)
                if hit == 'SL':
                    # รวมแท่ง SL เข้าใน extreme ทันที (ไม่ข้ามแท่งนี้)
                    _init_extreme = (
                        min(sig.tech_point, bar['low'])   if sig.direction == 'BUY'
                        else max(sig.tech_point, bar['high'])
                    )
                    post_sl_scans.append({
                        'direction'        : sig.direction,
                        'sl'               : sig.sl,
                        'tech_point'       : sig.tech_point,
                        'father_body_pips' : sig.father_body_pips,
                        'extreme'          : _init_extreme,
                        'trade'            : results[-1],
                    })
                # บันทึกสำหรับรอบ 2 (เฉพาะ SL)
                    _r1_info = {
                        'tech': sig.tech_point,
                        'f_open_r1': df.iloc[sig.father_start]['open'],
                        'entry_bar': sig.bar_index,
                        'direction': sig.direction,
                    }
                else:
                    _r1_info = None  # TP ชนะ → ไม่มีรอบ 2
                open_positions.pop(_pos_idx)
                _just_closed = True
            continue  # ยังใน position

        # reset inject หลัง loop จบ
        if _use_inject:
            _inject_hit = None; _inject_bar = None

        # -- Post-SL depth scan (queue) ----------------------------
        still_scanning = []
        for ps in post_sl_scans:
            f_pip = ps['father_body_pips']
            tp    = ps['tech_point']
            limit = f_pip * 2 * PIP
            if ps['direction'] == 'BUY':
                ps['extreme'] = min(ps['extreme'], bar['low'])
                stop = bar['high'] >= tp or bar['low'] <= tp - limit
            else:
                ps['extreme'] = max(ps['extreme'], bar['high'])
                stop = bar['low'] <= tp or bar['high'] >= tp + limit
            if stop:
                depth     = abs(ps['extreme'] - ps['tech_point']) / PIP
                depth_pct = depth / f_pip * 100 if f_pip > 0 else 0
                ps['trade']['post_sl_depth_pip']     = round(depth, 1)
                ps['trade']['post_sl_depth_pct']     = round(depth_pct, 1)
                ps['trade']['post_sl_scan_complete'] = True
            else:
                still_scanning.append(ps)
        post_sl_scans = still_scanning

        # -- Post-TP scan (TP1 hit แล้ว ราคาไปถึง TP2/TP3 ไหม) -----
        still_tp = []
        for pt in post_tp_scans:
            d    = pt['direction']
            tp2  = pt['tp2']
            tp3  = pt['tp3']
            _sl  = pt['sl']
            done = False
            if d == 'BUY':
                if bar['high'] >= tp3:
                    pt['trade']['tp_level_hit'] = 'TP3'
                    done = True                          # TP3 = หยุด
                elif bar['high'] >= tp2:
                    pt['trade']['tp_level_hit'] = 'TP2' # TP2 = บันทึก แต่สแกนต่อ
                # หยุดเมื่อ TP3 โดนแล้ว หรือราคากลับถึง SL
                if not done and bar['low'] <= _sl:
                    done = True
            else:
                if bar['low'] <= tp3:
                    pt['trade']['tp_level_hit'] = 'TP3'
                    done = True
                elif bar['low'] <= tp2:
                    pt['trade']['tp_level_hit'] = 'TP2'
                if not done and bar['high'] >= _sl:
                    done = True
            if not done:
                still_tp.append(pt)
        post_tp_scans = still_tp

        # -- Friday close rule -------------------------------------
        if _is_friday_close(bar['time']):
            continue

        # -- หา Setup (เปิดใหม่ได้เฉพาะเมื่อไม่มี position และไม่เพิ่งปิดแท่งนี้) --
        if open_positions or pending_limits or _just_closed:
            continue
        # ลองรอบ 1 ก่อน ถ้าไม่ได้ + มี r1_tech → ลองรอบ 2
        # ลองรอบ 1 ก่อน ถ้าไม่ได้ + มี r1_info → ลองรอบ 2
        sig = analyze_bar(df, i, portfolio)
        if sig is None and _r1_info and (i - _r1_info['entry_bar']) <= 16:  # outer limit (พ่อ R2 เริ่มต้องอยู่ใน 6)
            sig = analyze_bar(df, i, portfolio, round2_info=_r1_info)
            # ต้องทิศเดียวกับรอบ 1
            if sig is not None and sig.direction != _r1_info['direction']:
                sig = None
            if sig is not None:
                _r1_info = None  # reset หลังเข้ารอบ 2
        if sig is None:
            continue

        # เปิด position แยกตาม entries ที่ fill
        import copy
        for _eidx, (_ep, _er, _el, _is_mkt) in enumerate(sig.entries):
            _sig_copy           = copy.copy(sig)
            _sig_copy.entry     = _ep
            _sig_copy.entry_rule= _er
            _sig_copy.lot       = _el
            # ปรับ TP ตามจุดเข้า: ถ้าเข้าเสียเปรียบ → TP = entry ± SL_dist (R:R=1:1)
            _sl_dist = abs(_ep - sig.sl)
            _base_tp_dist = abs(sig.tp_selected - _ep)
            if _base_tp_dist >= _sl_dist:
                # ได้เปรียบ → TP ปกติ (R:R > 1 อยู่แล้ว)
                _adj_tp = sig.tp_selected
            else:
                # เสียเปรียบ → TP = entry ± SL_dist (R:R = 1.0)
                if sig.direction == 'BUY':
                    _adj_tp = _ep + _sl_dist
                else:
                    _adj_tp = _ep - _sl_dist
            _sig_copy.tp_selected = _adj_tp
            _sig_copy.tp_pips   = abs(_adj_tp - _ep) / PIP
            _sig_copy.sl_pips   = abs(_ep - sig.sl) / PIP
            _sig_copy.rr        = round(_sig_copy.tp_pips / _sig_copy.sl_pips, 2) if _sig_copy.sl_pips > 0 else 0
            if _is_mkt:
                # market fill: fill ทันที
                open_positions.append(_sig_copy)
            else:
                # limit: ตรวจ fill บน child bar ก่อน → ถ้าไม่ถึงค่อยวาง limit
                _child = df.iloc[i]
                _filled_child = False
                if _sig_copy.direction == 'BUY' and _child['low'] <= _sig_copy.entry:
                    _filled_child = True
                elif _sig_copy.direction == 'SELL' and _child['high'] >= _sig_copy.entry:
                    _filled_child = True
                if _filled_child:
                    open_positions.append(_sig_copy)
                else:
                    # วาง limit order รอ 5 แท่ง
                    pending_limits.append((_sig_copy, i + 5))
        if verbose:
            icon = "📈" if sig.direction == 'BUY' else "📉"
            print(f"\n{'='*60}")
            print(f"{icon} ไม้รวย {sig.direction}  @ {sig.time}")
            print(f"{'='*60}")
            print(f"  Entry={sig.entry:.3f}  SL={sig.sl:.3f}  TP={sig.tp_selected:.3f}")
            print(f"  R:R={sig.rr:.2f}  Lot={sig.lot}  Trend={sig.trend}")
            print(f"  พ่อ={sig.father_bars}แท่ง({sig.father_pct_r55:.0f}%R55) "
                  f"แม่={sig.mother_pct_r55:.0f}%R55 {sig.mother_quality}")
            print(f"  {sig.entry_rule}")

        # ── ตรวจ TP/SL บน child bar ทันทีหลังเปิด position ─────
        if open_positions:
            _child_bar = df.iloc[i]
            _any_hit = False
            for _cp in open_positions:
                _hit_child = None
                if _cp.direction == 'BUY':
                    if   _child_bar['low']  <= _cp.sl:          _hit_child = 'SL'
                    elif _child_bar['high'] >= _cp.tp_selected: _hit_child = 'TP'
                else:
                    if   _child_bar['high'] >= _cp.sl:          _hit_child = 'SL'
                    elif _child_bar['low']  <= _cp.tp_selected: _hit_child = 'TP'
                if _hit_child:
                    _any_hit = True
                    break
            if _any_hit:
                _inject_hit = _hit_child
                _inject_bar = _child_bar
            else:
                _inject_hit = None
                _inject_bar = None

    # -- Finalize scan ค้าง (loop จบหรือ break ก่อน stop) ----------
    for ps in post_sl_scans:
        f_pip     = ps['father_body_pips']
        depth     = abs(ps['extreme'] - ps['tech_point']) / PIP
        depth_pct = depth / f_pip * 100 if f_pip > 0 else 0
        ps['trade']['post_sl_depth_pip'] = round(depth, 1)
        ps['trade']['post_sl_depth_pct'] = round(depth_pct, 1)
        ps['trade']['post_sl_scan_complete'] = False  # scan ยังไม่จบ
    # -- Summary ---------------------------------------------------
    wins     = [r for r in results if r['result'] == 'WIN']
    losses   = [r for r in results if r['result'] == 'LOSS']
    net_pip  = sum(r['pnl'] for r in results)
    net_usd  = sum(r['pnl_usd'] for r in results)
    wr       = len(wins) / len(results) * 100 if results else 0

    return {
        'trades'         : results,
        'n_trades'       : len(results),
        'n_wins'         : len(wins),
        'n_losses'       : len(losses),
        'win_rate'       : wr,
        'net_pip'        : net_pip,
        'net_usd'        : net_usd,
        'init_portfolio' : portfolio,
    }


# ====================================================================
# PRINT SUMMARY (terminal)
# ====================================================================
def print_summary(summary: dict, portfolio: float = 1000.0):
    trades = summary['trades']
    if not trades:
        print("\n📭  ไม่พบสัญญาณใดในช่วงที่ทดสอบ")
        return

    print(f"\n{'='*64}")
    print("  📊  SIMULATION SUMMARY — ไม้รวย (Branch F)")
    print(f"{'='*64}")
    hdr = f"{'#':>3}  {'ผล':2}  {'เวลาเปิด':<20}  {'Dir':4}  {'pip':>8}  {'USD':>8}  {'R:R':>5}  {'พ่อ':>5}  {'แม่':>5}"
    print(hdr)
    print('-' * len(hdr))
    for i, t in enumerate(trades, 1):
        icon = '✅' if t['result'] == 'WIN' else '❌'
        print(f"{i:>3}  {icon}  {t['open_time'][:19]:<20}  {t['direction']:4}  "
              f"{t['pnl']:>+8.0f}  {t['pnl_usd']:>+8.2f}  {t['rr']:>5.2f}  "
              f"{t['father_bars']:>3}แท่ง  {t['mother_pct_r55']:>4.0f}%")
    print('-' * len(hdr))
    print(f"\n  ไม้ทั้งหมด  : {summary['n_trades']}")
    print(f"  ✅ TP       : {summary['n_wins']}")
    print(f"  ❌ SL       : {summary['n_losses']}")
    print(f"  Win Rate    : {summary['win_rate']:.1f}%")
    print(f"  Net pip     : {summary['net_pip']:+.0f}")
    print(f"  Net USD     : {summary['net_usd']:+.2f}")
    print(f"  Portfolio   : {portfolio:.0f} → {portfolio + summary['net_usd']:.2f} USD")
    print(f"{'='*64}")


# ====================================================================
# GOOGLE SHEETS EXPORT
# ====================================================================
def export_to_gsheet(summary: dict,
                     csv_filename: str,
                     version: str = "v1.0"):
    """
    สร้าง Google Sheet สรุปผล Backtest ไม้รวย
    9 Tab: รายการเทรด / สรุปรวม / แยก Pattern / รายวัน /
           รายเดือน / รายวันสัปดาห์ / Session / เงื่อนไข / Equity Curve
    """
    try:
        from googleapiclient.discovery import build
    except ImportError:
        print("❌ ต้องติดตั้ง google-api-python-client ก่อน")
        print("   !pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib")
        return

    # -- Auth (Colab) ----------------------------------------------
    try:
        from google.colab import auth as _ca
        _ca.authenticate_user()
        from google.auth import default as _gd
        creds, _ = _gd(scopes=[
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive',
        ])
    except Exception as e:
        print(f"❌ Auth ล้มเหลว: {e}")
        return

    sheets = build('sheets', 'v4', credentials=creds)
    drive  = build('drive',  'v3', credentials=creds)

    # -- ชื่อ Sheet ------------------------------------------------
    base        = re.sub(r'\.csv$', '', os.path.basename(csv_filename), flags=re.IGNORECASE)
    sheet_title = f"{base} ไม้รวย {version}"

    body = {'properties': {'title': sheet_title}}
    ss   = sheets.spreadsheets().create(
               body=body, fields='spreadsheetId,spreadsheetUrl').execute()
    sid  = ss['spreadsheetId']
    url  = ss['spreadsheetUrl']
    print(f"✅ สร้าง Google Sheet: {sheet_title}")
    print(f"   🔗 {url}")

    trades    = summary['trades']
    init_port = summary.get('init_portfolio', 1000.0)
    win_pip   = sum(t['pnl'] for t in trades if t['result'] == 'WIN')
    loss_pip  = sum(t['pnl'] for t in trades if t['result'] == 'LOSS')
    win_usd   = sum(t['pnl_usd'] for t in trades if t['result'] == 'WIN')
    loss_usd  = sum(t['pnl_usd'] for t in trades if t['result'] == 'LOSS')
    net_usd   = summary['net_usd']
    net_pip   = summary['net_pip']

    WEEKDAY_TH = {0:'จันทร์',1:'อังคาร',2:'พุธ',3:'พฤหัส',4:'ศุกร์'}
    SESS_TIME  = {
        'Asian':'00:00–07:00','Asian+London':'07:00–09:00',
        'London':'09:00–13:00','London+NewYork':'13:00–16:00',
        'New York':'16:00–22:00','After NY':'22:00–24:00',
    }

    def get_wd(d):
        try:
            return datetime.strptime(d, '%Y-%m-%d').weekday()
        except Exception:
            return None

    def get_sess(ts):
        try:
            h = datetime.strptime(str(ts)[:16], '%Y-%m-%d %H:%M').hour
            if  0<=h< 7: return 'Asian'
            if  7<=h< 9: return 'Asian+London'
            if  9<=h<13: return 'London'
            if 13<=h<16: return 'London+NewYork'
            if 16<=h<22: return 'New York'
            return 'After NY'
        except Exception:
            return 'Other'

    # ----------------------------------------------------------------
    # Sheet 1 — รายการเทรด
    # ----------------------------------------------------------------
    # col index (0-based หลัง header)
    TP_COL  = 18   # TP ราคา
    TPR_COL = 19   # เหตุผล TP
    SL_COL  = 20   # SL ราคา
    SLR_COL = 21   # เหตุผล SL
    hdr1 = [['วันเปิด','เวลาเปิด','วันปิด','เวลาปิด',
              'Direction','Entry','Exit','Lot','pip','USD','R:R',
              'เทรน','Vol Ratio','พ่อ(แท่ง)','พ่อ(%R55)','พ่อแท่งแรก(%R55)','เงื่อนไขพ่อ','แม่(%R55)','คุณภาพแม่',
              'จุดเทคนิค','Entry Rule',
              'TP','เหตุผล TP',
              'SL','เหตุผล SL',
              'พ่อเริ่ม','พ่อจบ','Post-SL pip','Post-SL %พ่อ']]
    rows1 = []
    for t in trades:
        ot  = str(t['open_time'])
        ct  = str(t['close_time'])
        od  = ot[:10] if len(ot) >= 10 else ot
        ow  = ot[11:16] if len(ot) >= 16 else ''
        cd  = ct[:10] if len(ct) >= 10 else ct
        cw  = ct[11:16] if len(ct) >= 16 else ''
        rows1.append([
            od, ow, cd, cw,
            t['direction'],
            t['entry'],
            t.get('actual_exit', t['tp'] if t['result']=='WIN' else t['sl']),
            round(t['lot'], 2),
            round(t['pnl'], 1),
            round(t['pnl_usd'], 2),
            t['rr'],
            t.get('trend',''),
            t.get('vol_ratio',''),
            t.get('father_bars',''),
            t.get('father_pct_r55',''),
            t.get('father_first_pct',''),
            t.get('father_pass',''),
            t.get('mother_pct_r55',''),
            t.get('mother_quality',''),
            round(t['tech_point'], 3) if t.get('tech_point') else '',
            t.get('entry_rule',''),
            round(t['tp'], 3),          # TP ราคา
            t.get('tp_name',''),         # เหตุผล TP
            round(t['sl'], 3),          # SL ราคา
            t.get('sl_label',''),        # เหตุผล SL
            t.get('father_start_time','')[:16] if t.get('father_start_time') else '',
            t.get('father_end_time','')[:16]   if t.get('father_end_time')   else '',
            t.get('post_sl_depth_pip','') if t['result']=='LOSS' else '',
            t.get('post_sl_depth_pct','') if t['result']=='LOSS' else '',
        ])

    # ----------------------------------------------------------------
    # Sheet 2 — สรุปรวม
    # ----------------------------------------------------------------
    _dg = defaultdict(lambda:{'w':0,'l':0,'net':0.0})
    for _t in trades:
        _d = _t.get('date','')[:10]
        if not _d: continue
        if _t['result'] == 'WIN': _dg[_d]['w'] += 1
        else: _dg[_d]['l'] += 1
        _dg[_d]['net'] += _t.get('pnl_usd', 0)
    total_days = len(_dg)
    win_days   = sum(1 for g in _dg.values() if g['net'] > 0)
    loss_days  = total_days - win_days

    mws=mls=cws=cls=0
    mwusd=mlusd=cwusd=clusd=0.0
    # Drawdown / Win depth (นับเป็นไม้)
    max_dd_trades=max_wd_trades=0
    # Drawdown/Win depth — นับแบบสะสม (ไม้ทั้งหมดตั้งแต่ peak จนถึง new peak)
    peak_eq=init_port; cur_dd_trades=0; cur_wd_trades=0
    in_drawdown=False; in_windepth=False
    running_eq=init_port
    # วันที่ชนะ/แพ้ติดกันสูงสุด
    max_win_days=max_loss_days=0
    cur_win_days=cur_loss_days=0
    prev_day_result=None
    day_results={}
    for t in trades:
        u = t.get('pnl_usd', 0)
        d = t.get('date','')[:10]
        if d not in day_results: day_results[d]=[]
        day_results[d].append(t['result'])
        running_eq += u
        if t['result'] == 'WIN':
            cws+=1; cls=0;  mws=max(mws, cws)
            cwusd+=u; clusd=max(0.0, clusd-u); mwusd=max(mwusd, cwusd)
            # Win depth: นับไม้สะสมตั้งแต่ equity ต่ำกว่า peak จนกลับ
            cur_wd_trades+=1; max_wd_trades=max(max_wd_trades,cur_wd_trades)
            cur_dd_trades=0
            if running_eq >= peak_eq:
                peak_eq=running_eq; cur_wd_trades=0
        else:
            cls+=1; cws=0;  mls=max(mls, cls)
            clusd+=abs(u); cwusd=max(0.0, cwusd-abs(u)); mlusd=max(mlusd, clusd)
            # Drawdown depth: นับไม้สะสมตั้งแต่ equity ต่ำกว่า peak
            cur_dd_trades+=1; max_dd_trades=max(max_dd_trades,cur_dd_trades)
            cur_wd_trades=0
    # วันที่ชนะ/แพ้ติดกัน
    cwd=cld=0
    for d in sorted(day_results):
        wins_d=day_results[d].count('WIN')
        loss_d=day_results[d].count('LOSS')
        day_net='WIN' if wins_d>loss_d else ('LOSS' if loss_d>wins_d else 'DRAW')
        if day_net=='WIN':  cwd+=1; cld=0; max_win_days=max(max_win_days,cwd)
        elif day_net=='LOSS': cld+=1; cwd=0; max_loss_days=max(max_loss_days,cld)
        else: cwd=0; cld=0

    # แจกแจงตาม % แม่ / % พ่อ
    pct_m_groups = [('<10%',0,10),('10-20%',10,20),('20-30%',20,30),('30-40%',30,40)]
    pct_f_groups = [('50-60%R55',50,60),('60-70%R55',60,70),('70-80%R55',70,80),('80-90%R55',80,90),('90-100%R55',90,100)]
    dir_groups   = [('BUY','BUY'),('SELL','SELL')]

    rows2 = [
        ['สรุปรวม','',''],
        ['รายการ','pip','USD'],
        ['เทรดทั้งหมด', summary['n_trades'],''],
        ['ชนะ',  summary['n_wins'], ''],
        ['แพ้',  summary['n_losses'],''],
        ['Win Rate', f"{summary['win_rate']:.1f}%",''],
        ['','',''],
        ['-- WR ตามจุดเข้า --','',''],
    ]
    # คำนวณ WR ตามจุดเข้า (group by signal_id)
    _sig_groups = defaultdict(list)
    for t in trades:
        _sid = t.get('signal_id', '')
        if _sid:
            _sig_groups[_sid].append(t)
    _sig_win = 0
    _sig_loss = 0
    for _sid, _sg in _sig_groups.items():
        _net = sum(t.get('pnl_usd', 0) for t in _sg)
        if _net > 0:
            _sig_win += 1
        else:
            _sig_loss += 1
    _sig_total = _sig_win + _sig_loss
    _sig_wr = _sig_win / _sig_total * 100 if _sig_total > 0 else 0
    # แยกตามประเภท (R1 / R2)
    _type_stats = {'R1': {'w':0,'l':0}, 'R2': {'w':0,'l':0}}
    for _sid, _sg in _sig_groups.items():
        _net = sum(t.get('pnl_usd', 0) for t in _sg)
        # ตรวจประเภทจาก father_pass ของ trade แรกในกลุ่ม
        _fp = _sg[0].get('father_pass', '')
        if 'R2' in _fp:
            _tkey = 'R2'
        else:
            _tkey = 'R1'
        if _net > 0:
            _type_stats[_tkey]['w'] += 1
        else:
            _type_stats[_tkey]['l'] += 1

    rows2 += [
        ['จุดเข้าทั้งหมด', _sig_total, f'WR {_sig_wr:.1f}%'],
        ['จุดเข้าชนะ', _sig_win, ''],
        ['จุดเข้าแพ้', _sig_loss, ''],
        ['','',''],
        ['-- แยกตามประเภท --','',''],
    ]
    for _tname, _tlabel in [('R1','ไม้รวยรอบ 1'),('R2','ไม้รวยรอบ 2')]:
        _tw = _type_stats[_tname]['w']
        _tl = _type_stats[_tname]['l']
        _tt = _tw + _tl
        _twr = _tw/_tt*100 if _tt > 0 else 0
        rows2.append([_tlabel, _tt, f'WR {_twr:.1f}% ({_tw}W/{_tl}L)'])

    # -- WR แยกตาม Pass --
    rows2 += [
        ['','',''],
        ['-- WR แยกตาม Pass --','',''],
        ['Pass','จำนวน','WR'],
    ]
    _pass_labels = [
        'Pass0 สีเดียว ไม่จำกัด >70%',
        'Pass0.5 ไม่จำกัด >90% +แทรก1',
        'Pass1 สีเดียว 1-8แท่ง',
        'Pass1.5 >80% +แทรก1',
        'Pass2 6-8แท่ง +แทรก ตำแหน่ง3-5',
        'R2 ไม้รวยรอบ2',
    ]
    for _plbl in _pass_labels:
        _pg = [t for t in trades if t.get('father_pass','') == _plbl]
        _pw = sum(1 for t in _pg if t['result']=='WIN')
        _pt = len(_pg)
        _pwr = _pw/_pt*100 if _pt > 0 else 0
        if _pt > 0:
            rows2.append([_plbl, _pt, f'{_pwr:.1f}% ({_pw}W/{_pt-_pw}L)'])

    # -- Pass 1 แยกย่อยตามจำนวนแท่ง --
    rows2 += [
        ['','',''],
        ['-- Pass 1 แยกตามจำนวนแท่ง --','',''],
        ['จำนวนแท่ง','จำนวน','WR'],
    ]
    _p1_trades = [t for t in trades if t.get('father_pass','') == 'Pass1 สีเดียว 1-8แท่ง']
    _p1_groups = [(1,3,'1-3 แท่ง'),(4,5,'4-5 แท่ง'),(6,8,'6-8 แท่ง')]
    for _p1lo, _p1hi, _p1lbl in _p1_groups:
        _p1g = [t for t in _p1_trades if _p1lo <= t.get('father_bars',0) <= _p1hi]
        _p1w = sum(1 for t in _p1g if t['result']=='WIN')
        _p1t = len(_p1g)
        _p1wr = _p1w/_p1t*100 if _p1t > 0 else 0
        if _p1t > 0:
            rows2.append([_p1lbl, _p1t, f'{_p1wr:.1f}% ({_p1w}W/{_p1t-_p1w}L)'])

    # -- WR ตาม Volatility Ratio --
    rows2 += [
        ['','',''],
        ['-- WR ตาม Volatility Ratio --','',''],
        ['ช่วง Ratio','จำนวน','WR'],
    ]
    _vr_ranges = [(0,1,'<1x'),(1,1.5,'1-1.5x'),(1.5,2,'1.5-2x'),(2,2.5,'2-2.5x'),
                  (2.5,3,'2.5-3x'),(3,4,'3-4x'),(4,5,'4-5x'),(5,100,'≥5x')]
    for _vlo, _vhi, _vlbl in _vr_ranges:
        _vg = [t for t in trades if _vlo <= t.get('vol_ratio',0) < _vhi]
        _vw = sum(1 for t in _vg if t['result']=='WIN')
        _vt = len(_vg)
        _vwr = _vw/_vt*100 if _vt > 0 else 0
        if _vt > 0:
            rows2.append([_vlbl, _vt, f'{_vwr:.1f}% ({_vw}W/{_vt-_vw}L)'])

    rows2 += [
        ['','',''],
        ['กำไรรวม',   round(win_pip,1),  round(win_usd,2)],
        ['ขาดทุนรวม', round(loss_pip,1), round(loss_usd,2)],
        ['Net',       round(net_pip,1),  round(net_usd,2)],
        ['','',''],
        ['Portfolio เริ่มต้น','', round(init_port,2)],
        ['Portfolio สุดท้าย', '', round(init_port+net_usd,2)],
        ['','',''],
        ['ชนะติดกันสูงสุด (ไม้)',  mws,              'ไม้'],
        ['แพ้ติดกันสูงสุด (ไม้)',  mls,              'ไม้'],
        ['Win USD สูงสุด (streak)', f'+{mwusd:.2f}',  'USD'],
        ['Loss USD สูงสุด (streak)',f'-{mlusd:.2f}',  'USD'],
        ['','',''],
        ['-- Drawdown / Win Depth --','',''],
        ['Drawdown depth (ไม้แพ้ติดกันสูงสุด)', max_dd_trades, 'ไม้'],
        ['Win depth (ไม้ชนะติดกันสูงสุด)',      max_wd_trades, 'ไม้'],
        ['','',''],
        ['-- วันที่ติดกันสูงสุด --','',''],
        ['วันชนะติดกันสูงสุด', max_win_days,  'วัน'],
        ['วันแพ้ติดกันสูงสุด', max_loss_days, 'วัน'],
        ['','',''],
        ['','',''],
        ['-- WR ตามขนาดแท่งพ่อแท่งแรก (%R55) --','',''],
        ['ช่วง %R55','WR','จำนวน'],
    ]
    _f1_ranges = [(0,5),(5,10),(10,15),(15,20),(20,25),(25,30),(30,35),(35,40),(40,100)]
    for _f1_lo, _f1_hi in _f1_ranges:
        _f1_lbl = f'{_f1_lo}-{_f1_hi}%' if _f1_hi < 100 else f'>{_f1_lo}%'
        _f1g = [t for t in trades if _f1_lo <= t.get('father_first_pct',0) < _f1_hi]
        _f1w = sum(1 for t in _f1g if t['result']=='WIN')
        _f1wr = _f1w/len(_f1g)*100 if _f1g else 0
        rows2.append([_f1_lbl, f'{_f1wr:.1f}%', len(_f1g)])
    rows2 += [
        ['','',''],
        ['-- Post-SL Depth Distribution (ไม้แพ้) --','',''],
        ['ช่วง %R55','จำนวนไม้','% ของไม้แพ้'],
    ]
    # Post-SL distribution
    sl_trades = [t for t in trades if t['result']=='LOSS' and t.get('post_sl_depth_pct') is not None]
    _brackets = [
        ('<10%',    0,   10),
        ('10-20%',  10,  20),
        ('20-30%',  20,  30),
        ('30-40%',  30,  40),
        ('40-50%',  40,  50),
        ('50-60%',  50,  60),
        ('60-70%',  60,  70),
        ('70-80%',  70,  80),
        ('80-90%',  80,  90),
        ('90-100%', 90,  100),
        ('100-110%',100, 110),
        ('110-120%',110, 120),
        ('120-130%',120, 130),
        ('130-140%',130, 140),
        ('140-150%',140, 150),
        ('150-160%',150, 160),
        ('160-170%',160, 170),
        ('170-180%',170, 180),
        ('180-190%',180, 190),
        ('190-200%',190, 200),
        ('>200%',   200, 9999),
    ]
    if sl_trades:
        total_sl = len(sl_trades)
        for _lbl, _lo, _hi in _brackets:
            _cnt = sum(1 for t in sl_trades if _lo <= t['post_sl_depth_pct'] < _hi)
            if _cnt > 0:
                rows2.append([_lbl, _cnt, f'{_cnt/total_sl*100:.1f}%'])
        rows2.append(['รวม', total_sl, '100%'])
    else:
        rows2 += [['ยังไม่มีข้อมูล','','']]
    rows2 += [
        ['','',''],
        ['-- วันเทรด --','',''],
        ['รวมวันที่มีการเทรด', total_days,'วัน'],
        ['วันกำไร',   win_days,  f'{win_days/total_days*100:.0f}%' if total_days else '-'],
        ['วันขาดทุน', loss_days, f'{loss_days/total_days*100:.0f}%' if total_days else '-'],
        ['','',''],
        ['-- แยก Direction --','',''],
        ['Direction','ไม้','WR%'],
    ]
    for dlbl, dval in dir_groups:
        _g = [t for t in trades if t['direction'] == dval]
        _w = sum(1 for t in _g if t['result']=='WIN')
        rows2.append([dlbl, len(_g), f'{_w/len(_g)*100:.1f}%' if _g else '-'])

    rows2 += [['','',''],['-- จำนวนแท่งพ่อ --','',''],['พ่อ (แท่ง)','ไม้','WR%']]
    for _bars in range(1, 9):
        _g = [t for t in trades if t.get('father_bars') == _bars]
        if not _g: continue
        _w = sum(1 for t in _g if t['result']=='WIN')
        rows2.append([f'{_bars} แท่ง', len(_g), f'{_w/len(_g)*100:.1f}%'])

    rows2 += [['','',''],['-- แม่ (%R55) --','',''],['Bracket','ไม้','WR%']]
    for lbl,lo,hi in pct_m_groups:
        _g = [t for t in trades if lo <= t.get('mother_pct_r55',0) < hi]
        if not _g: continue
        _w = sum(1 for t in _g if t['result']=='WIN')
        rows2.append([lbl, len(_g), f'{_w/len(_g)*100:.1f}%'])

    rows2 += [['','',''],['-- พ่อ (%R55) --','',''],['Bracket','ไม้','WR%']]
    for lbl,lo,hi in pct_f_groups:
        _g = [t for t in trades if lo <= t.get('father_pct_r55',0) < hi]
        if not _g: continue
        _w = sum(1 for t in _g if t['result']=='WIN')
        rows2.append([lbl, len(_g), f'{_w/len(_g)*100:.1f}%'])

    # ----------------------------------------------------------------
    # Sheet 3 — แยก Pattern (Direction)
    # ----------------------------------------------------------------
    pg = defaultdict(lambda:{'w':0,'l':0,'win_pip':0.,'loss_pip':0.,'win_usd':0.,'loss_usd':0.})
    for t in trades:
        pn = f"ไม้รวย {t['direction']}"
        if t['result']=='WIN':
            pg[pn]['w']+=1; pg[pn]['win_pip']+=t['pnl']; pg[pn]['win_usd']+=t.get('pnl_usd',0)
        else:
            pg[pn]['l']+=1; pg[pn]['loss_pip']+=t['pnl']; pg[pn]['loss_usd']+=t.get('pnl_usd',0)
    hdr3 = [['Pattern','ชนะ','แพ้','WR%','กำไร pip','ขาดทุน pip','Net pip','Net USD']]
    rows3 = []
    for pn, g in sorted(pg.items()):
        tot = g['w']+g['l']; wr2 = g['w']/tot*100 if tot else 0
        rows3.append([pn, g['w'], g['l'], round(wr2,1),
                      round(g['win_pip'],1), round(g['loss_pip'],1),
                      round(g['win_pip']+g['loss_pip'],1),
                      round(g['win_usd']+g['loss_usd'],2)])

    # ----------------------------------------------------------------
    # Sheet 4 — รายวัน
    # ----------------------------------------------------------------
    dg = defaultdict(lambda:{'w':0,'l':0,'win_pip':0.,'loss_pip':0.,'win_usd':0.,'loss_usd':0.})
    for t in trades:
        d = t.get('date','')[:10]
        if t['result']=='WIN': dg[d]['w']+=1; dg[d]['win_pip']+=t['pnl']; dg[d]['win_usd']+=t.get('pnl_usd',0)
        else: dg[d]['l']+=1; dg[d]['loss_pip']+=t['pnl']; dg[d]['loss_usd']+=t.get('pnl_usd',0)
    hdr4 = [['วันที่','ชนะ','แพ้','กำไร pip','ขาดทุน pip','Net pip','Net USD']]
    rows4 = [[d, g['w'], g['l'],
              round(g['win_pip'],1), round(g['loss_pip'],1),
              round(g['win_pip']+g['loss_pip'],1),
              round(g['win_usd']+g['loss_usd'],2)]
             for d, g in sorted(dg.items())]

    # ----------------------------------------------------------------
    # Sheet 5 — รายเดือน
    # ----------------------------------------------------------------
    mg = defaultdict(lambda:{'w':0,'l':0,'days':set(),'win_pip':0.,'loss_pip':0.,'win_usd':0.,'loss_usd':0.})
    for t in trades:
        d = t.get('date','')[:10]; ym = d[:7]
        if t['result']=='WIN': mg[ym]['w']+=1; mg[ym]['win_pip']+=t['pnl']; mg[ym]['win_usd']+=t.get('pnl_usd',0)
        else: mg[ym]['l']+=1; mg[ym]['loss_pip']+=t['pnl']; mg[ym]['loss_usd']+=t.get('pnl_usd',0)
        mg[ym]['days'].add(d)
    hdr5 = [['เดือน','วัน','ชนะ','แพ้','WR%','กำไร pip','ขาดทุน pip','Net pip','Net USD']]
    rows5 = [[ym, len(g['days']), g['w'], g['l'],
              round(g['w']/(g['w']+g['l'])*100 if g['w']+g['l'] else 0, 1),
              round(g['win_pip'],1), round(g['loss_pip'],1),
              round(g['win_pip']+g['loss_pip'],1),
              round(g['win_usd']+g['loss_usd'],2)]
             for ym, g in sorted(mg.items())]

    # ----------------------------------------------------------------
    # Sheet 6 — รายวันในสัปดาห์
    # ----------------------------------------------------------------
    wg = defaultdict(lambda:{'w':0,'l':0,'win_pip':0.,'loss_pip':0.,'win_usd':0.,'loss_usd':0.})
    for t in trades:
        wd = get_wd(t.get('date',''))
        if wd is None: continue
        if t['result']=='WIN': wg[wd]['w']+=1; wg[wd]['win_pip']+=t['pnl']; wg[wd]['win_usd']+=t.get('pnl_usd',0)
        else: wg[wd]['l']+=1; wg[wd]['loss_pip']+=t['pnl']; wg[wd]['loss_usd']+=t.get('pnl_usd',0)
    hdr6 = [['วัน','ชนะ','แพ้','WR%','Net pip','Net USD']]
    rows6 = [[WEEKDAY_TH.get(wd,'?'), g['w'], g['l'],
              round(g['w']/(g['w']+g['l'])*100 if g['w']+g['l'] else 0, 1),
              round(g['win_pip']+g['loss_pip'],1),
              round(g['win_usd']+g['loss_usd'],2)]
             for wd, g in sorted(wg.items())]

    # ----------------------------------------------------------------
    # Sheet 7 — Session
    # ----------------------------------------------------------------
    sg = defaultdict(lambda:{'w':0,'l':0,'win_pip':0.,'loss_pip':0.,'win_usd':0.,'loss_usd':0.})
    for t in trades:
        sess = get_sess(t.get('open_time',''))
        if t['result']=='WIN': sg[sess]['w']+=1; sg[sess]['win_pip']+=t['pnl']; sg[sess]['win_usd']+=t.get('pnl_usd',0)
        else: sg[sess]['l']+=1; sg[sess]['loss_pip']+=t['pnl']; sg[sess]['loss_usd']+=t.get('pnl_usd',0)
    SESS_ORDER = ['Asian','Asian+London','London','London+NewYork','New York','After NY','Other']
    hdr7 = [['Session','เวลา UTC','ชนะ','แพ้','WR%','Net pip','Net USD']]
    rows7 = [[s, SESS_TIME.get(s,'—'), g['w'], g['l'],
              round(g['w']/(g['w']+g['l'])*100 if g['w']+g['l'] else 0, 1),
              round(g['win_pip']+g['loss_pip'],1),
              round(g['win_usd']+g['loss_usd'],2)]
             for s in SESS_ORDER for g in [sg.get(s)] if g]

    # ----------------------------------------------------------------
    # Sheet 8 — เงื่อนไข
    # ----------------------------------------------------------------
    rows_cond = [
        ['เงื่อนไขการทดสอบ Backtest — ไม้รวย (Branch F)',''],
        ['Version', version],
        ['ไฟล์ CSV', csv_filename],
        ['Portfolio เริ่มต้น', f'{init_port:,.0f} USD'],
        ['',''],
        ['-- แท่งพ่อ --',''],
        ['จำนวนแท่งพ่อ', '1-5 แท่ง สีเดียวกัน'],
        ['ขนาดพ่อ (%R55)', '50-100% ของ Range55 (วัดเฉพาะเนื้อ)'],
        ['',''],
        ['-- แท่งแม่ --',''],
        ['ทิศทางแม่', 'สวนทิศพ่อ'],
        ['ขนาดแม่ (%R55)', '3-25% ของ R55'],
        ['สวยที่สุด', '5-20%'],
        ['ใช้ได้', '20-40%'],
        ['',''],
        ['-- Entry --',''],
        ['แม่ 5-10%', 'เข้า close แม่ทันที'],
        ['แม่ 10-30%', 'รอ midpoint แม่'],
        ['แม่ 30-40%', 'รอ tech_point (close_พ่อ ≈ open_แม่)'],
        ['Buffer', 'min(100pip, พ่อ×5%)'],
        ['',''],
        ['-- SL --',''],
        ['SL BUY',  'entry − ขนาดพ่อ (pip)'],
        ['SL SELL', 'entry + ขนาดพ่อ (pip)'],
        ['',''],
        ['-- TP --',''],
        ['TP1', '33% ของแท่งพ่อ (จากจุดเข้า)'],
        ['TP2', '50% ของแท่งพ่อ'],
        ['TP3', '80% ของแท่งพ่อ (default)'],
        ['',''],
        ['-- กฎเวลา --',''],
        ['ห้ามเปิดก่อนปิดตลาด', f'{NO_TRADE_MINS} นาที (วันศุกร์เท่านั้น)'],
        ['Gap เปิดตลาด > 20%R55', f'รอ {GAP_WAIT_BARS} แท่งก่อนเริ่มเทรด'],
        ['',''],
        ['-- Risk Management --',''],
        ['Lot', 'Portfolio × 10% ÷ SL (pip)'],
        ['แพ้ติดกัน 3 ไม้', 'หยุดเทรด'],
        ['',''],
        ['-- Anti-trend Filter --',''],
        ['เทรนขึ้น + SELL', 'ห้าม ❌'],
        ['เทรนลง + BUY', 'ห้าม ❌'],
        ['ไม่มีเทรน', 'เล่นได้ทั้งสองทิศ ✅'],
    ]

    # ----------------------------------------------------------------
    # Sheet 9 — Equity Curve
    # ----------------------------------------------------------------
    _eq = init_port
    rows_eq = [['#','วันที่เปิด','เวลา','ผล','Direction','pip','USD','Portfolio (USD)','กำไร% จากต้น']]
    rows_eq.append([0,'เริ่มต้น','','','','','',round(init_port,2),'0.00%'])
    for _i, t in enumerate(trades, 1):
        _eq += t.get('pnl_usd', 0)
        _pct = (_eq - init_port) / init_port * 100
        ot = str(t['open_time'])
        rows_eq.append([
            _i,
            ot[:10] if len(ot) >= 10 else ot,
            ot[11:16] if len(ot) >= 16 else '',
            t['result'],
            t['direction'],
            round(t['pnl'], 1),
            round(t.get('pnl_usd',0), 2),
            round(_eq, 2),
            f'{_pct:.2f}%',
        ])

    # ----------------------------------------------------------------
    # รวม sheet_defs
    # ----------------------------------------------------------------
    sheet_defs = [
        {'title':'รายการเทรด',    'data': hdr1 + rows1},
        {'title':'สรุปรวม',       'data': rows2},
        {'title':'แยก Pattern',   'data': hdr3 + rows3},
        {'title':'รายวัน',        'data': hdr4 + rows4},
        {'title':'รายเดือน',      'data': hdr5 + rows5},
        {'title':'รายวันสัปดาห์', 'data': hdr6 + rows6},
        {'title':'Session',       'data': hdr7 + rows7},
        {'title':'เงื่อนไข',      'data': rows_cond},
        {'title':'Equity Curve',  'data': rows_eq},
    ]

    # -- สร้าง Sheets ----------------------------------------------
    add_reqs = [{'addSheet':{'properties':{'title':s['title'],'index':i+1}}}
                for i, s in enumerate(sheet_defs[1:])]
    add_reqs.insert(0, {'updateSheetProperties':{
        'properties':{'sheetId':0,'title':sheet_defs[0]['title']},
        'fields':'title'}})
    sheets.spreadsheets().batchUpdate(spreadsheetId=sid,
        body={'requests':add_reqs}).execute()

    # -- ดึง sheetId -----------------------------------------------
    meta    = sheets.spreadsheets().get(spreadsheetId=sid).execute()
    sid_map = {s['properties']['title']:s['properties']['sheetId']
               for s in meta['sheets']}

    # -- เขียนข้อมูล -----------------------------------------------
    val_data = []
    for sd in sheet_defs:
        if not sd['data']: continue
        try:
            cleaned = []
            for row in sd['data']:
                cleaned.append([str(c) if not isinstance(c,(int,float,type(None))) else ('' if c is None else c) for c in row])
            val_data.append({
                'range': f"'{sd['title']}'!A1",
                'values': cleaned,
            })
        except Exception as e:
            print(f'⚠️  build data error [{sd["title"]}]: {e}')
    try:
        sheets.spreadsheets().values().batchUpdate(spreadsheetId=sid,
            body={'valueInputOption':'USER_ENTERED','data':val_data}).execute()
        print(f'✅ เขียนข้อมูลสำเร็จ {len(val_data)} sheets')
    except Exception as e:
        print(f'❌ เขียนข้อมูลล้มเหลว: {e}')

    # -- Formatting ------------------------------------------------
    fmt_reqs = []
    trade_shid = sid_map.get('รายการเทรด', 0)
    # ขยาย column width สำหรับ เงื่อนไขพ่อ (col 15)
    fmt_reqs.append({'updateDimensionProperties':{
        'range':{'sheetId':trade_shid,
                 'dimension':'COLUMNS',
                 'startIndex':16,'endIndex':17},
        'properties':{'pixelSize':250},
        'fields':'pixelSize'}})

    # Header row — ทุก Sheet
    for sd in sheet_defs:
        if not sd['data']: continue
        shid = sid_map.get(sd['title'], 0)
        fmt_reqs.append({'repeatCell':{
            'range':{'sheetId':shid,'startRowIndex':0,'endRowIndex':1},
            'cell':{'userEnteredFormat':{
                'backgroundColor':{'red':0.20,'green':0.47,'blue':0.75},
                'textFormat':{'bold':True,'foregroundColor':{'red':1,'green':1,'blue':1}},
                'horizontalAlignment':'CENTER',
                'verticalAlignment':'MIDDLE'}},
            'fields':'userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)'}})

    # Center all data rows
    for sd in sheet_defs:
        if not sd['data']: continue
        shid   = sid_map.get(sd['title'], 0)
        n_rows = len(sd['data'])
        fmt_reqs.append({'repeatCell':{
            'range':{'sheetId':shid,'startRowIndex':1,'endRowIndex':n_rows},
            'cell':{'userEnteredFormat':{
                'horizontalAlignment':'CENTER','verticalAlignment':'MIDDLE'}},
            'fields':'userEnteredFormat(horizontalAlignment,verticalAlignment)'}})

    # สีแถวตาม result
    for row_idx, t in enumerate(trades, start=1):
        tp_lv = t.get('tp_level_hit')
        if t['result'] == 'WIN' and tp_lv == 'TP3':
            bg = {'red':0.24,'green':0.52,'blue':0.95}   # 🔵 ฟ้า — WIN TP3
        elif t['result'] == 'WIN' and tp_lv == 'TP2':
            bg = {'red':0.18,'green':0.65,'blue':0.32}   # 🟢 เขียวเข้ม — WIN TP2
        elif t['result'] == 'WIN':
            bg = {'red':0.72,'green':0.93,'blue':0.72}   # 🟩 เขียวอ่อน — WIN TP1
        else:
            bg = {'red':0.95,'green':0.35,'blue':0.35}   # 🔴 แดง — SL
        fmt_reqs.append({'repeatCell':{
            'range':{'sheetId':trade_shid,'startRowIndex':row_idx,'endRowIndex':row_idx+1},
            'cell':{'userEnteredFormat':{'backgroundColor':bg}},
            'fields':'userEnteredFormat(backgroundColor)'}})

    # Highlight TP ที่เลือกใช้ด้วยสีเหลือง
    # สีเซลล์ TP และ SL ที่เลือกใช้
    _yellow = {'red':1.0, 'green':0.95, 'blue':0.2}   # TP
    _orange = {'red':1.0, 'green':0.75, 'blue':0.2}   # SL
    _black  = {'red':0.0, 'green':0.0, 'blue':0.0}
    _white  = {'red':1.0, 'green':1.0, 'blue':1.0}
    for row_idx, t in enumerate(trades, start=1):
        # ไม้รวยรอบ 2 → cell แรก พื้นดำตัวขาว
        if 'R2' in t.get('tp_name',''):
            fmt_reqs.append({'repeatCell':{
                'range':{'sheetId':trade_shid,
                         'startRowIndex':row_idx,'endRowIndex':row_idx+1,
                         'startColumnIndex':0,'endColumnIndex':1},
                'cell':{'userEnteredFormat':{
                    'backgroundColor':_black,
                    'textFormat':{'foregroundColor':_white,'bold':True}}},
                'fields':'userEnteredFormat(backgroundColor,textFormat)'}})
        # สีเซลล์ TP
        fmt_reqs.append({'repeatCell':{
            'range':{'sheetId':trade_shid,
                     'startRowIndex':row_idx,'endRowIndex':row_idx+1,
                     'startColumnIndex':TP_COL,'endColumnIndex':TP_COL+2},
            'cell':{'userEnteredFormat':{'backgroundColor':_yellow}},
            'fields':'userEnteredFormat(backgroundColor)'}})
        # สีเซลล์ SL
        fmt_reqs.append({'repeatCell':{
            'range':{'sheetId':trade_shid,
                     'startRowIndex':row_idx,'endRowIndex':row_idx+1,
                     'startColumnIndex':SL_COL,'endColumnIndex':SL_COL+2},
            'cell':{'userEnteredFormat':{'backgroundColor':_orange}},
            'fields':'userEnteredFormat(backgroundColor)'}})

    # Freeze row 1 ทุก Sheet
    for sd in sheet_defs:
        if not sd['data']: continue
        shid = sid_map.get(sd['title'], 0)
        fmt_reqs.append({'updateSheetProperties':{
            'properties':{'sheetId':shid,'gridProperties':{'frozenRowCount':1}},
            'fields':'gridProperties.frozenRowCount'}})

    if fmt_reqs:
        sheets.spreadsheets().batchUpdate(spreadsheetId=sid,
            body={'requests':fmt_reqs}).execute()

    # -- Equity Curve Chart ----------------------------------------
    eq_shid = sid_map.get('Equity Curve', 0)
    n_rows  = len(rows_eq)
    chart_reqs = [
        {'addChart':{'chart':{
            'spec':{
                'title': f'Equity Curve — Portfolio (USD)  [เริ่ม {init_port:,.0f} USD]',
                'basicChart':{
                    'chartType':'LINE',
                    'legendPosition':'BOTTOM_LEGEND',
                    'axis':[
                        {'position':'BOTTOM_AXIS','title':'ไม้ที่'},
                        {'position':'LEFT_AXIS', 'title':'Portfolio (USD)'},
                    ],
                    'domains':[{'domain':{'sourceRange':{'sources':[{
                        'sheetId':eq_shid,'startRowIndex':0,'endRowIndex':n_rows,
                        'startColumnIndex':0,'endColumnIndex':1}]}}}],
                    'series':[{'series':{'sourceRange':{'sources':[{
                        'sheetId':eq_shid,'startRowIndex':0,'endRowIndex':n_rows,
                        'startColumnIndex':7,'endColumnIndex':8}]}},
                        'targetAxis':'LEFT_AXIS'}],
                    'interpolateNulls':True,
                }
            },
            'position':{'overlayPosition':{
                'anchorCell':{'sheetId':eq_shid,'rowIndex':1,'columnIndex':10},
                'widthPixels':680,'heightPixels':360}},
        }}},
        {'addChart':{'chart':{
            'spec':{
                'title':'Equity Curve — กำไร% จากต้น',
                'basicChart':{
                    'chartType':'LINE',
                    'legendPosition':'BOTTOM_LEGEND',
                    'axis':[
                        {'position':'BOTTOM_AXIS','title':'ไม้ที่'},
                        {'position':'LEFT_AXIS','title':'%'},
                    ],
                    'domains':[{'domain':{'sourceRange':{'sources':[{
                        'sheetId':eq_shid,'startRowIndex':0,'endRowIndex':n_rows,
                        'startColumnIndex':0,'endColumnIndex':1}]}}}],
                    'series':[{'series':{'sourceRange':{'sources':[{
                        'sheetId':eq_shid,'startRowIndex':0,'endRowIndex':n_rows,
                        'startColumnIndex':8,'endColumnIndex':9}]}},
                        'targetAxis':'LEFT_AXIS'}],
                    'interpolateNulls':True,
                }
            },
            'position':{'overlayPosition':{
                'anchorCell':{'sheetId':eq_shid,'rowIndex':22,'columnIndex':10},
                'widthPixels':680,'heightPixels':360}},
        }}},
    ]
    sheets.spreadsheets().batchUpdate(spreadsheetId=sid,
        body={'requests':chart_reqs}).execute()

    print(f"\n✅ Export สำเร็จ — {sheet_title}")
    print(f"   🔗 {url}")
    return url

print("✅ Cell 1: Engine โหลดสำเร็จ")
print(f"   MaiRuaySignal, analyze_bar, run_backtest, export_to_gsheet พร้อมใช้งาน")
