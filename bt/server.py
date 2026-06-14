# ============================================================================
# bt/server.py — Backtest Console (local web server)
# ----------------------------------------------------------------------------
# orchestrator layer ล้วน (ADDITIVE) — ไม่แตะ engine/strategy/contract/data/report/viewer
# สเปค: docs/BACKTEST_CONSOLE_DESIGN.md · mockup: docs/backtest_console_mockup_v2.html
#
# สเต็ป 1 = "อ่าน + เสิร์ฟ" เท่านั้น (ยังไม่ run/save):
#   GET /                        เสิร์ฟ console.html
#   GET /api/configs             ลิสต์ configs/*.yaml
#   GET /api/configs/<id>        อ่าน YAML (ruamel round-trip) → tree + comment + raw
#   GET /api/data                ลิสต์ data/*.csv
#   GET /api/runs                ลิสต์ runs/* + summary.json แต่ละอัน
#   GET /runs/<name>/viewer.html เสิร์ฟ viewer.html เก่า (static)
#
# ความปลอดภัย: bind 127.0.0.1 เท่านั้น (local, ไม่ expose ออก net) · sanitize ชื่อไฟล์ (กัน ../)
#
# deps: ใช้ stdlib http.server (ไม่มี framework dep — เบากว่า Flask, รันได้ทันที)
#       อ่าน YAML แบบเก็บ comment ต้องลง:  pip install ruamel.yaml
#       (ถ้าไม่มี ruamel: server ยังเปิดได้ · /api/configs/<id> คืน error พร้อมวิธีติดตั้ง)
# ============================================================================
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONSOLE_HTML = os.path.join(_HERE, "console.html")
_COMPARE_HTML = os.path.join(_HERE, "compare.html")

# ฐานโปรเจกต์ = cwd ที่สั่ง `python3 -m bt serve` (เหมือน bt run ที่อ้าง path สัมพัทธ์)
def _root() -> str:
    return os.getcwd()


def _safe_name(name: str) -> str | None:
    """อนุญาตเฉพาะชื่อไฟล์/โฟลเดอร์ชั้นเดียว (กัน path traversal) — ไม่มี / \\ .. หรือ null"""
    name = unquote(name or "")
    if not name or "/" in name or "\\" in name or ".." in name or "\x00" in name:
        return None
    return name


# ---------- YAML → tree (ruamel round-trip, เก็บ inline comment) ----------
def _eol_comment(cmap, key) -> str | None:
    """ดึง comment ท้ายบรรทัด (# ...) ของ key ใน CommentedMap"""
    ca = getattr(cmap, "ca", None)
    if ca is None:
        return None
    item = ca.items.get(key)
    if item and len(item) > 2 and item[2] is not None:
        # ruamel มัก fold block-comment ของ key ถัดไปเข้ามาท้าย token เดียวกัน
        # → เอาเฉพาะบรรทัดแรก (comment ที่อยู่บรรทัดเดียวกับ key จริงๆ)
        first = item[2].value.split("\n", 1)[0].lstrip()
        if first.startswith("#"):
            first = first[1:]
        first = first.strip()
        return first or None
    return None


def _scalar(v):
    """ruamel scalar (ScalarInt/Float/Bool/str) → plain JSON-safe + ชื่อชนิด"""
    if isinstance(v, bool):
        return "bool", bool(v)
    if isinstance(v, int):
        return "int", int(v)
    if isinstance(v, float):
        return "float", float(v)
    if v is None:
        return "null", None
    return "str", str(v)


def _plain(v):
    """ruamel CommentedMap/Seq → plain dict/list/scalar (JSON-safe) — สำหรับส่งโครงซ้อน (entries) ให้ editor"""
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return _scalar(v)[1]


def _load_labels() -> dict:
    """อ่าน configs/field_labels.yaml (dotted-key → label/unit/help ภาษาคน)
    — เสริมคำอธิบายในฟอร์ม · ไม่มีไฟล์/พังก็ {} (ฟอร์ม fallback raw key + comment)"""
    p = os.path.join(_root(), "configs", "field_labels.yaml")
    if not os.path.isfile(p):
        return {}
    try:
        import yaml  # pyyaml — labels เป็น data ล้วน ไม่ต้อง round-trip
        with open(p, encoding="utf-8") as f:
            d = yaml.safe_load(f) or {}
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001 — label เสริมเท่านั้น ห้ามทำ endpoint ล่ม
        return {}


def _build_tree(cmap, labels: dict, prefix: str = "") -> dict:
    """แปลง CommentedMap → nested {key: node} · node = map/list/scalar + comment
    + merge label/unit/help จาก labels (key = dotted path เต็ม) ถ้ามี · generate จาก YAML จริง"""
    out = {}
    for k in cmap:
        v = cmap[k]
        cmt = _eol_comment(cmap, k)
        ks = str(k)
        full = prefix + ks
        if isinstance(v, dict):
            node = {"type": "map", "comment": cmt, "items": _build_tree(v, labels, full + ".")}
        elif isinstance(v, list):
            if any(isinstance(x, (dict, list)) for x in v):   # list ซ้อน (entries) → custom editor/Raw
                node = {"type": "list", "comment": cmt, "nested": True,
                        "summary": f"{len(v)} รายการ (ซ้อนหลายชั้น)", "value": [],
                        "data": _plain(v)}     # โครงจริง → ให้ editor ฝั่ง console สร้างการ์ด
            else:
                node = {"type": "list", "comment": cmt, "value": [_scalar(x)[1] for x in v]}
        else:
            t, val = _scalar(v)
            node = {"type": t, "comment": cmt, "value": val}
        lab = labels.get(full)
        if isinstance(lab, dict):
            for f in ("label", "unit", "help", "options"):
                if lab.get(f):
                    node[f] = lab[f]
        out[ks] = node
    return out


