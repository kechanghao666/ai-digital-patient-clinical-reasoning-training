# 测试报告

对应官方交付物“逐项测试报告”。报告只汇总**可复跑的工程测试**结果，正式验收所需的医学审核、真实学生评测和官方现场演示不在此列，也不能由本报告代替。当前干净克隆演练的代码基线为 `513073e125e4ef1f5f0acc979c043827a6dd2f8e`。本报告区分提交对应的 GitHub CI、干净克隆中的本轮实测和此前提交的本机结果；历史日期与数字保留原口径，不把旧结果冒充本轮复验。官方需求包原文仍未取得，工程测试与 CI 均不代表官方验收。

## 2026-11-25：513073e 干净克隆部署与恢复演练

演练在 Windows 11（10.0.26300 x86_64）自动创建的临时目录和从远端分支克隆的干净副本完成；代码基线 `513073e125e4ef1f5f0acc979c043827a6dd2f8e`，Python 3.11.18、Node v24.18.0、npm 11.16.0。Python 运行时与虚拟环境位于临时根目录。只使用合成账号、病例草稿、训练、评分、审计和科室配置；使用 `APP_AUTH_MODE=local`、`NEXENT_MODE=simulator`，真实模型调用 0 次，未读取凭据或现存数据。完整记录及边界：[演练证据 JSON](evidence/2026-11-25-clean-copy-deployment.json)。

| 验证 | 实际命令/操作 | 结果 | 边界 |
|---|---|---|---|
| Python 锁定安装 | `python -m pip install --require-hashes -r requirements.lock`；`python -m pip check` | 安装成功；无依赖冲突 | 新克隆、新 venv；Python 3.11.18 |
| 前端安装与构建 | 在 `web/`：`npm ci --no-audit --no-fund`、`npm run typecheck`、`npm run build` | 均通过；`web/dist` 与提交内容一致，`main.js` SHA-256 为 `591cb95c0bc9c01d114072435dd2bd0b00da2356b817a527b7450f2eaea9740f` | 仅同机干净克隆的前端重建，不是部署到独立机器 |
| 服务与浏览器流程 | loopback 启动 FastAPI 同源服务；`GET /health`、`/`、`/api/auth/config`；在 `web/` 执行 `node e2e/smoke.mjs` 并使用本轮临时合成账号 | 健康/主页/local 认证通过；师生浏览器流程 **13/13** | 模拟器，不请求供应商；合成教师只复核合成评分，未做专家评价 |
| 病例复制 | 合成教师 API 复制 `STEMI-001@3` 为 `SYNTH-DEPLOYMENT-CASE` 草稿 | 来源保持，审计只有 `draft_saved` | 未医学送审、未发布；病例事实未修改 |
| 专项测试 | `python -m pytest -q tests/test_data_backup_restore.py` | **6 passed in 2.84s** | 干净克隆；自动临时合成数据库 |
| 全量测试 | `python -m pytest -q` | **740 passed in 190.94s** | 干净克隆代码回归；不是生产或真人评测 |
| Python 编译 | `python -m compileall -q backend/app frontend tests scripts` | 通过 | Python 语法检查 |
| 离线备份/恢复 | 停止 API、MCP 和所有写入者；按 `docs/deployment.md` 的 CLI 命令将合成 `APP_DATA_DIR` 与外置 `INTEGRATION_DB_PATH` 备份，再恢复到从未存在的新目录 | 清单 7 项；封存训练事件/评分、集成数据、教师病例版本/来源/审计、科室当前/上一版配置一致；恢复后旧会话为 0、旧 bearer 401、新登录 200 | 同机临时目录、合成数据；离线停止写入 |
| 拒绝/保护场景 | 缺少 `--confirm-services-stopped`、篡改 `app.db` payload、恢复至已有目录 | 均返回受控错误；未创建/覆盖目标，已有 sentinel 保持；专项另覆盖 staging 故障清理和未知上传目录拒绝 | 不对任何既有业务目录做故障注入 |
| 证据清理 | 临时根目录 `/tmp/clinical-deploy-ythoe4vr`（当前运行环境 `TEMP`） | 已核对真实路径、子目录和无运行进程后清理；原始日志/截图未保留，仓库仅留 JSON 汇总 | 只清理本轮自动创建的临时根，未触碰其他临时数据 |

