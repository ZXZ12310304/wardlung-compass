from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.auth import credentials
from src.ui import patient_app
from src.tools.risk_rules import compute_risk_snapshot

_BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DB_PATH = os.path.join(_BASE_DIR, "data", "ward_demo.db")
_LOGO_DATA = ""
_ICONS: dict = {}

_BACKEND_CACHE: dict = {"ward_agent": None, "orchestrator": None, "image_analyzer": None}


def configure(*, base_dir: str, db_path: str, logo_data: str, icons: dict) -> None:
    global _BASE_DIR, _DB_PATH, _LOGO_DATA, _ICONS
    _BASE_DIR = base_dir
    _DB_PATH = db_path
    _LOGO_DATA = logo_data or ""
    _ICONS = icons or {}
    credentials.configure(db_path=_DB_PATH)


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _today_key() -> str:
    return datetime.utcnow().date().isoformat()


def _auto_shift_label() -> str:
    hour = datetime.now().hour
    if 7 <= hour < 15:
        return "Morning"
    if 15 <= hour < 23:
        return "Evening"
    return "Night"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _uploads_dir() -> str:
    path = os.path.join(_BASE_DIR, "data", "uploads")
    os.makedirs(path, exist_ok=True)
    return path


def _upload_url_to_path(url: str) -> str:
    text = (url or "").strip().replace("\\", "/")
    if not text.startswith("/uploads/"):
        return ""
    rel = text[len("/uploads/") :].lstrip("/")
    return os.path.join(_uploads_dir(), *rel.split("/"))


def _normalize_upload_url(path_or_url: str) -> str:
    text = (path_or_url or "").strip()
    if not text:
        return ""
    text = text.replace("\\", "/")
    if text.startswith("/uploads/"):
        return text
    needle = "/data/uploads/"
    idx = text.lower().find(needle)
    if idx >= 0:
        rel = text[idx + len(needle) :].lstrip("/")
        return "/uploads/" + rel
    if os.path.exists(path_or_url):
        try:
            rel = os.path.relpath(path_or_url, _uploads_dir()).replace("\\", "/")
            if not rel.startswith(".."):
                return "/uploads/" + rel
        except Exception:
            return ""
    return ""


def _persist_upload(src_path: str, request_id: str, prefix: str) -> str:
    if not src_path or not os.path.exists(src_path):
        return ""
    ext = os.path.splitext(src_path)[1] or ""
    dst_dir = os.path.join(_uploads_dir(), "escalations", request_id)
    os.makedirs(dst_dir, exist_ok=True)
    dst_name = f"{prefix}_{uuid.uuid4().hex}{ext}"
    dst_path = os.path.join(dst_dir, dst_name)
    shutil.copy2(src_path, dst_path)
    rel = os.path.relpath(dst_path, _uploads_dir()).replace("\\", "/")
    return "/uploads/" + rel


def _ensure_requests_table() -> None:
    try:
        with _connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS escalation_requests (
                    request_id TEXT PRIMARY KEY,
                    ward_id TEXT,
                    patient_id TEXT,
                    bed_id TEXT,
                    created_at TEXT,
                    status TEXT,
                    summary TEXT,
                    tags_json TEXT,
                    detail TEXT,
                    chat_summary TEXT,
                    audio_path TEXT,
                    image_paths_json TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_escalation_requests_ward ON escalation_requests(ward_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_escalation_requests_status ON escalation_requests(status)"
            )
    except Exception:
        pass


def _ensure_inbox_table() -> None:
    try:
        with _connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS inbox_messages (
                    message_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    sender_type TEXT NOT NULL,
                    sender_name TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    unread INTEGER NOT NULL
                )
                """
            )
    except Exception:
        pass


def _ensure_tasks_table() -> None:
    try:
        with _connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS nurse_ui_tasks (
                    staff_id TEXT PRIMARY KEY,
                    tasks_json TEXT NOT NULL,
                    updated_at TEXT
                )
                """
            )
    except Exception:
        pass


def _ensure_staff_prefs_table() -> None:
    try:
        with _connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS staff_ui_prefs (
                    staff_id TEXT PRIMARY KEY,
                    display_name TEXT,
                    avatar_data TEXT,
                    updated_at TEXT
                )
                """
            )
    except Exception:
        pass


def _ensure_doctor_orders_table() -> None:
    try:
        with _connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS doctor_orders_plan (
                    patient_id TEXT PRIMARY KEY,
                    plan_text TEXT,
                    patient_preview_text TEXT,
                    updated_by_staff_id TEXT,
                    updated_at TEXT
                )
                """
            )
    except Exception:
        pass


def _load_doctor_orders_plan(patient_id: str) -> dict:
    pid = str(patient_id or "").strip()
    if not pid:
        return {"plan_text": "", "patient_preview_text": ""}
    _ensure_doctor_orders_table()
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT plan_text, patient_preview_text FROM doctor_orders_plan WHERE patient_id = ?",
                (pid,),
            ).fetchone()
        if row:
            return {
                "plan_text": str(row["plan_text"] or "").strip(),
                "patient_preview_text": str(row["patient_preview_text"] or "").strip(),
            }
    except Exception:
        pass
    return {"plan_text": "", "patient_preview_text": ""}


def _save_doctor_orders_plan(*, patient_id: str, plan_text: str, patient_preview_text: str, staff_id: str) -> None:
    pid = str(patient_id or "").strip()
    if not pid:
        return
    _ensure_doctor_orders_table()
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO doctor_orders_plan(
                    patient_id, plan_text, patient_preview_text, updated_by_staff_id, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    pid,
                    str(plan_text or "").strip(),
                    str(patient_preview_text or "").strip(),
                    str(staff_id or "").strip(),
                    _now_iso(),
                ),
            )
    except Exception:
        pass


def _normalize_preview_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip()).lower()


def _is_cjk_text(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", str(text or "")))


def _strip_markdown_prefix(line: str) -> str:
    text = str(line or "").strip()
    text = re.sub(r"^\s*(?:[-*#•]+|\d+[.)]|[（(]?\d+[）)])\s*", "", text)
    return text.strip()


def _to_patient_second_person(text: str, use_cjk: bool) -> str:
    out = str(text or "")
    if use_cjk:
        out = re.sub(r"患者", "您", out)
        out = re.sub(r"病人", "您", out)
        return out
    out = re.sub(r"\bthe patient\b", "you", out, flags=re.IGNORECASE)
    out = re.sub(r"\bpatient's\b", "your", out, flags=re.IGNORECASE)
    out = re.sub(r"\bpatient\b", "you", out, flags=re.IGNORECASE)
    return out


def _format_patient_preview_text(text: str, source_text: str = "") -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    use_cjk = _is_cjk_text(source_text) or _is_cjk_text(raw)
    normalized = _to_patient_second_person(raw, use_cjk).replace("\r\n", "\n")
    lines = [_strip_markdown_prefix(ln) for ln in normalized.split("\n")]
    lines = [ln for ln in lines if ln]
    if len(lines) <= 1:
        base = lines[0] if lines else normalized
        parts = re.split(r"(?<=[。！？.!?])\s+|;\s+", base)
        lines = [_strip_markdown_prefix(p) for p in parts if _strip_markdown_prefix(p)]
    cleaned: List[str] = []
    for ln in lines:
        low = ln.lower()
        if "your updated care plan" in low or "care plan from your doctor" in low:
            continue
        if low in ("editable before send.", "editable before send"):
            continue
        cleaned.append(ln)
    lines = cleaned or lines
    if not lines:
        return ""
    if use_cjk:
        header = "给您的治疗计划："
        numbered = [f"第{i}项：{ln}" for i, ln in enumerate(lines, start=1)]
        tail = "如果您感觉症状加重，请及时告诉护士或医生。"
    else:
        header = "Your care plan in plain language:"
        numbered = [f"Step {i}: {ln}" for i, ln in enumerate(lines, start=1)]
        tail = "If you feel worse, tell your nurse or doctor right away."
    return "\n".join([header, *numbered, tail]).strip()


def _extract_preview_from_llm_result(result: Any, source_text: str) -> str:
    if not isinstance(result, dict) or result.get("error"):
        return ""
    source_norm = _normalize_preview_text(source_text)
    preferred_keys = [
        "patient_friendly_text",
        "answer",
        "gentle_summary",
        "summary",
        "rewritten_text",
        "text",
    ]
    candidates: List[str] = []
    for key in preferred_keys:
        val = result.get(key)
        if isinstance(val, str) and val.strip():
            candidates.append(val.strip())
    for key, val in result.items():
        if key in preferred_keys or key == "error":
            continue
        if isinstance(val, str) and val.strip():
            candidates.append(val.strip())
    for cand in candidates:
        if _normalize_preview_text(cand) != source_norm:
            return cand
    bullets = []
    for key in ("bullets", "items", "next_steps", "actions", "instructions"):
        val = result.get(key)
        if isinstance(val, list):
            for item in val:
                text = str(item or "").strip()
                if text:
                    bullets.append(text)
    if bullets:
        combined = "\n".join(bullets)
        if _normalize_preview_text(combined) != source_norm:
            return combined
    return ""


def _doctor_plan_to_patient_preview(plan_text: str) -> str:
    text = str(plan_text or "").strip()
    if not text:
        return ""
    fallback = _doctor_plan_to_patient_preview_fallback(text)
    use_llm = os.getenv("DOCTOR_PLAN_PREVIEW_USE_LLM", "1").strip().lower() in ("1", "true", "yes", "y")
    if not use_llm:
        return fallback
    try:
        medgemma = patient_app._get_medgemma_client()
    except Exception:
        return fallback
    prompts = [
        (
            "Rewrite the following doctor plan into fully patient-friendly language.\n"
            "Requirements:\n"
            "1) Rewrite ALL lines, not just add intro/outro.\n"
            "2) Keep all original medical intent and actions; do not add or remove key instructions.\n"
            "3) Use simple, non-technical wording and short sentences.\n"
            "4) Speak directly to the patient using second person (you/your).\n"
            "5) Keep point-by-point structure, each point on a new line.\n"
            "6) Do NOT use markdown symbols like -, *, #.\n"
            "7) Explain abbreviations in plain words (e.g., SpO2, CAP, MRSA).\n"
            "8) Keep the same language as the input.\n"
            "Return ONLY JSON: {\"patient_friendly_text\":\"...\"}\n\n"
            "Doctor plan:\n"
            f"{text}"
        ),
        (
            "Convert this doctor plan for a patient with plain language.\n"
            "Do not copy original wording. Keep meaning unchanged.\n"
            "Address the patient directly. Output line-by-line points without markdown symbols.\n"
            "Return ONLY JSON: {\"answer\":\"...\"}\n\n"
            f"{text}"
        ),
    ]
    for prompt in prompts:
        try:
            result = medgemma.run(prompt, max_new_tokens=512)
        except Exception:
            continue
        rewritten = _extract_preview_from_llm_result(result, text)
        if rewritten:
            formatted = _format_patient_preview_text(rewritten, source_text=text)
            if formatted:
                return formatted
    return fallback


def _doctor_plan_to_patient_preview_fallback(plan_text: str) -> str:
    text = str(plan_text or "").strip()
    if not text:
        return ""
    lines = [ln.strip(" -\t") for ln in text.replace("\r\n", "\n").split("\n")]
    lines = [ln for ln in lines if ln]
    items = []
    for ln in lines[:8]:
        simplified = str(ln)
        simplified = re.sub(
            r"SpO2\s*>\s*(\d+)\s*%",
            r"blood oxygen level (SpO2) above \1%",
            simplified,
            flags=re.IGNORECASE,
        )
        replacements = [
            (r"\badminister supplemental oxygen\b", "use oxygen support"),
            (r"\bempiric antibiotic therapy\b", "start antibiotics early while waiting for full test results"),
            (r"\bCAP\b", "community-acquired pneumonia (a lung infection)"),
            (r"\bMRSA\b", "a hard-to-treat bacteria (MRSA)"),
            (r"\bchest X-?ray\b", "chest X-ray (a picture of your lungs)"),
            (r"\bvital signs\b", "vital signs (temperature, pulse, breathing, and oxygen level)"),
        ]
        for pattern, repl in replacements:
            simplified = re.sub(pattern, repl, simplified, flags=re.IGNORECASE)
        ln = simplified
        if len(ln) > 180:
            ln = ln[:177] + "..."
        items.append(ln)
    if not items:
        return ""
    return _format_patient_preview_text("\n".join(items), source_text=text)


def _load_staff_prefs(staff_id: Optional[str]) -> Dict[str, str]:
    sid = str(staff_id or "").strip()
    if not sid:
        return {"display_name": "", "avatar_data": ""}
    _ensure_staff_prefs_table()
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT display_name, avatar_data FROM staff_ui_prefs WHERE staff_id = ?",
                (sid,),
            ).fetchone()
        if row:
            return {
                "display_name": str(row["display_name"] or "").strip(),
                "avatar_data": str(row["avatar_data"] or "").strip(),
            }
    except Exception:
        pass

    # Fallback to seeded staff account name when no UI prefs exist yet.
    try:
        store = patient_app.get_store()
        staff = store.get_staff_by_staff_id(sid)
        if staff and str(getattr(staff, "name", "") or "").strip():
            return {"display_name": str(staff.name).strip(), "avatar_data": ""}
    except Exception:
        pass
    return {"display_name": "", "avatar_data": ""}


def _save_staff_prefs(staff_id: Optional[str], display_name: str, avatar_data: str) -> None:
    sid = str(staff_id or "").strip()
    if not sid:
        return
    _ensure_staff_prefs_table()
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO staff_ui_prefs(staff_id, display_name, avatar_data, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    sid,
                    str(display_name or "").strip(),
                    str(avatar_data or "").strip(),
                    _now_iso(),
                ),
            )
    except Exception:
        pass


