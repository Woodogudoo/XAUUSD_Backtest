# XAUUSD Backtest — บันทึกการออกแบบสถาปัตยกรรม (Living Doc)

- **เวอร์ชันเอกสาร:** 1.4
- **วันที่:** 2026-06-10
- **สถานะ:** อนุมัติแล้ว ✅ — พร้อม implement (ดูลำดับการสร้างใน `IMPLEMENTATION_BRIEF.md`)

---

## 1. หลักการแกนกลาง (ยืนยันแล้ว)

**Simulator = ตัวรันที่ซื่อสัตย์ (faithful executor)** ไม่มีความเห็น ไม่ตัดสินใจเอง

- รันตาม `Signal` + เงื่อนไขของกลยุทธ์ "เป๊ะๆ"
- **ไม่บังคับ R:R** — ถ้ากลยุทธ์ให้ผลออกมา R:R = 0.5 ก็เทรดที่ 0.5
- ถ้ากลยุทธ์สั่งเข้าหลาย order → เข้าตามนั้น ไม่ยุบรวม ไม่ตัดทิ้ง
- SL / TP มาจากกลยุทธ์ทั้งหมด — engine ไม่ปรับ ไม่ขยับ
- ตรรกะที่ "ฉลาด" (ปรับ R:R, TP adjust, proximity cancel, dedup จุดเดิม)
  → อยู่ฝั่ง **กลยุทธ์** ไม่ใช่ engine ถ้ากลยุทธ์ไหนไม่ใส่ engine จะไม่ทำให้เอง

**ผลลัพธ์:** เปลี่ยน strategy/config แล้วผลต่างที่เห็นมาจาก "กฎ" ล้วนๆ
เพราะ engine เป็นตัวเดิมเป๊ะทุกรอบ → เปรียบเทียบเวอร์ชันได้สะอาด

---

## 2. สถาปัตยกรรม 4 ชั้น

1. **Data layer** — โหลด CSV → `list[Bar]` เท่านั้น ไม่มี logic เทรด
   คำนวณ R55 ต่อ window แยกเป็น util (ตรึง `start_bar = 54`)
2. **Strategy layer** — รับ `window` + `config` → คืน `Signal | None`
   เป็นที่เดียวที่เงื่อนไขเปลี่ยน · 1 กลยุทธ์ = 1 ไฟล์ · ทุกตัว interface เดียวกัน
3. **Engine / Simulator (แช่แข็ง)** — รับ `Signal` มารัน bar-by-bar:
   เติม pending, เช็ค SL/TP, แบ่ง order · *ห้ามแก้เวลาเปลี่ยนกฎ*
4. **Report layer** — `trades` → winrate, equity, เทียบหลาย run

---

## 3. สัญญาตรงกลาง (Contract) — ล็อกแล้ว (ทางเลือก B)

```python
# ---------- กลยุทธ์ → engine ----------
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

# ---------- engine → กลยุทธ์ ----------
@dataclass
class Position:
    tag: str; direction: str; entry: float
    sl: float; tp: float; lot: float
    entry_bar: int; plan_id: int

@dataclass
class ClosedInfo:           # engine แจ้งว่าไม้ปิดแล้ว — กลยุทธ์เอาไปจับคู่เอง
    tag: str; plan_id: int
    result: str             # "SL" | "TP"
    exit_bar: int; exit_price: float

@dataclass
class Context:
    bar: Bar
    bar_index: int
    window:      list[Bar]          # ถึงแท่งปัจจุบัน (รวม)
    positions:   list[Position]     # ไม้ที่ถืออยู่
    pendings:    list[Order]        # limit ที่รออยู่
    last_closed: list[ClosedInfo]   # ไม้ที่เพิ่งปิดในแท่งนี้ (อาจหลายไม้)
    allow_new_entry: bool           # engine guard: False ตอนศุกร์ใกล้ปิด / ช่วงรอ gap
```

**Reporting 2 ระดับ:** engine ออก `plan_id` ให้อัตโนมัติ 1 ค่าต่อ 1 batch ของ `place`
→ รายงานราย **ไม้** (per Order/tag) และรายงานตาม **จุดเข้า** (group by `plan_id`)

> engine รู้จักแค่ contract นี้ · ไม่มี exit · มี modify · ไม่รู้เรื่อง "แผน/รอบ2" เลย

---

## 4. เครื่องมือช่วย debug (ออกแบบมาแก้ปัญหาเดิม)

- **Decision log** — ทุก window ที่กลยุทธ์ "ปฏิเสธ" ให้ log เหตุผล
  (เช่น `father ผ่าน, mother=3.8% < 4% → reject`) ตรวจ `<=` vs `<` ได้ทันที
