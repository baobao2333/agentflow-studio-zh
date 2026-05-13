# 架构说明

## 设计目标

AgentFlow Studio 中文版要解决的是长流程 Agent 协作里的三个问题：

1. 人看不见 Agent 到了哪一步。
2. Agent 之间靠聊天传递状态，容易污染上下文。
3. 人类介入通常只剩最后批准，太晚。

所以本项目采用：

```text
State Graph + Artifact Handoff + Human Gate
```

## 三层结构

```text
Workflow
  定义节点、顺序、回退、人工审阅点。

Coordinator
  定义阶段门、交接标准和路由策略。

Agent
  定义某个专业 Agent 的职责、技能和边界。
```

## 为什么不让 Agent 自由聊天

自由聊天很灵活，但长流程中会带来三个副作用：

- 很难复现某个决策从哪里来。
- 很难判断哪个 Agent 拥有最终事实。
- 很难在中途人工介入后继续执行。

因此本项目要求 Agent 之间通过文件和结构化回执协作。

## 人类介入点

默认 Cocos 游戏流程提供两个人工节点：

```text
human_rules_review
  审阅玩法规则交接物。

human_playtest_review
  审阅首版实现与验证结果。
```

人工节点可以选择批准、退回实现、退回玩法规则。每次选择都会写入 `state.json` 和 `timeline.jsonl`。

## 可视化

每次运行都可以生成：

```text
runs/{run_id}/dashboard.html
```

看板包含：

- 当前状态
- 工作流图
- 已生成产物
- 时间线事件

默认 Mermaid 从 CDN 加载。离线部署时可以把 Mermaid 文件内置到模板里。

## 迁移到其他产品流程

迁移时通常只需要改三类 YAML：

```text
configs/agents/*.yaml
configs/coordinators/*.yaml
configs/workflows/*.yaml
```

例如 PRD 流程可以改成：

```text
idea_intake
  -> boundary
  -> rules
  -> page_spec
  -> human_prd_review
  -> engineering_handoff
```

运行内核不需要改。

