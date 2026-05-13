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
    return f"""## 运行信息

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
