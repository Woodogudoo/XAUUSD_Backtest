# คู่มือเขียน Strategy ให้เสียบ Engine ได้ไร้รอยต่อ

> เอกสารคู่กับ `BACKTEST_ARCHITECTURE_DESIGN.md` (ทางเลือก B — engine โง่ / strategy ฉลาด)
> อ่านไฟล์นี้ก่อนเขียนกลยุทธ์ใหม่ทุกครั้ง

---

## 0. หลักคิดใน 1 ประโยค

**Engine = มือที่ทำตามคำสั่ง · Strategy = สมองที่ตัดสินใจทุกอย่าง**
ถ้ามี logic ที่ "ตัดสินใจ" (เข้าไหน, TP/SL เท่าไร, ยกเลิกเมื่อไร, เลื่อนยังไง) → อยู่ใน strategy เสมอ
Engine ไม่เคยคิดแทน ไม่ปรับ R:R ไม่ยกเลิก/หมดอายุเอง ไม่รู้จักคำว่า "รอบ 2"

---

## 1. Interface ที่ต้อง implement

```python
from bt.contract import Context, Decision, Order, Modify
from bt.strategies.base import Strategy

class MyStrategy(Strategy):
    name = "my_strategy"            # ชื่อที่ใช้กับ --strategy

    def __init__(self, cfg: dict):
        self.cfg = cfg             # threshold ทั้งหมดมาจาก config (YAML)
        self._state = {}           # จำ state ของแผนตัวเองได้ตามต้องการ

    def on_bar(self, ctx: Context) -> Decision:
        ...
        return Decision(place=[...], cancel=[...], modify=[...])
```

มีแค่นี้ — `name` + `on_bar()` engine จะเรียก `on_bar` ให้ทีละแท่ง

---

## 2. รับอะไร (Context) / คืนอะไร (Decision)

### รับ — `ctx: Context`
| field | คือ |
|---|---|
| `ctx.bar` | แท่งปัจจุบัน (OHLC + time) |
| `ctx.bar_index` | ลำดับแท่ง |
| `ctx.window` | แท่งทั้งหมด **ถึงแท่งปัจจุบัน** (ห้ามดูเกินนี้ = ห้ามดูอนาคต) |
| `ctx.positions` | ไม้ที่ถืออยู่ (`Position`) |
| `ctx.pendings` | limit ที่รออยู่ (`Order`) |
| `ctx.last_closed` | ไม้ที่เพิ่งปิดในแท่งนี้ (`ClosedInfo`: tag, plan_id, result, exit_bar, exit_price) |
| `ctx.allow_new_entry` | `False` ตอนศุกร์ใกล้ปิด / ช่วงรอ gap (engine บอกให้ — จะเคารพหรือไม่ strategy เลือกเอง) |

### คืน — `Decision`
| field | คือ |
|---|---|
| `place` | `list[Order]` ออเดอร์ใหม่ — **1 batch ที่ไม่ว่าง = 1 จุดเข้า** (engine ออก `plan_id` ให้อัตโนมัติ) |
| `cancel` | `list[str]` tag ของ pending ที่จะยกเลิก |
| `modify` | `list[Modify]` เลื่อน SL/TP ของไม้ที่ถืออยู่ (`Modify(tag, sl=?, tp=?)`) |

`Order` = `kind("MARKET"/"LIMIT"), price, lot, sl, tp, tag`
> ไม่มีคำสั่ง `exit` — ไม้ปิดเองเมื่อชน SL/TP (ที่อาจถูก `modify` แล้ว) เท่านั้น

---

## 3. กฎที่ Strategy ต้องทำตาม (Contract Rules)

1. **คำนวณ TP/SL สุดท้ายต่อ Order ให้ครบเอง** — engine ใช้ค่าที่ส่งมาตรงๆ ไม่ปรับ R:R ให้
   (อยาก R:R=1.0 / 0.5 / อะไรก็ตาม → strategy จัดการก่อนใส่ใน Order)
2. **ใส่ `tag` ทุก Order** ให้ไม่ซ้ำในแผนเดียวกัน (เช่น `"ไม้1:market"`, `"ไม้2:tech"`) ใช้อ้างอิงตอน cancel/modify และในรายงาน
3. **1 จุดเข้า = ใส่ทุกไม้ใน `place` ครั้งเดียว** เพื่อให้ได้ `plan_id` เดียวกัน (รายงานตามจุดเข้าถึงจะถูก)
4. **proximity-cancel / expiry ทำเอง** — เช็ค `ctx.pendings` ทุกแท่ง ถ้าถึงเงื่อนไข → ใส่ tag ใน `cancel`
   (engine ไม่หมดอายุ pending ให้เอง)
