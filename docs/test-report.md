# 测试报告

对应官方交付物“逐项测试报告”。报告只汇总**可复跑的工程测试**结果，正式验收所需的医学审核、真实学生评测和官方现场演示不在此列，也不能由本报告代替。下方 2026-11-25 补充为当前工作树复验；其后保留的 2026-11-13 及更早内容均为历史，不改写为本轮结果。

## 2026-11-25 当前代码复验（代码提交 35ab4c6，基于 ee0a09a）

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
