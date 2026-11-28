# test_total_deadline_interrupts_real_socket_waits 偶发失败调查（2026-11-29）

## 1. 既有日志保留了什么

- `local-only/full-pytest-20261129-delivery-tools.log`：**2 行**——FAILED 节点行 + 批次摘要。无断言、无 elapsed 实测值。原因：保存方式是 `pytest -q | tail`，完整失败输出未被保留（trickle 那次同理）。
- 唯一的历史实测值来自更早一轮写进测试注释的记录：trickle 变体失败时 `elapsed_ms=500`（见 `tests/test_nexent_deadlines.py::test_first_event_deadline_rejects_comment_trickle` 的注释）。

## 2. 计时构成（`backend/app/integration/nexent_client.py`）

- `deadline = started + total_timeout_s(0.3)`；`work_deadline = deadline - min(0.25, 0.3×0.25)=started+0.225`。
- 超时后 `_best_effort_stop(..., deadline)` 的等待被 `deadline`（0.3s）封顶；`elapsed` ≈ 0.3s + 事件循环/停发/worker 退出开销。
- 断言 `0.2 ≤ elapsed < 0.45` 只留 150ms 余量；套件内数百模块加载后的 GC/线程/事件循环抖动即可突破。

## 3. 已排除的解释

1. **MockTransport 路径**：本文件 5 个 MockTransport 用例无墙钟断言，历史从未在该层失败——失败只在真套接字夹具（`ThreadingHTTPServer` + `slow_stop`）上出现。
2. **单文件内顺序/并发**：整文件 ×3 全过（6.65–6.93s），文件内无累积效应。
3. **纯 CPU 负载**：单测 ×30（正常负载）+ ×20（4 个 CPU 占满进程）全部通过，wall 仅从 ~2.3s 升至 ~2.9s——纯 CPU 满载**不足以**触发；触发条件是完整套件运行时的累积状态（内存/GC/线程churn），本轮未复现。
4. **产品时限逻辑**：`NexentClient.run` 的 deadline/work_deadline/stop 预算关系与两变体的失败模式一致（stall 0.3s 截止 vs 0.9s 脚本卡顿；trickle 0.2s 首事件截止 vs 2s 总时长），未发现产品层缺陷。

## 4. 实验记录

| 实验 | 次数 | 条件 | 失败 | 证据 |
| --- | --- | --- | --- | --- |
| A 单测正常负载 | 30 | 逐次独立 pytest | 0 | `exp-normal-30.json`（wall 2.31–2.4s） |
| B 单测 4×CPU 满载 | 20 | 4 个占满进程 | 0 | `exp-load4-20.json`（wall 2.75–2.91s） |
| C 整文件 | 3 | 全文件 | 0 | 31 passed ×3（6.65–6.93s） |
| 修后单测 6×CPU 满载 | 20 | 加宽后边界 | 0 | `exp-postfix-load6-20.json`（wall 2.81–3.39s） |

累计 73 次、0 次复现；失败仅出现在完整套件历史运行中（2026-11-26 起 ≥3 轮：trickle×1、stall×2）。

## 5. 本轮处置（保留原时限契约）

- **契约不动**：`total_timeout_s=0.3` 仍强制生效并证明中断（0.7s 上界仍远低于 0.9s 脚本卡顿与 slow_stop）。
- **上界按 trickle 先例放宽**：`0.45 → 0.7`（150ms → 400ms 调度余量），理由与既有 trickle 修复（1.0s/2.0s）一致，写入注释。
- **失败诊断捕获**：断言消息直接携带 `elapsed=…s stop_outcomes=[…]`，下次失败即使日志只剩断言行也有实测值与停止结果。
- **流程改进**：全量日志改为**完整输出**保存（本轮起），不再 tail。

## 6. 剩余限制

- 套件内触发条件未复现，不能宣称"已修复根因"；只能给出已排除解释 + 容差/诊断改进。若套件内再次失败，断言行将直接给出 elapsed 与 stop_outcome，可判定是调度余量还是停止预算问题。