def _seed_requests_if_empty(ward_id: str) -> None:
    _ensure_requests_table()
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT COUNT(1) FROM escalation_requests",
            ).fetchone()
            if row and row[0]:
                return
            samples = [
                {
                    "bed_id": "A-01",
                    "patient_id": "P-1024",
                    "summary": "Shortness of breath worsened overnight.",
                    "tags": ["Safety escalation", "Vitals alert"],
                    "detail": "Patient reports increased work of breathing and chest tightness.",
                    "status": "pending",
                },
                {
                    "bed_id": "A-03",
                    "patient_id": "P-1026",
                    "summary": "Pain control request.",
                    "tags": ["Pain", "Needs attention"],
                    "detail": "Pain score 6/10 despite PRN dose.",
                    "status": "pending",
                },
                {
                    "bed_id": "A-05",
                    "patient_id": "P-1028",
                    "summary": "Dizziness after ambulation.",
                    "tags": ["Needs attention"],
                    "detail": "Reported lightheadedness when walking to restroom.",
                    "status": "in_progress",
                },
            ]
            for item in samples:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO escalation_requests
                    (request_id, ward_id, patient_id, bed_id, created_at, status, summary, tags_json, detail, chat_summary, audio_path, image_paths_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        ward_id,
                        item["patient_id"],
                        item["bed_id"],
                        _now_iso(),
                        item["status"],
                        item["summary"],
                        json.dumps(item["tags"], ensure_ascii=False),
                        item["detail"],
                        "Last chat: Patient reports chest tightness and dry cough.",
                        "",
                        json.dumps([], ensure_ascii=False),
                    ),
                )
    except Exception:
        return


def _safe_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _compact_text(text: Any) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    cleaned = re.sub(r"\s+", " ", raw)
    return cleaned.strip(" -.;:")


def _patient_condition_phrase(primary: str) -> str:
    low = str(primary or "").strip().lower()
    if not low:
        return "a breathing-related condition"
    if "pneumonia" in low:
        return "a possible chest infection"
    if "asthma" in low:
        return "an asthma flare-up"
    if "copd" in low:
        return "a breathing flare-up"
    if "bronch" in low:
        return "an airway irritation or infection"
    return "a breathing-related condition"


def _patient_risk_sentence(risk: str) -> str:
    low = str(risk or "").strip().lower()
    if low == "high":
        return "Your team is monitoring you closely right now."
    if low == "medium":
        return "Your team will continue close monitoring."
    if low == "low":
        return "Current signs look relatively stable, and we will keep monitoring."
    return "Your team is monitoring your condition and response to treatment."


def _patient_action_from_suggestion(item: Any) -> str:
    text = _compact_text(item)
    low = text.lower()
    if not text:
        return ""
    if "oxygen" in low or "spo2" in low or "o2" in low:
        return "Use oxygen support exactly as instructed by your care team."
    if "antibiotic" in low:
        return "Take prescribed antibiotics on time and do not change doses yourself."
    if "bronchodilator" in low or "inhaler" in low:
        return "Use your inhaler only as directed by your nurse or doctor."
    if "x-ray" in low or "imaging" in low or "scan" in low:
        return "You may need a chest scan to help confirm progress."
    if "monitor" in low and ("respir" in low or "breath" in low):
        return "Tell staff early if your breathing feels worse."
    if "fluid" in low or "hydrat" in low:
        return "Drink fluids as allowed by your care plan."
    return ""


def _patient_watchout_from_flag(item: Any) -> str:
    text = _compact_text(item)
    low = text.lower()
    if not text:
        return ""
    if "spo2" in low or "oxygen" in low or "hypoxia" in low:
        return "Breathing becomes harder than usual, especially at rest."
    if "respiratory distress" in low:
        return "Fast breathing or trouble speaking full sentences."
    if "chest pain" in low:
        return "New or worsening chest pain."
    if "altered mental" in low or "confusion" in low or "drows" in low:
        return "New confusion, unusual sleepiness, or fainting."
    if "fever" in low or "temperature" in low:
        return "Fever getting higher or persistent shaking chills."
    return text


def _dedupe_preserve(items: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        txt = _compact_text(item)
        if not txt:
            continue
        key = txt.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(txt)
    return out


def _build_assessment_edit_text(result: Dict[str, Any]) -> str:
    payload = result or {}
    diag = (
        payload.get("diagnosis_json")
        or payload.get("diagnosis")
        or (payload.get("result_struct") or {}).get("diagnosis_json")
        or {}
    )
    if not isinstance(diag, dict):
        diag = {}
    primary = str(diag.get("primary_diagnosis") or "").strip()
    risk = str(diag.get("risk_level") or "").strip()
    suggestions = diag.get("treatment_suggestions") if isinstance(diag.get("treatment_suggestions"), list) else []
    red_flags = diag.get("red_flags") if isinstance(diag.get("red_flags"), list) else []

    condition = _patient_condition_phrase(primary)
    action_items = _dedupe_preserve([_patient_action_from_suggestion(x) for x in suggestions])[:3]
    if not action_items:
        action_items = [
            "Rest and pace your activity as tolerated.",
            "Take medicines exactly as instructed by your care team.",
            "Let staff know early if symptoms feel worse.",
        ]

    watch_items = _dedupe_preserve([_patient_watchout_from_flag(x) for x in red_flags])[:3]
    if not watch_items:
        watch_items = [
            "Breathing becomes harder than usual.",
            "New chest pain, confusion, or fainting.",
            "Fever rises or symptoms get worse quickly.",
        ]

    lines: List[str] = [
        "Hi, thanks for your update.",
        f"Your recent check suggests {condition}.",
        _patient_risk_sentence(risk),
        "",
        "What to do now:",
    ]
    for item in action_items:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("Please tell your nurse or doctor right away if:")
    for item in watch_items:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("We are here to support you and will keep monitoring your progress.")
    return "\n".join(lines).strip()


def _ward_label(ward_id: Optional[str]) -> str:
    if not ward_id:
        return "Ward A"
    text = str(ward_id).replace("_", " ").upper()
    if text.startswith("WARD "):
        return text.title()
    return f"Ward {text[-1:]}" if len(text) >= 1 else "Ward A"


def _ward_id_from_label(label: str) -> str:
    if not label:
        return "ward_a"
    text = label.strip().lower()
    if "ward" in text:
        letter = text.replace("ward", "").strip()
        if letter:
            return f"ward_{letter[0]}"
    return "ward_a"


def default_tasks() -> List[Dict[str, Any]]:
    return [
        {"task_id": "task_huddle", "label": "Morning huddle", "done": False},
        {"task_id": "task_huddle_report", "label": "Morning huddle report", "done": False},
        {"task_id": "task_check_completed", "label": "Check completed", "done": False},
        {"task_id": "task_controlled_drugs", "label": "Check controlled drugs", "done": False},
        {"task_id": "task_discharge", "label": "Sign off discharges", "done": False},
    ]


def _load_staff_tasks(staff_id: Optional[str]) -> List[Dict[str, Any]]:
    defaults = default_tasks()
    sid = str(staff_id or "").strip()
    if not sid:
        return defaults
    _ensure_tasks_table()
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT tasks_json FROM nurse_ui_tasks WHERE staff_id = ?",
                (sid,),
            ).fetchone()
            if not row:
                conn.execute(
                    "INSERT OR REPLACE INTO nurse_ui_tasks(staff_id, tasks_json, updated_at) VALUES(?,?,?)",
                    (sid, json.dumps(defaults, ensure_ascii=False), _now_iso()),
                )
                return defaults
            parsed = _safe_json(row["tasks_json"], defaults)
            if not isinstance(parsed, list):
                return defaults
            saved_map = {str(t.get("task_id")): bool(t.get("done")) for t in parsed if isinstance(t, dict)}
            merged = []
            for item in defaults:
                task = dict(item)
                if task["task_id"] in saved_map:
                    task["done"] = saved_map[task["task_id"]]
                merged.append(task)
            return merged
    except Exception:
        return defaults


def _save_staff_tasks(staff_id: Optional[str], tasks: List[Dict[str, Any]]) -> None:
    sid = str(staff_id or "").strip()
    if not sid:
        return
    _ensure_tasks_table()
    safe_tasks = []
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        safe_tasks.append(
            {
                "task_id": str(t.get("task_id") or ""),
                "label": str(t.get("label") or ""),
                "done": bool(t.get("done")),
            }
        )
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO nurse_ui_tasks(staff_id, tasks_json, updated_at) VALUES(?,?,?)",
                (sid, json.dumps(safe_tasks, ensure_ascii=False), _now_iso()),
            )
    except Exception:
        pass


def init_nurse_state(state: dict, staff_id: Optional[str], ward_id: Optional[str]) -> dict:
    state = state or {}
    resolved_staff_id = staff_id or state.get("staff_id")
    resolved_staff_text = str(resolved_staff_id or "").strip()
    prefs = _load_staff_prefs(resolved_staff_id)
    state.setdefault("current_page", "ward_dashboard")
    state.setdefault("ward_filter", "All")
    state.setdefault("ward_search", "")
    state.setdefault("ward_shift", "Morning")
    state.setdefault("ward_selected_label", _ward_label(ward_id))
    state.setdefault("ward_id", ward_id or _ward_id_from_label(state.get("ward_selected_label", "Ward A")))
    state.setdefault("nurse_selected_patient", None)
    state.setdefault("nurse_selected_bed", None)
    state["tasks"] = _load_staff_tasks(resolved_staff_id)
    state.setdefault("requests_filter", "Pending")
    state.setdefault("requests_source_filter", "All")
    state.setdefault("requests_search", "")
    state.setdefault("requests_selected_id", None)
    state.setdefault("requests_assessment_drafts", {})
    state.setdefault("requests_assessment_status_msg", "")
    state.setdefault("requests_assessment_status_request_id", None)
    state.setdefault("requests_forward_status_msg", "")
    state.setdefault("requests_forward_status_request_id", None)
    state.setdefault("requests_forward_doctor_id", "")
    state.setdefault("assessment_note", "")
    state.setdefault("assessment_result", None)
    state.setdefault("assessment_image_path", None)
    state.setdefault("assessment_audio_path", None)
    state.setdefault("handover_range", "Today")
    state.setdefault("handover_sbar_md", "")
    state.setdefault("handover_key_points", [])
    state.setdefault("handover_snapshot_id", None)
    state.setdefault("handover_forward_status_msg", "")
    state.setdefault("handover_forward_target_staff_id", "")
    state.setdefault("handover_forward_text", "")
    state.setdefault("handover_forward_image_path", None)
    state.setdefault("handover_forward_audio_path", None)
    state.setdefault("vitals_form", {})
    state.setdefault("mar_items", [])
    state.setdefault("vitals_save_lock", {})
    state.setdefault("mar_save_lock", {})
    state.setdefault("nurse_staff_id", resolved_staff_text)
    state["staff_display_name"] = (
        str(prefs.get("display_name") or "").strip() or state.get("staff_display_name") or resolved_staff_text
    )
    state["staff_avatar_data"] = str(prefs.get("avatar_data") or "").strip() or state.get("staff_avatar_data")
    return state


def init_doctor_state(state: dict, staff_id: Optional[str], ward_id: Optional[str]) -> dict:
    state = init_nurse_state(state, staff_id, ward_id)
    if str(state.get("current_page") or "").strip() in ("", "ward_dashboard"):
        state["current_page"] = "doctor_dashboard"
    state.setdefault("doctor_filter", "All")
    state.setdefault("doctor_search", "")
    state.setdefault("doctor_selected_patient", None)
    state.setdefault("doctor_inbox_filter", "Pending")
    state.setdefault("doctor_inbox_source_filter", "All")
    state.setdefault("doctor_inbox_search", "")
    state.setdefault("doctor_inbox_selected_id", None)
    state.setdefault("doctor_inbox_status_msg", "")
    state.setdefault("doctor_inbox_status_request_id", None)
    state.setdefault("doctor_notes_drafts", {})
    state.setdefault("doctor_note_status_msg", "")
    state.setdefault("doctor_note_status_patient_id", None)
    state.setdefault("doctor_assessment_drafts", {})
    state.setdefault("doctor_assessment_status_msg", "")
    state.setdefault("doctor_assessment_status_patient_id", None)
    state.setdefault("doctor_create_patient_status_msg", "")
    state.setdefault("doctor_create_nurse_status_msg", "")
    state.setdefault("doctor_orders_plan_drafts", {})
    state.setdefault("doctor_orders_preview_drafts", {})
    state.setdefault("doctor_orders_status_msg", "")
    state.setdefault("doctor_orders_status_patient_id", None)
    return state


def parse_ui_payload(payload: str) -> dict:
    if not payload:
        return {}
    try:
        data = json.loads(payload)
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


def _apply_payload_page(data: dict, state: dict) -> dict:
    page = (data or {}).get("current_page") or (data or {}).get("page") or ""
    page = str(page).strip()
    if page:
        state["current_page"] = page
    return state


def _get_orchestrator():
    orch = _BACKEND_CACHE.get("orchestrator")
    if orch is not None:
        return orch
    from src.agents.orchestrator import AnalysisOrchestrator
    from src.agents.observer import MedSigLIPAnalyzer

    medgemma = patient_app._get_medgemma_client()
    rag = patient_app._get_rag_engine()
    asr = patient_app._get_asr_transcriber()
    image_analyzer = MedSigLIPAnalyzer()
    orch = AnalysisOrchestrator(medgemma, image_analyzer, rag_engine=rag, asr_transcriber=asr)
    _BACKEND_CACHE["orchestrator"] = orch
    _BACKEND_CACHE["image_analyzer"] = image_analyzer
    return orch


def _ensure_patient_profile_extra_table() -> None:
    try:
        with _connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS patient_profile_extra (
                    patient_id TEXT PRIMARY KEY,
                    allergy_history TEXT,
                    updated_at TEXT
                )
                """
            )
    except Exception:
        pass


def _get_patient_allergy_history(patient_id: str) -> str:
    pid = str(patient_id or "").strip()
    if not pid:
        return ""
    _ensure_patient_profile_extra_table()
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT allergy_history FROM patient_profile_extra WHERE patient_id = ?",
                (pid,),
            ).fetchone()
        return str(row["allergy_history"] or "").strip() if row else ""
    except Exception:
        return ""


def _upsert_patient_allergy_history(patient_id: str, allergy_history: str) -> None:
    pid = str(patient_id or "").strip()
    if not pid:
        return
    _ensure_patient_profile_extra_table()
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO patient_profile_extra(patient_id, allergy_history, updated_at)
                VALUES (?, ?, ?)
                """,
                (pid, str(allergy_history or "").strip(), _now_iso()),
            )
    except Exception:
        pass