def _read_config(path: str) -> dict:
    """อ่าน 1 ไฟล์ YAML ด้วย ruamel (round-trip) → {raw, tree} · tree merge label ภาษาคน"""
    from ruamel.yaml import YAML  # lazy: serve อื่นๆ ยังทำงานได้แม้ไม่มี ruamel
    yaml = YAML()
    yaml.preserve_quotes = True
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    data = yaml.load(raw)
    labels = _load_labels()
    tree = _build_tree(data, labels) if data is not None else {}
    return {"raw": raw, "tree": tree}


# ---------- endpoint handlers (คืน (status, payload)) ----------
def api_strategies() -> tuple[int, dict]:
    """list strategy id ที่ลงทะเบียน (registry) — สำหรับ dropdown เลือกกลยุทธ์ในคอนโซล"""
    try:
        from bt.strategies.registry import strategy_ids   # lazy: เลี่ยง pull deps ตอน import server
        return 200, {"strategies": strategy_ids()}
    except Exception as e:  # noqa: BLE001 — endpoint เสริม ห้ามทำ server ล่ม
        return 500, {"error": f"โหลด registry ไม่ได้: {e}"}


def _config_strategy(path: str) -> str | None:
    """อ่าน field strategy (top-level) ของ config — ไม่มี/พัง → None (console โชว์แต่ mark 'ไม่มี tag')"""
    try:
        import yaml  # config = data ล้วน ไม่ต้อง round-trip
        with open(path, encoding="utf-8") as f:
            d = yaml.safe_load(f) or {}
        s = d.get("strategy") if isinstance(d, dict) else None
        return str(s) if s else None
    except Exception:  # noqa: BLE001 — กรอง config เสริมเท่านั้น ห้ามทำ endpoint ล่ม
        return None


def api_configs() -> tuple[int, dict]:
    d = os.path.join(_root(), "configs")
    # exclude hidden (.xxx) และ .trash/ — ไม่ให้ trashed โผล่เป็น config ที่เลือกได้
    files = sorted(f for f in os.listdir(d)
                   if not f.startswith(".") and f.endswith((".yaml", ".yml"))) \
        if os.path.isdir(d) else []
    # strategy tag ต่อไฟล์ (สำหรับกรอง config ตาม strategy ในคอนโซล) — core configs ข้าม
    strategies = {f: _config_strategy(os.path.join(d, f))
                  for f in files if f not in _CORE_CONFIGS}
    return 200, {"configs": files, "core": sorted(_CORE_CONFIGS), "strategies": strategies}


# ---------- config management: rename · soft-delete (trash) · restore · hard-delete ----------
_CORE_CONFIGS = {"global.yaml", "field_labels.yaml"}   # ห้าม rename/ลบ


def _configs_dir() -> str:
    return os.path.realpath(os.path.join(_root(), "configs"))


def _trash_dir() -> str:
    return os.path.join(_configs_dir(), ".trash")


def _under(path: str, base: str) -> bool:
    rp = os.path.realpath(path)
    return rp == os.path.realpath(base) or rp.startswith(os.path.realpath(base) + os.sep)


def _valid_new_name(new: str) -> str | None:
    """ตรวจชื่อ config ใหม่ — เติม .yaml ถ้าไม่มี · charset + no traversal · คืนชื่อหรือ None"""
    new = (new or "").strip()
    if not new:
        return None
    if not (new.endswith(".yaml") or new.endswith(".yml")):
        new += ".yaml"
    if "/" in new or "\\" in new or ".." in new or "\x00" in new:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", new):
        return None
    return new


def api_config_rename(name: str, payload: dict) -> tuple[int, dict]:
    safe = _safe_name(name)
    if safe is None:
        return 400, {"error": "ชื่อ config ไม่ถูกต้อง (ห้าม / .. absolute)"}
    if safe in _CORE_CONFIGS:
        return 403, {"error": f"{safe} เป็นไฟล์ระบบ — rename ไม่ได้"}
    src = os.path.join(_configs_dir(), safe)
    if not _under(src, _configs_dir()) or _under(src, _trash_dir()):
        return 400, {"error": "อยู่นอก configs/"}
    if not os.path.isfile(src):
        return 404, {"error": f"ไม่พบ config: {safe}"}
    new = _valid_new_name(payload.get("new_name", ""))
    if new is None:
        return 400, {"error": "ชื่อใหม่ใช้ได้แค่ A-Z a-z 0-9 _ . - และลงท้าย .yaml"}
    if new in _CORE_CONFIGS:
        return 403, {"error": f"{new} เป็นชื่อไฟล์ระบบ — ใช้ไม่ได้"}
    dest = os.path.join(_configs_dir(), new)
    if not _under(dest, _configs_dir()) or _under(dest, _trash_dir()):
        return 400, {"error": "ปลายทางอยู่นอก configs/"}
    if os.path.exists(dest):
        return 409, {"error": f"มีไฟล์ {new} อยู่แล้ว — เปลี่ยนชื่ออื่น"}
    try:
        os.rename(src, dest)
    except Exception as e:  # noqa: BLE001
        return 500, {"error": f"rename ไม่สำเร็จ: {e}"}
    return 200, {"ok": True, "old": safe, "new": new}


