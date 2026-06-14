# mai_ruay_v2 — สเปคกลยุทธ์ + แผน build

> ไม้รวยฉบับสะอาด: เก็บเฉพาะแกนหลัก ตัดเงื่อนไขเสริมทั้งหมด
> strategy id: `mai_ruay_v2` · ไฟล์: `bt/strategies/mai_ruay_v2.py` (ไฟล์ใหม่ ห้ามแตะ `mai_ruay.py`)

---

## หลักการ

- ใช้ contract / Signal / engine เดิมทั้งหมด — V2 เป็นแค่ strategy layer ใหม่
- ของเก่า (`mai_ruay` / config V1) ต้องรันได้เหมือนเดิมเป๊ะ (parity)
- ทุกเงื่อนไขอ้างอิงช่วงราคา R55 (จำนวนแท่งปรับได้)

## ตัดออกทั้งหมด (อย่าพอร์ตมา)

1. ไม้รวยรอบ 2
2. การเพิ่ม lot / หาร lot
3. TP / SL ย่อย (sub-levels)
4. Buffer

---

## โครง config (`configs/mairuay_v2_default.yaml`)

```yaml
strategy: mai_ruay_v2          # tag สำหรับจับคู่ strategy↔config ใน console

general:
  r55_bars: 55                 # จำนวนแท่งย้อนหลังคำนวณ R55 (ช่วงราคาอ้างอิงของทุกเงื่อนไข)
  min_tp_pip: 50               # TP ขั้นต่ำ (pip)
  pending_max_age: 30          # อายุ pending (แท่ง)
  proximity_cancel_enabled: true   # เปิดกฎยกเลิกเมื่อใกล้ TP

father:                        # การวัดขนาดแท่งพ่อ
  count_min: 1                 # จำนวนนับแท่งพ่อ (ต่ำสุด)
  count_max: 10                # จำนวนนับแท่งพ่อ (สูงสุด)
  body_min_pct_r55: 60         # ขนาดแท่งพ่อรวมขั้นต่ำ (%R55)
  per_bar_body_min: 4          # เนื้อพ่อต่อแท่งขั้นต่ำ (%R55)
  vol_ratio_min: 1.0           # อัตราส่วน Volume ขั้นต่ำ
  vol_window: 20               # ช่วงเทียบ Volume
  doji_stop_pct: 2             # เกณฑ์ Doji หยุดสแกน (%R55)
  big_bar_pct: 10              # เกณฑ์แท่งใหญ่ (%R55)
  small_bar_allow: 2           # อนุโลมแท่งเล็กได้ (จำนวนแท่ง)
  head_trim_small_max: 2       # ตัดแท่งเล็กหัวขบวนได้สูงสุด

mother:                        # การวัดขนาดแท่งแม่
  compare_mode: r55            # r55 | father  ← เทียบกับ %R55 หรือ %ขนาดแท่งพ่อ
  body_min_pct: 2              # เนื้อแม่ขั้นต่ำ (% ตาม compare_mode)
  body_max_pct: 25             # เนื้อแม่สูงสุด (% ตาม compare_mode)

entries:                       # หัวใจ: เลือกได้กี่ไม้ / จุดไหน / เงื่อนไขพ่อ-แม่แบบไหน
  - when: { father_min_pct: 70, mother_min_pct: 10, mother_max_pct: 15 }
    legs:
      - { mode: market }                 # ไม้ 1 = MARKET ที่ open ลูก
      - { mode: mother_pct, value: 50 }  # ไม้ 2 = ครึ่งแท่งแม่
      - { mode: mother_pct, value: 0 }   # ไม้ 3 = จุดเทคนิค
  - when: { father_min_pct: 50, mother_min_pct: 15, mother_max_pct: 30 }
    legs:
      - { mode: mother_pct, value: 0 }   # เงื่อนไขอื่น → เข้าไม้เดียว (จุดเทคนิค)

tpsl:                          # Fixed % จากแท่งพ่อ (ยังไม่มีเงื่อนไข)
  tp_pct_father: 50
  sl_pct_father: 30
```

### นิยาม `entries`

