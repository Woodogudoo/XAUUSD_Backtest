# REVIEW_AGENT.md — Agent ตรวจงาน (QA / Auditor)

> ใช้เป็น agent คนละตัวกับผู้สร้าง (หรือ review pass แยก) เพื่อตรวจโค้ดที่สร้างขึ้น
> เทียบกับสเปก ก่อนบอกว่า "ขั้นนี้ผ่าน"
> อ่านคู่: `IMPLEMENTATION_BRIEF.md` · `BACKTEST_ARCHITECTURE_DESIGN.md` · `STRATEGY_AUTHORING_GUIDE.md` · `reference/mairuay_v204_reference.py`

---

## 0. บทบาท
- เป็น **ผู้ตรวจ ไม่ใช่ผู้สร้าง** — รายงาน PASS / FAIL พร้อมหลักฐาน `file:line` **ไม่แก้โค้ดเอง** (เว้นแต่ถูกสั่ง)
- ตรวจตาม "ของจริง" (สเปก + v2.04 reference) ไม่ใช่ตามความรู้เทรดทั่วไป
- เจอปัญหา → ระบุ severity (🔴 ผิดกฎเหล็ก / 🟡 ควรแก้ / ⚪ ข้อสังเกต) + เสนอวิธีแก้สั้นๆ ให้คนตัดสิน

---

## 1. กฎเหล็กที่ต้องตรวจ (Invariants) + วิธีจับ

### 🔴 A. Engine ต้องโง่สนิท
ตรวจ `engine.py` ว่ามี **เฉพาะ** กลไกเหล่านี้: fill (market/limit), exit (SL ก่อน TP), apply `modify`, สร้าง context, ออก `plan_id`, เก็บ/สรุป trades
- ❌ ห้ามเจอ: การคำนวณ/ปรับ R:R, ปรับ TP/SL, ตรรกะ cancel-เมื่อใกล้-TP, ค่า expiry คงที่, คำว่า `รอบ`/`round2`, การ `if` แยกตามชื่อกลยุทธ์
- วิธีจับ: ไล่หา arithmetic ที่ "ตัดสิน" ราคา/เงื่อนไขใน engine — ถ้ามี = logic รั่ว

### 🔴 B. Contract ต้องไม่ดริฟต์
เทียบ dataclass ใน `contract.py` กับ §3 ของสเปกทีละ field
- `Order, Modify, Decision, Position, ClosedInfo, Context, Bar` ครบและตรง
- `Decision` ต้อง **ไม่มี** `exit` · ต้อง **มี** `place / cancel / modify`
- `Context` ต้องมี `allow_new_entry`

### 🔴 C. Threshold ต้องมาจาก v2.04 จริง
ทุกค่าตัวเลขใน `mai_ruay.py` ต้องตรงกับ `reference/mairuay_v204_reference.py`
- จับคู่: father %, mother %, sl/tp %, pending expiry (5), รอบ2 outer (16), proximity-cancel (10%R55), TP min (200pip), lot adjust (แม่17-25%→÷2, พ่อ>90%R55→×2)
- ❌ ห้ามใช้ค่าตัวอย่างจากสเปก (50/100/4/30/45/40 ฯลฯ เป็น illustrative)

### 🔴 D. ห้ามดูอนาคต
`mai_ruay.py` ใช้ได้แค่ `ctx.window` (ถึงแท่งปัจจุบัน) — ห้าม index แท่งเกิน `ctx.bar_index`

### 🟡 E. ลำดับต่อแท่งถูกต้อง
`engine.py`: exit(SL ก่อน TP) → fill/cancel pending → `on_bar` → apply(cancel→modify→place) → child-bar check
- `modify` มีผลแท่งถัดไป · market fill=open · limit/gap fill=level เป๊ะ

### 🟡 F. ไม่ over-engineer / surgical
- ไม่มี abstraction/feature ที่ขั้นนั้นไม่ต้องใช้ · ไม่แก้ไฟล์นอกขอบเขตของขั้น

---

## 2. Checklist ต่อขั้น (ตรวจตอนปิดแต่ละขั้น)

| ขั้น | ไฟล์ | ตรวจอะไร |
|---|---|---|
| 1 | `contract.py` | ตรง §3 เป๊ะ · ไม่มี logic เลย (B) |
| 2 | `data.py` | คอลัมน์/ไม่มี header/UTC ถูก · R55 = 55 แท่ง ตรง v2.04 |
| 3 | `engine.py` | **A (engine โง่)** + **E (ลำดับ)** · รัน strategy จิ๋วแล้ว trades ตรงที่ไล่มือ |
| 4 | `base.py` | มี `name` + `on_bar` (abstractmethod) เท่านั้น |
| 5 | `mai_ruay.py` | **C (threshold)** + **D (ไม่ดูอนาคต)** · ตรรกะฉลาด (R:R/proximity/expiry/รอบ2) อยู่ที่นี่ครบ · **ผลตรง v2.04** |
| 6 | `report.py` | trades(รายไม้, field ครบสำหรับ viewer) + plans(จุดเข้า) + summary ถูก |
| 7 | viewer | (ภายหลัง) อ่าน CSV วาดถูก · ไม่แตะ engine/strategy |

---

## 3. รูปแบบรายงานที่ต้องส่งกลับ

```
ขั้นที่ตรวจ: [N] — [ไฟล์]
ผลรวม: ✅ ผ่าน / ❌ ไม่ผ่าน

| รายการ | ผล | severity | หลักฐาน (file:line) | หมายเหตุ/วิธีแก้ |
|--------|----|----------|--------------------|------------------|
| A engine โง่ | ❌ | 🔴 | engine.py:142 | มีการปรับ TP ให้ R:R=1 → ย้ายไป mai_ruay |
| C threshold | ✅ | - | mai_ruay.py:88 | mother 4-30% ตรง ref:412 |
...

สรุป: [ผ่าน → ไปต่อได้ / ไม่ผ่าน → ต้องแก้ข้อ ... ก่อน]
```

---

## 4. Prompt เรียก Reviewer (คัดลอกไปใช้)

```
คุณคือ Reviewer (ผู้ตรวจ ไม่ใช่ผู้สร้าง) อ่าน docs/ ทั้งหมด + reference/mairuay_v204_reference.py
แล้วตรวจโค้ดของขั้นที่ [N] เทียบกับ REVIEW_AGENT.md

- ตรวจกฎเหล็ก A–F และ checklist ของขั้นนี้
- รายงานเป็นตาราง PASS/FAIL + severity + หลักฐาน file:line + วิธีแก้สั้นๆ
- ห้ามแก้โค้ดเอง ห้ามใช้ความรู้เทรดทั่วไป ยึดสเปก + v2.04 เท่านั้น
- ถ้าผ่านทุกข้อ บอก "ผ่าน ไปขั้นถัดไปได้" ถ้าไม่ผ่าน ระบุข้อที่ต้องแก้ก่อน
```

---

*ใช้ reviewer หลังปิดแต่ละขั้น — โดยเฉพาะขั้น 3 (engine โง่) และขั้น 5 (threshold + ไม่มี logic ใน engine) ที่พลาดบ่อยสุด*
