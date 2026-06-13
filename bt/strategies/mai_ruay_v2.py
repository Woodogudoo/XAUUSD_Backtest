# ============================================================================
# bt/strategies/mai_ruay_v2.py
# ----------------------------------------------------------------------------
# ไม้รวย v2 — "ฉบับสะอาด": เก็บเฉพาะแกนหลัก ตัดเงื่อนไขเสริมทั้งหมด
# สเปค: docs/MAIRUAY_V2_SPEC.md (source of truth) · id: mai_ruay_v2
#
# ใช้ contract / Signal / engine เดิม (ไฟล์นี้เป็นแค่ strategy layer ใหม่)
# *** ห้ามแตะ mai_ruay.py / engine / contract — V1 ต้องนิ่งเป๊ะ ***
#
# ตัดออกจาก V1 (ไม่พอร์ตมา): ไม้รวยรอบ 2 · เพิ่ม/หาร lot · TP/SL ย่อย · buffer · R:R adjust
#
# โครงตรรกะ (เหมือน V1 แต่ล้วน):
#   father_end = bar-2 · mother = bar-1 · child(เข้า) = bar
#   พ่อ = แท่งสีเดียวต่อเนื่องจบที่ father_end · แม่ = แท่งย่อสีตรงข้าม 1 แท่ง
#   tech_point = (พ่อปิด + แม่เปิด)/2 · TP/SL = fixed % ของขนาดพ่อ จาก tech_point
#   entries = list ของ tier → เลือก tier ตาม when{พ่อ%, แม่%} → เปิดไม้ตาม legs
# ============================================================================
from __future__ import annotations

from bt.contract import Bar, Decision, Order
from bt.data import PIP
from bt.strategies.base import Strategy
from bt.strategies.mai_ruay import Signal   # ใช้ Signal เดิม (contract ภายใน strategy)

# ค่าคงที่ภายใน (ไม่อยู่ใน config — V2 ตัด lot scaling/buffer ออก)
_RISK_PCT          = 10.0    # % พอร์ตต่อ 1 แผน (lot รวม = พอร์ต×risk÷SL_pips แล้วหารตามจำนวนไม้)
_PROXIMITY_PCT_R55 = 10.0    # ราคาเฉียด TP ≤ ค่านี้ (%R55) → ยก pending (เปิด/ปิดด้วย toggle ใน config)


def _r(x, n):
    return round(float(x), n)


# ---------- R55 (window ปรับได้ตาม general.r55_bars — calc_r55 ใน data.py fix 55) ----------
def _calc_r55(bars, end_idx: int, n: int) -> float:
    start = max(0, end_idx - n + 1)
    window = bars[start:end_idx + 1]
    if not window:
        return 0.0
    hi = max(b.high for b in window)
    lo = min(b.low for b in window)
    return (hi - lo) / PIP


# ---------- bar helpers ----------
def _body_pips(b: Bar) -> float: return abs(b.open - b.close) / PIP
def _is_bull(b: Bar) -> bool:    return b.close > b.open
def _is_bear(b: Bar) -> bool:    return b.close < b.open


