from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


END = "__end__"


@dataclass(frozen=True)
class WorkflowPaths:
    root: Path
    workflow: Path
    state: Path


def slugify(value: str, fallback: str = "game") -> str:
    import re

    normalized = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    normalized = normalized.strip("-")
    return normalized or fallback


def now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def format_template(value: str, state: dict[str, Any]) -> str:
    return value.format(
        run_id=state["run_id"],
        goal=state["goal"],
        game_name=state["game_name"],
        phase=state.get("phase", ""),
        iteration=state.get("iteration", 0),
    )

