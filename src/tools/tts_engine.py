from __future__ import annotations

import os
from pathlib import Path


def tts(text: str, lang: str = "en", card_id: str | None = None) -> str | None:
    try:
        import edge_tts
        import asyncio
    except Exception:
        return None

    if not text.strip():
        return None

    voice = "en-US-JennyNeural" if (lang or "en").startswith("en") else "zh-CN-XiaoxiaoNeural"
    out_dir = Path("data") / "tts_cache"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = card_id or "care_card"
    mp3_path = out_dir / f"{name}.mp3"

    async def _run():
        comm = edge_tts.Communicate(text=text, voice=voice)
        await comm.save(str(mp3_path))

    try:
        asyncio.run(_run())
    except RuntimeError:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_run())
    return str(mp3_path)