def api_config_softdelete(name: str) -> tuple[int, dict]:
    """soft-delete (ชั้น 1) — ย้ายไป configs/.trash/ (ไม่ unlink)"""
    safe = _safe_name(name)
    if safe is None:
        return 400, {"error": "ชื่อ config ไม่ถูกต้อง (ห้าม / .. absolute)"}
    if safe in _CORE_CONFIGS:
        return 403, {"error": f"{safe} เป็นไฟล์ระบบ — ลบไม่ได้"}
    src = os.path.join(_configs_dir(), safe)
    if not _under(src, _configs_dir()) or _under(src, _trash_dir()):
        return 400, {"error": "อยู่นอก configs/"}
    if not os.path.isfile(src):
        return 404, {"error": f"ไม่พบ config: {safe}"}
    os.makedirs(_trash_dir(), exist_ok=True)
    dest = os.path.join(_trash_dir(), safe)
    if os.path.exists(dest):   # ชนชื่อในถัง → ต่อท้าย __<ts> กันทับ
        base, ext = os.path.splitext(safe)
        dest = os.path.join(_trash_dir(), f"{base}__{int(time.time())}{ext}")
        while os.path.exists(dest):
            dest = os.path.join(_trash_dir(), f"{base}__{int(time.time()*1000)}{ext}")
    try:
        shutil.move(src, dest)
    except Exception as e:  # noqa: BLE001
        return 500, {"error": f"ย้ายลงถังขยะไม่สำเร็จ: {e}"}
    return 200, {"ok": True, "trashed": os.path.basename(dest)}


def api_trash_list() -> tuple[int, dict]:
    td = _trash_dir()
    items = []
    if os.path.isdir(td):
        for f in os.listdir(td):
            p = os.path.join(td, f)
            if os.path.isfile(p) and f.endswith((".yaml", ".yml")) and not f.startswith("."):
                items.append({"name": f, "size": os.path.getsize(p),
                              "mtime": int(os.path.getmtime(p))})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return 200, {"trash": items}


def api_config_restore(name: str) -> tuple[int, dict]:
    safe = _safe_name(name)
    if safe is None:
        return 400, {"error": "ชื่อไม่ถูกต้อง (ห้าม / .. absolute)"}
    src = os.path.join(_trash_dir(), safe)
    if not _under(src, _trash_dir()):
        return 400, {"error": "อยู่นอก configs/.trash/"}
    if not os.path.isfile(src):
        return 404, {"error": f"ไม่พบในถังขยะ: {safe}"}
    dest = os.path.join(_configs_dir(), safe)
    if not _under(dest, _configs_dir()) or _under(dest, _trash_dir()):
        return 400, {"error": "ปลายทางอยู่นอก configs/"}
    if os.path.exists(dest):
        return 409, {"error": f"ชื่อ {safe} มีอยู่แล้วใน configs/ — เปลี่ยนชื่อก่อน restore"}
    try:
        shutil.move(src, dest)
    except Exception as e:  # noqa: BLE001
        return 500, {"error": f"restore ไม่สำเร็จ: {e}"}
    return 200, {"ok": True, "restored": safe}


def api_trash_delete(name: str) -> tuple[int, dict]:
    """ลบถาวร (ชั้น 2) — hard unlink เฉพาะใน configs/.trash/"""
    safe = _safe_name(name)
    if safe is None:
        return 400, {"error": "ชื่อไม่ถูกต้อง (ห้าม / .. absolute)"}
    target = os.path.realpath(os.path.join(_trash_dir(), safe))
    if not _under(target, _trash_dir()) or target == os.path.realpath(_trash_dir()):
        return 400, {"error": "ลบถาวรได้เฉพาะไฟล์ใน configs/.trash/ เท่านั้น"}
    if not os.path.isfile(target):
        return 404, {"error": f"ไม่พบในถังขยะ: {safe}"}
    try:
        os.remove(target)
    except Exception as e:  # noqa: BLE001
        return 500, {"error": f"ลบถาวรไม่สำเร็จ: {e}"}
    return 200, {"ok": True, "deleted": safe}


def api_config_one(cid: str) -> tuple[int, dict]:
    name = _safe_name(cid)
    if name is None:
        return 400, {"error": "ชื่อไม่ถูกต้อง"}
    # รับทั้ง "mairuay_v204" และ "mairuay_v204.yaml"
    base = name[:-5] if name.endswith((".yaml", ".yml")) else name
    cdir = os.path.join(_root(), "configs")
    path = None
    for ext in (".yaml", ".yml"):
        p = os.path.join(cdir, base + ext)
        if os.path.isfile(p):
            path = p
            break
    if path is None:
        return 404, {"error": f"ไม่พบ config: {base}"}
    try:
        body = _read_config(path)
    except ModuleNotFoundError:
        return 501, {"error": "ต้องติดตั้ง ruamel.yaml ก่อน: pip install ruamel.yaml"}
    except Exception as e:  # noqa: BLE001 — รายงาน error ให้ฝั่งหน้าเว็บ
        return 500, {"error": f"อ่าน YAML ไม่ได้: {e}"}
    body["id"] = base
    body["file"] = os.path.relpath(path, _root())
    return 200, body


