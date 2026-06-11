# ============================================================================
# bt/engine.py
# ----------------------------------------------------------------------------
# Engine / Simulator (แช่แข็ง) — ทางเลือก B "engine โง่ / strategy ฉลาด"
#
# ลำดับใน 1 แท่ง (§9-Tier2):
#   (1) exit position เดิม (SL ก่อน TP, conservative)
#   (2) fill/cancel pending limit (fill ที่ราคา level เป๊ะ; gap ทะลุ = level เป๊ะ)
#   (3) on_bar(ctx) -> Decision
#   (4) apply: cancel -> modify -> place (MARKET=fill ที่ open; LIMIT=รอ step 5a)
#   (5a) same-bar limit fill: limit ที่เพิ่งวางในแท่งนี้ เช็ค fill กับแท่งเดียวกัน
#        (fill ที่ level เป๊ะ) ติด→เปิด ไม่ติด→เข้าคิว pending · ตรง v2.04 ที่เช็ค child bar
#   (5b) child-bar check ไม้ที่เพิ่งเปิดในแท่งนี้ (เช็ค SL/TP กับแท่งเดิม)
#
# *** engine ไม่มี logic ตัดสินใจ — ไม่ปรับ R:R, ไม่ cancel/expire เอง,
#     ไม่รู้จัก "รอบ 2" · ตรรกะฉลาดทั้งหมดอยู่ใน strategy ***
# ============================================================================
from __future__ import annotations

from dataclasses import dataclass

from bt.contract import Bar, Context, ClosedInfo, Decision, Order, Position
from bt.guards import allow_new_entry_flags

PIP = 0.01


class _BarWindow:
    """view ของ bars[0:n] แบบ read-only O(1) — กัน strategy แอบดูอนาคต
    + ไม่ copy prefix ทุกแท่ง (เลี่ยง O(n²)) · ทำตัวเหมือน list[Bar] (index/slice/iter)"""
    __slots__ = ("_bars", "_n")

    def __init__(self, bars, n):
        self._bars = bars
        self._n = n

    def __len__(self):
        return self._n

    def __getitem__(self, k):
        if isinstance(k, slice):
            start, stop, step = k.indices(self._n)   # clamp ถึงแท่งปัจจุบัน
            return self._bars[start:stop:step]
        if k < 0:
            k += self._n
        if not 0 <= k < self._n:
            raise IndexError("bar index out of window (no future peek)")
        return self._bars[k]

    def __iter__(self):
        return iter(self._bars[:self._n])


@dataclass
class _Open:
    """ห่อ Position ไว้กับ kind (kind ใช้ตอนรายงานเท่านั้น — contract ไม่มี kind)"""
    pos: Position
    kind: str   # "MARKET" | "LIMIT"


@dataclass
class _Pending:
    order:   Order
    plan_id: int


def _direction(sl: float, tp: float) -> str:
    """อนุมานทิศจาก tp/sl — BUY: tp อยู่บน/sl อยู่ล่าง · SELL: ตรงข้าม"""
    return "BUY" if tp > sl else "SELL"


def _check_exit(pos: Position, bar: Bar):
    """คืน (result, exit_price) ถ้าชน SL/TP — SL ก่อน TP เสมอ (conservative)"""
    if pos.direction == "BUY":
        if bar.low <= pos.sl:
            return "SL", pos.sl
        if bar.high >= pos.tp:
            return "TP", pos.tp
    else:
        if bar.high >= pos.sl:
            return "SL", pos.sl
        if bar.low <= pos.tp:
            return "TP", pos.tp
    return None, None


def _check_limit_fill(order: Order, bar: Bar):
    """คืนราคา fill ถ้า limit โดนแตะในแท่งนี้ (fill ที่ level เป๊ะ) ไม่งั้น None"""
    if _direction(order.sl, order.tp) == "BUY":
        if bar.low <= order.price:
            return order.price
    else:
        if bar.high >= order.price:
            return order.price
    return None


