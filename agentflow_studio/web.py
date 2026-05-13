from __future__ import annotations

import json
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import parse_qs, quote, unquote, urlparse

from .config import read_json, read_yaml, write_json
from .engine import (
    create_run,
    delete_studio_agent,
    ensure_studio_agents,
    find_node,
    import_studio_agent,
    recover_failed_run,
    resume_run,
    set_studio_agent_enabled,
    standard_agent_sources,
    step_run,
)
from .markdown import render_markdown
from .models import slugify
from .render import render_dashboard, render_history_row, workflow_to_mermaid


def serve(root: Path, state_path: Path, host: str, port: int) -> None:
    handler = make_handler(root.resolve(), state_path.resolve())
    server = ThreadingHTTPServer((host, port), handler)
    print(f"AgentFlow web server: http://{host}:{port}/review")
    server.serve_forever()


def make_handler(root: Path, state_path: Path) -> type[BaseHTTPRequestHandler]:
    action_lock = Lock()
    active_workers: set[str] = set()

    def selected_state_path(query: dict[str, list[str]], form: dict[str, list[str]] | None = None) -> Path:
        run_id = query.get("studio", [""])[0] or (form or {}).get("studio", [""])[0]
        if run_id and "/" not in run_id and "\\" not in run_id:
            candidate = root / "runs" / run_id / "state.json"
            if candidate.exists():
                return candidate
        return state_path

    def start_step_worker(target_state_path: Path, *, recover_working: bool = False) -> bool:
        key = str(target_state_path)
        if key in active_workers:
            return False

        active_workers.add(key)

        def work() -> None:
            try:
                if recover_working:
                    current = read_json(target_state_path)
                    if current.get("status") == "working":
                        current["status"] = "running"
                        current.pop("active_node", None)
                        current.pop("active_nodes", None)
                        current.pop("active_agents", None)
                        current.pop("heartbeat_at", None)
                        write_json(target_state_path, current)
                run_until_blocked(target_state_path)
            except Exception as exc:
                print(f"Background step failed for {target_state_path}: {exc}")
            finally:
                render_dashboard(root=root, state_path=target_state_path)
                active_workers.discard(key)

        Thread(target=work, daemon=True).start()
        return True

    def ensure_step_worker(target_state_path: Path) -> None:
        key = str(target_state_path)
        if key in active_workers:
            return

        current = read_json(target_state_path)
        if current.get("status") == "working":
            start_step_worker(target_state_path, recover_working=True)
        elif current.get("status") == "running":
            start_step_worker(target_state_path)

    def run_until_blocked(target_state_path: Path) -> None:
        steps = 0
        while True:
            current = read_json(target_state_path)
            if current.get("status") != "running":
                return

            workflow = read_yaml(root / current["workflow_path"])
            limit = len(workflow.get("nodes", [])) + int(current.get("max_iterations", 3))
            step_run(root=root, state_path=target_state_path)
            render_dashboard(root=root, state_path=target_state_path)
            steps += 1

            current = read_json(target_state_path)
            if current.get("status") != "running" or steps >= limit:
                return

    class AgentFlowHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)

            if path == "/":
                self.redirect(f"/dashboard?studio={quote(read_json(state_path)['run_id'])}")
                return

            if path == "/review":
                current_state_path = selected_state_path(query)
                ensure_step_worker(current_state_path)
                self.send_html(render_review_page(root, current_state_path, query))
                return

            if path == "/dashboard" or path.endswith("/dashboard.html"):
                current_state_path = selected_state_path(query)
                ensure_step_worker(current_state_path)
                self.send_html(render_dashboard_page(root, current_state_path, query))
                return

            if path.startswith("/artifact/"):
                key = path.removeprefix("/artifact/").strip("/")
                current_state_path = selected_state_path(query)
                ensure_step_worker(current_state_path)
                self.send_html(render_artifact_page(root, current_state_path, key))
                return

            if path.startswith("/raw/"):
                self.send_file(root / path.removeprefix("/raw/"))
                return

            if path.endswith(".md"):
                relative = path.lstrip("/")
                current_state_path = selected_state_path(query)
                state = read_json(current_state_path)
                key = artifact_key_for_path(state, relative)
                if key:
                    self.send_html(render_review_page(root, current_state_path, {"artifact": [key]}))
                else:
                    target = root / relative
                    self.send_html(
                        page_shell(
                            root,
                            state,
                            "review",
                            f"<section class='panel document'>{render_markdown_document(relative, target.read_text(encoding='utf-8'))}</section>",
                        )
                    )
                return

            target = root / path.lstrip("/")
            if target.is_file():
                self.send_file(target)
                return

            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else ""
            form = parse_qs(body)
            query = parse_qs(parsed.query)

            if parsed.path == "/action/studio/new":
                goal = form.get("goal", [""])[0].strip() or "新的 AgentFlow Studio"
                selection = choose_workflow(root, goal)
                requested_run_id = form.get("run_id", [""])[0].strip() or goal
                run_id = unique_run_id(root, requested_run_id)
                new_state_path = create_run(
                    root=root,
                    workflow_path=selection["path"].resolve(),
                    goal=goal,
                    run_id=run_id,
                    game_name=form.get("game_name", [""])[0].strip() or None,
                )
                new_state = read_json(new_state_path)
                new_state["studio_plan"] = {
                    "task_type": selection["task_type"],
                    "workflow": str(selection["path"].relative_to(root)),
                    "reason": selection["reason"],
                    "steps": selection["steps"],
                }
                write_json(new_state_path, new_state)
                start_step_worker(new_state_path)
                render_dashboard(root=root, state_path=new_state_path)
                self.redirect(f"/dashboard?studio={quote(run_id)}")
                return

            current_state_path = selected_state_path(query, form)

            if parsed.path == "/action/agent/import":
                with action_lock:
                    current = import_studio_agent(root, current_state_path, form["agent_id"][0])
                    render_dashboard(root=root, state_path=current_state_path)
                self.redirect(f"/dashboard?studio={quote(current['run_id'])}")
                return

            if parsed.path == "/action/agent/toggle":
                with action_lock:
                    current = set_studio_agent_enabled(
                        root,
                        current_state_path,
                        form["agent_id"][0],
                        form.get("enabled", ["false"])[0] == "true",
                    )
                    render_dashboard(root=root, state_path=current_state_path)
                self.redirect(f"/dashboard?studio={quote(current['run_id'])}")
                return

            if parsed.path == "/action/agent/delete":
                with action_lock:
                    current = delete_studio_agent(root, current_state_path, form["agent_id"][0])
                    render_dashboard(root=root, state_path=current_state_path)
                self.redirect(f"/dashboard?studio={quote(current['run_id'])}")
                return

            if parsed.path == "/action/resume":
                with action_lock:
                    current = read_json(current_state_path)
                    if form.get("phase", [""])[0] == current["phase"] and current["status"] == "paused":
                        state = resume_run(
                            root=root,
                            state_path=current_state_path,
                            decision=form["decision"][0],
                            note=form.get("note", [""])[0],
                        )
                        if state["status"] == "running":
                            start_step_worker(current_state_path)
                    render_dashboard(root=root, state_path=current_state_path)
                self.redirect(f"/review?studio={quote(current['run_id'])}")
                return

            if parsed.path == "/action/step":
                with action_lock:
                    current = read_json(current_state_path)
                    if form.get("phase", [""])[0] == current["phase"] and current["status"] == "running":
                        start_step_worker(current_state_path)
                    render_dashboard(root=root, state_path=current_state_path)
                self.redirect(f"/review?studio={quote(current['run_id'])}")
                return

            if parsed.path == "/action/recover":
                with action_lock:
                    current = read_json(current_state_path)
                    if form.get("phase", [""])[0] == current["phase"] and current["status"] == "failed":
                        current = recover_failed_run(
                            root=root,
                            state_path=current_state_path,
                            action=form.get("recovery", ["retry"])[0],
                        )
                        start_step_worker(current_state_path)
                    render_dashboard(root=root, state_path=current_state_path)
                self.redirect(f"/dashboard?studio={quote(current['run_id'])}")
                return

            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def send_html(self, html: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            data = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def send_file(self, path: Path) -> None:
            data = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type(path))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def redirect(self, location: str) -> None:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", location)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            print(f"{self.address_string()} - {format % args}")

    return AgentFlowHandler


