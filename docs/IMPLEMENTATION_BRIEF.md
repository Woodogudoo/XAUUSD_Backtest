# Implementation Brief — XAUUSD Backtest (พร้อม implement)

> Entry point สำหรับเริ่มเขียนโค้ดใน VS Code
> อ่านคู่: `BACKTEST_ARCHITECTURE_DESIGN.md` (สเปก + สัญญา) · `STRATEGY_AUTHORING_GUIDE.md` (วิธีเขียน strategy)
> **สเปกอนุมัติแล้ว — เริ่ม implement ได้เลย**

---

## เป้าหมาย
สร้าง backtest XAUUSD แบบ **engine โง่ / strategy ฉลาด** (ทางเลือก B)
รัน offline ใน terminal/VS Code · simulator แยกขาดจากตัวสร้างเงื่อนไข เพื่อเทียบเงื่อนไขได้สะอาด

> **TF:** v2.04 ที่พอร์ต = ไม้รวย **M1** → ใช้ข้อมูล **M1** สำหรับ validate
> (ไม้รวย M5 เป็นคนละเวอร์ชัน — v4.62 คนละกฎ — ไว้ทำทีหลังเป็น strategy แยก)
> โค้ดทั้งหมด TF-agnostic อยู่แล้ว (R55 = 55 แท่งเสมอ, threshold เป็น %R55) เปลี่ยน TF = แค่เปลี่ยนไฟล์ CSV

## สภาพแวดล้อม
- Python + pandas เท่านั้น · ทำงาน offline · ไม่เผื่อ Colab
- รัน: `python -m bt run --strategy mai_ruay --config configs/mairuay_v204.yaml --data data/XAUUSD_M1.csv --out runs/<name>/`

## โครงสร้าง repo
```
bt/        contract.py · data.py · engine.py · report.py · strategies/{base,mai_ruay,mountain}.py
configs/   global.yaml · mairuay_v204.yaml ...
data/      XAUUSD_M1.csv ...
runs/      ผลแต่ละรอบ
docs/      BACKTEST_ARCHITECTURE_DESIGN.md · STRATEGY_AUTHORING_GUIDE.md · IMPLEMENTATION_BRIEF.md
```

---

## กฎเหล็ก (ห้ามพลาด)
1. **engine ไม่มี logic ตัดสินใจ** — ไม่ปรับ R:R, ไม่ cancel/expire เอง, ไม่รู้จัก "รอบ 2" (ดู §1, §7 ของสเปก)
2. **ตรรกะฉลาดทุกอย่างอยู่ใน strategy** — R:R adjust, proximity-cancel, expiry, รอบ 2
3. **contract (§3) = source of truth** — ห้ามแก้ field โดยไม่อัปเดตสเปก + guide พร้อมกัน
4. **threshold จริงของไม้รวยต้องดึงจาก v2.04** (`cell1.py`: `find_father` / `validate_mother` / `calc_sl` / `calc_tp` / `analyze_bar`)
   — ค่าตัวเลขในสเปกเป็นแค่ "ตัวอย่าง" อย่าหยิบไปใช้ตรงๆ

---

## ลำดับการสร้าง (ทำทีละชิ้น verify ได้ทุกก้าว)

1. **`contract.py`** — dataclasses: `Bar, Order, Modify, Decision, Position, ClosedInfo, Context` (ไม่มี logic)
   - ✓ acceptance: import ได้ ไม่มี logic ปนเข้ามา
2. **`data.py`** — `load_csv` (`Time,Open,High,Low,Close[,Volume]`, ไม่มี header, UTC) + `calc_r55` (55 แท่ง)
   - ✓ acceptance: โหลดไฟล์ตัวอย่าง จำนวนแท่งถูก · R55 ตรงกับ v2.04
3. **`engine.py` (โครงเปล่า)** — ลูปต่อแท่งตามลำดับ §9-Tier2:
   exit (SL ก่อน TP) → fill/cancel pending → `on_bar` → apply (cancel→modify→place) → child-bar check
   + market fill = open, limit fill = level เป๊ะ, gap = level เป๊ะ, modify มีผลแท่งถัดไป, ออก `plan_id`
   - ✓ acceptance: รันด้วย strategy จิ๋ว ("BUY market ทุก 50 แท่ง, TP/SL คงที่") แล้ว trades ถูกตามที่ไล่มือ
4. **`strategies/base.py`** — `class Strategy` (`name`, `on_bar(ctx) -> Decision`)
5. **`strategies/mai_ruay.py`** — พอร์ตจาก v2.04 ทีละส่วน:
   father → mother → entries/แบ่งไม้ → TP/SL → R:R adjust → proximity-cancel → pending expiry(5) → รอบ 2(16)
   + อ่าน threshold จาก config + เคารพ `ctx.allow_new_entry` + one-plan guard
   - ✓ acceptance: ผลตรงกับ v2.04 (เทียบ Google Sheet)
6. **`report.py`** — `trades.csv` (รายไม้, field ครบสำหรับ viewer) + `plans.csv` (ตามจุดเข้า) + `summary.json`
7. **(ภายหลัง) HTML viewer แบบ MT5** — ทำหลัง core validate ผ่านแล้ว (กัน debug ซ้อนกัน)

---

## guards & portfolio (ตามที่ตกลง)
- Friday close + Gap → engine คำนวณเป็น fact ใส่ `ctx.allow_new_entry` (เปิด/ปิดผ่าน config) · strategy อ่านไปใช้
- **ไม่มี loss limit** · **fixed portfolio 1000 ไม่ทบต้น** · winrate-mode 0.01 = config toggle

## Validation
เลื่อนไว้ — golden = ผล Backtest v2.04 ใน Google Sheet · ระบบใหม่ต้องรันให้ตรง v2.04 เป๊ะก่อนเชื่อถือ

---

*ทั้ง 3 ไฟล์ใน `docs/` ส่งให้ AI assistant (Claude Code / Cursor) เป็น context ได้ทั้งชุด แล้วสั่งทำตามลำดับด้านบนทีละข้อ*
