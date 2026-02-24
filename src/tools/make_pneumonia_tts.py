import asyncio
import os
import subprocess

import edge_tts

OUT_DIR = "data/asr_tests/pneumonia"
VOICE = "en-US-JennyNeural"

SCRIPTS = {
    "pneumonia_cap_001": (
        "Chest X-ray shows right lower lobe patchy opacity consistent with pneumonia. "
        "No pleural effusion. Heart size is normal."
    ),
    "pneumonia_atypical_002": (
        "Bilateral perihilar interstitial opacities are suspicious for atypical pneumonia. "
        "No pneumothorax."
    ),
    "pneumonia_aspiration_003": (
        "Left lower lobe consolidation with air bronchograms. "
        "Aspiration pneumonia is a consideration."
    ),
}

def to_wav_16k_mono(mp3_path: str, wav_path: str) -> None:
   
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", mp3_path,
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-acodec", "pcm_s16le",
            wav_path,
        ],
        check=True,
    )

async def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)

    for name, text in SCRIPTS.items():
        mp3_path = os.path.join(OUT_DIR, f"{name}.mp3")
        wav_path = os.path.join(OUT_DIR, f"{name}.wav")

        print(f"[TTS]  -> {mp3_path}")
        comm = edge_tts.Communicate(text=text, voice=VOICE)
        await comm.save(mp3_path)

        print(f"[FFMPEG] -> {wav_path}")
        to_wav_16k_mono(mp3_path, wav_path)

    print("\nDone. Files are under:", OUT_DIR)

if __name__ == "__main__":
    asyncio.run(main())