def _get_ward_agent():
    agent = _BACKEND_CACHE.get("ward_agent")
    if agent is not None:
        return agent
    from src.agents.ward_agent import WardAgent

    store = patient_app.get_store()
    orch = _get_orchestrator()
    agent = WardAgent(
        store=store,
        orchestrator=orch,
        medgemma_client=patient_app._get_medgemma_client(),
        rag_engine=patient_app._get_rag_engine(),
        asr_transcriber=patient_app._get_asr_transcriber(),
        lang="en",
    )
    _BACKEND_CACHE["ward_agent"] = agent
    return agent


def _list_wards() -> List[str]:
    store = patient_app.get_store()
    try:
        wards = {p.ward_id for p in store.list_patients_by_ward("ward_a")}  # seed
    except Exception:
        wards = set()
    try:
        with _connect() as conn:
            rows = conn.execute("SELECT DISTINCT ward_id FROM patients").fetchall()
        for row in rows:
            if row[0]:
                wards.add(row[0])
    except Exception:
        pass
    if not wards:
        wards = {"ward_a"}
    return sorted(list(wards))


def _get_default_doctor_staff_id(ward_id: str) -> str:
    store = patient_app.get_store()
    try:
        staff_list = store.list_staff_by_ward(str(ward_id or ""))
    except Exception:
        staff_list = []
    doctors = []
    for staff in staff_list or []:
        role = str(getattr(staff, "role", "") or "").strip().lower()
        if role == "doctor":
            sid = str(getattr(staff, "staff_id", "") or "").strip()
            if sid:
                doctors.append(sid)
    doctors = sorted(doctors)
    return doctors[0] if doctors else ""


def _load_requests(ward_id: str, status: Optional[str], search: str, source_filter: str = "All") -> List[dict]:
    _seed_requests_if_empty(ward_id)
    _ensure_requests_table()
    rows: List[dict] = []
    try:
        with _connect() as conn:
            params: List[Any] = [ward_id]
            sql = "SELECT * FROM escalation_requests WHERE ward_id = ?"
            if status and status.lower() != "all":
                sql += " AND status = ?"
                params.append(status.lower())
            if search:
                sql += " AND (patient_id LIKE ? OR bed_id LIKE ? OR summary LIKE ?)"
                like = f"%{search}%"
                params.extend([like, like, like])
            sql += " ORDER BY created_at DESC"
            for r in conn.execute(sql, params).fetchall():
                rows.append(dict(r))
    except Exception:
        return []
    def _is_forwarded_handover(summary: str, detail: str, tags: list[str]) -> bool:
        summary_low = str(summary or "").strip().lower()
        detail_low = str(detail or "").strip().lower()
        tag_lows = [str(t or "").strip().lower() for t in (tags or [])]
        if any("handover" in t for t in tag_lows):
            return True
        if summary_low.startswith("handover from "):
            return True
        if "forwarded sbar handover" in detail_low:
            return True
        return False

    def _forward_meta(summary: str, detail: str, tags: list[str]) -> tuple[str, str]:
        from_staff = ""
        to_staff = ""
        for line in str(detail or "").splitlines():
            m_from = re.match(r"^\s*from\s*:\s*(.+)$", line, re.I)
            if m_from and not from_staff:
                from_staff = str(m_from.group(1) or "").strip()
            m_to = re.match(r"^\s*to\s*:\s*(.+)$", line, re.I)
            if m_to and not to_staff:
                to_staff = str(m_to.group(1) or "").strip()
        if not to_staff:
            for tag in tags or []:
                text = str(tag or "").strip()
                if text.lower().startswith("to "):
                    to_staff = text[3:].strip()
                    break
        if not (from_staff and to_staff):
            m = re.match(r"^\s*handover\s+from\s+(\S+)\s+to\s+(\S+)", str(summary or "").strip(), re.I)
            if m:
                if not from_staff:
                    from_staff = str(m.group(1) or "").strip()
                if not to_staff:
                    to_staff = str(m.group(2) or "").strip()
        return from_staff, to_staff

    def _source_category(
        *, is_forwarded: bool, tags: list[str], forward_from: str, summary: str, detail: str
    ) -> str:
        tag_lows = [str(t or "").strip().lower() for t in (tags or [])]
        summary_low = str(summary or "").strip().lower()
        detail_low = str(detail or "").strip().lower()
        ff = str(forward_from or "").strip().upper()
        if is_forwarded:
            if ff.startswith("D-") or "doctor" in ff.lower():
                return "Doctor"
            if ff.startswith("N-") or "nurse" in ff.lower():
                return "Nurse"
            return "Nurse"
        if any("forwarded by nurse" in t or "nurse forward" in t for t in tag_lows):
            return "Nurse"
        if any("forwarded by doctor" in t or "doctor forward" in t for t in tag_lows):
            return "Doctor"
        if any("doctor" in t for t in tag_lows):
            return "Doctor"
        if any("patient" in t for t in tag_lows):
            return "Patient"
        if any("safety escalation" in t for t in tag_lows):
            return "Patient"
        if any("manual request" in t for t in tag_lows):
            return "Patient"
        if "patient message" in detail_low or "patient requested" in summary_low:
            return "Patient"
        return "Patient"

    out = []
    for r in rows:
        raw_audio = str(r.get("audio_path") or "")
        audio_url = _normalize_upload_url(raw_audio)
        if audio_url and not os.path.exists(_upload_url_to_path(audio_url)):
            audio_url = ""
        raw_images = _safe_json(r.get("image_paths_json"), [])
        image_urls: List[str] = []
        for img in raw_images:
            u = _normalize_upload_url(str(img or ""))
            if u and os.path.exists(_upload_url_to_path(u)):
                image_urls.append(u)
        tags = _safe_json(r.get("tags_json"), [])
        summary_text = r.get("summary") or ""
        detail_text = r.get("detail") or ""
        is_forwarded = _is_forwarded_handover(summary_text, detail_text, tags)
        forward_from, forward_to = _forward_meta(summary_text, detail_text, tags)
        source_category = _source_category(
            is_forwarded=is_forwarded,
            tags=tags,
            forward_from=forward_from,
            summary=summary_text,
            detail=detail_text,
        )
        source_filter_text = str(source_filter or "All").strip().lower()
        if source_filter_text in ("patient", "nurse", "doctor") and source_category.lower() != source_filter_text:
            continue
        out.append(
            {
                "request_id": r.get("request_id"),
                "patient_id": r.get("patient_id"),
                "bed_id": r.get("bed_id"),
                "created_at": r.get("created_at") or "",
                "status": r.get("status") or "pending",
                "summary": summary_text,
                "tags": tags,
                "detail": detail_text,
                "chat_summary": r.get("chat_summary") or "",
                "audio_path": audio_url,
                "images": image_urls,
                "is_forwarded_handover": is_forwarded,
                "forward_from": forward_from,
                "forward_to": forward_to,
                "source_category": source_category,
            }
        )
    return out


def _update_request_status(request_id: str, status: str) -> None:
    if not request_id:
        return
    _ensure_requests_table()
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE escalation_requests SET status = ? WHERE request_id = ?",
                (status, request_id),
            )
    except Exception:
        return


def _delete_request(request_id: str) -> bool:
    rid = str(request_id or "").strip()
    if not rid:
        return False
    _ensure_requests_table()
    row = _get_request_row(rid)
    try:
        with _connect() as conn:
            cur = conn.execute("DELETE FROM escalation_requests WHERE request_id = ?", (rid,))
        deleted = bool(getattr(cur, "rowcount", 0) or 0)
    except Exception:
        return False
    if not deleted:
        return False
    try:
        if row:
            audio_url = _normalize_upload_url(str(row.get("audio_path") or ""))
            if audio_url:
                audio_path = _upload_url_to_path(audio_url)
                if audio_path and os.path.exists(audio_path):
                    os.remove(audio_path)
            for img in _safe_json(row.get("image_paths_json"), []):
                image_url = _normalize_upload_url(str(img or ""))
                image_path = _upload_url_to_path(image_url)
                if image_path and os.path.exists(image_path):
                    os.remove(image_path)
        shutil.rmtree(os.path.join(_uploads_dir(), "escalations", rid), ignore_errors=True)
    except Exception:
        pass
    return True


def _get_request_row(request_id: str) -> Optional[dict]:
    if not request_id:
        return None
    _ensure_requests_table()
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM escalation_requests WHERE request_id = ? LIMIT 1",
                (request_id,),
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def _insert_inbox_message(*, patient_id: str, sender_name: str, subject: str, body: str) -> bool:
    _ensure_inbox_table()
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO inbox_messages (message_id, patient_id, sender_type, sender_name, subject, body, created_at, unread)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    str(patient_id),
                    "Nurse",
                    sender_name,
                    subject,
                    body,
                    _now_iso(),
                    1,
                ),
            )
        if hasattr(patient_app, "_INBOX_CACHE"):
            try:
                patient_app._INBOX_CACHE.clear()
            except Exception:
                pass
        return True
    except Exception:
        return False


