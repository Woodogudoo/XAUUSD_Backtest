# ============================================================================
# bt/strategies/mai_ruay.py
# ----------------------------------------------------------------------------
# ไม้รวย — port จาก reference/mairuay_v204_reference.py
# threshold ทั้งหมดอ่านจาก config (configs/mairuay_v204.yaml) ไม่ hardcode
#
# Step 5.1 = signal core: find_father / find_father_r2 / validate_mother /
#            calc_tp(levels) / analyze_bar  (verify ตรง reference.analyze_bar)
# Step 5.2 = orchestration ใน on_bar (one-plan, R:R adjust, proximity, expiry)
# Step 5.3 = ไม้รวยรอบ 2
#
# หมายเหตุ port:
#  - SL ใช้ inline 40%พ่อ (ref:488) · ไม่ลอก calc_sl 70% (ref:300) ที่เป็น dead code
#  - ข้าม _swing_* helpers (ref:324-348) ที่ไม่ถูกเรียกใช้
# ============================================================================
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from bt.contract import Bar, Decision, Order
from bt.data import PIP, calc_r55
from bt.strategies.base import Strategy


def _r(x, n):
    """round แบบเดียวกับ v2.04 — reference ใช้ round() กับค่า np.float64 (จาก pandas)
    จึงได้ numpy rounding (half-to-even หลัง scale) ต่างจาก Python round ที่ขอบ .xx5
    """
    return float(np.round(x, n))


@dataclass
class Signal:
    bar_index:        int
    direction:        str           # 'BUY' | 'SELL'
    father_start:     int
    father_end:       int
    father_bars:      int
    father_body_pips: float
    father_pct_r55:   float
    father_first_pct: float
    vol_ratio:        float
    mother_idx:       int
    mother_body_pips: float
    mother_pct_r55:   float
    mother_quality:   str
    tech_point:       float
    buffer_pips:      float
    entry:            float
    sl:               float
    sl_pips:          float
    tp1:              float
    tp2:              float
    tp3:              float
    tp_selected:      float
    tp_pips:          float
    range55:          float
    rr:               float
    lot:              float
    is_round2:        bool
    entries:          list          # list[(price, label, lot, is_market)]
    reasons:          list          # analytics ล้วน — เหตุผลไทยจาก branch ที่เดินจริง


# ---------- bar helpers ----------
def _body_pips(b: Bar) -> float: return abs(b.open - b.close) / PIP
def _is_bull(b: Bar) -> bool:    return b.close > b.open
def _is_bear(b: Bar) -> bool:    return b.close < b.open


# ---------- แท่งพ่อ รอบ 1 (ref: find_father) ----------
def _find_father(bars, father_end, r55, fc):
    n = len(bars)
    if father_end < 0 or father_end >= n:
        return None
    eb = bars[father_end]
    if   _is_bull(eb): run_dir = 'UP'
    elif _is_bear(eb): run_dir = 'DOWN'
    else: return None

    if father_end + 1 < n:
        nb = bars[father_end + 1]
        if (run_dir == 'UP' and not _is_bear(nb)) or \
           (run_dir == 'DOWN' and not _is_bull(nb)):
            return None

    doji = fc['doji_pct_r55'] / 100
    run_start = father_end
    for k in range(father_end - 1, max(-1, father_end - fc['max_scan_bars']), -1):
        b = bars[k]
        same = (run_dir == 'UP' and _is_bull(b)) or (run_dir == 'DOWN' and _is_bear(b))
        if same and _body_pips(b) > r55 * doji:
            run_start = k
        else:
            break

    length = father_end - run_start + 1
    if length < 1:
        return None
    seg = bars[run_start:father_end + 1]
    tb  = abs(seg[0].open - seg[-1].close) / PIP
    pct = tb / r55 * 100 if r55 > 0 else 0
    if pct <= fc['min_total_pct_r55']:
        return None

    big     = fc['big_bar_pct_r55'] / 100
    per_bar = fc['per_bar_min_pct_r55'] / 100
    _big_count = sum(1 for idx in range(length) if _body_pips(seg[idx]) > r55 * big)
    _big_majority = _big_count > length / 2
    _fail_count = 0
    for idx in range(length):
        _bp = _body_pips(seg[idx])
        if _bp <= r55 * per_bar:
            if _big_majority and _bp > r55 * doji:
                _fail_count += 1
            else:
                return None
    if _fail_count > fc['leniency_max_bars']:
        return None

    _f_avg_body = tb / length
    pre20 = bars[max(0, run_start - fc['vol_pre_bars']):run_start]
    if len(pre20) > 0:
        _pre20_avg = sum(_body_pips(b) for b in pre20) / len(pre20)
        _vol_ratio = _f_avg_body / _pre20_avg if _pre20_avg > 0 else 0
    else:
        _vol_ratio = 0
    if _vol_ratio <= fc['vol_ratio_min']:
        return None

    return (run_start, father_end, run_dir, tb, 'Pass1 สีเดียว 1-10แท่ง >60%R55')


