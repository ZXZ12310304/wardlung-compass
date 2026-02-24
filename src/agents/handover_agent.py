from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple


def _get_env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip() in ("1", "true", "True", "yes", "YES")


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str):
        lines = [line.strip(" -\t") for line in value.splitlines()]
        return [line for line in lines if line]
    return [str(value)]


def _pick_value(data: Dict[str, Any], keys: List[str]) -> Any:
    if not isinstance(data, dict):
        return None
    for key in keys:
        if key in data and data.get(key) not in (None, "", "-", "--"):
            return data.get(key)
    return None


def _fmt_optional(value: Any, suffix: str = "") -> str:
    if value in (None, "", "-", "--"):
        return "not recorded"
    text = str(value).strip()
    if not text:
        return "not recorded"
    return f"{text}{suffix}" if suffix else text


def _format_vitals_text(vitals: Any) -> str:
    if not isinstance(vitals, dict) or not vitals:
        return "not recorded"
    temp = _pick_value(vitals, ["temperature_c", "temperature"])
    hr = _pick_value(vitals, ["heart_rate", "hr"])
    rr = _pick_value(vitals, ["resp_rate", "respiratory_rate", "rr"])
    bp = _pick_value(vitals, ["bp", "blood_pressure"])
    spo2 = _pick_value(vitals, ["spo2", "spo2_pct"])
    pain = _pick_value(vitals, ["pain", "pain_score"])
    parts: List[str] = []
    if temp is not None:
        parts.append(f"Temp {_fmt_optional(temp)} C")
    if hr is not None:
        parts.append(f"HR {_fmt_optional(hr)} bpm")
    if rr is not None:
        parts.append(f"RR {_fmt_optional(rr)}/min")
    if bp is not None:
        parts.append(f"BP {_fmt_optional(bp)}")
    if spo2 is not None:
        parts.append(f"SpO2 {_fmt_optional(spo2)}%")
    if pain is not None:
        parts.append(f"Pain {_fmt_optional(pain)}/10")
    return ", ".join(parts) if parts else "not recorded"


def build_sbar_skeleton(
    timeline: Dict[str, Any],
    risk_snapshot: Dict[str, Any],
    lang: str = "en",
) -> Tuple[str, List[str]]:
    lang = (lang or "en").lower()
    patient = (timeline or {}).get("patient_profile") or {}
    latest_log = (timeline or {}).get("latest_daily_log") or {}
    latest_admin = (timeline or {}).get("latest_nurse_admin") or {}
    latest_assessment = (timeline or {}).get("latest_assessment_summary") or {}

    risk_level = (risk_snapshot or {}).get("risk_level", "green")
    flags = _as_list([f.get("message") for f in (risk_snapshot.get("flags") or []) if isinstance(f, dict)])
    actions = _as_list(risk_snapshot.get("next_actions") or [])

    vitals = latest_admin.get("vitals_json") or {}
    sbar_lines: List[str] = []
    key_points: List[str] = []

    if lang.startswith("zh"):
        sbar_lines.append(f"**S（现状）**：风险灯={risk_level.upper()}。{flags[0] if flags else '暂无明显红旗。'}")
        sbar_lines.append(
            f"**B（背景）**：患者{patient.get('bed_id') or '-'}，"
            f"年龄{patient.get('age') or '-'}，性别{patient.get('sex') or '-'}。"
            f"今日饮食{latest_log.get('diet') or '-'}，饮水{latest_log.get('water_ml') or '-'}ml，睡眠{latest_log.get('sleep_hours') or '-'}小时。"
        )
        sbar_lines.append(
            f"**A（评估）**：最新体征{vitals or '-'}；"
            f"评估结论{latest_assessment.get('primary_diagnosis') or '-'}，"
            f"风险{latest_assessment.get('risk_level') or '-'}，缺口数{latest_assessment.get('gaps_count') or 0}。"
        )
        sbar_lines.append(f"**R（建议）**：{actions[:3] if actions else ['继续观察，必要时通知医生。']}")
    else:
        diet_text = _fmt_optional(latest_log.get("diet"))
        water_text = _fmt_optional(latest_log.get("water_ml"), " ml")
        sleep_text = _fmt_optional(latest_log.get("sleep_hours"), " hrs")
        vitals_text = _format_vitals_text(vitals)
        dx_text = _fmt_optional(latest_assessment.get("primary_diagnosis"))
        risk_text = _fmt_optional(latest_assessment.get("risk_level"))
        gaps_text = _fmt_optional(latest_assessment.get("gaps_count"))
        rec_items = actions[:3] if actions else ["Continue monitoring", "Notify doctor if worsening"]
        rec_text = "; ".join([str(x).strip().rstrip(".") for x in rec_items if str(x).strip()]) or "Continue monitoring"
        sbar_lines.append(f"**S (Situation)**: Risk light={risk_level.upper()}. {flags[0] if flags else 'No urgent red flags.'}")
        sbar_lines.append(
            f"**B (Background)**: Bed {patient.get('bed_id') or '-'}, age {patient.get('age') or '-'}, sex {patient.get('sex') or '-'}. "
            f"Diet {diet_text}, water {water_text}, sleep {sleep_text}."
        )
        sbar_lines.append(
            f"**A (Assessment)**: Latest vitals {vitals_text}; "
            f"assessment {dx_text}, risk {risk_text}, gaps {gaps_text}."
        )
        sbar_lines.append(f"**R (Recommendation)**: {rec_text}.")

    key_points.extend(flags[:3])
    key_points.extend(actions[:3])
    return "\n".join(sbar_lines), key_points[:6]


class HandoverAgent:
    def __init__(self, medgemma_client=None) -> None:
        self.medgemma_client = medgemma_client

    def generate(self, timeline: Dict[str, Any], risk_snapshot: Dict[str, Any], lang: str = "en") -> Dict[str, Any]:
        sbar_md, key_points = build_sbar_skeleton(timeline, risk_snapshot, lang=lang)

        if _get_env_flag("HANDOVER_USE_LLM", "0") and self.medgemma_client is not None:
            prompt = (
                "Polish the following SBAR for clarity. Keep structure and do not add new facts. "
                "Return only the polished SBAR text.\n\n"
                + sbar_md
            )
            try:
                res = self.medgemma_client.run(prompt)
                if isinstance(res, dict) and res.get("answer"):
                    sbar_md = str(res.get("answer"))
            except Exception:
                pass

        return {"sbar_md": sbar_md, "key_points": key_points}
