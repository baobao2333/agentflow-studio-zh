from __future__ import annotations

from pathlib import Path
from typing import Any

from .adapters import ADAPTERS
from .config import read_json, read_yaml, write_json
from .models import END, now_iso, slugify


def create_run(
    *,
    root: Path,
    workflow_path: Path,
    goal: str,
    run_id: str,
    game_name: str | None = None,
) -> Path:
    workflow = read_yaml(workflow_path)
    first_node = workflow["start"]
    resolved_game_name = slugify(game_name or goal)
    state_path = root / "runs" / run_id / "state.json"
    state = {
        "run_id": run_id,
        "goal": goal,
        "game_name": resolved_game_name,
        "workflow_path": str(workflow_path.relative_to(root)),
        "workflow_id": workflow["id"],
        "cocos_project": str((root.parent / "game" / resolved_game_name).resolve()),
        "phase": first_node,
        "status": "running",
        "iteration": 0,
        "max_iterations": workflow.get("defaults", {}).get("max_iterations", 3),
        "artifacts": {},
        "decisions": [],
        "rule_gaps": [],
        "history": [],
    }
    append_history(state, "run_created", {"node": first_node})
    write_json(state_path, state)
    append_timeline(root, state, "run_created", {"node": first_node})
    return state_path


def step_run(*, root: Path, state_path: Path) -> dict[str, Any]:
    state = read_json(state_path)
    if state["status"] in {"paused", "done", "working", "failed"}:
        return state

    workflow = read_yaml(root / state["workflow_path"])
    node = find_node(workflow, state["phase"])

    if node.get("kind") == "human_gate":
        pause_for_human(root, state, node)
        write_json(state_path, state)
        return state

    adapter_name = node.get("adapter", "mock")
    adapter = ADAPTERS.get(adapter_name)
    if adapter is None:
        raise ValueError(f"Unknown adapter: {adapter_name}")

    append_history(state, "node_started", {"node": node["id"]})
    append_timeline(root, state, "node_started", {"node": node["id"]})
    state["status"] = "working"
    state["active_node"] = node["id"]
    state["heartbeat_at"] = now_iso()
    write_json(state_path, state)

    try:
        result = adapter(root=root, state=state, node=node)
    except Exception as exc:
        state["status"] = "failed"
        append_history(state, "node_failed", {"node": node["id"], "error": str(exc)})
        append_timeline(root, state, "node_failed", {"node": node["id"], "error": str(exc)})
        write_json(state_path, state)
        raise

    state["artifacts"].update(result.get("artifacts", {}))

    append_history(
        state,
        "node_completed",
        {"node": node["id"], "status": result.status, "summary": result.get("summary", "")},
    )
    append_timeline(
        root,
        state,
        "node_completed",
        {"node": node["id"], "status": result.status},
    )

    next_node = resolve_next(node, result.status)
    state.pop("active_node", None)
    state.pop("heartbeat_at", None)
    move_to_next(state, next_node)
    write_json(state_path, state)
    return state


def resume_run(
    *,
    root: Path,
    state_path: Path,
    decision: str,
    note: str,
) -> dict[str, Any]:
    state = read_json(state_path)
    if state["status"] != "paused":
        raise ValueError("Run is not paused.")

    workflow = read_yaml(root / state["workflow_path"])
    node = find_node(workflow, state["phase"])
    if node.get("kind") != "human_gate":
        raise ValueError("Current node is not a human gate.")

    decision_record = {
        "id": f"decision-{len(state['decisions']) + 1}",
        "node": node["id"],
        "decision": decision,
        "note": note,
        "time": now_iso(),
    }
    state["decisions"].append(decision_record)
    append_history(state, "human_decision", decision_record)
    append_timeline(root, state, "human_decision", decision_record)

    next_map = node.get("next_on", {})
    next_node = next_map.get(decision)
    if not next_node:
        raise ValueError(f"No next node configured for decision: {decision}")

    state["status"] = "running"
    move_to_next(state, next_node)
    write_json(state_path, state)
    return state


def recover_failed_run(
    *,
    root: Path,
    state_path: Path,
    action: str,
) -> dict[str, Any]:
    state = read_json(state_path)
    if state["status"] != "failed":
        raise ValueError("Run is not failed.")

    workflow = read_yaml(root / state["workflow_path"])
    if action == "retry":
        target = state["phase"]
    elif action == "revise":
        target = "gameplay_rules" if has_node(workflow, "gameplay_rules") else workflow["start"]
    else:
        raise ValueError(f"Unknown recovery action: {action}")

    payload = {"action": action, "from": state["phase"], "to": target}
    state["phase"] = target
    state["status"] = "running"
    state.pop("active_node", None)
    state.pop("heartbeat_at", None)
    append_history(state, "failed_recovery", payload)
    write_json(state_path, state)
    append_timeline(root, state, "failed_recovery", payload)
    return state


def find_node(workflow: dict[str, Any], node_id: str) -> dict[str, Any]:
    for node in workflow["nodes"]:
        if node["id"] == node_id:
            return node
    raise ValueError(f"Node not found: {node_id}")


def has_node(workflow: dict[str, Any], node_id: str) -> bool:
    return any(node["id"] == node_id for node in workflow["nodes"])


def resolve_next(node: dict[str, Any], status: str) -> str:
    next_on = node.get("next_on")
    if next_on:
        return next_on.get(status, next_on.get("default", END))
    return node.get("next", END)


def move_to_next(state: dict[str, Any], next_node: str) -> None:
    if next_node == END:
        state["phase"] = END
        state["status"] = "done"
        append_history(state, "run_done", {})
        return
    state["phase"] = next_node
    state["status"] = "running"


def pause_for_human(root: Path, state: dict[str, Any], node: dict[str, Any]) -> None:
    payload = {
        "node": node["id"],
        "title": node.get("title", node["id"]),
        "prompt": node.get("prompt", "请审阅当前阶段输出。"),
        "choices": sorted(node.get("next_on", {}).keys()),
    }
    state["status"] = "paused"
    append_history(state, "human_gate_paused", payload)
    append_timeline(root, state, "human_gate_paused", payload)


def append_history(state: dict[str, Any], event: str, payload: dict[str, Any]) -> None:
    state["history"].append(
        {
            "time": now_iso(),
            "event": event,
            "phase": state.get("phase"),
            "payload": payload,
        }
    )


def append_timeline(
    root: Path,
    state: dict[str, Any],
    event: str,
    payload: dict[str, Any],
) -> None:
    import json

    timeline_path = root / "runs" / state["run_id"] / "timeline.jsonl"
    timeline_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "time": now_iso(),
        "event": event,
        "phase": state.get("phase"),
        "payload": payload,
    }
    with timeline_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