- **Config = YAML ต่อเวอร์ชัน** — threshold ทั้งหมดอยู่ในไฟล์ เป็น audit trail

---

## 5. โครงสร้างไฟล์ + การรัน (offline 100%)

```
bt/
  data.py          # load_csv, r55
  contract.py      # Bar, Order, Modify, Decision, Position, ClosedInfo, Context
  engine.py        # แช่แข็ง (อ่านแท่ง + fill/exit + modify + สรุปผล)
  report.py        # รายงานราย ไม้ + ตามจุดเข้า
  strategies/
    base.py        # class Strategy: on_bar(ctx) -> Decision
    mai_ruay.py
    mountain.py
configs/           # mai_ruay_v204.yaml ...
data/              # XAUUSD_M1.csv ...
runs/              # ผลแต่ละรอบ + เทียบ
```

รัน: `python -m bt run --strategy mai_ruay --config configs/v204.yaml --data data/XAUUSD_M1.csv --out runs/v204/`
ใช้ `pandas` ล้วน · ทำงาน offline · รันใน terminal/VSCode (**ไม่เผื่อ Colab**)

---

## 6. พฤติกรรมจริงของ v2.04 (ยืนยันจากโค้ด)

### A. การตัดสินภายในแท่งเดียว (intrabar)
- **SL ก่อน TP เสมอ** — โค้ดเช็ค `if low<=sl ... elif high>=tp` (BUY)
  ถ้าแท่งเดียวกวาดโดนทั้งคู่ → นับ SL (conservative) ✓
- **Limit fill กลางแท่งทันที** ที่ราคา limit (`sig.entry`) ไม่รอปิดแท่ง ✓
- มี **child-bar check**: เปิดไม้แท่งไหน เช็คทันทีว่าแท่งนั้นโดน SL/TP เลยไหม (inject)

### B. วงจรชีวิตของ "แผน"
- 1 แผน = 1 signal มีได้ **3 ไม้** (entries) — ไม้1 market/limit, ไม้2 tech, ไม้3 tech±10%พ่อ
- **แต่ละไม้เป็น position อิสระ** (`open_positions` เป็น list) — ไม้1 โดน SL **ไม่ปิด** ไม้2
- เปิดแผนใหม่ได้เมื่อ `open_positions` ว่าง **และ** ไม่มี pending **และ** ไม่เพิ่งปิดแท่งนี้
- **pending ที่ยังไม่ fill บล็อกแผนใหม่** ✓ (pending expiry = 5 แท่ง)

### C. กฎ global ใน v2.04
- **Friday close + Gap = engine-level (hardcode)** ใน `run_backtest`
- **ไม่มี Trailing SL** ในไม้รวย v2.04 (field `trail_log` ว่างเปล่า)

### D. ข้อมูล / TP-SL
- SL = 40%พ่อ จาก tech_point · TP = 45%พ่อ (มี variant: round2, tp30%)
- reject ถ้า TP < 200 pip · lot ปรับ: แม่ 17-25%→÷2, พ่อ>90%R55→×2

---

## 7. ⚠️ ตรรกะกลยุทธ์ที่ "ปน" อยู่ใน engine v2.04 (ต้องแยกออกในระบบใหม่)

> นี่คือจุดที่ v2.04 **ขัด** กับหลักการ "simulator ซื่อสัตย์" — ต้องย้ายไปฝั่งกลยุทธ์
>
> **สถานะ: ตัดสินใจแล้ว — ย้ายทั้ง 4 ข้อไปฝั่งกลยุทธ์ทั้งหมด ✓ (engine โง่สนิท)**

1. **R:R = 1.0 TP adjustment (สำคัญสุด)** — engine เขียนทับ TP รายไม้:
   ถ้าเข้าเสียเปรียบ → `TP = entry ± SL_dist` ให้ R:R=1.0
   → ขัดหลักการตรงๆ (engine ไม่ควรยุ่ง R:R) ต้องย้ายเป็น "กฎของกลยุทธ์" (เปิด/ปิดได้)
2. **TP-proximity-cancel** — ราคาเข้าใกล้ TP ≤10%R55 → ยกเลิก pending (กฎกลยุทธ์ ฝังใน engine)
3. **Pending expiry = 5 แท่ง** — magic number ใน engine → ควรมาจาก `Signal.valid_until_bar`
4. **ไม้รวยรอบ 2** — 16-แท่ง outer limit + ต้องทิศเดียวกัน → orchestration ปนใน loop engine