# ---------- แท่งพ่อ รอบ 2 (ref: find_father_r2) ----------
def _find_father_r2(bars, father_end, r55, rc):
    n = len(bars)
    if father_end < 0 or father_end >= n:
        return None
    eb = bars[father_end]
    if   _is_bull(eb): run_dir = 'UP'
    elif _is_bear(eb): run_dir = 'DOWN'
    else: return None
    if father_end + 1 < n:
        nb = bars[father_end + 1]
        if (run_dir == 'UP' and not _is_bear(nb)) or \
           (run_dir == 'DOWN' and not _is_bull(nb)):
            return None

    run_start = father_end
    opp_count = 0
    opp_bar_k = -1
    for k in range(father_end - 1, max(-1, father_end - rc['max_scan_bars']), -1):
        b = bars[k]
        same = (run_dir == 'UP' and _is_bull(b)) or (run_dir == 'DOWN' and _is_bear(b))
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
    if length < 1:
        return None
    seg = bars[run_start:father_end + 1]
    tb  = abs(seg[0].open - seg[-1].close) / PIP
    pct = tb / r55 * 100 if r55 > 0 else 0
    if pct <= rc['min_total_pct_r55']:
        return None

    if opp_count == 1:
        if _body_pips(bars[opp_bar_k]) > r55 * (rc['insertion_max_pct_r55'] / 100):
            return None
        if opp_bar_k + 1 <= father_end:
            _next = bars[opp_bar_k + 1]
            _opp  = bars[opp_bar_k]
            if run_dir == 'UP' and _next.close <= _opp.open:
                return None
            if run_dir == 'DOWN' and _next.close >= _opp.open:
                return None

    _fail6   = 0
    per_bar  = rc['per_bar_min_pct_r55'] / 100
    last_bar = rc['last_bar_min_pct_r55'] / 100
    for _bi in range(run_start, father_end + 1):
        if opp_count == 1 and _bi == opp_bar_k:
            continue
        _min_thresh = r55 * last_bar if _bi == father_end else r55 * per_bar
        if _body_pips(bars[_bi]) <= _min_thresh:
            _fail6 += 1
    if _fail6 > rc['leniency_max_bars']:
        return None
    return (run_start, father_end, run_dir, tb, 'R2 ไม้รวยรอบ2')


# ---------- แท่งแม่ (ref: validate_mother) ----------
def _validate_mother(bars, mother_idx, f_dir, r55, min_pct, mc):
    if mother_idx >= len(bars):
        return None
    m = bars[mother_idx]
    m_body = _body_pips(m)
    if f_dir == 'UP'   and not _is_bear(m):
        return None
    if f_dir == 'DOWN' and not _is_bull(m):
        return None
    pct = m_body / r55 * 100 if r55 > 0 else 0
    if pct < min_pct or pct > mc['max_pct_r55']:
        return None
    quality = (
        f"สวยที่สุด ({pct:.1f}%) ✅✅" if pct <= mc['quality_pct_r55'] else
        f"ใช้ได้     ({pct:.1f}%) ✅"
    )
    return (m_body, pct, quality)


# ---------- TP levels (ref: calc_tp) ----------
def _calc_tp_levels(tech_point, f_body, direction, tl):
    sign = 1 if direction == 'BUY' else -1
    f = f_body * PIP
    return (tech_point + sign * (f * tl['tp1_pct'] / 100),
            tech_point + sign * (f * tl['tp2_pct'] / 100),
            tech_point + sign * (f * tl['tp3_pct'] / 100))


def _calc_lot(portfolio, sl_pips, risk_pct):
    if sl_pips <= 0:
        return 0.0
    return _r((portfolio * risk_pct) / sl_pips, 2)


