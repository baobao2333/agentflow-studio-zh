# 参考资料

本项目的架构选择参考了以下公开资料。

## LangGraph

- LangGraph overview: https://docs.langchain.com/oss/python/langgraph/overview
  - 参考点：长运行、有状态 workflow、durable execution、human-in-the-loop、persistence。
- LangGraph interrupts: https://docs.langchain.com/oss/python/langgraph/interrupts
  - 参考点：在图节点中暂停执行、保存状态、等待外部输入并恢复。
- LangGraph time travel: https://docs.langchain.com/oss/python/langgraph/use-time-travel
  - 参考点：长流程调试和从历史状态重放的思路。

## OpenAI Agents SDK

- OpenAI Agents SDK: https://openai.github.io/openai-agents-python/
  - 参考点：agent、handoff、human-in-the-loop 和 tracing 的基础能力。
- Agents SDK tracing: https://openai.github.io/openai-agents-python/tracing/
  - 参考点：记录 LLM 生成、工具调用、handoff、guardrail 和自定义事件。

## 设计取舍

本项目当前没有直接依赖 LangGraph 或 OpenAI Agents SDK，而是先把长流程的状态、产物、人类介入和可视化协议做成可配置内核。这样可以先验证协作模型，再按需要接入更强的图执行或 Agent runtime。

