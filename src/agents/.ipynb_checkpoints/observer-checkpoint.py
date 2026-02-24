# src/agents/observer.py
import os
import re
from typing import Any, Dict, Optional, List, Tuple

import torch
from PIL import Image
from transformers import (
    AutoModelForImageClassification,
    AutoModelForImageTextToText,
    AutoProcessor,
)

from src.utils.json_utils import safe_json_loads
from src.utils.prompts import SYSTEM_PROMPT

FORCE_CUDA_ENV = "FORCE_CUDA"
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_HF_CACHE_DIR = os.path.join(_REPO_ROOT, "models")


def _force_cuda_enabled() -> bool:
    return os.getenv(FORCE_CUDA_ENV, "").strip().lower() in ("1", "true", "yes", "y")


def _hf_token() -> Optional[str]:
    return os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN")


def _from_pretrained_compat(fn, *, token: Optional[str], **kwargs):
   
    try:
        if token:
            return fn(**kwargs, token=token)
        return fn(**kwargs)
    except TypeError:
        if token:
            return fn(**kwargs, use_auth_token=token)
        return fn(**kwargs)


def _is_label_interpretable(label: str) -> bool:
    if not label:
        return False
    
    if re.match(r"^LABEL_\d+$", label.strip()):
        return False
    return True


def _evidence_strength(interpretable: bool, confidence: float) -> str:
    if not interpretable:
        return "low"
    if confidence >= 0.70:
        return "high"
    if confidence >= 0.40:
        return "medium"
    return "low"


class MedGemmaClient:
    def __init__(self, model_id: str = "google/medgemma-1.5-4b-it") -> None:
        self.model_id = model_id
        token = _hf_token()

        force_cuda = _force_cuda_enabled()
        if force_cuda and not torch.cuda.is_available():
            raise RuntimeError(
                "FORCE_CUDA is set but CUDA is not available. Install a CUDA-enabled "
                "PyTorch build or unset FORCE_CUDA."
            )

        if force_cuda:
            torch_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            device_map = "cuda"
        else:
            torch_dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
            device_map = "auto"

        self.processor = _from_pretrained_compat(
            AutoProcessor.from_pretrained,
            token=token,
            pretrained_model_name_or_path=model_id,
            cache_dir=DEFAULT_HF_CACHE_DIR,
        )
        self.model = _from_pretrained_compat(
            AutoModelForImageTextToText.from_pretrained,
            token=token,
            pretrained_model_name_or_path=model_id,
            torch_dtype=torch_dtype,
            device_map=device_map,
            cache_dir=DEFAULT_HF_CACHE_DIR,
        )
        self.model.eval()
        device = self.model.device if hasattr(self.model, "device") else "unknown"
        print(f"[MedGemma] Loaded on device: {device} (force_cuda={force_cuda})")

    def run(
        self, prompt: str, image: Optional[Image.Image] = None, max_new_tokens: int = 1024
    ) -> Dict[str, Any]:
        try:
            dtype = torch.bfloat16 if self.model.device.type == "cuda" else torch.float32

            content = [{"type": "text", "text": prompt}]
            if image is not None:
                content.append({"type": "image", "image": image})

            messages = [
                {"role": "system", "content": [{"type": "text", "text": SYSTEM_PROMPT}]},
                {"role": "user", "content": content},
            ]

            inputs = self.processor.apply_chat_template(
                messages,
                add_generation_prompt=True,
                tokenize=True,
                return_dict=True,
                return_tensors="pt",
            ).to(self.model.device, dtype=dtype)

            with torch.inference_mode():
                output = self.model.generate(**inputs, max_new_tokens=max_new_tokens)

            generated_text = self.processor.decode(
                output[0][inputs["input_ids"].shape[-1] :], skip_special_tokens=True
            )
            return safe_json_loads(generated_text)

        except Exception as exc:
            print(f"[MedGemma] Inference error: {exc}")
            return {"error": str(exc), "gentle_summary": "Error in processing."}