# ---------- core analyzer (ref: analyze_bar) ----------
def analyze_bar(bars, bar_idx, cfg, portfolio, round2_info=None) -> Signal | None:
    rw = cfg['range_window']
    if bar_idx < rw - 1:
        return None
    mother_idx = bar_idx - 1
    father_end = bar_idx - 2
    if father_end < 0:
        return None

    r55 = calc_r55(bars, bar_idx - 1)          # R55 ณ จบแท่งแม่
    if r55 <= 0:
        return None

    if round2_info:
        fr = _find_father_r2(bars, father_end, r55, cfg['father_r2'])
    else:
        fr = _find_father(bars, father_end, r55, cfg['father'])
    if fr is None:
        return None
    f_start, f_end, f_dir, f_body, _f_pass = fr
    reasons = [f"พ่อ: {_f_pass}"]   # analytics — เก็บเหตุผลจาก branch ที่เดินจริง

    f_first = bars[f_start]
    _f_first_pct = abs(f_first.open - f_first.close) / PIP / r55 * 100 if r55 > 0 else 0
    _f_bar_count = f_end - f_start + 1
    _f_avg_body  = f_body / _f_bar_count if _f_bar_count > 0 else 0
    pre20 = bars[max(0, f_start - cfg['father']['vol_pre_bars']):f_start]
    if len(pre20) > 0:
        _pre20_avg = sum(_body_pips(b) for b in pre20) / len(pre20)
        _vol_ratio = _r(_f_avg_body / _pre20_avg, 2) if _pre20_avg > 0 else 0
    else:
        _vol_ratio = 0

    if round2_info and (f_start - round2_info['entry_bar']) > cfg['round2']['father_start_max_bars']:
        return None

    min_m = cfg['mother']['min_pct_r55']
    mr = _validate_mother(bars, mother_idx, f_dir, r55, min_m, cfg['mother'])
    if mr is None:
        return None
    m_body, m_pct, m_quality = mr
    reasons.append(f"แม่ {m_pct:.1f}% ({min_m:.0f}–{cfg['mother']['max_pct_r55']}%)"
                   f" → ผ่านเงื่อนไขแม่ ({m_quality.split('(')[0].strip()})")

    mai_dir      = 'BUY' if f_dir == 'DOWN' else 'SELL'
    m_row        = bars[mother_idx]
    father_close = bars[f_end].close
    mother_open  = m_row.open
    tech_point   = (father_close + mother_open) / 2
    buffer_pips  = min(cfg['buffer']['max_pip'], f_body * (cfg['buffer']['pct_father'] / 100))
    mid_mother   = (mother_open + m_row.close) / 2
    child        = bars[bar_idx]

    e3_off = f_body * (cfg['entry']['e3_offset_pct_father'] / 100) * PIP
    _e_tech = tech_point
    _e_far  = tech_point - e3_off if mai_dir == 'BUY' else tech_point + e3_off

    mk_max = cfg['mother']['market_entry_max_pct']
    if min_m <= m_pct < mk_max:
        _e1, _e1_label, _e1_mkt = child.open, 'จุด1:market(open_child)', True
        reasons.append(f"แม่ {m_pct:.1f}% (<{mk_max}%) → จุด1 = market (open ลูก)")
    else:
        _e1, _e1_label, _e1_mkt = mid_mother, 'จุด1:mid_แม่', False
        reasons.append(f"แม่ {m_pct:.1f}% (≥{mk_max}%) → จุด1 = mid แม่")
    entries = [
        (_e1,    _e1_label,        _e1_mkt),
        (_e_tech, 'จุด2:tech_point', False),
        (_e_far,  'จุด3:tech±10%พ่อ', False),
    ]
    entry = _e1

    tp1, tp2, tp3 = _calc_tp_levels(tech_point, f_body, mai_dir, cfg['tp_levels'])

    sl_off = f_body * (cfg['tp_sl']['sl_pct_father'] / 100) * PIP
    sl = tech_point - sl_off if mai_dir == 'BUY' else tech_point + sl_off
    sl_pips = abs(entry - sl) / PIP
    if sl_pips < cfg['tp_sl']['min_sl_pips']:
        return None

    tp_off = f_body * (cfg['tp_sl']['tp_pct_father'] / 100) * PIP
    tp_sel = tech_point + tp_off if mai_dir == 'BUY' else tech_point - tp_off

    # ── Round 2: TP/SL จากพ่อรวม (R1+R2) ──
    if round2_info:
        reasons.append("ไม้รวยรอบ 2 → TP/SL จากพ่อรวม (R1+R2)")
        _combined = abs(round2_info['f_open_r1'] - bars[f_end].close) / PIP
        r2tp = cfg['round2_tp_sl']['tp_pct_combined'] / 100
        r2sl = cfg['round2_tp_sl']['sl_pct_combined'] / 100
        tp_sel = tech_point + _combined * r2tp * PIP if mai_dir == 'BUY' \
                 else tech_point - _combined * r2tp * PIP
        sl     = tech_point - _combined * r2sl * PIP if mai_dir == 'BUY' \
                 else tech_point + _combined * r2sl * PIP
        sl_pips = abs(entry - sl) / PIP

    # ── TP30 variant (50 แท่งก่อนพ่อ) — ไม่ใช้กับรอบ 2 ──
    if not round2_info:
        t30 = cfg['tp30']
        pre50 = bars[max(0, f_start - t30['pre_bars']):f_start]
        r55_window = bars[max(0, bar_idx - rw):bar_idx]
        _low55  = min(b.low for b in r55_window) if r55_window else 0
        _level60 = _low55 + r55 * (t30['level_buy_pct_r55'] / 100) * PIP
        _level40 = _low55 + r55 * (t30['level_sell_pct_r55'] / 100) * PIP
        _trig = False
        if mai_dir == 'BUY' and len(pre50) > 0:
            if min(b.low for b in pre50) >= _level60:
                _trig = True
        elif mai_dir == 'SELL' and len(pre50) > 0:
            if max(b.high for b in pre50) <= _level40:
                _trig = True
        if _trig:
            reasons.append(f"TP30: 50 แท่งก่อนพ่อสะอาด → TP {t30['tp_pct_father']}%พ่อ"
                           f" / SL {t30['sl_pct_father']}%พ่อ")
            tp30p = t30['tp_pct_father'] / 100
            sl30p = t30['sl_pct_father'] / 100
            tp_sel = tech_point + f_body * tp30p * PIP if mai_dir == 'BUY' \
                     else tech_point - f_body * tp30p * PIP
            sl     = tech_point - f_body * sl30p * PIP if mai_dir == 'BUY' \
                     else tech_point + f_body * sl30p * PIP
            sl_pips = abs(entry - sl) / PIP

    tp_pips = abs(tp_sel - entry) / PIP
    if tp_pips < cfg['tp_min_pip']:
        return None
    rr = tp_pips / sl_pips if sl_pips > 0 else 0.0

    lc  = cfg['lot']
    lot = _calc_lot(portfolio, sl_pips, lc['risk_pct'] / 100)
    if lc.get('mother_halve_enabled', True) \
            and lc['mother_halve_min_pct'] <= m_pct <= lc['mother_halve_max_pct']:
        lot = _r(lot / 2, 2)
        reasons.append(f"แม่ {m_pct:.1f}% ({lc['mother_halve_min_pct']}–"
                       f"{lc['mother_halve_max_pct']}%) → lot ÷2")
    _f_pct = f_body / r55 * 100
    if lc.get('father_double_enabled', True) and _f_pct > lc['father_double_pct_r55']:
        lot = _r(lot * 2, 2)
        reasons.append(f"พ่อ {_f_pct:.1f}% (>{lc['father_double_pct_r55']}%R55) → lot ×2")

    _n = len(entries)
    _lot_each = _r(lot / _n, 2) if _n > 0 else lot
    entries_full = [(ep, er, _lot_each, mkt) for ep, er, mkt in entries]

    return Signal(
        bar_index=bar_idx, direction=mai_dir,
        father_start=f_start, father_end=f_end, father_bars=_f_bar_count,
        father_body_pips=f_body, father_pct_r55=f_body / r55 * 100,
        father_first_pct=_r(_f_first_pct, 1), vol_ratio=_vol_ratio,
        mother_idx=mother_idx, mother_body_pips=m_body, mother_pct_r55=m_pct,
        mother_quality=m_quality,
        tech_point=tech_point, buffer_pips=buffer_pips,
        entry=entry, sl=sl, sl_pips=sl_pips,
        tp1=tp1, tp2=tp2, tp3=tp3, tp_selected=tp_sel, tp_pips=tp_pips,
        range55=r55, rr=rr, lot=lot, is_round2=bool(round2_info),
        entries=entries_full, reasons=reasons,
    )