**ที่เป็น mechanic ของ engine จริงๆ (เก็บไว้ได้):** SL/TP intrabar, limit fill, child-bar check,
position list, one-plan guard, Friday/Gap guard, post-SL/TP scan (เป็น analytics ล้วน ไม่กระทบผล)

---

## 8. ผลที่ตามมา — ต้องเลือกรูปแบบ interface กลยุทธ์ ↔ engine

ย้าย "ไม้รวยรอบ 2" ไปฝั่งกลยุทธ์ = กลยุทธ์ต้องรู้ว่าแผนก่อนหน้าปิดยังไง (SL ที่แท่งไหน)
→ กลยุทธ์แบบ stateless `window → Signal` เดิม **ใช้ไม่ได้แล้ว** ต้องเห็น feedback จาก engine

### ทางเลือก A — engine โง่แบบรับพารามิเตอร์ (parameterized)
- กลยุทธ์ใส่ทุกอย่างเป็น "ข้อมูล" ใน Signal: TP/SL ต่อไม้ (คำนวณเสร็จ), `expiry_bars`,
  เงื่อนไข cancel (เช่น cancel ถ้าราคาใกล้ TP ≤ X%)
- engine แค่ทำตามตัวเลข ไม่มี `if` แยกกลยุทธ์
- ✅ ใกล้ของเดิม · ❌ รอบ 2 ทำตรงๆ ไม่ได้ (ต้องการ feedback) + กฎ cancel แปลกใหม่ในอนาคต
  อาจ express ไม่พอ → logic แอบไหลกลับเข้า engine

### ทางเลือก B — engine เรียกกลยุทธ์ทุกแท่ง (stateful agent) ← *แนะนำ*
- ทุกแท่ง engine ส่ง `(bar, context)` ให้กลยุทธ์ (context = position/pending/แผนล่าสุดที่ปิด)
- กลยุทธ์คืน "คำสั่ง": `place` / `cancel` / ออก signal ใหม่
- engine แค่จัดการ order book + position + fill/exit ตามคำสั่ง — **โง่สนิทจริง**
- รอบ 2, proximity-cancel, expiry → เป็นการตัดสินใจของกลยุทธ์ล้วน ไม่มี code กลยุทธ์ใน engine เลย
- ❌ กลยุทธ์ต้องจำ state ของแผนตัวเอง (แต่รอบ 2 ก็ต้องจำอยู่แล้ว)

> เหตุผลที่แนะนำ B: เป็นทางเดียวที่ทำให้ engine โง่สนิท **และ** รองรับรอบ 2 ได้
> ซึ่งตรงกับสิ่งที่ตกลงกันไว้ ("engine ทำตาม signal เป๊ะๆ")
>
> **ตัดสินใจแล้ว: เลือกทางเลือก B (stateful agent) ✓**

---

## 9. คำถามที่ยังเปิดอยู่ (รอบสัมภาษณ์ถัดไป)

### Tier 1 — กระทบ contract โดยตรง ✅ เคลียร์แล้ว (ดู §3)
- [x] `Decision`: **ไม่มี `exit`** · **มี `modify`** (เลื่อน SL/TP) — position ปิดเองด้วย SL/TP เท่านั้น
- [x] `Context.last_closed` = แบบมินิมอล `{tag, plan_id, result, exit_bar, exit_price}` · กลยุทธ์จำแผนตัวเอง
- [x] รายงาน 2 ระดับ: ราย **ไม้** + ตาม **จุดเข้า** (`plan_id` ออกโดย engine ต่อ place-batch)

### Tier 2 — กลไกการรัน ✅ เคลียร์แล้ว
**ลำดับใน 1 แท่ง:** (1) เช็ค exit position เดิม (**SL ก่อน TP**) → (2) fill/cancel pending →
(3) `on_bar` → (4) ทำตาม Decision: cancel → modify → place → (5) child-bar check: ไม้ที่เพิ่งเปิด เช็ค SL/TP แท่งเดียวกัน **+ LIMIT ที่เพิ่ง place แท่งนี้ → ตรวจ fill บนแท่งเดียวกันด้วย**
- [x] market fill = `open` ของแท่งที่วาง · limit fill = ราคา limit เป๊ะ
- [x] **same-bar limit fill (parity v2.04):** LIMIT ที่ place ในแท่ง i ถ้าราคาแท่ง i แตะ level → fill บนแท่ง i เลย (ราคา limit เป๊ะ) แล้วเช็ค SL/TP ต่อ · เป็น mechanic (เหมือน gap fill / child-bar check §7) ไม่ใช่ logic — ตรงกับ v2.04 ที่ entry fill บนแท่งสัญญาณ
- [x] Gap ทะลุ level → fill ที่ราคา level เป๊ะ (ไม่จำลอง slippage)
- [x] SL+TP โดนแท่งเดียว → นับ SL (conservative)
- [x] `modify` มีผลตั้งแต่แท่ง i+1 (จูน intrabar ตอนพอร์ต Mountain)