- เป็น **list ของ tier** — แต่ละ tier มีเงื่อนไข `when` (พ่อใหญ่กี่ %, แม่อยู่ช่วงไหน)
- เข้าเงื่อนไข tier ไหน → เปิดไม้ตาม `legs` ของ tier นั้น (กี่ไม้ / จุดไหน)
- **leg มี 2 ชนิด:**
  - `{ mode: market }` — MARKET ที่ open แท่งลูก (เปิดทันที · เงื่อนไขเข้าอยู่ที่ tier `when{}` อย่างเดียว)
  - `{ mode: mother_pct, value: N }` — LIMIT ที่ระดับ **% ของแท่งแม่**

#### `value: N` (ช่วง `−100..100`)

ราคา leg วัดจากจุดเทคนิคโดยอ้าง "ความยาวตัวแท่งแม่":

```
mother_body_len = |open แม่ − close แม่|
leg_price       = tech_price ± (mother_body_len × value / 100)      (± ตามทิศ BUY/SELL — mirror)
                  BUY:  tech + …   ·   SELL:  tech − …
```

- `0` = **จุดเทคนิค** (close พ่อ ≈ open แม่)
- `+N` = เข้าไปในตัวแท่งแม่ `N%` — ทิศ **เข้าหาแม่** (BUY = ขึ้น / SELL = ลง)
  · `50` = ครึ่งแม่ · **cap `+100`** = ปลายแม่
- `−N` = เลย tech ออกไป `N%` (เข้า **ได้เปรียบ**) — ทิศ **ตรงข้ามแม่** (BUY = ลง / SELL = ขึ้น)
  · **floor `−100`**

> เดิม `point: tech` = `value: 0` · `point: half_mother` = `value: 50` (ยุบเข้าระบบ % แล้ว — ลบ `point` เก่าทิ้ง)

---

## จับคู่ strategy ↔ config (auto)

- ทุก config มี field `strategy: <id>` บนสุด
- config V1 เดิม (`mairuay_v204.yaml` ฯลฯ) → เพิ่ม `strategy: mai_ruay`
- console: เลือก strategy ใน dropdown → กรอง config list เหลือเฉพาะตัวที่ `strategy` ตรง
- กันรัน config ผิด strategy (key คนละชุด)

---

## แผน build (ทำทีละส่วน · commit แยก · verify ก่อนไปต่อ)

### ส่วน 1 — strategy logic (CLI ก่อน)
สร้าง `bt/strategies/mai_ruay_v2.py` + `configs/mairuay_v2_default.yaml` → รันผ่าน CLI ได้
- verify: รัน V2 ออก trades/plans/summary · entries เลือกไม้ถูกตาม `when` · TP/SL = %พ่อ ·
  `compare_mode` r55 vs father ต่างกันตามคาด · V1 เดิม md5 นิ่ง · tests เขียว
- commit: `Add mai_ruay_v2 strategy logic + default config (CLI)`

### ส่วน 2 — strategy registry + console dropdown
- registry ให้ `--strategy` รับชื่อ → map class (เก่า + ใหม่) · `GET /api/strategies`
- console เพิ่ม dropdown เลือก strategy (เก่า+ใหม่โผล่ครบ) · command preview ใส่ `--strategy` ถูก
- commit: `Strategy registry + console strategy dropdown`

### ส่วน 3 — config filtering ตาม strategy
- tag config V1 ด้วย `strategy: mai_ruay` · `/api/configs` คืน field `strategy`
- console: เปลี่ยน strategy → กรอง config list อัตโนมัติ
- `field_labels.yaml`: เพิ่ม label key ใหม่ (general/father/mother/tpsl) ·
  `entries` (nested) แก้ผ่าน Raw YAML mode ไปก่อน (custom UI ทีหลัง)
- commit: `Filter configs by selected strategy`

---

## Invariants (ทุกส่วน)

- ห้ามแตะ `engine` / `contract` / `mai_ruay.py` เดิม
- ผล V1 (trades/plans/summary) md5 ต้องนิ่ง · tests เขียวทั้ง 6
- เริ่มส่วน 1 ให้รัน CLI ผ่านก่อน แล้วค่อยต่อ console (แยกชั้น debug)