def render_review_page(root: Path, state_path: Path, query: dict[str, list[str]]) -> str:
    state = ensure_studio_agents(root, state_path)
    artifact_key = query.get("artifact", [default_artifact_key(state)])[0]
    artifact_html = render_artifact_body(root, state, artifact_key)
    actions = render_actions(root, state)
    studio = quote(state["run_id"])
    artifacts = "\n".join(
        f"<li><a href='/review?studio={studio}&artifact={escape(key)}'>{escape(key)}</a> "
        f"<a class='muted' href='/raw/{escape(path)}'>raw</a></li>"
        for key, path in state.get("artifacts", {}).items()
    )
    history = "\n".join(
        f"<li><code>{escape(item['event'])}</code> - {escape(str(item.get('phase', '')))}</li>"
        for item in state.get("history", [])[-8:]
    )
    live_events = render_live_events(read_timeline_events(root, state)[-12:])
    return page_shell(
        root,
        state,
        "review",
        f"""
        <section class="hero">
          <div>
            <p class="eyebrow">review console / artifact handoff</p>
            <h2>审阅工作台</h2>
            <p>{escape(state["goal"])}</p>
          </div>
          <div class="status {escape(state["status"])}">{escape(state["status"])}</div>
        </section>
        <section class="grid">
          <aside class="panel">
            <h3>当前</h3>
            <p>阶段：<code>{escape(state["phase"])}</code></p>
            {actions}
            <button type="button" onclick="openExecutionDetails()">Execution details</button>
            <h3>产物</h3>
            <ul class="artifact-list">{artifacts or "<li>暂无产物</li>"}</ul>
            <h3>最近事件</h3>
            <ul>{history}</ul>
            <h3>执行信号</h3>
            <ol class="live-log">{live_events}</ol>
          </aside>
          <main class="panel document">
            {artifact_html}
          </main>
        </section>
        """,
        auto_refresh=state["status"] == "working",
    )


def render_dashboard_page(root: Path, state_path: Path, query: dict[str, list[str]]) -> str:
    state = ensure_studio_agents(root, state_path)
    workflow = read_yaml(root / state["workflow_path"])
    studio = quote(state["run_id"])
    mermaid = workflow_to_mermaid(workflow, state)
    history_rows = "\n".join(render_history_row(item) for item in state["history"])
    live_events = render_live_events(read_timeline_events(root, state)[-18:])
    artifacts = "\n".join(
        f"<li><code>{escape(key)}</code><a href='/artifact/{escape(key)}?studio={studio}'>{escape(path)}</a></li>"
        for key, path in state.get("artifacts", {}).items()
    )
    active_node = state.get("active_node") or state["phase"]
    agent_console = render_agent_console(root, state, workflow)
    agent_workload = render_agent_workload(root, state, workflow)
    plan = render_studio_plan(state)
    return page_shell(
        root,
        state,
        "dashboard",
        f"""
        <section class="hero">
          <div>
            <p class="eyebrow">agentflow studio / execution surface</p>
            <h2>{escape(state["run_id"])}</h2>
            <p>{escape(state["goal"])}</p>
          </div>
          <div class="telemetry">
            <span>phase <code>{escape(state["phase"])}</code></span>
            <span>active <code>{escape(str(active_node))}</code></span>
            <span class="status {escape(state["status"])}">{escape(state["status"])}</span>
            <button type="button" onclick="openExecutionDetails()">Execution details</button>
          </div>
        </section>
        {agent_console}
        {agent_workload}
        {plan}
        <section class="panel artifact-strip">
          <h3>产物</h3>
          <ul>{artifacts or "<li>暂无产物</li>"}</ul>
        </section>
        <details class="panel technical-details">
          <summary>Technical details</summary>
          <h3>执行轨迹</h3>
          <ol class="live-log">{live_events}</ol>
          <h3>工作流图</h3>
          <pre class="mermaid">{escape(mermaid)}</pre>
          <h3>原始时间线</h3>
          <table>
            <thead><tr><th>时间</th><th>事件</th><th>阶段</th><th>详情</th></tr></thead>
            <tbody>{history_rows}</tbody>
          </table>
        </details>
        """,
        auto_refresh=state["status"] == "working",
    )


