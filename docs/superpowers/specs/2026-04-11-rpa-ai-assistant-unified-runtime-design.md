# RPA 录制助手统一技能与运行时设计

## 摘要

本设计用于解决当前 RPA 技能录制助手的三个核心问题：

1. AI 录制步骤只要脚本执行成功就会被视为成功，缺少基于用户指令的录制态验收。
2. 前端暴露 `agent` 与非 `agent` 两种模式，增加了用户理解和使用成本。
3. 当前导出产物默认会被压平成纯脚本，但部分场景天然需要运行时语义判断与分支，无法稳定脚本化。

本方案采用“统一技能制品、脚本优先、Agent 按需介入”的设计：

- 对用户只暴露一个录制助手入口和一个技能运行入口。
- 技能内部统一使用一个 manifest 表达步骤，但步骤可区分为 `script_step` 与 `agent_step`。
- 录制阶段对 AI 生成的脚本步骤执行“执行 + 验收”闭环，只有通过验收的步骤才入库。
- 运行阶段默认只执行脚本步骤，不做全局 Agent 验收。
- 只有两类场景才启用 Agent：
  - 该步骤在录制阶段被判定为 `agent_step`
  - 脚本步骤执行失败，需要 Agent 做环境恢复后重试原脚本步骤

该方案保持 RPA 主路径的确定性，同时补足 AI 步骤的可靠性与复杂场景覆盖能力。

## 背景与问题

### 当前现状

从当前实现看：

- 后端 `/rpa/session/{session_id}/chat` 通过 `mode=chat|react` 分两条路径：
  - `chat` 路径主要执行单步结构化动作或 `ai_script`
  - `react` 路径由 `RPAReActAgent` 进行多轮观察与动作
- 前端 [RecorderPage.vue](D:/code/MyScienceClaw/RpaClaw/frontend/src/pages/rpa/RecorderPage.vue) 暴露 `agentMode` 开关，用户需要自己选择运行模式。
- 当前技能导出由 [skill_exporter.py](D:/code/MyScienceClaw/RpaClaw/backend/rpa/skill_exporter.py) 生成 `SKILL.md + skill.py`，默认把录制结果当成纯脚本技能处理。
- 当前 `assistant.py` 虽然已经支持结构化动作、页面快照、提取动作与 ReAct Agent，但“成功”仍然以执行层是否报错为主，而不是以“是否满足用户指令”为主。

### 当前问题

#### 问题 1：AI 录制步骤缺少录制态验收

用户输入“提取第一页第一条评论标题”时，系统可能生成一段能成功执行的脚本，但提取回来的值来自错误区域。当前路径中，只要脚本返回成功，就可能被直接记为有效步骤。

这会导致：

- 录制结果看似成功，但实际业务语义错误
- 导出技能在运行时重复稳定地产生错误结果
- 用户难以理解为什么“录制成功”但“结果不对”

#### 问题 2：模式切换暴露给用户

当前用户需要理解什么时候用普通模式，什么时候切到 Agent 模式。这种模式选择本质上是系统实现细节，不应转嫁给用户。

#### 问题 3：复杂语义步骤无法稳定脚本化

例如“判断用户评价是积极还是消极，并执行不同动作”这种步骤，依赖运行时语义判断与条件分支，不适合被强行压平为纯 Playwright 脚本。

#### 问题 4：脚本失败后缺少受控恢复机制

例如脚本点击失败，只是因为运行时多了一个遮挡弹窗。这类失败不应立刻导致技能失败，更合理的做法是由系统先尝试恢复环境，再重试原步骤。

## 目标

- 对用户提供一个统一的录制入口，不再暴露 `agent` 与非 `agent` 模式切换
- 对用户提供一个统一的技能制品与运行入口
- 录制阶段对 AI 生成的脚本步骤执行真实验收，而不是只看脚本是否执行成功
- 运行阶段保持脚本优先，不把整个技能执行改造成全面 Agent 化
- 仅在必要场景保留 `agent_step`
- 支持脚本步骤失败时由 Agent 自动进行环境恢复，并在恢复后继续重试原脚本步骤
- 保持与现有 Recorder V2、结构化动作、frame-aware 执行模型兼容

