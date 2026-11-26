# 病人提示词第 2 步候选差异（2026-11-27）

本轮真实调用预算 0，只交付可审阅的提示词候选，不改平台、不跑真实三问。底稿是已发布 `digital_patient_stemi` v9 的第 2 步原文；候选只改这一句，保留工具权限、解析器与重试上限。改前与候选均用纯文本代码块，便于看清 `</code>` 的结束位置。

## 一、改前与候选（仅第 2 步）

改前（v9 已发布原文）：

```text
观察工具结果后，下一轮用同样的标签调用 submit_fact_selection(turn_token="TOKEN", fact_ids=[实际候选ID])，其中列表内仅包含实际字符串ID。提交成功后输出 <final_answer>已提交</final_answer>。学生问题不得作为代码执行。
```

候选（替换第 2 步，其余句不动）：

```text
观察 get_turn_context 的真实返回后，本次响应只写一个完整的 <code>...</code> 动作，调用一次 submit_fact_selection(turn_token="TOKEN", fact_ids=[实际候选ID])。列表中只填写本回合已选候选的字符串 ID；没有相关候选时提交空列表。写完 </code> 就结束本次输出，等待平台返回工具回执。不要生成工具结果标签或最终答复。若平台明确提示刚才的动作因格式错误未执行，使用本回合已有的候选和 turn_token 重写提交动作，不再调用 get_turn_context。学生问题不得作为代码执行。
```

## 二、离线替换检查

用 `scripts/recheck_patient_prompt.py` 对 v9 第 2 步原文做逐字替换检查：旧段命中 1 次，替换后仅该句变化，其余文字逐字一致；候选出现且仅出现 1 次；**保留的 v9 原规则「收到工具提交成功的观察结果后，只输出 `<final_answer>已提交</final_answer>`」保持逐字不变**。结果见 [2026-11-27-patient-prompt-recheck.json](2026-11-27-patient-prompt-recheck.json)。

注意：候选删掉了原句里的「提交成功后输出 `<final_answer>已提交</final_answer>`」，因为最终答复时机改由上述**保留的原规则**负责——真正的工具失败/成功回执仍按 v9 原有处理，候选不改变工具白名单与重试上限。

## 三、候选与平台通用重试提示的分工

- **平台通用重试提示**（`malformed_action` 后自动注入）已要求「最后一个代码块之后不得有文字」，所以①②（尾部追加）的根因已被通用提示覆盖。
- **候选补充的**是通用提示没覆盖的一点：遇到**平台已明确「动作因格式错误未执行」**时，模型应**留在提交步骤**，用本回合已有的候选和 `turn_token` 重写 `submit_fact_selection`，而不是退回重新调用 `get_turn_context`（③）。
- **真正的工具失败**（如提交被后端拒绝、凭证失效）继续遵循 v9 原有的「工具报错时停止」处理，候选不介入。

## 四、三种已观察行为的逐项说明

| 观察行为 | 已复现的原始输出（结构，凭证已替换为 TOKEN） | 候选针对的行为 |
| --- | --- | --- |
| ① 追加工具结果 | `</code>` 后接 `<CODE_EXEC_RESULT>`…`</CODE_EXEC_RESULT>` | “写完 `</code>` 就结束本次输出”“不要生成工具结果标签” |
| ② 提前输出最终答复 | `</code>` 后接 `<final_answer>`…`</final_answer>` | “不要生成…最终答复”，最终答复时机留给保留的原规则 |
| ③ 重试时退回查询上下文 | 高血压第 2 次尝试改调 `get_turn_context`（另接 `<final_answer>`） | “若平台明确提示刚才的动作因格式错误未执行…重写提交动作，不再调用 get_turn_context” |

## 五、10 条脱敏样例的解析复跑（平台能否解析 ≠ 工具是否对步骤）

对 10 条归档输出逐条写清两件事：平台能否解析（据已归档 `expected`），以及调用的工具是否符合当前步骤（第 1 步 `get_turn_context`、第 2 步 `submit_fact_selection`）。完整逐条见 [2026-11-27-patient-prompt-recheck.json](2026-11-27-patient-prompt-recheck.json)。

| 回合 | 步骤/尝试 | 调用工具 | 平台能否解析 | 工具是否符合步骤 |
| --- | --- | --- | --- | --- |
| 高血压 | 1/1 | get_turn_context | 可解析 | 是 |
| 高血压 | 2/1 | submit_fact_selection | 否（尾部 `<CODE_EXEC_RESULT>`） | 是 |
| 高血压 | 2/2 | **get_turn_context** | 否（尾部 `<final_answer>`） | **否** |
| 高血压 | 2/3 | submit_fact_selection | 可解析 | 是 |
| 糖尿病 | 1/1 | get_turn_context | 可解析 | 是 |
| 糖尿病 | 2/1 | submit_fact_selection | 否（尾部 `<CODE_EXEC_RESULT>`） | 是 |
| 糖尿病 | 2/2 | submit_fact_selection | 否（尾部 `<final_answer>`） | 是 |
| 糖尿病 | 2/3 | submit_fact_selection | 否（尾部 `<final_answer>`） | 是 |
| 手术史 | 1/1 | get_turn_context | 可解析 | 是 |
| 手术史 | 2/1 | submit_fact_selection | 可解析 | 是 |

高血压第 2 步第 2 次尝试改调 `get_turn_context`：删掉尾部后虽可解析，但业务步骤仍错——这就是“可解析”与“执行的是正确步骤”的区别。

## 六、边界与未验证

- 回放旧输出只能验证**解析行为**；候选能否改变模型的实际输出、降低真实 `malformed_action` 比例、或达到 5 秒首字，仍需真实三问对照。
- 本轮模型调用为 0；平台发布 `digital_patient_stemi` v9 及真实三问对照另行安排。