def render_artifact_page(root: Path, state_path: Path, key: str) -> str:
    state = ensure_studio_agents(root, state_path)
    return page_shell(
        root,
        state,
        "review",
        f"<section class='panel document'>{render_artifact_body(root, state, key)}</section>",
        auto_refresh=state["status"] == "working",
    )


def render_artifact_body(root: Path, state: dict, key: str) -> str:
    relative = state["artifacts"][key]
    target = root / relative
    return render_markdown_document(relative, target.read_text(encoding="utf-8"))


def render_markdown_document(title: str, markdown: str) -> str:
    return f"""
    <div class="doc-title">
      <span>Markdown</span>
      <code>{escape(title)}</code>
    </div>
    {render_markdown(markdown)}
    """


def render_actions(root: Path, state: dict) -> str:
    workflow = read_yaml(root / state["workflow_path"])
    node = next((item for item in workflow["nodes"] if item["id"] == state["phase"]), None)
    studio = quote(state["run_id"])

    if state["status"] == "paused" and node and node.get("kind") == "human_gate":
        buttons = []
        for decision in node.get("next_on", {}):
            buttons.append(
                f"""
                <form method="post" action="/action/resume?studio={studio}">
                  <input type="hidden" name="decision" value="{escape(decision)}" />
                  <input type="hidden" name="phase" value="{escape(state["phase"])}" />
                  <input type="hidden" name="studio" value="{escape(state["run_id"])}" />
                  <input name="note" placeholder="审阅备注，可为空" />
                  <button type="submit">{escape(decision)}</button>
                </form>
                """
            )
        return f"<div class='actions'><p>{escape(node.get('prompt', '请审阅。'))}</p>{''.join(buttons)}</div>"

    if state["status"] == "running":
        return f"""
        <form method="post" action="/action/step?studio={studio}" class="actions">
          <input type="hidden" name="phase" value="{escape(state["phase"])}" />
          <input type="hidden" name="studio" value="{escape(state["run_id"])}" />
          <p class="muted">自动执行已就绪；打开页面后会一路推进到 Human Gate、失败或完成。</p>
          <button type="submit">立即自动执行</button>
        </form>
        """

    if state["status"] == "working":
        return "<p class='muted'>节点正在执行，执行信号会自动更新。</p>"

    if state["status"] == "failed":
        return f"""
        <div class="actions">
          <p class="muted">节点失败。先查看日志，再选择重试或回到规则修订。</p>
          <button type="button" onclick="openExecutionDetails()">Open logs</button>
          <form method="post" action="/action/recover?studio={studio}">
            <input type="hidden" name="phase" value="{escape(state["phase"])}" />
            <input type="hidden" name="studio" value="{escape(state["run_id"])}" />
            <input type="hidden" name="recovery" value="retry" />
            <button type="submit">Retry node</button>
          </form>
          <form method="post" action="/action/recover?studio={studio}">
            <input type="hidden" name="phase" value="{escape(state["phase"])}" />
            <input type="hidden" name="studio" value="{escape(state["run_id"])}" />
            <input type="hidden" name="recovery" value="revise" />
            <button type="submit">Revise rules</button>
          </form>
        </div>
        """

    return "<p class='muted'>当前无需操作。</p>"


def render_agent_console(root: Path, state: dict, workflow: dict) -> str:
    node = find_node(workflow, state["phase"]) if state.get("phase") != "__end__" else {}
    active_agents = active_agent_entries(state, workflow)
    if len(active_agents) > 1:
        agent = f"{len(active_agents)} active agents"
        title = "multi-agent execution"
    else:
        agent = active_agents[0]["agent"] if active_agents else node.get("agent") or ("human gate" if node.get("kind") == "human_gate" else "system")
        title = active_agents[0]["title"] if active_agents else node.get("title", state.get("phase", ""))
    status_line = status_message(state, node)
    latest_messages = render_agent_messages(root, state)
    actions = render_actions(root, state)
    return f"""
    <section class="agent-console">
      <div class="agent-header">
        <div>
          <p class="eyebrow">current agent</p>
          <h3>{escape(agent)}</h3>
          <p>{escape(title)}</p>
        </div>
        <span class="status {escape(state["status"])}">{escape(state["status"])}</span>
      </div>
      <div class="dialogue">
        <div class="agent-message system">
          <span>system</span>
          <p>{escape(status_line)}</p>
        </div>
        {latest_messages}
      </div>
      <div class="agent-actions">
        {actions}
      </div>
    </section>
    """


def render_agent_workload(root: Path, state: dict, workflow: dict) -> str:
    rows = "\n".join(render_agent_workload_row(item) for item in agent_workloads(root, state, workflow))
    return f"""
    <section class="agent-workload">
      <div>
        <p class="eyebrow">agent workload</p>
        <h3>Studio Agents</h3>
      </div>
      <div class="agent-workload-grid">
        {rows or "<p class='muted'>暂无挂载 Agent</p>"}
      </div>
    </section>
    """


def render_agent_workload_row(item: dict) -> str:
    active_tasks = "".join(f"<li>{escape(task)}</li>" for task in item["active_tasks"])
    if not active_tasks:
        active_tasks = "<li class='muted'>idle</li>"
    return f"""
    <article class="agent-load-row">
      <div>
        <span class="status {escape(item['status'])}">{escape(item['status'])}</span>
        <h4>{escape(item['name'])}</h4>
        <code>{escape(item['id'])}</code>
      </div>
      <div class="agent-load-stats">
        <span>{item['active_count']} active</span>
        <span>{item['completed_count']} completed</span>
        <span>{item['assigned_count']} assigned</span>
      </div>
      <ol>{active_tasks}</ol>
    </article>
    """