## 非目标

- 不将所有运行时步骤都交给 Agent 判定是否达成目标
- 不将所有技能统一改造成纯 Agent 运行
- 不移除现有 `ai_script` / 结构化动作能力，而是对其进行收敛与编排
- 不在本设计中引入任意复杂的通用工作流 DSL
- 不在第一阶段解决所有业务级推理问题，只聚焦录制准确性、统一入口与受控恢复

## 设计原则

- 脚本优先：对于用户明确录制出的可确定步骤，优先转成脚本或结构化脚本步骤
- Agent 按需：Agent 只用于脚本无法表达的业务步骤，或脚本失败后的环境恢复
- 录制先验收：AI 生成的录制步骤只有通过验收才进入步骤列表
- 运行最小智能化：运行时不做全局 LLM 验收，只做脚本执行、断言校验和必要恢复
- 恢复不接管主流程：恢复 Agent 只负责把环境恢复到脚本可继续执行的状态，然后把控制权交还给原脚本步骤
- 统一制品：用户侧只有一种技能，内部再表达脚本步与 Agent 步
- 增量演进：尽量复用现有 `assistant.py`、`generator.py`、`skill_exporter.py`、`RecorderPage.vue`

## 总体方案

### 对用户的产品形态

用户看到的体验统一为：

- 一个录制助手输入框
- 一个步骤列表
- 一个导出的技能
- 一个运行入口

用户不再选择模式。系统自动决定每次录制指令落成哪种内部步骤类型。

### 对系统的内部形态

技能内部步骤分为两类：

- `script_step`
  - 用于可确定执行的动作
  - 默认由脚本/结构化动作执行
  - 可配置轻量校验
  - 失败时可选进入恢复流程
- `agent_step`
  - 仅用于天然依赖运行时语义判断或分支控制的步骤
  - 由 Agent 在运行时观察、执行并判断该步骤自身是否完成

运行主路径为：

`script_step -> validate -> next`

失败时：

`script_step fail -> recovery_agent -> retry same script_step -> next`

仅当步骤本身为 Agent 步时进入：

`agent_step -> next`

## 录制阶段设计

### 统一录制产线

录制阶段统一采用如下流程：

`用户指令 -> 生成候选步骤 -> 尝试脚本化 -> 执行 -> 录制态验收 -> 入库`

如果无法稳定脚本化，则转为：

`用户指令 -> 生成候选步骤 -> 判定为 agent_step -> 执行/确认 -> 入库`

这意味着：

- 前端不再直接区分 `chat` 和 `react`
- 后端录制入口需要一个统一编排层负责分类、执行与验收

### 步骤分类规则

系统默认优先尝试生成 `script_step`。只有满足以下任一条件时才生成 `agent_step`：

- 指令本身包含运行时语义判断或分支，例如“判断评论积极/消极后分别执行”
- 当前 `structured_intent` 与脚本模板无法表达目标动作
- 候选脚本连续两次生成与执行都无法通过验收
- 步骤的成功条件依赖 LLM 运行时判断，而非 DOM、URL、页面文本或结构断言

其余情况下，优先生成 `script_step`。

### 录制态验收

对 AI 生成的 `script_step`，执行成功不等于录制成功。系统必须在录制阶段增加一步验收：

`候选步骤执行 -> 检查结果是否满足用户指令 -> 通过才保存步骤`

#### 验收目标

录制态验收只解决一个问题：

“这一步是否真的完成了用户刚刚要求的动作或提取目标？”

它不负责全局业务收尾，也不要求对整个技能目标做运行时闭环判断。

#### 验收方式

