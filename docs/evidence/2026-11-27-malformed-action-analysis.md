# 2026-11-27 三问中 malformed_action 的离线复核

入口：[o10a-real-three-turns.json](2026-11-27-o10a-real-three-turns.json)。本轮真实调用预算 0，只离线复核已有运行记录。结论：**具体被拒绝的原始动作未导出到本机，无法确定触发的是哪条解析条件，未定；但可确定解析失败发生在第 2 步 `submit_fact_selection` 的动作文本上，以及高血压回合最终到达提交、糖尿病回合未到达。**

## 一、成功与失败的逐步对照

病人智能体 `digital_patient_stemi` v9，协议 `dual`（`get_turn_context` → `submit_fact_selection`），模型 `spark-x2.6-4b`，`enable_protocol_repair_retry=true`（平台对 malformed 动作自动重试，见 `local-only/digital_patient_single.json` 同类配置）。

| 步骤 | 高血压回合（成功） | 糖尿病回合（HTTP 502） |
| --- | --- | --- |
| 步骤 1 `get_turn_context` | 成功（step_monitor 记 [1, 3643 入 / 120 出]） | 成功（[1, 3650 / 130]） |
| 步骤 2 `submit_fact_selection` | 连续 2 次 `malformed_action`（diagnostics 记 [2, 3915/180]、[2, 4006/92]）后第 3 次成功 | 连续 3 次 `malformed_action`（[2, 3930/215]、[2, 4021/97]、[2, 4112/147]）后 `loop_status=error` |
| 结果 | 渲染“有高血压10年。”，点击→完整回答 13.953 秒 | HTTP 502 无回答，约 13.6 秒 |
| 是否到达 `submit_fact_selection` | **是**（第 3 次尝试为合法动作并成功提交，后端据此渲染） | **否**（3 次均为 malformed，模型从未成功调用该工具） |

手术史回合干净成功（step 1+2，无 malformed，点击→完整回答 5.348 秒），作为同一协议下的对照。5 秒内出现有效回答 0/3，保留原阈值结论。

## 二、缺哪一段证据

本机可复核的只有：
- `o10a-real-20261127-015541/run.json`：浏览器计时、回答、request_id（无模型原文）；
- 归档报告 `platform` 段：平台运行日志的**聚合摘要**（step、reason、consecutive_errors、部分 token 计数），不是逐字输出；
- 本机 `turn_calls` 表只记 `tool`/`ok`，`detail` 为空（`local-only/run-nexent-20261122/integration.db`）。

**缺失**：每一步被判定 `malformed_action` 的模型原始输出文本（第 2 步要输出的 `<code>` 动作原文）。它在 Nexent 平台运行时日志里，未随结果导出到本机。因此**无法指出具体是 Markdown 围栏、JSON 调用、自然语言还是混排多动作触发的**，这部分结论保留未定。

## 三、触发的解析条件（动作协议）

依据 [patient-selection-prompt.txt](../patient-selection-prompt.txt) 与单次提交智能体导出的提示词，第 2 步要求一次响应只输出一个完整动作：

```
<code>
result = submit_fact_selection(turn_token="TOKEN", fact_ids=[...])
print(result)
</code>
```

以下任一都会被判 `malformed_action`：用 Markdown 围栏（```` ``` ````）代替 `<code>`；JSON/函数调用样式；自然语言代替代码；一个响应混排工具动作与 `<final_answer>`；动作缺 `turn_token`/`fact_ids` 或调用语法不完整；一个响应含多个动作块。平台 `enable_protocol_repair_retry=true` 会自动重试，本次观测到约 3 次（2 次重试）后进入 `error`。

## 四、证据支持的修复位置

1. **提示词（首要，可离线改）**：`docs/patient-selection-prompt.txt` 第 2 步（第 11–12 行）是本次失败点的直接约束。可在此处收紧“一次响应只输出一个 `<code>` 动作、给出 `submit_fact_selection(turn_token=..., fact_ids=[...])` 的固定骨架、禁止任何围栏/自然语言/JSON”，降低模型在第 2 步输出 malformed 动作的概率。因原始输出缺失，只能做通用加固，不能针对单一错误句式。
2. **平台配置（次要，实例侧）**：`max_steps`（病人 dual 当前 6 步）与 `enable_protocol_repair_retry` 决定 malformed 后的重试预算；可评估是否提高重试上限，但这只是兜底，不消除 malformed 根因，且改动在平台不在本仓库。

## 五、可复跑最小用例

`tests/test_nexent_action_format.py` 提供一个离线参考分类器 `classify(raw)`（按上表协议判定 `valid`/`malformed`），含 6 个正反例（合法动作、Markdown 围栏、自然语言、JSON 调用、混排 final_answer、缺参）。它不是平台解析器，仅用于：拿到平台原始输出后，把每一步 `malformed_action` 的原文喂给 `classify` 即可锁定具体触发条件。命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_nexent_action_format.py -q
```