def agent_workloads(root: Path, state: dict, workflow: dict) -> list[dict]:
    node_by_id = {node["id"]: node for node in workflow.get("nodes", [])}
    active_by_agent: dict[str, list[str]] = {}
    for item in active_agent_entries(state, workflow):
        active_by_agent.setdefault(item["agent"], []).append(item["title"])

    completed_by_agent: dict[str, int] = {}
    for item in state.get("history", []):
        if item.get("event") != "node_completed":
            continue
        node = node_by_id.get(item.get("payload", {}).get("node"))
        agent_id = node.get("agent") if node else ""
        if agent_id:
            completed_by_agent[agent_id] = completed_by_agent.get(agent_id, 0) + 1

    assigned_by_agent: dict[str, int] = {}
    for node in workflow.get("nodes", []):
        agent_id = node.get("agent")
        if agent_id:
            assigned_by_agent[agent_id] = assigned_by_agent.get(agent_id, 0) + 1

    rows = []
    for agent_id, agent in sorted(state.get("agents", {}).items()):
        active_tasks = active_by_agent.get(agent_id, [])
        rows.append(
            {
                "id": agent_id,
                "name": agent.get("name", agent_id),
                "status": agent.get("status", "unknown"),
                "active_tasks": active_tasks,
                "active_count": len(active_tasks),
                "completed_count": completed_by_agent.get(agent_id, 0),
                "assigned_count": assigned_by_agent.get(agent_id, 0),
            }
        )
    return rows


def active_agent_entries(state: dict, workflow: dict) -> list[dict]:
    if state.get("status") != "working":
        return []

    entries = []
    for item in state.get("active_agents", []):
        agent_id = item.get("agent")
        if agent_id:
            entries.append(
                {
                    "agent": agent_id,
                    "node": item.get("node", ""),
                    "title": item.get("title", item.get("node", "")),
                }
            )
    if entries:
        return entries

    node_ids = state.get("active_nodes") or [state.get("active_node")]
    for node_id in node_ids:
        if not node_id:
            continue
        node = find_node(workflow, node_id)
        entries.append(
            {
                "agent": node.get("agent", "system"),
                "node": node_id,
                "title": node.get("title", node_id),
            }
        )
    return entries


def status_message(state: dict, node: dict) -> str:
    status = state.get("status")
    if status == "working":
        return f"{node.get('title', state.get('phase'))} 正在执行，页面会自动刷新执行状态。"
    if status == "running":
        return f"{node.get('title', state.get('phase'))} 已进入自动执行队列，会继续推进到需要人工介入的位置。"
    if status == "paused":
        return node.get("prompt", "当前节点等待人工审阅。")
    if status == "failed":
        return "当前节点失败。请先打开执行详情查看日志，再选择重试或回到规则修订。"
    if status == "done":
        return "当前 Studio 已完成。"
    return f"当前状态：{status}"


def render_agent_messages(root: Path, state: dict) -> str:
    timeline = read_timeline_events(root, state)
    relevant = []
    for item in reversed(timeline):
        event = item.get("event")
        if event not in {"node_started", "node_progress", "node_completed", "node_failed", "human_gate_paused", "self_correction_routed", "failed_recovery"}:
            continue
        relevant.append(item)
        if len(relevant) == 4:
            break
    if not relevant:
        relevant = list(reversed(state.get("history", [])[-3:]))
    rows = []
    for item in reversed(relevant):
        payload = item.get("payload", {})
        message = payload.get("message") or payload.get("summary") or payload.get("reason") or json.dumps(payload, ensure_ascii=False)
        rows.append(
            f"""
            <div class="agent-message">
              <span>{escape(item.get("event", "event"))} · {escape(item.get("time", ""))}</span>
              <p>{escape(str(message))}</p>
            </div>
            """
        )
    return "\n".join(rows)


def render_studio_plan(state: dict) -> str:
    plan = state.get("studio_plan")
    if not plan:
        return ""
    steps = "".join(f"<li>{escape(step)}</li>" for step in plan.get("steps", []))
    return f"""
    <section class="plan-strip">
      <div>
        <p class="eyebrow">workflow decision</p>
        <h3>{escape(plan.get("task_type", "task"))}</h3>
        <p>{escape(plan.get("reason", ""))}</p>
      </div>
      <ol>{steps}</ol>
    </section>
    """