def create_escalation_request(
    *,
    patient_id: str,
    ward_id: Optional[str],
    bed_id: Optional[str],
    summary: str,
    detail: str,
    tags: Optional[List[str]] = None,
    chat_summary: str = "",
    audio_src_path: str = "",
    image_src_paths: Optional[List[str]] = None,
    status: str = "pending",
) -> str:
    _ensure_requests_table()
    pid = (patient_id or "").strip()
    if not pid:
        return ""
    wid = ward_id or "ward_a"
    b = bed_id or ""
    summary_text = (summary or "").strip() or "Escalation request from patient chat."
    detail_text = (detail or "").strip() or summary_text
    safe_tags = [str(t).strip() for t in (tags or []) if str(t).strip()]
    safe_tags = safe_tags[:3]
    now = datetime.utcnow()
    now_iso = now.isoformat()

    try:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT request_id, created_at
                FROM escalation_requests
                WHERE patient_id = ?
                  AND status IN ('pending', 'in_progress')
                  AND summary = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (pid, summary_text),
            ).fetchone()
            if row:
                created_at = str(row["created_at"] or "")
                try:
                    dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                except Exception:
                    dt = None
                if dt is not None and abs((now - dt.replace(tzinfo=None)).total_seconds()) <= 120:
                    return str(row["request_id"] or "")

            request_id = uuid.uuid4().hex
            audio_url = _persist_upload(audio_src_path, request_id, "audio") if audio_src_path else ""
            image_urls = []
            for p in image_src_paths or []:
                u = _persist_upload(p, request_id, "image")
                if u:
                    image_urls.append(u)
            conn.execute(
                """
                INSERT OR REPLACE INTO escalation_requests
                (request_id, ward_id, patient_id, bed_id, created_at, status, summary, tags_json, detail, chat_summary, audio_path, image_paths_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    wid,
                    pid,
                    b,
                    now_iso,
                    (status or "pending").lower(),
                    summary_text,
                    json.dumps(safe_tags, ensure_ascii=False),
                    detail_text,
                    chat_summary or "",
                    audio_url,
                    json.dumps(image_urls, ensure_ascii=False),
                ),
            )
            return request_id
    except Exception:
        return ""


def _select_patient_default(ward_id: str) -> Optional[str]:
    store = patient_app.get_store()
    try:
        pts = store.list_patients_by_ward(ward_id)
        if pts:
            return pts[0].patient_id
    except Exception:
        pass
    return None


def _format_vitals(vitals: dict) -> str:
    if not vitals:
        return "---"
    hr = vitals.get("heart_rate") or vitals.get("hr")
    spo2 = vitals.get("spo2_pct") or vitals.get("spo2")
    if hr:
        return f"HR {hr}"
    if spo2:
        return f"SpO2 {spo2}%"
    return "Updated"


def _format_last_mar(meds: list) -> str:
    if not meds:
        return "---"
    statuses = {str(m.get("status") or "").lower() for m in meds}
    if "delayed" in statuses or "refused" in statuses:
        return "Needs follow-up"
    if "due" in statuses or "pending" in statuses:
        return "Due now"
    return "Done"


def get_nurse_sidebar_data(state: dict) -> dict:
    staff_id = state.get("staff_id") or state.get("nurse_staff_id") or "Staff"
    role = "Nurse"
    display_name = state.get("staff_display_name") or staff_id
    avatar = state.get("staff_avatar_data") or ""
    if not avatar:
        try:
            avatar = patient_app._avatar_data_uri(display_name)
        except Exception:
            avatar = _LOGO_DATA
    return {"staff_id": staff_id, "display_name": display_name, "role": role, "avatar": avatar}


def get_dashboard_data(state: dict) -> dict:
    store = patient_app.get_store()
    ward_id = state.get("ward_id") or _ward_id_from_label(state.get("ward_selected_label", "Ward A"))
    state["ward_id"] = ward_id
    search = (state.get("ward_search") or "").strip()
    filter_tag = state.get("ward_filter") or "All"
    patients = []
    try:
        all_patients = store.list_patients_by_ward(ward_id)
    except Exception:
        all_patients = []

    for p in all_patients:
        if search and search.lower() not in (p.patient_id or "").lower() and search.lower() not in (p.bed_id or "").lower():
            continue
        latest_admin = store.get_latest_nurse_admin(p.patient_id)
        latest_assessment = store.get_latest_assessment(p.patient_id)
        latest_risk = store.get_latest_risk_snapshot(p.patient_id)
        vitals = _safe_json(getattr(latest_admin, "vitals_json", None), {})
        meds = _safe_json(getattr(latest_admin, "administered_meds_json", None), [])

        risk_level = "stable"
        risk_label = "Stable"
        if latest_risk:
            risk_level = (latest_risk.risk_level or "stable").lower()
            if risk_level in ("high", "red"):
                risk_label = "High Priority"
            elif risk_level in ("medium", "yellow"):
                risk_label = "Needs Attention"
                risk_level = "attention"
            else:
                risk_label = "Stable"
                risk_level = "stable"
        if filter_tag == "Stable" and risk_level != "stable":
            continue
        if filter_tag == "Needs Attention" and risk_level == "stable":
            continue

        patients.append(
            {
                "bed_id": p.bed_id or "--",
                "patient_id": p.patient_id,
                "risk_level": risk_level,
                "risk_label": risk_label,
                "last_vitals": _format_vitals(vitals),
                "last_mar": _format_last_mar(meds),
                "last_assessment": "Pending" if not latest_assessment else "Updated",
            }
        )

    pending_requests = _load_requests(ward_id, "pending", "")
    tasks = state.get("tasks") or default_tasks()
    return {
        "ward_label": _ward_label(ward_id),
        "shift": _auto_shift_label(),
        "search": search,
        "filter": filter_tag,
        "patients": patients,
        "pending_requests": pending_requests[:4],
        "tasks": tasks,
    }


def get_patient_picker(state: dict) -> dict:
    store = patient_app.get_store()
    ward_id = state.get("ward_id") or _ward_id_from_label(state.get("ward_selected_label", "Ward A"))
    try:
        patients = store.list_patients_by_ward(ward_id)
    except Exception:
        patients = []
    options = []
    for p in patients:
        label = f"Bed {p.bed_id} | {p.patient_id}" if p.bed_id else p.patient_id
        options.append({"value": p.patient_id, "label": label, "bed_id": p.bed_id})
    selected = state.get("nurse_selected_patient") or (options[0]["value"] if options else None)
    return {"options": options, "selected": selected, "ward_label": _ward_label(ward_id)}


def get_vitals_data(state: dict) -> dict:
    store = patient_app.get_store()
    picker = get_patient_picker(state)
    patient_id = picker["selected"]
    if patient_id is None:
        return {"picker": picker, "patient": None, "latest_admin": None, "mar_items": []}
    latest_admin = store.get_latest_nurse_admin(patient_id)
    vitals = _safe_json(getattr(latest_admin, "vitals_json", None), {})
    meds = _safe_json(getattr(latest_admin, "administered_meds_json", None), [])
    if not meds:
        meds = [
            {"name": "Metformin", "dose": "500mg", "time": "08:00", "status": "Given"},
            {"name": "Paracetamol", "dose": "1g", "time": "PRN", "status": "Delayed"},
            {"name": "Salbutamol Inhaler", "dose": "2 puffs", "time": "14:00", "status": "Given"},
        ]
    patient = store.get_patient(patient_id)
    latest_risk = store.get_latest_risk_snapshot(patient_id)
    risk_text = "Stable"
    if latest_risk:
        level = str(latest_risk.risk_level or "stable").lower()
        if level in ("high", "red"):
            risk_text = "High Priority"
        elif level in ("medium", "yellow"):
            risk_text = "Needs Attention"
    details = {
        "bed_id": patient.bed_id if patient else "",
        "patient_id": patient.patient_id if patient else patient_id,
        "age": patient.age if patient else "--",
        "sex": patient.sex if patient else "--",
        "allergy": "--",
        "risk": risk_text,
        "updated_at": latest_admin.timestamp if latest_admin else "---",
    }
    alerts: List[str] = []
    meds_statuses = {str((m or {}).get("status") or "").strip().lower() for m in meds}
    if "due" in meds_statuses or "pending" in meds_statuses:
        alerts.append("Medication due now.")
    if "delayed" in meds_statuses:
        alerts.append("Medication delayed.")
    if "refused" in meds_statuses:
        alerts.append("Medication refused.")
    recent = []
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT * FROM nurse_admin WHERE patient_id = ? ORDER BY timestamp DESC LIMIT 10",
                (patient_id,),
            ).fetchall()
        spo2_values: List[float] = []
        hr_values: List[float] = []
        for row in rows:
            vv = _safe_json(row["vitals_json"], {})
            spo2_raw = vv.get("spo2") if vv.get("spo2") is not None else vv.get("spo2_pct")
            hr_raw = vv.get("heart_rate") if vv.get("heart_rate") is not None else vv.get("hr")
            try:
                if spo2_raw is not None and str(spo2_raw).strip() != "":
                    spo2_values.append(float(spo2_raw))
            except Exception:
                pass
            try:
                if hr_raw is not None and str(hr_raw).strip() != "":
                    hr_values.append(float(hr_raw))
            except Exception:
                pass
        if len(spo2_values) >= 2 and spo2_values[0] < spo2_values[1]:
            alerts.append(f"SpO2 trending down (latest {int(spo2_values[0])}%).")
        elif spo2_values and spo2_values[0] < 92:
            alerts.append(f"SpO2 low at {int(spo2_values[0])}%.")
        if hr_values and hr_values[0] > 110:
            alerts.append(f"Heart rate elevated ({int(hr_values[0])} bpm).")
        for r in rows:
            v = _safe_json(r["vitals_json"], {})
            hr = v.get("heart_rate") if v.get("heart_rate") is not None else v.get("hr")
            spo2 = v.get("spo2") if v.get("spo2") is not None else v.get("spo2_pct")
            vitals_summary = f"HR {hr or '--'}, BP {v.get('bp','--')}, SpO2 {spo2 or '--'}%"
            meds_summary = "Meds updated"
            recent.append({"time": r["timestamp"][:16], "vitals": vitals_summary, "meds": meds_summary})
    except Exception:
        pass
    if not alerts:
        alerts = ["No active alerts."]
    today = _today_key()
    vitals_lock = _safe_json(state.get("vitals_save_lock"), {})
    mar_lock = _safe_json(state.get("mar_save_lock"), {})
    vitals_locked = str(vitals_lock.get(str(patient_id), "")) == today
    mar_locked = str(mar_lock.get(str(patient_id), "")) == today
    return {
        "picker": picker,
        "patient": details,
        "vitals": vitals,
        "alerts": alerts,
        "mar_items": meds,
        "recent": recent,
        "vitals_locked": vitals_locked,
        "mar_locked": mar_locked,
    }


def get_assessment_data(state: dict) -> dict:
    store = patient_app.get_store()
    picker = get_patient_picker(state)
    patient_id = picker["selected"]
    latest_log = store.get_latest_daily_log(patient_id) if patient_id else None
    latest_admin = store.get_latest_nurse_admin(patient_id) if patient_id else None
    latest_assessment = store.get_latest_assessment(patient_id) if patient_id else None
    stored_result = None
    if latest_assessment:
        stored_result = {
            "diagnosis_json": _safe_json(latest_assessment.diagnosis_json, {}),
            "gaps": _safe_json(latest_assessment.gaps_json, []),
            "tool_trace": _safe_json(latest_assessment.tool_trace_json, []),
            "rag_evidence": _safe_json(latest_assessment.rag_evidence_json, []),
        }
    sources = {
        "daily_log": "Available" if latest_log else "Missing",
        "vitals": "Available" if latest_admin else "Missing",
        "mar": "Available" if latest_admin else "Missing",
        "assessment": "Available" if latest_assessment else "None",
    }
    return {
        "picker": picker,
        "sources": sources,
        "note": state.get("assessment_note", ""),
        "image_name": os.path.basename(state.get("assessment_image_path") or ""),
        "audio_name": os.path.basename(state.get("assessment_audio_path") or ""),
        "result": state.get("assessment_result") or stored_result,
        "edit_text": (state.get("assessment_edit_text") or _build_assessment_edit_text(state.get("assessment_result") or stored_result or {})),
        "status_msg": state.get("assessment_status_msg", ""),
    }


def get_handover_data(state: dict) -> dict:
    store = patient_app.get_store()
    picker = get_patient_picker(state)
    patient_id = picker["selected"]
    latest = store.get_latest_handover(patient_id) if patient_id else None
    sbar_md = state.get("handover_sbar_md") or (latest.sbar_md if latest else "")
    key_points = state.get("handover_key_points") or []
    valid_ranges = {"Today", "Last 3 days"}
    current_range = str(state.get("handover_range", "Today") or "Today")
    if current_range not in valid_ranges:
        current_range = "Today"
    return {
        "picker": picker,
        "range": current_range,
        "sbar_md": sbar_md,
        "key_points": key_points,
        "status_msg": state.get("handover_status_msg", ""),
        "forward_status_msg": state.get("handover_forward_status_msg", ""),
        "target_staff_id": state.get("handover_forward_target_staff_id", ""),
        "forward_text": state.get("handover_forward_text") or sbar_md,
        "forward_image_name": os.path.basename(state.get("handover_forward_image_path") or "") or "Add image (optional)",
        "forward_audio_name": os.path.basename(state.get("handover_forward_audio_path") or "") or "Add audio (optional)",
    }


def get_inbox_data(state: dict) -> dict:
    ward_id = state.get("ward_id") or _ward_id_from_label(state.get("ward_selected_label", "Ward A"))
    filter_tab = state.get("requests_filter", "Pending")
    source_filter = state.get("requests_source_filter", "All")
    search = state.get("requests_search", "")
    selected_id = state.get("requests_selected_id")
    status_map = {
        "Pending": "pending",
        "In Progress": "in_progress",
        "Done": "done",
        "All": "all",
    }
    requests = _load_requests(ward_id, status_map.get(filter_tab, "pending"), search, str(source_filter or "All"))
    selected = next((r for r in requests if r["request_id"] == selected_id), None)
    if not selected and requests:
        selected = requests[0]
    selected_request_id = str((selected or {}).get("request_id") or "")
    forward_status_msg = ""
    if selected_request_id and str(state.get("requests_forward_status_request_id") or "") == selected_request_id:
        forward_status_msg = str(state.get("requests_forward_status_msg") or "").strip()
    forward_doctor_id = str(state.get("requests_forward_doctor_id") or "").strip()
    return {
        "ward_label": _ward_label(ward_id),
        "filter": filter_tab,
        "source_filter": source_filter,
        "search": search,
        "requests": requests,
        "selected": selected,
        "forward_doctor_id": forward_doctor_id,
        "forward_status_msg": forward_status_msg,
    }


def ward_update(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    # Ward and shift are fixed/auto-derived for nurse workflow.
    if "filter" in data:
        state["ward_filter"] = data.get("filter") or "All"
    if "search" in data:
        state["ward_search"] = data.get("search") or ""
    return state


def nurse_select_patient(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    patient_id = data.get("patient_id")
    bed_id = data.get("bed_id")
    if patient_id:
        previous_patient_id = str(state.get("nurse_selected_patient") or "").strip()
        next_patient_id = str(patient_id or "").strip()
        state["nurse_selected_patient"] = patient_id
        state["assessment_status_msg"] = ""
        state["handover_status_msg"] = ""
        if next_patient_id and next_patient_id != previous_patient_id:
            # Clear patient-scoped transient outputs so the newly selected patient
            # falls back to data loaded from store instead of stale in-memory drafts.
            state["assessment_result"] = None
            state["assessment_edit_text"] = ""
            state["assessment_note"] = ""
            state["assessment_image_path"] = None
            state["assessment_audio_path"] = None
            state["handover_sbar_md"] = ""
            state["handover_key_points"] = []
            state["handover_snapshot_id"] = None
            state["handover_forward_status_msg"] = ""
            state["handover_forward_target_staff_id"] = ""
            state["handover_forward_text"] = ""
            state["handover_forward_image_path"] = None
            state["handover_forward_audio_path"] = None
    if bed_id:
        state["nurse_selected_bed"] = bed_id
    return state


def task_toggle(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    task_id = data.get("task_id")
    tasks = state.get("tasks") or default_tasks()
    for t in tasks:
        if t.get("task_id") == task_id:
            t["done"] = not t.get("done")
    state["tasks"] = tasks
    _save_staff_tasks(state.get("staff_id") or state.get("nurse_staff_id"), tasks)
    return state


def requests_filter(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    state["requests_filter"] = data.get("filter", "Pending")
    return state


def requests_source_filter(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    value = str(data.get("source_filter") or "All")
    allowed = {"All", "Patient", "Nurse", "Doctor"}
    state["requests_source_filter"] = value if value in allowed else "All"
    return state


def requests_search(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    state["requests_search"] = data.get("q", "")
    return state


def requests_select(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    rid = data.get("request_id")
    if rid:
        state["requests_selected_id"] = rid
        if str(state.get("requests_forward_status_request_id") or "") != str(rid):
            state["requests_forward_status_msg"] = ""
    return state


def requests_update(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    rid = data.get("request_id")
    status = data.get("status")
    if rid and status:
        _update_request_status(rid, status)
        state["requests_selected_id"] = rid
        if status == "in_progress":
            state["requests_filter"] = "In Progress"
        elif status == "done":
            state["requests_filter"] = "Done"
    return state


def requests_delete(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    rid = str(data.get("request_id") or state.get("requests_selected_id") or "").strip()
    if not rid:
        state["toast"] = "Select a request first."
        return state
    ok = _delete_request(rid)
    if not ok:
        state["toast"] = "Delete failed."
        return state
    drafts = _safe_json(state.get("requests_assessment_drafts"), {})
    if rid in drafts:
        drafts.pop(rid, None)
        state["requests_assessment_drafts"] = drafts
    if str(state.get("requests_assessment_status_request_id") or "") == rid:
        state["requests_assessment_status_msg"] = ""
        state["requests_assessment_status_request_id"] = None
    if str(state.get("requests_forward_status_request_id") or "") == rid:
        state["requests_forward_status_msg"] = ""
        state["requests_forward_status_request_id"] = None
    ward_id = state.get("ward_id") or _ward_id_from_label(state.get("ward_selected_label", "Ward A"))
    filter_tab = state.get("requests_filter", "Pending")
    source_filter = state.get("requests_source_filter", "All")
    search = state.get("requests_search", "")
    status_map = {
        "Pending": "pending",
        "In Progress": "in_progress",
        "Done": "done",
        "All": "all",
    }
    requests = _load_requests(ward_id, status_map.get(filter_tab, "pending"), search, str(source_filter or "All"))
    state["requests_selected_id"] = requests[0]["request_id"] if requests else None
    state["toast"] = "Request deleted."
    return state


def requests_forward_doctor(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    rid = str(data.get("request_id") or state.get("requests_selected_id") or "").strip()
    if not rid:
        state["requests_forward_status_msg"] = "Select a request first."
        state["requests_forward_status_request_id"] = None
        state["toast"] = "Select a request first."
        return state
    row = _get_request_row(rid)
    if not row:
        state["requests_forward_status_msg"] = "Request not found."
        state["requests_forward_status_request_id"] = rid
        state["toast"] = "Request not found."
        return state
    patient_id = str(row.get("patient_id") or "").strip()
    if not patient_id:
        state["requests_forward_status_msg"] = "Request has no patient ID."
        state["requests_forward_status_request_id"] = rid
        state["toast"] = "Request has no patient ID."
        return state
    ward_id = str(row.get("ward_id") or state.get("ward_id") or "ward_a").strip() or "ward_a"
    doctor_id = str(data.get("doctor_staff_id") or state.get("requests_forward_doctor_id") or "").strip()
    state["requests_forward_doctor_id"] = doctor_id
    if not doctor_id:
        state["requests_forward_status_msg"] = "Enter doctor staff ID first."
        state["requests_forward_status_request_id"] = rid
        state["toast"] = state["requests_forward_status_msg"]
        return state
    store = patient_app.get_store()
    try:
        doctor_staff = store.get_staff_by_staff_id(doctor_id)
    except Exception:
        doctor_staff = None
    if not doctor_staff or str(getattr(doctor_staff, "role", "") or "").strip().lower() != "doctor":
        state["requests_forward_status_msg"] = f"Doctor account {doctor_id} not found."
        state["requests_forward_status_request_id"] = rid
        state["toast"] = state["requests_forward_status_msg"]
        return state

    sender_id = str(state.get("staff_id") or state.get("nurse_staff_id") or "Nurse").strip() or "Nurse"
    bed_id = str(row.get("bed_id") or "").strip()
    original_summary = str(row.get("summary") or "").strip() or "Escalation request"
    original_detail = str(row.get("detail") or "").strip()
    original_chat_summary = str(row.get("chat_summary") or "").strip()
    original_audio = _upload_url_to_path(_normalize_upload_url(str(row.get("audio_path") or "")))
    raw_images = _safe_json(row.get("image_paths_json"), [])
    original_images = []
    for raw in raw_images:
        image_path = _upload_url_to_path(_normalize_upload_url(str(raw or "")))
        if image_path and os.path.exists(image_path):
            original_images.append(image_path)
    if original_audio and not os.path.exists(original_audio):
        original_audio = ""
    detail_lines = [
        f"Forwarded for doctor review from nurse {sender_id}.",
        f"Original request ID: {rid}",
        f"Patient: {patient_id}",
        f"Bed: {bed_id or '--'}",
        "",
        "Original summary:",
        original_summary,
    ]
    if original_detail:
        detail_lines.extend(["", "Original detail:", original_detail])
    if original_chat_summary:
        detail_lines.extend(["", "Original chat summary:", original_chat_summary])
    detail_text = "\n".join(detail_lines).strip()

    new_request_id = create_escalation_request(
        patient_id=patient_id,
        ward_id=ward_id,
        bed_id=bed_id,
        summary=f"Doctor review requested: {original_summary}",
        detail=detail_text,
        tags=["MD review", f"To {doctor_id}", "Forwarded by nurse"],
        chat_summary=original_chat_summary or original_summary,
        audio_src_path=original_audio,
        image_src_paths=original_images,
        status="pending",
    )
    if new_request_id:
        _update_request_status(rid, "in_progress")
        state["requests_forward_status_msg"] = f"Forwarded to doctor {doctor_id}."
        state["requests_forward_status_request_id"] = rid
        state["toast"] = state["requests_forward_status_msg"]
    else:
        state["requests_forward_status_msg"] = "Forward failed. Please retry."
        state["requests_forward_status_request_id"] = rid
        state["toast"] = state["requests_forward_status_msg"]
    return state


def requests_generate_assessment(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    rid = data.get("request_id") or state.get("requests_selected_id")
    if not rid:
        state["requests_assessment_status_msg"] = "Select a request first."
        state["requests_assessment_status_request_id"] = None
        return state
    state["requests_selected_id"] = rid
    row = _get_request_row(str(rid))
    if not row:
        state["requests_assessment_status_msg"] = "Request not found."
        state["requests_assessment_status_request_id"] = str(rid)
        return state

    patient_id = str(row.get("patient_id") or "").strip()
    if not patient_id:
        state["requests_assessment_status_msg"] = "Request has no patient id."
        state["requests_assessment_status_request_id"] = str(rid)
        return state
    state["nurse_selected_patient"] = patient_id

    summary = str(row.get("summary") or "").strip()
    detail = str(row.get("detail") or "").strip()
    chat_summary = str(row.get("chat_summary") or "").strip()
    history_lines = []
    if detail:
        history_lines.append(detail)
    if chat_summary:
        history_lines.append(f"Patient chat summary: {chat_summary}")
    history_text = "\n".join(history_lines).strip() or "Escalation follow-up."

    image_obj = None
    raw_images = _safe_json(row.get("image_paths_json"), [])
    for raw in raw_images:
        image_url = _normalize_upload_url(str(raw or ""))
        image_path = _upload_url_to_path(image_url) if image_url else ""
        if not image_path or not os.path.exists(image_path):
            continue
        try:
            from PIL import Image

            image_obj = Image.open(image_path).convert("RGB")
            break
        except Exception:
            image_obj = None

    audio_url = _normalize_upload_url(str(row.get("audio_path") or ""))
    audio_path = _upload_url_to_path(audio_url) if audio_url else ""
    if audio_path and not os.path.exists(audio_path):
        audio_path = ""

    req_payload = {
        "age": data.get("age"),
        "sex": data.get("sex"),
        "chief": summary or "Escalation follow-up",
        "history": history_text,
        "intern_plan": "",
        "timestamp": _now_iso(),
    }
    try:
        agent = _get_ward_agent()
        result = agent.handle(
            mode="generate_assessment",
            role="nurse",
            patient_id=patient_id,
            ward_id=state.get("ward_id"),
            payload=req_payload,
            image=image_obj,
            audio_path=audio_path,
            request_id=uuid.uuid4().hex,
        )
        generated = result.get("result") if result.get("ok") else None
        if generated:
            draft = _build_assessment_edit_text(generated)
            drafts = _safe_json(state.get("requests_assessment_drafts"), {})
            drafts[str(rid)] = draft
            state["requests_assessment_drafts"] = drafts
            state["requests_assessment_status_msg"] = "Draft generated. You can edit it before sending."
            state["requests_assessment_status_request_id"] = str(rid)
        else:
            state["requests_assessment_status_msg"] = "Draft generation returned no result."
            state["requests_assessment_status_request_id"] = str(rid)
    except Exception:
        state["requests_assessment_status_msg"] = "Draft generation failed. Please retry."
        state["requests_assessment_status_request_id"] = str(rid)
    return state


def requests_assessment_draft(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    rid = data.get("request_id") or state.get("requests_selected_id")
    if not rid:
        state["toast"] = "Select a request first."
        return state
    text = str(data.get("text") or "").strip()
    drafts = _safe_json(state.get("requests_assessment_drafts"), {})
    drafts[str(rid)] = text
    state["requests_assessment_drafts"] = drafts
    state["requests_assessment_status_msg"] = "Draft saved. Not sent to patient."
    state["requests_assessment_status_request_id"] = str(rid)
    state["toast"] = "Draft saved."
    return state


def requests_assessment_send(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    rid = data.get("request_id") or state.get("requests_selected_id")
    if not rid:
        state["toast"] = "Select a request first."
        state["requests_assessment_status_msg"] = "Select a request first."
        state["requests_assessment_status_request_id"] = None
        return state
    row = _get_request_row(str(rid))
    patient_id = str(data.get("patient_id") or (row or {}).get("patient_id") or "").strip()
    if not patient_id:
        state["toast"] = "Missing patient id."
        state["requests_assessment_status_msg"] = "Failed to send: missing patient id."
        state["requests_assessment_status_request_id"] = str(rid)
        return state

    drafts = _safe_json(state.get("requests_assessment_drafts"), {})
    text = str(data.get("text") or drafts.get(str(rid)) or "").strip()
    if not text:
        state["toast"] = "Draft is empty."
        state["requests_assessment_status_msg"] = "Draft is empty."
        state["requests_assessment_status_request_id"] = str(rid)
        return state

    drafts[str(rid)] = text
    state["requests_assessment_drafts"] = drafts
    sender_name = str(state.get("staff_display_name") or state.get("staff_id") or "Nurse")
    ok = _insert_inbox_message(
        patient_id=patient_id,
        sender_name=sender_name,
        subject="Assessment update",
        body=text,
    )
    if ok:
        state["requests_assessment_status_msg"] = "Sent to patient inbox."
        state["requests_assessment_status_request_id"] = str(rid)
        state["toast"] = "Sent to patient."
    else:
        state["requests_assessment_status_msg"] = "Failed to send to patient inbox."
        state["requests_assessment_status_request_id"] = str(rid)
        state["toast"] = "Failed to send."
    return state


def vitals_save(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    patient_id = data.get("patient_id") or state.get("nurse_selected_patient")
    if not patient_id:
        state["toast"] = "Please select a patient first."
        return state
    ward_id = state.get("ward_id") or _ward_id_from_label(state.get("ward_selected_label", "Ward A"))
    state["ward_id"] = ward_id
    vitals = data.get("vitals")
    if vitals is None:
        try:
            latest_admin = patient_app.get_store().get_latest_nurse_admin(patient_id)
            vitals = _safe_json(getattr(latest_admin, "vitals_json", None), {})
        except Exception:
            vitals = {}
    meds = data.get("mar_items")
    if meds is None:
        try:
            latest_admin = patient_app.get_store().get_latest_nurse_admin(patient_id)
            meds = _safe_json(getattr(latest_admin, "administered_meds_json", None), [])
        except Exception:
            meds = []
    payload = {
        "timestamp": _now_iso(),
        "vitals_json": json.dumps(vitals, ensure_ascii=False),
        "administered_meds_json": json.dumps(meds, ensure_ascii=False),
        "notes": data.get("notes") or "",
        "nurse_id": state.get("staff_id") or state.get("nurse_staff_id"),
    }
    try:
        agent = _get_ward_agent()
        result = agent.handle(
            mode="submit_nurse_admin",
            role="nurse",
            patient_id=patient_id,
            ward_id=ward_id,
            payload=payload,
            request_id=uuid.uuid4().hex,
        )
        if result.get("ok"):
            state["toast"] = "Vitals saved successfully."
        else:
            msg = str(result.get("message") or result.get("error_code") or "unknown error").strip()
            state["toast"] = f"Failed to save vitals: {msg}."
    except Exception:
        state["toast"] = "Failed to save vitals: internal error."
    return state


def mar_save(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    patient_id = data.get("patient_id") or state.get("nurse_selected_patient")
    if not patient_id:
        state["toast"] = "Please select a patient first."
        return state
    ward_id = state.get("ward_id") or _ward_id_from_label(state.get("ward_selected_label", "Ward A"))
    state["ward_id"] = ward_id
    meds = data.get("mar_items")
    if meds is None:
        meds = []
    try:
        latest_admin = patient_app.get_store().get_latest_nurse_admin(patient_id)
        vitals = _safe_json(getattr(latest_admin, "vitals_json", None), {})
    except Exception:
        vitals = {}
    payload = {
        "timestamp": _now_iso(),
        "vitals_json": json.dumps(vitals, ensure_ascii=False),
        "administered_meds_json": json.dumps(meds, ensure_ascii=False),
        "notes": data.get("notes") or "",
        "nurse_id": state.get("staff_id") or state.get("nurse_staff_id"),
    }
    try:
        agent = _get_ward_agent()
        result = agent.handle(
            mode="submit_nurse_admin",
            role="nurse",
            patient_id=patient_id,
            ward_id=ward_id,
            payload=payload,
            request_id=uuid.uuid4().hex,
        )
        if result.get("ok"):
            state["toast"] = "MAR saved successfully."
        else:
            msg = str(result.get("message") or result.get("error_code") or "unknown error").strip()
            state["toast"] = f"Failed to save MAR: {msg}."
    except Exception:
        state["toast"] = "Failed to save MAR: internal error."
    return state


def assessment_note(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    state["assessment_note"] = data.get("note", "")
    return state


def assessment_attach_image(path: str, state: dict):
    state = state or {}
    state["assessment_image_path"] = path
    return state


def assessment_attach_audio(path: str, state: dict):
    state = state or {}
    state["assessment_audio_path"] = path
    return state


def assessment_generate(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    patient_id = data.get("patient_id") or state.get("nurse_selected_patient")
    if not patient_id:
        state["assessment_status_msg"] = "Select a patient first."
        return state
    note = data.get("note") or state.get("assessment_note") or ""
    image_path = state.get("assessment_image_path")
    audio_path = state.get("assessment_audio_path")

    image_obj = None
    if image_path and os.path.exists(image_path):
        try:
            from PIL import Image

            image_obj = Image.open(image_path).convert("RGB")
        except Exception:
            image_obj = None

    payload = {
        "age": data.get("age"),
        "sex": data.get("sex"),
        "chief": data.get("chief") or "Patient follow-up",
        "history": data.get("history") or note,
        "intern_plan": data.get("intern_plan") or "",
        "timestamp": _now_iso(),
    }
    try:
        agent = _get_ward_agent()
        result = agent.handle(
            mode="generate_assessment",
            role="nurse",
            patient_id=patient_id,
            ward_id=state.get("ward_id"),
            payload=payload,
            image=image_obj,
            audio_path=audio_path,
            request_id=uuid.uuid4().hex,
        )
        state["assessment_result"] = result.get("result") if result.get("ok") else None
        if result.get("ok"):
            state["assessment_edit_text"] = _build_assessment_edit_text(state.get("assessment_result") or {})
            diag = ((state.get("assessment_result") or {}).get("diagnosis") or {})
            diag_error = str(diag.get("error") or "").strip() if isinstance(diag, dict) else ""
            if diag_error:
                if "out of memory" in diag_error.lower():
                    state["assessment_status_msg"] = "Assessment failed: GPU memory is insufficient. Try again with shorter note or without media."
                else:
                    state["assessment_status_msg"] = f"Assessment failed: {diag_error}"
            else:
                state["assessment_status_msg"] = "Assessment pipeline completed."
        else:
            msg = str(result.get("message") or result.get("error_code") or "unknown error").strip()
            state["assessment_status_msg"] = f"Assessment pipeline returned no result: {msg}."
    except Exception:
        state["assessment_result"] = None
        state["assessment_status_msg"] = "Assessment pipeline failed. Please retry."
    return state


def assessment_edit_save(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    text = str(data.get("text") or "").strip()
    state["assessment_edit_text"] = text
    state["assessment_status_msg"] = "Assessment draft saved."
    state["toast"] = "Assessment draft saved."
    return state


def assessment_send_patient(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    patient_id = data.get("patient_id") or state.get("nurse_selected_patient")
    if not patient_id:
        state["assessment_status_msg"] = "Select a patient first."
        state["toast"] = "Select a patient first."
        return state
    text = str(data.get("text") or state.get("assessment_edit_text") or "").strip()
    if not text:
        state["assessment_status_msg"] = "Assessment draft is empty."
        state["toast"] = "Assessment draft is empty."
        return state
    sender_name = str(state.get("staff_display_name") or state.get("staff_id") or "Nurse")
    ok = _insert_inbox_message(
        patient_id=str(patient_id),
        sender_name=sender_name,
        subject="Assessment update",
        body=text,
    )
    if ok:
        state["assessment_status_msg"] = "Assessment sent to patient inbox."
        state["toast"] = "Sent to patient."
    else:
        state["assessment_status_msg"] = "Failed to send assessment to patient."
        state["toast"] = "Failed to send."
    return state


def handover_generate(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    patient_id = data.get("patient_id") or state.get("nurse_selected_patient")
    if not patient_id:
        state["handover_status_msg"] = "Select a patient first."
        return state
    try:
        agent = _get_ward_agent()
        result = agent.handle(
            mode="generate_handover_draft",
            role="nurse",
            patient_id=patient_id,
            ward_id=state.get("ward_id"),
            payload={"lang": "en"},
            request_id=uuid.uuid4().hex,
        )
        if result.get("ok"):
            generated_sbar = result.get("sbar_md") or ""
            state["handover_sbar_md"] = generated_sbar
            state["handover_key_points"] = result.get("key_points") or []
            state["handover_snapshot_id"] = result.get("related_snapshot_id")
            state["handover_status_msg"] = "SBAR generated."
            state["handover_forward_status_msg"] = ""
            state["handover_forward_text"] = generated_sbar
        else:
            state["handover_status_msg"] = "SBAR generation returned no result."
    except Exception:
        state["handover_status_msg"] = "SBAR generation failed. Please retry."
    return state


def handover_save(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    patient_id = data.get("patient_id") or state.get("nurse_selected_patient")
    if not patient_id:
        return state
    sbar_md = data.get("sbar_md") or state.get("handover_sbar_md") or ""
    try:
        agent = _get_ward_agent()
        agent.handle(
            mode="save_handover",
            role="nurse",
            patient_id=patient_id,
            ward_id=state.get("ward_id"),
            payload={
                "sbar_md": sbar_md,
                "key_points": state.get("handover_key_points") or [],
                "related_snapshot_id": state.get("handover_snapshot_id"),
            },
            request_id=uuid.uuid4().hex,
        )
    except Exception:
        pass
    return state


def handover_forward(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    patient_id = data.get("patient_id") or state.get("nurse_selected_patient")
    target_staff_id = str(data.get("target_staff_id") or "").strip()
    sbar_md = str(data.get("sbar_md") or state.get("handover_sbar_md") or "").strip()
    forward_text = str(data.get("forward_text") or state.get("handover_forward_text") or sbar_md).strip()
    audio_src_path = str(state.get("handover_forward_audio_path") or "").strip()
    image_src_path = str(state.get("handover_forward_image_path") or "").strip()
    image_src_paths = [image_src_path] if image_src_path else []
    state["handover_forward_target_staff_id"] = target_staff_id
    state["handover_forward_text"] = forward_text

    if not patient_id:
        state["handover_forward_status_msg"] = "Select a patient first."
        state["handover_status_msg"] = "Select a patient first."
        state["toast"] = "Select a patient first."
        return state
    if not sbar_md:
        state["handover_forward_status_msg"] = "Generate SBAR first."
        state["handover_status_msg"] = "Generate SBAR first."
        state["toast"] = "Generate SBAR first."
        return state
    if not target_staff_id:
        state["handover_forward_status_msg"] = "Enter target nurse ID."
        state["handover_status_msg"] = "Enter target nurse ID."
        state["toast"] = "Enter target nurse ID."
        return state
    sender_id = str(state.get("staff_id") or state.get("nurse_staff_id") or "").strip()

    store = patient_app.get_store()
    try:
        target_staff = store.get_staff_by_staff_id(target_staff_id)
    except Exception:
        target_staff = None
    if not target_staff:
        state["handover_forward_status_msg"] = f"Target nurse {target_staff_id} not found."
        state["handover_status_msg"] = state["handover_forward_status_msg"]
        state["toast"] = state["handover_forward_status_msg"]
        return state
    if str(getattr(target_staff, "role", "") or "").lower() != "nurse":
        state["handover_forward_status_msg"] = f"{target_staff_id} is not a nurse account."
        state["handover_status_msg"] = state["handover_forward_status_msg"]
        state["toast"] = state["handover_forward_status_msg"]
        return state

    try:
        patient = store.get_patient(str(patient_id))
        bed_id = str(getattr(patient, "bed_id", "") or "")
    except Exception:
        bed_id = ""

    sender_id = sender_id or "Nurse"
    target_ward_id = str(getattr(target_staff, "ward_id", "") or state.get("ward_id") or "ward_a")
    summary = f"Handover from {sender_id} to {target_staff_id}"
    detail = (
        f"Forwarded SBAR handover for patient {patient_id}.\n"
        f"From: {sender_id}\n"
        f"To: {target_staff_id}\n\n"
        f"{forward_text or sbar_md}"
    )
    request_id = create_escalation_request(
        patient_id=str(patient_id),
        ward_id=target_ward_id,
        bed_id=bed_id,
        summary=summary,
        detail=detail,
        tags=["Handover", f"To {target_staff_id}"],
        chat_summary="Forwarded from SBAR handover.",
        audio_src_path=audio_src_path,
        image_src_paths=image_src_paths,
        status="pending",
    )
    if request_id:
        state["handover_forward_status_msg"] = f"Forwarded to {target_staff_id}."
        state["handover_status_msg"] = state["handover_forward_status_msg"]
        state["toast"] = state["handover_forward_status_msg"]
        state["handover_forward_audio_path"] = None
        state["handover_forward_image_path"] = None
    else:
        state["handover_forward_status_msg"] = "Forward failed. Please retry."
        state["handover_status_msg"] = state["handover_forward_status_msg"]
        state["toast"] = state["handover_forward_status_msg"]
    return state


def handover_forward_attach_image(path: str, state: dict):
    state = state or {}
    state["handover_forward_image_path"] = path
    state["handover_forward_status_msg"] = ""
    return state


def handover_forward_attach_audio(path: str, state: dict):
    state = state or {}
    state["handover_forward_audio_path"] = path
    state["handover_forward_status_msg"] = ""
    return state


def handover_range(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    valid_ranges = {"Today", "Last 3 days"}
    selected_range = str(data.get("range") or "Today")
    state["handover_range"] = selected_range if selected_range in valid_ranges else "Today"
    return state


def staff_settings_save(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    existing_name = str(state.get("staff_display_name") or "").strip()
    existing_avatar = str(state.get("staff_avatar_data") or "").strip()
    next_name = str(data.get("display_name") or "").strip() or existing_name
    next_avatar = str(data.get("avatar_data") or "").strip() or existing_avatar
    state["staff_display_name"] = next_name
    state["staff_avatar_data"] = next_avatar
    _save_staff_prefs(
        state.get("staff_id") or state.get("nurse_staff_id"),
        display_name=next_name,
        avatar_data=next_avatar,
    )
    state["toast"] = "Saved"
    return state


def staff_settings_pass(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    staff_id = state.get("staff_id") or state.get("nurse_staff_id") or ""
    role = state.get("role") or "nurse"
    ok, message = credentials.change_password(
        account_key=str(staff_id),
        role=str(role),
        old_password=str(data.get("old") or ""),
        new_password=str(data.get("new") or ""),
        confirm_password=str(data.get("confirm") or ""),
    )
    state["toast"] = message if ok else message
    return state


def get_doctor_sidebar_data(state: dict) -> dict:
    staff_id = state.get("staff_id") or state.get("nurse_staff_id") or "Doctor"
    display_name = state.get("staff_display_name") or staff_id
    avatar = state.get("staff_avatar_data") or ""
    if not avatar:
        try:
            avatar = patient_app._avatar_data_uri(display_name)
        except Exception:
            avatar = _LOGO_DATA
    return {"staff_id": staff_id, "display_name": display_name, "role": "Doctor", "avatar": avatar}


def _doctor_risk_bucket(snapshot) -> tuple[str, str, int]:
    level = str(getattr(snapshot, "risk_level", "") or "stable").lower()
    score = int(getattr(snapshot, "risk_score", 0) or 0) if snapshot else 0
    if level in ("high", "red"):
        return "High Priority", "attention", score
    if level in ("medium", "yellow"):
        return "Needs Attention", "attention", score
    return "Stable", "stable", score


def _doctor_ward_picker(state: dict) -> dict:
    wards = _list_wards()
    ward_map = {str(w).strip().lower(): str(w).strip() for w in wards if str(w).strip()}
    selected_raw = str(state.get("ward_id") or "").strip()
    selected = ward_map.get(selected_raw.lower()) if selected_raw else None
    if not selected:
        selected = wards[0] if wards else "ward_a"
    options = [{"value": w, "label": _ward_label(w)} for w in wards]
    return {"options": options, "selected": selected, "ward_label": _ward_label(selected)}


def _doctor_patient_picker(state: dict) -> dict:
    store = patient_app.get_store()
    ward_picker = _doctor_ward_picker(state)
    ward_id = ward_picker.get("selected") or "ward_a"
    state["ward_id"] = ward_id
    state["ward_selected_label"] = _ward_label(ward_id)
    try:
        patients = store.list_patients_by_ward(ward_id)
    except Exception:
        patients = []
    options = []
    for p in patients:
        label = f"Bed {p.bed_id} | {p.patient_id}" if p.bed_id else p.patient_id
        options.append({"value": p.patient_id, "label": label, "bed_id": p.bed_id})
    selected = str(state.get("doctor_selected_patient") or "").strip()
    option_values = {str(o.get("value") or "") for o in options}
    if not selected or selected not in option_values:
        selected = options[0]["value"] if options else None
    return {"options": options, "selected": selected, "ward_label": _ward_label(ward_id)}


def get_doctor_dashboard_data(state: dict) -> dict:
    store = patient_app.get_store()
    ward_picker = _doctor_ward_picker(state)
    ward_id = ward_picker.get("selected") or "ward_a"
    state["ward_id"] = ward_id
    state["ward_selected_label"] = _ward_label(ward_id)
    search = (state.get("doctor_search") or "").strip()
    filter_tag = state.get("doctor_filter") or "All"

    patients = []
    try:
        all_patients = store.list_patients_by_ward(ward_id)
    except Exception:
        all_patients = []

    for p in all_patients:
        if search and search.lower() not in (p.patient_id or "").lower() and search.lower() not in (p.bed_id or "").lower():
            continue
        latest_admin = store.get_latest_nurse_admin(p.patient_id)
        latest_assessment = store.get_latest_assessment(p.patient_id)
        latest_handover = store.get_latest_handover(p.patient_id)
        latest_risk = store.get_latest_risk_snapshot(p.patient_id)
        vitals = _safe_json(getattr(latest_admin, "vitals_json", None), {})
        risk_label, risk_level, risk_score = _doctor_risk_bucket(latest_risk)
        if filter_tag == "Stable" and risk_level != "stable":
            continue
        if filter_tag == "Needs Attention" and risk_level == "stable":
            continue
        patients.append(
            {
                "bed_id": p.bed_id or "--",
                "patient_id": p.patient_id,
                "risk_level": risk_level,
                "risk_label": risk_label,
                "risk_score": risk_score,
                "last_vitals": _format_vitals(vitals),
                "last_assessment": "Updated" if latest_assessment else "Pending",
                "last_handover": "Ready" if latest_handover else "None",
            }
        )

    pending = _load_requests(ward_id, "pending", "", "All")
    in_progress = _load_requests(ward_id, "in_progress", "", "All")
    done = _load_requests(ward_id, "done", "", "All")
    source_count = {"Patient": 0, "Nurse": 0, "Doctor": 0}
    for r in pending:
        src = str(r.get("source_category") or "Patient")
        if src in source_count:
            source_count[src] += 1

    return {
        "ward_picker": ward_picker,
        "ward_label": _ward_label(ward_id),
        "shift": _auto_shift_label(),
        "search": search,
        "filter": filter_tag,
        "patients": patients,
        "pending_count": len(pending),
        "in_progress_count": len(in_progress),
        "done_count": len(done),
        "source_count": source_count,
    }


def get_doctor_patient360_data(state: dict) -> dict:
    store = patient_app.get_store()
    ward_picker = _doctor_ward_picker(state)
    state["ward_id"] = ward_picker.get("selected") or "ward_a"
    state["ward_selected_label"] = _ward_label(state["ward_id"])
    picker = _doctor_patient_picker(state)
    patient_id = picker.get("selected")
    patient = store.get_patient(patient_id) if patient_id else None
    latest_risk = store.get_latest_risk_snapshot(patient_id) if patient_id else None
    latest_assessment = store.get_latest_assessment(patient_id) if patient_id else None
    latest_handover = store.get_latest_handover(patient_id) if patient_id else None
    latest_admin = store.get_latest_nurse_admin(patient_id) if patient_id else None

    daily_logs = store.list_daily_logs(patient_id, limit=5) if patient_id else []
    nurse_admin_logs = store.list_nurse_admin(patient_id, limit=5) if patient_id else []
    chat_summaries = store.list_chat_summaries(patient_id, limit=5) if patient_id else []

    vitals_now = _safe_json(getattr(latest_admin, "vitals_json", None), {})
    mar_now = _safe_json(getattr(latest_admin, "administered_meds_json", None), [])
    diag = _safe_json(getattr(latest_assessment, "diagnosis_json", None), {})
    audit = _safe_json(getattr(latest_assessment, "audit_json", None), {})
    reverse = _safe_json(getattr(latest_assessment, "reverse_json", None), {})
    evidence = _safe_json(getattr(latest_assessment, "rag_evidence_json", None), [])
    tool_trace = _safe_json(getattr(latest_assessment, "tool_trace_json", None), [])
    gaps = _safe_json(getattr(latest_assessment, "gaps_json", None), [])
    flags = _safe_json(getattr(latest_risk, "flags_json", None), [])
    next_actions = _safe_json(getattr(latest_risk, "next_actions_json", None), [])

    timeline_daily = []
    for item in daily_logs[:5]:
        timeline_daily.append(
            {
                "time": str(getattr(item, "date", "") or ""),
                "text": f"Diet {getattr(item, 'diet', '--') or '--'}, Sleep {getattr(item, 'sleep_hours', '--') or '--'}h",
            }
        )
    timeline_admin = []
    for item in nurse_admin_logs[:5]:
        vv = _safe_json(getattr(item, "vitals_json", None), {})
        meds = _safe_json(getattr(item, "administered_meds_json", None), [])
        timeline_admin.append(
            {
                "time": str(getattr(item, "timestamp", "") or "")[:16],
                "text": f"{_format_vitals(vv)} | MAR {_format_last_mar(meds)}",
            }
        )
    timeline_chat = []
    for item in chat_summaries[:5]:
        timeline_chat.append(
            {
                "time": str(getattr(item, "timestamp", "") or "")[:16],
                "text": str(getattr(item, "summary_text", "") or ""),
            }
        )
    drafts = _safe_json(state.get("doctor_notes_drafts"), {})
    note_text = str(drafts.get(str(patient_id or "")) or "").strip()
    note_status_msg = ""
    if str(state.get("doctor_note_status_patient_id") or "") == str(patient_id or ""):
        note_status_msg = str(state.get("doctor_note_status_msg") or "").strip()
    assessment_drafts = _safe_json(state.get("doctor_assessment_drafts"), {})
    assessment_note_text = str(assessment_drafts.get(str(patient_id or "")) or "").strip()
    assessment_status_msg = ""
    if str(state.get("doctor_assessment_status_patient_id") or "") == str(patient_id or ""):
        assessment_status_msg = str(state.get("doctor_assessment_status_msg") or "").strip()

    risk_label, risk_level, risk_score = _doctor_risk_bucket(latest_risk)
    return {
        "ward_picker": ward_picker,
        "picker": picker,
        "patient": {
            "patient_id": getattr(patient, "patient_id", patient_id) if patient_id else "",
            "bed_id": getattr(patient, "bed_id", "") if patient else "",
            "ward_id": getattr(patient, "ward_id", "") if patient else "",
            "age": getattr(patient, "age", "") if patient else "",
            "sex": getattr(patient, "sex", "") if patient else "",
            "allergy_history": _get_patient_allergy_history(str(patient_id or "")),
        },
        "risk": {
            "label": risk_label,
            "level": risk_level,
            "score": risk_score,
            "flags": flags if isinstance(flags, list) else [],
            "next_actions": next_actions if isinstance(next_actions, list) else [],
        },
        "current": {
            "vitals_text": _format_vitals(vitals_now),
            "mar_text": _format_last_mar(mar_now if isinstance(mar_now, list) else []),
            "handover_time": str(getattr(latest_handover, "created_at", "") or "")[:16],
            "handover_text": str(getattr(latest_handover, "sbar_md", "") or "").strip(),
        },
        "timeline_daily": timeline_daily,
        "timeline_admin": timeline_admin,
        "timeline_chat": timeline_chat,
        "assessment": {
            "timestamp": str(getattr(latest_assessment, "timestamp", "") or "")[:16],
            "primary_diagnosis": str(diag.get("primary_diagnosis") or "Not specified"),
            "risk_level": str(diag.get("risk_level") or "Not specified"),
            "confidence_score": diag.get("confidence_score"),
            "summary": str(diag.get("summary") or ""),
            "audit": audit if isinstance(audit, dict) else {},
            "reverse": reverse if isinstance(reverse, dict) else {},
            "evidence": evidence if isinstance(evidence, list) else [],
            "tool_trace": tool_trace if isinstance(tool_trace, list) else [],
            "gaps": gaps if isinstance(gaps, list) else [],
        },
        "assessment_note_text": assessment_note_text,
        "assessment_status_msg": assessment_status_msg,
        "note_text": note_text,
        "note_status_msg": note_status_msg,
    }


def get_doctor_orders_data(state: dict) -> dict:
    store = patient_app.get_store()
    ward_picker = _doctor_ward_picker(state)
    state["ward_id"] = ward_picker.get("selected") or "ward_a"
    state["ward_selected_label"] = _ward_label(state["ward_id"])
    picker = _doctor_patient_picker(state)
    patient_id = picker.get("selected")
    patient = store.get_patient(patient_id) if patient_id else None

    plan_drafts = _safe_json(state.get("doctor_orders_plan_drafts"), {})
    preview_drafts = _safe_json(state.get("doctor_orders_preview_drafts"), {})
    cached_plan = str(plan_drafts.get(str(patient_id or "")) or "").strip()
    cached_preview = str(preview_drafts.get(str(patient_id or "")) or "").strip()
    if patient_id and not (cached_plan or cached_preview):
        stored = _load_doctor_orders_plan(str(patient_id))
        cached_plan = str(stored.get("plan_text") or "").strip()
        cached_preview = str(stored.get("patient_preview_text") or "").strip()
        if cached_plan:
            plan_drafts[str(patient_id)] = cached_plan
        if cached_preview:
            preview_drafts[str(patient_id)] = cached_preview
        state["doctor_orders_plan_drafts"] = plan_drafts
        state["doctor_orders_preview_drafts"] = preview_drafts

    if patient_id:
        if not cached_plan:
            latest_assessment = store.get_latest_assessment(str(patient_id))
            diag = _safe_json(getattr(latest_assessment, "diagnosis_json", None), {})
            suggestions = diag.get("treatment_suggestions") if isinstance(diag.get("treatment_suggestions"), list) else []
            if suggestions:
                cached_plan = "\n".join(f"- {str(x).strip()}" for x in suggestions if str(x).strip())
                plan_drafts[str(patient_id)] = cached_plan
                state["doctor_orders_plan_drafts"] = plan_drafts
            if not cached_preview and cached_plan:
                cached_preview = _doctor_plan_to_patient_preview(cached_plan)
                preview_drafts[str(patient_id)] = cached_preview
                state["doctor_orders_preview_drafts"] = preview_drafts

    status_msg = ""
    if str(state.get("doctor_orders_status_patient_id") or "") == str(patient_id or ""):
        status_msg = str(state.get("doctor_orders_status_msg") or "").strip()
    return {
        "ward_picker": ward_picker,
        "picker": picker,
        "patient": {
            "patient_id": getattr(patient, "patient_id", patient_id) if patient_id else "",
            "bed_id": getattr(patient, "bed_id", "") if patient else "",
            "age": getattr(patient, "age", "") if patient else "",
            "sex": getattr(patient, "sex", "") if patient else "",
        },
        "plan_text": cached_plan,
        "preview_text": cached_preview,
        "status_msg": status_msg,
    }


def get_doctor_inbox_data(state: dict) -> dict:
    ward_picker = _doctor_ward_picker(state)
    ward_id = ward_picker.get("selected") or "ward_a"
    state["ward_id"] = ward_id
    state["ward_selected_label"] = _ward_label(ward_id)
    filter_tab = state.get("doctor_inbox_filter", "Pending")
    source_filter = state.get("doctor_inbox_source_filter", "All")
    search = state.get("doctor_inbox_search", "")
    selected_id = state.get("doctor_inbox_selected_id")
    status_map = {
        "Pending": "pending",
        "In Progress": "in_progress",
        "Done": "done",
        "All": "all",
    }
    requests = _load_requests(ward_id, status_map.get(filter_tab, "pending"), search, str(source_filter or "All"))
    selected = next((r for r in requests if r["request_id"] == selected_id), None)
    if not selected and requests:
        selected = requests[0]

    status_msg = ""
    if selected and str(state.get("doctor_inbox_status_request_id") or "") == str(selected.get("request_id") or ""):
        status_msg = str(state.get("doctor_inbox_status_msg") or "").strip()
    return {
        "ward_picker": ward_picker,
        "ward_label": _ward_label(ward_id),
        "filter": filter_tab,
        "source_filter": source_filter,
        "search": search,
        "requests": requests,
        "selected": selected,
        "status_msg": status_msg,
    }


def doctor_update(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    if "ward_id" in data:
        next_ward_raw = str(data.get("ward_id") or "").strip()
        all_wards = _list_wards()
        ward_map = {str(w).strip().lower(): str(w).strip() for w in all_wards if str(w).strip()}
        resolved_ward = ward_map.get(next_ward_raw.lower()) if next_ward_raw else None
        if resolved_ward:
            previous_ward = str(state.get("ward_id") or "").strip().lower()
            state["ward_id"] = resolved_ward
            state["ward_selected_label"] = _ward_label(resolved_ward)
            if previous_ward != resolved_ward.lower():
                state["doctor_selected_patient"] = None
                state["doctor_orders_status_msg"] = ""
                state["doctor_note_status_msg"] = ""
                state["doctor_assessment_status_msg"] = ""
        elif next_ward_raw:
            state["toast"] = "Invalid ward."
    if "filter" in data:
        state["doctor_filter"] = data.get("filter") or "All"
    if "search" in data:
        state["doctor_search"] = data.get("search") or ""
    return state


def doctor_select_patient(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    patient_id = data.get("patient_id")
    if patient_id:
        state["doctor_selected_patient"] = str(patient_id)
        if str(state.get("doctor_orders_status_patient_id") or "") != str(patient_id):
            state["doctor_orders_status_msg"] = ""
        if str(state.get("doctor_note_status_patient_id") or "") != str(patient_id):
            state["doctor_note_status_msg"] = ""
        if str(state.get("doctor_assessment_status_patient_id") or "") != str(patient_id):
            state["doctor_assessment_status_msg"] = ""
    return state


def doctor_assessment_generate(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    patient_id = str(data.get("patient_id") or state.get("doctor_selected_patient") or "").strip()
    if not patient_id:
        state["doctor_assessment_status_msg"] = "Select a patient first."
        state["doctor_assessment_status_patient_id"] = None
        state["toast"] = "Select a patient first."
        return state

    note = str(data.get("note") or "").strip()
    assessment_drafts = _safe_json(state.get("doctor_assessment_drafts"), {})
    assessment_drafts[patient_id] = note
    state["doctor_assessment_drafts"] = assessment_drafts

    store = patient_app.get_store()
    patient = store.get_patient(patient_id)
    assessment_payload = {
        "age": getattr(patient, "age", None) if patient else None,
        "sex": getattr(patient, "sex", None) if patient else None,
        "chief": "Doctor reassessment",
        "history": note or "Doctor requested reassessment based on current clinical timeline.",
        "intern_plan": "",
        "timestamp": _now_iso(),
    }
    try:
        agent = _get_ward_agent()
        result = agent.handle(
            mode="generate_assessment",
            role="doctor",
            patient_id=patient_id,
            ward_id=state.get("ward_id"),
            payload=assessment_payload,
            request_id=uuid.uuid4().hex,
        )
        if result.get("ok"):
            generated = result.get("result") or {}
            diag = generated.get("diagnosis") if isinstance(generated, dict) else {}
            diag_error = str(diag.get("error") or "").strip() if isinstance(diag, dict) else ""
            if diag_error:
                state["doctor_assessment_status_msg"] = f"Assessment returned with warning: {diag_error}"
                state["toast"] = "Assessment finished with warning."
            else:
                state["doctor_assessment_status_msg"] = "Assessment regenerated and saved."
                state["toast"] = "Assessment regenerated."
        else:
            msg = str(result.get("message") or result.get("error_code") or "unknown error").strip()
            state["doctor_assessment_status_msg"] = f"Assessment failed: {msg}."
            state["toast"] = "Assessment failed."
    except Exception:
        state["doctor_assessment_status_msg"] = "Assessment pipeline failed. Please retry."
        state["toast"] = "Assessment failed."
    state["doctor_assessment_status_patient_id"] = patient_id
    return state


def doctor_note_save(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    patient_id = str(data.get("patient_id") or state.get("doctor_selected_patient") or "").strip()
    if not patient_id:
        state["toast"] = "Select a patient first."
        return state
    text = str(data.get("text") or "").strip()
    drafts = _safe_json(state.get("doctor_notes_drafts"), {})
    drafts[patient_id] = text
    state["doctor_notes_drafts"] = drafts
    state["doctor_note_status_msg"] = "Draft saved."
    state["doctor_note_status_patient_id"] = patient_id
    state["toast"] = "Draft saved."
    return state


def doctor_note_send(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    patient_id = str(data.get("patient_id") or state.get("doctor_selected_patient") or "").strip()
    if not patient_id:
        state["doctor_note_status_msg"] = "Select a patient first."
        state["doctor_note_status_patient_id"] = None
        state["toast"] = "Select a patient first."
        return state
    text = str(data.get("text") or "").strip()
    if not text:
        state["doctor_note_status_msg"] = "Message is empty."
        state["doctor_note_status_patient_id"] = patient_id
        state["toast"] = "Message is empty."
        return state
    sender_name = str(state.get("staff_display_name") or state.get("staff_id") or "Doctor")
    ok = _insert_inbox_message(
        patient_id=patient_id,
        sender_name=sender_name,
        subject="Doctor update",
        body=text,
    )
    if ok:
        drafts = _safe_json(state.get("doctor_notes_drafts"), {})
        drafts[patient_id] = text
        state["doctor_notes_drafts"] = drafts
        state["doctor_note_status_msg"] = "Sent to patient inbox."
        state["doctor_note_status_patient_id"] = patient_id
        state["toast"] = "Sent to patient."
    else:
        state["doctor_note_status_msg"] = "Failed to send."
        state["doctor_note_status_patient_id"] = patient_id
        state["toast"] = "Failed to send."
    return state


def doctor_orders_preview(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    patient_id = str(data.get("patient_id") or state.get("doctor_selected_patient") or "").strip()
    if not patient_id:
        state["doctor_orders_status_msg"] = "Select a patient first."
        state["doctor_orders_status_patient_id"] = None
        state["toast"] = "Select a patient first."
        return state
    plan_text = str(data.get("plan_text") or "").strip()
    if not plan_text:
        drafts = _safe_json(state.get("doctor_orders_plan_drafts"), {})
        plan_text = str(drafts.get(patient_id) or "").strip()
    preview_text = _doctor_plan_to_patient_preview(plan_text)
    plan_drafts = _safe_json(state.get("doctor_orders_plan_drafts"), {})
    preview_drafts = _safe_json(state.get("doctor_orders_preview_drafts"), {})
    plan_drafts[patient_id] = plan_text
    preview_drafts[patient_id] = preview_text
    state["doctor_orders_plan_drafts"] = plan_drafts
    state["doctor_orders_preview_drafts"] = preview_drafts
    state["doctor_orders_status_msg"] = "Patient-friendly preview generated."
    state["doctor_orders_status_patient_id"] = patient_id
    state["toast"] = "Preview generated."
    return state


def doctor_orders_save(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    patient_id = str(data.get("patient_id") or state.get("doctor_selected_patient") or "").strip()
    if not patient_id:
        state["doctor_orders_status_msg"] = "Select a patient first."
        state["doctor_orders_status_patient_id"] = None
        state["toast"] = "Select a patient first."
        return state
    plan_text = str(data.get("plan_text") or "").strip()
    preview_text = str(data.get("preview_text") or "").strip()
    if not preview_text and plan_text:
        preview_text = _doctor_plan_to_patient_preview(plan_text)
    plan_drafts = _safe_json(state.get("doctor_orders_plan_drafts"), {})
    preview_drafts = _safe_json(state.get("doctor_orders_preview_drafts"), {})
    plan_drafts[patient_id] = plan_text
    preview_drafts[patient_id] = preview_text
    state["doctor_orders_plan_drafts"] = plan_drafts
    state["doctor_orders_preview_drafts"] = preview_drafts
    _save_doctor_orders_plan(
        patient_id=patient_id,
        plan_text=plan_text,
        patient_preview_text=preview_text,
        staff_id=str(state.get("staff_id") or ""),
    )
    state["doctor_orders_status_msg"] = "Orders & plan saved."
    state["doctor_orders_status_patient_id"] = patient_id
    state["toast"] = "Orders & plan saved."
    return state


def doctor_orders_send(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    patient_id = str(data.get("patient_id") or state.get("doctor_selected_patient") or "").strip()
    if not patient_id:
        state["doctor_orders_status_msg"] = "Select a patient first."
        state["doctor_orders_status_patient_id"] = None
        state["toast"] = "Select a patient first."
        return state
    plan_text = str(data.get("plan_text") or "").strip()
    preview_text = str(data.get("preview_text") or "").strip()
    if not plan_text:
        plan_drafts = _safe_json(state.get("doctor_orders_plan_drafts"), {})
        plan_text = str(plan_drafts.get(patient_id) or "").strip()
    if not preview_text:
        preview_drafts = _safe_json(state.get("doctor_orders_preview_drafts"), {})
        preview_text = str(preview_drafts.get(patient_id) or "").strip()
    if not preview_text and plan_text:
        preview_text = _doctor_plan_to_patient_preview(plan_text)
    if not preview_text:
        state["doctor_orders_status_msg"] = "Preview is empty. Add plan text first."
        state["doctor_orders_status_patient_id"] = patient_id
        state["toast"] = "Preview is empty."
        return state
    sender_name = str(state.get("staff_display_name") or state.get("staff_id") or "Doctor")
    ok = _insert_inbox_message(
        patient_id=patient_id,
        sender_name=sender_name,
        subject="Doctor Orders & Plan",
        body=preview_text,
    )
    if ok:
        plan_drafts = _safe_json(state.get("doctor_orders_plan_drafts"), {})
        preview_drafts = _safe_json(state.get("doctor_orders_preview_drafts"), {})
        plan_drafts[patient_id] = plan_text
        preview_drafts[patient_id] = preview_text
        state["doctor_orders_plan_drafts"] = plan_drafts
        state["doctor_orders_preview_drafts"] = preview_drafts
        _save_doctor_orders_plan(
            patient_id=patient_id,
            plan_text=plan_text,
            patient_preview_text=preview_text,
            staff_id=str(state.get("staff_id") or ""),
        )
        state["doctor_orders_status_msg"] = "Orders & plan sent to patient inbox."
        state["doctor_orders_status_patient_id"] = patient_id
        state["toast"] = "Sent to patient."
    else:
        state["doctor_orders_status_msg"] = "Failed to send plan."
        state["doctor_orders_status_patient_id"] = patient_id
        state["toast"] = "Failed to send plan."
    return state


def doctor_inbox_filter(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    state["doctor_inbox_filter"] = data.get("filter", "Pending")
    return state


def doctor_inbox_source_filter(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    value = str(data.get("source_filter") or "All")
    allowed = {"All", "Patient", "Nurse", "Doctor"}
    state["doctor_inbox_source_filter"] = value if value in allowed else "All"
    return state


def doctor_inbox_search(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    state["doctor_inbox_search"] = data.get("q", "")
    return state


def doctor_inbox_select(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    rid = data.get("request_id")
    state["doctor_inbox_selected_id"] = rid
    return state


def doctor_inbox_update(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    rid = data.get("request_id")
    status = data.get("status")
    if not rid or not status:
        return state
    status = str(status).strip().lower()
    if status not in ("pending", "in_progress", "done"):
        return state
    _update_request_status(str(rid), status)
    state["doctor_inbox_selected_id"] = rid
    state["doctor_inbox_status_msg"] = f"Marked as {status.replace('_', ' ')}."
    state["doctor_inbox_status_request_id"] = str(rid)
    state["toast"] = state["doctor_inbox_status_msg"]
    return state


def doctor_inbox_delete(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    rid = str(data.get("request_id") or state.get("doctor_inbox_selected_id") or "").strip()
    if not rid:
        state["toast"] = "Select a request first."
        state["doctor_inbox_status_msg"] = "Select a request first."
        state["doctor_inbox_status_request_id"] = None
        return state
    ok = _delete_request(rid)
    if not ok:
        state["toast"] = "Delete failed."
        state["doctor_inbox_status_msg"] = "Delete failed."
        state["doctor_inbox_status_request_id"] = rid
        return state
    if str(state.get("doctor_inbox_status_request_id") or "") == rid:
        state["doctor_inbox_status_msg"] = ""
        state["doctor_inbox_status_request_id"] = None
    ward_picker = _doctor_ward_picker(state)
    ward_id = ward_picker.get("selected") or "ward_a"
    filter_tab = state.get("doctor_inbox_filter", "Pending")
    source_filter = state.get("doctor_inbox_source_filter", "All")
    search = state.get("doctor_inbox_search", "")
    status_map = {
        "Pending": "pending",
        "In Progress": "in_progress",
        "Done": "done",
        "All": "all",
    }
    requests = _load_requests(ward_id, status_map.get(filter_tab, "pending"), search, str(source_filter or "All"))
    state["doctor_inbox_selected_id"] = requests[0]["request_id"] if requests else None
    state["toast"] = "Request deleted."
    return state


def doctor_inbox_send(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    rid = str(data.get("request_id") or state.get("doctor_inbox_selected_id") or "").strip()
    text = str(data.get("text") or "").strip()
    if not rid:
        state["toast"] = "Select a request first."
        state["doctor_inbox_status_msg"] = "Select a request first."
        state["doctor_inbox_status_request_id"] = None
        return state
    if not text:
        state["toast"] = "Message is empty."
        state["doctor_inbox_status_msg"] = "Message is empty."
        state["doctor_inbox_status_request_id"] = rid
        return state
    row = _get_request_row(rid)
    patient_id = str((row or {}).get("patient_id") or "").strip()
    if not patient_id:
        state["toast"] = "Missing patient ID."
        state["doctor_inbox_status_msg"] = "Missing patient ID."
        state["doctor_inbox_status_request_id"] = rid
        return state
    sender_name = str(state.get("staff_display_name") or state.get("staff_id") or "Doctor")
    ok = _insert_inbox_message(
        patient_id=patient_id,
        sender_name=sender_name,
        subject="Doctor response",
        body=text,
    )
    if ok:
        _update_request_status(rid, "in_progress")
        state["doctor_inbox_status_msg"] = "Doctor response sent."
        state["doctor_inbox_status_request_id"] = rid
        state["toast"] = "Response sent."
    else:
        state["doctor_inbox_status_msg"] = "Failed to send response."
        state["doctor_inbox_status_request_id"] = rid
        state["toast"] = "Failed to send response."
    return state


def doctor_settings_save(payload: str, state: dict):
    return staff_settings_save(payload, state)


def doctor_settings_pass(payload: str, state: dict):
    return staff_settings_pass(payload, state)


def doctor_create_patient(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    patient_id = str(data.get("patient_id") or "").strip()
    ward_input = str(data.get("ward_id") or state.get("ward_id") or "ward_a").strip() or "ward_a"
    ward_id = _ward_id_from_label(ward_input) if "ward" in ward_input.lower() else ward_input.lower()
    bed_id = str(data.get("bed_id") or "").strip()
    sex = str(data.get("sex") or "").strip() or None
    age_raw = str(data.get("age") or "").strip()
    allergy_history = str(data.get("allergy_history") or "").strip()
    if not patient_id:
        state["doctor_create_patient_status_msg"] = "Patient ID is required."
        state["toast"] = state["doctor_create_patient_status_msg"]
        return state
    if patient_id.upper().startswith("N-") or patient_id.upper().startswith("D-"):
        state["doctor_create_patient_status_msg"] = "Patient ID cannot start with N- or D-."
        state["toast"] = state["doctor_create_patient_status_msg"]
        return state
    age = None
    if age_raw:
        try:
            age = int(age_raw)
            if age < 0:
                raise ValueError("invalid age")
        except Exception:
            state["doctor_create_patient_status_msg"] = "Age must be a non-negative integer."
            state["toast"] = state["doctor_create_patient_status_msg"]
            return state
    try:
        from src.store.schemas import Patient

        store = patient_app.get_store()
        existing = store.get_patient(patient_id)
        created_at = getattr(existing, "created_at", "") or _now_iso()
        store.upsert_patient(
            Patient(
                patient_id=patient_id,
                ward_id=ward_id,
                bed_id=bed_id or None,
                sex=sex,
                age=age,
                created_at=created_at,
            )
        )
        _upsert_patient_allergy_history(patient_id, allergy_history)
        credentials.ensure_default_credential(
            account_key=patient_id,
            role="patient",
            default_password="Demo@123",
        )
        state["doctor_selected_patient"] = patient_id
        state["doctor_create_patient_status_msg"] = f"Patient account {patient_id} saved. Default password: Demo@123."
        state["toast"] = "Patient account saved."
    except Exception:
        state["doctor_create_patient_status_msg"] = "Failed to save patient account."
        state["toast"] = "Failed to save patient account."
    return state


def doctor_create_nurse(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state or {})
    staff_id = str(data.get("staff_id") or "").strip()
    ward_input = str(data.get("ward_id") or state.get("ward_id") or "ward_a").strip() or "ward_a"
    ward_id = _ward_id_from_label(ward_input) if "ward" in ward_input.lower() else ward_input.lower()
    name = str(data.get("name") or "").strip() or None
    email = str(data.get("email") or "").strip() or None
    if not staff_id:
        state["doctor_create_nurse_status_msg"] = "Nurse staff ID is required."
        state["toast"] = state["doctor_create_nurse_status_msg"]
        return state
    if not staff_id.upper().startswith("N-"):
        state["doctor_create_nurse_status_msg"] = "Nurse staff ID should start with N-."
        state["toast"] = state["doctor_create_nurse_status_msg"]
        return state
    try:
        from src.store.schemas import StaffAccount

        store = patient_app.get_store()
        existing = store.get_staff_by_staff_id(staff_id)
        created_at = getattr(existing, "created_at", "") or _now_iso()
        store.upsert_staff_account(
            StaffAccount(
                staff_id=staff_id,
                role="nurse",
                ward_id=ward_id,
                name=name,
                email=email,
                created_at=created_at,
            )
        )
        credentials.ensure_default_credential(
            account_key=staff_id,
            role="nurse",
            default_password="Demo@123",
        )
        state["doctor_create_nurse_status_msg"] = f"Nurse account {staff_id} saved. Default password: Demo@123."
        state["toast"] = "Nurse account saved."
    except Exception:
        state["doctor_create_nurse_status_msg"] = "Failed to save nurse account."
        state["toast"] = "Failed to save nurse account."
    return state


def get_nurse_ctx() -> dict:
    return {
        "get_nurse_sidebar_data": get_nurse_sidebar_data,
        "get_dashboard_data": get_dashboard_data,
        "get_patient_picker": get_patient_picker,
        "get_vitals_data": get_vitals_data,
        "get_assessment_data": get_assessment_data,
        "get_handover_data": get_handover_data,
        "get_inbox_data": get_inbox_data,
        "ward_label": _ward_label,
    }


def get_doctor_ctx() -> dict:
    return {
        "get_doctor_sidebar_data": get_doctor_sidebar_data,
        "get_doctor_dashboard_data": get_doctor_dashboard_data,
        "get_doctor_patient360_data": get_doctor_patient360_data,
        "get_doctor_orders_data": get_doctor_orders_data,
        "get_doctor_inbox_data": get_doctor_inbox_data,
    }