class MedSigLIPAnalyzer:
    

    
    DEFAULT_CANDIDATE_LABELS: List[str] = [
        "normal chest x-ray",
        "no pneumothorax",
        "pneumonia",
        "atypical pneumonia",
        "aspiration pneumonia",
        "right lower lobe consolidation",
        "left lower lobe consolidation",
        "interstitial opacities",
        "pleural effusion",
    ]

    def __init__(self, model_id: str = "google/medsiglip-448") -> None:
        token = _hf_token()
        force_cuda = _force_cuda_enabled()
        if force_cuda and not torch.cuda.is_available():
            raise RuntimeError(
                "FORCE_CUDA is set but CUDA is not available. Install a CUDA-enabled "
                "PyTorch build or unset FORCE_CUDA."
            )
        self.device = "cuda" if force_cuda or torch.cuda.is_available() else "cpu"
        self.model_id = model_id

       
        self.processor = _from_pretrained_compat(
            AutoProcessor.from_pretrained,
            token=token,
            pretrained_model_name_or_path=model_id,
            cache_dir=DEFAULT_HF_CACHE_DIR,
        )

      
        self.zero_shot = False
        self.zs_cls = None
        try:
            from transformers import AutoModelForZeroShotImageClassification  # type: ignore

            self.zs_cls = AutoModelForZeroShotImageClassification
            self.zero_shot = True
        except Exception:
            self.zero_shot = False

        if self.zero_shot and self.zs_cls is not None:
            self.model = _from_pretrained_compat(
                self.zs_cls.from_pretrained,
                token=token,
                pretrained_model_name_or_path=model_id,
                cache_dir=DEFAULT_HF_CACHE_DIR,
            ).to(self.device)
        else:
            # fallback
            self.model = _from_pretrained_compat(
                AutoModelForImageClassification.from_pretrained,
                token=token,
                pretrained_model_name_or_path=model_id,
                cache_dir=DEFAULT_HF_CACHE_DIR,
            ).to(self.device)

        self.model.eval()
        print(f"[MedSigLIP] Loaded on device: {self.device} zero_shot={self.zero_shot} (force_cuda={force_cuda})")

    def analyze(
        self,
        image: Image.Image,
        candidate_labels: Optional[List[str]] = None,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        labels = candidate_labels or self.DEFAULT_CANDIDATE_LABELS
        issues: List[str] = []
        top_candidates: List[Dict[str, Any]] = []

        # --- zero-shot path ---
        if self.zero_shot:
            try:
                inputs = self.processor(
                    images=image,
                    text=labels,
                    return_tensors="pt",
                    padding=True,
                ).to(self.device)

                with torch.no_grad():
                    out = self.model(**inputs)
                    
                    logits = getattr(out, "logits_per_image", None)
                    if logits is None:
                        logits = getattr(out, "logits", None)
                    if logits is None:
                        raise RuntimeError("zero-shot model output has no logits")

                    probs = torch.softmax(logits[0], dim=-1)  # (num_labels,)
                    k = min(int(top_k), probs.shape[-1])
                    vals, idxs = torch.topk(probs, k=k)

                for p, i in zip(vals.tolist(), idxs.tolist()):
                    top_candidates.append({"label": labels[i], "prob": round(float(p), 4)})

                primary_label = top_candidates[0]["label"] if top_candidates else "Unknown"
                confidence = float(top_candidates[0]["prob"]) if top_candidates else 0.0

                interpretable = _is_label_interpretable(primary_label)
                if not interpretable:
                    issues.append("vision_label_not_interpretable")

                suggests_pneumonia = "pneumonia" in primary_label.lower() or "consolidation" in primary_label.lower()

                return {
                    "model": "MedSigLIP",
                    "mode": "zero_shot",
                    "primary_finding": primary_label,
                    "confidence": round(confidence, 4),
                    "top_candidates": top_candidates,
                    "interpretable": interpretable,
                    "suggests_pneumonia": bool(suggests_pneumonia),
                    "evidence_strength": _evidence_strength(interpretable, confidence),
                    "issues": issues,
                }
            except Exception as exc:
                issues.append(f"zero_shot_failed: {exc}")
                # fallback to classification below

        # --- fallback classification path ---
        try:
            inputs = self.processor(images=image, return_tensors="pt").to(self.device)
            with torch.no_grad():
                logits = self.model(**inputs).logits
                probs = torch.softmax(logits, dim=-1)[0]
                k = min(int(top_k), probs.shape[-1])
                vals, idxs = torch.topk(probs, k=k)

            for p, i in zip(vals.tolist(), idxs.tolist()):
                label = self.model.config.id2label.get(int(i), f"LABEL_{int(i)}")
                top_candidates.append({"label": label, "prob": round(float(p), 4)})

            idx = int(idxs[0].item()) if idxs.numel() > 0 else int(probs.argmax().item())
            label = self.model.config.id2label.get(idx, f"LABEL_{idx}")
            confidence = float(probs[idx])

            interpretable = _is_label_interpretable(label)
            if not interpretable:
                issues.append("vision_label_not_interpretable")
            suggests_pneumonia = interpretable and ("pneumonia" in label.lower())

            return {
                "model": "MedSigLIP",
                "mode": "classification_fallback",
                "primary_finding": label,
                "confidence": round(float(confidence), 4),
                "top_candidates": top_candidates,
                "interpretable": interpretable,
                "suggests_pneumonia": bool(suggests_pneumonia),
                "evidence_strength": _evidence_strength(interpretable, float(confidence)),
                "issues": issues,
            }

        except Exception as exc:
            return {
                "model": "MedSigLIP",
                "mode": "failed",
                "primary_finding": "Unknown",
                "confidence": 0.0,
                "top_candidates": [],
                "interpretable": False,
                "suggests_pneumonia": False,
                "evidence_strength": "low",
                "issues": issues + [f"vision_failed: {exc}"],
            }
