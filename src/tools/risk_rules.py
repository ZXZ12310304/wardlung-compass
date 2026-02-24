from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_load(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _get_num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _add_flag(
    flags: List[Dict[str, Any]],
    flag_id: str,
    severity: str,
    message: str,
    evidence: Optional[Dict[str, Any]] = None,
    recommendation: Optional[str] = None,
) -> int:
    flags.append(
        {
            "id": flag_id,
            "severity": severity,
            "message": message,
            "evidence": evidence or {},
            "recommendation": recommendation or "",
        }
    )
    if severity == "high":
        return 35
    if severity == "medium":
        return 15
    return 5


def compute_risk_snapshot(
    patient_profile: Dict[str, Any],
    latest_daily_log: Dict[str, Any],
    latest_nurse_admin: Dict[str, Any],
    latest_assessment_summary: Dict[str, Any],
    care_cards_state: Dict[str, Any],
    gaps: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    flags: List[Dict[str, Any]] = []
    score = 0

    vitals = _json_load(latest_nurse_admin.get("vitals_json"), {}) if latest_nurse_admin else {}
    symptoms = _json_load(latest_daily_log.get("symptoms_json"), {}) if latest_daily_log else {}
    notes_text = _as_text(latest_nurse_admin.get("notes") if latest_nurse_admin else "")

    spo2 = _get_num(vitals.get("spo2_pct") or vitals.get("spo2") or vitals.get("SpO2"))
    temp = _get_num(vitals.get("temperature_c") or vitals.get("temperature") or vitals.get("temp_c"))
    rr = _get_num(vitals.get("resp_rate") or vitals.get("rr"))
    hr = _get_num(vitals.get("heart_rate") or vitals.get("hr"))
    sbp = _get_num(vitals.get("bp_sys") or vitals.get("sbp"))

    if spo2 is not None and spo2 < 90:
        score += _add_flag(
            flags,
            "low_spo2",
            "high",
            f"SpO2 {spo2:.0f}% (<90)",
            {"spo2": spo2},
            "Notify doctor immediately",
        )

    if temp is not None and temp >= 39.0:
        score += _add_flag(
            flags,
            "high_temp",
            "high",
            f"Temperature {temp:.1f}°C (>=39.0)",
            {"temperature_c": temp},
            "Monitor closely and notify doctor if persistent",
        )
    elif temp is not None and temp >= 38.0 and hr is not None and hr > 110:
        score += _add_flag(
            flags,
            "fever_with_tachycardia",
            "medium",
            f"Temperature {temp:.1f}°C with HR {hr:.0f}",
            {"temperature_c": temp, "heart_rate": hr},
            "Recheck vitals and consider escalation",
        )

    if rr is not None and rr >= 30:
        score += _add_flag(
            flags,
            "high_rr",
            "high",
            f"RR {rr:.0f} (>=30)",
            {"resp_rate": rr},
            "Notify doctor immediately",
        )

    if sbp is not None and sbp < 90:
        score += _add_flag(
            flags,
            "low_sbp",
            "high",
            f"SBP {sbp:.0f} (<90)",
            {"sbp": sbp},
            "Urgent review needed",
        )

    if hr is not None and hr >= 130:
        score += _add_flag(
            flags,
            "high_hr",
            "high",
            f"HR {hr:.0f} (>=130)",
            {"heart_rate": hr},
            "Urgent review needed",
        )
    elif hr is not None and 110 <= hr < 130:
        score += _add_flag(
            flags,
            "moderate_hr",
            "medium",
            f"HR {hr:.0f} (110-129)",
            {"heart_rate": hr},
            "Recheck and monitor",
        )

    mental_keywords = ["confusion", "drowsy", "altered", "意识差", "嗜睡"]
    text_blob = (notes_text + " " + _as_text(symptoms)).lower()
    if any(k in text_blob for k in mental_keywords):
        score += _add_flag(
            flags,
            "mental_status_change",
            "high",
            "Possible altered mental status",
            {"notes": notes_text},
            "Notify doctor immediately",
        )

    diet_text = _as_text(latest_daily_log.get("diet") if latest_daily_log else "").lower()
    water_ml = _get_num(latest_daily_log.get("water_ml") if latest_daily_log else None)
    sleep_hours = _get_num(latest_daily_log.get("sleep_hours") if latest_daily_log else None)
    low_diet = any(k in diet_text for k in ["intake=少", "几乎没吃", "very little", "low"])
    if low_diet and (water_ml is not None and water_ml < 600) and (sleep_hours is not None and sleep_hours < 4):
        score += _add_flag(
            flags,
            "low_intake_dehydration",
            "medium",
            "Low intake + low water + short sleep",
            {"water_ml": water_ml, "sleep_hours": sleep_hours},
            "Encourage fluids and rest; monitor closely",
        )

    symptom_text = _as_text(symptoms).lower()
    if any(k in symptom_text for k in ["咯血", "hemoptysis", "severe shortness of breath"]):
        score += _add_flag(
            flags,
            "severe_resp_symptom",
            "high",
            "Severe respiratory symptom reported",
            {"symptoms": symptoms},
            "Escalate to doctor immediately",
        )
    elif any(k in symptom_text for k in ["胸痛", "chest pain", "气短明显", "shortness of breath"]):
        score += _add_flag(
            flags,
            "resp_symptom_warning",
            "medium",
            "Respiratory warning symptom reported",
            {"symptoms": symptoms},
            "Monitor and consider escalation",
        )

    gap_ids = set()
    for g in gaps or []:
        if isinstance(g, dict):
            gap_ids.add(g.get("id"))
    missing_vitals = {"missing_spo2", "missing_temp", "missing_rr", "missing_hr"} & gap_ids
    if missing_vitals:
        score += _add_flag(
            flags,
            "missing_vitals",
            "medium",
            "Missing vital signs data",
            {"missing": sorted(list(missing_vitals))},
            "Measure vital signs",
        )

    if "low_audio_quality" in gap_ids:
        score += _add_flag(
            flags,
            "low_audio_quality",
            "low",
            "Audio quality is low",
            {},
            "Use text input or re-record",
        )

    assessment_risk = _as_text(latest_assessment_summary.get("risk_level") if latest_assessment_summary else "")
    if assessment_risk.lower() == "high":
        score += _add_flag(
            flags,
            "assessment_high_risk",
            "medium",
            "Assessment risk_level=High",
            {"risk_level": assessment_risk},
            "Prioritize monitoring",
        )

    risk_level = "green"
    if any(f["severity"] == "high" for f in flags):
        risk_level = "red"
    elif any(f["severity"] == "medium" for f in flags):
        risk_level = "yellow"

    score = max(0, min(100, int(score)))

    actions = []
    for f in flags:
        rec = f.get("recommendation")
        if rec:
            actions.append(rec)
    actions = list(dict.fromkeys(actions))[:6]

    return {
        "risk_level": risk_level,
        "risk_score": score,
        "flags": flags,
        "next_actions": actions,
        "computed_at": _now_iso(),
        "rules_version": "r1.0",
    }


def _demo_case(name: str, vitals: dict, gaps: list[dict]) -> None:
    snapshot = compute_risk_snapshot(
        patient_profile={"age": 68, "sex": "M"},
        latest_daily_log={"diet": "intake=少", "water_ml": 500, "sleep_hours": 3.5, "symptoms_json": '{"chest_pain": true}'},
        latest_nurse_admin={"vitals_json": vitals, "notes": ""},
        latest_assessment_summary={"risk_level": "High"},
        care_cards_state={},
        gaps=gaps,
    )
    print(f"\n== {name} ==")
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _demo_case("normal", {"spo2_pct": 96, "temperature_c": 37.2, "resp_rate": 18, "heart_rate": 88}, [])
    _demo_case("low_spo2", {"spo2_pct": 89, "temperature_c": 37.5, "resp_rate": 22, "heart_rate": 95}, [])
    _demo_case("high_fever_rr", {"spo2_pct": 92, "temperature_c": 39.1, "resp_rate": 32, "heart_rate": 128}, [{"id": "missing_rr"}])
