from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _json_dump(data: Any) -> str:
    try:
        return json.dumps(data, ensure_ascii=False)
    except Exception:
        return "{}"


def build_chat_prompt(
    role: str,
    lang: str,
    user_message: str,
    timeline: Dict[str, Any],
    memory_summaries: List[str],
    rag_evidence: Optional[List[Dict[str, Any]]] = None,
    asr_quality: Optional[Dict[str, Any]] = None,
) -> str:
    role = (role or "").strip()
    lang = (lang or "en").strip().lower()
    language_hint = "English" if lang == "en" else "中文"

    role_rules = {
        "patient": (
            "Patient-facing: explain in plain language; self-care tips; when to call nurse/doctor. "
            "Do NOT give prescriptions, dose changes, or definitive diagnosis. Use uncertainty language. "
            "Write in second person (\"you/your\") and do not speak as the patient."
        ),
        "nurse": (
            "Nurse-facing: nursing workflow, monitoring, handoff points, when to escalate to doctor. "
            "Do NOT change prescriptions or give dosing changes."
        ),
        "doctor": (
            "Doctor-facing: differential discussion, evidence, missing data, next tests; keep uncertainty and safety notes."
        ),
    }
    role_rule = role_rules.get(role, role_rules["patient"])

    asr_hint = ""
    if asr_quality:
        issues = asr_quality.get("audio_issues") or []
        if issues:
            asr_hint = (
                f"ASR quality issues: {issues}. "
                "Please advise to re-record / speak slowly / move closer to mic if needed."
            )

    rag_block = ""
    if rag_evidence:
        rag_block = f"""
RAG evidence (short snippets, may be partial):
{_json_dump(rag_evidence)}
"""

    prompt = f"""
You are a clinical Q&A assistant. Follow role rules strictly.
Role: {role}
Language: {language_hint}
Role rules: {role_rule}
Safety: Keep answers non-definitive and include a short disclaimer appropriate to role.

Timeline (structured snapshot, do not invent beyond it):
{_json_dump(timeline)}

Recent memory summaries (last N):
{_json_dump(memory_summaries)}

{rag_block}

User message:
{user_message}

{asr_hint}

Return STRICT JSON with this schema:
{{
  "ok": true,
  "role": "{role}",
  "language": "{lang}",
  "answer": "...",
  "suggested_actions": ["...", "..."],
  "need_escalation": false,
  "escalation_reason": "",
  "safety_flags": [],
  "citations": [],
  "new_gaps": [],
  "topic_tag": "med_adherence|symptom_worsening|diet_sleep|education|other",
  "assistant_summary_for_memory": "1-2 sentence summary for memory"
}}
Only output JSON.
"""
    return prompt.strip()
