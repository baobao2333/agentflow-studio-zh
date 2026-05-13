from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import format_template, now_iso


class AdapterResult(dict):
    @property
    def status(self) -> str:
        return str(self.get("status", "done"))


def run_mock_agent(
    *,
    root: Path,
    state: dict[str, Any],
    node: dict[str, Any],
) -> AdapterResult:
    artifacts: dict[str, str] = {}
    for output in node.get("outputs", []):
        key = output["key"]
        relative_path = format_template(output["path"], state)
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_artifact(state, node, output), encoding="utf-8")
        artifacts[key] = relative_path

    return AdapterResult(
        status=node.get("mock_status", "done"),
        summary=node.get("summary", node.get("objective", "")),
        artifacts=artifacts,
    )


def render_artifact(
    state: dict[str, Any],
    node: dict[str, Any],
    output: dict[str, Any],
) -> str:
    title = output.get("title", node.get("title", node["id"]))
    checklist = "\n".join(
        f"- {item}" for item in node.get("checklist", ["确认本阶段输出可被下一节点消费。"])
    )
    return f"""# {title}

> 由 `{node["id"]}` 节点生成。默认适配器只生成结构化占位内容，真实项目可替换为 LLM/工具执行适配器。

## 目标

{node.get("objective", "完成当前节点任务。")}

## 用户目标

{state["goal"]}

## 推荐检查

{checklist}

## 运行信息

| 项 | 值 |
|---|---|
| Run ID | {state["run_id"]} |
| Game Name | {state["game_name"]} |
| Node | {node["id"]} |
| Generated At | {now_iso()} |
"""


ADAPTERS = {
    "mock": run_mock_agent,
}

