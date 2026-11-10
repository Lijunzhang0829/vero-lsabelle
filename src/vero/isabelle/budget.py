"""Read-only normal-runtime clock projection; legacy absolute clocks stay readable."""
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from .benchmark import read


def duration(value):
    if type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
        raise ValueError("budget_seconds must be a positive finite number")
    return value


def snapshot(path, *, now=None):
    data = read(Path(path))
    if isinstance(data, dict) and data.get("schema") == "experiment-budget-v2":
        from .active_clock import view
        return view(data, now=now)
    if (not isinstance(data, dict) or data.get("schema") != "experiment-budget-v1"
            or not isinstance(data.get("run_id"), str) or not data["run_id"]):
        raise ValueError("invalid experiment budget identity")
    start, deadline, budget = (data[k] for k in ("started_unix", "deadline_unix", "budget_seconds"))
    if any(type(v) not in (float, int) or not math.isfinite(v) for v in (start, deadline, budget)):
        raise ValueError("budget values must be finite numbers")
    if budget <= 0 or deadline <= start or abs(deadline - start - budget) > .01:
        raise ValueError("deadline must match the authorized total budget")
    now = time.time() if now is None else now
    if type(now) not in (float, int) or not math.isfinite(now) or now < start:
        raise ValueError("invalid experiment clock")
    remaining = max(0, deadline - now)
    return {
        "status": "available" if remaining else "exhausted",
        "run_id": data["run_id"], "budget_seconds": budget,
        "started_unix": start, "deadline_unix": deadline,
        "deadline_utc": datetime.fromtimestamp(deadline, timezone.utc).isoformat(),
        "observed_unix": now, "elapsed_seconds": now - start,
        "remaining_seconds": remaining,
        "basis": "supervisor-owned experiment deadline; read-only query, no budget reset",
    }