def api_data() -> tuple[int, dict]:
    d = os.path.join(_root(), "data")
    files = sorted(f for f in os.listdir(d) if f.endswith(".csv")) \
        if os.path.isdir(d) else []
    return 200, {"data": files}


_TF_RE = re.compile(r"(?:^|[_\-./ ])([MHDmhd]\d{1,3})(?:[_\-./ ]|$)")


def _tf_of(s: str):
    """หา timeframe token (M1/M5/M15/H1/D1…) จากชื่อ data/ชื่อ run · ไม่เจอ → None"""
    m = _TF_RE.search(s or "")
    return m.group(1).upper() if m else None


def _run_tf(rdir: str, name: str):
    """TF ของ run: จาก config_used.yaml (_run.data) ก่อน · ไม่มี → derive จากชื่อ run"""
    cu = os.path.join(rdir, "config_used.yaml")
    if os.path.isfile(cu):
        try:
            import yaml
            c = yaml.safe_load(open(cu, encoding="utf-8")) or {}
            data = (c.get("_run") or {}).get("data") if isinstance(c, dict) else None
            if data:
                tf = _tf_of(os.path.basename(str(data)))
                if tf:
                    return tf
        except Exception:  # noqa: BLE001
            pass
    return _tf_of(name)


def api_runs() -> tuple[int, dict]:
    base = os.path.join(_root(), "runs")
    runs = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            rdir = os.path.join(base, name)
            if not os.path.isdir(rdir):
                continue
            summary = None
            sp = os.path.join(rdir, "summary.json")
            if os.path.isfile(sp):
                try:
                    with open(sp, encoding="utf-8") as f:
                        summary = json.load(f)
                except Exception:  # noqa: BLE001 — summary เสีย → ข้าม (ยังลิสต์ run ได้)
                    summary = None
            runs.append({
                "name": name,
                "summary": summary,
                "has_viewer": os.path.isfile(os.path.join(rdir, "viewer.html")),
                "mtime": int(os.path.getmtime(rdir)),
                "tf": _run_tf(rdir, name),   # timeframe — สำหรับ filter ในประวัติ
            })
    runs.sort(key=lambda r: r["mtime"], reverse=True)
    return 200, {"runs": runs}


def api_delete_run(name: str) -> tuple[int, dict]:
    """ลบ folder ใต้ runs/ เท่านั้น — scope เข้มงวด (กันหลุดออกนอก runs/)"""
    safe = _safe_name(name)                       # guard เดียวกับ step 1: no / \\ .. null
    if safe is None:
        return 400, {"error": "ชื่อ run ไม่ถูกต้อง (ห้าม / .. หรือ absolute path)"}
    runs_root = os.path.realpath(os.path.join(_root(), "runs"))
    target = os.path.realpath(os.path.join(runs_root, safe))
    # realpath แล้วต้องอยู่ "ใต้ runs/ จริง" (เผื่อ symlink/หลุดออกนอก) และไม่ใช่ runs/ เอง
    if target == runs_root or not target.startswith(runs_root + os.sep):
        return 400, {"error": "ลบได้เฉพาะ folder ภายใต้ runs/ เท่านั้น"}
    if not os.path.isdir(target):
        return 404, {"error": f"ไม่พบ run: {safe}"}
    try:
        shutil.rmtree(target)
    except Exception as e:  # noqa: BLE001 — รายงาน error ให้ฝั่งหน้าเว็บ
        return 500, {"error": f"ลบไม่สำเร็จ: {e}"}
    return 200, {"ok": True, "deleted": safe}


# ---------- GET: dashboard metrics (pure, read-only) ----------
def api_metrics(name: str) -> tuple[int, dict]:
    """คำนวณ metrics ของ run — scope runs/ เท่านั้น · ไม่มี trades.csv → 404"""
    safe = _safe_name(name)
    if safe is None:
        return 400, {"error": "ชื่อ run ไม่ถูกต้อง (ห้าม / .. หรือ absolute path)"}
    runs_root = os.path.realpath(os.path.join(_root(), "runs"))
    run_dir = os.path.realpath(os.path.join(runs_root, safe))
    if run_dir == runs_root or not run_dir.startswith(runs_root + os.sep):
        return 400, {"error": "อ่านได้เฉพาะ folder ภายใต้ runs/ เท่านั้น"}
    if not os.path.isfile(os.path.join(run_dir, "trades.csv")):
        return 404, {"error": f"ไม่พบ trades.csv ใน run: {safe}"}
    try:
        from bt import metrics   # lazy import (เหมือน endpoint อื่น)
        return 200, metrics.compute_metrics(run_dir)
    except Exception as e:  # noqa: BLE001
        return 500, {"error": f"คำนวณ metrics ไม่ได้: {e}"}


def api_metric_defs() -> tuple[int, dict]:
    """metadata ของทุก metric (label/calc/fmt/direction/group/order) — single source of truth"""
    from bt import metrics
    return 200, {"metric_defs": metrics.METRIC_DEFS}