早期演练驱动的失败与重试记录保存在证据 JSON；问题均在业务/恢复验收前暴露于临时 harness，最终全新 `run-05` 的 39 项驱动断言通过。它们不是产品测试失败。演练覆盖的是同机临时干净副本，不能表述为异机部署；未覆盖生产、在线热备份、Windows ACL、监控告警、备份保留轮转或真实业务恢复。前端类型/构建本轮已运行，不是未重跑。

## 2026-11-25：文档/证据提交 b9960ad 的 GitHub CI

完整 SHA `b9960ad3659c5b0492aa5979efd557a410a4b0f5` 的 Push run [45598856117](https://github.com/kechanghao666/ai-digital-patient-clinical-reasoning-training/actions/runs/45598856117) 与 PR run [45598868887](https://github.com/kechanghao666/ai-digital-patient-clinical-reasoning-training/actions/runs/45598868887) 均 completed/success；两次的 `tests` 与 `dependency-audit` 均通过。CI 的 `tests` 工作流包含 pytest、工程样本脚本和 Python 编译。该文档提交未改源码；本机专项 6 项、全量 740 项、前端类型/构建和浏览器流程的结果来自 `513073e` 干净克隆，不冒称在 `b9960ad` 工作区重跑。CI 不等于官方验收。

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

## 2026-11-25：账号管理操作审计（起点 `6d76f95` 的当前工作树）

本节只记录 `6d76f95bd06dea0c9f335898e097f8be56a154f0` 之后的本地工作树改动验证；基线提交本身不含这些改动。Windows 本机仓库 `.venv` 的 Python 3.11.18。测试均由 pytest 自动创建临时合成数据库/账号，不访问既有数据目录、模型凭据或供应商；真实模型调用 0。

| 验证 | 实际命令 | 结果 | 边界 |
|---|---|---|---|
| 账号、教师班级、备份恢复专项 | `./.venv/Scripts/python.exe -m pytest -q tests/test_accounts.py tests/test_roster.py tests/test_data_backup_restore.py` | **18 passed / 0 failed，16.56s** | Windows 临时合成账号与 SQLite；含只读审计 CLI、无权限教师拒绝、事务回滚、重复停用幂等和账号审计备份恢复一致性 |
| 全量离线回归 | `./.venv/Scripts/python.exe -m pytest -q` | **743 passed / 0 failed，167.67s** | 本机工作树结果，不是基线提交 CI 或官方验收 |
| Python 编译 | `./.venv/Scripts/python.exe -m compileall -q backend/app frontend tests scripts` | 通过 | 编译检查；前端源码未改 |
| 前端类型/构建 | 未运行 `npm run typecheck` / `npm run build` | 未重跑 | 本轮未改前端或构建产物 |

专项结果示例（脱敏）：`{"occurred_at":"<UTC>","operation":"password_reset","target_account_id":"<redacted>","actor_id":"<authenticated-teacher-id>","entry":"teacher_api"}`。教师对非本班学生的重置/停用/启用请求均返回 404；账号状态、原会话和审计条目保持不变。注入 `_append_account_audit` 写入异常后，新账号插入回滚；重置密码和停用状态、会话撤销一并回滚，旧 bearer 仍可认证。审计恢复后与快照一致，但旧登录会话被清除。

测试执行初期有两次夹具接线失败：一是预期审计列表未从快照构造函数返回，二是新增返回值未同步到第二个调用方；均为测试代码错误，修复后目标专项 **18/18** 通过。未覆盖管理员角色、学生自助改密/登录行为、全站敏感数据访问审计、生产/异机 ACL、安全运维配置、临床审核或真实学生评测；这些仍开放。

## 2026-11-25：M1 参考科室目录与师生流程复验（`962d318` 未提交工作树）

本轮开始时 HEAD 与 `origin/codex/feedback-protocol-fix` 均为 `962d31836e672fc98c8ee88e431a52428fde5942`；工作区已有 M1 代码和测试修改。本节是其后的未提交工作树验证，不归为 `962d318` 提交结果。Windows 10.0.26300 x86_64、Python 3.11.18、Node v24.18.0、Edge 158.0.4370.33；仅临时合成配置/账号、`APP_AUTH_MODE=local`、`NEXENT_MODE=simulator`，真实模型调用 0，未使用或读取凭据、现存数据库和病例原件。

### 计数口径

- 参考目录：代码共 **34 个 L1 / 120 个有明确目录编码的命名 L2**。不称 34/143 全量；来源与逐项名称、排除和待核理由见 [`科室目录来源与范围.md`](科室目录来源与范围.md)。
- 合成院校启用：**3 个 L1 / 8 个 L2**，仅为本轮临时配置，不代表院校实际建制。
- 病例：只读扫描 11 个版本文件并按 `case_id` 取最新版本，得 5 个唯一病例；旧 `specialty` 兼容映射后覆盖 2 个有病例 L1（03、04）和 5 条不同 L1/L2 路径。随仓库最新病例均为 draft。
- 临时生产式 API 配置：模拟审核发布的合成病例 **1 个可训练版本**；停用 `05/05.01` 后可训练数为 **0**，封存事件/评分仍可回看。真实院校配置未取得。开发模式浏览器显示 5 个草稿行不等于生产发布数。

### 命令和结果

| 验证 | 实际命令/操作 | 结果 | 证据边界 |
|---|---|---|---|
| 目录/病例生命周期专项 | `./.venv/Scripts/python.exe -m pytest -q tests/test_department_catalog.py tests/test_case_copy_publish_visibility.py tests/test_case_training_stop.py tests/test_teacher_cases.py` | **64 passed / 0 failed，6.66s** | 合成院校/账号/API；错误父子组合 422 且未落库；生产草稿不可见；模拟审核标签明确；合成发布后开训/封存，停用后新开训拒绝、历史评分不变 |
| 浏览器实际页面操作 | `./.venv/Scripts/python.exe scripts/run_department_catalog_browser_probe.py --evidence docs/evidence/2026-11-25-department-catalog-browser-final.json` | **5/5**：教师选择 `05/05.01` 未保存；病例库 5 行；空 `05` 隐藏；`03/03.04` 筛选 1 行；0 个脚本错误 | 临时数据已清理；真实 Edge 本地操作，不是生产部署/真实学生使用；JSON 记录 `worktree_clean=false` |
| 全量离线回归 | `./.venv/Scripts/python.exe -m pytest -q` | **746 passed / 0 failed，164.33s** | 当前未提交本机工作树，不是 CI 或临床验收 |
| Python 编译 | `./.venv/Scripts/python.exe -m compileall -q backend/app frontend tests scripts` | 通过 | 本轮执行，未输出编译错误 |
| 前端类型检查/构建 | 在 `web/`：`npm run typecheck`、`npm run build` | 均通过；构建后 `git diff -- web/dist` 为空 | 前端业务源码未改，但 E2E 探针脚本更新；构建产物未产生差异 |
| 浏览器证据敏感信息检查 | 检查复跑 JSON 结构与口令/令牌/密钥模式 | JSON 有效，匹配数 0 | 不读取任何凭据；原先浏览器摘要保留未覆盖 |

**分层结论：** API/目录/发布门禁由自动化合成测试证明；教师选择和学生筛选由本机浏览器实际页面操作证明；所有模拟审核都是测试夹具。没有真实临床审核、真实院校建制、真实学生试用或真实模型运行。本轮未发现需改写病例原件或迁移既有编码的证据；官方原始附件仍未完整归档，34/143 的明细差异仍待核。
