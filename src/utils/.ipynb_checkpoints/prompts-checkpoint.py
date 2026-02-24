# src/utils/prompts.py
import json
from typing import Any, Dict, Optional

SYSTEM_PROMPT = (
    "You are a clinical AI assistant.\n"
    "You provide decision support only, not a final diagnosis.\n"
    "If risk is high or uncertainty is significant, expert review is required.\n"
    "CRITICAL OUTPUT RULES:\n"
    "- Output ONLY a single valid JSON object.\n"
    "- Do NOT wrap JSON in markdown.\n"
    "- Do NOT include any text before or after the JSON.\n"
)


def build_diagnosis_prompt(
    view_mode: str,
    patient: Dict[str, Any],
    img_findings: Optional[Dict[str, Any]] = None,
    evidence_text: str = "",
) -> str:
    is_doctor = view_mode == "Doctor View"
    has_image = img_findings is not None

    transcript = (patient.get("audio_transcript") or "").strip()
    fused = (patient.get("multimodal_summary") or "").strip()

    modalities = patient.get("modalities", {}) or {}
    quality = patient.get("quality", {}) or {}
    basis_hint = (patient.get("primary_basis_hint") or "").strip()

    audio_block = f"\n[AUDIO TRANSCRIPT]\n{transcript}\n" if transcript else ""
    fused_block = f"\n[MULTIMODAL FUSION SUMMARY]\n{fused}\n" if fused else ""

    # imaging context + evidence strength
    if has_image:
        img_context = (
            "[IMAGING DATA DETECTED]\n"
            f"AI Finding: {img_findings.get('primary_finding', 'Unknown')}\n"
            f"Confidence: {img_findings.get('confidence', 'N/A')}\n"
            f"Interpretable: {img_findings.get('interpretable', False)}\n"
            f"Evidence Strength: {img_findings.get('evidence_strength', 'low')}\n"
            f"Suggests Pneumonia: {img_findings.get('suggests_pneumonia', False)}\n"
            f"Top Candidates: {img_findings.get('top_candidates', [])}\n"
            f"Issues: {img_findings.get('issues', [])}\n"
        )
    else:
        img_context = "[NO IMAGING DATA PROVIDED] - Rely STRICTLY on clinical history and symptoms."

    evidence_block = ""
    if evidence_text:
        evidence_block = (
            "\n[EVIDENCE (RAG)]\n"
            "Use RAG evidence first when it is relevant and consistent. "
            "If evidence is insufficient or conflicting, say so and rely on clinical data.\n"
            f"{evidence_text}\n"
        )

    # --- Shared modality block ---
    modality_block = f"""
[MODALITIES]
- has_audio: {bool(modalities.get("has_audio", False))}
- has_image: {bool(modalities.get("has_image", False))}

[QUALITY SCORES] (0.0~1.0, higher = more reliable)
- audio_quality_score: {quality.get("audio_quality_score", 0.0)}
- image_quality_score: {quality.get("image_quality_score", 0.0)}
- rag_used: {bool(evidence_text.strip())}
- basis_hint: {basis_hint if basis_hint else "N/A"}

[EVIDENCE PRIORITY RULES]
1) If imaging output is NOT interpretable (e.g., LABEL_*), treat it as LOW confidence and do NOT let it override clinical text evidence.
2) If audio transcript contains heavy noise / repetition, treat it as LOW confidence.
3) Prefer consistent evidence across modalities. If conflict exists, explicitly list conflicts.
4) Never claim sex-specific contradictions for common diseases (e.g., pneumonia can occur in any sex).
""".strip()

    if is_doctor:
        json_schema = """
{
  "primary_diagnosis": "Most likely condition",
  "confidence_score": 0-100,
  "risk_level": "Low/Medium/High",
  "risk_drivers": ["factor 1", "factor 2"],
  "treatment_suggestions": ["action 1", "action 2"],
  "red_flags": ["warning 1", "warning 2"],

  "primary_basis": "audio|image|rag|clinical|mixed",
  "evidence_used": ["clinical", "audio", "image", "rag"],
  "evidence_strength": {
    "clinical": 0.0,
    "audio": 0.0,
    "image": 0.0,
    "rag": 0.0
  },
  "evidence_conflicts": ["describe conflicts if any, else empty array"]
}
""".strip()

        role = "You are a clinical AI assistant (Junior Doctor). Provide a primary diagnosis."

        return f"""
{role}

{modality_block}

{img_context}
{audio_block}
{fused_block}
{evidence_block}

Patient Data:
Age: {patient["age"]}, Sex: {patient["sex"]}
Complaint: {patient["chief"]}
History: {patient["history"]}

CRITICAL: Return ONLY a single valid JSON object. No markdown.
Target JSON Schema:
{json_schema}
""".strip()

    # Patient View
    return f"""
You are a helpful medical assistant explaining to a patient.

{modality_block}

Context: Age {patient["age"]}, {patient["sex"]}, Complaint: {patient["chief"]}
{img_context}
{audio_block}
{fused_block}
{evidence_block}

TASK:
1. Explain the situation simply.
2. Provide actionable advice.
3. Create a COMPREHENSION QUIZ (Exactly 3 Questions).

CRITICAL QUIZ RULES:
- Do NOT ask about patient history.
- Exactly 3 questions.
- CONTEXTUAL RELEVANCE: All questions must be strictly about the patient's current complaint ("{patient['chief']}").
- ANTI-HALLUCINATION: Avoid unrelated questions.
- LOGIC CHECK: Make sure the 'correct_index' matches the right answer.

Return ONLY JSON:
{{
  "gentle_summary": "Simple explanation...",
  "what_to_watch": ["symptom 1", "symptom 2"],
  "next_steps": ["action 1", "action 2"],
  "quiz": [
    {{
      "question": "Q1: Knowledge Check",
      "options": ["Wrong A", "Correct B", "Wrong C"],
      "correct_index": 1,
      "explanation": "..."
    }},
    {{
      "question": "Q2: Symptom Check",
      "options": ["Correct A", "Wrong B", "Wrong C"],
      "correct_index": 0,
      "explanation": "..."
    }},
    {{
      "question": "Q3: Action Check",
      "options": ["Wrong A", "Wrong B", "Correct C"],
      "correct_index": 2,
      "explanation": "..."
    }}
  ],
  "primary_basis": "audio|image|rag|clinical|mixed"
}}
""".strip()


