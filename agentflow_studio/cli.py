from __future__ import annotations

import argparse
from pathlib import Path

from .config import read_json
from .engine import create_run, resume_run, step_run
from .render import render_dashboard
from .web import serve


def main() -> None:
    parser = argparse.ArgumentParser(prog="agentflow")
    parser.add_argument("--root", default=".", help="Project root.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    new_parser = subparsers.add_parser("new", help="Create a run.")
    new_parser.add_argument("--workflow", required=True)
    new_parser.add_argument("--run-id", required=True)
    new_parser.add_argument("--goal", required=True)
    new_parser.add_argument("--game-name")

    step_parser = subparsers.add_parser("step", help="Advance one node.")
    step_parser.add_argument("state")

    resume_parser = subparsers.add_parser("resume", help="Resume a paused human gate.")
    resume_parser.add_argument("state")
    resume_parser.add_argument("--decision", required=True)
    resume_parser.add_argument("--note", default="")

    status_parser = subparsers.add_parser("status", help="Show run status.")
    status_parser.add_argument("state")

    render_parser = subparsers.add_parser("render", help="Render HTML dashboard.")
    render_parser.add_argument("state")

    serve_parser = subparsers.add_parser("serve", help="Serve review UI for a run.")
    serve_parser.add_argument("state")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)

    args = parser.parse_args()
    root = Path(args.root).resolve()

    if args.command == "new":
        state_path = create_run(
            root=root,
            workflow_path=(root / args.workflow).resolve(),
            goal=args.goal,
            run_id=args.run_id,
            game_name=args.game_name,
        )
        dashboard = render_dashboard(root=root, state_path=state_path)
        print(f"已创建运行: {state_path}")
        print(f"看板: {dashboard}")
        return

    state_path = (root / args.state).resolve()

    if args.command == "step":
        state = step_run(root=root, state_path=state_path)
        dashboard = render_dashboard(root=root, state_path=state_path)
        print_status(state)
        print(f"看板: {dashboard}")
        return

    if args.command == "resume":
        state = resume_run(
            root=root,
            state_path=state_path,
            decision=args.decision,
            note=args.note,
        )
        dashboard = render_dashboard(root=root, state_path=state_path)
        print_status(state)
        print(f"看板: {dashboard}")
        return

    if args.command == "status":
        print_status(read_json(state_path))
        return

    if args.command == "render":
        dashboard = render_dashboard(root=root, state_path=state_path)
        print(f"看板: {dashboard}")
        return

    if args.command == "serve":
        render_dashboard(root=root, state_path=state_path)
        serve(root=root, state_path=state_path, host=args.host, port=args.port)
        return


def print_status(state: dict) -> None:
    print(f"Run: {state['run_id']}")
    print(f"状态: {state['status']}")
    print(f"当前阶段: {state['phase']}")
    if state.get("artifacts"):
        print("产物:")
        for key, path in state["artifacts"].items():
            print(f"- {key}: {path}")


if __name__ == "__main__":
    main()
