from __future__ import annotations

import json
from typing import Any, Dict, Optional


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def build_care_card_prompt(
    role: str,
    lang: str,
    patient_id: str,
    timeline: Dict[str, Any],
    assessment_struct: Dict[str, Any],
    card_level: str,
    draft: Optional[Dict[str, Any]] = None,
) -> str:
    language = "English" if (lang or "en").lower().startswith("en") else "Chinese"
    level_text = "nursing" if card_level == "nursing" else "medical"
    boundaries = {
        "nursing": [
            "No prescription changes, no dosing, no definitive diagnosis statements.",
            "Plain language, short sentences, action-oriented.",
        ],
        "medical": [
            "May mention possible tests/plans but must say 'doctor confirmation required'.",
            "Avoid definitive claims; keep uncertainty language.",
        ],
    }
    boundary_lines = boundaries["medical" if card_level == "medical" else "nursing"]

    schema = {
        "title": "...",
        "one_liner": "...",
        "bullets": ["..."],
        "red_flags": ["..."],
        "follow_up": ["..."],
        "boundaries": {
            "no_prescription_changes": True,
            "needs_doctor_approval": True,
        },
    }

    parts = [
        f"You are generating a {level_text} care card for patient_id={patient_id}.",
        f"Language: {language}.",
        "Output MUST be English only if Language is English. Do NOT use Chinese characters.",
        "Do NOT mention missing data, gaps, or ask for measurements. Use available info only; if unknown, omit it.",
        "Return STRICT JSON only. No extra commentary.",
        "JSON schema:",
        _json(schema),
        "Role boundaries:",
        "- " + "\n- ".join(boundary_lines),
        "Timeline (structured, short):",
        _json(timeline),
        "Latest assessment struct (lightweight):",
        _json(assessment_struct),
    ]
    if draft:
        parts.append("Draft skeleton to polish (keep structure, improve clarity):")
        parts.append(_json(draft))

    return "\n".join(parts)
