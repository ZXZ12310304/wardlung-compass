import json
import re
from typing import Any, Dict


def safe_json_loads(text: str) -> Dict[str, Any]:
    if not isinstance(text, str):
        raise ValueError("Input must be a string.")
    start_idx = text.find("{")
    if start_idx == -1:
        raise ValueError("Text does not contain JSON braces.")
    json_str = text[start_idx:]
    json_str = re.sub(r",\s*([}\]])", r"\1", json_str)
    json_str = json_str.replace("\n", " ")
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(json_str)
        return obj
    except json.JSONDecodeError:
        end_idx = text.rfind("}")
        if end_idx == -1:
            raise ValueError("Text does not contain JSON braces.")
        json_str = text[start_idx : end_idx + 1]
        json_str = re.sub(r",\s*([}\]])", r"\1", json_str)
        json_str = json_str.replace("\n", " ")
        return json.loads(json_str)