def build_audit_prompt(patient: Dict[str, Any], initial_diagnosis: Dict[str, Any]) -> str:
    modalities = patient.get("modalities", {}) or {}
    quality = patient.get("quality", {}) or {}

    return f"""
You are a Senior Chief Physician (Auditor).
Your task: Review the Junior Doctor's diagnosis for logic errors, hallucinations, or safety risks.

[HARD SAFETY RULES]
- Do NOT invent sex-specific contradictions for common diseases (e.g., pneumonia can occur in any sex).
- Only flag contradictions that are truly impossible (e.g., pregnancy in a male, wrong-age diseases with absolute claims).
- If evidence is weak/low-quality, you may recommend expert review instead of failing with made-up logic.

[MODALITIES]
has_audio={bool(modalities.get("has_audio", False))}, has_image={bool(modalities.get("has_image", False))}
audio_quality_score={quality.get("audio_quality_score", 0.0)}, image_quality_score={quality.get("image_quality_score", 0.0)}

[PATIENT DATA]
Age: {patient["age"]}, Sex: {patient["sex"]}, Complaint: {patient["chief"]}

[JUNIOR DOCTOR'S DIAGNOSIS]
{json.dumps(initial_diagnosis)}

CRITICAL TASK:
1. Check for logical contradictions (true impossibilities).
2. Check for absolute statements ("definitely", "guaranteed").
3. Assign a Risk Score.

Return ONLY this JSON:
{{
  "audit_status": "Pass" or "Fail",
  "audit_risk_score": "Low" | "Medium" | "High",
  "critique": ["Point out specific errors or confirm logic"],
  "safety_warning": "Any immediate safety concern?"
}}
""".strip()


def build_reverse_prompt(patient: Dict[str, Any], initial_diagnosis: Dict[str, Any]) -> str:
    primary_dx = initial_diagnosis.get("primary_diagnosis", "Unknown Condition")
    return f"""
You are a Critical Diagnostic Expert.
Current working diagnosis is: "{primary_dx}".

TASK: Challenge this diagnosis.
1. Assume the primary diagnosis is WRONG.
2. What are the most dangerous/life-threatening alternatives (Differential Diagnosis)?
3. List specific "Rule-out" actions (Labs/Imaging) to exclude these killers.

Return ONLY this JSON:
{{
  "alternative_diagnoses": ["Disease A", "Disease B"],
  "rule_out_logic": [
    {{
      "suspect": "Disease A",
      "why_dangerous": "...",
      "action_to_exclude": "Check D-dimer / ECG..."
    }},
    {{
      "suspect": "Disease B",
      "why_dangerous": "...",
      "action_to_exclude": "..."
    }}
  ]
}}
""".strip()
