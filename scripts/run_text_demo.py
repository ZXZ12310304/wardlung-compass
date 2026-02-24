from __future__ import annotations

import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.agents.observer import MedGemmaClient, MedSigLIPAnalyzer
from src.agents.asr import MedASRTranscriber
from src.agents.orchestrator import AnalysisOrchestrator
from src.tools.rag_engine import RAGEngine


def main() -> None:
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

    patient = {
        "age": 65,
        "sex": "Male",
        "chief": "Cough and fever for 3 days, shortness of breath.",
        "history": "COPD. No recent antibiotics.",
        "intern_plan": "",
    }

    result = orchestrator.run(
        view_mode="Doctor View",
        patient=patient,
        image=None,
        audio_path=None,
        patient_id="demo_patient_001",
        context_snapshot={"note": "demo only"},
    )

    print("assessment_id:", result.get("assessment_id"))
    print("tool_trace:", json.dumps(result.get("tool_trace", []), ensure_ascii=False, indent=2))
    print("gaps:", json.dumps(result.get("gaps", []), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
