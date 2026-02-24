from __future__ import annotations

import base64
import html
import json
import re
import os
import sqlite3
import threading
import uuid
import time
from datetime import date, datetime
from typing import Any, Optional

import gradio as gr

from src.auth import credentials
from src.ui.patient_pages import render_patient_page

_BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DB_PATH = os.path.join(_BASE_DIR, "data", "ward_demo.db")
_LOGO_DATA = ""
_ICONS: dict = {}
_USE_BACKEND_MODEL = True
_CHAT_RAG_ENABLED = True
_CHAT_RAG_TOP_K = 4
_WARMUP_ON_START = False
_BACKEND_CACHE: dict = {
    "store": None,
    "medgemma": None,
    "rag": None,
    "chat_agent": None,
    "care_card_agent": None,
    "asr": None,
}
_PATIENT_DATA_CACHE: dict = {}
_CARE_CARD_CACHE: dict = {}
_INBOX_CACHE: dict = {}
_PATIENT_CTX: Optional[dict] = None
_CHAT_LOCK = threading.Lock()
_CHAT_RESULTS: dict[str, list[dict]] = {}
_PERF_LOG = os.getenv("PERF_LOG", "1").strip().lower() in ("1", "true", "yes", "y")


def _log_perf(label: str, start: float, extra: str = "") -> None:
    if not _PERF_LOG:
        return
    elapsed_ms = (time.perf_counter() - start) * 1000
    suffix = f" | {extra}" if extra else ""
    print(f"[perf] {label}: {elapsed_ms:.1f}ms{suffix}")


def configure(
    *,
    base_dir: str,
    db_path: str,
    logo_data: str,
    icons: dict,
    use_backend_model: bool = True,
    chat_rag_enabled: bool = True,
    chat_rag_top_k: Optional[int] = None,
    warmup_on_start: bool = False,
) -> None:
    global _BASE_DIR, _DB_PATH, _LOGO_DATA, _ICONS
    global _USE_BACKEND_MODEL, _CHAT_RAG_ENABLED, _CHAT_RAG_TOP_K, _WARMUP_ON_START
    global _BACKEND_CACHE, _PATIENT_DATA_CACHE, _PATIENT_CTX

    _BASE_DIR = base_dir or _BASE_DIR
    _DB_PATH = db_path or _DB_PATH
    _LOGO_DATA = logo_data or ""
    _ICONS = icons or {}
    credentials.configure(db_path=_DB_PATH)
    _USE_BACKEND_MODEL = bool(use_backend_model)
    _CHAT_RAG_ENABLED = bool(chat_rag_enabled)
    if chat_rag_top_k is None:
        try:
            chat_rag_top_k = int(os.getenv("CHAT_RAG_TOP_K", str(_CHAT_RAG_TOP_K)))
        except Exception:
            chat_rag_top_k = _CHAT_RAG_TOP_K
    _CHAT_RAG_TOP_K = max(3, min(5, int(chat_rag_top_k)))
    _WARMUP_ON_START = bool(warmup_on_start)

    _BACKEND_CACHE = {
        "store": None,
        "medgemma": None,
        "rag": None,
        "chat_agent": None,
        "care_card_agent": None,
        "asr": None,
    }
    _PATIENT_DATA_CACHE = {}
    _CARE_CARD_CACHE = {}
    _INBOX_CACHE = {}
    _PATIENT_CTX = None


def _avatar_data_uri(name: str) -> str:
    initial = (name or "P").strip()[:1].upper() or "P"
    svg = f"""<svg xmlns='http://www.w3.org/2000/svg' width='96' height='96'>
      <defs>
        <linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>
          <stop offset='0%' stop-color='#C2F2F4'/>
          <stop offset='100%' stop-color='#6AB8C4'/>
        </linearGradient>
      </defs>
      <circle cx='48' cy='48' r='48' fill='url(#g)'/>
      <text x='50%' y='54%' text-anchor='middle' font-size='40' font-family='Segoe UI, Arial' fill='#052659' dy='.1em'>{initial}</text>
    </svg>"""
    b64 = base64.b64encode(svg.encode("utf-8")).decode("utf-8")
    return f"data:image/svg+xml;base64,{b64}"


def onclick(target_id: str) -> str:
    return "(function(){{var app=document.querySelector('gradio-app');var root=app&&app.shadowRoot?app.shadowRoot:document;var btn=root.querySelector('#{id}');if(btn)btn.click();}})();return false;".format(
        id=target_id
    )


def _get_any_patient_id() -> str:
    if not os.path.exists(_DB_PATH):
        return "demo_patient_001"
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            row = conn.execute("SELECT patient_id FROM patients LIMIT 1").fetchone()
        return row[0] if row else "demo_patient_001"
    except Exception:
        return "demo_patient_001"


def _safe_json(value: Any, default: Any):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def get_store():
    store = _BACKEND_CACHE.get("store")
    if store is not None:
        return store
    from src.store.sqlite_store import SQLiteStore

    start = time.perf_counter()
    store = SQLiteStore(_DB_PATH)
    store.init_db()
    _log_perf("init SQLiteStore", start, _DB_PATH)
    _BACKEND_CACHE["store"] = store
    return store


def _get_medgemma_client():
    if not _USE_BACKEND_MODEL:
        return None
    client = _BACKEND_CACHE.get("medgemma")
    if client is not None:
        return client
    from src.agents.observer import MedGemmaClient

    start = time.perf_counter()
    client = MedGemmaClient()
    _log_perf("init MedGemmaClient", start)
    _BACKEND_CACHE["medgemma"] = client
    return client


def _get_rag_engine():
    if not (_USE_BACKEND_MODEL and _CHAT_RAG_ENABLED):
        return None
    engine = _BACKEND_CACHE.get("rag")
    if engine is not None:
        return engine
    from src.tools.rag_engine import RAGEngine

    start = time.perf_counter()
    engine = RAGEngine()
    _log_perf("init RAGEngine", start)
    _BACKEND_CACHE["rag"] = engine
    return engine


def _get_chat_agent():
    agent = _BACKEND_CACHE.get("chat_agent")
    if agent is not None:
        return agent
    from src.agents.chat_agent import ChatAgent

    start = time.perf_counter()
    agent = ChatAgent(_get_medgemma_client(), rag_engine=_get_rag_engine(), lang="en")
    _log_perf("init ChatAgent", start)
    _BACKEND_CACHE["chat_agent"] = agent
    return agent


def _get_asr_transcriber():
    if not _USE_BACKEND_MODEL:
        return None
    transcriber = _BACKEND_CACHE.get("asr")
    if transcriber is not None:
        return transcriber
    from src.agents.asr import MedASRTranscriber

    start = time.perf_counter()
    transcriber = MedASRTranscriber()
    _log_perf("init ASR Transcriber", start)
    _BACKEND_CACHE["asr"] = transcriber
    return transcriber


def _get_care_card_agent():
    agent = _BACKEND_CACHE.get("care_card_agent")
    if agent is not None:
        return agent
    from src.agents.care_card_agent import CareCardAgent

    start = time.perf_counter()
    agent = CareCardAgent(_get_medgemma_client(), rag_engine=_get_rag_engine())
    _log_perf("init CareCardAgent", start)
    _BACKEND_CACHE["care_card_agent"] = agent
    return agent


def warmup_models() -> None:
    if not _USE_BACKEND_MODEL or not _WARMUP_ON_START:
        return
    try:
        _get_medgemma_client()
    except Exception as exc:
        print(f"[Warmup] MedGemma load failed: {exc}")
    if _CHAT_RAG_ENABLED:
        try:
            _get_rag_engine()
        except Exception as exc:
            print(f"[Warmup] RAG load failed: {exc}")


