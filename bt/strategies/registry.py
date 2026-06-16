# ============================================================================
# bt/strategies/registry.py
# ----------------------------------------------------------------------------
# Strategy registry — single source of truth: id (--strategy) → class
# ใช้ร่วมกัน: bt/__main__ (CLI map) และ bt/server (GET /api/strategies)
#
# ลงทะเบียนกลยุทธ์ใหม่ = เพิ่ม 1 บรรทัดใน STRATEGIES (id ตรงกับ class.name)
# *** ไม่มี logic เทรด — แค่ทะเบียนชื่อ ***
# ============================================================================
from __future__ import annotations

from bt.strategies.mai_ruay import MaiRuay
from bt.strategies.mai_ruay_v2 import MaiRuayV2

STRATEGIES = {
    MaiRuay.name:   MaiRuay,      # "mai_ruay"
    MaiRuayV2.name: MaiRuayV2,    # "mai_ruay_v2"
}


def strategy_ids() -> list[str]:
    """list ของ strategy id ที่ลงทะเบียน (เรียงตามลำดับลงทะเบียน) — สำหรับ /api/strategies + dropdown"""
    return list(STRATEGIES)
