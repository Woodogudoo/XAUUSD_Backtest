# ============================================================================
# bt/strategies/base.py
# ----------------------------------------------------------------------------
# Interface กลาง: ทุกกลยุทธ์ subclass Strategy แล้วเขียน on_bar
# engine เรียก on_bar(ctx) ทีละแท่ง (ดู STRATEGY_AUTHORING_GUIDE.md §1)
# ============================================================================
from __future__ import annotations

from bt.contract import Context, Decision


class Strategy:
    name: str = ""              # ชื่อที่ใช้กับ --strategy

    def __init__(self, cfg: dict):
        self.cfg = cfg          # threshold ทั้งหมดมาจาก config (YAML)

    def on_bar(self, ctx: Context) -> Decision:
        raise NotImplementedError
