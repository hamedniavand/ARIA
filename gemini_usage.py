"""Shared Gemini token usage counter — atomic read/write to .gemini_usage.json."""
import json
import os
import tempfile

_FILE = os.path.join(os.path.dirname(__file__), ".gemini_usage.json")

# Gemini 2.5 Flash pricing (USD per 1M tokens)
_INPUT_COST_PER_M  = 0.30
_OUTPUT_COST_PER_M = 2.50
_USD_TO_EUR        = 0.92


def increment(input_tokens: int = 0, output_tokens: int = 0) -> dict:
    data = read()
    data["input_tokens"]  += input_tokens
    data["output_tokens"] += output_tokens
    data["calls"]         += 1
    _write(data)
    return data


def read() -> dict:
    try:
        with open(_FILE) as f:
            d = json.load(f)
            return {
                "input_tokens":  d.get("input_tokens", 0),
                "output_tokens": d.get("output_tokens", 0),
                "calls":         d.get("calls", 0),
            }
    except Exception:
        return {"input_tokens": 0, "output_tokens": 0, "calls": 0}


def cost_eur(data: dict | None = None) -> float:
    if data is None:
        data = read()
    usd = (
        data["input_tokens"]  / 1_000_000 * _INPUT_COST_PER_M +
        data["output_tokens"] / 1_000_000 * _OUTPUT_COST_PER_M
    )
    return round(usd * _USD_TO_EUR, 4)


def _write(data: dict) -> None:
    dir_ = os.path.dirname(_FILE) or "."
    fd, tmp = tempfile.mkstemp(dir=dir_)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, _FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