每个候选 `script_step` 生成时，同时附带最小验收描述：

- `expected_observation`
- `acceptance_hint`
- `expected_output_schema`

示例：

```json
{
  "type": "script",
  "action": "extract_text",
  "description": "提取最新评论标题",
  "validator": {
    "kind": "intent_check",
    "acceptance_hint": "提取结果应来自评论列表第一项标题",
    "expected_output_schema": "non_empty_string"
  }
}
```

#### 分动作验收策略

`click / press / navigate`

- 校验 URL、标题、目标区域、关键 DOM 状态至少有一项符合预期
- 例如“点击搜索结果第一项”，不能只看 click 不报错，需要确认页面发生了预期跳转或详情内容出现

`fill`

- 校验目标输入框值确实被写入
- 如果用户指令是完整动作的一部分，例如“搜索 xxx”，则 `fill` 本身不应提前算作完整意图完成，仍需依附后续动作

`extract_text`

- 先校验有结果
- 再校验结果来源与语义是否满足指令
- 优先使用结构化校验；仅当结构化校验无法判定时，才调用一次轻量 LLM 做录制态验收判断

#### 验收失败的处理

- 不将该候选步骤写入 session steps
- 把失败原因反馈给录制助手
- 允许系统自动重试一次生成与执行
- 若仍失败，则提示用户该动作不适合脚本化，或自动升级为 `agent_step` 候选

## 运行阶段设计

### 运行原则

运行时遵循以下边界：

- 普通 `script_step` 不使用 Agent 做目标完成判定
- 普通 `script_step` 只做执行和轻量校验
- 只有 `agent_step` 才由 Agent 负责“该步骤是否完成”
- 脚本失败时，Agent 只负责恢复环境，不接管主业务流程

### 运行状态机

统一技能运行时建议包含四个主状态：

1. `run_script_step`
2. `validate_script_step`
3. `recover_environment`
4. `run_agent_step`

状态流转如下：

- `run_script_step -> validate_script_step -> next`
- `run_script_step fail -> recover_environment -> retry same script_step`
- `validate_script_step fail -> recover_environment or fail`
- `run_agent_step -> next`
- `recover_environment fail -> skill fail`

### 脚本步骤的运行时校验

运行时脚本步骤的校验保持轻量，只做最小断言：

- 元素是否出现/消失
- URL 是否变化
- 文本是否存在
- 字段值是否写入
- 提取结果是否为空

运行时不对普通脚本步骤做额外 LLM 意图判断。

### Agent 步的运行时语义

`agent_step` 仅用于如下场景：

- 条件分支
- 语义判断
- 复杂开放式网页理解
- 录制阶段已明确无法脚本化的业务动作

对于 `agent_step`，Agent 需要负责：

- 观察当前页面
- 执行步骤内部动作
- 判断该步骤本身是否完成

这里的 Agent 判定边界仅限于当前 `agent_step`，不扩展为整个技能运行的全局完成判定。

## 自动恢复设计

### 恢复能力的目标

当脚本步骤失败时，不立即判定整个技能失败，而是优先尝试修复运行环境，使原脚本步骤可以继续执行。

典型场景包括：

- 弹窗遮挡导致元素无法点击
- 焦点丢失
- 页签切换错误
- 临时遮罩层覆盖
- 页面偏移到非预期区域

### 恢复 Agent 的职责边界

恢复 Agent 不是业务 Agent，只负责环境修复：

- 关闭弹窗
- 回到目标页签
- 取消遮罩层影响
- 恢复到脚本可继续执行的页面状态

恢复 Agent 不负责：

- 擅自跳过业务步骤
- 擅自完成原步骤之后的后续业务动作
- 在未明确授权下执行提交、删除、支付等高风险操作

### 恢复流程

对于启用恢复的 `script_step`：