5. **logic ที่อิงผลของแผนก่อนหน้า (เช่นไม้รวยรอบ 2) → จำ state เอง** ใช้ `ctx.last_closed` จับคู่กับแผนที่เคยวาง
6. **one-plan-ต่อครั้ง (ถ้าต้องการ) ทำเอง** — ถ้า `ctx.positions` หรือ `ctx.pendings` ไม่ว่าง ก็อย่าออก signal ใหม่
7. **เคารพ `ctx.allow_new_entry` (ถ้าต้องการกฎ Friday/Gap)** — `False` แล้วไม่ `place` ไม้ใหม่ (แต่ยัง cancel/modify ได้)
8. **ห้ามดูอนาคต** — ใช้ได้แค่ `ctx.window` (ถึงแท่งปัจจุบัน) เท่านั้น
9. **threshold อยู่ใน config** ไม่ hardcode — อ่านจาก `self.cfg` เพื่อเปลี่ยนเงื่อนไขได้โดยไม่แก้โค้ด

---

## 4. สิ่งที่ Engine รับประกัน (Strategy ไม่ต้องทำเอง)

- ลำดับใน 1 แท่ง: เช็ค exit (**SL ก่อน TP**) → fill/cancel pending → `on_bar` → apply (cancel→modify→place) → child-bar check ไม้ที่เพิ่งเปิด
- market fill = `open` ของแท่งที่วาง · limit fill = ราคา limit เป๊ะ · gap ทะลุ level = fill ที่ level เป๊ะ
- SL+TP โดนแท่งเดียว → นับ **SL**
- `modify` มีผลตั้งแต่แท่งถัดไป
- ออก `plan_id`, เก็บ trades, ทำ `trades.csv` / `plans.csv` / `summary.json`

---

## 5. โครง Strategy ขั้นต่ำ (คัดลอกไปเริ่มได้เลย)

```python
class MyStrategy(Strategy):
    name = "my_strategy"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._plan = None     # state สำหรับ logic อิงอดีต (เช่น รอบ 2)

    def on_bar(self, ctx: Context) -> Decision:
        place, cancel, modify = [], [], []

        # (1) ยกเลิก pending ตามเงื่อนไขของกลยุทธ์ (เช่น proximity-cancel / expiry)
        for o in ctx.pendings:
            if self._should_cancel(o, ctx):
                cancel.append(o.tag)

        # (2) เลื่อน SL/TP ไม้ที่ถืออยู่ (trailing) — ถ้ามี
        for p in ctx.positions:
            new_sl = self._trail(p, ctx)
            if new_sl is not None:
                modify.append(Modify(tag=p.tag, sl=new_sl))

        # (3) one-plan guard (ถ้าต้องการ 1 แผนต่อครั้ง)
        if ctx.positions or ctx.pendings:
            return Decision(place, cancel, modify)

        # (4) เคารพ Friday/Gap (ถ้าต้องการ)
        if not ctx.allow_new_entry:
            return Decision(place, cancel, modify)

        # (5) หาสัญญาณจาก ctx.window (ห้ามดูอนาคต) — รวม logic รอบ 2 ผ่าน ctx.last_closed
        sig = self._find_signal(ctx)
        if sig is not None:
            place = self._build_orders(sig)   # list[Order] — tag/lot/sl/tp ครบ, R:R คิดเสร็จ

        return Decision(place, cancel, modify)
```

---

## 6. Checklist ก่อนบอกว่า "strategy พร้อมรัน"

- [ ] subclass `Strategy` มี `name` และ `on_bar(ctx) -> Decision`
- [ ] ทุก `Order` มี `tag` ไม่ซ้ำ + `sl`/`tp` คำนวณเสร็จ (ไม่พึ่ง engine ปรับ R:R)
- [ ] ไม้ของจุดเข้าเดียวกันอยู่ใน `place` batch เดียว
- [ ] proximity-cancel / expiry / รอบ 2 / one-plan / Friday-Gap — ทำในกลยุทธ์ครบ (ถ้าต้องการ)
- [ ] ไม่อ้างอิงข้อมูลเกิน `ctx.window`
- [ ] threshold อ่านจาก `self.cfg` ไม่ hardcode
- [ ] รันแล้วผลตรงกับ golden (เทียบ v2.04 หรือผลที่คาดไว้)

---

*ถ้า engine ต้องเพิ่มความสามารถใหม่ (เช่นคำสั่ง exit) ให้แก้ contract + เอกสารนี้พร้อมกัน*
