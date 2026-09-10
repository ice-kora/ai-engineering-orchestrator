"""P2-04 Store extensions: run-event journal + review-latest repair.

The journal is AUDIT ONLY — never an authority. Current state is always
rebuilt from live facts (Beads/Git/Mail/Store-owned docs).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from orchestrator.store import Store


def append_run_event(store: Store, plan_id: str, event: dict) -> int:
    """Append-only event: run_events/<plan_id>/<seq>.json. Returns the seq."""
    folder = store.root / "run_events" / plan_id
    folder.mkdir(parents=True, exist_ok=True)
    seq = len(list(folder.glob("*.json"))) + 1
    event.setdefault("event_id", f"{plan_id}-{seq:04d}")
    event["plan_id"] = plan_id
    event["sequence"] = seq
    (folder / f"{seq:04d}.json").write_text(
        json.dumps(event, ensure_ascii=False, indent=1), encoding="utf-8")
    return seq


def run_events(store: Store, plan_id: str) -> list[dict]:
    folder = store.root / "run_events" / plan_id
    if not folder.exists():
        return []
    out = []
    for f in sorted(folder.glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            continue
    return out


def repair_review_latest(store: Store, beads_task_id: str) -> dict | None:
    """C2 recovery: rebuild the latest-review compat view from immutable
    history when it is missing or stale. NEVER calls the reviewer again and
    NEVER mutates history. Returns the repaired payload or None.
    """
    history = store.review_history(beads_task_id)
    if not history:
        return None
    latest_iter = max(history)
    latest = history[latest_iter]
    current = store.review(beads_task_id)
    if current is None or current.get("iteration", 0) < latest_iter:
        store._write(f"reviews/{beads_task_id}.json", latest)
        return latest
    return None  # already consistent
