# src/agents/orchestrator.py
import re
from typing import Any, Dict, Optional, List
from PIL import Image

from src.agents.observer import MedGemmaClient, MedSigLIPAnalyzer
from src.agents.asr import MedASRTranscriber
from src.tools.rag_engine import RAGEngine
from src.utils.prompts import build_audit_prompt, build_diagnosis_prompt, build_reverse_prompt


class AnalysisOrchestrator:
    def __init__(
        self,
        medgemma: MedGemmaClient,
        image_analyzer: Optional[MedSigLIPAnalyzer] = None,
        rag_engine: Optional[RAGEngine] = None,
        asr_transcriber: Optional[MedASRTranscriber] = None,
    ) -> None:
        self.medgemma = medgemma
        self.image_analyzer = image_analyzer
        self.rag_engine = rag_engine
        self.asr_transcriber = asr_transcriber

    # ---------------------------
    # Quality / gating helpers
    # ---------------------------
    def _assess_audio_quality(self, transcript: str) -> Dict[str, Any]:
        """
        0~1：越高越可信
        用非常轻量的规则判断（不引入新依赖）
        """
        t = (transcript or "").strip()
        issues: List[str] = []
        if not t:
            return {"audio_quality_score": 0.0, "audio_issues": ["empty_transcript"]}

        eps_count = t.count("<epsilon>") + t.lower().count("epsilon")
        token_count = max(1, len(t.split()))
        eps_ratio = eps_count / float(token_count)

        if eps_ratio > 0.2:
            issues.append("epsilon_noise_high")

        words = re.findall(r"[A-Za-z']+", t.lower())
        if len(words) >= 8:
            uniq_ratio = len(set(words)) / float(len(words))
            if uniq_ratio < 0.45:
                issues.append("repetition_high")
        else:
            issues.append("very_short_transcript")

        score = 1.0
        if "very_short_transcript" in issues:
            score -= 0.35
        if "epsilon_noise_high" in issues:
            score -= 0.45
        if "repetition_high" in issues:
            score -= 0.35

        score = max(0.0, min(1.0, score))
        return {"audio_quality_score": round(score, 3), "audio_issues": issues}

    def _assess_image_quality(self, img_findings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        0~1：越高越可信
        - interpretable False / LABEL_* 直接降权
        - evidence_strength low 再降权
        """
        if not img_findings:
            return {"image_quality_score": 0.0, "image_issues": ["no_image_findings"]}

        issues = list(img_findings.get("issues", []) or [])
        interpretable = bool(img_findings.get("interpretable", False))
        conf = float(img_findings.get("confidence", 0.0) or 0.0)
        strength = str(img_findings.get("evidence_strength", "low") or "low").lower()

        score = 0.2             
        if interpretable:
            score = 0.4 + 0.6 * conf                        
        else:
            issues.append("image_not_interpretable")

        if strength == "low":
            score -= 0.15
        elif strength == "medium":
            score -= 0.05

        score = max(0.0, min(1.0, score))
        return {"image_quality_score": round(score, 3), "image_issues": issues}

    def _pick_primary_basis(self, has_audio: bool, has_image: bool, audio_q: float, image_q: float, rag_used: bool) -> str:
        """
        你希望 UI 能标注“主要依据”
        这里给一个稳定的启发式：
        - 两个都高：mixed
        - 谁高信谁
        - 都低：clinical 或 rag（若 rag 有）
        """
        if has_audio and has_image:
            if audio_q >= 0.6 and image_q >= 0.6:
                return "mixed"
            return "audio" if audio_q >= image_q else "image"

        if has_audio:
            return "audio" if audio_q >= 0.35 else ("rag" if rag_used else "clinical")
        if has_image:
            return "image" if image_q >= 0.35 else ("rag" if rag_used else "clinical")
        return "rag" if rag_used else "clinical"

    def _route_tag(self, has_audio: bool, has_image: bool) -> str:
        if has_audio and has_image:
            return "audio_image"
        if has_audio:
            return "audio_only"
        if has_image:
            return "image_only"
        return "none"

    # ---------------------------
    # Main run
    # ---------------------------
    def run(
        self,
        view_mode: str,
        patient: Dict[str, Any],
        image: Optional[Image.Image] = None,
        audio_path: Optional[str] = None,
        progress: Optional[Any] = None,
    ) -> Dict[str, Any]:
        patient = dict(patient)

        # ===== Modalities =====
        has_audio = bool(audio_path)
        has_image = image is not None
        route_tag = self._route_tag(has_audio, has_image)

        patient["modalities"] = {
            "has_audio": has_audio,
            "has_image": has_image,
            "route_tag": route_tag,
        }

                                        
        if has_audio and self.asr_transcriber is not None:
            self._notify(progress, 0.05, "Audio: Transcribing...")
            try:
                patient["audio_transcript"] = self.asr_transcriber.transcribe(audio_path)
            except Exception as exc:
                patient["audio_transcript"] = f"[ASR error] {exc}"
        else:
            patient.setdefault("audio_transcript", "")

        audio_quality = self._assess_audio_quality(patient.get("audio_transcript", ""))
        patient["quality"] = dict(audio_quality)

        # ===== Vision =====
        img_findings = None
        if has_image and self.image_analyzer is not None:
            self._notify(progress, 0.1, "Vision: Analyzing scan...")
            try:
                img_findings = self.image_analyzer.analyze(image)
            except Exception as exc:
                img_findings = {
                    "model": "MedSigLIP",
                    "mode": "failed",
                    "primary_finding": "Unknown",
                    "confidence": 0.0,
                    "top_candidates": [],
                    "interpretable": False,
                    "suggests_pneumonia": False,
                    "evidence_strength": "low",
                    "issues": [f"vision_failed: {exc}"],
                }

        image_quality = self._assess_image_quality(img_findings)
        patient["quality"].update(image_quality)

        # ===== RAG evidence =====
        self._notify(progress, 0.25, "RAG: Retrieving evidence...")
        evidence_text = self._build_rag_context(patient)
        rag_used = bool((evidence_text or "").strip())

        # ===== primary basis hint (for prompt + UI) =====
        audio_q = float(patient["quality"].get("audio_quality_score", 0.0) or 0.0)
        image_q = float(patient["quality"].get("image_quality_score", 0.0) or 0.0)
        basis = self._pick_primary_basis(has_audio, has_image, audio_q, image_q, rag_used)
        patient["primary_basis_hint"] = basis

        # ===== Fusion summary =====
        patient["multimodal_summary"] = self._build_fusion_summary(
            audio_transcript=patient.get("audio_transcript", ""),
            img_findings=img_findings,
            has_audio=has_audio,
            has_image=has_image,
            audio_quality=audio_quality,
            image_quality=image_quality,
            rag_used=rag_used,
            basis=basis,
        )

        # ===== Diagnosis =====
        self._notify(progress, 0.35, "Cognitive: Generating initial diagnosis...")
        prompt_1 = build_diagnosis_prompt(
            view_mode=view_mode,
            patient=patient,
            img_findings=img_findings,
            evidence_text=evidence_text,
        )

        r_initial = self.medgemma.run(prompt_1, image=image if has_image else None)

        meta = {
            "route_tag": route_tag,
            "has_audio": has_audio,
            "has_image": has_image,
            "audio_quality_score": audio_q,
            "audio_issues": patient["quality"].get("audio_issues", []),
            "image_quality_score": image_q,
            "image_issues": patient["quality"].get("image_issues", []),
            "rag_used": rag_used,
            "primary_basis": basis,
        }

        if view_mode == "Patient View":
            return {
                "mode": "patient",
                "meta": meta,
                "diagnosis": r_initial,
                "image_findings": img_findings,
                "audio_transcript": patient.get("audio_transcript", ""),
                "multimodal_summary": patient.get("multimodal_summary", ""),
            }

        # ===== Audit =====
        self._notify(progress, 0.55, "Meta-cognition: Auditing response...")
        prompt_2 = build_audit_prompt(patient, r_initial)
        r_audit = self.medgemma.run(prompt_2, image=None)

        # ===== Differential =====
        self._notify(progress, 0.75, "Routing: Running differential diagnosis...")
        prompt_3 = build_reverse_prompt(patient, r_initial)
        r_reverse = self.medgemma.run(prompt_3, image=None)

        self._notify(progress, 0.9, "Rendering report...")
        return {
            "mode": "doctor",
            "meta": meta,
            "diagnosis": r_initial,
            "audit": r_audit,
            "reverse": r_reverse,
            "image_findings": img_findings,
            "audio_transcript": patient.get("audio_transcript", ""),
            "multimodal_summary": patient.get("multimodal_summary", ""),
        }

    # ---------------------------
    # Utilities
    # ---------------------------
    def _notify(self, progress: Optional[Any], value: float, desc: str) -> None:
        if progress is None:
            return
        try:
            progress(value, desc=desc)
        except Exception:
            pass

    def _build_rag_context(self, patient: Dict[str, Any]) -> str:
        if self.rag_engine is None:
            return ""
        query_text = self._compose_query(patient)
        if not query_text:
            return ""
        try:
            evidence = self.rag_engine.query(query_text, top_k=6)
        except Exception:
            return ""
        if not evidence:
            return ""
        lines = []
        for item in evidence:
            source = item.get("source_file") or item.get("source_path") or "source"
            text = item.get("text", "").replace("\n", " ").strip()
            if text:
                lines.append(f"- ({source}) {text}")
        return "\n".join(lines)

    def _compose_query(self, patient: Dict[str, Any]) -> str:
        parts = [
            patient.get("chief", ""),
            patient.get("history", ""),
            patient.get("intern_plan", "") or "",
            patient.get("audio_transcript", "") or "",
            patient.get("multimodal_summary", "") or "",
        ]
        return " ".join(part for part in parts if part).strip()

    def _build_fusion_summary(
        self,
        audio_transcript: str,
        img_findings: Optional[Dict[str, Any]],
        has_audio: bool,
        has_image: bool,
        audio_quality: Dict[str, Any],
        image_quality: Dict[str, Any],
        rag_used: bool,
        basis: str,
    ) -> str:
        """
        不做医学结论，只做：
        - 你到底给了哪些模态
        - 每个模态质量怎么样
        - 图像输出是否可解释
        - 是否存在明显冲突（非常轻量）
        """
        lines: List[str] = []
        lines.append(f"- route_tag: {('audio_image' if (has_audio and has_image) else 'audio_only' if has_audio else 'image_only' if has_image else 'none')}")
        lines.append(f"- primary_basis_hint: {basis}")
        lines.append(f"- rag_used: {rag_used}")

        if has_audio:
            t = (audio_transcript or "").strip()
            lines.append(f"- audio_transcript_len: {len(t)}")
            lines.append(f"- audio_quality_score: {audio_quality.get('audio_quality_score', 0.0)}")
            if audio_quality.get("audio_issues"):
                lines.append(f"- audio_issues: {audio_quality.get('audio_issues')}")

        if has_image:
            if img_findings:
                lines.append(f"- vision_primary: {img_findings.get('primary_finding', 'Unknown')}")
                lines.append(f"- vision_confidence: {img_findings.get('confidence', 'N/A')}")
                lines.append(f"- vision_interpretable: {img_findings.get('interpretable', False)}")
                lines.append(f"- vision_strength: {img_findings.get('evidence_strength', 'low')}")
                lines.append(f"- image_quality_score: {image_quality.get('image_quality_score', 0.0)}")
                if image_quality.get("image_issues"):
                    lines.append(f"- image_issues: {image_quality.get('image_issues')}")
            else:
                lines.append("- image provided but vision analyzer returned no findings.")

        conflict_flags = []
        at = (audio_transcript or "").lower()
        if "pneumonia" in at and img_findings:
            top = str(img_findings.get("primary_finding", "")).lower()
            if "normal" in top or ("no pneumothorax" in top and img_findings.get("suggests_pneumonia") is False):
                conflict_flags.append("audio_mentions_pneumonia_but_vision_top_not_pneumonia")
        if conflict_flags:
            lines.append(f"- potential_conflicts: {conflict_flags}")

        return "FUSED INPUT SUMMARY:\n" + "\n".join(lines)