class MaiRuay(Strategy):
    name = "mai_ruay"

    def __init__(self, cfg: dict, portfolio: float = 1000.0,
                 pre_fill_cancel: bool = False):
        self.cfg = cfg
        self.portfolio = portfolio
        # pre_fill_cancel=True (ใช้กับ engine inject_compat): engine ยกเลิก "ก่อน" fill
        # → ขอบ expiry เป็น i-placed > expiry (ให้ fillable p+1..p+5 ตรง v2.04)
        # False (default/correct): engine fill ก่อน strategy ยกเลิก → i-placed >= expiry
        self.pre_fill_cancel = pre_fill_cancel
        self._pend = {}        # tag -> {tp_sel, r55, dir, placed, plan_id, price, place_time}
        self._r1 = None        # ข้อมูลแผนที่ปิด SL ล่าสุด → ลองรอบ 2 (None ถ้า TP/ยังไม่มี)
        self._last_plan = None # {tech, f_open_r1, entry_bar, direction} ของแผนที่กำลังถือ
        # --- analytics ล้วน (ไม่กระทบ Decision/ผลเทรด) ---
        self._plan_id = 0      # mirror engine next_plan_id (เพิ่มต่อ 1 batch place ที่ไม่ว่าง)
        self.unfilled = []     # ไม้ที่ตายแบบไม่ fill (proximity/expiry) — report/viewer อ่าน
        self._placements = []  # {plan_id, bar, tags} ทุก batch — สำหรับ sanity check plan_id
        self.plan_meta = {}    # plan_id -> {พ่อ/แม่/%/reasons} — report เขียน plan_meta.csv

    def on_bar(self, ctx) -> Decision:
        cfg = self.cfg
        place, cancel, modify = [], [], []
        i, bar = ctx.bar_index, ctx.bar
        expiry = cfg['pending_expiry_bars']
        prox   = cfg['proximity_cancel_pct_r55'] / 100
        prox_on = cfg.get('proximity_cancel_enabled', True)   # toggle กฎ proximity (default เปิด)

        # (0) อัปเดต state รอบ 2 จากไม้ที่เพิ่งปิด — SL ของแผนล่าสุด → จำไว้ลองรอบ 2 · TP → ล้าง
        #     (ตัวสุดท้ายใน last_closed ชนะ = ตรง v2.04 ที่ set _r1_info ราย position)
        for c in ctx.last_closed:
            if self._last_plan is not None and c.result == 'SL':
                self._r1 = dict(self._last_plan)
            elif c.result == 'TP':
                self._r1 = None

        pend_tags = {o.tag for o in ctx.pendings}
        # prune state → เก็บเฉพาะ tag ที่ยัง pending จริง (filled/cancelled หลุดไป)
        self._pend = {t: m for t, m in self._pend.items() if t in pend_tags}

        # (A) proximity-cancel + expiry (ทำเองทุกแท่ง — engine ไม่หมดอายุ/cancel ให้)
        for o in ctx.pendings:
            m = self._pend.get(o.tag)
            if m is None:
                continue
            age = i - m['placed']
            if (age > expiry) if self.pre_fill_cancel else (age >= expiry):  # หมดอายุ
                cancel.append(o.tag); self._log_unfilled(o.tag, m, i, bar, 'expiry'); continue
            if not prox_on:                                # toggle ปิดกฎ proximity ของ strategy
                continue
            buf = m['r55'] * prox * PIP                    # เฉียด TP ≤10%R55
            if m['dir'] == 'BUY'  and bar.high >= m['tp_sel'] - buf:
                cancel.append(o.tag); self._log_unfilled(o.tag, m, i, bar, 'proximity'); continue
            if m['dir'] == 'SELL' and bar.low  <= m['tp_sel'] + buf:
                cancel.append(o.tag); self._log_unfilled(o.tag, m, i, bar, 'proximity'); continue

        remaining = pend_tags - set(cancel)

        # (B) one-plan guard — มี position / pending(เหลือ) / เพิ่งปิดแท่งนี้ → ไม่เปิดใหม่
        #     pre_fill_cancel (compat): on_bar รันก่อน exit → ใช้ positions แทน just_closed
        #     (กัน over-block) · correct: ใช้ last_closed (just_closed) ตามปกติ
        just_closed = [] if self.pre_fill_cancel else ctx.last_closed
        if ctx.positions or remaining or just_closed:
            return Decision(place, cancel, modify)
        # (C) เคารพ allow_new_entry (Friday/Gap fact จาก engine)
        if not ctx.allow_new_entry:
            return Decision(place, cancel, modify)

        # (D) หาสัญญาณ — รอบ 1 ก่อน · ไม่ได้ + มีแผน SL ล่าสุดใน 16 แท่ง → ลองรอบ 2 (ทิศเดียวกัน)
        #     toggle round2.enabled=false → ข้าม logic รอบ 2 ทั้งหมด (default true = เดิม)
        sig = analyze_bar(ctx.window, i, cfg, self.portfolio)
        if sig is None and self._r1 is not None \
                and cfg['round2'].get('enabled', True) \
                and (i - self._r1['entry_bar']) <= cfg['round2']['outer_bars']:
            r2 = analyze_bar(ctx.window, i, cfg, self.portfolio, round2_info=self._r1)
            if r2 is not None and r2.direction == self._r1['direction']:
                sig = r2
                self._r1 = None        # reset หลังเข้ารอบ 2 (ตรง v2.04)
        if sig is None:
            return Decision(place, cancel, modify)

        # จำข้อมูลแผนนี้ไว้ (เผื่อปิด SL → ใช้เป็น round2_info ของรอบถัดไป)
        self._last_plan = {
            'tech': sig.tech_point,
            'f_open_r1': ctx.window[sig.father_start].open,
            'entry_bar': i,
            'direction': sig.direction,
        }

        # mirror engine plan_id: เพิ่มต่อ 1 batch place ที่ไม่ว่าง (sig.entries ไม่ว่างเสมอ)
        self._plan_id += 1
        self._placements.append({'plan_id': self._plan_id, 'bar': i,
                                 'tags': [e[1] for e in sig.entries]})

        # analytics ล้วน — บันทึก พ่อ/แม่/%/เหตุผล (จาก branch ที่ analyze_bar เดินจริง)
        self.plan_meta[self._plan_id] = {
            'plan_id':          self._plan_id,
            'father_start_bar': sig.father_start,
            'father_end_bar':   sig.father_end,
            'father_pct':       _r(sig.father_pct_r55, 1),
            'mother_bar':       sig.mother_idx,
            'mother_pct':       _r(sig.mother_pct_r55, 1),
            'reasons':          list(sig.reasons),
        }

        # (E) สร้าง Order ต่อไม้ + R:R=1.0 adjust (เข้าเสียเปรียบ → TP = entry ± SL_dist)
        _rr_adjusted = False
        for ep, label, lot, is_mkt in sig.entries:
            sl_dist      = abs(ep - sig.sl)
            base_tp_dist = abs(sig.tp_selected - ep)
            if base_tp_dist >= sl_dist:
                tp = sig.tp_selected
            else:
                tp = ep + sl_dist if sig.direction == 'BUY' else ep - sl_dist
                _rr_adjusted = True
            kind = "MARKET" if is_mkt else "LIMIT"
            place.append(Order(kind=kind, price=ep, lot=lot, sl=sig.sl, tp=tp, tag=label))
            if not is_mkt:
                # proximity-cancel ใช้ tp ของไม้ "หลัง R:R adjust" (ตรง v2.04: _pl_sig.tp_selected)
                self._pend[label] = {'tp_sel': tp, 'sl': sig.sl, 'r55': sig.range55,
                                     'dir': sig.direction, 'placed': i,
                                     'plan_id': self._plan_id, 'price': ep,
                                     'place_time': str(ctx.bar.time)}
        if _rr_adjusted:
            self.plan_meta[self._plan_id]['reasons'].append(
                "R:R<1 → ปรับ TP = entry ± SL_dist (บางไม้)")
        return Decision(place, cancel, modify)

    def _log_unfilled(self, tag, m, death_bar, bar, reason):
        """บันทึกไม้ที่ตายแบบไม่ fill (analytics) — ไม่กระทบ Decision"""
        self.unfilled.append({
            'plan_id': m.get('plan_id'), 'tag': tag, 'direction': m['dir'],
            'kind': 'LIMIT', 'limit_price': m.get('price'),
            'sl': m.get('sl'), 'tp': m.get('tp_sel'),
            'place_time': m.get('place_time'), 'place_bar': m['placed'],
            'death_time': str(bar.time), 'death_bar': death_bar, 'reason': reason,
        })