def _generate_care_card_background(patient_id: str, answers: dict) -> None:
    try:
        store = get_store()
        from src.store.schemas import CareCard
        from src.utils.care_card_render import render_care_card

        cards = _build_patient_care_cards(patient_id, days=3)
        if not cards:
            return
        latest_version = store.get_latest_care_card_version(patient_id, "nursing")
        version = int(latest_version)
        for card_json in cards:
            version += 1
            title_override = str(card_json.get("title") or "Today's Care Card")
            text_md = render_care_card(card_json, lang="en", show_footer=True)
            card = CareCard(
                card_id=uuid.uuid4().hex,
                patient_id=patient_id,
                ward_id=None,
                created_at=datetime.utcnow().isoformat(),
                created_by_role="system",
                status="published",
                card_level="nursing",
                card_type="daily",
                language="en",
                title=title_override,
                one_liner=str(card_json.get("one_liner") or ""),
                bullets_json=json.dumps(card_json.get("bullets") or [], ensure_ascii=False),
                red_flags_json=json.dumps(card_json.get("red_flags") or [], ensure_ascii=False),
                followup_json=json.dumps(card_json.get("follow_up") or [], ensure_ascii=False),
                text_md=text_md,
                audio_path=None,
                source_assessment_id=None,
                version=version,
            )
            store.add_care_card(card)
        _CARE_CARD_CACHE.pop(patient_id, None)
    except Exception:
        try:
            _create_care_card_from_answers(patient_id, answers or {})
        except Exception:
            pass


def _build_timeline_for_patient(patient_id: str, store) -> dict:
    timeline: dict = {}
    try:
        patient = store.get_patient(patient_id)
        if patient:
            timeline["patient_profile"] = patient.to_dict()
    except Exception:
        pass
    try:
        latest_log = store.get_latest_daily_log(patient_id)
        if latest_log:
            timeline["latest_daily_log"] = latest_log.to_dict()
    except Exception:
        pass
    try:
        latest_admin = store.get_latest_nurse_admin(patient_id)
        if latest_admin:
            timeline["latest_nurse_admin"] = latest_admin.to_dict()
    except Exception:
        pass
    try:
        latest_assessment = store.get_latest_assessment(patient_id)
        if latest_assessment:
            diag = _safe_json(latest_assessment.diagnosis_json, {})
            timeline["latest_assessment_summary"] = {
                "primary_diagnosis": diag.get("primary_diagnosis"),
                "risk_level": diag.get("risk_level"),
                "primary_basis": latest_assessment.primary_basis,
            }
    except Exception:
        pass
    return timeline


def _build_assessment_struct(assessment) -> dict:
    if not assessment:
        return {}
    return {
        "assessment_id": assessment.assessment_id,
        "route_tag": assessment.route_tag,
        "primary_basis": assessment.primary_basis,
        "diagnosis": _safe_json(assessment.diagnosis_json, {}),
        "audit": _safe_json(assessment.audit_json, {}),
        "reverse": _safe_json(assessment.reverse_json, {}),
        "rag_evidence": _safe_json(assessment.rag_evidence_json, []),
        "tool_trace": _safe_json(assessment.tool_trace_json, []),
        "gaps": _safe_json(assessment.gaps_json, []),
    }


def _policy_filter_answer(role: str, answer: dict) -> dict:
    text = str(answer.get("answer") or "")
    flags = set(answer.get("safety_flags") or [])

    def _contains_dose(text_in: str) -> bool:
        # Filter only medication/prescription dosing guidance; allow generic lifestyle advice.
        med_terms = r"\b(medication|medicine|drug|antibiotic|steroid|inhaler|prescription|pill|tablet|capsule|dose|dosage)\b"
        if not re.search(med_terms, text_in, re.I):
            return False

        has_dose_amount = re.search(
            r"\b\d+(\.\d+)?\s?(mg|mcg|ug|g|ml|mL|units|iu|IU|tablets?|capsules?|puffs?|drops?)\b",
            text_in,
            re.I,
        ) is not None
        has_dose_frequency = re.search(
            r"\b\d+(\.\d+)?\s?(times|x)\s?/?\s?(day|daily)\b",
            text_in,
            re.I,
        ) is not None
        has_rx_change = re.search(
            r"\b(start|stop|increase|decrease|adjust|change)\b.{0,40}\b(dose|dosage|medication|medicine|drug|antibiotic|steroid|inhaler|prescription)\b",
            text_in,
            re.I,
        ) is not None
        return has_dose_amount or has_dose_frequency or has_rx_change

    if role == "patient":
        if _contains_dose(text):
            answer["answer"] = (
                "I can't provide specific medication doses. Please follow your clinician's instructions."
            )
            flags.add("policy_filtered")
    if role == "nurse":
        if _contains_dose(text):
            answer["answer"] = "This concerns prescription changes. Please confirm with a doctor."
            flags.add("policy_filtered")
    answer["safety_flags"] = list(flags)
    return answer


def _get_patient_sidebar_data(state: dict) -> dict:
    patient_id = state.get("patient_id") or _get_any_patient_id()
    prefs = _get_prefs(patient_id)
    display_name = prefs.get("display_name") or patient_id
    role = "Patient"
    avatar = prefs.get("avatar_data") or _avatar_data_uri(display_name)
    return {
        "patient_id": patient_id,
        "display_name": display_name,
        "role": role,
        "avatar": avatar,
    }


def _get_patient_data(state: dict) -> dict:
    patient_id = state.get("patient_id") or _get_any_patient_id()
    cache_key = f"{patient_id}"
    cached = _PATIENT_DATA_CACHE.get(cache_key)
    if cached and (datetime.utcnow().timestamp() - cached["ts"] < 4.0):
        return cached["data"]
    start = time.perf_counter()
    prefs = _get_prefs(patient_id)
    display_name = prefs.get("display_name") or patient_id
    role = "Patient"
    avatar = prefs.get("avatar_data") or _avatar_data_uri(display_name)
    today = date.today().isoformat()
    completed = False
    bullets = []
    today_card_count = 0
    unread_card_count = 0
    unread_msg_count = 0
    latest_msg_preview = ""
    try:
        store = get_store()
        latest_log = store.get_latest_daily_log(patient_id)
        if latest_log and getattr(latest_log, "date", None) == today:
            completed = True
        cards = _load_care_cards(patient_id)[:20]
        for c in cards:
            if str(c.get("created_at", "")).startswith(today):
                today_card_count += 1
            if not bool(c.get("understood")):
                unread_card_count += 1
        latest = cards[0] if cards else None
        if latest:
            bullets = latest.get("bullets") or []
        summaries = store.list_chat_summaries(patient_id, limit=1)
        if summaries:
            latest_msg_preview = summaries[0].summary_text[:60]
            unread_msg_count = 1
    except Exception:
        pass
    data = {
        "patient_id": patient_id,
        "display_name": display_name,
        "role": role,
        "avatar": avatar,
        "completed": completed,
        "today_card_count": today_card_count,
        "unread_card_count": unread_card_count,
        "unread_msg_count": unread_msg_count,
        "latest_msg_preview": latest_msg_preview,
        "bullets": bullets,
    }
    _PATIENT_DATA_CACHE[cache_key] = {"ts": datetime.utcnow().timestamp(), "data": data}
    _log_perf("load patient dashboard data", start, f"patient={patient_id}")
    return data


def _default_daily_answers() -> dict:
    return {
        "diet_status": "",
        "diet_triggers": [],
        "sleep_quality": "",
        "sleep_hours": "",
        "med_adherence": "",
        "symptoms": {"cough": "", "sob": "", "chest_pain": ""},
        "notes_text": "",
    }