# ---------- แท่งพ่อ (นับแท่ง + ขนาด + vol) ----------
def _find_father(bars, father_end, r55, fc):
    """หาแท่งพ่อ = run สีเดียวต่อเนื่องจบที่ father_end · คืน (start, end, dir, body_pips) หรือ None"""
    n = len(bars)
    if father_end < 0 or father_end >= n:
        return None
    eb = bars[father_end]
    if   _is_bull(eb): run_dir = 'UP'
    elif _is_bear(eb): run_dir = 'DOWN'
    else: return None

    # ถัดจากพ่อต้องเป็นแท่งย่อสีตรงข้าม (= ตำแหน่งแม่)
    if father_end + 1 < n:
        nb = bars[father_end + 1]
        if (run_dir == 'UP' and not _is_bear(nb)) or \
           (run_dir == 'DOWN' and not _is_bull(nb)):
            return None

    doji    = fc['doji_stop_pct'] / 100
    per_bar = fc['per_bar_body_min'] / 100
    big     = fc['big_bar_pct'] / 100

    # นับย้อนหลังขณะสีเดียว + เนื้อ > doji · สูงสุด count_max แท่ง
    run_start = father_end
    for k in range(father_end - 1, max(-1, father_end - fc['count_max']), -1):
        b = bars[k]
        same = (run_dir == 'UP' and _is_bull(b)) or (run_dir == 'DOWN' and _is_bear(b))
        if same and _body_pips(b) > r55 * doji:
            run_start = k
        else:
            break

    # ตัดแท่งเล็ก (เนื้อ ≤ per_bar) ที่หัวขบวน ได้สูงสุด head_trim_small_max แท่ง
    trimmed = 0
    while run_start < father_end and trimmed < fc['head_trim_small_max'] \
            and _body_pips(bars[run_start]) <= r55 * per_bar:
        run_start += 1
        trimmed += 1

    length = father_end - run_start + 1
    if length < fc['count_min']:
        return None

    seg = bars[run_start:father_end + 1]
    tb  = abs(seg[0].open - seg[-1].close) / PIP
    pct = tb / r55 * 100 if r55 > 0 else 0
    if pct < fc['body_min_pct_r55']:               # ขนาดพ่อรวมขั้นต่ำ (%R55)
        return None

    # เนื้อต่อแท่ง: อนุโลมแท่งเล็กได้เมื่อ "แท่งใหญ่เป็นส่วนมาก" และไม่เป็น doji · ≤ small_bar_allow แท่ง
    big_count = sum(1 for b in seg if _body_pips(b) > r55 * big)
    big_majority = big_count > length / 2
    fail = 0
    for b in seg:
        bp = _body_pips(b)
        if bp <= r55 * per_bar:
            if big_majority and bp > r55 * doji:
                fail += 1
            else:
                return None
    if fail > fc['small_bar_allow']:
        return None

    # Vol ratio: เนื้อเฉลี่ยพ่อ ÷ เนื้อเฉลี่ย vol_window แท่งก่อนหน้า ต้อง ≥ vol_ratio_min
    avg_body = tb / length
    pre = bars[max(0, run_start - fc['vol_window']):run_start]
    vol_ratio = 0.0
    if pre:
        pre_avg = sum(_body_pips(b) for b in pre) / len(pre)
        vol_ratio = avg_body / pre_avg if pre_avg > 0 else 0.0
    if vol_ratio < fc['vol_ratio_min']:
        return None

    return (run_start, father_end, run_dir, tb, _r(vol_ratio, 2))


# ---------- แท่งแม่ (ย่อตัวสีตรงข้าม · เทียบ %R55 หรือ %พ่อ ตาม compare_mode) ----------
def _validate_mother(bars, mother_idx, f_dir, r55, f_body, mc):
    """คืน (m_body_pips, m_pct) หรือ None · m_pct เทียบตาม compare_mode (r55|father)"""
    if mother_idx >= len(bars):
        return None
    m = bars[mother_idx]
    if f_dir == 'UP'   and not _is_bear(m):
        return None
    if f_dir == 'DOWN' and not _is_bull(m):
        return None
    m_body = _body_pips(m)
    base = f_body if mc.get('compare_mode') == 'father' else r55
    pct = m_body / base * 100 if base > 0 else 0
    if pct < mc['body_min_pct'] or pct > mc['body_max_pct']:
        return None
    return (m_body, pct)


# ---------- เลือก tier จาก entries ตามเงื่อนไข when ----------
def _select_tier(entries, f_pct, m_pct):
    """tier แรกที่ match (พ่อ ≥ father_min_pct และ mother_min ≤ แม่ ≤ mother_max) — ลำดับใน list = ลำดับความสำคัญ"""
    for tier in entries:
        w = tier.get('when', {})
        if f_pct >= w.get('father_min_pct', 0) \
                and w.get('mother_min_pct', 0) <= m_pct <= w.get('mother_max_pct', 1e9):
            return tier
    return None