1. 记录失败步骤、错误信息、当前页面快照
2. 调用恢复 Agent
3. 恢复 Agent 尝试修复环境
4. 恢复成功后，重新执行失败的原脚本步骤
5. 原步骤成功后，继续后续脚本流程

若恢复失败或重试仍失败，则整技能失败。

### 恢复约束

- 每个步骤默认最多恢复 1 次
- 恢复成功后必须回到“原步骤重试”
- 恢复 Agent 默认只允许低风险环境操作
- 高风险操作仍需显式确认机制

## 统一技能制品设计

### 制品形态

对外仍然保留当前技能目录形式，避免破坏技能系统兼容性：

- `SKILL.md`
- `skill.py`
- `manifest.json`

其中：

- `manifest.json` 表达统一技能计划、步骤类型、校验策略与恢复策略
- `skill.py` 作为运行时入口，读取 manifest 并执行统一技能运行时
- `SKILL.md` 继续承载技能元信息和用法说明

### Manifest 结构

示例：

```json
{
  "version": 2,
  "name": "review_processor",
  "description": "处理评论并提取结果",
  "goal": {
    "summary": "按录制好的流程完成评论处理"
  },
  "steps": [
    {
      "id": "step_1",
      "type": "script",
      "action": "click",
      "description": "打开评论列表",
      "script_fragment": "...",
      "validator": {
        "kind": "dom_change",
        "acceptance_hint": "评论列表区域出现"
      },
      "recovery": {
        "enabled": true,
        "max_attempts": 1
      }
    },
    {
      "id": "step_2",
      "type": "script",
      "action": "extract_text",
      "description": "提取最新评论标题",
      "script_fragment": "...",
      "validator": {
        "kind": "intent_check",
        "acceptance_hint": "结果来自评论列表第一项标题",
        "expected_output_schema": "non_empty_string"
      },
      "recovery": {
        "enabled": false
      }
    },
    {
      "id": "step_3",
      "type": "agent",
      "description": "判断评论情绪并执行分支动作",
      "goal": "如果评论积极则执行 X，否则执行 Y"
    }
  ]
}
```

## 前端交互设计

### 统一输入模型

前端录制页不再暴露模式切换，而是统一为：

- 用户输入“想录制的动作或目标”
- 系统返回“本次录制结果”

前端不需要让用户理解“当前是 chat 还是 react 模式”。

### 前端反馈内容

对每次录制结果，前端建议展示如下信息：

- 已录制为脚本步骤
- 已录制为 Agent 步骤
- 已录制为脚本步骤，支持失败自动恢复
- 录制态验收已通过
- 验收失败，未保存为步骤

### 运行时反馈内容

运行时建议展示：

- 当前执行步骤
- 当前是否处于恢复中
- 恢复后是否重试成功

但不需要把“恢复 Agent”概念强暴露给普通用户，只需自然语言说明系统正在尝试自动恢复环境。

## 后端改造方案

### 新增模块建议

建议新增以下模块：

- `backend/rpa/recording_orchestrator.py`
  - 统一录制编排入口
- `backend/rpa/step_classifier.py`
  - 判定候选步骤应落为 `script_step` 还是 `agent_step`
- `backend/rpa/step_validator.py`
  - 负责录制态验收与运行时轻量校验
- `backend/rpa/recovery_agent.py`
  - 负责环境恢复
- `backend/rpa/skill_runtime.py`
  - 统一技能运行时状态机
- `backend/rpa/skill_manifest.py`
  - 统一 manifest 结构定义与序列化

### 现有模块改造建议

[assistant.py](D:/code/MyScienceClaw/RpaClaw/backend/rpa/assistant.py)

- 保留页面快照、结构化动作、LLM 调用与现有 Agent 基础能力
- 把“单步候选生成”从当前 `chat/react` 分叉中抽离出来
- 不再承担用户可见的模式切换语义

[route/rpa.py](D:/code/MyScienceClaw/RpaClaw/backend/route/rpa.py)