def _ensure_daily_draft_table() -> None:
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_check_drafts (
                    patient_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    answers_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (patient_id, date)
                )
                """
            )
            conn.commit()
    except Exception:
        pass


def _load_daily_draft(patient_id: str) -> dict | None:
    _ensure_daily_draft_table()
    today = date.today().isoformat()
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            row = conn.execute(
                "SELECT answers_json FROM daily_check_drafts WHERE patient_id = ? AND date = ?",
                (patient_id, today),
            ).fetchone()
        if row and row[0]:
            return json.loads(row[0])
    except Exception:
        pass
    return None


def _save_daily_draft(patient_id: str, answers: dict) -> None:
    _ensure_daily_draft_table()
    today = date.today().isoformat()
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_check_drafts (patient_id, date, answers_json, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (patient_id, today, json.dumps(answers, ensure_ascii=False), datetime.utcnow().isoformat()),
            )
            conn.commit()
    except Exception:
        pass


def _delete_daily_draft(patient_id: str) -> None:
    _ensure_daily_draft_table()
    today = date.today().isoformat()
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                "DELETE FROM daily_check_drafts WHERE patient_id = ? AND date = ?",
                (patient_id, today),
            )
            conn.commit()
    except Exception:
        pass


def _answers_from_payload(payload: str, fallback: dict) -> dict:
    if not payload:
        return fallback
    try:
        data = json.loads(payload)
        if isinstance(data, dict):
            return data
    except Exception:
        return fallback
    return fallback


def _build_daily_log_from_answers(patient_id: str, answers: dict):
    try:
        from src.store.schemas import DailyLog
    except Exception:
        return None
    sleep_hours = answers.get("sleep_hours")
    try:
        sleep_hours = float(sleep_hours) if sleep_hours not in (None, "") else None
    except Exception:
        sleep_hours = None
    symptoms_payload = {
        "diet_triggers": answers.get("diet_triggers", []),
        "sleep_quality": answers.get("sleep_quality", ""),
        "symptoms": answers.get("symptoms", {}),
        "notes": answers.get("notes_text", ""),
    }
    meds_payload = {"med_adherence": answers.get("med_adherence", "")}
    return DailyLog(
        patient_id=patient_id,
        date=date.today().isoformat(),
        diet=answers.get("diet_status") or None,
        water_ml=None,
        sleep_hours=sleep_hours,
        symptoms_json=json.dumps(symptoms_payload, ensure_ascii=False),
        patient_reported_meds_json=json.dumps(meds_payload, ensure_ascii=False),
        created_at=datetime.utcnow().isoformat(),
    )


def _score_symptom(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    mapping = {"none": 0, "mild": 1, "moderate": 2, "severe": 3}
    return mapping.get(str(value).strip().lower())


def _score_diet(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    mapping = {
        "normal": 0,
        "reduced appetite": 1,
        "nausea": 2,
        "can't eat": 3,
        "cannot eat": 3,
    }
    return mapping.get(str(value).strip().lower())


def _score_sleep_quality(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    mapping = {"good": 0, "fair": 1, "poor": 2}
    return mapping.get(str(value).strip().lower())


def _score_med_adherence(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    mapping = {"took on time": 0, "not sure": 1, "missed": 2}
    return mapping.get(str(value).strip().lower())


def _trend_label(values: list[Optional[int]]) -> str:
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return "unknown"
    first = vals[0]
    last = vals[-1]
    if last > first:
        return "worsening"
    if last < first:
        return "improving"
    if max(vals) != min(vals):
        return "fluctuating"
    return "stable"


def _trim_list(items: list[str], min_len: int, max_len: int, filler: list[str]) -> list[str]:
    out = [i for i in items if i]
    if len(out) < min_len:
        for f in filler:
            if f not in out:
                out.append(f)
            if len(out) >= min_len:
                break
    return out[:max_len]


def _build_patient_care_cards(patient_id: str, days: int = 3) -> list[dict]:
    store = get_store()
    logs = store.list_daily_logs(patient_id, limit=max(1, int(days)))
    if not logs:
        return []
    logs = list(reversed(logs))  # oldest -> newest
    parsed_logs = []
    for log in logs:
        symptoms_payload = _safe_json(getattr(log, "symptoms_json", None), {})
        meds_payload = _safe_json(getattr(log, "patient_reported_meds_json", None), {})
        parsed_logs.append(
            {
                "date": getattr(log, "date", ""),
                "diet": getattr(log, "diet", "") or "",
                "sleep_hours": getattr(log, "sleep_hours", None),
                "sleep_quality": (symptoms_payload or {}).get("sleep_quality", ""),
                "symptoms": (symptoms_payload or {}).get("symptoms", {}) or {},
                "notes": (symptoms_payload or {}).get("notes", ""),
                "med_adherence": (meds_payload or {}).get("med_adherence", ""),
            }
        )
    today = parsed_logs[-1]
    series = {
        "cough": [_score_symptom(p["symptoms"].get("cough")) for p in parsed_logs],
        "sob": [_score_symptom(p["symptoms"].get("sob")) for p in parsed_logs],
        "chest": [_score_symptom(p["symptoms"].get("chest_pain")) for p in parsed_logs],
        "diet": [_score_diet(p.get("diet")) for p in parsed_logs],
        "sleep": [_score_sleep_quality(p.get("sleep_quality")) for p in parsed_logs],
        "med": [_score_med_adherence(p.get("med_adherence")) for p in parsed_logs],
    }
    trends = {k: _trend_label(v) for k, v in series.items()}
    today_scores = {
        "cough": series["cough"][-1],
        "sob": series["sob"][-1],
        "chest": series["chest"][-1],
        "diet": series["diet"][-1],
        "sleep": series["sleep"][-1],
        "med": series["med"][-1],
    }

    def _focus_from_trend(topic: str, trend: str) -> str:
        if trend == "worsening":
            return f"Focus today: {topic} has been getting worse—slow down and prioritize comfort."
        if trend == "improving":
            return f"Focus today: keep the steady progress with gentle, paced activity."
        if trend == "fluctuating":
            return f"Focus today: symptoms have been up and down—take it easy and listen to your body."
        return f"Focus today: protect your recovery and avoid overexertion."

    def _worst_trend(*vals: str) -> str:
        rank = {"worsening": 4, "fluctuating": 3, "stable": 2, "improving": 1, "unknown": 0}
        best = "unknown"
        best_score = -1
        for v in vals:
            score = rank.get(v, 0)
            if score > best_score:
                best_score = score
                best = v
        return best

    def _make_card(title: str, focus: str, do: list[str], dont: list[str], help_now: list[str]) -> dict:
        do_list = _trim_list(
            do,
            3,
            6,
            [
                "Follow your care team's guidance.",
                "Rest between activities and pace yourself.",
                "Keep your call bell within reach.",
            ],
        )
        dont_list = _trim_list(
            dont,
            2,
            4,
            [
                "Don't push through worsening symptoms.",
                "Don't stop prescribed medicines on your own.",
            ],
        )
        help_list = _trim_list(
            help_now,
            3,
            5,
            [
                "Breathing suddenly gets worse.",
                "Chest pain is new or getting worse.",
                "You cannot speak a full sentence.",
                "You feel confused, very drowsy, or faint.",
                "You cough up blood.",
            ],
        )
        return {
            "title": title,
            "one_liner": focus,
            "bullets": do_list,
            "follow_up": dont_list,
            "red_flags": help_list,
        }

    cards: list[dict] = []

    breathing_issue = (
        (today_scores["sob"] or 0) >= 2
        or (today_scores["chest"] or 0) >= 1
        or trends["sob"] == "worsening"
        or trends["chest"] == "worsening"
    )
    cough_issue = (today_scores["cough"] or 0) >= 2 or trends["cough"] == "worsening"
    nutrition_issue = (today_scores["diet"] or 0) >= 1 or trends["diet"] == "worsening"
    sleep_issue = (today_scores["sleep"] or 0) >= 2 or (
        isinstance(today.get("sleep_hours"), (int, float)) and today["sleep_hours"] <= 5
    )
    med_issue = (today_scores["med"] or 0) >= 1

    if breathing_issue:
        focus = _focus_from_trend("breathing", _worst_trend(trends["sob"], trends["chest"]))
        cards.append(
            _make_card(
                "Breathing & Chest Comfort",
                focus,
                [
                    "Sit upright or prop yourself with pillows.",
                    "Pace activities and rest between tasks.",
                    "Use prescribed oxygen or inhalers as directed.",
                    "Practice slow, gentle breathing to reduce anxiety.",
                ],
                [
                    "Don't lie flat if it makes breathing harder.",
                    "Don't push through breathlessness.",
                    "Don't delay asking for help if symptoms worsen.",
                ],
                [
                    "Breathing is much worse than usual.",
                    "Chest pain is new or getting worse.",
                    "You cannot speak a full sentence.",
                    "You feel faint or very drowsy.",
                    "You cough up blood.",
                ],
            )
        )
    if not breathing_issue and cough_issue:
        focus = _focus_from_trend("cough", trends["cough"])
        cards.append(
            _make_card(
                "Cough Relief & Airway Care",
                focus,
                [
                    "Sip warm fluids if allowed.",
                    "Cough gently to clear mucus.",
                    "Use prescribed medicines as directed.",
                    "Rest your voice and avoid long talking.",
                ],
                [
                    "Don't smoke or stay around smoke.",
                    "Don't suppress a cough that brings up mucus.",
                ],
                [
                    "Coughing up blood.",
                    "Cough with severe chest pain.",
                    "Breathing becomes much worse.",
                ],
            )
        )
    if nutrition_issue:
        focus = _focus_from_trend("appetite", trends["diet"])
        cards.append(
            _make_card(
                "Nutrition & Hydration",
                focus,
                [
                    "Have small, frequent meals if tolerated.",
                    "Choose easy-to-digest foods.",
                    "Sip fluids regularly if allowed.",
                    "Ask the nurse if nausea limits eating.",
                ],
                [
                    "Don't force large meals.",
                    "Don't skip fluids if you can tolerate small sips.",
                ],
                [
                    "You cannot keep fluids down.",
                    "Repeated vomiting or severe nausea.",
                    "Severe abdominal pain or weakness.",
                ],
            )
        )
    if sleep_issue and len(cards) < 3:
        focus = _focus_from_trend("rest", trends["sleep"])
        cards.append(
            _make_card(
                "Rest & Recovery",
                focus,
                [
                    "Plan short rest periods through the day.",
                    "Keep the room quiet and lights dim for sleep.",
                    "Ask for help with repositioning if needed.",
                    "Use relaxation breathing before sleep.",
                ],
                [
                    "Don't overexert yourself when tired.",
                    "Don't stay in one position too long.",
                ],
                [
                    "Severe dizziness or fainting.",
                    "Breathing worsens at rest.",
                    "New confusion or extreme sleepiness.",
                ],
            )
        )
    if med_issue and len(cards) < 3:
        cards.append(
            _make_card(
                "Medication & Routine",
                "Focus today: take medicines safely and on schedule.",
                [
                    "Take prescribed medicines as directed.",
                    "Ask the nurse if you missed a dose.",
                    "Report side effects promptly.",
                ],
                [
                    "Don't double up doses to catch up.",
                    "Don't stop medicines on your own.",
                ],
                [
                    "Severe dizziness or fainting after medicines.",
                    "Rash, swelling, or trouble breathing.",
                    "New severe nausea or vomiting.",
                ],
            )
        )
    if not cards:
        cards.append(
            _make_card(
                "Recovery Basics",
                "Focus today: rest, hydrate, and listen to your body.",
                [
                    "Rest between activities and pace yourself.",
                    "Stay hydrated if allowed.",
                    "Use prescribed medicines as directed.",
                ],
                [
                    "Don't overexert yourself.",
                    "Don't ignore worsening symptoms.",
                ],
                [
                    "Breathing suddenly gets worse.",
                    "Chest pain is new or getting worse.",
                    "You feel faint, confused, or very drowsy.",
                ],
            )
        )
    return cards[:3]


def _create_care_card_from_answers(patient_id: str, answers: dict) -> None:
    try:
        from src.store.schemas import CareCard
        from src.utils.care_card_render import render_care_card
    except Exception:
        return
    try:
        store = get_store()
        patient = store.get_patient(patient_id)
        ward_id = getattr(patient, "ward_id", None) if patient else None
        cards = _build_patient_care_cards(patient_id, days=3)
        if not cards:
            return
        latest_version = store.get_latest_care_card_version(patient_id, "nursing")
        version = int(latest_version)
        for card_json in cards:
            version += 1
            text_md = render_care_card(card_json, lang="en", show_footer=True)
            card = CareCard(
                card_id=uuid.uuid4().hex,
                patient_id=patient_id,
                ward_id=ward_id,
                created_at=datetime.utcnow().isoformat(),
                created_by_role="system",
                status="published",
                card_level="nursing",
                card_type="daily",
                language="en",
                title=str(card_json.get("title") or "Today's Care Card"),
                one_liner=str(card_json.get("one_liner") or ""),
                bullets_json=json.dumps(card_json.get("bullets") or [], ensure_ascii=False),
                red_flags_json=json.dumps(card_json.get("red_flags") or [], ensure_ascii=False),
                followup_json=json.dumps(card_json.get("follow_up") or [], ensure_ascii=False),
                text_md=text_md,
                audio_path=None,
                source_assessment_id=None,
                version=version,
            )
            store.add_care_card(card)
        _CARE_CARD_CACHE.pop(patient_id, None)
    except Exception:
        return


def _init_daily_state(state: dict) -> dict:
    patient_id = state.get("patient_id") or _get_any_patient_id()
    if not state.get("daily_loaded"):
        draft = _load_daily_draft(patient_id)
        state["daily_answers"] = draft or _default_daily_answers()
        state["daily_step"] = 1
        state["daily_loaded"] = True
    return state


def _ensure_care_read_table() -> None:
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS care_card_reads (
                    patient_id TEXT NOT NULL,
                    card_id TEXT NOT NULL,
                    understood_at TEXT NOT NULL,
                    PRIMARY KEY (patient_id, card_id)
                )
                """
            )
            conn.commit()
    except Exception:
        pass


