"""
Shared Serper.dev query counter for ARIA and AdContact.
Both modules read/write the same JSON file atomically so the 2,500-query
free-tier limit is tracked across both services in one place.
"""
import json
import os
import tempfile

_COUNTER_FILE = os.path.join(os.path.dirname(__file__), ".serper_usage.json")
_LIMIT = 2500


def _load() -> dict:
    try:
        with open(_COUNTER_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"total": 0}


def _save(data: dict) -> None:
    """Atomic write: write to tmp file then rename."""
    dir_ = os.path.dirname(_COUNTER_FILE)
    fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".serper_usage_tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, _COUNTER_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read() -> int:
    """Return the current total queries used."""
    return _load().get("total", 0)


def increment(n: int = 1) -> int:
    """Increment the counter by n, save, and return the new total."""
    data = _load()
    data["total"] = data.get("total", 0) + n
    _save(data)
    return data["total"]


def remaining() -> int:
    """Return how many queries are left in the free tier."""
    return max(0, _LIMIT - read())
