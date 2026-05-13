from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from .config import read_json, read_yaml


def render_dashboard(*, root: Path, state_path: Path) -> Path:
    state = read_json(state_path)
    workflow = read_yaml(root / state["workflow_path"])
    mermaid = workflow_to_mermaid(workflow, state)
    history_rows = "\n".join(render_history_row(item) for item in state["history"])
    artifacts = "\n".join(
        f"<li><code>{escape(key)}</code>: <a href='/artifact/{escape(key)}'>{escape(path)}</a> "
        f"<a class='muted' href='/{escape(path)}'>raw</a></li>"
        for key, path in state.get("artifacts", {}).items()
    )
    review_link = "<a href='/review'>进入审阅页</a>"
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AgentFlow Studio - {escape(state["run_id"])}</title>
  <script type="module">
    import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
    mermaid.initialize({{ startOnLoad: true, theme: 'neutral' }});
  </script>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; color: #1f2937; background: #f8fafc; }}
    header {{ padding: 24px 32px; background: #0f172a; color: white; }}
    main {{ padding: 24px 32px; display: grid; gap: 20px; }}
    section {{ background: white; border: 1px solid #e5e7eb; border-radius: 8px; padding: 20px; }}
    h1, h2 {{ margin: 0 0 12px; }}
    code {{ background: #f1f5f9; padding: 2px 5px; border-radius: 4px; }}
    a {{ color: #2563eb; }}
    .muted {{ color: #6b7280; font-size: 13px; margin-left: 6px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; padding: 8px; text-align: left; vertical-align: top; }}
    .status {{ display: inline-block; padding: 4px 10px; border-radius: 999px; background: #dbeafe; color: #1e40af; }}
    .paused {{ background: #fef3c7; color: #92400e; }}
    .done {{ background: #dcfce7; color: #166534; }}
  </style>
</head>
<body>
  <header>
    <h1>AgentFlow Studio</h1>
    <div>Run <code>{escape(state["run_id"])}</code> · 当前阶段 <code>{escape(state["phase"])}</code> · <span class="status {escape(state["status"])}">{escape(state["status"])}</span></div>
  </header>
  <main>
    <section>
      <h2>目标</h2>
      <p>{escape(state["goal"])}</p>
      <p>{review_link}</p>
    </section>
    <section>
      <h2>工作流图</h2>
      <pre class="mermaid">{escape(mermaid)}</pre>
    </section>
    <section>
      <h2>产物</h2>
      <ul>{artifacts or "<li>暂无产物</li>"}</ul>
    </section>
    <section>
      <h2>时间线</h2>
      <table>
        <thead><tr><th>时间</th><th>事件</th><th>阶段</th><th>详情</th></tr></thead>
        <tbody>{history_rows}</tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""
    output = state_path.parent / "dashboard.html"
    output.write_text(html, encoding="utf-8")
    return output


def workflow_to_mermaid(workflow: dict[str, Any], state: dict[str, Any]) -> str:
    lines = ["flowchart TD"]
    current = state.get("phase")
    for node in workflow["nodes"]:
        node_id = safe_id(node["id"])
        label = node.get("title", node["id"])
        shape = f'{node_id}["{label}"]'
        if node["id"] == current:
            shape = f'{node_id}["{label}<br/>当前"]'
        lines.append(f"  {shape}")
        if node.get("kind") == "human_gate":
            for decision, target in node.get("next_on", {}).items():
                if target == "__end__":
                    lines.append(f"  {node_id} -->|{decision}| END((结束))")
                else:
                    lines.append(f"  {node_id} -->|{decision}| {safe_id(target)}")
        else:
            target = node.get("next")
            if target:
                if target == "__end__":
                    lines.append(f"  {node_id} --> END((结束))")
                else:
                    lines.append(f"  {node_id} --> {safe_id(target)}")
            for status, target in node.get("next_on", {}).items():
                if status == "default":
                    continue
                if target == "__end__":
                    lines.append(f"  {node_id} -->|{status}| END((结束))")
                else:
                    lines.append(f"  {node_id} -->|{status}| {safe_id(target)}")
    return "\n".join(lines)


def safe_id(value: str) -> str:
    return "N_" + value.replace("-", "_")


def render_history_row(item: dict[str, Any]) -> str:
    import json

    payload = json.dumps(item.get("payload", {}), ensure_ascii=False)
    return (
        "<tr>"
        f"<td>{escape(item.get('time', ''))}</td>"
        f"<td>{escape(item.get('event', ''))}</td>"
        f"<td><code>{escape(str(item.get('phase', '')))}</code></td>"
        f"<td><code>{escape(payload)}</code></td>"
        "</tr>"
    )
