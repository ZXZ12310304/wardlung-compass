# src/agents/orchestrator.py
import re
import os
import time
import uuid
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
        patient_id: Optional[str] = None,
        context_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        patient = dict(patient)
        assessment_id = uuid.uuid4().hex
        tool_trace: List[Dict[str, Any]] = []
        gaps: List[Dict[str, Any]] = []
        gap_ids: set[str] = set()
        error_summary: List[str] = []
        vital_gap_ids = {"missing_spo2", "missing_temp", "missing_rr", "missing_hr"}

        def _trace_step(
            step: str,
            start_time: float,
            success: bool,
            status: str,
            summary: str,
            error: Optional[str] = None,
            artifacts: Optional[Dict[str, Any]] = None,
        ) -> None:
            latency_ms = int((time.monotonic() - start_time) * 1000)
            record = {
                "step": step,
                "success": bool(success),
                "status": status,
                "latency_ms": latency_ms,
                "summary": summary,
                "error": error,
                "artifacts": artifacts or {},
            }
            tool_trace.append(record)
            if error:
                error_summary.append(f"{step}: {error}")

        def _add_gap(
            gap_id: str,
            severity: str,
            message: str,
            suggested_fields: Optional[List[str]] = None,
        ) -> None:
            if gap_id in gap_ids:
                return
            if view_mode == "Patient View" and gap_id in vital_gap_ids:
                severity = "low"
                message = f"{message}（患者端可请护士/医生测量）"
            gaps.append(
                {
                    "id": gap_id,
                    "severity": severity,
                    "message": message,
                    "suggested_fields": suggested_fields or [],
                }
            )
            gap_ids.add(gap_id)

        # ===== Modalities =====
        has_audio = bool(audio_path)
        has_image = image is not None
        route_tag = self._route_tag(has_audio, has_image)

        patient["modalities"] = {
            "has_audio": has_audio,
            "has_image": has_image,
            "route_tag": route_tag,
        }

        # ===== Audio -> Transcript =====
        asr_start = time.monotonic()
        if has_audio and self.asr_transcriber is not None:
            self._notify(progress, 0.05, "Audio: Transcribing...")
            try:
                patient["audio_transcript"] = self.asr_transcriber.transcribe(audio_path)
                _trace_step(
                    "asr",
                    asr_start,
                    True,
                    "ok",
                    f"ASR ok, transcript_len={len(patient.get('audio_transcript', ''))}",
                )
            except Exception as exc:
                err = str(exc)
                patient["audio_transcript"] = f"[ASR error] {err}"
                _trace_step("asr", asr_start, False, "failed", "ASR failed", error=err)
                _add_gap(
                    "asr_failed",
                    "high",
                    "音频转写失败，请改用文字或重新录音。",
                    ["audio"],
                )
        elif has_audio and self.asr_transcriber is None:
            patient.setdefault("audio_transcript", "")
            _trace_step(
                "asr",
                asr_start,
                False,
                "failed",
                "ASR unavailable",
                error="asr_transcriber_missing",
            )
            _add_gap(
                "asr_unavailable",
                "medium",
                "音频转写组件不可用，请改用文字输入。",
                ["audio"],
            )
        else:
            patient.setdefault("audio_transcript", "")
            _trace_step("asr", asr_start, True, "skipped", "ASR skipped (no audio)")

        audio_quality = self._assess_audio_quality(patient.get("audio_transcript", ""))
        patient["quality"] = dict(audio_quality)

        # ===== Vision =====
        img_findings = None
        vision_start = time.monotonic()
        if has_image and self.image_analyzer is not None:
            self._notify(progress, 0.1, "Vision: Analyzing scan...")
            try:
                img_findings = self.image_analyzer.analyze(image)
                _trace_step(
                    "vision",
                    vision_start,
                    True,
                    "ok",
                    f"Vision ok, primary={img_findings.get('primary_finding', 'Unknown')}",
                    artifacts={
                        "model": img_findings.get("model"),
                        "mode": img_findings.get("mode"),
                        "confidence": img_findings.get("confidence"),
                        "interpretable": img_findings.get("interpretable"),
                    },
                )
            except Exception as exc:
                err = str(exc)
                img_findings = {
                    "model": "MedSigLIP",
                    "mode": "failed",
                    "primary_finding": "Unknown",
                    "confidence": 0.0,
                    "top_candidates": [],
                    "interpretable": False,
                    "suggests_pneumonia": False,
                    "evidence_strength": "low",
                    "issues": [f"vision_failed: {err}"],
                }
                _trace_step("vision", vision_start, False, "failed", "Vision failed", error=err)
                _add_gap(
                    "vision_failed",
                    "medium",
                    "影像分析失败，请重新上传清晰图像。",
                    ["image"],
                )
        elif has_image and self.image_analyzer is None:
            _trace_step(
                "vision",
                vision_start,
                False,
                "failed",
                "Vision unavailable",
                error="image_analyzer_missing",
            )
            _add_gap(
                "vision_unavailable",
                "medium",
                "影像分析组件不可用。",
                ["image"],
            )
        else:
            _trace_step("vision", vision_start, True, "skipped", "Vision skipped (no image)")

        image_quality = self._assess_image_quality(img_findings)
        patient["quality"].update(image_quality)

        # ===== RAG evidence =====
        self._notify(progress, 0.25, "RAG: Retrieving evidence...")
        rag_start = time.monotonic()
        evidence_text, rag_evidence, rag_error = self._build_rag_context(
            patient, return_evidence=True
        )
        rag_used = bool((evidence_text or "").strip())
        if rag_used:
            _trace_step(
                "rag",
                rag_start,
                True,
                "ok",
                f"RAG ok, evidence={len(rag_evidence)}",
                artifacts={"top_k": len(rag_evidence)},
            )
        else:
            err = rag_error or "no_relevant_evidence"
            _trace_step("rag", rag_start, False, "failed", "RAG unavailable", error=err)
            _add_gap(
                "rag_unavailable",
                "low",
                "知识库未加载或无相关文档。",
                ["knowledge_base"],
            )

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

        # ===== Gaps rules =====
        combined_text = " ".join(
            [
                str(patient.get("chief", "") or ""),
                str(patient.get("history", "") or ""),
                str(patient.get("intern_plan", "") or ""),
                str(patient.get("audio_transcript", "") or ""),
            ]
        ).lower()

        if len((patient.get("chief") or "").strip()) < 10:
            _add_gap(
                "chief_too_short",
                "medium",
                "主诉过短，建议补充起病时间、咳嗽/痰色、胸痛、气促等。",
                ["chief"],
            )

        history_text = (patient.get("history") or "").lower()
        if not any(
            k in history_text
            for k in [
                "copd",
                "asthma",
                "肺",
                "immun",
                "transplant",
                "steroid",
                "chemo",
                "antibiotic",
                "抗生素",
                "免疫",
            ]
        ):
            _add_gap(
                "history_missing_key",
                "low",
                "病史缺少既往肺病/免疫抑制/近期抗生素等关键信息。",
                ["history"],
            )

        def _has_pattern(patterns: List[str]) -> bool:
            return any(re.search(p, combined_text) for p in patterns)

        if not _has_pattern([r"\bspo2\b", r"o2\s*sat", r"血氧", r"氧饱和"]):
            _add_gap("missing_spo2", "high", "缺少血氧（SpO₂），建议补充或测量。", ["spo2"])
        if not _has_pattern([r"\btemp\b", r"temperature", r"体温", r"℃", r"°c"]):
            _add_gap("missing_temp", "high", "缺少体温信息，建议补充或测量。", ["temperature"])
        if not _has_pattern([r"\brr\b", r"respiratory rate", r"呼吸频率", r"呼吸率"]):
            _add_gap("missing_rr", "medium", "缺少呼吸频率，建议补充。", ["resp_rate"])
        if not _has_pattern([r"\bhr\b", r"heart rate", r"心率", r"脉搏"]):
            _add_gap("missing_hr", "medium", "缺少心率，建议补充。", ["heart_rate"])

        if has_audio and float(audio_quality.get("audio_quality_score", 0.0)) < 0.35:
            _add_gap(
                "audio_quality_low",
                "medium",
                "音频质量较差，建议重录或改用文字输入。",
                ["audio"],
            )
        if has_image and float(image_quality.get("image_quality_score", 0.0)) < 0.35:
            _add_gap(
                "image_quality_low",
                "medium",
                "图像质量较差，建议重新拍摄避免遮挡或模糊。",
                ["image"],
            )

        # ===== Diagnosis =====
        self._notify(progress, 0.35, "Cognitive: Generating initial diagnosis...")
        prompt_1 = build_diagnosis_prompt(
            view_mode=view_mode,
            patient=patient,
            img_findings=img_findings,
            evidence_text=evidence_text,
        )

        diag_start = time.monotonic()
        diag_error_text = ""
        try:
            r_initial = self.medgemma.run(prompt_1, image=image if has_image else None, max_new_tokens=384)
            success = "error" not in r_initial
            if not success:
                diag_error_text = str(r_initial.get("error") or "")
            _trace_step(
                "medgemma_diagnosis",
                diag_start,
                success,
                "ok" if success else "failed",
                "MedGemma diagnosis ok" if success else "MedGemma diagnosis error",
                error=str(r_initial.get("error")) if not success else None,
                artifacts={"model_id": getattr(self.medgemma, "model_id", None)},
            )
            if not success:
                _add_gap(
                    "diagnosis_failed",
                    "high",
                    "诊断生成失败，请补充信息或稍后重试。",
                    ["chief", "history"],
                )
                if "out of memory" in diag_error_text.lower():
                    _add_gap(
                        "gpu_oom",
                        "high",
                        "GPU memory is insufficient for current assessment context. Try shorter note or retry.",
                        ["history", "image", "audio"],
                    )
        except Exception as exc:
            err = str(exc)
            diag_error_text = err
            r_initial = {"error": err, "gentle_summary": "Error in processing."}
            _trace_step(
                "medgemma_diagnosis",
                diag_start,
                False,
                "failed",
                "MedGemma diagnosis failed",
                error=err,
            )
            _add_gap(
                "diagnosis_failed",
                "high",
                "诊断生成失败，请补充信息或稍后重试。",
                ["chief", "history"],
            )
            if "out of memory" in err.lower():
                _add_gap(
                    "gpu_oom",
                    "high",
                    "GPU memory is insufficient for current assessment context. Try shorter note or retry.",
                    ["history", "image", "audio"],
                )

        # ===== Meta (UI) =====
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

        context_snapshot_used = {
            "provided": context_snapshot is not None,
            "keys": list((context_snapshot or {}).keys())[:10],
            "size": len(context_snapshot or {}),
        }

        result_struct = {
            "assessment_id": assessment_id,
            "patient_id": patient_id,
            "context_snapshot_used": context_snapshot_used,
            "route_tag": route_tag,
            "primary_basis": basis,
            "rag_evidence": rag_evidence,
            "tool_trace": tool_trace,
            "gaps": gaps,
            "diagnosis_json": r_initial,
        }

        if view_mode == "Patient View":
            return {
                "mode": "patient",
                "meta": meta,
                "diagnosis": r_initial,
                "image_findings": img_findings,
                "audio_transcript": patient.get("audio_transcript", ""),
                "multimodal_summary": patient.get("multimodal_summary", ""),
                "route_tag": route_tag,
                "primary_basis": basis,
                "input_quality": {
                    "audio": audio_quality,
                    "image": image_quality,
                },
                "rag_evidence": rag_evidence,
                "transcript": patient.get("audio_transcript", ""),
                "asr_quality": audio_quality,
                "assessment_id": assessment_id,
                "patient_id": patient_id,
                "context_snapshot_used": context_snapshot_used,
                "tool_trace": tool_trace,
                "gaps": gaps,
                "error_summary": error_summary,
                "result_struct": result_struct,
            }

        # ===== Audit =====
        skip_secondary = "error" in (r_initial or {})
        if skip_secondary:
            r_audit = {"error": "skipped_due_to_diagnosis_failure"}
            _trace_step(
                "medgemma_audit",
                time.monotonic(),
                True,
                "skipped",
                "Audit skipped due to diagnosis failure",
            )
        else:
            self._notify(progress, 0.55, "Meta-cognition: Auditing response...")
            prompt_2 = build_audit_prompt(patient, r_initial)
            audit_start = time.monotonic()
            try:
                r_audit = self.medgemma.run(prompt_2, image=None, max_new_tokens=256)
                success = "error" not in r_audit
                _trace_step(
                    "medgemma_audit",
                    audit_start,
                    success,
                    "ok" if success else "failed",
                    "MedGemma audit ok" if success else "MedGemma audit error",
                    error=str(r_audit.get("error")) if not success else None,
                )
                if not success:
                    _add_gap(
                        "audit_failed",
                        "low",
                        "审计生成失败，结果可能缺少安全复核。",
                        [],
                    )
            except Exception as exc:
                err = str(exc)
                r_audit = {"error": err}
                _trace_step(
                    "medgemma_audit",
                    audit_start,
                    False,
                    "failed",
                    "MedGemma audit failed",
                    error=err,
                )
                _add_gap(
                    "audit_failed",
                    "low",
                    "审计生成失败，结果可能缺少安全复核。",
                    [],
                )

        # ===== Differential =====
        if skip_secondary:
            r_reverse = {"error": "skipped_due_to_diagnosis_failure"}
            _trace_step(
                "medgemma_reverse",
                time.monotonic(),
                True,
                "skipped",
                "Reverse diagnosis skipped due to diagnosis failure",
            )
        else:
            self._notify(progress, 0.75, "Routing: Running differential diagnosis...")
            prompt_3 = build_reverse_prompt(patient, r_initial)
            rev_start = time.monotonic()
            try:
                r_reverse = self.medgemma.run(prompt_3, image=None, max_new_tokens=256)
                success = "error" not in r_reverse
                _trace_step(
                    "medgemma_reverse",
                    rev_start,
                    success,
                    "ok" if success else "failed",
                    "MedGemma reverse ok" if success else "MedGemma reverse error",
                    error=str(r_reverse.get("error")) if not success else None,
                )
                if not success:
                    _add_gap(
                        "reverse_failed",
                        "low",
                        "鉴别诊断生成失败。",
                        [],
                    )
            except Exception as exc:
                err = str(exc)
                r_reverse = {"error": err}
                _trace_step(
                    "medgemma_reverse",
                    rev_start,
                    False,
                    "failed",
                    "MedGemma reverse failed",
                    error=err,
                )
                _add_gap(
                    "reverse_failed",
                    "low",
                    "鉴别诊断生成失败。",
                    [],
                )

        self._notify(progress, 0.9, "Rendering report...")
        result_struct.update(
            {
                "audit_json": r_audit,
                "reverse_json": r_reverse,
            }
        )
        return {
            "mode": "doctor",
            "meta": meta,
            "diagnosis": r_initial,
            "audit": r_audit,
            "reverse": r_reverse,
            "image_findings": img_findings,
            "audio_transcript": patient.get("audio_transcript", ""),
            "multimodal_summary": patient.get("multimodal_summary", ""),
            "route_tag": route_tag,
            "primary_basis": basis,
            "input_quality": {
                "audio": audio_quality,
                "image": image_quality,
            },
            "rag_evidence": rag_evidence,
            "transcript": patient.get("audio_transcript", ""),
            "asr_quality": audio_quality,
            "assessment_id": assessment_id,
            "patient_id": patient_id,
            "context_snapshot_used": context_snapshot_used,
            "tool_trace": tool_trace,
            "gaps": gaps,
            "error_summary": error_summary,
            "result_struct": result_struct,
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
            return

    def _build_rag_context(
        self, patient: Dict[str, Any], return_evidence: bool = False
    ):
        if self.rag_engine is None:
            return ("", [], "rag_engine_missing") if return_evidence else ""
        query_text = self._compose_query(patient)
        if not query_text:
            return ("", [], "empty_query") if return_evidence else ""
        try:
            evidence = self.rag_engine.query(query_text, top_k=6)
        except Exception as exc:
            return ("", [], f"rag_query_failed: {exc}") if return_evidence else ""
        if not evidence:
            return ("", [], "no_evidence") if return_evidence else ""
        per_item_limit = max(160, min(1200, int(os.getenv("RAG_EVIDENCE_ITEM_CHARS", "500"))))
        total_limit = max(800, min(6000, int(os.getenv("RAG_EVIDENCE_TOTAL_CHARS", "2200"))))
        lines = []
        rag_evidence = []
        total_chars = 0
        for item in evidence:
            source_file = item.get("source_file") or ""
            source_path = item.get("source_path") or ""
            score = item.get("score")
            text_full = item.get("text", "").replace("\n", " ").strip()
            if len(text_full) > per_item_limit:
                text_full = text_full[: per_item_limit - 3].rstrip() + "..."
            snippet = text_full[:300] + "..." if len(text_full) > 300 else text_full
            category = item.get("category") or ""
            rag_evidence.append(
                {
                    "source_file": source_file,
                    "source_path": source_path,
                    "score": score,
                    "text": snippet,
                    "category": category,
                }
            )
            source = source_file or source_path or "source"
            if text_full:
                line = f"- ({source}) {text_full}"
                if total_chars + len(line) > total_limit:
                    break
                lines.append(line)
                total_chars += len(line)
        context = "\n".join(lines)
        if return_evidence:
            return context, rag_evidence, None
        return context

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
        lines: List[str] = []
        lines.append(
            f"- route_tag: {('audio_image' if (has_audio and has_image) else 'audio_only' if has_audio else 'image_only' if has_image else 'none')}"
        )
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
            if "normal" in top or (
                "no pneumothorax" in top and img_findings.get("suggests_pneumonia") is False
            ):
                conflict_flags.append("audio_mentions_pneumonia_but_vision_top_not_pneumonia")
        if conflict_flags:
            lines.append(f"- potential_conflicts: {conflict_flags}")

        return "FUSED INPUT SUMMARY:\n" + "\n".join(lines)


if __name__ == "__main__":
    import json
    import os

    from src.agents.observer import MedGemmaClient, MedSigLIPAnalyzer
    from src.agents.asr import MedASRTranscriber
    from src.tools.rag_engine import RAGEngine

    demo_patient = {
        "age": 70,
        "sex": "Male",
        "chief": "cough",
        "history": "",
        "intern_plan": "",
    }

    medgemma = MedGemmaClient()
    image_analyzer = MedSigLIPAnalyzer()
    asr_transcriber = MedASRTranscriber()
    rag_engine = None if os.getenv("DEMO_RAG_FAIL", "1") == "1" else RAGEngine()

    orch = AnalysisOrchestrator(
        medgemma,
        image_analyzer,
        rag_engine=rag_engine,
        asr_transcriber=asr_transcriber,
    )

    out = orch.run(
        view_mode="Doctor View",
        patient=demo_patient,
        image=None,
        audio_path=None,
        patient_id="demo_fail_case",
        context_snapshot=None,
    )

    print("assessment_id:", out.get("assessment_id"))
    print("tool_trace:", json.dumps(out.get("tool_trace", []), ensure_ascii=False, indent=2))
    print("gaps:", json.dumps(out.get("gaps", []), ensure_ascii=False, indent=2))