# ---------- core analyzer ----------
def analyze_bar(bars, bar_idx, cfg, portfolio) -> Signal | None:
    g  = cfg['general']
    rw = g['r55_bars']
    if bar_idx < rw - 1:
        return None
    mother_idx = bar_idx - 1
    father_end = bar_idx - 2
    if father_end < 0:
        return None

    r55 = _calc_r55(bars, bar_idx - 1, rw)          # R55 ณ จบแท่งแม่
    if r55 <= 0:
        return None

    fr = _find_father(bars, father_end, r55, cfg['father'])
    if fr is None:
        return None
    f_start, f_end, f_dir, f_body, vol_ratio = fr
    f_pct = f_body / r55 * 100 if r55 > 0 else 0

    mc = cfg['mother']
    mr = _validate_mother(bars, mother_idx, f_dir, r55, f_body, mc)
    if mr is None:
        return None
    m_body, m_pct = mr

    tier = _select_tier(cfg['entries'], f_pct, m_pct)
    if tier is None:
        return None

    mai_dir      = 'BUY' if f_dir == 'DOWN' else 'SELL'
    father_close = bars[f_end].close
    m_row        = bars[mother_idx]
    mother_open  = m_row.open
    tech_point   = (father_close + mother_open) / 2
    half_mother  = (mother_open + m_row.close) / 2
    child        = bars[bar_idx]

    # TP/SL = fixed % ของขนาดพ่อ จาก tech_point (ยังไม่มีเงื่อนไข)
    tp_off = f_body * (cfg['tpsl']['tp_pct_father'] / 100) * PIP
    sl_off = f_body * (cfg['tpsl']['sl_pct_father'] / 100) * PIP
    sl     = tech_point - sl_off if mai_dir == 'BUY' else tech_point + sl_off
    tp_sel = tech_point + tp_off if mai_dir == 'BUY' else tech_point - tp_off

    tp_pips = abs(tp_sel - tech_point) / PIP
    if tp_pips < g['min_tp_pip']:
        return None

    # สร้างไม้ตาม legs ของ tier (point ∈ market | half_mother | tech)
    mk_thr = mc['market_entry_threshold']
    point_price = {'market': child.open, 'half_mother': half_mother, 'tech': tech_point}
    entries = []
    for idx, leg in enumerate(tier['legs']):
        pt = leg['point']
        price = point_price.get(pt, tech_point)
        is_mkt = (pt == 'market')
        # market_entry_threshold: ไม้ market เข้าได้เมื่อแม่เล็กพอ · แม่ใหญ่ → วาง limit ที่ half_mother แทน
        if pt == 'market' and m_pct >= mk_thr:
            price, is_mkt, pt = half_mother, False, 'market→half_mother'
        entries.append((price, f"ไม้{idx + 1}:{pt}", is_mkt))

    # lot รวมจาก SL ของไม้แรก (risk คงที่ — V2 ตัด lot scaling) แล้วหารตามจำนวนไม้
    first_price = entries[0][0]
    sl_pips = abs(first_price - sl) / PIP
    if sl_pips <= 0:
        return None
    lot = _r((portfolio * (_RISK_PCT / 100)) / sl_pips, 2)
    lot_each = _r(lot / len(entries), 2) if entries else lot
    entries_full = [(ep, er, lot_each, mkt) for ep, er, mkt in entries]

    reasons = [
        f"พ่อ {f_pct:.1f}%R55 ({f_end - f_start + 1} แท่ง) · vol {vol_ratio:.2f}",
        f"แม่ {m_pct:.1f}% (compare={mc.get('compare_mode')})",
        f"เข้า tier: พ่อ≥{tier['when'].get('father_min_pct')}%, "
        f"แม่ {tier['when'].get('mother_min_pct')}–{tier['when'].get('mother_max_pct')}% "
        f"→ {len(entries)} ไม้",
        f"TP/SL = {cfg['tpsl']['tp_pct_father']}% / {cfg['tpsl']['sl_pct_father']}% ของพ่อ",
    ]

    return Signal(
        bar_index=bar_idx, direction=mai_dir,
        father_start=f_start, father_end=f_end, father_bars=f_end - f_start + 1,
        father_body_pips=f_body, father_pct_r55=f_pct,
        father_first_pct=0.0, vol_ratio=vol_ratio,
        mother_idx=mother_idx, mother_body_pips=m_body, mother_pct_r55=m_pct,
        mother_quality="",
        tech_point=tech_point, buffer_pips=0.0,
        entry=first_price, sl=sl, sl_pips=sl_pips,
        tp1=0.0, tp2=0.0, tp3=0.0, tp_selected=tp_sel, tp_pips=tp_pips,
        range55=r55, rr=(tp_pips / sl_pips if sl_pips > 0 else 0.0),
        lot=lot, is_round2=False,
        entries=entries_full, reasons=reasons,
    )


