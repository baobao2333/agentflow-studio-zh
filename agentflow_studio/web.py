from __future__ import annotations

from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .config import read_json, read_yaml
from .engine import resume_run, step_run
from .markdown import render_markdown
from .render import render_dashboard


def serve(root: Path, state_path: Path, host: str, port: int) -> None:
    handler = make_handler(root.resolve(), state_path.resolve())
    server = ThreadingHTTPServer((host, port), handler)
    print(f"AgentFlow web server: http://{host}:{port}/review")
    server.serve_forever()


def make_handler(root: Path, state_path: Path) -> type[BaseHTTPRequestHandler]:
    class AgentFlowHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)

            if path in {"/", "/review"}:
                self.send_html(render_review_page(root, state_path, parse_qs(parsed.query)))
                return

            if path == "/dashboard" or path.endswith("/dashboard.html"):
                dashboard = render_dashboard(root=root, state_path=state_path)
                self.send_html(dashboard.read_text(encoding="utf-8"))
                return

            if path.startswith("/artifact/"):
                key = path.removeprefix("/artifact/").strip("/")
                self.send_html(render_artifact_page(root, state_path, key))
                return

            if path.startswith("/raw/"):
                target = safe_join(root, path.removeprefix("/raw/"))
                if target and target.exists() and target.is_file():
                    self.send_file(target)
                    return

            if path.endswith(".md"):
                relative = path.lstrip("/")
                target = safe_join(root, relative)
                if target and target.exists():
                    state = read_json(state_path)
                    key = artifact_key_for_path(state, relative)
                    if key:
                        self.send_html(render_review_page(root, state_path, {"artifact": [key]}))
                    else:
                        self.send_html(
                            page_shell(
                                state,
                                f"<section class='panel document'>{render_markdown_document(relative, target.read_text(encoding='utf-8'))}</section>",
                            )
                        )
                    return

            target = safe_join(root, path.lstrip("/"))
            if target and target.exists() and target.is_file():
                self.send_file(target)
                return

            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else ""
            form = parse_qs(body)

            try:
                if parsed.path == "/action/resume":
                    decision = first(form, "decision")
                    note = first(form, "note")
                    resume_run(root=root, state_path=state_path, decision=decision, note=note)
                    render_dashboard(root=root, state_path=state_path)
                    self.redirect("/review")
                    return

                if parsed.path == "/action/step":
                    step_run(root=root, state_path=state_path)
                    render_dashboard(root=root, state_path=state_path)
                    self.redirect("/review")
                    return
            except Exception as exc:
                self.send_html(render_error(str(exc)), status=HTTPStatus.BAD_REQUEST)
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
    state = read_json(state_path)
    artifact_key = query.get("artifact", [default_artifact_key(state)])[0]
    artifact_html = render_artifact_body(root, state, artifact_key)
    actions = render_actions(root, state)
    artifacts = "\n".join(
        f"<li><a href='/review?artifact={escape(key)}'>{escape(key)}</a> "
        f"<a class='muted' href='/raw/{escape(path)}'>raw</a></li>"
        for key, path in state.get("artifacts", {}).items()
    )
    history = "\n".join(
        f"<li><code>{escape(item['event'])}</code> - {escape(str(item.get('phase', '')))}</li>"
        for item in state.get("history", [])[-8:]
    )
    return page_shell(
        state,
        f"""
        <section class="panel hero">
          <div>
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
            <h3>产物</h3>
            <ul class="artifact-list">{artifacts or "<li>暂无产物</li>"}</ul>
            <h3>最近事件</h3>
            <ul>{history}</ul>
          </aside>
          <main class="panel document">
            {artifact_html}
          </main>
        </section>
        """,
    )


def render_artifact_page(root: Path, state_path: Path, key: str) -> str:
    state = read_json(state_path)
    return page_shell(state, f"<section class='panel document'>{render_artifact_body(root, state, key)}</section>")


