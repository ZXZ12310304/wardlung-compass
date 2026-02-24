from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from src.utils.care_card_prompts import build_care_card_prompt


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _ensure_list(value: Any, default: Optional[List[str]] = None) -> List[str]:
    if default is None:
        default = []
    if value is None:
        return default
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str):
        lines = [line.strip(" -\t") for line in value.splitlines()]
        return [line for line in lines if line]
    return [str(value)]


def _contains_cjk(text: str) -> bool:
    for ch in text or "":
        if "\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf":
            return True
    return False


def _card_has_cjk(card: Dict[str, Any]) -> bool:
    if not isinstance(card, dict):
        return False
    fields = []
    for key in ("title", "one_liner"):
        fields.append(str(card.get(key) or ""))
    for key in ("bullets", "red_flags", "follow_up"):
        val = card.get(key) or []
        if isinstance(val, list):
            fields.extend([str(v) for v in val])
        else:
            fields.append(str(val))
    return any(_contains_cjk(text) for text in fields)


_MISSING_HINTS = (
    "missing",
    "lack",
    "not available",
    "insufficient",
    "need to measure",
    "measure",
    "check",
    "sp02",
    "spo2",
    "oxygen",
    "temperature",
    "respiratory rate",
    "heart rate",
    "vitals",
    "缺少",
    "建议补充",
    "测量",
    "血氧",
    "体温",
    "呼吸频率",
    "心率",
)


