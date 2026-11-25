# 测试报告

对应官方交付物“逐项测试报告”。报告只汇总**可复跑的工程测试**结果，正式验收所需的医学审核、真实学生评测和官方现场演示不在此列，也不能由本报告代替。当前接手基线为 `0b34f461131c2f5054032b828ed5889f4227cc55`。本报告分开记录该基线的 GitHub CI 与此前 `35ab4c6` 的本机代码复验；旧日期和结果保留原口径，不把较早结果冒充为在 `0b34f461` 上重跑。

## 2026-11-25：0b34f461 基线 GitHub CI

通过公开 GitHub Actions API 核验该 SHA 的两次 `Engineering checks` 工作流：Push run [45575084806](https://github.com/kechanghao666/ai-digital-patient-clinical-reasoning-training/actions/runs/45575084806) 与 PR run [45575096301](https://github.com/kechanghao666/ai-digital-patient-clinical-reasoning-training/actions/runs/45575096301) 均为 completed/success；两次的 `tests`、`dependency-audit` check 均为 success。此处记录的是 CI 对提交 `0b34f461` 的结果，不代表本机重跑，也不构成官方验收。

## 2026-11-25：当前工作树隔离备份/恢复复验（起点 0b34f461）

本轮新增的 Python 工程改动在 Windows 本机、Python 3.11.18（仓库 `.venv`）的临时目录上验证，未读取 `APP_DATA_DIR` 实际业务路径、默认 `backend/data`、密钥或 `local-only/`。测试样本与可复跑入口为 [`tests/test_data_backup_restore.py`](../tests/test_data_backup_restore.py)：自动创建合成 app/accounts/integration 数据库、封存训练与评分、教师病例副本/来源/操作审计、科室当前及 `.previous` 配置，并从外置 `INTEGRATION_DB_PATH` 恢复到新的临时目录；仓库病例文件仅只读作为合成草稿模板。临时数据在 pytest 临时目录中，不生成仓库快照证据或使用业务数据。

| 验证 | 实际命令/范围 | 结果 | 边界 |
|---|---|---|---|
| 备份恢复专项（最终） | `./.venv/Scripts/python.exe -m pytest -q tests/test_data_backup_restore.py` | **6 passed / 0 failed，3.23s** | 含文档 CLI 的 subprocess 往返、恢复暂存故障注入；全部使用自动临时目录与合成数据 |
| 全量测试 | `./.venv/Scripts/python.exe -m pytest -q` | **740 passed / 0 failed，153.08s** | 本机回归，不是新提交 CI、生产/异机部署或真实数据恢复 |
| Python 编译 | `./.venv/Scripts/python.exe -m compileall -q backend/app frontend tests scripts` | 通过 | Python 语法编译 |
| 前端检查 | 未运行 `npm run typecheck` / `npm run build` | 未重跑 | 本轮无前端源码或构建产物改动；此前的前端结果属于 `35ab4c6` |

异常过程保留：最初专项运行 4 项时 2 项失败，原因是 WAL 模式数据库备份副本在恢复前校验时生成 `integration.db-shm`，使严格 payload 清单拒绝该文件；已仅将快照副本改为独立 DELETE journal mode（不改源库），随后专项 4 项通过。新增 CLI 端到端探针与恢复暂存阶段故障注入后，最终专项 6/6、全量 740/740 通过。损坏快照校验、非空目标保护、暂存失败不发布且清理 staging、未确认停机和源数据目录出现上传图片目录均有受控错误回归。

本段本机测试结果记录时，工作树尚待提交/推送；`0b34f461` 的上游 CI 不能代替本次改动后的 CI。依赖审计的本机 `pip_audit` 仍不可用；真实模型调用 0 次；无外部服务请求、部署或医学内容修改。之后的推送与 GitHub CI 复核见紧接着的状态记录。

## 2026-11-25：c6b1d87 GitHub CI 复核

公开 GitHub Actions API 对完整 SHA `c6b1d8788455d88a1d2adfec306d669cd66f1327` 返回两次 `Engineering checks` 成功：Push run [45586549113](https://github.com/kechanghao666/ai-digital-patient-clinical-reasoning-training/actions/runs/45586549113) 与 PR run [45586562357](https://github.com/kechanghao666/ai-digital-patient-clinical-reasoning-training/actions/runs/45586562357) 均为 completed/success；两次的 `tests` 和 `dependency-audit` check 均 completed/success。此为该工程提交的 CI 结果，不是官方验收，也不是本机重新执行结果。

## 2026-11-25：此前本机代码复验（代码提交 35ab4c6，基于 ee0a09a）

### 环境与样本

- Windows 本机；Python **3.11.18**（仓库 `.venv`）；Node **v24.18.0**；npm **11.16.0**。
- 被测源码对应本地代码提交 `35ab4c6`，含病例来源追溯/操作审计及教师病例页改动；病例、临床规则、数据库和密钥均未作为本轮测试输入或输出提交。真实模型请求 **0 次**。
- 以下结果只表示工程自动化/本机替身，不表示 CI、医学审核、真实模型、真人学生、干净机器或官方验收通过。

### 命令与实际结果

| 验证 | 实际命令 | 结果 | 边界 |
|---|---|---|---|
| 病例专项 | `./.venv/Scripts/python.exe -m pytest -q tests/test_teacher_cases.py tests/test_case_copy_publish_visibility.py tests/test_case_training_stop.py` | **22 passed / 0 failed，6.40s**（最终代码复验） | 使用临时测试目录；模拟审核值明确为测试夹具；不证明审核人资质 |
| 全量后端 | `./.venv/Scripts/python.exe -m pytest -q` | **734 passed / 0 failed，157.58s**（最终代码复验） | 本机离线回归；没有运行真实模型或临床评判 |
| 前端类型 | 在 `web/` 执行 `npm run typecheck` | 通过 | TypeScript 静态检查 |
| 前端构建 | 在 `web/` 执行 `npm run build` | 通过；连续两次构建 `dist/assets/main.js` SHA-256 均为 `591cb95c0bc9c01d114072435dd2bd0b00da2356b817a527b7450f2eaea9740f` | 构建产物与源码可重复；没有将其解释为生产部署 |
| Python 编译 | `./.venv/Scripts/python.exe -m compileall -q backend/app frontend tests scripts` | 通过 | 语法编译检查 |
| 学生问诊浏览器探针 | `BASE_COMMIT=ee0a09a OUT=../docs/evidence/2026-11-25-case-lifecycle node e2e/ask-saved-answer.mjs`（工作目录 `web/`） | **25 项通过**，无未捕获脚本错误；报告 [`docs/evidence/2026-11-25-case-lifecycle/run.json`](evidence/2026-11-25-case-lifecycle/run.json) | 合成病例、临时数据库、loopback fake Nexent/MCP；检查失败、重放、迟到响应和页面展示，不测供应商时延 |
| 停用任务浏览器探针 | `node e2e/stopped-task.mjs`（工作目录 `web/`） | 通过 | 固定 API 回包；确认阻止新开训并保留在途续训/封存复盘，不是完整真实师生账号流程 |
| Python 依赖审计 | `./.venv/Scripts/python.exe -m pip_audit --strict --require-hashes -r requirements.lock` | **未运行**：本机错误为 `No module named pip_audit` | 不是零漏洞结论；CI 对当前未提交工作树的依赖审计未运行 |
| 差异格式 | `git --no-pager diff --check` | 通过 | Git 提示部分工作树 LF 将转换为 CRLF；无空白错误 |

没有运行 `node e2e/smoke.mjs`：该完整流程需要教师/学生登录配置，本轮未读取或使用任何本机凭据。没有重跑 `scripts/run_engineering_samples.py`、正式 20 轮问答、模型性能、真实 Nexent、异机部署、备份恢复或专家评测。连续两次前端构建产物哈希一致，已确认当前 `web/dist/assets/main.js` 与本轮源码构建输出一致。

## 历史报告：2026-11-13

报告日期为 2026-11-13，测试环境和数字仅代表当时工作树，见下方历史内容。历史报告仍保留当时表述，不替代本轮结果。

## 一、测试环境

自动测试运行在隔离的 Linux 沙箱中。Python 版本为 3.10，测试时临时补了 `asyncio.timeout_at` 以兼容 3.10，这个补丁不进入项目。项目目标版本是 Python 3.11，CI 也用 3.11；正式交付前须在 3.11 环境和 CI 上重跑一次。患者智能体使用本地规则模拟（`NEXENT_MODE=simulator`），真实 Nexent 平台的联调记录见 `docs/evidence`。

## 二、结果汇总

| 类别 | 入口 | 结果 |
|---|---|---|
| 单元、接口与页面回归 | `python -m pytest -q` | 574 项全部通过（含班级/任务/改密 `test_roster.py`、检查目录 `test_exam_catalog.py`、SQLite 并发 `test_sqlite_concurrency.py`） |
| 浏览器端到端（教师建班→学生从目录自主开单完成任务→教师复核） | `node web/e2e/smoke.mjs` | 13/13 步通过，无前端脚本错误；截图与 run.json 见 `docs/evidence/exam-catalog-monitor-20261114/` |
| 编译检查 | `python -m compileall -q backend/app frontend tests scripts` | 通过 |
| 十二组工程样本（正常、延误、边界） | `scripts/run_engineering_samples.py` | 12/12 通过，原业务库未改动 |
| 问诊评测：主评测集 100 条 | `scripts/run_dialogue_eval.py` | 通过率 100%，召回 100%，精确 100%，无泄露，三次重复一致（调参集） |
| 问诊评测：留出集 40 条 | `--set evaluation/dialogue_holdout_set.json` | 通过率 70%，召回 86%，精确 82%，无泄露，三次重复一致 |
| 常见问法覆盖（40 问 × 5 科） | `scripts/eval_dialogue_coverage.py` | 每科 31–34 问可披露事实，无“无法理解” |

## 三、与课题1指标的对应

| 官方指标 | 本轮工程证据 | 结论 |
|---|---|---|
| 只回答被问及的授权事实、结果一致（20 轮抽检≥95%） | 两组评测共 140 条，零泄露、三次一致；留出集通过率 70% | 规则层不泄露；对未见问法的回答完整度仍不足，正式抽检未做 |
| 五例全部完成问诊→开单→回报→病历→评分 | 十二组样本中的五个正常闭环 | 工程通过；真实模型五科完整闭环待做 |
| 五环节分项、扣分、建议、回放 | 评分与回放回归测试；教师看板按五环节汇总 | 工程通过；专家一致性待验证 |
| 明显错误决策的演进 100% 触发 | 延误规则与处置取消的样本 | 已配置的分支通过；其他错误分支尚未定义和审核 |
| 相同输入 3 次方向一致 | 评测脚本逐条重复 3 次 | 规则层 100%；真实模型生成需另行判定 |
| 首字≤5 秒 | 规则层进程内耗时为毫秒级 | 真实模型此前实测 5.6–26.9 秒，**未达标** |
| 评分与专家一致率≥85% | 无 | 未开始，须真实学生和专家参与 |

## 四、本轮新增的测试

| 文件 | 覆盖内容 |
|---|---|
| `tests/test_natural_dialogue.py` | 意图路由、误匹配屏蔽、疾病名不串答、固定非事实回答、新病例版本的合法性与防泄露、覆盖下限、评测集回归门槛 |
| `tests/test_review_fixes.py` | 本机代理登录不共用限流、外部来源仍限流、超长密码不回显、单个证据框列多条事实的计分 |
| `tests/test_dashboard_coverage.py` | 问诊结构进度只依据提问文本、详情接口返回进度、教师看板的聚合结果与权限、看板只读、账号目录不含密钥、看板跳转评分复核、学生端显示进度 |

## 五、已知限制

规则问诊仍有四类问题：同一句问多个方面时多答、治疗类问题被当成病史、同义说法覆盖不全、多轮指代无法处理。看板展示的是原规则分，未合并教师裁定，也不是正式成绩。本轮没有在真实浏览器中截图；页面行为由 Streamlit AppTest 验证，并在本机启动服务后做了接口级实跑。