### Tier 3 — guards & portfolio ✅ เคลียร์แล้ว
- [x] Friday close + Gap: engine คำนวณเป็นข้อเท็จจริงปฏิทิน → `ctx.allow_new_entry` · กลยุทธ์อ่านไปใช้เอง · เปิด/ปิดผ่าน config
- [x] Loss limit: **ไม่ทำ** (ตัดออกจากขอบเขต)
- [x] Lot: **fixed portfolio 1000 ไม่ทบต้น** · winrate-mode 0.01 = config toggle

### Tier 4 — ข้อมูล & รายงาน
- [x] CSV: `Time,Open,High,Low,Close[,Volume]` ไม่มี header · Volume จำเป็น (vol_ratio) · 1 ไฟล์ = 1 TF
- [x] timezone ข้อมูล = **UTC** (Friday close 21:00 UTC)
- [x] output: `runs/<name>/` = `trades.csv` (รายไม้) + `plans.csv` (ตามจุดเข้า, แพ้/ชนะ = net pnl รวม) + `summary.json` (สกอร์บอร์ด) · decision_log ตัด (เพิ่มกลับเป็น optional ได้)
- [x] compare runs: **ไม่ทำคำสั่งอัตโนมัติ** — เทียบด้วยมือผ่าน `summary.json` ของแต่ละรอบ
- [x] validation vs v2.04: **เลื่อนไว้** — golden = ผล Backtest v2.04 ใน Google Sheet (เอามาเทียบทีหลัง)

---

## 10. รองรับการขยายในอนาคต (ยืนยันว่าสถาปัตยกรรมรองรับ)

1. **เพิ่มไฟล์ CSV OHLC** — วางใน `data/` แล้วชี้ `--data` ได้เลย ไม่ต้องแก้โค้ด
   (format เดิม UTC · 1 ไฟล์ = 1 TF) · หรืออัปโหลดเข้า Claude project ให้ใช้ก็ได้
2. **เพิ่มกลยุทธ์ใหม่** — = เพิ่มไฟล์ `strategies/xxx.py` (subclass `Strategy`, เขียน `on_bar`)
   กำหนด entry / TP / SL / แบ่งไม้ ได้อิสระเต็มที่ · **engine / contract / report ไม่ต้องแตะ**
   ทุกกลยุทธ์ใช้ engine+report เดียวกัน → เทียบผลกันได้ตรงๆ (= เป้าหมายหลักของดีไซน์)
3. **HTML viewer (แบบ MT5)** — เป็น report layer เสริม · ทำงาน **offline** (ฝัง chart lib ในไฟล์ เช่น
   TradingView lightweight-charts): อ่าน CSV OHLC → วาดแท่งเทียน, อ่าน trades/plans → ทับสัญลักษณ์
   entry / exit / Buy-Sell limit / TP / SL + รายการจุดสัญญาณให้ค้นหา/คลิกกระโดดไปดู
   - ไม่กระทบ engine/strategy · ทำเป็น **milestone หลัง core validate** แล้ว (กัน debug ซ้อนกัน)
   - `trades.csv` ต้องเก็บ field ครบสำหรับวาด: entry/exit time+price, kind, limit price, sl, tp, dir, result (รวมไม้ที่ไม่ fill)

---

## 11. Config — สองระดับ

`configs/` เก็บ **"ค่าปรับ" (threshold + toggle) ไม่ใช่ logic**

1. **Per-strategy(-version)** — ไฟล์ละกลยุทธ์/เวอร์ชัน เช่น `mairuay_v204.yaml`
   เก็บ threshold เฉพาะกลยุทธ์ (พ่อ %, แม่ %, การแบ่งไม้, TP/SL %, R55 window) → อ่านผ่าน `self.cfg`
2. **Global / run** — ใช้ร่วมทุกกลยุทธ์ เช่น `global.yaml`
   portfolio, winrate_mode (lot 0.01), guards (Friday 21:00 UTC, gap threshold/wait)

```yaml
# configs/mairuay_v204.yaml
strategy: mai_ruay
father:  { min_pct_r55: 50, max_pct_r55: 100, max_bars: 8 }
mother:  { min_pct_father: 4, max_pct_father: 30 }
entry:   { split: true, tp_pct_father: 45, sl_pct_father: 40 }
tp_min_pip: 200
```
```yaml
# configs/global.yaml
portfolio: 1000
winrate_mode: false
guards:
  friday_close: { enable: true, close_hour_utc: 21, no_trade_min: 120 }
  gap:          { enable: true, threshold_pct_r55: 20, wait_bars: 80 }
```

