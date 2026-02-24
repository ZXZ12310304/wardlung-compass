# src/agents/asr.py
import os
import re
import tempfile
from typing import Optional, Tuple, Dict, Any, List

import torch
from transformers import AutoProcessor, AutoModelForCTC

# ====== Hard constraint: ONLY MedASR ======
MEDASR_MODEL_ID = "google/medasr"

FORCE_CUDA_ENV = "FORCE_CUDA"
MED_ASR_MODEL_ID_ENV = "MED_ASR_MODEL_ID"
MED_ASR_DEVICE_ENV = "MED_ASR_DEVICE"
MED_ASR_USE_FP16_ENV = "MED_ASR_USE_FP16"  
MED_ASR_DEBUG_ENV = "MED_ASR_DEBUG"       


MED_ASR_CHUNK_LENGTH_S_ENV = "MED_ASR_CHUNK_LENGTH_S"   
MED_ASR_STRIDE_LENGTH_S_ENV = "MED_ASR_STRIDE_LENGTH_S" 

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_HF_CACHE_DIR = os.path.join(_REPO_ROOT, "models")


def _force_cuda_enabled() -> bool:
    return os.getenv(FORCE_CUDA_ENV, "").strip().lower() in ("1", "true", "yes", "y")


def _hf_token() -> Optional[str]:
    return os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN")


def _debug_enabled() -> bool:
    return os.getenv(MED_ASR_DEBUG_ENV, "").strip().lower() in ("1", "true", "yes", "y")


def _resolve_device(force_cuda: bool) -> torch.device:
    if force_cuda:
        if not torch.cuda.is_available():
            raise RuntimeError(
                "FORCE_CUDA is set but CUDA is not available. "
                "Install CUDA-enabled PyTorch or unset FORCE_CUDA."
            )
        return torch.device("cuda")

    mode = os.getenv(MED_ASR_DEVICE_ENV, "auto").strip().lower()
    if mode == "cpu":
        return torch.device("cpu")
    if mode == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("MED_ASR_DEVICE=cuda but CUDA is not available.")
        return torch.device("cuda")
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _ensure_only_medasr(model_id: str) -> str:
    mid = (model_id or "").strip() or MEDASR_MODEL_ID
    if mid != MEDASR_MODEL_ID:
        raise ValueError(
            f"Only MedASR is allowed in this project. "
            f"Expected model_id='{MEDASR_MODEL_ID}', got '{mid}'."
        )
    return mid


def _ensure_ffmpeg_and_pydub() -> None:
    try:
        from pydub import AudioSegment  # noqa: F401
    except Exception as exc:
        raise RuntimeError(
            "Missing dependency 'pydub'. Install it with: pip install pydub\n"
            "And ensure ffmpeg exists: apt-get update && apt-get install -y ffmpeg"
        ) from exc


def _normalize_audio_to_wav16k_mono(audio_path: str, force_resample_wav: bool = True) -> Tuple[str, bool]:
   
    if not audio_path:
        raise ValueError("audio_path is empty")

    _ensure_ffmpeg_and_pydub()
    from pydub import AudioSegment

    lower = audio_path.lower()
    if lower.endswith(".wav") and not force_resample_wav:
        return audio_path, False

    audio = AudioSegment.from_file(audio_path)
    audio = audio.set_frame_rate(16000).set_channels(1)

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    audio.export(tmp.name, format="wav")
    return tmp.name, True


def _from_pretrained_compat(fn, *, token: Optional[str], trust_remote_code: bool, **kwargs):
    
    try:
        if token:
            return fn(**kwargs, token=token, trust_remote_code=trust_remote_code)
        return fn(**kwargs, trust_remote_code=trust_remote_code)
    except TypeError:
        if token:
            return fn(**kwargs, use_auth_token=token, trust_remote_code=trust_remote_code)
        return fn(**kwargs, trust_remote_code=trust_remote_code)


def _to_device_and_dtype(batch: Dict[str, Any], device: torch.device, dtype: torch.dtype) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in batch.items():
        if torch.is_tensor(v):
            v = v.to(device)
            if v.is_floating_point():
                v = v.to(dtype=dtype)
        out[k] = v
    return out


def _post_clean(text: str) -> str:
    
    if not text:
        return ""
    t = text.strip()

    t = t.replace("</s>", "").replace("<s>", "").strip()

    rep = {
        "{period}": ".",
        "{comma}": ",",
        "{colon}": ":",
        "{semicolon}": ";",
        "{question}": "?",
        "{exclamation}": "!",
        "{new paragraph}": "\n",
        "{newline}": "\n",
    }
    for k, v in rep.items():
        t = t.replace(k, v)

    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\s+([.,:;!?])", r"\1", t)
    return t.strip()


def _ctc_collapse(ids: List[int], blank_id: Optional[int]) -> List[int]:
   
    collapsed: List[int] = []
    prev = None
    for i in ids:
        if prev is not None and i == prev:
            continue
        prev = i
        if blank_id is not None and i == blank_id:
            continue
        collapsed.append(i)
    return collapsed


