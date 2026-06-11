# ============================================================================
# bt/contract.py
# ----------------------------------------------------------------------------
# สัญญาตรงกลาง (Contract) ระหว่าง strategy <-> engine — ทางเลือก B
# ตาม §3 ของ BACKTEST_ARCHITECTURE_DESIGN.md (source of truth)
#
# *** dataclasses ล้วน — ห้ามมี logic ตัดสินใจใด ๆ ในไฟล์นี้ ***
# ============================================================================
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


# ---------- ข้อมูลแท่ง ----------
@dataclass
class Bar:
    time:   datetime          # UTC (pd.Timestamp ใช้ .weekday()/.hour ได้)
    open:   float
    high:   float
    low:    float
    close:  float
    volume: float | None = None   # บางไฟล์ไม่มี Volume — ไม้รวยที่ใช้ vol_ratio ค่อยเช็คเอง


# ---------- กลยุทธ์ -> engine ----------
@dataclass
class Order:
    kind:  str        # "MARKET" | "LIMIT"
    price: float      # ราคา limit (MARKET ไม่ใช้)
    lot:   float
    sl:    float      # กลยุทธ์คำนวณเสร็จแล้ว
    tp:    float      # กลยุทธ์คำนวณเสร็จแล้ว (R:R เท่าไรก็ได้)
    tag:   str        # ระบุไม้ เช่น "ไม้2:tech"


@dataclass
class Modify:         # เลื่อน SL/TP ของ position ที่เปิดอยู่ (trailing ฯลฯ)
    tag: str
    sl:  float | None = None
    tp:  float | None = None


@dataclass
class Decision:
    place:  list[Order]    # ออเดอร์ใหม่ — 1 batch ที่ไม่ว่าง = 1 "จุดเข้า" (plan)
    cancel: list[str]      # tag ของ pending ที่จะยกเลิก
    modify: list[Modify]   # เลื่อน SL/TP ของไม้ที่ถืออยู่
    # *** ไม่มี exit *** — position ปิดเองเมื่อชน SL/TP (ที่อาจถูกเลื่อนแล้ว) เท่านั้น


# ---------- engine -> กลยุทธ์ ----------
@dataclass
class Position:
    tag: str
    direction: str
    entry: float
    sl: float
    tp: float
    lot: float
    entry_bar: int
    plan_id: int


@dataclass
class ClosedInfo:           # engine แจ้งว่าไม้ปิดแล้ว — กลยุทธ์เอาไปจับคู่เอง
    tag: str
    plan_id: int
    result: str             # "SL" | "TP"
    exit_bar: int
    exit_price: float


@dataclass
class Context:
    bar: Bar
    bar_index: int
    window:      list[Bar]          # ถึงแท่งปัจจุบัน (รวม)
    positions:   list[Position]     # ไม้ที่ถืออยู่
    pendings:    list[Order]        # limit ที่รออยู่
    last_closed: list[ClosedInfo]   # ไม้ที่เพิ่งปิดในแท่งนี้ (อาจหลายไม้)
    allow_new_entry: bool           # engine guard: False ตอนศุกร์ใกล้ปิด / ช่วงรอ gap