def _make_trade(open_pos: _Open, bars: list[Bar],
                exit_bar: int, exit_price: float, result: str) -> dict:
    pos = open_pos.pos
    sign = 1 if pos.direction == "BUY" else -1
    pnl_pips = sign * (exit_price - pos.entry) / PIP
    return {
        "plan_id":    pos.plan_id,
        "tag":        pos.tag,
        "direction":  pos.direction,
        "kind":       open_pos.kind,
        "entry_bar":  pos.entry_bar,
        "entry_time": bars[pos.entry_bar].time,
        "entry":      pos.entry,
        "exit_bar":   exit_bar,
        "exit_time":  bars[exit_bar].time,
        "exit_price": exit_price,
        "sl":         pos.sl,
        "tp":         pos.tp,
        "lot":        pos.lot,
        "result":     result,            # "SL" | "TP"
        "pnl_pips":   round(pnl_pips, 1),
        "pnl_usd":    round(pnl_pips * pos.lot, 2),
    }


def run(bars: list[Bar], strategy, guards: dict | None = None,
        inject_compat: bool = False) -> dict:
    """รัน backtest bar-by-bar · strategy = object ที่มี on_bar(ctx) -> Decision
    guards (optional) = global guards config → engine คำนวณ allow_new_entry เป็น fact
    (ไม่บล็อกเอง · strategy อ่านไปตัดสิน) · None = allow_new_entry True เสมอ
    inject_compat=True → จำลองบั๊ก child-bar inject ของ v2.04 (validate เท่านั้น)
    """
    allow_flags = allow_new_entry_flags(bars, guards, inject_compat) if guards else None
    if inject_compat:
        return _run_inject_compat(bars, strategy, allow_flags)
    opens:    list[_Open]    = []
    pendings: list[_Pending] = []
    trades:   list[dict]     = []
    next_plan_id = 1
    carry_closed: list[ClosedInfo] = []   # child-bar closures จากแท่งก่อน -> last_closed แท่งนี้

    for i, bar in enumerate(bars):
        closed_this_bar: list[ClosedInfo] = list(carry_closed)
        carry_closed = []
        opened_now: list[_Open]    = []
        placed_now: list[_Pending] = []   # limit ที่เพิ่งวางในแท่งนี้ (รอ step 5a)

        # (1) exit position เดิม (SL ก่อน TP)
        survivors: list[_Open] = []
        for o in opens:
            result, exit_price = _check_exit(o.pos, bar)
            if result:
                trades.append(_make_trade(o, bars, i, exit_price, result))
                closed_this_bar.append(
                    ClosedInfo(o.pos.tag, o.pos.plan_id, result, i, exit_price))
            else:
                survivors.append(o)
        opens = survivors

        # (2) fill pending limit (จากแท่งก่อน ๆ)
        still_pending: list[_Pending] = []
        for p in pendings:
            fill_price = _check_limit_fill(p.order, bar)
            if fill_price is not None:
                o = _open_from_order(p.order, p.plan_id, fill_price, i, "LIMIT")
                opens.append(o)
                opened_now.append(o)
            else:
                still_pending.append(p)
        pendings = still_pending

        # (3) on_bar
        ctx = Context(
            bar=bar,
            bar_index=i,
            window=_BarWindow(bars, i + 1),
            positions=[o.pos for o in opens],
            pendings=[p.order for p in pendings],
            last_closed=closed_this_bar,
            allow_new_entry=(allow_flags[i] if allow_flags is not None else True),
        )
        decision = strategy.on_bar(ctx)

        # (4) apply: cancel -> modify -> place
        if decision.cancel:
            cancel_set = set(decision.cancel)
            pendings = [p for p in pendings if p.order.tag not in cancel_set]
        for m in decision.modify:
            for o in opens:
                if o.pos.tag == m.tag:
                    if m.sl is not None:
                        o.pos.sl = m.sl
                    if m.tp is not None:
                        o.pos.tp = m.tp
        if decision.place:
            plan_id = next_plan_id
            next_plan_id += 1
            for order in decision.place:
                if order.kind == "MARKET":
                    o = _open_from_order(order, plan_id, bar.open, i, "MARKET")
                    opens.append(o)
                    opened_now.append(o)
                else:   # LIMIT -> รอ step 5a
                    placed_now.append(_Pending(order=order, plan_id=plan_id))

        # (5a) same-bar limit fill — limit ที่เพิ่งวางในแท่งนี้ เช็ค fill กับแท่งเดียวกัน
        for p in placed_now:
            fill_price = _check_limit_fill(p.order, bar)
            if fill_price is not None:
                o = _open_from_order(p.order, p.plan_id, fill_price, i, "LIMIT")
                opens.append(o)
                opened_now.append(o)
            else:
                pendings.append(p)   # ไม่ติดแท่งนี้ -> เข้าคิวรอแท่งถัดไป

        # (5b) child-bar check ไม้ที่เพิ่งเปิดในแท่งนี้
        if opened_now:
            survivors = []
            opened_set = id_set(opened_now)
            for o in opens:
                if id(o) in opened_set:
                    result, exit_price = _check_exit(o.pos, bar)
                    if result:
                        trades.append(_make_trade(o, bars, i, exit_price, result))
                        carry_closed.append(
                            ClosedInfo(o.pos.tag, o.pos.plan_id, result, i, exit_price))
                        continue
                survivors.append(o)
            opens = survivors

    return {"trades": trades}