def page_shell(
    root: Path,
    state: dict,
    active: str,
    body: str,
    *,
    auto_refresh: bool = False,
) -> str:
    studio = quote(state["run_id"])
    refresh = '<meta http-equiv="refresh" content="5" />' if auto_refresh else ""
    sidebar = render_studio_sidebar(root, state)
    execution_dialog = render_execution_dialog(root, state)
    agent_dialog = render_agent_management_dialog(root, state)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  {refresh}
  <title>AgentFlow Studio - {escape(state["run_id"])}</title>
  <script type="module">
    import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
    mermaid.initialize({{ startOnLoad: true, theme: 'dark' }});
  </script>
  <style>
    :root {{
      --bg: #070a0e;
      --panel: #10151b;
      --paper: #f1e8d8;
      --muted: #a7aaa3;
      --blue: #6aa7c8;
      --copper: #b07655;
      --line: rgba(216, 208, 192, .16);
      --read: #e3dccf;
      --glow: rgba(106, 167, 200, .34);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background:
        linear-gradient(90deg, rgba(7,10,14,.98), rgba(7,10,14,.9)),
        radial-gradient(circle at 78% 6%, rgba(106,167,200,.16), transparent 34rem);
      color: var(--read);
      font-family: "Noto Serif SC", "Source Han Serif SC", "Songti SC", Georgia, serif;
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background: repeating-linear-gradient(180deg, rgba(255,255,255,.025) 0, rgba(255,255,255,.025) 1px, transparent 1px, transparent 6px);
      opacity: .28;
    }}
    a {{ color: var(--blue); text-decoration: none; }}
    code, pre, .eyebrow, .status, .telemetry, .rail, input, textarea, select, button, table, .doc-title, .live-log {{
      font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    }}
    code {{ background: rgba(106, 167, 200, .1); border: 1px solid var(--line); border-radius: 4px; padding: 2px 5px; color: var(--paper); }}
    .app-shell {{ display: grid; grid-template-columns: minmax(15rem, 18vw) minmax(0, 1fr); min-height: 100svh; }}
    .rail {{ position: sticky; top: 0; height: 100svh; padding: 22px 18px; border-right: 1px solid var(--line); background: rgba(10, 14, 19, .88); overflow: auto; }}
    .brand {{ color: var(--paper); font-size: 17px; letter-spacing: 0; margin-bottom: 4px; }}
    .rail .sub {{ color: var(--muted); font-size: 12px; margin-bottom: 22px; }}
    .studio-list {{ display: grid; gap: 8px; margin: 14px 0 18px; }}
    .studio-link {{ display: grid; gap: 4px; padding: 10px 0; border-bottom: 1px solid rgba(216,208,192,.1); color: var(--muted); }}
    .studio-link.active {{ color: var(--paper); border-left: 2px solid var(--blue); padding-left: 10px; }}
    .studio-title {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; }}
    .studio-link small {{ color: var(--muted); overflow-wrap: anywhere; }}
    .agent-dock {{ margin-top: 24px; padding-top: 16px; border-top: 1px solid var(--line); }}
    .rail-heading {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; color: var(--paper); margin-bottom: 10px; }}
    .rail-heading button {{ padding: 5px 8px; font-size: 11px; }}
    .agent-list {{ display: grid; gap: 8px; }}
    .agent-compact {{ display: grid; gap: 2px; padding: 8px 0; border-bottom: 1px solid rgba(216,208,192,.1); }}
    .agent-compact span {{ color: var(--paper); }}
    .agent-compact small {{ color: var(--muted); overflow-wrap: anywhere; }}
    .badge {{ border: 1px solid rgba(176,118,85,.55); color: var(--copper); background: rgba(176,118,85,.12); padding: 2px 6px; font-size: 10px; }}
    .mode-tabs {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 18px 0; }}
    .mode-tabs a, button {{
      border: 1px solid rgba(106, 167, 200, .44);
      background: rgba(106, 167, 200, .12);
      color: var(--paper);
      padding: 9px 11px;
      cursor: pointer;
      text-align: center;
      transition: transform .18s ease, border-color .18s ease, background .18s ease;
    }}
    .mode-tabs a.active, button:hover {{ border-color: var(--blue); background: rgba(106, 167, 200, .2); transform: translateY(-1px); }}
    button:disabled {{ cursor: not-allowed; opacity: .42; transform: none; }}
    .workspace {{ padding: 26px 34px 42px; display: grid; gap: 20px; min-width: 0; }}
    .hero {{ display: flex; justify-content: space-between; align-items: end; gap: 24px; padding: 12px 0 22px; border-bottom: 1px solid var(--line); }}
    .hero h2 {{ color: var(--paper); font-size: clamp(32px, 5vw, 72px); line-height: 1; margin: 0 0 12px; letter-spacing: 0; }}
    .hero p {{ margin: 0; max-width: 820px; color: var(--muted); line-height: 1.7; }}
    .eyebrow {{ color: var(--blue); font-size: 12px; text-transform: lowercase; margin-bottom: 10px !important; }}
    .telemetry {{ display: grid; gap: 8px; justify-items: end; color: var(--muted); font-size: 12px; }}
    .panel {{ background: rgba(16, 21, 27, .78); border: 1px solid var(--line); padding: 18px; }}
    .panel h3 {{ margin: 0 0 12px; color: var(--paper); font-size: 17px; }}
    .grid {{ display: grid; grid-template-columns: minmax(260px, 340px) minmax(0, 1fr); gap: 18px; align-items: start; }}
    .dashboard-grid {{ display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(18rem, .6fr); gap: 18px; align-items: start; }}
    .agent-console {{ border: 1px solid var(--line); background: rgba(16, 21, 27, .72); padding: 22px; display: grid; gap: 18px; }}
    .agent-header {{ display: flex; justify-content: space-between; gap: 18px; align-items: flex-start; border-bottom: 1px solid var(--line); padding-bottom: 16px; }}
    .agent-header h3 {{ margin: 0 0 6px; color: var(--paper); font-size: 26px; }}
    .agent-header p {{ margin: 0; color: var(--muted); }}
    .dialogue {{ display: grid; gap: 12px; max-width: 980px; }}
    .agent-message {{ border-left: 2px solid var(--blue); background: rgba(106, 167, 200, .08); padding: 12px 14px; }}
    .agent-message.system {{ border-left-color: var(--copper); background: rgba(176, 118, 85, .08); }}
    .agent-message span {{ color: var(--muted); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; }}
    .agent-message p {{ margin: 6px 0 0; line-height: 1.65; }}
    .agent-actions {{ max-width: 420px; }}
    .agent-workload {{ border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); padding: 18px 0; display: grid; grid-template-columns: minmax(13rem, .4fr) minmax(0, 1fr); gap: 18px; }}
    .agent-workload h3 {{ margin: 0; color: var(--paper); }}
    .agent-workload-grid {{ display: grid; gap: 10px; }}
    .agent-load-row {{ display: grid; grid-template-columns: minmax(13rem, .7fr) minmax(16rem, .8fr) minmax(0, 1fr); gap: 12px; align-items: start; border-left: 2px solid var(--blue); background: rgba(106,167,200,.06); padding: 12px; }}
    .agent-load-row h4 {{ margin: 8px 0 4px; color: var(--paper); }}
    .agent-load-row ol {{ margin: 0; padding-left: 18px; color: var(--read); }}
    .agent-load-stats {{ display: flex; flex-wrap: wrap; gap: 8px; color: var(--muted); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; }}
    .enabled {{ color: #b9d8c0; border-color: rgba(185,216,192,.44); }}
    .disabled {{ color: var(--muted); border-color: rgba(167,170,163,.32); background: rgba(167,170,163,.08); }}
    .plan-strip {{ border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); padding: 18px 0; display: grid; grid-template-columns: minmax(0, .8fr) minmax(0, 1.2fr); gap: 22px; }}
    .plan-strip h3 {{ margin: 0 0 8px; color: var(--paper); }}
    .plan-strip p {{ margin: 0; color: var(--muted); line-height: 1.6; }}
    .plan-strip ol {{ margin: 0; display: grid; gap: 8px; color: var(--read); }}
    .technical-details summary {{ cursor: pointer; color: var(--paper); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }}
    .technical-details[open] {{ display: grid; gap: 16px; }}
    .status {{ display: inline-block; padding: 4px 9px; color: var(--blue); border: 1px solid rgba(106,167,200,.44); background: rgba(106,167,200,.1); }}
    .paused {{ color: var(--copper); border-color: rgba(176,118,85,.55); background: rgba(176,118,85,.12); }}
    .working {{ color: var(--paper); box-shadow: 0 0 24px var(--glow); }}
    .failed {{ color: #ffb4a8; border-color: rgba(255,180,168,.48); }}
    .done {{ color: #b9d8c0; border-color: rgba(185,216,192,.44); }}
    .muted {{ color: var(--muted); font-size: 13px; }}
    .artifact-list, .artifact-strip ul {{ display: grid; gap: 8px; padding-left: 18px; }}
    .artifact-strip li {{ display: grid; gap: 4px; }}
    .actions {{ display: grid; gap: 8px; margin: 12px 0 20px; }}
    .actions form {{ display: grid; gap: 8px; }}
    input, textarea, select {{
      width: 100%;
      border: 1px solid var(--line);
      background: rgba(7,10,14,.82);
      color: var(--paper);
      padding: 9px;
    }}
    textarea {{ min-height: 92px; resize: vertical; }}
    .document {{
      max-width: 900px;
      line-height: 1.72;
      overflow: auto;
      color: var(--read);
    }}
    .document h1, .document h2, .document h3 {{ color: var(--paper); }}
    .document h1 {{ margin-top: 0; font-size: 34px; }}
    .doc-title {{ display: flex; gap: 8px; align-items: center; color: var(--muted); border-bottom: 1px solid var(--line); padding-bottom: 10px; margin-bottom: 16px; }}
    table {{ width: 100%; border-collapse: collapse; color: var(--read); font-size: 13px; }}
    td, th {{ border-bottom: 1px solid var(--line); padding: 8px; text-align: left; vertical-align: top; }}
    blockquote {{ border-left: 2px solid var(--copper); margin-left: 0; padding: 8px 14px; background: rgba(176, 118, 85, .08); color: var(--paper); }}
    pre {{ background: rgba(7,10,14,.82); color: var(--read); padding: 14px; overflow: auto; border: 1px solid var(--line); }}
    .live-log {{ display: grid; gap: 10px; padding: 0; margin: 0; list-style: none; }}
    .live-log li {{ border-left: 2px solid var(--blue); padding-left: 10px; color: var(--muted); }}
    .live-log strong {{ color: var(--paper); font-weight: 500; }}
    dialog {{ border: 1px solid var(--line); background: var(--panel); color: var(--read); width: min(560px, calc(100vw - 32px)); }}
    dialog.wide {{ width: min(1080px, calc(100vw - 32px)); max-height: min(860px, calc(100svh - 32px)); overflow: auto; }}
    dialog::backdrop {{ background: rgba(0,0,0,.72); }}
    .dialog-actions {{ display: flex; justify-content: flex-end; gap: 10px; margin-top: 14px; }}
    .detail-links {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 10px 0 18px; }}
    .log-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    .log-grid pre {{ max-height: 260px; white-space: pre-wrap; }}
    .agent-manager-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
    .agent-table {{ display: grid; gap: 10px; }}
    .agent-row {{ display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 10px; align-items: center; padding: 12px 0; border-bottom: 1px solid var(--line); }}
    .agent-row strong, .agent-row code, .agent-row small {{ display: block; margin-bottom: 4px; }}
    .agent-row small {{ color: var(--muted); line-height: 1.45; }}
    @media (max-width: 900px) {{
      .app-shell {{ grid-template-columns: 1fr; }}
      .rail {{ position: static; height: auto; }}
      .workspace {{ padding: 20px; }}
      .grid, .dashboard-grid, .log-grid, .plan-strip, .agent-workload, .agent-load-row, .agent-manager-grid, .agent-row {{ grid-template-columns: 1fr; }}
      .agent-header {{ flex-direction: column; }}
      .hero {{ align-items: flex-start; flex-direction: column; }}
      .telemetry {{ justify-items: start; }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      *, *::before, *::after {{ animation-duration: .001ms !important; animation-iteration-count: 1 !important; scroll-behavior: auto !important; transition-duration: .001ms !important; }}
    }}
  </style>
</head>
<body>
  <div class="app-shell">
    {sidebar}
    <main class="workspace">
      <nav class="mode-tabs">
        <a class="{active_class(active, 'dashboard')}" href="/dashboard?studio={studio}">Dashboard</a>
        <a class="{active_class(active, 'review')}" href="/review?studio={studio}">Review</a>
      </nav>
      {body}
    </main>
  </div>
  <dialog id="new-studio">
    <form method="post" action="/action/studio/new">
      <h3>New Studio</h3>
      <p class="muted">输入目标即可，系统会先拆解任务并自动选择可复用的工作流。</p>
      <label>目标<textarea name="goal" required placeholder="例如：做一个俯视角抢车位小游戏"></textarea></label>
      <label>Run ID<input name="run_id" placeholder="留空则按目标生成" /></label>
      <label>Game Name<input name="game_name" placeholder="可选" /></label>
      <div class="dialog-actions">
        <button type="button" onclick="document.getElementById('new-studio').close()">取消</button>
        <button type="submit">创建</button>
      </div>
    </form>
  </dialog>
  {execution_dialog}
  {agent_dialog}
  <script>
    window.openNewStudio = () => document.getElementById('new-studio').showModal();
    window.openExecutionDetails = () => document.getElementById('execution-details').showModal();
    window.openAgentManager = () => document.getElementById('agent-manager').showModal();
  </script>
</body>
</html>
"""


def render_studio_sidebar(root: Path, current: dict) -> str:
    studios = list_studios(root)
    agent_dock = render_agent_dock(root, current)
    items = "\n".join(
        f"""
        <a class="studio-link {active_class(item['run_id'], current['run_id'])}" href="/dashboard?studio={quote(item['run_id'])}">
          <span class="studio-title">
            <span>{escape(item['run_id'])}</span>
            {render_review_badge(item)}
          </span>
          <small>{escape(item['status'])} / {escape(item['phase'])}</small>
        </a>
        """
        for item in studios
    )
    return f"""
    <aside class="rail">
      <div class="brand">AgentFlow Studio</div>
      <div class="sub">artifact pipeline / human gates</div>
      <button type="button" onclick="openNewStudio()">New Studio</button>
      <div class="studio-list">{items or "<p class='muted'>暂无 Studio</p>"}</div>
      {agent_dock}
    </aside>
    """


def render_agent_dock(root: Path, current: dict) -> str:
    workflow = read_yaml(root / current["workflow_path"])
    active = active_agent_entries(current, workflow)
    active_counts: dict[str, int] = {}
    for item in active:
        active_counts[item["agent"]] = active_counts.get(item["agent"], 0) + 1

    rows = []
    for agent_id, agent in sorted(current.get("agents", {}).items()):
        active_count = active_counts.get(agent_id, 0)
        rows.append(
            f"""
            <div class="agent-compact">
              <span>{escape(agent.get("name", agent_id))}</span>
              <small>{escape(agent.get("status", "unknown"))}{f" / {active_count} active" if active_count else ""}</small>
            </div>
            """
        )
    return f"""
    <section class="agent-dock">
      <div class="rail-heading">
        <span>Agents</span>
        <button type="button" onclick="openAgentManager()">Manage</button>
      </div>
      <div class="agent-list">{''.join(rows) or "<p class='muted'>暂无挂载 Agent</p>"}</div>
    </section>
    """


def render_agent_management_dialog(root: Path, state: dict) -> str:
    studio = quote(state["run_id"])
    mounted = state.get("agents", {})
    workflow = read_yaml(root / state["workflow_path"])
    running_agents = {item["agent"] for item in active_agent_entries(state, workflow)}
    mounted_rows = "\n".join(
        render_mounted_agent_row(studio, agent_id, agent, agent_id in running_agents)
        for agent_id, agent in sorted(mounted.items())
    )
    import_rows = "\n".join(
        render_standard_agent_import(studio, agent_id, item["data"])
        for agent_id, item in sorted(standard_agent_sources(root).items())
        if agent_id not in mounted
    )
    return f"""
    <dialog id="agent-manager" class="wide">
      <h3>Agent Manager</h3>
      <p class="muted">标准 Agent 来自 <code>configs/agents/*.yaml</code>；导入后会复制为当前 Studio 的独立副本。</p>
      <section class="agent-manager-grid">
        <div>
          <h3>Mounted</h3>
          <div class="agent-table">{mounted_rows or "<p class='muted'>暂无挂载 Agent</p>"}</div>
        </div>
        <div>
          <h3>Standard Library</h3>
          <div class="agent-table">{import_rows or "<p class='muted'>标准库 Agent 已全部导入</p>"}</div>
        </div>
      </section>
      <div class="dialog-actions">
        <button type="button" onclick="document.getElementById('agent-manager').close()">Close</button>
      </div>
    </dialog>
    """


def render_mounted_agent_row(studio: str, agent_id: str, agent: dict, running: bool) -> str:
    enabled = agent.get("status") == "enabled"
    next_enabled = "false" if enabled else "true"
    toggle_label = "停用" if enabled else "启用"
    disabled = "disabled" if running else ""
    running_copy = " · running" if running else ""
    return f"""
    <article class="agent-row">
      <div>
        <strong>{escape(agent.get("name", agent_id))}</strong>
        <code>{escape(agent_id)}</code>
        <small>{escape(agent.get("status", "unknown"))}{running_copy} · {escape(agent.get("copy", ""))}</small>
      </div>
      <form method="post" action="/action/agent/toggle?studio={studio}">
        <input type="hidden" name="agent_id" value="{escape(agent_id)}" />
        <input type="hidden" name="enabled" value="{next_enabled}" />
        <button type="submit" {disabled}>{toggle_label}</button>
      </form>
      <form method="post" action="/action/agent/delete?studio={studio}">
        <input type="hidden" name="agent_id" value="{escape(agent_id)}" />
        <button type="submit" {disabled}>删除</button>
      </form>
    </article>
    """


def render_standard_agent_import(studio: str, agent_id: str, agent: dict) -> str:
    return f"""
    <article class="agent-row">
      <div>
        <strong>{escape(agent.get("name", agent_id))}</strong>
        <code>{escape(agent_id)}</code>
        <small>{escape(agent.get("mission", "")).strip()}</small>
      </div>
      <form method="post" action="/action/agent/import?studio={studio}">
        <input type="hidden" name="agent_id" value="{escape(agent_id)}" />
        <button type="submit">导入</button>
      </form>
    </article>
    """


def list_studios(root: Path) -> list[dict]:
    studios = []
    for path in (root / "runs").glob("*/state.json"):
        try:
            state = read_json(path)
        except Exception:
            continue
        studios.append(
            {
                "run_id": state.get("run_id", path.parent.name),
                "status": state.get("status", "unknown"),
                "phase": state.get("phase", ""),
                "needs_review": needs_human_review(root, state),
                "updated": path.stat().st_mtime,
            }
        )
    return sorted(studios, key=lambda item: item["updated"], reverse=True)


def render_review_badge(item: dict) -> str:
    if item.get("needs_review"):
        return "<span class='badge'>review</span>"
    if item.get("status") == "failed":
        return "<span class='badge'>failed</span>"
    return ""


def needs_human_review(root: Path, state: dict) -> bool:
    if state.get("status") != "paused":
        return False
    try:
        workflow = read_yaml(root / state["workflow_path"])
        return find_node(workflow, state["phase"]).get("kind") == "human_gate"
    except Exception:
        return False


def render_execution_dialog(root: Path, state: dict) -> str:
    node_id = latest_execution_node(state)
    if not node_id:
        return """
        <dialog id="execution-details" class="wide">
          <h3>Execution Details</h3>
          <p class="muted">暂无执行日志。</p>
          <div class="dialog-actions"><button type="button" onclick="document.getElementById('execution-details').close()">Close</button></div>
        </dialog>
        """

    paths = execution_log_paths(root, state, node_id)
    links = "\n".join(
        render_log_link(root, label, path)
        for label, path in paths.items()
    )
    stdout_tail = tail_text(paths["stdout"])
    stderr_tail = tail_text(paths["stderr"])
    last_tail = tail_text(paths["last_message"])
    return f"""
    <dialog id="execution-details" class="wide">
      <h3>Execution Details</h3>
      <p class="muted">node <code>{escape(node_id)}</code></p>
      <div class="detail-links">{links}</div>
      <section class="log-grid">
        <div>
          <h3>stdout tail</h3>
          <pre>{escape(stdout_tail or "No stdout log yet.")}</pre>
        </div>
        <div>
          <h3>stderr tail</h3>
          <pre>{escape(stderr_tail or "No stderr log yet.")}</pre>
        </div>
      </section>
      <section>
        <h3>last message</h3>
        <pre>{escape(last_tail or "No last message yet.")}</pre>
      </section>
      <div class="dialog-actions">
        <button type="button" onclick="document.getElementById('execution-details').close()">Close</button>
      </div>
    </dialog>
    """


def latest_execution_node(state: dict) -> str:
    if state.get("active_node"):
        return str(state["active_node"])
    for item in reversed(state.get("history", [])):
        payload = item.get("payload", {})
        node = payload.get("node")
        if item.get("event") in {"node_started", "node_completed", "node_failed"} and node:
            return str(node)
    return ""


def execution_log_paths(root: Path, state: dict, node_id: str) -> dict[str, Path]:
    run_dir = root / "runs" / state["run_id"] / "codex"
    return {
        "prompt": run_dir / f"{node_id}.prompt.md",
        "stdout": run_dir / f"{node_id}.stdout.log",
        "stderr": run_dir / f"{node_id}.stderr.log",
        "last_message": run_dir / f"{node_id}.last.md",
    }


def render_log_link(root: Path, label: str, path: Path) -> str:
    if not path.exists():
        return f"<span class='muted'>{escape(label)} missing</span>"
    return f"<a href='/raw/{escape(path.relative_to(root).as_posix())}'>{escape(label)}</a>"


def tail_text(path: Path, limit: int = 6000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-limit:]


def choose_workflow(root: Path, goal: str) -> dict:
    workflows = sorted((root / "configs" / "workflows").glob("*.yaml"))
    if not workflows:
        raise ValueError("No workflows configured.")

    normalized = goal.lower()
    game_markers = ["cocos", "creator", "游戏", "玩法", "关卡", "小游戏", "game"]
    for path in workflows:
        data = read_yaml(path)
        text = f"{data.get('id', '')} {data.get('name', '')}".lower()
        if any(marker in normalized for marker in game_markers) and (
            "cocos" in text or "game" in text or "游戏" in data.get("name", "")
        ):
            return {
                "path": path,
                "task_type": "Cocos game workflow",
                "reason": "目标包含游戏/Cocos/玩法信号，复用 Cocos 游戏开发长流程。",
                "steps": [
                    "澄清玩法目标、玩家承诺和关键未知项。",
                    "收敛核心循环、MVP 边界和非目标。",
                    "建立规则、流程、验收和实现交接物。",
                    "进入 Cocos 实现、构建验证和人工审阅。",
                ],
            }

    first = workflows[0]
    data = read_yaml(first)
    return {
        "path": first,
        "task_type": data.get("name", "Configured workflow"),
        "reason": "没有命中特定工作流信号，使用当前默认工作流作为最接近的可复用流程。",
        "steps": [
            "读取目标并拆解关键输入。",
            "按工作流阶段生成产物。",
            "在人工节点等待审阅或修订。",
            "根据验证结果继续推进或自纠偏。",
        ],
    }


def active_class(value: str, current: str) -> str:
    return "active" if value == current else ""


def unique_run_id(root: Path, value: str) -> str:
    base = slugify(value, fallback="studio")
    run_id = base
    counter = 2
    while (root / "runs" / run_id / "state.json").exists():
        run_id = f"{base}-{counter}"
        counter += 1
    return run_id


def read_timeline_events(root: Path, state: dict) -> list[dict]:
    path = root / "runs" / state["run_id"] / "timeline.jsonl"
    if not path.exists():
        return []

    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def render_live_events(events: list[dict]) -> str:
    if not events:
        return "<li><strong>idle</strong><br><span>暂无执行信号</span></li>"

    rows = []
    for item in events:
        payload = item.get("payload", {})
        message = payload.get("message") or payload.get("summary") or json.dumps(payload, ensure_ascii=False)
        rows.append(
            "<li>"
            f"<strong>{escape(item.get('event', 'event'))}</strong> "
            f"<span>{escape(item.get('time', ''))}</span><br>"
            f"<span>{escape(str(message))}</span>"
            "</li>"
        )
    return "\n".join(rows)


def default_artifact_key(state: dict) -> str:
    for key in ("gameplay_handoff", "validation_report", "implementation_report"):
        if key in state.get("artifacts", {}):
            return key
    artifacts = state.get("artifacts", {})
    return next(reversed(artifacts), "") if artifacts else ""


def artifact_key_for_path(state: dict, relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/")
    for key, path in state.get("artifacts", {}).items():
        if path.replace("\\", "/") == normalized:
            return key
    return ""


def content_type(path: Path) -> str:
    if path.suffix == ".html":
        return "text/html; charset=utf-8"
    if path.suffix == ".md":
        return "text/markdown; charset=utf-8"
    if path.suffix == ".json":
        return "application/json; charset=utf-8"
    if path.suffix == ".css":
        return "text/css; charset=utf-8"
    if path.suffix == ".js":
        return "text/javascript; charset=utf-8"
    if path.suffix == ".png":
        return "image/png"
    return "application/octet-stream"
