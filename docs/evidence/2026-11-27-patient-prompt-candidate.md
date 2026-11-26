# 病人提示词第 2 步候选差异（2026-11-27）

本轮真实调用预算 0，只交付可审阅的提示词候选，不改平台、不跑真实三问。底稿是仓库 `docs/patient-selection-prompt.txt` 的 dual 第 2 步；`digital_patient_stemi` v9 的实际发布文本以平台导出存档为准（受保护目录），仓库模板与其存在差异，发布前请把本候选与 v9 存档逐句核对。

## 一、候选差异（仅改第 2 步，保留第 1/3 步、工具权限、解析器与重试上限）

**改前（仓库模板第 2 步）**

> 2. 观察真实工具结果后，下一轮只输出 <code>...</code> 动作调用 submit_fact_selection(turn_token="TOKEN", fact_ids=[实际候选字符串ID])，并打印返回结果。不得在这一响应中拼接 <final_answer>，也不得在尚未调用工具时声称成功。

**候选（替换第 2 步，其余句不动）**

> 2. 观察真实工具结果后，只调用一次 submit_fact_selection(turn_token="TOKEN", fact_ids=[实际候选字符串ID])，把动作写在完整的 <code>...</code> 内，输出在该动作的 </code> 处结束，之后不再追加任何文字、结果标签或 <final_answer>，然后等待真实工具回执。若本轮被要求重试且仍处于提交步骤，继续用同一 turn_token 完成 submit_fact_selection，不要退回重新调用 get_turn_context。

改动的具体语句：
- 把“下一轮只输出 <code>...</code> 动作调用…并打印返回结果”改为“**只调用一次** …输出在 **</code> 处结束**，之后不再追加…**等待真实工具回执**”（对应行为①追加结果、②提前最终答复）。
- 新增末句“**若被要求重试且仍处于提交步骤，继续完成 submit_fact_selection，不要退回重新调用 get_turn_context**”（对应行为③退回查询）。

## 二、三种已观察行为的逐项说明

| 观察行为 | 已复现的原始输出（结构，凭证已替换为 TOKEN） | 解析器为何拒绝 | 候选第 2 步如何消除 |
| --- | --- | --- | --- |
| ① 追加工具结果 | `<code>\nsubmit_fact_selection(turn_token="TOKEN", fact_ids=["f_hx_dm"])\n</code>` 之后接 `<CODE_EXEC_RESULT>`…`</CODE_EXEC_RESULT>` | 最后一个 `</code>` 之后仍有非空白内容（`_parse_code_action` 第 323 行） | “输出在该动作的 `</code>` 处结束，之后不再追加任何文字、结果标签” |
| ② 提前输出最终答复 | `<code>\nsubmit_fact_selection(turn_token="TOKEN", fact_ids=["f_hx_dm"])\n</code>` 之后接 `<final_answer>`…`</final_answer>` | 同上（`</code>` 后仍有尾部） | “不再追加…`<final_answer>`，然后等待真实工具回执” |
| ③ 重试时退回查询上下文 | 高血压第 2 次尝试改写为 `<code>\ncontext = get_turn_context(turn_token="TOKEN")\nprint(context)\n</code>`（另接 `<final_answer>`） | 尾部仍拒；且即使删尾部也只是“可解析”，并非本步该做的提交动作 | “若被要求重试且仍处于提交步骤，继续完成 submit_fact_selection，不要退回重新调用 get_turn_context” |

## 三、解析边界核对（可解析 ≠ 正确步骤）

用 `docs/evidence/2026-11-27-patient-action-parser-replay.json` 的 10 条结构样例区分两件事：

- **可以解析**：只要 `</code>` 之后没有非空白尾部，即使动作里带文字前缀、即使调用的是 `get_turn_context`（错步骤），也会被判 `ExecutableAction`。所以“解析通过”不代表“执行了正确步骤”。
- **正确步骤**：第 2 步必须且只能调用 `submit_fact_selection`；高血压第 2 次尝试删尾后虽可解析，但调用的是 `get_turn_context`，仍不是本步该做的业务动作。

候选只改第 2 步措辞，不改变工具白名单（仍仅 `get_turn_context`、`submit_fact_selection`）、不动平台解析器、不动 `enable_protocol_repair_retry` 与重试上限。

## 四、尚未验证

本候选基于“被拒原因=尾部追加”与“③退回查询”两条已复现证据，但**未验证**：改提示词后能降低真实 `malformed_action` 比例、或达到 5 秒首字。发布到 `digital_patient_stemi` v9 及真实三问对照另行安排；本轮模型调用为 0。
