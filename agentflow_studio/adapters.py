from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import time
from pathlib import Path
from typing import Any

from .config import read_yaml
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
        path.write_text(render_artifact_for_path(state, node, output, path), encoding="utf-8")
        artifacts[key] = relative_path

    return AdapterResult(
        status=node.get("mock_status", "done"),
        summary=node.get("summary", node.get("objective", "")),
        artifacts=artifacts,
    )


def render_artifact_for_path(
    state: dict[str, Any],
    node: dict[str, Any],
    output: dict[str, Any],
    path: Path,
) -> str:
    markdown = render_artifact(state, node, output)
    if path.suffix.lower() in {".html", ".htm"}:
        return render_mock_html(state, node, output, markdown)
    return markdown


def render_mock_html(
    state: dict[str, Any],
    node: dict[str, Any],
    output: dict[str, Any],
    markdown: str,
) -> str:
    from html import escape

    title = output.get("title", node.get("title", node["id"]))
    subject = state.get("artifact_namespace") or state.get("feature_name") or state.get("game_name", "")
    body = escape(markdown)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} - {escape(state["run_id"])}</title>
  <style>
    body {{ margin: 0; background: #f7f8fa; color: #15191f; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; line-height: 1.55; }}
    main {{ max-width: 980px; margin: 0 auto; padding: 32px 20px 64px; }}
    header {{ border-bottom: 1px solid #d8dee6; margin-bottom: 22px; padding-bottom: 18px; }}
    .kicker {{ color: #667085; font-size: 12px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }}
    h1 {{ margin: 8px 0; font-size: 36px; line-height: 1.12; }}
    pre {{ white-space: pre-wrap; overflow-wrap: anywhere; background: #fff; border: 1px solid #d8dee6; border-radius: 8px; padding: 18px; }}
  </style>
</head>
<body>
  <main>
    <header>
      <div class="kicker">Mock HTML Artifact</div>
      <h1>{escape(title)}</h1>
      <p>Run <code>{escape(state["run_id"])}</code> · Subject <code>{escape(subject)}</code> · Node <code>{escape(node["id"])}</code></p>
    </header>
    <pre>{body}</pre>
  </main>
</body>
</html>
"""


def run_codex_cli_agent(
    *,
    root: Path,
    state: dict[str, Any],
    node: dict[str, Any],
) -> AdapterResult:
    codex = shutil.which("codex")
    if not codex:
        raise RuntimeError("Codex CLI not found in PATH.")

    artifacts: dict[str, str] = {}
    outputs = []
    for output in node.get("outputs", []):
        relative_path = format_template(output["path"], state)
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        outputs.append({**output, "path": relative_path})
        artifacts[output["key"]] = relative_path

    run_dir = root / "runs" / state["run_id"] / "codex"
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = run_dir / f"{node['id']}.prompt.md"
    stdout_path = run_dir / f"{node['id']}.stdout.log"
    stderr_path = run_dir / f"{node['id']}.stderr.log"
    last_message_path = run_dir / f"{node['id']}.last.md"

    cocos_projects = resolve_cocos_projects(root, state, node)
    prompt = build_codex_prompt(
        root=root,
        state=state,
        node=node,
        outputs=outputs,
        cocos_projects=cocos_projects,
    )
    prompt_path.write_text(prompt, encoding="utf-8")

    command = [
        codex,
        "exec",
        "--cd",
        str(root),
        "--sandbox",
        "workspace-write",
    ]
    for project in cocos_projects:
        if project.resolve() != root.resolve():
            command.extend(["--add-dir", str(project)])
    command.extend(
        [
            "--output-last-message",
            str(last_message_path),
            "-",
        ]
    )

    append_runtime_event(
        root,
        state,
        "node_progress",
        {
            "node": node["id"],
            "message": "Codex CLI process started.",
            "logs": {
                "stdout": str(stdout_path.relative_to(root)),
                "stderr": str(stderr_path.relative_to(root)),
                "last_message": str(last_message_path.relative_to(root)),
            },
        },
    )
    timeout_seconds = int(node.get("timeout_seconds", 1800))
    start = time.monotonic()
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    first_communicate = True
    while True:
        elapsed = time.monotonic() - start
        if elapsed >= timeout_seconds:
            process.kill()
            stdout, stderr = process.communicate()
            stdout_path.write_text(stdout, encoding="utf-8")
            stderr_path.write_text(stderr, encoding="utf-8")
            append_runtime_event(
                root,
                state,
                "node_progress",
                {"node": node["id"], "message": f"Codex CLI timed out after {timeout_seconds}s."},
            )
            raise TimeoutError(f"Codex CLI timed out for node {node['id']} after {timeout_seconds}s.")

        try:
            stdout, stderr = process.communicate(
                input=prompt if first_communicate else None,
                timeout=min(15, max(1, timeout_seconds - int(elapsed))),
            )
            break
        except subprocess.TimeoutExpired:
            first_communicate = False
            append_runtime_event(
                root,
                state,
                "node_progress",
                {
                    "node": node["id"],
                    "message": f"Codex CLI still running after {int(time.monotonic() - start)}s.",
                },
            )

    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    append_runtime_event(
        root,
        state,
        "node_progress",
        {"node": node["id"], "message": f"Codex CLI finished in {int(time.monotonic() - start)}s."},
    )

    if process.returncode != 0:
        raise RuntimeError(
            f"Codex CLI failed for node {node['id']} with exit code {process.returncode}. "
            f"See {stderr_path.relative_to(root)}"
        )

    missing = [item["path"] for item in outputs if not (root / item["path"]).exists()]
    if missing:
        raise RuntimeError(
            f"Codex CLI completed but did not create expected outputs for node {node['id']}: "
            + ", ".join(missing)
        )

    return AdapterResult(
        status=infer_result_status(root, node, outputs),
        summary=read_summary(last_message_path),
        artifacts=artifacts,
        logs={
            "prompt": str(prompt_path.relative_to(root)),
            "stdout": str(stdout_path.relative_to(root)),
            "stderr": str(stderr_path.relative_to(root)),
            "last_message": str(last_message_path.relative_to(root)),
        },
    )


def build_codex_prompt(
    *,
    root: Path,
    state: dict[str, Any],
    node: dict[str, Any],
    outputs: list[dict[str, Any]],
    cocos_projects: list[Path] | None = None,
) -> str:
    cocos_projects = cocos_projects or []
    agent_config = find_agent_config(root, state, node.get("agent", ""))
    prior_artifacts = collect_prior_artifacts(root, state, node)
    output_lines = "\n".join(
        f"- `{item['path']}`: {item.get('title', item['key'])} (key: {item['key']})"
        for item in outputs
    )
    checklist = "\n".join(f"- {item}" for item in node.get("checklist", []))
    agent_yaml = json.dumps(agent_config, ensure_ascii=False, indent=2)
    prior_text = "\n\n".join(prior_artifacts) or "无。"
    cocos_environment = render_cocos_environment(root, cocos_projects)
    execution_rules = build_execution_rules(node, cocos_projects)

    return textwrap.dedent(
        f"""
        你是 AgentFlow Studio 正在调用的真实 Codex CLI agent。请按当前节点要求直接写文件，不要只给建议。

        # Agent 配置

        ```json
        {agent_yaml}
        ```

        # 当前 Run

        - run_id: {state["run_id"]}
        - artifact_namespace: {state.get("artifact_namespace") or state.get("game_name", "")}
        - 用户目标: {state["goal"]}
        - 当前节点: {node["id"]} / {node.get("title", "")}
        - 节点目标: {node.get("objective", "")}

        # 本节点必须产出的文件

        {output_lines}

        # 节点检查标准

        {checklist}

        # 已有上游产物

        {prior_text}

        # 检测到的 Cocos 环境

        {cocos_environment}

        # 执行规则

        {execution_rules}

        完成后最终回复只写：已生成的文件列表和一句摘要。
        """
    ).strip()


def find_agent_config(root: Path, state: dict[str, Any], agent_id: str) -> dict[str, Any]:
    mounted = state.get("agents", {}).get(agent_id, {})
    copy_path = mounted.get("copy")
    if copy_path:
        path = root / copy_path
        if path.exists():
            return read_yaml(path)

    for path in (root / "configs" / "agents").glob("*.yaml"):
        data = read_yaml(path)
        if data.get("id") == agent_id:
            return data
    return {"id": agent_id}


def collect_prior_artifacts(root: Path, state: dict[str, Any], node: dict[str, Any]) -> list[str]:
    artifacts = []
    for key, relative_path in state.get("artifacts", {}).items():
        if node["id"] == "cocos_implementation" and key in {"implementation_report", "validation_report"}:
            continue
        path = root / relative_path
        text = path.read_text(encoding="utf-8")
        if len(text) > 6000:
            text = text[:6000] + "\n\n[内容过长，已截断]"
        artifacts.append(f"## {key}: `{relative_path}`\n\n{text}")
    return artifacts


def read_summary(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    return text[:1000]


def append_runtime_event(
    root: Path,
    state: dict[str, Any],
    event: str,
    payload: dict[str, Any],
) -> None:
    record = {
        "time": now_iso(),
        "event": event,
        "phase": state.get("phase"),
        "payload": payload,
    }
    timeline_path = root / "runs" / state["run_id"] / "timeline.jsonl"
    timeline_path.parent.mkdir(parents=True, exist_ok=True)
    with timeline_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def resolve_cocos_projects(root: Path, state: dict[str, Any], node: dict[str, Any]) -> list[Path]:
    if node.get("agent") != "cocos-implementation-agent":
        return []

    configured = state.get("cocos_project")
    subject = state.get("artifact_namespace") or state.get("game_name", "game")
    project = (root / configured).resolve() if configured else (root.parent / "game" / subject).resolve()
    if node["id"] in {"cocos_implementation", "build_validation"}:
        project.mkdir(parents=True, exist_ok=True)
    return [project]


def is_cocos_project(path: Path) -> bool:
    package_path = path / "package.json"
    if not package_path.exists():
        return False
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        package = {}
    return bool(package.get("creator")) or (
        (path / "assets").is_dir()
        and ((path / "settings").is_dir() or (path / "profiles").is_dir())
    )


def infer_result_status(root: Path, node: dict[str, Any], outputs: list[dict[str, Any]]) -> str:
    text = "\n".join(read_output_text(root, item["path"]) for item in outputs)

    if node["id"] == "cocos_implementation":
        if has_any(text, ["本次未落地 Cocos 源码", "只产出实现报告", "待源码写入", "无法创建 Cocos", "未写入 Cocos"]):
            return "needs_fix"

    if node["id"] == "build_validation":
        if has_any(text, ["是否返回 Needs Gameplay Revision | 是", "偏差来自规则缺口", "规则缺口 | 是"]):
            return "needs_revision"
        if has_any(text, ["玩法交接对齐 | 不通过", "偏差属于实现问题", "| 不通过 |", "P0 偏差", "IMP-"]):
            return "needs_fix"

    return "done"


def read_output_text(root: Path, relative_path: str) -> str:
    path = root / relative_path
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def has_any(text: str, markers: list[str]) -> bool:
    return any(marker in text for marker in markers)


def render_cocos_environment(root: Path, projects: list[Path]) -> str:
    if not projects:
        return "当前节点不需要 Cocos Creator 项目。"
    lines = []
    for project in projects:
        try:
            display = project.relative_to(root)
        except ValueError:
            display = project
        lines.append(f"- `{display}`")
    return "\n".join(lines) + "\n\n只能把上面的目录当作本 run 的 Cocos 目标项目；不要读取、复用或改写其他兄弟 Cocos 项目，除非用户显式配置。"


def build_execution_rules(node: dict[str, Any], cocos_projects: list[Path]) -> str:
    rules = [
        "- 必须用简体中文写面向用户审阅的 Markdown。",
        "- 必须创建或覆盖“本节点必须产出的文件”列表里的每个文件。",
        "- 不要写 mock、placeholder、示例占位字样。请根据用户目标和上游产物做真实设计、实现或验证。",
        "- 如果信息不足，使用“推荐默认值 + 需确认”推进，不要空等用户。",
        "- 表格要可执行，避免空泛描述。",
        "- 文件结尾附上“运行信息”表，包含 Run ID、Game Name、Node。",
    ]

    if node["id"] == "cocos_implementation":
        rules.extend(
            [
                "- 这是实现节点：必须先检查检测到的 Cocos 项目，并在该项目内完成玩法代码、场景、UI 或资源绑定落盘。",
                "- 如果目标 Cocos 项目为空或不是 Creator 项目，必须在该目录初始化或补齐首版可运行项目结构；不要改用其他旧项目。",
                "- 不要因为本节点有 implementation_report 输出就只写报告；报告必须汇总实际源码改动、项目路径和验证方式。",
                "- 允许修改检测到的 Cocos 项目内的 `assets/**`、`settings/**`、`profiles/**`、`package.json`、`tsconfig.json`、`progress.md`，以及本节点要求的报告文件。",
                "- 不要修改 AgentFlow Studio 的源码、配置、README、测试或上游玩法文档，除非它们就是本节点要求的输出文件。",
                "- 如果无法完成源码落盘，必须在报告中写明阻塞，并让本节点结果保持 needs_fix；不要声称实现完成。",
            ]
        )
    elif node["id"] == "build_validation":
        rules.extend(
            [
                "- 这是验证节点：必须针对检测到的 Cocos 项目运行可用的构建、预览或冒烟检查，并把命令、结果和证据写入验证报告。",
                "- 允许在检测到的 Cocos 项目内写入验证产物或修复明显的构建阻塞；不要改玩法规则。",
                "- 如果无法运行 Cocos 构建或预览，必须写清检测到的项目路径、尝试过的命令和失败原因。",
                "- 如果 P0 验收、玩法交接对齐或核心源码落地不通过，报告必须明确写出 needs_fix，不要把节点视为完成。",
            ]
        )
    else:
        rules.append("- 不要修改本节点输出以外的项目代码、配置、README、测试或其他文件。")

    if cocos_projects and node.get("agent") == "cocos-implementation-agent":
        rules.append("- 检测到的 Cocos 项目已作为 Codex CLI 额外可写目录传入，可以直接编辑其中源码。")

    return "\n".join(rules)


def render_artifact(
    state: dict[str, Any],
    node: dict[str, Any],
    output: dict[str, Any],
) -> str:
    key = output["key"]
    if key == "idea_intake":
        return render_idea_intake(state, node, output)
    if key == "loop_boundary":
        return render_loop_boundary(state, node, output)
    if key == "gameplay_rules":
        return render_gameplay_rules(state, node, output)
    if key == "flows_acceptance":
        return render_flows_acceptance(state, node, output)
    if key == "gameplay_handoff":
        return render_gameplay_handoff(state, node, output)
    if key == "implementation_report":
        return render_implementation_report(state, node, output)
    if key == "validation_report":
        return render_validation_report(state, node, output)
    return render_generic_artifact(state, node, output)


def render_generic_artifact(
    state: dict[str, Any],
    node: dict[str, Any],
    output: dict[str, Any],
) -> str:
    title = output.get("title", node.get("title", node["id"]))
    subject = state.get("artifact_namespace") or state.get("feature_name") or state.get("game_name", "")
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
| Artifact Namespace | {subject} |
| Node | {node["id"]} |
| Generated At | {now_iso()} |
"""


def render_idea_intake(state: dict[str, Any], node: dict[str, Any], output: dict[str, Any]) -> str:
    return f"""# {output["title"]}

> Mock draft. 真实项目中，这一页应由 Gameplay Design Agent 根据用户对话补全。

## 1. 一句话游戏想法

{state["goal"]}

## 2. 玩家承诺

| 项 | 草案 |
|---|---|
| 玩家角色 | 俯视角停车场中的抢位玩家 |
| 即时目标 | 在 NPC 抢走车位前选择并占领目标车位 |
| 核心幻想 / 感受 | 快速判断、抢先一步、把车位收入囊中 |
| 核心张力 | 空车位有限，NPC 会持续竞争，玩家需要在时间压力下选择 |
| 单局形态 | 60 秒左右的短局，结束后显示结果 |

## 3. 玩家动词

| 动词 | 在本游戏中的含义 | 为什么重要 |
|---|---|---|
| 观察 | 看空车位、NPC 占用、时间和分数 | 决定下一次点击目标 |
| 点击 | 选择一个空车位作为目标 | 核心输入 |
| 抢占 | 车辆移动并尝试占位 | 核心反馈 |
| 重开 | 结束后再次开始一局 | 支持快速迭代体验 |

## 4. 事实、假设、待决策

| 类型 | 项 | 为什么重要 |
|---|---|---|
| Fact | 目标是先设计玩法规则，再实现 Cocos 首版 | 决定先出 handoff |
| Assumption | MVP 是单机俯视角短局 | 降低首版复杂度 |
| Decision needed | 最终美术风格、关卡数量、是否有长期养成 | 不阻塞首版规则 |

## 5. 下一阶段输入

进入 `loop_boundary`，定义核心循环、MVP 范围和非目标。

{run_info(state, node)}
"""


def render_loop_boundary(state: dict[str, Any], node: dict[str, Any], output: dict[str, Any]) -> str:
    return f"""# {output["title"]}

> Mock draft. 真实项目中，这一页应由 Gameplay Design Agent 与用户确认。

## 1. 核心循环

```text
1. 玩家进入一局，看到停车场、时间、分数和可用车位。
2. 玩家观察空车位和 NPC 占用状态。
3. 玩家点击一个空车位。
4. 系统判断目标是否仍为空，并移动车辆。
5. 成功则加分并标记为玩家车位；失败则给出抢位失败反馈。
6. NPC 周期性占用空车位，玩家继续选择下一个目标。
```

## 2. 局内阶段

| 阶段 | 进入条件 | 玩家可做 | 系统变化 | 退出条件 |
|---|---|---|---|---|
| 准备 | 点击开始或重开 | 查看初始布局 | 初始化车位、时间、分数 | 计时开始 |
| 抢位 | 计时中 | 点击空车位 | 玩家/NPC 竞争占位 | 时间结束或胜负达成 |
| 结算 | 单局结束 | 查看结果、重开 | 停止 NPC 行为 | 玩家点击重开 |

## 3. MVP 范围

| 项 | MVP 包含? | 原因 |
|---|---:|---|
| 单机短局 | 是 | 可以验证核心抢位体验 |
| NPC 随机抢位 | 是 | 形成竞争压力 |
| 多关卡 | 否 | 首版不需要内容扩展 |
| 经济/养成 | 否 | 避免偏离核心循环 |
| 联网 PvP | 否 | 首版范围过大 |

## 4. 初始调参默认值

| 参数 | 默认值 | 理由 | 需确认? |
|---|---:|---|---|
| 单局时长 | 60 秒 | 适合快速试玩 | 是 |
| 车位网格 | 4 x 3 | 屏幕内容适中 | 否 |
| 初始 NPC 占位 | 3 | 保留足够选择空间 | 否 |
| 胜利目标 | 占到 5 个车位 | 有明确追求 | 是 |

## 5. 下一阶段输入

进入 `gameplay_rules`，把循环转成实体、状态、输入和规则。

{run_info(state, node)}
"""


def render_gameplay_rules(state: dict[str, Any], node: dict[str, Any], output: dict[str, Any]) -> str:
    return f"""# {output["title"]}

> Mock draft. 真实项目中，这一页应由 Gameplay Design Agent 精确化并消除 Rule gap。

## 1. 规则实体

| 实体 | 定义 | 关键字段 | 备注 |
|---|---|---|---|
| PlayerCar | 玩家车辆 | position, targetSpot, moving | 响应点击移动 |
| ParkingSpot | 车位 | index, state, owner | 核心争夺对象 |
| NPC | 竞争者 | claimInterval, targetSpot | 周期性抢位 |
| Round | 单局 | timeLeft, score, claimedCount, status | 控制开始/结束 |

## 2. 实体状态

| 实体 | 状态 | 进入条件 | 退出条件 | 玩家可见反馈 |
|---|---|---|---|---|
| ParkingSpot | empty | 初始化或释放 | 被玩家/NPC 选中 | 空车位样式 |
| ParkingSpot | targeted | 玩家点击空车位 | 车辆到达或失败 | 高亮目标 |
| ParkingSpot | mine | 玩家成功占位 | 本局结束 | 玩家颜色/标记 |
| ParkingSpot | npc | NPC 占位 | 本局结束 | NPC 颜色/标记 |
| Round | playing | 开始一局 | 时间结束或目标达成 | 计时与分数更新 |
| Round | ended | 胜负结算 | 重开 | 结算面板 |

## 3. 输入与动作

| 输入 | 动作 | 有效目标 | 条件 | 即时反馈 |
|---|---|---|---|---|
| 点击车位 | 选择目标 | empty spot | Round=playing 且玩家未移动 | 目标高亮，车辆移动 |
| 点击重开 | 重置单局 | Restart button | Round=ended 或 playing | 所有状态重置 |

## 4. 核心规则

| Rule ID | 触发 | 条件 | 结果 | 玩家反馈 | Unknowns |
|---|---|---|---|---|---|
| R1 | 玩家点击空车位 | 目标 state=empty | 目标变 targeted，车辆开始移动 | 车位高亮 | 无 |
| R2 | 玩家车辆到达 | 目标仍为空或 targeted | 目标变 mine，分数 +100 | 分数弹跳/车位变色 | 奖励数值待确认 |
| R3 | NPC 抢位计时触发 | 存在 empty spot | 随机 empty spot 变 npc | NPC 标记出现 | 随机权重待确认 |
| R4 | 目标途中被 NPC 占用 | 玩家目标变 npc | 玩家抢位失败，分数 -20 | 失败提示 | 扣分待确认 |
| R5 | 时间为 0 | Round=playing | 进入 ended | 显示胜负 | 无 |

## 5. 胜负与重开

| 结果 | 触发 | 结果状态 | 反馈 | 重开行为 |
|---|---|---|---|---|
| 胜利 | claimedCount >= 5 | Round=ended | 显示胜利和分数 | 重置全部车位 |
| 失败 | timeLeft=0 且 claimedCount < 5 | Round=ended | 显示失败和分数 | 重置全部车位 |

## 6. 下一阶段输入

进入 `flows_acceptance`，生成玩家流程、异常流程和验收用例。

{run_info(state, node)}
"""


def render_flows_acceptance(state: dict[str, Any], node: dict[str, Any], output: dict[str, Any]) -> str:
    return f"""# {output["title"]}

> Mock draft. 真实项目中，这一页应由 Gameplay Design Agent 根据规则表补齐可验收用例。

## 1. 玩家快乐路径

```text
进入一局 -> 查看空车位 -> 点击空车位 -> 车辆移动 -> 成功占位 -> 分数增加 -> 继续抢下一个车位 -> 达成胜利或时间结束
```

## 2. 系统判断流

```text
收到点击 -> 判断 Round 是否 playing -> 判断目标是否 empty -> 标记 targeted -> 移动车辆 -> 到达时再次判断目标 -> 成功/失败反馈
```

## 3. 状态转移表

| 实体 | From | Trigger | To | 玩家可见反馈 |
|---|---|---|---|---|
| ParkingSpot | empty | 玩家点击 | targeted | 高亮 |
| ParkingSpot | targeted | 玩家到达 | mine | 玩家占位样式 |
| ParkingSpot | empty | NPC 计时触发 | npc | NPC 占位样式 |
| Round | playing | 时间归零 | ended | 结算面板 |

## 4. HUD 与反馈需求

| 信息 | 玩家为什么需要 | 来源规则/状态 | 更新时间 |
|---|---|---|---|
| 时间 | 判断剩余压力 | Round.timeLeft | 每帧或每秒 |
| 分数 | 判断当前表现 | Round.score | 得分/扣分时 |
| 占位数 | 判断胜利进度 | claimedCount | 成功占位时 |
| 当前提示 | 理解成功/失败 | 最近动作结果 | 动作完成时 |

## 5. 验收标准

| Case ID | 场景 | 前置条件 | 操作 | 预期结果 | 优先级 |
|---|---|---|---|---|---|
| P0-1 | 成功抢位 | 存在 empty spot | 点击空车位 | 车位变 mine，分数 +100 | P0 |
| P0-2 | NPC 抢位 | Round=playing | 等待 NPC 间隔 | 一个 empty spot 变 npc | P0 |
| P0-3 | 失败结算 | timeLeft=0 且 claimedCount < 5 | 等待结束 | 显示失败面板 | P0 |
| P0-4 | 重开 | Round=ended | 点击 Restart | 时间、分数、车位重置 | P0 |

## 6. 下一阶段输入

输出 `04-gameplay-rules-handoff.md`，供 Cocos Implementation Agent 消费。

{run_info(state, node)}
"""


def render_gameplay_handoff(state: dict[str, Any], node: dict[str, Any], output: dict[str, Any]) -> str:
    return f"""# {output["title"]}

> Mock draft. 这是给 Cocos Implementation Agent 的交接物结构示例。

## 1. 实现摘要

实现一个单机俯视角抢车位 MVP：玩家在 60 秒内点击空车位抢占，NPC 会周期性占位，玩家达到目标占位数则胜利，否则时间结束失败。

## 2. 必须实现的玩法契约

| Area | Requirement |
|---|---|
| Player goal | 60 秒内占到至少 5 个车位 |
| Core loop | 观察空位 -> 点击目标 -> 车辆移动 -> 占位/失败反馈 -> 继续选择 |
| Inputs | 点击空车位、点击 Restart |
| Entities | PlayerCar, ParkingSpot, NPC, Round |
| States | empty, targeted, mine, npc, playing, ended |
| Win/loss | claimedCount >= 5 胜利；时间结束且不足 5 个失败 |
| Required feedback | 目标高亮、占位变色、分数/时间/HUD、结算面板 |

## 3. 优先实现规则

| Priority | Rule ID | Why first |
|---|---|---|
| P0 | R1 | 点击空车位是核心输入 |
| P0 | R2 | 成功占位验证核心循环 |
| P0 | R3 | NPC 抢位制造竞争压力 |
| P0 | R5 | 单局必须可结束和重开 |

## 4. Cocos 实现提示

| Gameplay need | Possible Cocos concept | Notes |
|---|---|---|
| 车位网格 | Node + UITransform 或 Graphics | MVP 可运行时创建 |
| 点击车位 | Node input event | 只允许 empty spot 被选中 |
| 分数/时间 | Label | HUD 常驻 |
| 车辆移动 | tween | 使用 delta/time 无关移动也可 |
| 结算 | Canvas panel | 胜利/失败共用面板 |

## 5. 首轮验证清单

| Check | Expected observable result |
|---|---|
| 点击空车位 | 目标高亮，车辆移动，最终车位变 mine |
| 等待 NPC 行为 | 空车位周期性变 npc |
| 时间结束 | 出现胜负结果 |
| 点击 Restart | 单局状态重置 |

## 6. 当前开放项

| Item | Recommendation | Blocks implementation? |
|---|---|---|
| 美术风格 | 首版使用几何图形或占位图 | 否 |
| 奖励数值 | +100 / -20 作为默认值 | 否 |
| 胜利目标 | 5 个车位 | 否，后续可调 |

{run_info(state, node)}
"""


def render_implementation_report(state: dict[str, Any], node: dict[str, Any], output: dict[str, Any]) -> str:
    return f"""# {output["title"]}

> Mock draft. 真实项目中，这一页应由 Cocos Implementation Agent 汇总源码改动。

## 1. 实现范围

| Handoff area | Implementation status | Notes |
|---|---|---|
| Player goal | pending | mock adapter 未执行真实 Cocos 改动 |
| Core loop | pending | 需要接入真实实现 adapter |
| HUD feedback | pending | 需要实现分数、时间、提示 |
| Restart | pending | 需要实现重开 |

## 2. 变更文件

| File | Purpose |
|---|---|
| pending | 真实 adapter 执行后填写 |

## 3. 偏差或 Rule gap

当前 mock adapter 未发现 Rule gap。

{run_info(state, node)}
"""


def render_validation_report(state: dict[str, Any], node: dict[str, Any], output: dict[str, Any]) -> str:
    return f"""# {output["title"]}

> Mock draft. 真实项目中，这一页应记录构建、截图、交互和控制台结果。

## 1. 验证摘要

| Check | Result | Evidence |
|---|---|---|
| Build or preview | not run | mock adapter |
| Canvas visible | not run | mock adapter |
| Click empty spot | not run | mock adapter |
| Restart | not run | mock adapter |

## 2. 下一步

接入真实 Cocos adapter 后，本节点应运行构建/预览，并把截图与错误日志写入 `runs/{state["run_id"]}/`。

{run_info(state, node)}
"""


def run_info(state: dict[str, Any], node: dict[str, Any]) -> str:
    subject = state.get("artifact_namespace") or state.get("feature_name") or state.get("game_name", "")
    return f"""## 运行信息

| 项 | 值 |
|---|---|
| Run ID | {state["run_id"]} |
| Artifact Namespace | {subject} |
| Node | {node["id"]} |
| Generated At | {now_iso()} |
"""


ADAPTERS = {
    "codex_cli": run_codex_cli_agent,
    "mock": run_mock_agent,
}
