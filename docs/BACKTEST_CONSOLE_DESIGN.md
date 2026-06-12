# Backtest Console — Design Spec (v0.1)

หน้าเว็บ local สำหรับ "ตั้งค่า + สั่งรัน backtest + ดูประวัติ" โดยไม่ต้องพิมพ์ CLI เอง
สถานะ: ออกแบบเสร็จ (มี mockup แล้ว) · ยังไม่เริ่ม build

---

## 1. เป้าหมาย
ให้ผู้ใช้ทำ 4 อย่างผ่านหน้าเว็บ:
1. ปรับค่า **ทุกค่า** ใน YAML config
2. ตั้งชื่อ folder output (`--out`)
3. เลือกไฟล์ `.csv` ที่จะ backtest (`--data`)
4. ดูประวัติการรัน + ลิงก์เปิด `viewer.html` ที่สร้างไว้แล้ว

## 2. การตัดสินใจเชิงสถาปัตยกรรม
- **Browser รัน Python เองไม่ได้** (sandbox) → ต้องมี local server เป็นตัวกลาง
- **เลือก Option A: local server เล็กๆ** (`python3 -m bt serve` → localhost:8000)
  - ยังออฟไลน์ (รันในเครื่อง) แต่ "สั่งรานผ่าน HTML" ได้จริง
  - ปฏิเสธ Option B (static command-builder) เพราะรันเอง/แก้ YAML/ลิสต์ runs ไม่ได้
- **server เป็น orchestrator เท่านั้น** — อ่าน/เขียน config, ลิสต์ไฟล์, เรียก CLI เดิม (`bt run`)
  - **ห้ามแตะ engine / strategy / contract** → parity (595/561) ไม่กระทบ
  - รันผ่าน console ต้องได้ผลเท่ากับรัน CLI ตรงๆ เป๊ะ

## 3. Components (เพิ่มใหม่ทั้งหมด ไม่แก้ของเดิม)
```
bt/server.py     ← Flask/FastAPI app
bt/console.html  ← หน้า console (generate ฟอร์มจาก YAML)
CLI:  python3 -m bt serve   ← เปิด server แล้วเข้า localhost:8000
```

### Endpoints
| method | path | หน้าที่ |
|--------|------|--------|
| GET | `/` | เสิร์ฟ console.html |
| GET | `/api/configs` | ลิสต์ `configs/*.yaml` |
| GET | `/api/configs/<id>` | อ่าน YAML 1 ไฟล์ → schema (key+ชนิด+comment) ให้ฟอร์ม |
| POST | `/api/configs` | save เป็นเวอร์ชันใหม่ (`configs/..._vNNN.yaml`) — ไม่ทับเดิม |
| GET | `/api/data` | ลิสต์ `data/*.csv` |
| GET | `/api/runs` | ลิสต์ `runs/*` + อ่าน `summary.json` แต่ละอัน |
| POST | `/api/run` | subprocess `python3 -m bt run ... --viewer` → คืนผล + path viewer |
| GET | `/runs/<name>/viewer.html` | เปิด viewer เก่า |

## 4. YAML editor (หัวใจของฟีเจอร์)
- **โชว์ทุกค่าในไฟล์** ไม่ใช่ key fields ที่เลือกมา
- **ฟอร์ม generate จาก YAML จริง** (server ส่ง schema มา) — ไม่ hardcode field
  - เพิ่ม/ลด/เปลี่ยนชื่อค่าในไฟล์ → ฟอร์มอัปเดตเอง ไม่ต้องแก้โค้ดหน้าเว็บ
- **ใช้ `ruamel.yaml` (round-trip) อ่าน/เขียน** ไม่ใช่ pyyaml
  - เก็บ comment + ลำดับเดิมไว้ครบ (กัน audit-trail provenance comment หาย)
- จัดกลุ่มตาม section (father / mother / sl-tp / pending / round2 / lot / guards / engine / run) — พับได้
- **ช่องค้นหา** (ค่าเยอะ 30+ ตัว)
- input ตามชนิด: number → ช่องเลข, bool → toggle, string → text, list → text comma (v1)
- **สลับ Form ↔ Raw YAML** (แก้ทั้งไฟล์ดิบได้)
- **Save as new** → ไฟล์เวอร์ชันใหม่เสมอ (ไม่ทับ) ตามหลัก config-per-version

## 5. UX อื่น
- output folder **auto-suggest** จาก config+TF+mode (เช่น `mairuay_v204_m1_correct`) กันทับ
- **command preview** สด — โชว์คำสั่ง CLI จริงที่จะรัน (ช่วยเรียน CLI + โปร่งใส)
- mode (correct/compat) + lot (risk/flat) เป็น toggle → map ไป `engine.inject_compat` / `winrate_mode`
- history: การ์ดต่อ run โชว์ ไม้/WR/net (สีตามได้-เสีย) + ปุ่ม "เปิด viewer →"

## 6. Layout ที่เลือก
- **Dashboard หน้าเดียว**: ซ้าย = ตั้งค่า+สั่งรัน · ขวา = ประวัติ (ไม่เอา wizard)

## 7. Deferred (เฟสหลัง)
- ปุ่ม **เทียบ 2 runs** (เปิด viewer คู่กัน correct↔compat / m1↔m5)
- list/ค่าซับซ้อนใน YAML แบบ UI เฉพาะ (v1 ใช้ text ไปก่อน)

## 8. แผน build (แยกสเต็ป)
1. `bt/server.py` + `python3 -m bt serve` — endpoints อ่านอย่างเดียวก่อน (configs/data/runs) + เสิร์ฟหน้า
2. console.html — generate ฟอร์มจาก `/api/configs/<id>` + history จาก `/api/runs`
3. wiring: POST `/api/run` (subprocess) + POST `/api/configs` (save-as-new, ruamel) + ปุ่มรัน/บันทึก

## 9. Invariant (เช็คทุกสเต็ป)
- ไม่แตะ `bt/engine.py`, `bt/strategies/*`, `bt/contract.py`
- รันผ่าน console → ผลเท่ารัน CLI เป๊ะ (สุ่มเทียบ: 595 correct / 561 compat, md5 trades/plans/summary นิ่ง)
- server เป็น additive layer ล้วน
