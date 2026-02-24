from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from src.agents.orchestrator import AnalysisOrchestrator
from src.agents.chat_agent import ChatAgent
from src.agents.care_card_agent import CareCardAgent
from src.agents.handover_agent import HandoverAgent
from src.store.schemas import (
    Assessment,
    CareCard,
    ChatSummary,
    DailyLog,
    HandoverRecord,
    NurseAdmin,
    Patient,
    PatientCard,
    RiskSnapshot,
)
from src.store.sqlite_store import SQLiteStore
from src.ui.i18n import t
from src.utils.care_card_render import render_care_card
from src.tools.risk_rules import compute_risk_snapshot


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _json_dumps(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _json_load(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str):
        lines = [line.strip(" -\t") for line in value.splitlines()]
        return [line for line in lines if line]
    return [str(value)]


def _build_patient_card_md(assessment: Assessment, log: Optional[DailyLog], lang: str) -> str:
    lang = (lang or "en").strip().lower()
    diag = _json_load(assessment.diagnosis_json, {})
    gaps = _json_load(assessment.gaps_json, [])
    primary = diag.get("primary_diagnosis", "Needs clinician review")
    risk = diag.get("risk_level", "N/A")

    lines = [f"### {t(lang, 'card_title_today_plan')}"]
    lines.append(f"- {t(lang, 'card_section_what_we_know')}: {primary} (risk: {risk}).")
    if log:
        lines.append(
            f"- Self report: diet={log.diet or 'N/A'}; water={log.water_ml or 'N/A'} ml; sleep={log.sleep_hours or 'N/A'} hrs."
        )

    lines.append(f"\n### {t(lang, 'card_section_what_to_do_today')}")
    items = [
        "Rest well and reduce exertion.",
        "Take medications as prescribed. Do not change doses by yourself.",
        "Hydrate appropriately if no restriction.",
        "Tell the nurse/doctor if symptoms worsen.",
    ]
    if gaps:
        for g in gaps[:2]:
            msg = g.get("message") if isinstance(g, dict) else str(g)
            if msg:
                items.append(msg)
    for item in items[:5]:
        lines.append(f"- {item}")

    lines.append(f"\n### {t(lang, 'card_section_red_flags')}")
    lines.append("- Severe shortness of breath / chest pain / confusion / persistent high fever")
    lines.append(f"\n{t(lang, 'card_footer_not_medical_advice')}")
    return "\n".join(lines)


class WardAgent:
    def __init__(
        self,
        store: Optional[SQLiteStore],
        orchestrator: AnalysisOrchestrator,
        medgemma_client=None,
        rag_engine=None,
        chat_agent: Optional[ChatAgent] = None,
        care_card_agent: Optional[CareCardAgent] = None,
        handover_agent: Optional[HandoverAgent] = None,
        asr_transcriber=None,
        lang: str = "en",
    ) -> None:
        self.store = store
        self.orchestrator = orchestrator
        self.medgemma_client = medgemma_client
        self.rag_engine = rag_engine
        self.chat_agent = chat_agent
        self.care_card_agent = care_card_agent
        self.handover_agent = handover_agent
        self.asr_transcriber = asr_transcriber
        self.lang = (lang or "en").strip().lower()

    @staticmethod
    def _clip_text(text: str, max_len: int = 180) -> str:
        raw = str(text or "").strip()
        if len(raw) <= max_len:
            return raw
        return raw[: max_len - 3].rstrip() + "..."

    def _summarize_recent_daily_logs(self, logs: list[DailyLog]) -> str:
        if not logs:
            return ""
        lines: list[str] = []
        for log in logs[:5]:
            symptoms_payload = _json_load(getattr(log, "symptoms_json", None), {})
            symptoms = symptoms_payload.get("symptoms") if isinstance(symptoms_payload, dict) else {}
            symptoms = symptoms if isinstance(symptoms, dict) else {}
            cough = str(symptoms.get("cough") or "NA")
            sob = str(symptoms.get("sob") or "NA")
            chest = str(symptoms.get("chest_pain") or "NA")
            sleep = str(log.sleep_hours) if getattr(log, "sleep_hours", None) not in (None, "") else "NA"
            diet = str(log.diet or "NA")
            notes = ""
            if isinstance(symptoms_payload, dict):
                notes = self._clip_text(str(symptoms_payload.get("notes") or ""), 70)
            row = (
                f"{str(log.date or '--')}: symptoms(cough={cough}, sob={sob}, chest_pain={chest}), "
                f"sleep={sleep}h, diet={diet}"
            )
            if notes:
                row += f", note={notes}"
            lines.append(row)
        return "Recent Daily Check records:\n- " + "\n- ".join(lines)

    def _summarize_recent_nurse_admin(self, records: list[NurseAdmin]) -> str:
        if not records:
            return ""
        lines: list[str] = []
        for rec in records[:5]:
            vitals = _json_load(getattr(rec, "vitals_json", None), {})
            meds = _json_load(getattr(rec, "administered_meds_json", None), [])
            vitals = vitals if isinstance(vitals, dict) else {}
            meds = meds if isinstance(meds, list) else []
            spo2 = vitals.get("spo2_pct") or vitals.get("spo2") or "NA"
            temp = vitals.get("temperature_c") or vitals.get("temp_c") or vitals.get("temperature") or "NA"
            rr = vitals.get("resp_rate") or vitals.get("rr") or "NA"
            hr = vitals.get("heart_rate") or vitals.get("hr") or "NA"
            med_states: dict[str, int] = {}
            for item in meds:
                if not isinstance(item, dict):
                    continue
                status = str(item.get("status") or "unknown").strip().lower()
                med_states[status] = med_states.get(status, 0) + 1
            med_state_text = ", ".join(f"{k}:{v}" for k, v in sorted(med_states.items())[:4]) if med_states else "none"
            note = self._clip_text(str(getattr(rec, "notes", "") or ""), 70)
            row = (
                f"{str(getattr(rec, 'timestamp', '') or '--')[:16]}: "
                f"vitals(SpO2={spo2}, Temp={temp}, RR={rr}, HR={hr}), MAR={med_state_text}"
            )
            if note:
                row += f", nurse_note={note}"
            lines.append(row)
        return "Recent nurse Vitals/MAR records:\n- " + "\n- ".join(lines)

    def _get_chat_agent(self) -> ChatAgent:
        if self.chat_agent is not None:
            return self.chat_agent
        if self.medgemma_client is None:
            raise RuntimeError("medgemma_client is required for chat")
        self.chat_agent = ChatAgent(self.medgemma_client, rag_engine=self.rag_engine, lang=self.lang)
        return self.chat_agent

    def _get_care_card_agent(self) -> CareCardAgent:
        if self.care_card_agent is not None:
            return self.care_card_agent
        self.care_card_agent = CareCardAgent(self.medgemma_client, rag_engine=self.rag_engine)
        return self.care_card_agent

    def _get_handover_agent(self) -> HandoverAgent:
        if self.handover_agent is not None:
            return self.handover_agent
        self.handover_agent = HandoverAgent(self.medgemma_client)
        return self.handover_agent

    def _assess_audio_quality(self, transcript: str) -> Dict[str, Any]:
        t = (transcript or "").strip()
        issues = []
        if not t or t == "[empty transcript]":
            return {"audio_quality_score": 0.0, "audio_issues": ["empty_transcript"]}

        eps_count = t.count("<epsilon>") + t.lower().count("epsilon")
        token_count = max(1, len(t.split()))
        eps_ratio = eps_count / float(token_count)
        if eps_ratio > 0.2:
            issues.append("epsilon_noise_high")

        if len(t.split()) < 3:
            issues.append("very_short_transcript")

        score = 1.0
        if "very_short_transcript" in issues:
            score -= 0.35
        if "epsilon_noise_high" in issues:
            score -= 0.45
        score = max(0.0, min(1.0, score))
        return {"audio_quality_score": round(score, 3), "audio_issues": issues}

    def _build_timeline(self, patient_id: str) -> Dict[str, Any]:
        if self.store is None:
            return {}
        patient = self.store.get_patient(patient_id)
        latest_log = self.store.get_latest_daily_log(patient_id)
        latest_admin = self.store.get_latest_nurse_admin(patient_id)
        latest_assessment = self.store.get_latest_assessment(patient_id)
        latest_card = self.store.get_latest_patient_card(patient_id, status="patient_published")
        latest_care_nursing = self.store.get_latest_care_card(patient_id, "nursing", status_filter="published")
        latest_care_medical = self.store.get_latest_care_card(patient_id, "medical", status_filter="published")

        timeline = {
            "patient_profile": {
                "patient_id": patient.patient_id if patient else patient_id,
                "ward_id": patient.ward_id if patient else None,
                "bed_id": patient.bed_id if patient else None,
                "age": patient.age if patient else None,
                "sex": patient.sex if patient else None,
            },
            "latest_daily_log": {
                "date": latest_log.date if latest_log else None,
                "diet": latest_log.diet if latest_log else None,
                "water_ml": latest_log.water_ml if latest_log else None,
                "sleep_hours": latest_log.sleep_hours if latest_log else None,
                "created_at": latest_log.created_at if latest_log else None,
            },
            "latest_nurse_admin": {
                "timestamp": latest_admin.timestamp if latest_admin else None,
                "vitals_json": latest_admin.vitals_json if latest_admin else None,
                "notes": latest_admin.notes if latest_admin else None,
            },
            "latest_assessment_summary": {},
            "latest_published_patient_card": {},
            "latest_published_care_cards": {},
            "recent_events": [],
        }

        if latest_assessment:
            diag = _json_load(latest_assessment.diagnosis_json, {})
            gaps = _json_load(latest_assessment.gaps_json, [])
            timeline["latest_assessment_summary"] = {
                "assessment_id": latest_assessment.assessment_id,
                "primary_diagnosis": diag.get("primary_diagnosis"),
                "risk_level": diag.get("risk_level"),
                "primary_basis": latest_assessment.primary_basis,
                "gaps_count": len(gaps),
            }

        if latest_card:
            content = latest_card.content_md or ""
            timeline["latest_published_patient_card"] = {
                "card_id": latest_card.card_id,
                "status": latest_card.status,
                "created_at": latest_card.created_at,
                "content_preview": content[:300],
            }
        if latest_care_nursing:
            timeline["latest_published_care_cards"]["nursing"] = {
                "card_id": latest_care_nursing.card_id,
                "created_at": latest_care_nursing.created_at,
                "title": latest_care_nursing.title,
                "one_liner": latest_care_nursing.one_liner,
            }
        if latest_care_medical:
            timeline["latest_published_care_cards"]["medical"] = {
                "card_id": latest_care_medical.card_id,
                "created_at": latest_care_medical.created_at,
                "title": latest_care_medical.title,
                "one_liner": latest_care_medical.one_liner,
            }

        events = []
        if latest_log:
            events.append(f"daily_log {latest_log.date}: diet={latest_log.diet}, water_ml={latest_log.water_ml}, sleep={latest_log.sleep_hours}")
        if latest_assessment:
            diag = _json_load(latest_assessment.diagnosis_json, {})
            events.append(
                f"assessment {latest_assessment.assessment_id}: primary={diag.get('primary_diagnosis')}, risk={diag.get('risk_level')}"
            )
        if latest_admin:
            events.append(f"nurse_admin {latest_admin.timestamp}")
        timeline["recent_events"] = events[:3]
        return timeline

    def _compute_and_store_risk(
        self,
        patient_id: str,
        ward_id: Optional[str],
        latest_assessment: Optional[Assessment] = None,
    ) -> Optional[RiskSnapshot]:
        if self.store is None:
            return None
        timeline = self._build_timeline(patient_id)
        latest_log = self.store.get_latest_daily_log(patient_id)
        latest_admin = self.store.get_latest_nurse_admin(patient_id)
        assessment = latest_assessment or self.store.get_latest_assessment(patient_id)
        care_cards_state = {
            "nursing_published": bool(self.store.get_latest_care_card(patient_id, "nursing", status_filter="published")),
            "medical_published": bool(self.store.get_latest_care_card(patient_id, "medical", status_filter="published")),
        }
        assessment_summary = {}
        gaps = []
        if assessment:
            diag = _json_load(assessment.diagnosis_json, {})
            assessment_summary = {
                "risk_level": diag.get("risk_level"),
                "primary_diagnosis": diag.get("primary_diagnosis"),
            }
            gaps = _json_load(assessment.gaps_json, [])

        snapshot_dict = compute_risk_snapshot(
            patient_profile=timeline.get("patient_profile") or {},
            latest_daily_log=latest_log.to_dict() if latest_log else {},
            latest_nurse_admin=latest_admin.to_dict() if latest_admin else {},
            latest_assessment_summary=assessment_summary,
            care_cards_state=care_cards_state,
            gaps=gaps,
        )
        snapshot = RiskSnapshot(
            snapshot_id=uuid.uuid4().hex,
            patient_id=patient_id,
            ward_id=ward_id,
            computed_at=snapshot_dict.get("computed_at") or _now_iso(),
            risk_level=snapshot_dict.get("risk_level", "green"),
            risk_score=int(snapshot_dict.get("risk_score", 0)),
            flags_json=_json_dumps(snapshot_dict.get("flags", [])),
            next_actions_json=_json_dumps(snapshot_dict.get("next_actions", [])),
            rules_version=snapshot_dict.get("rules_version", "r1.0"),
        )
        self.store.add_risk_snapshot(snapshot)
        return snapshot

    def _policy_filter(self, role: str, answer: Dict[str, Any]) -> Dict[str, Any]:
        text = str(answer.get("answer") or "")
        lowered = text.lower()
        flags = set(answer.get("safety_flags") or [])
        lang = str(answer.get("language") or self.lang).lower()

        patient_block = ["mg", "dose", "dosage", "increase", "decrease", "stop", "start", "antibiotic", "steroid"]
        nurse_block = ["increase dose", "decrease dose", "change dose", "stop antibiotic", "start antibiotic"]

        if role == "patient" and any(k in lowered for k in patient_block):
            answer["answer"] = (
                "Please consult your nurse or doctor for medication-related decisions."
                if lang == "en"
                else "Please consult your nurse or doctor for medication-related decisions."
            )
            flags.add("policy_filtered")
        if role == "nurse" and any(k in lowered for k in nurse_block):
            answer["answer"] = (
                "This concerns prescription changes. Please confirm with a doctor."
                if lang == "en"
                else "This concerns prescription changes. Please confirm with a doctor."
            )
            flags.add("policy_filtered")

        answer["safety_flags"] = list(flags)
        return answer

    def _need_escalation_by_rule(self, text: str) -> bool:
        lowered = (text or "").lower()
        keywords = ["chest pain", "severe shortness of breath", "confusion", "faint", "low oxygen"]
        return any(k in lowered for k in keywords)

    def _infer_topic_tag(self, text: str) -> str:
        t = (text or "").lower()
        if any(k in t for k in ["med", "dose", "medication", "药", "服药", "漏服", "加药", "停药"]):
            return "med_adherence"
        if any(k in t for k in ["worse", "worsen", "加重", "恶化", "更厉害", "夜间咳嗽"]):
            return "symptom_worsening"
        if any(k in t for k in ["sleep", "睡眠", "diet", "饮食", "喝水", "water"]):
            return "diet_sleep"
        if any(k in t for k in ["why", "what is", "怎么", "为什么", "解释", "education"]):
            return "education"
        return "other"

    def handle(
        self,
        mode: str,
        role: str,
        patient_id: Optional[str],
        ward_id: Optional[str],
        payload: dict,
        image=None,
        audio_path: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        mode = (mode or "").strip()
        role = (role or "").strip()
        payload = payload or {}
        request_id = request_id or ""

        if role not in ("patient", "nurse", "doctor"):
            return self._error("BAD_ROLE", "invalid role", request_id)

        if role == "patient":
            if not patient_id:
                return self._error("MISSING_PATIENT_ID", "patient_id required", request_id)
            if mode == "submit_nurse_admin":
                return self._error("FORBIDDEN", "patient cannot submit nurse admin", request_id)
            if mode in (
                "generate_patient_card_draft",
                "publish_patient_card",
                "hold_patient_card",
                "generate_care_card_draft",
                "update_care_card_draft",
                "publish_care_card",
                "hold_care_card",
                "compute_risk_snapshot",
                "generate_handover_draft",
                "save_handover",
                "list_handovers",
            ):
                return self._error("FORBIDDEN", "patient cannot manage care cards", request_id)
        else:
            if not ward_id:
                return self._error("MISSING_WARD_ID", "ward_id required", request_id)
            if mode == "chat" and not patient_id:
                return self._error("MISSING_PATIENT_ID", "patient_id required", request_id)

        if mode not in (
            "submit_daily_log",
            "submit_nurse_admin",
            "generate_assessment",
            "generate_patient_card_draft",
            "publish_patient_card",
            "hold_patient_card",
            "generate_care_card_draft",
            "update_care_card_draft",
            "publish_care_card",
            "hold_care_card",
            "tts_care_card",
            "compute_risk_snapshot",
            "generate_handover_draft",
            "save_handover",
            "list_handovers",
            "chat",
        ):
            return self._error("BAD_MODE", "unsupported mode", request_id)

        if mode == "submit_daily_log":
            return self._handle_daily_log(patient_id, payload, request_id)
        if mode == "submit_nurse_admin":
            if not patient_id:
                return self._error("MISSING_PATIENT_ID", "patient_id required", request_id)
            return self._handle_nurse_admin(patient_id, ward_id, payload, request_id)
        if mode == "generate_assessment":
            return self._handle_generate_assessment(
                role=role,
                patient_id=patient_id,
                ward_id=ward_id,
                payload=payload,
                image=image,
                audio_path=audio_path,
                request_id=request_id,
            )
        if mode == "generate_patient_card_draft":
            return self._handle_generate_patient_card_draft(
                role=role,
                patient_id=patient_id,
                ward_id=ward_id,
                payload=payload,
                request_id=request_id,
            )
        if mode == "publish_patient_card":
            return self._handle_publish_patient_card(
                role=role,
                patient_id=patient_id,
                ward_id=ward_id,
                payload=payload,
                status="patient_published",
                request_id=request_id,
            )
        if mode == "hold_patient_card":
            return self._handle_publish_patient_card(
                role=role,
                patient_id=patient_id,
                ward_id=ward_id,
                payload=payload,
                status="needs_review",
                request_id=request_id,
            )
        if mode == "generate_care_card_draft":
            return self._handle_generate_care_card_draft(
                role=role,
                patient_id=patient_id,
                ward_id=ward_id,
                payload=payload,
                request_id=request_id,
            )
        if mode == "update_care_card_draft":
            return self._handle_update_care_card_draft(
                role=role,
                patient_id=patient_id,
                ward_id=ward_id,
                payload=payload,
                request_id=request_id,
            )
        if mode == "publish_care_card":
            return self._handle_publish_care_card(
                role=role,
                patient_id=patient_id,
                ward_id=ward_id,
                payload=payload,
                status="published",
                request_id=request_id,
            )
        if mode == "hold_care_card":
            return self._handle_publish_care_card(
                role=role,
                patient_id=patient_id,
                ward_id=ward_id,
                payload=payload,
                status="held",
                request_id=request_id,
            )
        if mode == "tts_care_card":
            return self._handle_tts_care_card(
                role=role,
                patient_id=patient_id,
                ward_id=ward_id,
                payload=payload,
                request_id=request_id,
            )
        if mode == "tts_care_card":
            return self._handle_tts_care_card(
                role=role,
                patient_id=patient_id,
                ward_id=ward_id,
                payload=payload,
                request_id=request_id,
            )
        if mode == "compute_risk_snapshot":
            return self._handle_compute_risk_snapshot(
                role=role,
                patient_id=patient_id,
                ward_id=ward_id,
                request_id=request_id,
            )
        if mode == "generate_handover_draft":
            return self._handle_generate_handover_draft(
                role=role,
                patient_id=patient_id,
                ward_id=ward_id,
                payload=payload,
                request_id=request_id,
            )
        if mode == "save_handover":
            return self._handle_save_handover(
                role=role,
                patient_id=patient_id,
                ward_id=ward_id,
                payload=payload,
                request_id=request_id,
            )
        if mode == "list_handovers":
            return self._handle_list_handovers(
                role=role,
                patient_id=patient_id,
                ward_id=ward_id,
                request_id=request_id,
            )
        if mode == "chat":
            return self._handle_chat(
                role=role,
                patient_id=patient_id,
                ward_id=ward_id,
                payload=payload,
                audio_path=audio_path,
                request_id=request_id,
            )
        return self._error("NOT_IMPLEMENTED", "chat will be implemented in Phase A2", request_id)

    def _handle_daily_log(
        self, patient_id: Optional[str], payload: dict, request_id: str
    ) -> Dict[str, Any]:
        if not patient_id:
            return self._error("MISSING_PATIENT_ID", "patient_id required", request_id)
        log = DailyLog(
            patient_id=patient_id,
            date=str(payload.get("date") or ""),
            diet=payload.get("diet"),
            water_ml=payload.get("water_ml"),
            sleep_hours=payload.get("sleep_hours"),
            symptoms_json=payload.get("symptoms_json"),
            patient_reported_meds_json=payload.get("patient_reported_meds_json"),
            created_at=str(payload.get("created_at") or _now_iso()),
        )
        stored = False
        if self.store is not None:
            self.store.add_daily_log(log)
            stored = True
        return {
            "ok": True,
            "mode": "submit_daily_log",
            "patient_id": patient_id,
            "stored": stored,
            "log": log.to_dict(),
            "request_id": request_id,
        }

    def _handle_nurse_admin(
        self, patient_id: str, ward_id: Optional[str], payload: dict, request_id: str
    ) -> Dict[str, Any]:
        rec = NurseAdmin(
            patient_id=patient_id,
            timestamp=str(payload.get("timestamp") or ""),
            vitals_json=payload.get("vitals_json"),
            administered_meds_json=payload.get("administered_meds_json"),
            notes=payload.get("notes"),
            nurse_id=payload.get("nurse_id"),
        )
        stored = False
        risk_snapshot = None
        if self.store is not None:
            self.store.add_nurse_admin(rec)
            stored = True
            try:
                resolved_ward = ward_id
                if not resolved_ward:
                    patient = self.store.get_patient(patient_id)
                    resolved_ward = patient.ward_id if patient else None
                risk_snapshot = self._compute_and_store_risk(patient_id, resolved_ward)
            except Exception:
                risk_snapshot = None
        return {
            "ok": True,
            "mode": "submit_nurse_admin",
            "patient_id": patient_id,
            "stored": stored,
            "record": rec.to_dict(),
            "risk_snapshot": risk_snapshot.to_dict() if risk_snapshot else None,
            "request_id": request_id,
        }

    def _handle_generate_assessment(
        self,
        role: str,
        patient_id: Optional[str],
        ward_id: Optional[str],
        payload: dict,
        image=None,
        audio_path: Optional[str] = None,
        request_id: str = "",
    ) -> Dict[str, Any]:
        if role in ("nurse", "doctor") and not patient_id:
            return self._error("MISSING_PATIENT_ID", "patient_id required", request_id)
        if role == "patient" and not patient_id:
            return self._error("MISSING_PATIENT_ID", "patient_id required", request_id)

        note_or_history = str(payload.get("history") or "").strip()
        chief_text = str(payload.get("chief") or "").strip()
        if not chief_text or chief_text.lower() == "patient follow-up":
            chief_text = "Clinical follow-up based on recent patient status and nursing records."
        patient_dict = {
            "age": payload.get("age"),
            "sex": payload.get("sex"),
            "chief": chief_text,
            "history": note_or_history,
            "intern_plan": payload.get("intern_plan"),
        }

        context_snapshot = None
        timeline_hint = {}
        if self.store is not None and patient_id:
            latest_log = self.store.get_latest_daily_log(patient_id)
            latest_admin = self.store.get_latest_nurse_admin(patient_id)
            latest_assessment = self.store.get_latest_assessment(patient_id)
            recent_logs = self.store.list_daily_logs(patient_id, limit=5)
            recent_admin = self.store.list_nurse_admin(patient_id, limit=5)
            recent_daily_text = self._summarize_recent_daily_logs(recent_logs)
            recent_admin_text = self._summarize_recent_nurse_admin(recent_admin)

            history_parts = [note_or_history] if note_or_history else []
            if recent_daily_text:
                history_parts.append(recent_daily_text)
            if recent_admin_text:
                history_parts.append(recent_admin_text)
            if latest_assessment:
                diag = _json_load(latest_assessment.diagnosis_json, {})
                last_dx = str(diag.get("primary_diagnosis") or "").strip()
                last_risk = str(diag.get("risk_level") or "").strip()
                if last_dx or last_risk:
                    history_parts.append(
                        f"Last assessment: primary_diagnosis={last_dx or 'NA'}, risk_level={last_risk or 'NA'}."
                    )
            if history_parts:
                patient_dict["history"] = "\n\n".join(history_parts).strip()

            context_snapshot = {
                "latest_daily_log": latest_log.to_dict() if latest_log else None,
                "latest_nurse_admin": latest_admin.to_dict() if latest_admin else None,
                "latest_assessment_id": latest_assessment.assessment_id if latest_assessment else None,
                "recent_daily_logs": [log.to_dict() for log in recent_logs],
                "recent_nurse_admin": [rec.to_dict() for rec in recent_admin],
            }
            timeline_hint = {
                "daily_log_time": latest_log.created_at if latest_log else None,
                "nurse_admin_time": latest_admin.timestamp if latest_admin else None,
            }
        else:
            context_snapshot = payload.get("context_snapshot")

        view_mode = "Patient View" if role == "patient" else "Doctor View"

        result = self.orchestrator.run(
            view_mode=view_mode,
            patient=patient_dict,
            image=image,
            audio_path=audio_path,
            patient_id=patient_id,
            context_snapshot=context_snapshot,
        )
        result["assessment_status"] = "clinical_only"
        recommendations = []
        if self.store is not None and patient_id:
            try:
                timeline = self._build_timeline(patient_id)
                recommendations = self._get_care_card_agent().recommend_cards(result.get("gaps") or [], timeline)
            except Exception:
                recommendations = []

        if self.store is not None and patient_id:
            assessment = Assessment(
                assessment_id=str(result.get("assessment_id") or ""),
                patient_id=patient_id,
                timestamp=str(payload.get("timestamp") or _now_iso()),
                route_tag=result.get("route_tag"),
                primary_basis=result.get("primary_basis"),
                diagnosis_json=_json_dumps(result.get("diagnosis", {})),
                audit_json=_json_dumps(result.get("audit", {})),
                reverse_json=_json_dumps(result.get("reverse", {})),
                rag_evidence_json=_json_dumps(result.get("rag_evidence", [])),
                tool_trace_json=_json_dumps(result.get("tool_trace", [])),
                gaps_json=_json_dumps(result.get("gaps", [])),
            )
            self.store.add_assessment(assessment)
            try:
                risk_snapshot = self._compute_and_store_risk(patient_id, ward_id, latest_assessment=assessment)
            except Exception:
                risk_snapshot = None

            try:
                timeline = self._build_timeline(patient_id)
                assessment_struct = self._assessment_struct_from_row(assessment)
                card_json = self._get_care_card_agent().generate(
                    role="nurse",
                    lang=self.lang,
                    patient_id=patient_id,
                    timeline=timeline,
                    assessment_struct=assessment_struct,
                    card_level="nursing",
                )
                text_md = render_care_card(card_json, lang=self.lang, show_footer=True)
                latest_version = self.store.get_latest_care_card_version(patient_id, "nursing")
                daily_card = CareCard(
                    card_id=uuid.uuid4().hex,
                    patient_id=patient_id,
                    ward_id=ward_id,
                    created_at=_now_iso(),
                    created_by_role="system",
                    status="published",
                    card_level="nursing",
                    card_type="daily",
                    language=self.lang,
                    title=str(card_json.get("title") or "Daily Care Card"),
                    one_liner=str(card_json.get("one_liner") or ""),
                    bullets_json=_json_dumps(card_json.get("bullets") or []),
                    red_flags_json=_json_dumps(card_json.get("red_flags") or []),
                    followup_json=_json_dumps(card_json.get("follow_up") or []),
                    text_md=text_md,
                    audio_path=None,
                    source_assessment_id=assessment.assessment_id,
                    version=latest_version + 1,
                )
                self.store.add_care_card(daily_card)
            except Exception:
                pass

        return {
            "ok": True,
            "mode": "generate_assessment",
            "patient_id": patient_id,
            "ward_id": ward_id,
            "assessment_id": result.get("assessment_id"),
            "result": result,
            "care_card_recommendations": recommendations,
            "risk_snapshot": risk_snapshot.to_dict() if "risk_snapshot" in locals() and risk_snapshot else None,
            "timeline_hint": timeline_hint,
            "request_id": request_id,
        }

    def _handle_chat(
        self,
        role: str,
        patient_id: Optional[str],
        ward_id: Optional[str],
        payload: dict,
        audio_path: Optional[str],
        request_id: str = "",
    ) -> Dict[str, Any]:
        if not patient_id:
            return self._error("MISSING_PATIENT_ID", "patient_id required", request_id)
        if self.store is None:
            return self._error("STORE_UNAVAILABLE", "store not configured", request_id)

        tool_trace = []
        gaps = []
        user_message = str(payload.get("message") or "").strip()
        lang = (payload.get("lang") or self.lang or "en").strip().lower()

        transcript = ""
        asr_quality = None
        if audio_path:
            if self.asr_transcriber is None:
                tool_trace.append(
                    {"step": "asr_chat", "status": "failed", "success": False, "latency_ms": 0, "summary": "ASR unavailable", "error": "asr_missing", "artifacts": {}}
                )
                gaps.append(
                    {"id": "asr_unavailable", "severity": "low", "message": "ASR unavailable. Please use text input."}
                )
            else:
                import time

                start = time.monotonic()
                try:
                    transcript = self.asr_transcriber.transcribe(audio_path)
                    asr_quality = self._assess_audio_quality(transcript)
                    tool_trace.append(
                        {
                            "step": "asr_chat",
                            "status": "ok",
                            "success": True,
                            "latency_ms": int((time.monotonic() - start) * 1000),
                            "summary": f"ASR ok, transcript_len={len(transcript)}",
                            "error": None,
                            "artifacts": asr_quality,
                        }
                    )
                except Exception as exc:
                    tool_trace.append(
                        {
                            "step": "asr_chat",
                            "status": "failed",
                            "success": False,
                            "latency_ms": int((time.monotonic() - start) * 1000),
                            "summary": "ASR failed",
                            "error": str(exc),
                            "artifacts": {},
                        }
                    )
                    gaps.append(
                        {"id": "asr_failed", "severity": "low", "message": "ASR failed. Please use text input."}
                    )
        else:
            tool_trace.append(
                {"step": "asr_chat", "status": "skipped", "success": True, "latency_ms": 0, "summary": "ASR skipped (no audio)", "error": None, "artifacts": {}}
            )

        if asr_quality:
            if asr_quality.get("audio_quality_score", 0.0) < 0.4 or "empty_transcript" in (asr_quality.get("audio_issues") or []):
                gaps.append(
                    {"id": "low_audio_quality", "severity": "low", "message": "Audio is unclear. Please re-record or use text."}
                )

        if user_message and transcript:
            user_message = user_message + "\n\n[Voice transcript]\n" + transcript
        elif not user_message and transcript:
            user_message = transcript

        if not user_message:
            return self._error("EMPTY_MESSAGE", "message is empty", request_id)

        timeline = self._build_timeline(patient_id)
        summaries = self.store.list_chat_summaries(patient_id, limit=5)
        memory_summaries = [s.summary_text for s in summaries]

        chat_agent = self._get_chat_agent()
        import time
        start = time.monotonic()
        answer = chat_agent.answer(
            role=role,
            patient_id=patient_id,
            user_message=user_message,
            timeline=timeline,
            memory_summaries=memory_summaries,
            lang=lang,
            asr_quality=asr_quality,
        )
        tool_trace.append(
            {
                "step": "chat_agent",
                "status": "ok",
                "success": True,
                "latency_ms": int((time.monotonic() - start) * 1000),
                "summary": "ChatAgent ok",
                "error": None,
                "artifacts": {"lang": lang},
            }
        )

        if asr_quality:
            answer["asr_quality"] = asr_quality

        if gaps:
            existing = answer.get("new_gaps") or []
            answer["new_gaps"] = existing + gaps

        if self._need_escalation_by_rule(user_message):
            answer["need_escalation"] = True
            if not answer.get("escalation_reason"):
                answer["escalation_reason"] = "triggered_by_keywords"

        answer = self._policy_filter(role, answer)
        if not answer.get("topic_tag"):
            answer["topic_tag"] = self._infer_topic_tag(user_message + " " + str(answer.get("answer") or ""))

        stored = False
        summary_text = str(answer.get("assistant_summary_for_memory") or "").strip()
        key_flags = answer.get("safety_flags") or []
        if summary_text:
            chat_summary = ChatSummary(
                patient_id=patient_id,
                timestamp=_now_iso(),
                role=role,
                summary_text=summary_text,
                topic_tag=answer.get("topic_tag"),
                key_flags_json=_json_dumps(key_flags),
            )
            self.store.add_chat_summary(chat_summary)
            stored = True

        care_card = None
        try:
            card_json = self._get_care_card_agent().build_qa_card(user_message, answer, lang=lang)
            text_md = render_care_card(card_json, lang=lang, show_footer=True)
            latest_version = self.store.get_latest_care_card_version(patient_id, "nursing")
            care_card = CareCard(
                card_id=uuid.uuid4().hex,
                patient_id=patient_id,
                ward_id=ward_id,
                created_at=_now_iso(),
                created_by_role=role,
                status="published",
                card_level="nursing",
                card_type="qa",
                language=lang,
                title=str(card_json.get("title") or "Q&A Care Card"),
                one_liner=str(card_json.get("one_liner") or ""),
                bullets_json=_json_dumps(card_json.get("bullets") or []),
                red_flags_json=_json_dumps(card_json.get("red_flags") or []),
                followup_json=_json_dumps(card_json.get("follow_up") or []),
                text_md=text_md,
                audio_path=None,
                source_assessment_id=None,
                version=latest_version + 1,
            )
            self.store.add_care_card(care_card)
        except Exception:
            care_card = None

        return {
            "ok": True,
            "mode": "chat",
            "patient_id": patient_id,
            "answer": answer,
            "tool_trace": tool_trace,
            "stored_summary": stored,
            "care_card": care_card.to_dict() if care_card else None,
            "request_id": request_id,
        }

    def _handle_generate_patient_card_draft(
        self,
        role: str,
        patient_id: Optional[str],
        ward_id: Optional[str],
        payload: dict,
        request_id: str = "",
    ) -> Dict[str, Any]:
        if role != "nurse":
            return self._error("FORBIDDEN", "only nurse can generate patient card draft", request_id)
        if not patient_id:
            return self._error("MISSING_PATIENT_ID", "patient_id required", request_id)
        if self.store is None:
            return self._error("STORE_UNAVAILABLE", "store not configured", request_id)

        assessment = self.store.get_latest_assessment(patient_id)
        if assessment is None:
            return self._error("NO_ASSESSMENT", "no assessment found", request_id)
        log = self.store.get_latest_daily_log(patient_id)

        content_md = _build_patient_card_md(assessment, log, self.lang)
        card = PatientCard(
            card_id=uuid.uuid4().hex,
            patient_id=patient_id,
            ward_id=ward_id,
            status="patient_draft",
            content_md=content_md,
            source_assessment_id=assessment.assessment_id,
            created_at=_now_iso(),
            updated_at=None,
            author_role=role,
            card_type=None,
        )
        self.store.add_patient_card(card)
        return {
            "ok": True,
            "mode": "generate_patient_card_draft",
            "patient_id": patient_id,
            "ward_id": ward_id,
            "status": "patient_draft",
            "card": card.to_dict(),
            "request_id": request_id,
        }

    def _handle_publish_patient_card(
        self,
        role: str,
        patient_id: Optional[str],
        ward_id: Optional[str],
        payload: dict,
        status: str,
        request_id: str = "",
    ) -> Dict[str, Any]:
        if role != "nurse":
            return self._error("FORBIDDEN", "only nurse can publish/hold patient card", request_id)
        if not patient_id:
            return self._error("MISSING_PATIENT_ID", "patient_id required", request_id)
        if self.store is None:
            return self._error("STORE_UNAVAILABLE", "store not configured", request_id)

        content_md = str(payload.get("content_md") or "").strip()
        if not content_md:
            return self._error("EMPTY_CONTENT", "patient_card_md required", request_id)

        source_assessment_id = payload.get("source_assessment_id")
        if not source_assessment_id:
            latest = self.store.get_latest_assessment(patient_id)
            source_assessment_id = latest.assessment_id if latest else None

        card = PatientCard(
            card_id=uuid.uuid4().hex,
            patient_id=patient_id,
            ward_id=ward_id,
            status=status,
            content_md=content_md,
            source_assessment_id=source_assessment_id,
            created_at=_now_iso(),
            updated_at=None,
            author_role=role,
            card_type=None,
        )
        self.store.add_patient_card(card)
        return {
            "ok": True,
            "mode": "publish_patient_card",
            "patient_id": patient_id,
            "ward_id": ward_id,
            "status": status,
            "card": card.to_dict(),
            "request_id": request_id,
        }

    def _assessment_struct_from_row(self, assessment: Assessment) -> Dict[str, Any]:
        return {
            "assessment_id": assessment.assessment_id,
            "primary_basis": assessment.primary_basis,
            "diagnosis_json": _json_load(assessment.diagnosis_json, {}),
            "gaps": _json_load(assessment.gaps_json, []),
        }

    def _handle_generate_care_card_draft(
        self,
        role: str,
        patient_id: Optional[str],
        ward_id: Optional[str],
        payload: dict,
        request_id: str = "",
    ) -> Dict[str, Any]:
        if role not in ("nurse", "doctor"):
            return self._error("FORBIDDEN", "only nurse/doctor can generate care card draft", request_id)
        if not patient_id:
            return self._error("MISSING_PATIENT_ID", "patient_id required", request_id)
        if self.store is None:
            return self._error("STORE_UNAVAILABLE", "store not configured", request_id)

        card_level = str(payload.get("card_level") or "nursing").strip().lower()
        if card_level not in ("nursing", "medical"):
            return self._error("BAD_CARD_LEVEL", "card_level must be nursing or medical", request_id)
        lang = str(payload.get("lang") or self.lang or "en").strip().lower()

        assessment = self.store.get_latest_assessment(patient_id)
        if assessment is None:
            return self._error("NO_ASSESSMENT", "no assessment found", request_id)

        timeline = self._build_timeline(patient_id)
        assessment_struct = self._assessment_struct_from_row(assessment)

        draft = payload.get("draft")
        care_card_agent = self._get_care_card_agent()
        card_json = care_card_agent.generate(
            role=role,
            lang=lang,
            patient_id=patient_id,
            timeline=timeline,
            assessment_struct=assessment_struct,
            card_level=card_level,
            draft=draft if isinstance(draft, dict) else None,
        )

        latest_version = self.store.get_latest_care_card_version(patient_id, card_level)
        status = "draft"
        if card_level == "medical" and role != "doctor":
            status = "needs_review"
        card_type = str(payload.get("card_type") or "manual").strip().lower() or "manual"
        text_md = render_care_card(card_json, lang=lang, show_footer=True)

        card = CareCard(
            card_id=uuid.uuid4().hex,
            patient_id=patient_id,
            ward_id=ward_id,
            created_at=_now_iso(),
            created_by_role=role,
            status=status,
            card_level=card_level,
            card_type=card_type,
            language=lang,
            title=str(card_json.get("title") or ""),
            one_liner=str(card_json.get("one_liner") or ""),
            bullets_json=_json_dumps(card_json.get("bullets") or []),
            red_flags_json=_json_dumps(card_json.get("red_flags") or []),
            followup_json=_json_dumps(card_json.get("follow_up") or []),
            text_md=text_md,
            audio_path=None,
            source_assessment_id=assessment.assessment_id,
            version=latest_version + 1,
        )
        self.store.add_care_card(card)
        return {
            "ok": True,
            "mode": "generate_care_card_draft",
            "patient_id": patient_id,
            "ward_id": ward_id,
            "status": status,
            "card": card.to_dict(),
            "card_json": card_json,
            "request_id": request_id,
        }

    def _handle_update_care_card_draft(
        self,
        role: str,
        patient_id: Optional[str],
        ward_id: Optional[str],
        payload: dict,
        request_id: str = "",
    ) -> Dict[str, Any]:
        if role not in ("nurse", "doctor"):
            return self._error("FORBIDDEN", "only nurse/doctor can update care card draft", request_id)
        if not patient_id:
            return self._error("MISSING_PATIENT_ID", "patient_id required", request_id)
        if self.store is None:
            return self._error("STORE_UNAVAILABLE", "store not configured", request_id)

        card_id = str(payload.get("card_id") or "").strip()
        if not card_id:
            return self._error("MISSING_CARD_ID", "card_id required", request_id)

        title = str(payload.get("title") or "").strip()
        one_liner = str(payload.get("one_liner") or "").strip()
        bullets = _as_list(payload.get("bullets"))
        red_flags = _as_list(payload.get("red_flags"))
        follow_up = _as_list(payload.get("follow_up"))
        if not title:
            return self._error("EMPTY_TITLE", "title required", request_id)

        existing = self.store.get_care_card(card_id)
        lang = existing.language if existing and existing.language else self.lang
        text_md = render_care_card(
            {
                "title": title,
                "one_liner": one_liner,
                "bullets": bullets,
                "red_flags": red_flags,
                "follow_up": follow_up,
            },
            lang=lang,
            show_footer=True,
        )
        self.store.update_care_card_content(
            card_id=card_id,
            title=title,
            one_liner=one_liner,
            bullets_json=_json_dumps(bullets),
            red_flags_json=_json_dumps(red_flags),
            followup_json=_json_dumps(follow_up),
            text_md=text_md,
        )
        updated = self.store.get_care_card(card_id)
        return {
            "ok": True,
            "mode": "update_care_card_draft",
            "patient_id": patient_id,
            "ward_id": ward_id,
            "card": updated.to_dict() if updated else None,
            "request_id": request_id,
        }

    def _handle_publish_care_card(
        self,
        role: str,
        patient_id: Optional[str],
        ward_id: Optional[str],
        payload: dict,
        status: str,
        request_id: str = "",
    ) -> Dict[str, Any]:
        if role not in ("nurse", "doctor"):
            return self._error("FORBIDDEN", "only nurse/doctor can manage care cards", request_id)
        if not patient_id:
            return self._error("MISSING_PATIENT_ID", "patient_id required", request_id)
        if self.store is None:
            return self._error("STORE_UNAVAILABLE", "store not configured", request_id)

        card_id = str(payload.get("card_id") or "").strip()
        if not card_id:
            return self._error("MISSING_CARD_ID", "card_id required", request_id)
        card = self.store.get_care_card(card_id)
        if card is None:
            return self._error("CARD_NOT_FOUND", "care card not found", request_id)

        if card.card_level == "medical" and role != "doctor":
            return self._error("FORBIDDEN", "medical care card requires doctor approval", request_id)
        if card.card_level == "nursing" and role not in ("nurse", "doctor"):
            return self._error("FORBIDDEN", "nursing care card requires nurse/doctor", request_id)

        if status not in ("published", "held", "needs_review", "draft"):
            return self._error("BAD_STATUS", "unsupported status", request_id)

        self.store.update_care_card_status(card_id, status)
        updated = self.store.get_care_card(card_id)
        return {
            "ok": True,
            "mode": "publish_care_card",
            "patient_id": patient_id,
            "ward_id": ward_id,
            "status": status,
            "card": updated.to_dict() if updated else None,
            "request_id": request_id,
        }

    def _handle_tts_care_card(
        self,
        role: str,
        patient_id: Optional[str],
        ward_id: Optional[str],
        payload: dict,
        request_id: str = "",
    ) -> Dict[str, Any]:
        if not patient_id:
            return self._error("MISSING_PATIENT_ID", "patient_id required", request_id)
        if self.store is None:
            return self._error("STORE_UNAVAILABLE", "store not configured", request_id)

        card_id = str(payload.get("card_id") or "").strip()
        if not card_id:
            return self._error("MISSING_CARD_ID", "card_id required", request_id)
        card = self.store.get_care_card(card_id)
        if card is None:
            return self._error("CARD_NOT_FOUND", "care card not found", request_id)

        if card.audio_path:
            return {
                "ok": True,
                "mode": "tts_care_card",
                "patient_id": patient_id,
                "ward_id": ward_id,
                "audio_path": card.audio_path,
                "request_id": request_id,
            }

        try:
            from src.tools.tts_engine import tts
        except Exception as exc:
            return self._error("TTS_UNAVAILABLE", str(exc), request_id)

        text_md = card.text_md or render_care_card(
            {
                "title": card.title,
                "one_liner": card.one_liner,
                "bullets": _json_load(card.bullets_json, []),
                "red_flags": _json_load(card.red_flags_json, []),
                "follow_up": _json_load(card.followup_json, []),
            },
            lang=card.language or self.lang,
            show_footer=True,
        )
        audio_path = tts(text_md, lang=card.language or self.lang, card_id=card.card_id)
        if not audio_path:
            return self._error("TTS_FAILED", "tts failed", request_id)
        self.store.update_care_card_audio(card.card_id, audio_path)
        return {
            "ok": True,
            "mode": "tts_care_card",
            "patient_id": patient_id,
            "ward_id": ward_id,
            "audio_path": audio_path,
            "request_id": request_id,
        }

    def _handle_compute_risk_snapshot(
        self,
        role: str,
        patient_id: Optional[str],
        ward_id: Optional[str],
        request_id: str = "",
    ) -> Dict[str, Any]:
        if role not in ("nurse", "doctor"):
            return self._error("FORBIDDEN", "only nurse/doctor can compute risk", request_id)
        if not patient_id:
            return self._error("MISSING_PATIENT_ID", "patient_id required", request_id)
        if self.store is None:
            return self._error("STORE_UNAVAILABLE", "store not configured", request_id)

        snapshot = self._compute_and_store_risk(patient_id, ward_id)
        return {
            "ok": True,
            "mode": "compute_risk_snapshot",
            "patient_id": patient_id,
            "ward_id": ward_id,
            "snapshot": snapshot.to_dict() if snapshot else None,
            "request_id": request_id,
        }

    def _handle_generate_handover_draft(
        self,
        role: str,
        patient_id: Optional[str],
        ward_id: Optional[str],
        payload: dict,
        request_id: str = "",
    ) -> Dict[str, Any]:
        if role not in ("nurse", "doctor"):
            return self._error("FORBIDDEN", "only nurse/doctor can generate handover", request_id)
        if not patient_id:
            return self._error("MISSING_PATIENT_ID", "patient_id required", request_id)
        if self.store is None:
            return self._error("STORE_UNAVAILABLE", "store not configured", request_id)

        lang = str(payload.get("lang") or self.lang or "en").strip().lower()
        timeline = self._build_timeline(patient_id)
        snapshot = self.store.get_latest_risk_snapshot(patient_id)
        if snapshot is None:
            snapshot = self._compute_and_store_risk(patient_id, ward_id)

        handover_agent = self._get_handover_agent()
        result = handover_agent.generate(
            timeline=timeline,
            risk_snapshot={
                "risk_level": snapshot.risk_level if snapshot else "green",
                "flags": _json_load(snapshot.flags_json, []) if snapshot else [],
                "next_actions": _json_load(snapshot.next_actions_json, []) if snapshot else [],
            },
            lang=lang,
        )
        return {
            "ok": True,
            "mode": "generate_handover_draft",
            "patient_id": patient_id,
            "ward_id": ward_id,
            "sbar_md": result.get("sbar_md", ""),
            "key_points": result.get("key_points", []),
            "related_snapshot_id": snapshot.snapshot_id if snapshot else None,
            "request_id": request_id,
        }

    def _handle_save_handover(
        self,
        role: str,
        patient_id: Optional[str],
        ward_id: Optional[str],
        payload: dict,
        request_id: str = "",
    ) -> Dict[str, Any]:
        if role not in ("nurse", "doctor"):
            return self._error("FORBIDDEN", "only nurse/doctor can save handover", request_id)
        if not patient_id:
            return self._error("MISSING_PATIENT_ID", "patient_id required", request_id)
        if self.store is None:
            return self._error("STORE_UNAVAILABLE", "store not configured", request_id)

        sbar_md = str(payload.get("sbar_md") or "").strip()
        if not sbar_md:
            return self._error("EMPTY_CONTENT", "sbar_md required", request_id)
        key_points = payload.get("key_points") or []
        related_snapshot_id = payload.get("related_snapshot_id")

        latest_version = self.store.get_latest_handover_version(patient_id)
        record = HandoverRecord(
            handover_id=uuid.uuid4().hex,
            patient_id=patient_id,
            ward_id=ward_id,
            created_at=_now_iso(),
            created_by_role=role,
            sbar_md=sbar_md,
            key_points_json=_json_dumps(key_points),
            related_snapshot_id=related_snapshot_id,
            version=latest_version + 1,
        )
        self.store.add_handover(record)
        return {
            "ok": True,
            "mode": "save_handover",
            "patient_id": patient_id,
            "ward_id": ward_id,
            "handover": record.to_dict(),
            "request_id": request_id,
        }

    def _handle_list_handovers(
        self,
        role: str,
        patient_id: Optional[str],
        ward_id: Optional[str],
        request_id: str = "",
    ) -> Dict[str, Any]:
        if role not in ("nurse", "doctor"):
            return self._error("FORBIDDEN", "only nurse/doctor can list handovers", request_id)
        if not patient_id:
            return self._error("MISSING_PATIENT_ID", "patient_id required", request_id)
        if self.store is None:
            return self._error("STORE_UNAVAILABLE", "store not configured", request_id)

        handovers = self.store.list_handovers(patient_id, limit=10)
        return {
            "ok": True,
            "mode": "list_handovers",
            "patient_id": patient_id,
            "ward_id": ward_id,
            "handovers": [h.to_dict() for h in handovers],
            "request_id": request_id,
        }

    def _error(self, code: str, message: str, request_id: str) -> Dict[str, Any]:
        return {
            "ok": False,
            "error_code": code,
            "message": message,
            "request_id": request_id,
        }


if __name__ == "__main__":
    from src.agents.observer import MedGemmaClient, MedSigLIPAnalyzer
    from src.agents.asr import MedASRTranscriber
    from src.tools.rag_engine import RAGEngine

    medgemma = MedGemmaClient()
    image_analyzer = MedSigLIPAnalyzer()
    rag_engine = RAGEngine()
    asr_transcriber = MedASRTranscriber()

    orchestrator = AnalysisOrchestrator(
        medgemma,
        image_analyzer,
        rag_engine=rag_engine,
        asr_transcriber=asr_transcriber,
    )

    agent = WardAgent(store=None, orchestrator=orchestrator)

    daily = agent.handle(
        mode="submit_daily_log",
        role="patient",
        patient_id="demo_patient_001",
        ward_id=None,
        payload={
            "date": "2026-02-09",
            "diet": "normal",
            "water_ml": 700,
            "sleep_hours": 6.0,
            "symptoms_json": "{\"cough\": true}",
            "patient_reported_meds_json": "[]",
        },
        request_id="demo_daily_001",
    )
    print("submit_daily_log:", json.dumps(daily, ensure_ascii=False, indent=2))

    assessment = agent.handle(
        mode="generate_assessment",
        role="doctor",
        patient_id="demo_patient_001",
        ward_id="ward_a",
        payload={
            "age": 65,
            "sex": "Male",
            "chief": "Cough and fever for 3 days, shortness of breath.",
            "history": "COPD. No recent antibiotics.",
            "intern_plan": "",
        },
        request_id="demo_assess_001",
    )
    print("generate_assessment:", json.dumps(assessment, ensure_ascii=False, indent=2))
