from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from PIL import Image

from src.utils.chat_prompts import build_chat_prompt


def _ensure_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _truncate(text: str, limit: int = 220) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return t[: limit - 3] + "..."


def _should_use_rag(message: str) -> bool:
    msg = (message or "").strip().lower()
    if not msg:
        return False
    if len(msg) >= 120:
        return True
    if "?" in msg:
        return True
    keywords = [
        "guideline",
        "evidence",
        "protocol",
        "recommend",
        "treatment",
        "antibiotic",
        "pneumonia",
        "cap",
        "risk",
        "criteria",
        "what is",
        "why",
        "how to",
        "explain",
    ]
    return any(k in msg for k in keywords)


def _short_snippet(text: str, limit: int = 220) -> str:
    return _truncate(text or "", limit=limit)


def _normalize_patient_voice(answer: str) -> str:
    text = (answer or "").strip()
    if not text:
        return text
    replacements = [
        (r"\bI should\b", "You should"),
        (r"\bI need to\b", "You should"),
        (r"\bI have to\b", "You should"),
        (r"\bI must\b", "You should"),
        (r"\bI will\b", "You can"),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text, flags=re.I)
    for pattern, repl in [
        (r"\bmy cough\b", "your cough"),
        (r"\bmy symptoms\b", "your symptoms"),
        (r"\bmy breathing\b", "your breathing"),
        (r"\bmy chest\b", "your chest"),
        (r"\bmy energy\b", "your energy"),
        (r"\bmy fever\b", "your fever"),
        (r"\bmy pain\b", "your pain"),
    ]:
        text = re.sub(pattern, repl, text, flags=re.I)
    return text


class ChatAgent:
    def __init__(self, medgemma_client, rag_engine=None, lang: str = "en") -> None:
        self.medgemma = medgemma_client
        self.rag_engine = rag_engine
        self.lang = (lang or "en").strip().lower()

    def answer(
        self,
        role: str,
        patient_id: str,
        user_message: str,
        timeline: Dict[str, Any],
        memory_summaries: List[str],
        lang: str = "en",
        asr_quality: Optional[Dict[str, Any]] = None,
        image: Optional[Image.Image] = None,
    ) -> Dict[str, Any]:
        role = (role or "").strip()
        lang = (lang or self.lang or "en").strip().lower()
        user_message = (user_message or "").strip()

        rag_evidence: List[Dict[str, Any]] = []
        if self.rag_engine is not None and _should_use_rag(user_message):
            try:
                top_k = max(3, min(5, int(os.getenv("CHAT_RAG_TOP_K", "4"))))
            except Exception:
                top_k = 4
            try:
                rag_evidence = self.rag_engine.query(user_message, top_k=top_k)
            except Exception:
                rag_evidence = []
        if rag_evidence:
            rag_evidence = [
                {
                    "source_file": e.get("source_file"),
                    "score": e.get("score"),
                    "snippet": _short_snippet(e.get("text", "")),
                }
                for e in rag_evidence[:5]
            ]

        prompt = build_chat_prompt(
            role=role,
            lang=lang,
            user_message=user_message,
            timeline=timeline or {},
            memory_summaries=memory_summaries or [],
            rag_evidence=rag_evidence or None,
            asr_quality=asr_quality,
        )

        raw = self.medgemma.run(prompt, image=image)
        if not isinstance(raw, dict) or raw.get("error"):
            fallback = (
                "Please contact your nurse/doctor if symptoms worsen."
                if lang == "en"
                else "如症状加重，请及时联系护士或医生。"
            )
            return {
                "ok": True,
                "role": role,
                "language": lang,
                "answer": fallback,
                "suggested_actions": [],
                "need_escalation": False,
                "escalation_reason": "",
                "safety_flags": ["model_error"],
                "citations": [],
                "new_gaps": [],
                "assistant_summary_for_memory": _truncate(user_message or fallback),
            }

        answer = str(raw.get("answer") or "").strip()
        if role == "patient":
            answer = _normalize_patient_voice(answer)
        suggested_actions = [str(x) for x in _ensure_list(raw.get("suggested_actions")) if str(x).strip()]
        safety_flags = [str(x) for x in _ensure_list(raw.get("safety_flags")) if str(x).strip()]
        citations = [c for c in _ensure_list(raw.get("citations")) if isinstance(c, dict)]
        new_gaps = [g for g in _ensure_list(raw.get("new_gaps")) if isinstance(g, dict)]
        topic_tag = str(raw.get("topic_tag") or "").strip() or "other"

        if rag_evidence:
            rag_cites = [
                {"source_file": e.get("source_file"), "score": e.get("score")}
                for e in rag_evidence
                if e.get("source_file")
            ]
            if not citations:
                citations = rag_cites
            else:
                citations = citations + rag_cites

        need_escalation = raw.get("need_escalation")
        if not isinstance(need_escalation, bool):
            need_escalation = False

        escalation_reason = str(raw.get("escalation_reason") or "").strip()
        summary = str(raw.get("assistant_summary_for_memory") or "").strip()
        if not summary:
            summary = _truncate(answer or user_message)

        return {
            "ok": True,
            "role": role,
            "language": lang,
            "answer": answer,
            "suggested_actions": suggested_actions[:6],
            "need_escalation": bool(need_escalation),
            "escalation_reason": escalation_reason,
            "safety_flags": safety_flags,
            "citations": citations[:3],
            "new_gaps": new_gaps,
            "topic_tag": topic_tag,
            "assistant_summary_for_memory": summary,
        }
