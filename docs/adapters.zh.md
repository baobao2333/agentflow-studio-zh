# Adapter 接入说明

## 当前 adapter

当前只内置 `mock` adapter。

它会根据 workflow node 的 `outputs` 生成 Markdown 占位产物，用于测试：

- 流程是否能推进
- 人工节点是否能暂停和恢复
- 产物路径是否正确
- 看板是否能渲染

## 真实 Agent adapter 应该做什么

真实 adapter 可以替换 `agentflow_studio/adapters.py` 中的执行逻辑，但建议保持统一回执：

```json
{
  "status": "done",
  "summary": "本节点完成内容",
  "artifacts": {
    "gameplay_handoff": "docs/gameplay-workspace/demo/04-gameplay-rules-handoff.md"
  }
}
```

如果发现规则缺口：

```json
{
  "status": "needs_revision",
  "summary": "胜利条件和验收标准冲突",
  "rule_gaps": [
    {
      "source": "02-rules.md",
      "description": "胜利条件未定义时间结束时的平局处理"
    }
  ]
}
```

## OpenAI Agents SDK 接入建议

可以把每个 workflow node 映射为一次 Agents SDK run：

```text
node.agent -> Agent instructions
node.objective -> user task
state.artifacts -> input context
node.outputs -> expected files
```

Agents SDK tracing 可记录模型生成、工具调用和 handoff，适合补充本项目的 `timeline.jsonl`。

## LangGraph 接入建议

如果需要更强的可恢复和图执行，可以把 YAML workflow 编译为 LangGraph：

```text
workflow node -> LangGraph node
human_gate -> interrupt()
state.json -> graph state
timeline.jsonl -> stream/update log
```

本项目的 YAML 可作为 LangGraph 图的配置来源。

