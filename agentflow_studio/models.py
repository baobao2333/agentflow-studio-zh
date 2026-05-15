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


def slugify(value: str, fallback: str = "artifact") -> str:
    import re

    normalized = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    normalized = normalized.strip("-")
    return normalized or fallback


def now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def format_template(value: str, state: dict[str, Any]) -> str:
    artifact_namespace = state.get("artifact_namespace") or state.get("feature_name") or state.get("game_name", "")
    return value.format(
        run_id=state["run_id"],
        goal=state["goal"],
        artifact_namespace=artifact_namespace,
        feature_name=state.get("feature_name", artifact_namespace),
        game_name=state.get("game_name", artifact_namespace),
        phase=state.get("phase", ""),
        iteration=state.get("iteration", 0),
    )
