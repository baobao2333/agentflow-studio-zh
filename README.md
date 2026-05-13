# AgentFlow Studio 中文版

一个用于长流程、多 Agent、人类可介入协作的轻量工作流模板。

它不是又一个沉重的 Agent 平台，而是把复杂协作拆成四个可以迁移的部分：

```text
1. coordinator 配置
2. agent 配置
3. workflow 状态图
4. artifact + timeline + dashboard
```

默认示例是「玩法规则设计 Agent + Cocos 实现 Agent」协作完成游戏开发，但结构可以迁移到产品 PRD、网页应用、数据分析、内容生产等长流程任务。

## 适合什么场景

- 一个任务不是一轮就能完成。
- 你想看到 Agent 当前做到了哪一步。
- 你想在关键节点暂停、审阅、批准或退回。
- Agent 之间需要协作，但不希望自由聊天失控。
- 你希望 agent、coordinator、workflow 都能用配置迁移。

## 核心架构

```text
User
  -> Coordinator
  -> Gameplay Design Agent
  -> human_rules_review
  -> Cocos Implementation Agent
  -> build_validation
  -> human_playtest_review
  -> Done / Fix / Revise Rules
```

Agent 之间不直接互聊，而是通过产物交接：

```text
docs/gameplay-workspace/{game_name}/04-gameplay-rules-handoff.md
```

实现 Agent 如果发现规则缺口，不允许自己发明玩法，而是返回玩法修订节点。

## 快速开始

```powershell
cd agentflow-studio-zh
python -m pip install -e .
agentflow new --workflow configs/workflows/cocos-game-dev.zh.yaml --run-id demo --goal "做一个俯视角抢车位小游戏" --game-name parking-space
agentflow step runs/demo/state.json
agentflow step runs/demo/state.json
agentflow step runs/demo/state.json
agentflow step runs/demo/state.json
agentflow step runs/demo/state.json
```

第五次 `step` 会停在人类审阅节点。打开：

```text
runs/demo/dashboard.html
```

批准进入 Cocos 实现：

```powershell
agentflow resume runs/demo/state.json --decision approve --note "玩法规则通过，进入首版实现"
agentflow step runs/demo/state.json
agentflow step runs/demo/state.json
agentflow step runs/demo/state.json
```

也可以直接启动网页审阅服务，在浏览器里阅读产物并点击 approve / revise：

```powershell
agentflow serve runs/demo/state.json --port 8765
```

打开：

```text
http://127.0.0.1:8765/review
```

`serve` 会把 Markdown 按 UTF-8 渲染成网页，避免浏览器直接打开 `.md` 时猜错编码。

如果试玩后需要继续修实现：

```powershell
agentflow resume runs/demo/state.json --decision fix --note "点击反馈不够清楚，继续修实现"
```

如果要改玩法规则：

```powershell
agentflow resume runs/demo/state.json --decision revise_rules --note "胜利条件太单薄，回到规则建模"
```

## 目录结构

```text
agentflow_studio/             Python 轻量运行内核
configs/
  agents/                     Agent 配置
  coordinators/               Coordinator 配置
  workflows/                  长流程状态图配置
docs/                         架构说明
runs/                         每次运行的状态、时间线和看板
tests/                        最小回归测试
```

## 可配置点

- `configs/agents/*.yaml`：Agent 名称、职责、技能、输入输出、边界。
- `configs/coordinators/*.yaml`：阶段门、策略、交接要求。
- `configs/workflows/*.yaml`：节点、流转、人工审阅点、产物路径。
- `agentflow_studio/adapters.py`：替换节点执行方式，例如接 OpenAI Agents SDK、LangGraph、Codex CLI 或内部服务。

## 当前实现状态

当前版本提供一个确定性的 `mock` adapter，用来验证流程、文件交接、人类介入和可视化看板。它会为不同阶段生成不同结构的示例文档，但这些内容仍是 mock draft，不等同于真实 Agent 推理结果。真实接入 LLM 时，建议保留同样的状态结构和回执格式，只替换 adapter。

默认 Cocos workflow 已使用 `codex_cli` adapter，要求本机已登录 Codex CLI：

```powershell
codex --version
codex login
```

如果只想离线测试流程壳，可以把 workflow 里的 `adapter: codex_cli` 临时改成 `adapter: mock`。

## 参考资料

- LangGraph 官方文档说明它专注于长运行、有状态的 Agent 编排，并支持 durable execution、human-in-the-loop、persistence 等能力：https://docs.langchain.com/oss/python/langgraph/overview
- LangGraph interrupts 支持暂停图执行、保存状态并等待外部输入恢复：https://docs.langchain.com/oss/python/langgraph/interrupts
- OpenAI Agents SDK 提供 agent、handoff、human-in-the-loop、tracing 等能力：https://openai.github.io/openai-agents-python/
- OpenAI Agents SDK tracing 可以记录 LLM 生成、工具调用、handoff、guardrail 等运行事件：https://openai.github.io/openai-agents-python/tracing/

## 协议

MIT License。允许使用、复制、修改、分发和商业使用。
