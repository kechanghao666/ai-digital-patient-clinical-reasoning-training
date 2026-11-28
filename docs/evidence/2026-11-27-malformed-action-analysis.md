# 真实三问的动作解析复核（2026-11-27）

在 `nexent-runtime` 保留的 17:55:41–17:56:21 UTC（11 月 26 日）日志中找到了全部 10 条 `MODEL OUTPUT`。在内存中重建每条原文，交给同一容器安装的 `classify_model_output(protocol="code_action", finish_reason="stop")`，只解析，不执行。五条原文通过、五条复现 `malformed_action`。五个异常都来自 `_parse_code_action` 第 323 行：最后一个 `</code>` 之后仍有非空白内容。保留前缀和代码，只移除该尾部，五条均改判 `ExecutableAction`。三条问诊的网络结果仍以[原始报告](2026-11-27-o10a-real-three-turns.json)为准。

| 问题 | 第 2 步各次尝试，依时间顺序 | 学生结果 |
| --- | --- | --- |
| 高血压 | ① 提交事实后附带 `<CODE_EXEC_RESULT>` 等结果标签，拒绝。② 改写为再次查询上下文，附带 `<final_answer>`，拒绝。③ 只提交事实，解析通过。 | 有高血压10年。13.953 秒 |
| 糖尿病 | 三次代码均调用 `submit_fact_selection` 并选择 `f_hx_dm`。① 代码后附带 `<CODE_EXEC_RESULT>`。②③ 代码后附带 `<final_answer>`。三条都在执行前被拒绝。 | HTTP 502，约 13.6 秒后结束，无回答 |
| 手术史 | 一次提交直接通过。 | 没做过手术。5.348 秒 |

三个回合的第 1 步均解析通过。成功轮的第 2 步也说明带文字前缀的输出可以通过。单看工具名或参数不能复现上述拒绝；高血压第二次甚至提出了错误步骤的工具，删除尾部后虽然可解析，仍非正确的业务动作。仅将删减后的文本用作离线对照，线上继续拒绝整条不合规输出。真实测量为 5 秒内出现有效回答 0/3、60 秒内出现完整回答 2/3。本轮模型调用为 0。

## 离线复跑

[结构样例](2026-11-27-patient-action-parser-replay.json)包含 10 条原始输出的 UTC 时点、字符数、工具代码和尾部标签顺序。凭证统一替换为 `TOKEN`，省略生成的说明文字、标签内文本和内部引用。样例只保留复现判断所需的结构。解析器 SHA-256 为 `e39e9b9db7165a0917798a25ad3fde7ef8913fa444bc012db2a815837baad6e9`。在仓库根目录 PowerShell 运行：

```powershell
$parserReplay = Get-Content -Raw -Encoding utf8 scripts/replay_patient_action_parser.py
Get-Content -Raw -Encoding utf8 docs/evidence/2026-11-27-patient-action-parser-replay.json | docker exec -i nexent-runtime python -c $parserReplay
```

`tests/test_nexent_action_format.py` 的六例只说明本仓库对提示词格式的约定；它既不代表实例解析器，也不检查工具签名。不能据此把 Markdown、缺参数或多个动作块统称为平台的 `malformed_action`。旧训练库的 `turn_calls` 不用于证明本次隔离会话的工具执行。

## 开发交接

优先比较 v9 实际发布的第 2 步提示与平台的修复提示。候选改动应让本轮输出在 `</code>` 结束；结果由工具返回，收到成功回执才进入后续独立回复。当前仓库模板已经写明禁止混排最终声明。核对发布版后准备一个仅影响第 2 步的候选差异，保留工具权限、解析器及重试上限。现有证据确认了被拒原因；尚未验证改提示词可以提升真实成功率或达到 5 秒。

提示词候选已准备：[第 2 步候选差异](2026-11-27-patient-prompt-candidate.md)（只改第 2 步，覆盖追加结果、提前最终答复、退回查询三种行为；发布及真实三问对照另行安排）。