class MedASRTranscriber:
    

    def __init__(
        self,
        model_id: Optional[str] = None,
        force_resample_wav: bool = True,
    ) -> None:
        env_mid = os.getenv(MED_ASR_MODEL_ID_ENV, "").strip()
        self.model_id = _ensure_only_medasr(model_id or env_mid or MEDASR_MODEL_ID)

        force_cuda = _force_cuda_enabled()
        self.device = _resolve_device(force_cuda)
        use_fp16 = os.getenv(MED_ASR_USE_FP16_ENV, "1").strip().lower() in ("1", "true", "yes", "y")
        self.dtype = torch.float16 if (use_fp16 and self.device.type == "cuda") else torch.float32

        self.chunk_length_s = int(os.getenv(MED_ASR_CHUNK_LENGTH_S_ENV, "20"))
        self.stride_length_s = int(os.getenv(MED_ASR_STRIDE_LENGTH_S_ENV, "2"))
        self.force_resample_wav = bool(force_resample_wav)

        token = _hf_token()
        trust_remote_code = True  

        self.processor = _from_pretrained_compat(
            AutoProcessor.from_pretrained,
            token=token,
            trust_remote_code=trust_remote_code,
            pretrained_model_name_or_path=self.model_id,
            cache_dir=DEFAULT_HF_CACHE_DIR,
        )

        self.model = _from_pretrained_compat(
            AutoModelForCTC.from_pretrained,
            token=token,
            trust_remote_code=trust_remote_code,
            pretrained_model_name_or_path=self.model_id,
            cache_dir=DEFAULT_HF_CACHE_DIR,
        ).to(self.device)

        self.model = self.model.to(dtype=self.dtype).eval()

        
        self.tokenizer = getattr(self.processor, "tokenizer", None)
        blank_id = None
        if self.tokenizer is not None:
            try:
                
                blank_id = self.tokenizer.convert_tokens_to_ids("<epsilon>")
               
                if hasattr(self.tokenizer, "get_vocab") and "<epsilon>" not in self.tokenizer.get_vocab():
                    blank_id = None
            except Exception:
                blank_id = None
        self.blank_id = blank_id

        print(f"[MedASR] Loaded model={self.model_id} device={self.device.type} dtype={self.dtype}")

    @torch.inference_mode()
    def _infer_one_chunk(self, speech_16k: Any) -> str:
        """
        speech_16k: 1D numpy array (float32) or list[float]
        """
        inputs = self.processor(
            speech_16k,
            sampling_rate=16000,
            return_tensors="pt",
            padding=True,
        )
        inputs = _to_device_and_dtype(dict(inputs), self.device, self.dtype)

        outputs = self.model.generate(**inputs)  # (B, T)
        if outputs is None:
            return ""

        ids = outputs[0].detach().cpu().tolist()
        
        ids = _ctc_collapse(ids, self.blank_id)

        if self.tokenizer is not None:
            text = self.tokenizer.decode(ids, skip_special_tokens=True)
        else:
            
            text = self.processor.batch_decode([ids], skip_special_tokens=True)[0]

        return _post_clean(str(text or ""))

    def _chunk_waveform(self, wav_path: str) -> List[Any]:
       
        import torchaudio

        wav, sr = torchaudio.load(wav_path)  # (C, T)
        if wav.numel() == 0:
            return []

        # mono
        if wav.dim() == 2 and wav.size(0) > 1:
            wav = wav.mean(dim=0, keepdim=True)

        # resample to 16k
        if sr != 16000:
            wav = torchaudio.functional.resample(wav, sr, 16000)
            sr = 16000

        wav = wav.squeeze(0)  # (T,)
        total = wav.shape[0]
        if total <= 0:
            return []

        chunk = self.chunk_length_s * sr
        stride = self.stride_length_s * sr
        chunk = max(1, int(chunk))
        stride = max(0, int(stride))

        step = max(1, chunk - stride)

        chunks = []
        start = 0
        while start < total:
            end = min(total, start + chunk)
            piece = wav[start:end]
            chunks.append(piece.cpu().numpy().astype("float32"))
            if end >= total:
                break
            start += step
        return chunks

    def transcribe(self, audio_path: str) -> str:
        wav_path, is_temp = _normalize_audio_to_wav16k_mono(audio_path, force_resample_wav=self.force_resample_wav)
        try:
            if _debug_enabled():
                try:
                    import torchaudio
                    w, sr = torchaudio.load(wav_path)
                    dur = w.shape[-1] / float(sr)
                    peak = float(w.abs().max().item())
                    print(f"[MedASR][debug] sr={sr} dur={dur:.2f}s peak={peak:.4f} path={wav_path}")
                except Exception as e:
                    print(f"[MedASR][debug] torchaudio inspect failed: {e}")

            segments = self._chunk_waveform(wav_path)
            if not segments:
                return "[empty transcript]"

            texts: List[str] = []
            for seg in segments:
                t = self._infer_one_chunk(seg)
                if t:
                    texts.append(t)

            merged = " ".join(texts).strip()
            return merged if merged else "[empty transcript]"

        finally:
            if is_temp:
                try:
                    os.remove(wav_path)
                except Exception:
                    pass