- `/chat` 改为统一录制入口
- 移除显式 `mode=chat|react` 的前端使用路径
- 返回新增字段，告诉前端当前步骤的内部类型与验收状态

[generator.py](D:/code/MyScienceClaw/RpaClaw/backend/rpa/generator.py)

- 继续负责脚本片段生成
- 不再假设整个技能最终一定能压平成纯静态脚本

[skill_exporter.py](D:/code/MyScienceClaw/RpaClaw/backend/rpa/skill_exporter.py)

- 从导出纯脚本技能升级为导出统一 manifest 技能
- `skill.py` 变成 runtime entry，而不是全部业务逻辑的唯一载体

[RecorderPage.vue](D:/code/MyScienceClaw/RpaClaw/frontend/src/pages/rpa/RecorderPage.vue)

- 移除 `agentMode`
- 展示步骤类型、录制态验收结果、恢复能力说明

## 兼容性策略

- 已有普通录制步骤继续视为 `script_step`
- 已有 `ai_script` 步骤在迁移期可作为 `script_step` 的一种脚本载体
- 老技能如果没有 manifest，可继续按旧逻辑执行
- 新导出的技能使用 `manifest.json + skill.py` 组合
- 迁移期允许新旧技能共存

## 风险与权衡

### 风险 1：录制态验收增加延迟

AI 步骤在录制阶段需要“执行 + 校验”，会比现在更慢。

权衡：

- 这是必要成本，因为该延迟换来的是录制结果正确性
- 仅对 AI 生成步骤启用，不影响手动录制步骤

### 风险 2：恢复 Agent 过度扩权

如果恢复 Agent 不设边界，可能会演化成隐式主流程 Agent。

权衡：

- 必须严格限制其职责为环境修复
- 必须要求恢复后回到原脚本步骤重试

### 风险 3：步骤分类误判

有些步骤可能被错误标为脚本步或 Agent 步。

权衡：

- 默认脚本优先，但允许录制态验收失败后升级为 Agent 步
- 分类逻辑应可观测、可调试、可覆盖测试

## 测试与验收标准

### 单元测试

- `step_classifier`
  - 可正确区分脚本步与 Agent 步
- `step_validator`
  - 提取类步骤提取错值时不能通过验收
- `recovery_agent`
  - 能在限定边界内生成恢复动作

### 集成测试

- AI 提取步骤执行成功但结果错误时，不应写入步骤列表
- AI 指令为条件分支时，应落为 `agent_step`
- 脚本步骤点击失败且页面有遮挡弹窗时，应能自动恢复并重试成功
- 恢复失败时，应明确报错并停止执行

### 前端验收

- 录制页不再显示模式切换
- 用户连续发送录制指令时，系统可自动产出正确步骤类型
- 用户可看到录制态验收结果与恢复能力说明

## 实施顺序建议

建议按以下顺序落地：

1. 统一录制入口，移除前端模式切换
2. 新增 `recording_orchestrator` 与 `step_classifier`
3. 为 AI 录制步骤增加录制态验收
4. 引入统一 manifest 导出
5. 实现运行时 `script -> validate -> recover -> retry` 状态机
6. 最后补充 `agent_step` 运行时与新旧技能兼容收尾

## 最终结论

本方案不是把当前 RPA 录制助手全面 Agent 化，而是在保持脚本确定性主路径的前提下，补上两个关键能力：

- 录制阶段对 AI 生成脚本步骤做真实验收
- 运行阶段在脚本失败时提供受控的 Agent 恢复能力

最终产品形态为：

- 一个录制助手入口
- 一个统一技能制品
- 一个脚本优先、Agent 按需介入的运行时

这满足以下业务要求：

- 用户不再需要理解模式切换
- AI 录制步骤不会因为“脚本跑通”就被错误地当成成功
- 无法稳定脚本化的步骤可以保留为 `agent_step`
- 运行时脚本失败后可以自动恢复并继续按原脚本执行