def _is_care_understood(patient_id: str, card_id: str) -> bool:
    _ensure_care_read_table()
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            row = conn.execute(
                "SELECT 1 FROM care_card_reads WHERE patient_id = ? AND card_id = ?",
                (patient_id, card_id),
            ).fetchone()
        return bool(row)
    except Exception:
        return False


def _get_understood_card_ids(patient_id: str) -> set[str]:
    _ensure_care_read_table()
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            rows = conn.execute(
                "SELECT card_id FROM care_card_reads WHERE patient_id = ?",
                (patient_id,),
            ).fetchall()
        return {str(r[0]) for r in rows if r and r[0]}
    except Exception:
        return set()


def _mark_care_understood(patient_id: str, card_id: str) -> None:
    _ensure_care_read_table()
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO care_card_reads (patient_id, card_id, understood_at) VALUES (?, ?, ?)",
                (patient_id, card_id, datetime.utcnow().isoformat()),
            )
            conn.commit()
        _CARE_CARD_CACHE.pop(patient_id, None)
    except Exception:
        pass


def _delete_care_card(patient_id: str, card_id: str) -> bool:
    pid = str(patient_id or "").strip()
    cid = str(card_id or "").strip()
    if not pid or not cid:
        return False
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            row = conn.execute(
                "SELECT patient_id, audio_path FROM care_cards WHERE card_id = ? LIMIT 1",
                (cid,),
            ).fetchone()
            if not row or str(row[0] or "").strip() != pid:
                return False
            conn.execute("DELETE FROM care_cards WHERE card_id = ? AND patient_id = ?", (cid, pid))
            conn.execute("DELETE FROM care_card_reads WHERE patient_id = ? AND card_id = ?", (pid, cid))
            conn.commit()
            audio_path = str(row[1] or "").strip()
        if audio_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except Exception:
                pass
        _CARE_CARD_CACHE.pop(pid, None)
        return True
    except Exception:
        return False