def _flatten(d: dict) -> dict:
    """nested dict → {dotted_key: value} (leaf เท่านั้น)"""
    out: dict = {}

    def _go(node, prefix=""):
        for k, v in node.items():
            key = f"{prefix}{k}"
            if isinstance(v, dict):
                _go(v, key + ".")
            else:
                out[key] = v
    if isinstance(d, dict):
        _go(d)
    return out


def api_run_config(name: str) -> tuple[int, dict]:
    """snapshot config ที่ใช้รัน (runs/<name>/config_used.yaml) → flat {dotted_key: value}
    + labels จาก field_labels — สำหรับ config diff หน้าเทียบ · ไม่มี snapshot → 404
    fallback: ไม่มี snapshot แต่ summary.json เก็บ config/global path → อ่าน+merge ใหม่"""
    safe = _safe_name(name)
    if safe is None:
        return 400, {"error": "ชื่อ run ไม่ถูกต้อง (ห้าม / .. หรือ absolute path)"}
    runs_root = os.path.realpath(os.path.join(_root(), "runs"))
    run_dir = os.path.realpath(os.path.join(runs_root, safe))
    if run_dir == runs_root or not run_dir.startswith(runs_root + os.sep):
        return 400, {"error": "อ่านได้เฉพาะ folder ภายใต้ runs/ เท่านั้น"}

    labels = {k: (lab.get("label") if isinstance(lab, dict) else None)
              for k, lab in _load_labels().items()}
    p = os.path.join(run_dir, "config_used.yaml")
    import yaml

    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:  # noqa: BLE001
            return 500, {"error": f"อ่าน config_used ไม่ได้: {e}"}
        return 200, {"exists": True, "values": _flatten(data), "labels": labels}

    # fallback: run เก่าไม่มี snapshot แต่ summary เก็บ config path → re-read + merge
    sp = os.path.join(run_dir, "summary.json")
    if os.path.isfile(sp):
        try:
            with open(sp, encoding="utf-8") as f:
                summ = json.load(f)
            cfg_path = summ.get("config") or summ.get("config_used")
            if cfg_path:
                cp = os.path.join(_root(), cfg_path)
                if os.path.isfile(cp):
                    with open(cp, encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                    return 200, {"exists": True, "values": _flatten(data),
                                 "labels": labels, "from": "summary-fallback"}
        except Exception:  # noqa: BLE001
            pass

    return 404, {"error": f"ไม่พบ config snapshot ของ run นี้ (config_used.yaml) — รันใหม่เพื่อบันทึก",
                 "exists": False}


# ---------- POST: save config (ruamel round-trip) ----------


# ---------- POST: save config (ruamel round-trip) ----------
_NAME_RE = re.compile(r"[a-z0-9_-]+\.ya?ml")      # ชื่อไฟล์ config ใหม่
_OUT_RE  = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")  # ชื่อ folder out (ไม่มี / .. ช่องว่าง · ไม่ขึ้นต้น -/.)
_STRAT_RE = re.compile(r"[a-z0-9_]+")


def _coerce(cur, raw):
    """แปลงค่าจากฟอร์ม (string/bool) → ชนิดเดิมของ base config (กัน type เพี้ยน)"""
    if isinstance(cur, bool):
        return raw is True or str(raw).strip().lower() in ("true", "1", "yes")
    if isinstance(cur, int) and not isinstance(cur, bool):
        return int(float(raw))
    if isinstance(cur, float):
        return float(raw)
    if isinstance(cur, list):
        if isinstance(raw, list):
            return raw
        return [x.strip() for x in str(raw).split(",") if x.strip()]
    if cur is None:
        s = str(raw)
        return s if s != "" else None
    return str(raw)


def _is_structural(v) -> bool:
    """โครงซ้อน (map / list-of-map-or-list เช่น entries) ที่ฟอร์มแบนแก้ไม่ได้ — ห้ามทับด้วยค่าแบน"""
    if isinstance(v, dict):
        return True
    if isinstance(v, list):
        return any(isinstance(x, (dict, list)) for x in v)
    return False


def _set_path(root, dotted: str, raw) -> bool:
    """เซ็ตค่า leaf ตาม dotted path บน CommentedMap เดิม (key ต้องมีอยู่แล้ว — ไม่สร้างใหม่)
    → comment/ลำดับ/โครงสร้างเดิมอยู่ครบ (อัปเดตเฉพาะค่า)
    *** ข้าม node ที่เป็นโครงซ้อน (entries ฯลฯ) — กันฟอร์มแบนทับโครงหาย ***"""
    node = root
    parts = dotted.split(".")
    for seg in parts[:-1]:
        if not isinstance(node, dict) or seg not in node:
            return False
        node = node[seg]
    last = parts[-1]
    if not isinstance(node, dict) or last not in node:
        return False
    if _is_structural(node[last]):
        return False   # leaf-only patch — โครงซ้อนแก้ผ่าน Raw YAML เท่านั้น
    node[last] = _coerce(node[last], raw)
    return True


def _check_entries(entries) -> str | None:
    """ตรวจโครง entries จาก editor ก่อนเขียน (defense — client validate แล้วชั้นหนึ่ง) · ผิด → ข้อความ, ถูก → None"""
    if not isinstance(entries, list) or not entries:
        return "ต้องเป็น list ของ tier ที่ไม่ว่าง"
    for i, t in enumerate(entries):
        if not isinstance(t, dict) or not isinstance(t.get("when"), dict):
            return f"tier {i + 1} ต้องมี when (dict)"
        legs = t.get("legs")
        if not isinstance(legs, list) or not legs:
            return f"tier {i + 1} ต้องมี legs ≥1 ไม้"
        for j, lg in enumerate(legs):
            if not isinstance(lg, dict) or lg.get("mode") not in ("market", "mother_pct"):
                return f"tier {i + 1} ไม้ {j + 1}: mode ต้องเป็น market | mother_pct"
            if lg["mode"] == "mother_pct":
                v = lg.get("value")
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not (-100 <= v <= 100):
                    return f"tier {i + 1} ไม้ {j + 1}: value ต้องเป็นตัวเลข −100..100"
    return None


def api_save_config(payload: dict) -> tuple[int, dict]:
    base = _safe_name(payload.get("base", ""))
    name = str(payload.get("name", "")).strip()
    values = payload.get("values") or {}
    raw = payload.get("raw")     # Raw YAML mode → เขียนทั้งก้อน (แก้ entries ฯลฯ) · None = Form mode (patch base)
    entries = payload.get("entries")   # Form mode + custom editor → เขียน nested ตรงๆ (ไม่ผ่าน coerce)
    overwrite = bool(payload.get("overwrite", False))   # True = บันทึกทับไฟล์เดิม in-place · False = สร้างไฟล์ใหม่
    if base is None:
        return 400, {"error": "base config ไม่ถูกต้อง"}
    if not _NAME_RE.fullmatch(name):
        return 400, {"error": "ชื่อไฟล์ใช้ได้แค่ a-z 0-9 _ - และต้องลงท้าย .yaml (เช่น mairuay_v205.yaml)"}
    cdir = os.path.join(_root(), "configs")
    target = os.path.join(cdir, name)
    if os.path.dirname(os.path.abspath(target)) != os.path.abspath(cdir):
        return 400, {"error": "ชื่อไฟล์ไม่ถูกต้อง"}
    if overwrite:
        # บันทึกทับไฟล์เดิม — บล็อก core (global/field_labels) · ไฟล์ต้องมีอยู่จริง
        if name in _CORE_CONFIGS:
            return 403, {"error": f"{name} เป็นไฟล์ระบบ — บันทึกทับไม่ได้ (ใช้ บันทึกเป็น… ตั้งชื่อใหม่)"}
        if not os.path.isfile(target):
            return 404, {"error": f"ไม่พบไฟล์ {name} ที่จะบันทึกทับ — ใช้ บันทึกเป็น… แทน"}
    elif os.path.exists(target):
        return 409, {"error": f"มีไฟล์ {name} อยู่แล้ว — เปลี่ยนชื่อใหม่ หรือใช้ บันทึก (ทับไฟล์เดิม)"}
    # resolve base file
    bbase = base[:-5] if base.endswith((".yaml", ".yml")) else base
    bfile = None
    for ext in (".yaml", ".yml"):
        p = os.path.join(cdir, bbase + ext)
        if os.path.isfile(p):
            bfile = p
            break
    if bfile is None:
        return 404, {"error": f"ไม่พบ base config: {bbase}"}
    try:
        from ruamel.yaml import YAML
        yaml = YAML()
        yaml.preserve_quotes = True
        if isinstance(raw, str):
            # Raw YAML mode — validate ก่อนเขียนทั้งก้อน (รักษาข้อความ/คอมเมนต์ของผู้ใช้เป๊ะ)
            try:
                parsed = yaml.load(raw)
            except Exception as e:  # noqa: BLE001
                return 400, {"error": f"YAML ไม่ถูกต้อง — แก้ก่อนบันทึก: {e}"}
            if not isinstance(parsed, dict):
                return 400, {"error": "YAML ราก ต้องเป็น mapping (key: value)"}
            with open(target, "w", encoding="utf-8") as f:
                f.write(raw if raw.endswith("\n") else raw + "\n")
            applied, skipped = len(parsed), []
        else:
            # Form mode — โหลดไฟล์ base จาก disk แล้ว patch เฉพาะ leaf ที่ฟอร์มแก้ (โครงซ้อนคงเดิม)
            with open(bfile, encoding="utf-8") as f:
                data = yaml.load(f)
            applied, skipped = 0, []
            for dotted, val in values.items():
                if _set_path(data, str(dotted), val):
                    applied += 1
                else:
                    skipped.append(dotted)
            # custom editor (entries) → เขียนโครง nested ตรงๆ ทับ key เดิม (กัน bug coerce ตัด comma)
            if entries is not None:
                err = _check_entries(entries)
                if err:
                    return 400, {"error": f"entries ไม่ถูกต้อง: {err}"}
                data["entries"] = entries
                applied += 1
            with open(target, "w", encoding="utf-8") as f:
                yaml.dump(data, f)
    except ModuleNotFoundError:
        return 501, {"error": "ต้องติดตั้ง ruamel.yaml ก่อน: pip install ruamel.yaml"}
    except Exception as e:  # noqa: BLE001
        return 500, {"error": f"เขียน YAML ไม่ได้: {e}"}
    return 200, {"ok": True, "name": name, "id": name[:-5] if name.endswith((".yaml", ".yml")) else name,
                 "file": os.path.relpath(target, _root()), "applied": applied, "skipped": skipped,
                 "overwrite": overwrite}


# ---------- POST: run backtest (subprocess arg-list, shell=False) ----------
def _write_derived_global(mode: str, lot: str) -> str:
    """สร้าง global config ชั่วคราว (ruamel round-trip จาก global.yaml) ตั้ง inject_compat/winrate_mode
    — ใช้กับ --global เมื่อ mode=compat หรือ lot=flat · correct+risk ใช้ configs/global.yaml เดิม (คำสั่งเป๊ะ CLI)"""
    from ruamel.yaml import YAML
    yaml = YAML()
    yaml.preserve_quotes = True
    with open(os.path.join(_root(), "configs", "global.yaml"), encoding="utf-8") as f:
        g = yaml.load(f)
    eng = g.get("engine")
    if isinstance(eng, dict):
        eng["inject_compat"] = (mode == "compat")
    else:
        g["engine"] = {"inject_compat": mode == "compat"}
    g["winrate_mode"] = (lot == "flat")
    fd, path = tempfile.mkstemp(suffix=".yaml", prefix="global_console_")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(g, f)
    return path


def api_run(payload: dict) -> tuple[int, dict]:
    strategy = str(payload.get("strategy", "")).strip()
    config = _safe_name(payload.get("config", ""))
    data = _safe_name(payload.get("data", ""))
    out = str(payload.get("out", "")).strip()
    mode = payload.get("mode", "correct")
    lot = payload.get("lot", "risk")
    overwrite = bool(payload.get("overwrite", False))

    # --- validate ทุก param (no traversal / no shell meta) ---
    if not _STRAT_RE.fullmatch(strategy):
        return 400, {"error": "ชื่อ strategy ไม่ถูกต้อง (a-z 0-9 _ เท่านั้น)"}
    if config is None:
        return 400, {"error": "ชื่อ config ไม่ถูกต้อง"}
    if data is None:
        return 400, {"error": "ชื่อ data ไม่ถูกต้อง"}
    if not _OUT_RE.fullmatch(out):
        return 400, {"error": "ชื่อ folder ใช้ได้แค่ A-Z a-z 0-9 . _ - (ห้าม / .. ช่องว่าง อักขระพิเศษ และห้ามขึ้นต้น . หรือ -)"}
    if mode not in ("correct", "compat"):
        return 400, {"error": "mode ต้องเป็น correct หรือ compat"}
    if lot not in ("risk", "flat"):
        return 400, {"error": "lot ต้องเป็น risk หรือ flat"}

    cbase = config[:-5] if config.endswith((".yaml", ".yml")) else config
    cfg_rel = None
    for ext in (".yaml", ".yml"):
        if os.path.isfile(os.path.join(_root(), "configs", cbase + ext)):
            cfg_rel = f"configs/{cbase}{ext}"
            break
    if cfg_rel is None:
        return 404, {"error": f"ไม่พบ config: {cbase}"}
    dbase = data[:-4] if data.endswith(".csv") else data
    if not os.path.isfile(os.path.join(_root(), "data", dbase + ".csv")):
        return 404, {"error": f"ไม่พบ data: {dbase}.csv"}
    data_rel = f"data/{dbase}.csv"

    out_dir = os.path.join(_root(), "runs", out)
    if os.path.exists(out_dir) and not overwrite:
        return 409, {"error": f"โฟลเดอร์ runs/{out}/ มีอยู่แล้ว — เปลี่ยนชื่อ หรือยืนยันเขียนทับ",
                     "exists": True, "out": out}

    gpath = None
    if mode == "compat" or lot == "flat":
        try:
            gpath = _write_derived_global(mode, lot)
        except ModuleNotFoundError:
            return 501, {"error": "โหมด compat/flat ต้องติดตั้ง ruamel.yaml ก่อน: pip install ruamel.yaml"}

    cmd = [sys.executable, "-m", "bt", "run",
           "--strategy", strategy,
           "--config", cfg_rel,
           "--data", data_rel,
           "--out", f"runs/{out}/",
           "--viewer"]
    if gpath:
        cmd += ["--global", gpath]
    try:
        proc = subprocess.run(cmd, cwd=_root(), capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return 504, {"error": "รันนานเกิน 15 นาที — ยกเลิก"}
    finally:
        if gpath and os.path.exists(gpath):
            os.remove(gpath)

    if proc.returncode != 0:
        return 500, {"error": "รัน backtest ไม่สำเร็จ (ดู log)",
                     "stderr": (proc.stderr or proc.stdout or "")[-1600:], "cmd": cmd}
    # config_used.yaml เขียนโดย bt/__main__ (resolved config) — ครอบคลุมทั้ง CLI และ console run
    summary = None
    sp = os.path.join(out_dir, "summary.json")
    if os.path.isfile(sp):
        try:
            with open(sp, encoding="utf-8") as f:
                summary = json.load(f)
        except Exception:  # noqa: BLE001
            summary = None
    return 200, {"ok": True, "out": out, "summary": summary,
                 "viewer": f"/runs/{out}/viewer.html",
                 "mode": mode, "lot": lot,
                 "stdout": (proc.stdout or "")[-1400:], "cmd": cmd}


# ---------- HTTP handler ----------
class _Handler(BaseHTTPRequestHandler):
    server_version = "btconsole/0.1"
    protocol_version = "HTTP/1.1"

    def _send(self, status: int, body: bytes, ctype: str):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # local-only console → กัน cache ปนเวอร์ชันเก่า
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _file(self, path: str, ctype: str):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except FileNotFoundError:
            return self._json(404, {"error": "not found"})
        self._send(200, body, ctype)

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/" or path == "/index.html":
            if os.path.isfile(_CONSOLE_HTML):
                return self._file(_CONSOLE_HTML, "text/html; charset=utf-8")
            return self._send(200, b"<h1>btconsole</h1><p>console.html missing</p>",
                              "text/html; charset=utf-8")

        if path == "/api/strategies":
            return self._json(*api_strategies())

        if path == "/api/configs":
            return self._json(*api_configs())

        if path == "/api/configs/trash":   # ก่อน /api/configs/<id> (กัน 'trash' ถูกอ่านเป็น config)
            return self._json(*api_trash_list())

        m = re.fullmatch(r"/api/configs/([^/]+)", path)
        if m:
            return self._json(*api_config_one(m.group(1)))

        if path == "/api/data":
            return self._json(*api_data())

        if path == "/api/runs":
            return self._json(*api_runs())

        if path == "/compare":
            if os.path.isfile(_COMPARE_HTML):
                return self._file(_COMPARE_HTML, "text/html; charset=utf-8")
            return self._send(200, b"<h1>compare.html missing</h1>", "text/html; charset=utf-8")

        m = re.fullmatch(r"/api/runs/([^/]+)/metrics", path)
        if m:
            return self._json(*api_metrics(m.group(1)))

        m = re.fullmatch(r"/api/runs/([^/]+)/config", path)
        if m:
            return self._json(*api_run_config(m.group(1)))

        if path == "/api/metric-defs":
            return self._json(*api_metric_defs())

        m = re.fullmatch(r"/runs/([^/]+)/viewer\.html", path)
        if m:
            name = _safe_name(m.group(1))
            if name is None:
                return self._json(400, {"error": "ชื่อไม่ถูกต้อง"})
            vp = os.path.join(_root(), "runs", name, "viewer.html")
            return self._file(vp, "text/html; charset=utf-8")

        return self._json(404, {"error": f"ไม่พบ route: {path}"})

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(n) if n > 0 else b"{}"
            return json.loads(raw or b"{}")
        except Exception:  # noqa: BLE001
            return None

    def do_POST(self):
        path = urlparse(self.path).path
        payload = self._read_json()
        if payload is None or not isinstance(payload, dict):
            return self._json(400, {"error": "body ต้องเป็น JSON object"})
        if path == "/api/configs":
            return self._json(*api_save_config(payload))
        if path == "/api/run":
            return self._json(*api_run(payload))
        m = re.fullmatch(r"/api/configs/trash/([^/]+)/restore", path)
        if m:
            return self._json(*api_config_restore(m.group(1)))
        m = re.fullmatch(r"/api/configs/([^/]+)/rename", path)
        if m:
            return self._json(*api_config_rename(m.group(1), payload))
        m = re.fullmatch(r"/api/runs/([^/]+)/delete", path)   # POST alias ของ DELETE
        if m:
            return self._json(*api_delete_run(m.group(1)))
        return self._json(404, {"error": f"ไม่พบ route: POST {path}"})

    def do_DELETE(self):
        path = urlparse(self.path).path
        m = re.fullmatch(r"/api/configs/trash/([^/]+)", path)   # ลบถาวร (ชั้น 2) — ก่อน soft-delete
        if m:
            return self._json(*api_trash_delete(m.group(1)))
        m = re.fullmatch(r"/api/configs/([^/]+)", path)         # soft-delete (ชั้น 1)
        if m:
            return self._json(*api_config_softdelete(m.group(1)))
        m = re.fullmatch(r"/api/runs/([^/]+)", path)
        if m:
            return self._json(*api_delete_run(m.group(1)))
        return self._json(404, {"error": f"ไม่พบ route: DELETE {path}"})

    def do_HEAD(self):
        self.do_GET()

    def log_message(self, fmt, *args):
        # log สั้นๆ ลง stderr (ปิด default verbose ของ http.server)
        print(f"  · {self.command} {self.path} → {args[1] if len(args) > 1 else ''}")


def serve(host: str = "127.0.0.1", port: int = 8000) -> int:
    """เปิด console server — bind 127.0.0.1 เท่านั้น (local) · run/save ผ่าน subprocess arg-list"""
    httpd = ThreadingHTTPServer((host, port), _Handler)
    url = f"http://{host}:{port}"
    print(f"\n{'='*52}")
    print("  Backtest Console")
    print(f"{'='*52}")
    print(f"  เปิดที่   : {url}")
    print(f"  root      : {_root()}")
    print("  GET       : /api/strategies · /api/configs · /api/configs/<id> · /api/data · /api/runs")
    print("              /api/runs/<name>/metrics · /api/metric-defs")
    print("  POST      : /api/configs (save as new) · /api/run (รัน backtest)")
    print("  DELETE    : /api/runs/<name> (ลบ folder ใต้ runs/ เท่านั้น)")
    print("  หยุด      : Ctrl+C")
    print(f"{'='*52}\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  ปิด server แล้ว")
    finally:
        httpd.server_close()
    return 0