def render_artifact_body(root: Path, state: dict, key: str) -> str:
    relative = state.get("artifacts", {}).get(key)
    if not relative:
        return f"<h2>找不到产物</h2><p><code>{escape(key)}</code></p>"
    target = safe_join(root, relative)
    if not target or not target.exists():
        return f"<h2>产物文件不存在</h2><p><code>{escape(relative)}</code></p>"
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

    if state["status"] == "paused" and node and node.get("kind") == "human_gate":
        buttons = []
        for decision in node.get("next_on", {}):
            buttons.append(
                f"""
                <form method="post" action="/action/resume">
                  <input type="hidden" name="decision" value="{escape(decision)}" />
                  <input name="note" placeholder="审阅备注，可为空" />
                  <button type="submit">{escape(decision)}</button>
                </form>
                """
            )
        return f"<div class='actions'><p>{escape(node.get('prompt', '请审阅。'))}</p>{''.join(buttons)}</div>"

    if state["status"] == "running":
        return """
        <form method="post" action="/action/step" class="actions">
          <button type="submit">推进下一步</button>
        </form>
        """

    return "<p class='muted'>当前无需操作。</p>"


def page_shell(state: dict, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AgentFlow Review - {escape(state["run_id"])}</title>
  <style>
    body {{ margin: 0; background: #f6f7f9; color: #1f2937; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    header {{ background: #111827; color: #fff; padding: 22px 32px; }}
    header h1 {{ margin: 0 0 8px; font-size: 28px; }}
    header a {{ color: #bfdbfe; }}
    .wrap {{ padding: 24px 32px; display: grid; gap: 18px; }}
    .panel {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 20px; }}
    .hero {{ display: flex; justify-content: space-between; align-items: center; gap: 16px; }}
    .grid {{ display: grid; grid-template-columns: minmax(260px, 340px) 1fr; gap: 18px; align-items: start; }}
    .status {{ border-radius: 999px; padding: 6px 12px; background: #dbeafe; color: #1e40af; }}
    .paused {{ background: #fef3c7; color: #92400e; }}
    .done {{ background: #dcfce7; color: #166534; }}
    code {{ background: #f1f5f9; border-radius: 4px; padding: 2px 5px; }}
    a {{ color: #1d4ed8; }}
    .muted {{ color: #6b7280; font-size: 13px; margin-left: 6px; }}
    .artifact-list li {{ margin: 8px 0; }}
    .actions {{ display: grid; gap: 8px; margin: 12px 0 20px; }}
    .actions form {{ display: grid; gap: 8px; }}
    input {{ border: 1px solid #d1d5db; border-radius: 6px; padding: 8px; }}
    button {{ border: 0; border-radius: 6px; background: #2563eb; color: white; padding: 9px 12px; cursor: pointer; font-weight: 600; }}
    button:hover {{ background: #1d4ed8; }}
    .document {{ line-height: 1.65; overflow: auto; }}
    .document h1 {{ margin-top: 0; }}
    .doc-title {{ display: flex; gap: 8px; align-items: center; color: #6b7280; border-bottom: 1px solid #e5e7eb; padding-bottom: 10px; margin-bottom: 16px; }}
    table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
    td, th {{ border: 1px solid #e5e7eb; padding: 8px; vertical-align: top; }}
    blockquote {{ border-left: 4px solid #93c5fd; margin-left: 0; padding: 8px 12px; background: #eff6ff; }}
    pre {{ background: #0f172a; color: #e5e7eb; padding: 14px; border-radius: 8px; overflow: auto; }}
    @media (max-width: 900px) {{ .grid {{ grid-template-columns: 1fr; }} .hero {{ align-items: flex-start; flex-direction: column; }} }}
  </style>
</head>
<body>
  <header>
    <h1>AgentFlow Review</h1>
    <div>Run <code>{escape(state["run_id"])}</code> · <a href="/dashboard">Dashboard</a> · <a href="/review">Review</a></div>
  </header>
  <div class="wrap">{body}</div>
</body>
</html>
"""


def render_error(message: str) -> str:
    return f"<!doctype html><meta charset='utf-8'><h1>操作失败</h1><p>{escape(message)}</p><p><a href='/review'>返回审阅页</a></p>"


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


def first(form: dict[str, list[str]], key: str) -> str:
    return form.get(key, [""])[0]


def safe_join(root: Path, relative: str) -> Path | None:
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


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
