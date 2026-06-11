# ============================================================================
# bt/guards.py
# ----------------------------------------------------------------------------
# คำนวณ allow_new_entry เป็น "ข้อเท็จจริงปฏิทิน/ราคา" (Friday close + Gap)
# port จาก reference (_find_gap_start / _is_friday_close)
#
# *** engine เอา fact นี้ใส่ ctx.allow_new_entry เฉย ๆ — ไม่บล็อกเอง
#     strategy เป็นคนอ่านไปตัดสิน (เปิด/ปิดผ่าน config) ***
# ============================================================================
from __future__ import annotations

from bt.contract import Bar

PIP = 0.01


def _find_gap_start(bars: list[Bar], gap_pct: float, wait: int) -> int:
    """[compat] คืน bar index เริ่มเทรดหลัง gap ใหญ่ "ตัวแรกในไฟล์" + wait (0 = ไม่มี)
    *** quirk v2.04: ใช้ first-gap เป็น start แบบ global → ข้ามทุกแท่งก่อน gap ทั้งหมด
        (วันที่เทรดได้ก่อน gap ถูกทิ้ง) + จัดการ gap แค่ตัวเดียว · ใช้เฉพาะ inject_compat ***
    mirror reference._find_gap_start (day-boundary only, threshold ผูก r55_init แท่งแรก)
    """
    if len(bars) < 2:
        return 0
    first55 = bars[:min(55, len(bars))]
    r55_init = (max(b.high for b in first55) - min(b.low for b in first55)) / PIP
    gap_threshold = r55_init * gap_pct / 100      # USD
    for i in range(1, len(bars)):
        if bars[i].time.date() != bars[i - 1].time.date():
            if abs(bars[i].open - bars[i - 1].close) > gap_threshold:
                return i + wait
    return 0


def _is_friday_close(t, close_hour: int, close_min: int, buffer_min: int) -> bool:
    """True = ห้ามเปิด (ศุกร์ใกล้ปิดตลาด) — mirror reference"""
    if t.weekday() != 4:
        return False
    close_dt = t.replace(hour=close_hour, minute=close_min, second=0, microsecond=0)
    diff = (close_dt - t).total_seconds() / 60
    return 0 <= diff <= buffer_min


def allow_new_entry_flags(bars: list[Bar], guards: dict,
                          inject_compat: bool = False) -> list[bool]:
    """คำนวณ allow_new_entry ต่อแท่ง · เคารพ enable flag ของแต่ละ guard

    correct (default): **ไม่มี gap guard** — gap ไม่กระทบ allow_new_entry (เทรดทุกช่วง)
        มีแค่ Friday-close guard เท่านั้น
    inject_compat=True: คง quirk v2.04 — gap global-skip จาก first-gap+wait (ดู _find_gap_start)
        + Friday-close
    """
    n = len(bars)
    gap_cfg = guards.get('gap', {})
    fri_cfg = guards.get('friday_close', {})
    fri_on = fri_cfg.get('enable', False)

    flags = [True] * n
    if inject_compat and gap_cfg.get('enable', False):
        # --- compat only: quirk v2.04 global-skip ---
        gap_start = _find_gap_start(bars, gap_cfg.get('threshold_pct', 0),
                                    gap_cfg.get('wait_bars', 0))
        for i in range(n):
            flags[i] = i >= gap_start
    # correct (default): ไม่แตะ flags จาก gap — คง True (Friday เท่านั้นด้านล่าง)

    if fri_on:
        for i in range(n):
            if flags[i] and _is_friday_close(
                    bars[i].time, fri_cfg['close_hour_utc'],
                    fri_cfg['close_min'], fri_cfg['no_trade_min']):
                flags[i] = False
    return flags