class MaiRuayV2(Strategy):
    name = "mai_ruay_v2"

    def __init__(self, cfg: dict, portfolio: float = 1000.0,
                 pre_fill_cancel: bool = False):
        self.cfg = cfg
        self.portfolio = portfolio
        self.pre_fill_cancel = pre_fill_cancel
        self._pend = {}        # tag -> {tp_sel, sl, r55, dir, placed, plan_id, price, place_time}
        # analytics ล้วน (ไม่กระทบ Decision/ผลเทรด)
        self._plan_id = 0
        self.unfilled = []
        self.plan_meta = {}

    def on_bar(self, ctx) -> Decision:
        cfg = self.cfg
        g = cfg['general']
        place, cancel, modify = [], [], []
        i, bar = ctx.bar_index, ctx.bar
        expiry = g['pending_max_age']
        prox_on = g.get('proximity_cancel_enabled', True)
        prox = _PROXIMITY_PCT_R55 / 100

        pend_tags = {o.tag for o in ctx.pendings}
        self._pend = {t: m for t, m in self._pend.items() if t in pend_tags}

        # (A) proximity-cancel + expiry (strategy ทำเอง — engine ไม่หมดอายุ/cancel ให้)
        for o in ctx.pendings:
            m = self._pend.get(o.tag)
            if m is None:
                continue
            age = i - m['placed']
            if (age > expiry) if self.pre_fill_cancel else (age >= expiry):
                cancel.append(o.tag); self._log_unfilled(o.tag, m, i, bar, 'expiry'); continue
            if not prox_on:
                continue
            buf = m['r55'] * prox * PIP
            if m['dir'] == 'BUY'  and bar.high >= m['tp_sel'] - buf:
                cancel.append(o.tag); self._log_unfilled(o.tag, m, i, bar, 'proximity'); continue
            if m['dir'] == 'SELL' and bar.low  <= m['tp_sel'] + buf:
                cancel.append(o.tag); self._log_unfilled(o.tag, m, i, bar, 'proximity'); continue

        remaining = pend_tags - set(cancel)

        # (B) one-plan guard — มี position / pending(เหลือ) / เพิ่งปิดแท่งนี้ → ไม่เปิดใหม่
        just_closed = [] if self.pre_fill_cancel else ctx.last_closed
        if ctx.positions or remaining or just_closed:
            return Decision(place, cancel, modify)
        # (C) เคารพ allow_new_entry (Friday/Gap fact จาก engine)
        if not ctx.allow_new_entry:
            return Decision(place, cancel, modify)

        # (D) หาสัญญาณ
        sig = analyze_bar(ctx.window, i, cfg, self.portfolio)
        if sig is None:
            return Decision(place, cancel, modify)

        # mirror engine plan_id (เพิ่มต่อ 1 batch place ที่ไม่ว่าง)
        self._plan_id += 1
        self.plan_meta[self._plan_id] = {
            'plan_id':          self._plan_id,
            'father_start_bar': sig.father_start,
            'father_end_bar':   sig.father_end,
            'father_pct':       _r(sig.father_pct_r55, 1),
            'mother_bar':       sig.mother_idx,
            'mother_pct':       _r(sig.mother_pct_r55, 1),
            'reasons':          list(sig.reasons),
        }

        # (E) สร้าง Order ต่อไม้ (TP/SL เดียวกันทุกไม้ — ไม่มี R:R adjust)
        for ep, label, lot, is_mkt in sig.entries:
            kind = "MARKET" if is_mkt else "LIMIT"
            place.append(Order(kind=kind, price=ep, lot=lot, sl=sig.sl, tp=sig.tp_selected, tag=label))
            if not is_mkt:
                self._pend[label] = {'tp_sel': sig.tp_selected, 'sl': sig.sl, 'r55': sig.range55,
                                     'dir': sig.direction, 'placed': i,
                                     'plan_id': self._plan_id, 'price': ep,
                                     'place_time': str(ctx.bar.time)}
        return Decision(place, cancel, modify)

    def _log_unfilled(self, tag, m, death_bar, bar, reason):
        self.unfilled.append({
            'plan_id': m.get('plan_id'), 'tag': tag, 'direction': m['dir'],
            'kind': 'LIMIT', 'limit_price': m.get('price'),
            'sl': m.get('sl'), 'tp': m.get('tp_sel'),
            'place_time': m.get('place_time'), 'place_bar': m['placed'],
            'death_time': str(bar.time), 'death_bar': death_bar, 'reason': reason,
        })