def _format_short_date(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%b %d")
    except Exception:
        return ts[:10] if ts else ""


def _load_care_cards(patient_id: str, search: str = "") -> list[dict]:
    cache_key = patient_id
    now_ts = datetime.utcnow().timestamp()
    cached = _CARE_CARD_CACHE.get(cache_key)
    if cached and (now_ts - cached["ts"] < 15.0):
        cards = cached["data"]
    else:
        start = time.perf_counter()
        cards: list[dict] = []
        try:
            store = get_store()
            items = store.list_care_cards(patient_id, limit=50, card_type="daily")
            if not items:
                items = store.list_care_cards(patient_id, limit=50)
            understood_ids = _get_understood_card_ids(patient_id)
            for c in items:
                title = getattr(c, "title", "") or "Care Card"
                if title.lower().startswith("nursing care card"):
                    title = "Today's Care Card"
                bullets = []
                try:
                    bullets = json.loads(getattr(c, "bullets_json", "[]") or "[]")
                except Exception:
                    bullets = []
                red_flags = []
                try:
                    red_flags = json.loads(getattr(c, "red_flags_json", "[]") or "[]")
                except Exception:
                    red_flags = []
                follow_up = []
                try:
                    follow_up = json.loads(getattr(c, "followup_json", "[]") or "[]")
                except Exception:
                    follow_up = []
                created_at = getattr(c, "created_at", "")
                understood = c.card_id in understood_ids
                cards.append(
                    {
                        "card_id": c.card_id,
                        "title": title,
                        "one_liner": getattr(c, "one_liner", "") or "",
                        "bullets": bullets,
                        "red_flags": red_flags,
                        "follow_up": follow_up,
                        "date": _format_short_date(created_at),
                        "created_at": created_at,
                        "audio_path": getattr(c, "audio_path", None),
                        "understood": understood,
                    }
                )
        except Exception:
            cards = []
        _CARE_CARD_CACHE[cache_key] = {"ts": now_ts, "data": cards}
        _log_perf("load care cards", start, f"patient={patient_id} count={len(cards)}")
    if search:
        s = search.lower().strip()
        return [
            c
            for c in cards
            if s in (c["title"] or "").lower() or any(s in (b or "").lower() for b in c["bullets"])
        ]
    cards.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return list(cards)


def _ensure_inbox_table() -> None:
    try:
        with sqlite3.connect(_DB_PATH) as conn:
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
            conn.commit()
    except Exception:
        pass


def _seed_inbox_if_empty(patient_id: str) -> None:
    if patient_id != "demo_patient_001":
        return
    _ensure_inbox_table()
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            row = conn.execute(
                "SELECT COUNT(1) FROM inbox_messages WHERE patient_id = ?",
                (patient_id,),
            ).fetchone()
            if row and row[0] > 0:
                sample = conn.execute(
                    "SELECT subject, body FROM inbox_messages WHERE patient_id = ? LIMIT 1",
                    (patient_id,),
                ).fetchone()
                if sample and any(ord(ch) > 127 for ch in (sample[0] or "") + (sample[1] or "")):
                    conn.execute("DELETE FROM inbox_messages WHERE patient_id = ?", (patient_id,))
                else:
                    return
            samples = [
                (
                    uuid.uuid4().hex,
                    patient_id,
                    "Nurse",
                    "Nurse station",
                    "Post-discharge care steps",
                    """Hello,
Please follow your daily care card instructions.
Next steps:
- Complete daily check
- Hydration reminders
""",
                    datetime.utcnow().isoformat(),
                    1,
                ),
                (
                    uuid.uuid4().hex,
                    patient_id,
                    "Doctor",
                    "Dr. Chen",
                    "Reviewing your daily check",
                    "We reviewed your daily check. Please continue resting and monitor symptoms.",
                    datetime.utcnow().isoformat(),
                    1,
                ),
                (
                    uuid.uuid4().hex,
                    patient_id,
                    "System",
                    "System",
                    "Weekly summary available",
                    "Your weekly summary is now available in Care Cards.",
                    datetime.utcnow().isoformat(),
                    0,
                ),
            ]
            conn.executemany(
                """
                INSERT INTO inbox_messages (message_id, patient_id, sender_type, sender_name, subject, body, created_at, unread)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                samples,
            )
            conn.commit()
    except Exception:
        pass


def _load_inbox_messages(patient_id: str, category: str = "All", search: str = "") -> list[dict]:
    cache_key = patient_id
    now_ts = datetime.utcnow().timestamp()
    cached = _INBOX_CACHE.get(cache_key)
    if cached and (now_ts - cached["ts"] < 12.0):
        msgs = cached["data"]
    else:
        start = time.perf_counter()
        _seed_inbox_if_empty(patient_id)
        msgs: list[dict] = []
        try:
            with sqlite3.connect(_DB_PATH) as conn:
                rows = conn.execute(
                    "SELECT * FROM inbox_messages WHERE patient_id = ? ORDER BY created_at DESC",
                    (patient_id,),
                ).fetchall()
            for r in rows:
                msg = {
                    "message_id": r[0],
                    "patient_id": r[1],
                    "sender_type": r[2],
                    "sender_name": r[3],
                    "subject": r[4],
                    "body": r[5],
                    "created_at": r[6],
                    "unread": bool(r[7]),
                }
                msgs.append(msg)
        except Exception:
            msgs = []
        _INBOX_CACHE[cache_key] = {"ts": now_ts, "data": msgs}
        _log_perf("load inbox messages", start, f"patient={patient_id} count={len(msgs)}")
    if category and category != "All":
        msgs = [m for m in msgs if m["sender_type"].lower() == category.lower()]
    if search:
        s = search.lower().strip()
        msgs = [m for m in msgs if s in m["subject"].lower() or s in m["body"].lower()]
    return list(msgs)


def _mark_message_read(message_id: str) -> None:
    _ensure_inbox_table()
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute("UPDATE inbox_messages SET unread = 0 WHERE message_id = ?", (message_id,))
            conn.commit()
        _INBOX_CACHE.clear()
    except Exception:
        pass


def _delete_inbox_message(message_id: str, patient_id: str) -> bool:
    _ensure_inbox_table()
    mid = str(message_id or "").strip()
    pid = str(patient_id or "").strip()
    if not mid or not pid:
        return False
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            cur = conn.execute(
                "DELETE FROM inbox_messages WHERE message_id = ? AND patient_id = ?",
                (mid, pid),
            )
            conn.commit()
            deleted = bool(getattr(cur, "rowcount", 0) or 0)
        if deleted:
            _INBOX_CACHE.clear()
        return deleted
    except Exception:
        return False


def _ensure_prefs_table() -> None:
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS patient_prefs (
                    patient_id TEXT PRIMARY KEY,
                    language TEXT,
                    font_size TEXT,
                    display_name TEXT,
                    avatar_data TEXT
                )
                """
            )
            cols = [r[1] for r in conn.execute("PRAGMA table_info(patient_prefs)").fetchall()]
            if "display_name" not in cols:
                conn.execute("ALTER TABLE patient_prefs ADD COLUMN display_name TEXT")
            if "avatar_data" not in cols:
                conn.execute("ALTER TABLE patient_prefs ADD COLUMN avatar_data TEXT")
            conn.commit()
    except Exception:
        pass


def _get_prefs(patient_id: str) -> dict:
    _ensure_prefs_table()
    prefs = {"language": "English", "font_size": "Normal", "display_name": "", "avatar_data": ""}
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            row = conn.execute(
                "SELECT language, font_size, display_name, avatar_data FROM patient_prefs WHERE patient_id = ?",
                (patient_id,),
            ).fetchone()
        if row:
            prefs["language"] = row[0] or prefs["language"]
            prefs["font_size"] = row[1] or prefs["font_size"]
            prefs["display_name"] = row[2] or prefs["display_name"]
            prefs["avatar_data"] = row[3] or prefs["avatar_data"]
    except Exception:
        pass
    return prefs


def _save_prefs(patient_id: str, font_size: str, display_name: str, avatar_data: str) -> None:
    _ensure_prefs_table()
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO patient_prefs
                (patient_id, language, font_size, display_name, avatar_data)
                VALUES (?, ?, ?, ?, ?)
                """,
                (patient_id, "English", font_size, display_name, avatar_data),
            )
            conn.commit()
    except Exception:
        pass


def dc_onclick(action_id: str) -> str:
    js = f"""(function(){{
  var root=document.querySelector('gradio-app');
  var dom=root&&root.shadowRoot?root.shadowRoot:document;
  var scope=(dom.querySelector?dom.querySelector('.daily-check-card'):null) || document.querySelector('.daily-check-card');
  if(!scope) return false;
  var base={{}};
  try{{ base=JSON.parse(scope.getAttribute('data-answers')||'{{}}'); }}catch(e){{}}
  var diet=scope.querySelector('input[name="diet_status"]:checked');
  if(diet) base.diet_status = diet.value;
  var triggers=scope.querySelectorAll('input[name="diet_triggers"]');
  if(triggers.length) base.diet_triggers = Array.from(triggers).filter(x=>x.checked).map(x=>x.value);
  var sleep=scope.querySelector('input[name="sleep_quality"]:checked');
  if(sleep) base.sleep_quality = sleep.value;
  var hours=scope.querySelector('#sleep_hours');
  if(hours) base.sleep_hours = hours.value;
  var med=scope.querySelector('input[name="med_adherence"]:checked');
  if(med) base.med_adherence = med.value;
  var cough=scope.querySelector('input[name="symptom_cough"]:checked');
  if(cough) {{ base.symptoms = base.symptoms || {{}}; base.symptoms.cough = cough.value; }}
  var sob=scope.querySelector('input[name="symptom_sob"]:checked');
  if(sob) {{ base.symptoms = base.symptoms || {{}}; base.symptoms.sob = sob.value; }}
  var chest=scope.querySelector('input[name="symptom_chest_pain"]:checked');
  if(chest) {{ base.symptoms = base.symptoms || {{}}; base.symptoms.chest_pain = chest.value; }}
  var notes=scope.querySelector('#dc_notes');
  if(notes) base.notes_text = notes.value;
  var page=(window._wl_page||(function(){{try{{return localStorage.getItem('wl_page')||'';}}catch(e){{return '';}}}})());
  if(page) base.current_page = page;
  var payload=JSON.stringify(base);
  var input=dom.querySelector('#dc_payload textarea, #dc_payload input');
  if(input) {{ input.value = payload; input.dispatchEvent(new Event('input',{{bubbles:true}})); }}
  var btn=dom.querySelector('#{action_id}');
  if(btn) btn.click();
}})(); return false;"""
    return html.escape(js, quote=True)


def ui_onclick(action_id: str, payload: dict | None = None) -> str:
    payload = payload or {}
    payload_str = json.dumps(payload, ensure_ascii=False)
    payload_str = payload_str.replace("\\", "\\\\").replace("'", "\\'")
    js = f"""(function(){{
  var root=document.querySelector('gradio-app');
  var dom=root&&root.shadowRoot?root.shadowRoot:document;
  var payload={payload_str};
  var page=(window._wl_page||(function(){{try{{return localStorage.getItem('wl_page')||'';}}catch(e){{return '';}}}})());
  if(page) payload.current_page = page;
  var input=dom.querySelector('#ui_payload textarea, #ui_payload input');
  if(input) {{ input.value = JSON.stringify(payload); input.dispatchEvent(new Event('input',{{bubbles:true}})); }}
  var btn=dom.querySelector('#{action_id}');
  if(btn) btn.click();
}})(); return false;"""
    return html.escape(js, quote=True)


def _build_patient_ctx() -> dict:
    return {
        "icons": _ICONS,
        "logo_data": _LOGO_DATA,
        "onclick": onclick,
        "ui_onclick": ui_onclick,
        "dc_onclick": dc_onclick,
        "get_patient_data": _get_patient_data,
        "get_patient_sidebar_data": _get_patient_sidebar_data,
        "get_prefs": _get_prefs,
        "avatar_data_uri": _avatar_data_uri,
        "load_care_cards": _load_care_cards,
        "load_inbox_messages": _load_inbox_messages,
        "init_daily_state": _init_daily_state,
        "default_daily_answers": _default_daily_answers,
        "get_any_patient_id": _get_any_patient_id,
    }


def get_patient_ctx() -> dict:
    global _PATIENT_CTX
    if _PATIENT_CTX is None:
        _PATIENT_CTX = _build_patient_ctx()
    return _PATIENT_CTX


def render_patient_view(state: dict) -> str:
    start = time.perf_counter()
    html_out = render_patient_page(state, get_patient_ctx())
    _log_perf("render patient view", start, f"page={state.get('current_page')}")
    return html_out


def default_state() -> dict:
    return {
        "authed": False,
        "role": None,
        "patient_id": None,
        "staff_id": None,
        "ward_id": None,
        "current_page": "dashboard",
        "toast": "",
        "daily_loaded": False,
        "daily_step": 1,
        "daily_answers": _default_daily_answers(),
        "care_search": "",
        "care_modal_id": None,
        "highlight_card_id": None,
        "care_audio_path": None,
        "nurse_request_detail": "",
        "nurse_request_image_path": None,
        "nurse_request_audio_path": None,
        "nurse_request_image_name": "",
        "nurse_request_audio_name": "",
        "chat_history": [],
        "chat_pending": False,
        "inbox_filter": "All",
        "inbox_search": "",
        "inbox_selected_id": None,
        "settings_lang": None,
        "settings_font": None,
    }


def init_daily_state(state: dict) -> dict:
    return _init_daily_state(state)


def default_daily_answers() -> dict:
    return _default_daily_answers()


def get_any_patient_id() -> str:
    return _get_any_patient_id()


def get_prefs(patient_id: str) -> dict:
    return _get_prefs(patient_id)


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
    state = state or {}
    page = (data or {}).get("current_page") or (data or {}).get("page") or ""
    page = str(page).strip()
    if page:
        state["current_page"] = page
    return state


def _append_chat_result(patient_id: str, result: dict) -> None:
    with _CHAT_LOCK:
        _CHAT_RESULTS.setdefault(patient_id, []).append(result)


def _pop_chat_result(patient_id: str) -> Optional[dict]:
    with _CHAT_LOCK:
        items = _CHAT_RESULTS.get(patient_id)
        if not items:
            return None
        result = items.pop(0)
        if not items:
            _CHAT_RESULTS.pop(patient_id, None)
        return result


def _has_chat_result(patient_id: str) -> bool:
    with _CHAT_LOCK:
        return bool(_CHAT_RESULTS.get(patient_id))


def nav_to(state: dict, page: str):
    state = state or {}
    state["current_page"] = page
    if page == "daily":
        state = _init_daily_state(state)
    return state, render_patient_view(state)


def do_tts(state: dict):
    state = state or {}
    state["toast"] = "Audio is being prepared..."
    return state, render_patient_view(state)


def _update_daily_state_from_payload(payload: str, state: dict) -> dict:
    state = state or {}
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state)
    state = _init_daily_state(state)
    answers = _answers_from_payload(payload, state.get("daily_answers") or _default_daily_answers())
    state["daily_answers"] = answers
    return state


def dc_step_prev(payload: str, state: dict):
    state = _update_daily_state_from_payload(payload, state)
    state["daily_step"] = max(1, int(state.get("daily_step", 1)) - 1)
    state["toast"] = ""
    return state, render_patient_view(state)


def dc_step_next(payload: str, state: dict):
    state = _update_daily_state_from_payload(payload, state)
    state["daily_step"] = min(5, int(state.get("daily_step", 1)) + 1)
    state["toast"] = ""
    return state, render_patient_view(state)


def dc_save_draft(payload: str, state: dict):
    state = _update_daily_state_from_payload(payload, state)
    patient_id = state.get("patient_id") or _get_any_patient_id()
    _save_daily_draft(patient_id, state.get("daily_answers") or {})
    state["toast"] = "Draft saved"
    return state, render_patient_view(state)


def dc_submit_daily(payload: str, state: dict):
    state = _update_daily_state_from_payload(payload, state)
    patient_id = state.get("patient_id") or _get_any_patient_id()
    try:
        store = get_store()
        log = _build_daily_log_from_answers(patient_id, state.get("daily_answers") or {})
        if log:
            store.add_daily_log(log)
        if _USE_BACKEND_MODEL:
            threading.Thread(
                target=_generate_care_card_background,
                args=(patient_id, state.get("daily_answers") or {}),
                daemon=True,
            ).start()
            state["toast"] = "Submitted. Generating care card in background..."
        else:
            _create_care_card_from_answers(patient_id, state.get("daily_answers") or {})
        _delete_daily_draft(patient_id)
        if not state.get("toast"):
            state["toast"] = "Daily check submitted"
        state["current_page"] = "dashboard"
    except Exception:
        state["toast"] = "Something went wrong, please try again."
    return state, render_patient_view(state)


def dc_voice_toast(state: dict):
    state = state or {}
    state["toast"] = "Voice input coming soon"
    return state, render_patient_view(state)


def care_open(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = state or {}
    state = _apply_payload_page(data, state)
    state["care_modal_id"] = data.get("card_id")
    state["toast"] = ""
    return state, render_patient_view(state)


def care_close(payload: str, state: dict):
    state = state or {}
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state)
    state["care_modal_id"] = None
    return state, render_patient_view(state)


def care_mark(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = state or {}
    state = _apply_payload_page(data, state)
    card_id = data.get("card_id")
    patient_id = state.get("patient_id") or _get_any_patient_id()
    if card_id:
        _mark_care_understood(patient_id, card_id)
        state["toast"] = "Marked as understood"
        state["care_modal_id"] = None
    return state, render_patient_view(state)


def care_delete(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = state or {}
    state = _apply_payload_page(data, state)
    patient_id = state.get("patient_id") or _get_any_patient_id()
    card_id = str(data.get("card_id") or state.get("care_modal_id") or "").strip()
    if not card_id:
        state["toast"] = "Select a care card first."
        return state, render_patient_view(state)
    ok = _delete_care_card(str(patient_id), card_id)
    if not ok:
        state["toast"] = "Delete failed."
        return state, render_patient_view(state)
    state["care_modal_id"] = None
    state["highlight_card_id"] = None
    state["toast"] = "Care card deleted."
    return state, render_patient_view(state)


def care_tts(payload: str, state: dict):
    state = state or {}
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state)
    state["toast"] = "Audio is being prepared..."
    return state, render_patient_view(state)


def care_search(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = state or {}
    state = _apply_payload_page(data, state)
    state["care_search"] = data.get("q", "")
    return state, render_patient_view(state)


def care_open_latest(payload: str, state: dict):
    state = state or {}
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state)
    patient_id = state.get("patient_id") or _get_any_patient_id()
    cards = _load_care_cards(patient_id)
    if cards:
        state["current_page"] = "cards"
        state["highlight_card_id"] = cards[0]["card_id"]
        state["care_modal_id"] = cards[0]["card_id"]
    return state, render_patient_view(state)


def request_nurse_now(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = state or {}
    state = _apply_payload_page(data, state)
    patient_id = state.get("patient_id") or _get_any_patient_id()
    reason = str(data.get("reason") or "").strip() or "Patient requested nurse assistance."
    detail = str(data.get("detail") or state.get("nurse_request_detail") or "").strip()
    if not detail:
        state["toast"] = "Please enter request details before sending."
        return state, render_patient_view(state)
    if len(reason) > 120:
        reason = reason[:117] + "..."
    image_path = str(state.get("nurse_request_image_path") or "").strip()
    audio_path = str(state.get("nurse_request_audio_path") or "").strip()
    if image_path and not os.path.exists(image_path):
        image_path = ""
    if audio_path and not os.path.exists(audio_path):
        audio_path = ""
    try:
        from src.ui import nurse_app

        store = get_store()
        patient = store.get_patient(patient_id)
        ward_id = getattr(patient, "ward_id", None) if patient else None
        bed_id = getattr(patient, "bed_id", None) if patient else None
        request_id = nurse_app.create_escalation_request(
            patient_id=str(patient_id),
            ward_id=ward_id,
            bed_id=bed_id,
            summary=reason,
            detail=detail,
            tags=["Manual request", "Patient initiated"],
            chat_summary=detail[:300],
            audio_src_path=audio_path,
            image_src_paths=[image_path] if image_path else [],
            status="pending",
        )
        if request_id:
            if image_path:
                try:
                    os.remove(image_path)
                except Exception:
                    pass
            if audio_path:
                try:
                    os.remove(audio_path)
                except Exception:
                    pass
            state["nurse_request_detail"] = ""
            state["nurse_request_image_path"] = None
            state["nurse_request_audio_path"] = None
            state["nurse_request_image_name"] = ""
            state["nurse_request_audio_name"] = ""
            state["toast"] = "Nurse has been notified."
        else:
            state["toast"] = "Unable to notify nurse. Please retry."
    except Exception:
        state["toast"] = "Unable to notify nurse. Please retry."
    return state, render_patient_view(state)


def request_nurse_attach_image(path: str, detail: str, page: str, state: dict):
    state = state or {}
    if page:
        state["current_page"] = page
    old_path = str(state.get("nurse_request_image_path") or "").strip()
    if old_path and old_path != str(path or "").strip():
        try:
            os.remove(old_path)
        except Exception:
            pass
    state["nurse_request_image_path"] = path
    state["nurse_request_image_name"] = os.path.basename(path or "")
    state["nurse_request_detail"] = str(detail or state.get("nurse_request_detail") or "").strip()
    state["toast"] = "Image attached."
    return state, render_patient_view(state)


def request_nurse_attach_audio(path: str, detail: str, page: str, state: dict):
    state = state or {}
    if page:
        state["current_page"] = page
    old_path = str(state.get("nurse_request_audio_path") or "").strip()
    if old_path and old_path != str(path or "").strip():
        try:
            os.remove(old_path)
        except Exception:
            pass
    state["nurse_request_audio_path"] = path
    state["nurse_request_audio_name"] = os.path.basename(path or "")
    state["nurse_request_detail"] = str(detail or state.get("nurse_request_detail") or "").strip()
    state["toast"] = "Audio attached."
    return state, render_patient_view(state)


def chat_send(payload: str, image_path, state: dict):
    data = parse_ui_payload(payload)
    msg = (data.get("message") or "").strip()
    state = state or {}
    state = _apply_payload_page(data, state)
    audio_path = None
    direct_audio_path = (data.get("audio_path") or "").strip()
    if direct_audio_path and os.path.exists(direct_audio_path):
        audio_path = direct_audio_path
    audio_b64 = (data.get("audio_b64") or "").strip()
    if audio_b64:
        try:
            header, b64data = audio_b64.split(",", 1)
            ext = "webm" if "webm" in header else "wav"
            tmp_dir = os.path.join(_BASE_DIR, "data", "tmp_audio")
            os.makedirs(tmp_dir, exist_ok=True)
            tmp_path = os.path.join(tmp_dir, f"chat_{uuid.uuid4().hex}.{ext}")
            with open(tmp_path, "wb") as f:
                f.write(base64.b64decode(b64data))
            audio_path = tmp_path
        except Exception:
            audio_path = None
    if not msg and not audio_path and not image_path:
        return state, render_patient_view(state)
    history = state.get("chat_history") or []
    display_msg = msg or ("Voice message" if audio_path else "")
    if image_path:
        display_msg = f"{display_msg} [Image]" if display_msg else "Image uploaded"
    history.append({"role": "user", "text": display_msg or "Message"})
    if _USE_BACKEND_MODEL:
        patient_id = state.get("patient_id") or _get_any_patient_id()

        def _worker(pid: str, message: str, audio_file: Optional[str], img_path: Optional[str]):
            assistant_text = "Please contact your nurse if symptoms worsen."
            summary_text = ""
            topic_tag = "other"
            key_flags_json = "[]"
            transcript = ""
            asr_quality = None
            chat_image_obj = None
            answer: dict[str, Any] = {}
            try:
                if audio_file:
                    transcriber = _get_asr_transcriber()
                    if transcriber is not None:
                        asr_out = transcriber.transcribe(audio_file)
                        if isinstance(asr_out, dict):
                            transcript = str(asr_out.get("transcript") or "").strip()
                            asr_quality = asr_out.get("asr_quality")
                        else:
                            transcript = str(asr_out or "").strip()
                            asr_quality = None
            except Exception:
                transcript = ""
                asr_quality = None

            if img_path:
                try:
                    from PIL import Image

                    with Image.open(img_path) as im:
                        chat_image_obj = im.convert("RGB").copy()
                except Exception:
                    chat_image_obj = None

            user_message = message or transcript or "Voice message"
            if img_path:
                user_message = (user_message + "\n\n[Image uploaded]") if user_message else "[Image uploaded]"
            try:
                from src.store.schemas import ChatSummary

                store = get_store()
                summaries = store.list_chat_summaries(pid, limit=5)
                memory = [s.summary_text for s in summaries if getattr(s, "summary_text", None)]
                timeline = _build_timeline_for_patient(pid, store)
                agent = _get_chat_agent()
                answer = agent.answer(
                    role="patient",
                    patient_id=pid,
                    user_message=user_message,
                    timeline=timeline,
                    memory_summaries=memory,
                    lang="en",
                    asr_quality=asr_quality,
                    image=chat_image_obj,
                )
                answer = _policy_filter_answer("patient", answer)
                assistant_text = (
                    str(answer.get("answer") or "").strip()
                    or "Please contact your nurse if symptoms worsen."
                )
                summary_text = str(answer.get("assistant_summary_for_memory") or "").strip()
                topic_tag = str(answer.get("topic_tag") or "other")
                key_flags_json = json.dumps(answer.get("safety_flags") or [], ensure_ascii=False)
                if summary_text:
                    chat_summary = ChatSummary(
                        patient_id=pid,
                        timestamp=datetime.utcnow().isoformat(),
                        role="patient",
                        summary_text=summary_text,
                        topic_tag=topic_tag,
                        key_flags_json=key_flags_json,
                    )
                    store.add_chat_summary(chat_summary)

                if bool(answer.get("need_escalation")):
                    from src.ui import nurse_app

                    patient = store.get_patient(pid)
                    ward_id = getattr(patient, "ward_id", None) if patient else None
                    bed_id = getattr(patient, "bed_id", None) if patient else None
                    escalation_reason = str(answer.get("escalation_reason") or "").strip()
                    if not escalation_reason:
                        escalation_reason = "Escalation needed based on patient chat."
                    if len(escalation_reason) > 120:
                        escalation_reason = escalation_reason[:117] + "..."

                    detail_lines = [f"Patient message: {user_message}"]
                    if summary_text:
                        detail_lines.append(f"AI summary: {summary_text}")
                    detail_text = "\n".join(detail_lines)

                    tags = ["Safety escalation"]
                    for flag in answer.get("safety_flags") or []:
                        text = str(flag or "").replace("_", " ").strip()
                        if text and text.lower() != "none":
                            tags.append(text.title())
                        if len(tags) >= 3:
                            break

                    nurse_app.create_escalation_request(
                        patient_id=pid,
                        ward_id=ward_id,
                        bed_id=bed_id,
                        summary=escalation_reason,
                        detail=detail_text,
                        tags=tags[:3],
                        chat_summary=summary_text or assistant_text,
                        audio_src_path=audio_file or "",
                        image_src_paths=[img_path] if img_path else [],
                        status="pending",
                    )
            except Exception:
                assistant_text = "Thanks for sharing. If symptoms worsen, please contact your nurse."
            finally:
                if chat_image_obj is not None:
                    try:
                        chat_image_obj.close()
                    except Exception:
                        pass
                if audio_file:
                    try:
                        os.remove(audio_file)
                    except Exception:
                        pass
                if img_path:
                    try:
                        os.remove(img_path)
                    except Exception:
                        pass
            _append_chat_result(
                pid,
                {
                    "assistant_text": assistant_text,
                    "summary_text": summary_text,
                    "topic_tag": topic_tag,
                    "key_flags_json": key_flags_json,
                },
            )

        threading.Thread(
            target=_worker,
            args=(patient_id, msg, audio_path, image_path),
            daemon=True,
        ).start()
        state["chat_pending"] = True
    else:
        reply = "Thanks for sharing. If symptoms worsen, please contact your nurse."
        history.append({"role": "assistant", "text": reply})
        state["chat_pending"] = False
    state["chat_history"] = history
    return state, render_patient_view(state)


def chat_voice(payload: str, state: dict):
    state = state or {}
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state)
    state["toast"] = "Voice input coming soon"
    return state, render_patient_view(state)


def chat_image(payload: str, state: dict):
    state = state or {}
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state)
    state["toast"] = "Image analysis coming soon"
    return state, render_patient_view(state)


def inbox_filter(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = state or {}
    state = _apply_payload_page(data, state)
    state["inbox_filter"] = data.get("category", "All")
    return state, render_patient_view(state)


def inbox_search(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = state or {}
    state = _apply_payload_page(data, state)
    state["inbox_search"] = data.get("q", "")
    return state, render_patient_view(state)


def inbox_select(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = state or {}
    state = _apply_payload_page(data, state)
    msg_id = data.get("message_id")
    if msg_id:
        state["inbox_selected_id"] = msg_id
        _mark_message_read(msg_id)
    return state, render_patient_view(state)


def inbox_ack(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = state or {}
    state = _apply_payload_page(data, state)
    msg_id = data.get("message_id")
    if msg_id:
        _mark_message_read(msg_id)
        state["toast"] = "Acknowledged"
    return state, render_patient_view(state)


def inbox_reply(payload: str, state: dict):
    state = state or {}
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state)
    state["toast"] = "Reply sent"
    return state, render_patient_view(state)


def inbox_delete(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = state or {}
    state = _apply_payload_page(data, state)
    patient_id = str(state.get("patient_id") or _get_any_patient_id() or "").strip()
    msg_id = str(data.get("message_id") or state.get("inbox_selected_id") or "").strip()
    if not msg_id:
        state["toast"] = "Select a message first."
        return state, render_patient_view(state)
    ok = _delete_inbox_message(msg_id, patient_id)
    if not ok:
        state["toast"] = "Delete failed."
        return state, render_patient_view(state)
    category = str(state.get("inbox_filter") or "All")
    search = str(state.get("inbox_search") or "")
    messages = _load_inbox_messages(patient_id, category=category, search=search)
    state["inbox_selected_id"] = messages[0]["message_id"] if messages else None
    state["toast"] = "Message deleted."
    return state, render_patient_view(state)


def settings_save(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = state or {}
    state = _apply_payload_page(data, state)
    patient_id = state.get("patient_id") or _get_any_patient_id()
    font_size = data.get("font_size", state.get("settings_font") or "Normal")
    display_name = data.get("display_name", "")
    avatar_data = data.get("avatar_data", "")
    _save_prefs(patient_id, font_size, display_name, avatar_data)
    state["settings_font"] = font_size
    state["toast"] = "Saved"
    return state, render_patient_view(state)


def settings_font(payload: str, state: dict):
    data = parse_ui_payload(payload)
    state = state or {}
    state = _apply_payload_page(data, state)
    state["settings_font"] = data.get("font_size", "Normal")
    return state, render_patient_view(state)


def settings_pass(payload: str, state: dict):
    state = state or {}
    data = parse_ui_payload(payload)
    state = _apply_payload_page(data, state)
    patient_id = state.get("patient_id") or _get_any_patient_id()
    ok, message = credentials.change_password(
        account_key=str(patient_id),
        role="patient",
        old_password=str(data.get("old") or ""),
        new_password=str(data.get("new") or ""),
        confirm_password=str(data.get("confirm") or ""),
    )
    state["toast"] = message if ok else message
    return state, render_patient_view(state)


def poll_chat_updates(state: dict):
    state = state or {}
    if not state.get("authed") or state.get("role") != "patient":
        return state, gr.update()
    if not state.get("chat_pending"):
        return state, gr.update()
    patient_id = state.get("patient_id") or _get_any_patient_id()
    result = _pop_chat_result(patient_id)
    if not result:
        return state, gr.update()
    history = state.get("chat_history") or []
    history.append({"role": "assistant", "text": result.get("assistant_text") or ""})
    state["chat_history"] = history
    state["chat_pending"] = _has_chat_result(patient_id)
    return state, render_patient_view(state)