def _is_missing_hint(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(k in lowered for k in _MISSING_HINTS)


class CareCardAgent:
    def __init__(self, medgemma_client=None, rag_engine=None) -> None:
        self.medgemma_client = medgemma_client
        self.rag_engine = rag_engine

    def _skeleton_from_gaps(self, card_level: str, gaps: List[Dict[str, Any]]) -> Dict[str, Any]:
        actions = []
        for g in gaps[:3]:
            msg = g.get("message") if isinstance(g, dict) else str(g)
            if msg:
                actions.append(msg)
        if not actions:
            actions = ["Monitor symptoms and report changes."]
        red_flags = [
            "Severe shortness of breath",
            "Chest pain",
            "Confusion or fainting",
        ]
        return {
            "title": "Care Card",
            "one_liner": "Today focus and safety tips.",
            "bullets": actions[:5],
            "red_flags": red_flags,
            "follow_up": [],
            "boundaries": {
                "no_prescription_changes": True,
                "needs_doctor_approval": card_level == "medical",
            },
        }

    def _normalize(self, card: Dict[str, Any], fallback: Dict[str, Any], card_level: str) -> Dict[str, Any]:
        card = card or {}
        title = str(card.get("title") or fallback.get("title") or "Care Card")
        one_liner = str(card.get("one_liner") or fallback.get("one_liner") or "")
        bullets = _ensure_list(card.get("bullets"), _ensure_list(fallback.get("bullets")))
        red_flags = _ensure_list(card.get("red_flags"), _ensure_list(fallback.get("red_flags")))
        follow_up = _ensure_list(card.get("follow_up"), _ensure_list(fallback.get("follow_up")))
        if _is_missing_hint(one_liner):
            one_liner = ""
        bullets = [b for b in bullets if b and not _is_missing_hint(b)]
        if not bullets:
            bullets = [
                "Follow your care team's guidance.",
                "Rest and hydrate as tolerated.",
                "Monitor symptoms and report changes.",
            ]
        boundaries = card.get("boundaries") if isinstance(card.get("boundaries"), dict) else {}
        boundaries.setdefault("no_prescription_changes", True)
        boundaries.setdefault("needs_doctor_approval", card_level == "medical")
        return {
            "title": title,
            "one_liner": one_liner,
            "bullets": bullets,
            "red_flags": red_flags,
            "follow_up": follow_up,
            "boundaries": boundaries,
        }

    def _english_fallback(self, card_level: str) -> Dict[str, Any]:
        card = {
            "title": "Today's Care Card",
            "one_liner": "Daily care focus and safety tips.",
            "bullets": [
                "Monitor symptoms and report changes.",
                "Rest and hydrate as tolerated.",
                "Follow your care team's guidance.",
            ],
            "red_flags": [
                "Severe shortness of breath",
                "Chest pain",
                "Confusion or fainting",
            ],
            "follow_up": [],
            "boundaries": {
                "no_prescription_changes": True,
                "needs_doctor_approval": card_level == "medical",
            },
        }
        return self._normalize(card, card, card_level)

    def generate(
        self,
        role: str,
        lang: str,
        patient_id: str,
        timeline: Dict[str, Any],
        assessment_struct: Dict[str, Any],
        card_level: str,
        draft: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        gaps = []
        if isinstance(assessment_struct, dict):
            gaps = assessment_struct.get("gaps") or []
        fallback = draft or self._skeleton_from_gaps(card_level, gaps if isinstance(gaps, list) else [])

        if self.medgemma_client is None:
            return self._normalize(fallback, fallback, card_level)

        prompt = build_care_card_prompt(
            role=role,
            lang=lang,
            patient_id=patient_id,
            timeline=timeline,
            assessment_struct=assessment_struct,
            card_level=card_level,
            draft=fallback,
        )
        try:
            result = self.medgemma_client.run(prompt)
        except Exception as exc:
            return self._normalize({"title": "Care Card", "one_liner": str(exc)}, fallback, card_level)
        if isinstance(result, dict) and result.get("error"):
            return self._normalize({}, fallback, card_level)
        normalized = self._normalize(result if isinstance(result, dict) else {}, fallback, card_level)
        if (lang or "").lower().startswith("en") and _card_has_cjk(normalized):
            return self._english_fallback(card_level)
        return normalized

    def recommend_cards(self, gaps: List[Dict[str, Any]], timeline: Dict[str, Any]) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []
        gap_ids = set()
        for g in gaps or []:
            if isinstance(g, dict):
                gap_ids.add(g.get("id"))
        if {"missing_spo2", "missing_temp", "missing_rr", "missing_hr"} & gap_ids:
            recs.append(
                {
                    "type": "missing_vitals",
                    "priority": "high",
                    "suggested_card_level": "nursing",
                    "reason": "Missing vital signs (SpO2/temperature/respiratory rate/heart rate).",
                    "suggested_actions": [
                        "Measure vital signs today",
                        "Report any abnormal readings",
                    ],
                }
            )
        if "low_audio_quality" in gap_ids:
            recs.append(
                {
                    "type": "low_audio_quality",
                    "priority": "low",
                    "suggested_card_level": "nursing",
                    "reason": "Voice note quality is low or transcript is empty.",
                    "suggested_actions": [
                        "Use text input if possible",
                        "Speak slowly and close to the microphone",
                    ],
                }
            )
        latest_log = (timeline or {}).get("latest_daily_log") or {}
        diet_text = str(latest_log.get("diet") or "").lower()
        sleep_hours = latest_log.get("sleep_hours")
        water_ml = latest_log.get("water_ml")
        if any(k in diet_text for k in ["intake=少", "几乎", "low", "little", "very little"]):
            recs.append(
                {
                    "type": "nutrition_rest",
                    "priority": "medium",
                    "suggested_card_level": "nursing",
                    "reason": "Low intake reported.",
                    "suggested_actions": [
                        "Small frequent meals if tolerated",
                        "Ask nurse for nutrition help if needed",
                    ],
                }
            )
        if isinstance(sleep_hours, (int, float)) and sleep_hours <= 5:
            recs.append(
                {
                    "type": "sleep_rest",
                    "priority": "medium",
                    "suggested_card_level": "nursing",
                    "reason": "Sleep reported as short.",
                    "suggested_actions": [
                        "Rest when possible",
                        "Reduce unnecessary activity",
                    ],
                }
            )
        if isinstance(water_ml, (int, float)) and water_ml <= 600:
            recs.append(
                {
                    "type": "hydration",
                    "priority": "medium",
                    "suggested_card_level": "nursing",
                    "reason": "Low fluid intake reported.",
                    "suggested_actions": [
                        "Sip water regularly if not restricted",
                        "Tell nurse if nausea prevents drinking",
                    ],
                }
            )
        assessment = (timeline or {}).get("latest_assessment_summary") or {}
        risk_level = str(assessment.get("risk_level") or "").lower()
        if risk_level in ("high", "medium"):
            recs.append(
                {
                    "type": "red_flags",
                    "priority": "high",
                    "suggested_card_level": "nursing",
                    "reason": "Risk level indicates close monitoring.",
                    "suggested_actions": [
                        "Call nurse if breathing worsens",
                        "Seek help for chest pain or confusion",
                    ],
                }
            )
        if assessment:
            recs.append(
                {
                    "type": "medical_plan",
                    "priority": "medium",
                    "suggested_card_level": "medical",
                    "reason": "Assessment available; a medical explanation may be helpful.",
                    "suggested_actions": [
                        "Explain upcoming tests or treatments",
                        "Clarify what needs doctor confirmation",
                    ],
                }
            )
        return recs[:5]

    def build_qa_card(
        self,
        user_message: str,
        answer: Dict[str, Any],
        lang: str = "en",
    ) -> Dict[str, Any]:
        title = "Q&A Care Card"
        if user_message:
            short = user_message.strip().splitlines()[0][:40]
            title = f"Q&A: {short}"
        actions = answer.get("suggested_actions") or []
        red_flags = answer.get("safety_flags") or []
        if not actions:
            actions = ["Follow nurse/doctor guidance and monitor symptoms."]
        return {
            "title": title,
            "one_liner": answer.get("answer", "")[:120],
            "bullets": actions[:6],
            "red_flags": red_flags[:5],
            "follow_up": [],
            "boundaries": {
                "no_prescription_changes": True,
                "needs_doctor_approval": False,
            },
        }
