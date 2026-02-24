from __future__ import annotations

from typing import Any, Dict, Iterable, List

from src.ui.i18n import t


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str):
        lines = [line.strip(" -\t") for line in value.splitlines()]
        return [line for line in lines if line]
    return [str(value)]


def render_care_card(card: Dict[str, Any], lang: str = "en", show_footer: bool = True) -> str:
    lang = (lang or "en").strip().lower()
    title = str(card.get("title") or "")
    one_liner = str(card.get("one_liner") or "")
    bullets = _as_list(card.get("bullets"))
    red_flags = _as_list(card.get("red_flags"))
    follow_up = _as_list(card.get("follow_up"))

    lines: List[str] = []
    if title:
        lines.append(f"### {title}")
    if one_liner:
        lines.append(one_liner)

    if bullets:
        label = "DO" if lang.startswith("en") else t(lang, "carecard_section_actions")
        lines.append(f"\n**{label}**")
        for item in bullets:
            lines.append(f"- {item}")

    if red_flags:
        label = "GET HELP NOW" if lang.startswith("en") else t(lang, "carecard_section_redflags")
        lines.append(f"\n**{label}**")
        for item in red_flags:
            lines.append(f"- {item}")

    if follow_up:
        label = "DON'T" if lang.startswith("en") else t(lang, "carecard_section_followup")
        lines.append(f"\n**{label}**")
        for item in follow_up:
            lines.append(f"- {item}")

    if show_footer:
        lines.append(f"\n{t(lang, 'carecard_footer_not_medical_advice')}")

    return "\n".join(lines)