รัน: `--strategy mai_ruay --config configs/mairuay_v204.yaml` (+ global)

- **ประโยชน์:** เทียบเวอร์ชันที่ต่างกันแค่ตัวเลข = code เดิม สลับแค่ config → diff config = เงื่อนไขที่เปลี่ยน
- **ข้อควรรู้:** config = knob ปรับค่า · เปลี่ยน **ตรรกะ/โครงสร้างการตัดสินใจ** ยังต้องแก้โค้ด strategy

---

## 12. ความต่างจาก v2.04 ที่ค้นพบตอนพอร์ต + `inject_compat` toggle

ตอน validate (รัน reference เทียบ engine ใหม่ทุกแท่งบน M1 จริง) พบว่า engine สะอาด ↔ v2.04
ต่างกัน **2 จุดเท่านั้น** ทั้งคู่เป็น "บั๊ก/ลำดับ" ของ v2.04 ที่ระบบใหม่จงใจทำให้ถูกต้องกว่า:

1. **child-bar inject (บั๊ก)** — v2.04: เมื่อแผนเปิดหลายไม้ในแท่งเดียว แล้วไม้ใดไม้หนึ่งชน SL/TP
   บนแท่งเปิด → ยัด hit เดียวกันให้ **ทุกไม้ในแผน** ที่แท่งถัดไป (แต่ละไม้ปิดที่ tp/sl ของตัวเอง)
   แม้ไม้นั้นไม่ได้แตะจริง (ref `cell1.py` :692-705, :970-990)
   → engine ใหม่ทำถูก: เช็ค SL/TP **ราย position** อิสระ (§9-Tier2 step 5b)
2. **cancel-before-fill (ลำดับ)** — v2.04 เช็ค expiry/proximity (ยกเลิก pending) **ก่อน** fill
   ในแท่งเดียวกัน (ref :660-687) → pending ที่ทั้งโดน proximity และราคาแตะ level บนแท่งเดียวกัน
   จะ "ถูกยกเลิก ไม่ fill" · engine ใหม่ (dumb): fill ที่ step 2 ก่อน strategy ยกเลิกที่ on_bar (step 3)
3. **gap guard global-skip (quirk)** — v2.04 `_find_gap_start` หา gap ใหญ่ **ตัวแรกในไฟล์**
   (day-boundary, threshold ผูก R55 แท่งแรก) แล้วใช้เป็นจุดเริ่มเทรด global → **ทิ้งทุกแท่งก่อน
   gap** + จัดการ gap แค่ตัวเดียว (ref :607-627, :647)
   → engine ใหม่ (correct): **ไม่มี gap guard เลย** — gap ไม่กระทบ `allow_new_entry` (เทรดทุกช่วง)
   เหลือแค่ Friday-close guard · (ดู `bt/guards.allow_new_entry_flags`)

**`inject_compat` toggle** (`configs/global.yaml: engine.inject_compat`):
- `false` (**default = correct**): ปิดราย position อิสระ + fill-then-cancel (พฤติกรรมถูกต้อง §1)
- `true` (**validate เท่านั้น**): จำลองทั้ง 2 จุดให้ตรง v2.04 เป๊ะ — โค้ดแยกใน `engine._run_inject_compat`
  คู่กับ `MaiRuay(pre_fill_cancel=True)` · **ห้ามใช้รันจริง** มีไว้พิสูจน์ว่าความต่างทั้งหมด
  มาจาก 2 จุดนี้เท่านั้น ไม่มีบั๊กอื่นซ่อน

ผล validate (M1 เต็ม 150,063 แท่ง): `inject_compat=true` ตรง `reference.run_backtest` **561/561 เป๊ะ**
(รวมไม้รอบ 2 ทั้ง 32 ไม้) · `inject_compat=false` (correct) = **595 ไม้** (ต่างจาก v2.04 ด้วย 3 จุดนี้:
inject + cancel-before-fill + ไม่มี gap guard)

---

*สเปกนี้อนุมัติแล้ว — ใช้เป็น source of truth ตอน implement · เริ่มจาก `IMPLEMENTATION_BRIEF.md`*
*⚠️ ค่าตัวเลขใน §3/§11 เป็น **ตัวอย่าง (illustrative)** — threshold จริงของไม้รวยต้องดึงจาก v2.04 (`cell1.py`)*