# ============================================================================
# โหมด inject_compat — จำลองบั๊ก child-bar inject ของ v2.04 (validate vs golden เท่านั้น)
# ----------------------------------------------------------------------------
# *** ไม่ใช่พฤติกรรมที่ถูกต้อง · เปิดผ่าน flag เท่านั้น · ห้ามรั่วเข้า path correct ***
# 2 บั๊ก/ลำดับที่จำลอง (mirror reference/mairuay_v204_reference.py):
#  (1) child-bar inject: เมื่อไม้ใน "แท่งเปิด (signal bar)" ชน SL/TP ไม้ใดไม้หนึ่ง → v2.04
#      ยัด hit เดียวกันให้ทุก position ที่เปิดอยู่ที่แท่งถัดไป (แต่ละไม้ปิดที่ tp/sl ของตัวเอง,
#      timestamp = แท่ง inject) แม้ไม้นั้นไม่ได้แตะ tp/sl จริง (ref :692-705, :970-990)
#  (2) cancel-before-fill: v2.04 เช็ค expiry/proximity (ยกเลิก) "ก่อน" fill ในแท่งเดียวกัน
#      (ref :660-687) — pending ที่ทั้งโดน proximity และราคาแตะ level บนแท่งเดียวกันจะ "ถูกยกเลิก
#      ไม่ fill" ต่างจาก path correct (engine fill ที่ step2 ก่อน strategy ยกเลิก)
# วิธีจำลอง: เรียก on_bar "ก่อน" fill → ยกเลิกตามที่ strategy สั่งก่อน แล้วค่อย fill
#   ลำดับ: on_bar(cancel/place) → apply cancel → fill → exit/inject → apply place → child-detect
#   ลำดับนี้ตรง v2.04: pending(cancel/fill) → SLTP → guard/entry → child-detect
#   last_closed ส่ง [] (exit แท่งนี้เกิดหลัง on_bar) — guard พึ่ง positions/pendings (= just_closed
#   ของ v2.04 ผ่านการที่ position ยังเปิดอยู่ตอน on_bar) · expiry: strategy รับ pre_fill_cancel=True
#   → ใช้ขอบ i-placed > expiry (cancel-before-fill ให้ fillable p+1..p+5 ตรง v2.04)
# ============================================================================
def _run_inject_compat(bars: list[Bar], strategy, allow_flags) -> dict:
    opens:    list[_Open]    = []
    pendings: list[_Pending] = []
    trades:   list[dict]     = []
    next_plan_id = 1
    inject = None          # (hit_type, child_bar_index) ค้างไปยังแท่งถัดไป
    carry_closed: list[ClosedInfo] = []   # exit แท่งก่อน → last_closed แท่งนี้ (round2 ใช้)

    for i, bar in enumerate(bars):
        closed_this_bar: list[ClosedInfo] = []
        opened_signal: list[_Open] = []
        placed_now:    list[_Pending] = []

        # (1) on_bar ก่อน fill/exit — cancel pre-empts fill (mirror v2.04 pending loop)
        #     last_closed = exit ของแท่งก่อน (carry) : exit แท่งนี้เกิดหลัง on_bar
        #     → guard พึ่ง positions/pendings (strategy pre_fill_cancel) · round2 ใช้ last_closed
        ctx = Context(
            bar=bar, bar_index=i, window=_BarWindow(bars, i + 1),
            positions=[o.pos for o in opens],
            pendings=[p.order for p in pendings],
            last_closed=carry_closed,
            allow_new_entry=(allow_flags[i] if allow_flags is not None else True),
        )
        decision = strategy.on_bar(ctx)

        # (2) apply cancel + modify ก่อน fill
        if decision.cancel:
            cancel_set = set(decision.cancel)
            pendings = [p for p in pendings if p.order.tag not in cancel_set]
        for m in decision.modify:
            for o in opens:
                if o.pos.tag == m.tag:
                    if m.sl is not None:
                        o.pos.sl = m.sl
                    if m.tp is not None:
                        o.pos.tp = m.tp

        # (3) fill pending (mirror v2.04 step1 — หลัง cancel)
        still_pending: list[_Pending] = []
        for p in pendings:
            fill_price = _check_limit_fill(p.order, bar)
            if fill_price is not None:
                opens.append(_open_from_order(p.order, p.plan_id, fill_price, i, "LIMIT"))
            else:
                still_pending.append(p)
        pendings = still_pending

        # (4) exit / inject (mirror v2.04 step2 + _use_inject) — รวมไม้ที่เพิ่ง fill
        if inject is not None:
            ih, ibar = inject
            for o in opens:
                ex = o.pos.tp if ih == "TP" else o.pos.sl
                trades.append(_make_trade(o, bars, ibar, ex, ih))
                closed_this_bar.append(ClosedInfo(o.pos.tag, o.pos.plan_id, ih, ibar, ex))
            opens = []
            inject = None
        else:
            survivors = []
            for o in opens:
                result, exit_price = _check_exit(o.pos, bar)
                if result:
                    trades.append(_make_trade(o, bars, i, exit_price, result))
                    closed_this_bar.append(
                        ClosedInfo(o.pos.tag, o.pos.plan_id, result, i, exit_price))
                else:
                    survivors.append(o)
            opens = survivors

        # (5) apply place (new entry) — market fill ที่ open; limit รอ same-bar
        if decision.place:
            plan_id = next_plan_id
            next_plan_id += 1
            for order in decision.place:
                if order.kind == "MARKET":
                    o = _open_from_order(order, plan_id, bar.open, i, "MARKET")
                    opens.append(o)
                    opened_signal.append(o)
                else:
                    placed_now.append(_Pending(order=order, plan_id=plan_id))

        # (6) same-bar limit fill (signal-bar) -> opened_signal
        for p in placed_now:
            fill_price = _check_limit_fill(p.order, bar)
            if fill_price is not None:
                o = _open_from_order(p.order, p.plan_id, fill_price, i, "LIMIT")
                opens.append(o)
                opened_signal.append(o)
            else:
                pendings.append(p)

        # (7) child-detect: ไม้แรกใน signal-bar ที่ชน → ตั้ง inject (ยัดยกแผงแท่งถัดไป)
        for o in opened_signal:
            result, _ = _check_exit(o.pos, bar)
            if result:
                inject = (result, i)
                break

        carry_closed = closed_this_bar   # exit แท่งนี้ → last_closed แท่งถัดไป

    return {"trades": trades}


def _open_from_order(order: Order, plan_id: int, entry_price: float,
                     entry_bar: int, kind: str) -> _Open:
    pos = Position(
        tag=order.tag,
        direction=_direction(order.sl, order.tp),
        entry=entry_price,
        sl=order.sl,
        tp=order.tp,
        lot=order.lot,
        entry_bar=entry_bar,
        plan_id=plan_id,
    )
    return _Open(pos=pos, kind=kind)


def id_set(opens: list[_Open]) -> set[int]:
    return {id(o) for o in opens}
