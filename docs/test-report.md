# 测试报告

## 2026-11-27 晚间权限与导出回归

本轮基于 `4b525dd` 工作树，全量 **888 passed / 0 failed（179.28s）**；教师授权相关9模块190项、导出/备份相关76项分别为修复过程专项，不能与全量相加。教师页面16/16、隔离师生流程13/13、工程样本12/12，前端typecheck/build、Python编译与npm审计通过。真实模型调用0。

详见[完整证据](evidence/2026-11-27-evening-audit/validation.json)。此结果替代下方历史批次作为本轮本机结果；远程CI按发布后的精确SHA核对。

本报告保留各日期和提交的工程证据。官方原件已于2026-11-27归档，工程测试不等于正式验收。当前情况见[全面复盘](全面复盘与交接-20261127.md)。

## 2026-11-27 全面复盘增补

基线c11265d加本轮3项可信等待测试的工作树，全量 **863通过、0失败，187.08秒**。前端类型/构建通过，npm高危审计0告警，12组隔离工程样本通过。完整日志、逐问时间、五科轨迹、师生浏览器及视频证据见[本轮归档](evidence/comprehensive-review-20261127/validation.json)。没有新增真实模型调用。最终SHA的Push/PR结果另按Git记录核实。


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

## 2026-11-25：O3 问答路由修复与提交后工程抽检

### 基线、评测冻结与范围

- 开始时分支 `codex/feedback-protocol-fix`，本地 HEAD 与远端均为 `30028a042f7599f4fb94a4ea1cb28288baba362c`，工作区干净；该基线 Push run `45616293272`、PR run `45616301640` 的 `tests`、`dependency-audit` 通过。
- 产品修复提交 `fd140738023b9a2daac8dac2cd1255bc80e02693`。复现探针 SHA-256 为 `c5383a0bf983b96c785a0cb47348510d4d11f74f90f4f5b72f8ccd4386f845c4`；20 轮固定工程集 SHA-256 为 `827f61361d892e474fd703ad6e7bda09813a1355ad24512a5516942f66719ada`。固定输入未修改，未将开发用例混入 20 轮。
- 20 轮覆盖 `STEMI-001@3`、`APPY-001@2`、`STROKE-001@2`、`CAP-001@2`、`DKA-001@2`，各自仓库源文件 SHA-256 固定；均为 draft。每例四轮按预登记顺序共享一个会话，不跨病例共享。冻结输入逐轮记录意图、问题、必答/允许事实、禁止披露项、可接受无事实回答与判定规则。评测由同一开发者撰写和预登记，不是盲测；历史 40 条留出集未用于调参或本轮判定。
- 运行环境：Windows 11 build `10.0.26300`，系统 Python `3.14.5`，本地认证、`NEXENT_MODE=simulator`、临时隔离的 `APP_DATA_DIR` 与 `INTEGRATION_DB_PATH`。运行临时开启草稿病例可见，仅用于这些合成病例的隔离工程检查；测试数据运行后已清理。真实模型/供应商调用 0 次，未读凭据或接触现存数据库/病例原件。

### 修复前复现与提交后学生 API 结果

- 四个对照输入使用相同关键词但不同意图：当前“现在需要做手术吗？”与历史“以前做过手术吗？”；“家里人什么时候发现症状的？”与明确的家族史问题。修前 API 2/4 正确：当前处置问题披露 APPY `f_hx`；亲属发现症状问题披露 STROKE `f_family` 而漏答 `f_care`。家族史查询会额外受到共享词语影响。
- 修复后同一学生 API 输入为 4/4：处置决策返回固定非事实答复且事实 ID 为空；历史手术问题仍返回 `f_hx`；亲属发现症状仅返回 `f_care`；家族史问题仅返回 `f_family`。四个 API 回答均非空，所有保存事件与响应 event ID 匹配。
- 冻结 20 轮修前事实 ID 级主结果：9/20 正确，11/20 越权披露；错误分类为可重叠：漏答 1、多答 11、越权披露 11、不当拒答 0、接口失败 0。提交后事实 ID 级主结果 20/20 通过；漏答 0、多答 0、越权披露 0、不当拒答 0、接口失败 0。类别判定保留所有 20 轮作分母，且每轮保存主结果。句内原子事实的潜在多答风险另见下文，不纳入该自动分数。
- 四探针逐项前后报告：[`pre`](../evaluation/reports/o3_reproduction_pre_20261125.json)、[`post-commit`](../evaluation/reports/o3_reproduction_postcommit_20261125.json)。冻结 20 轮报告：[`修前`](../evaluation/reports/o3_spotcheck_pre_20261125.json)、[`产品提交后`](../evaluation/reports/o3_spotcheck_postcommit_20261125.json)。报告逐轮包含输入、完整回答、事实 ID、禁披露项、HTTP 状态、事件 ID/匹配、规则判定和适配器来源。

| 轮次 | 自动主结果（事实 ID 级） | API 实际披露事实 ID | 自动错误标记 | 句内原子信息待复核 |
|---|---|---|---|---|
| STEMI-001-R01 | 正确 | — | — | — |
| STEMI-001-R02 | 正确 | `f_allergy` | — | 含过敏信息 |
| STEMI-001-R03 | 正确 | — | — | — |
| STEMI-001-R04 | 正确 | `f_family` | — | — |
| APPY-001-R01 | 正确 | — | — | — |
| APPY-001-R02 | 正确 | `f_hx` | — | 含既往健康与过敏信息 |
| APPY-001-R03 | 正确 | — | — | — |
| APPY-001-R04 | 正确 | `f_family` | — | — |
| STROKE-001-R01 | 正确 | — | — | — |
| STROKE-001-R02 | 正确 | `f_anticoag` | — | 含抗凝/出血信息 |
| STROKE-001-R03 | 正确 | `f_care` | — | 含“路上没吃过任何药” |
| STROKE-001-R04 | 正确 | `f_family` | — | — |
| CAP-001-R01 | 正确 | — | — | — |
| CAP-001-R02 | 正确 | `f_surgery` | — | 含外伤信息 |
| CAP-001-R03 | 正确 | — | — | — |
| CAP-001-R04 | 正确 | `f_family` | — | — |
| DKA-001-R01 | 正确 | — | — | — |
| DKA-001-R02 | 正确 | `f_allergy` | — | 含过敏信息 |
| DKA-001-R03 | 正确 | — | — | — |
| DKA-001-R04 | 正确 | `f_family` | — | — |

**判定粒度限制：** 上表及 20/20 只表示冻结规则按事实 ID 检查通过，不能推出句内每个原子事实都与问句意图一致。五例的 R02 都返回整个复合事实，例如 STEMI/DKA 的 `f_allergy` 还含过敏信息，APPY 的 `f_hx` 还含既往健康与过敏，STROKE 的 `f_anticoag` 还含抗凝/出血信息，CAP 的 `f_surgery` 还含外伤信息；另外 STROKE-001-R03 的 `f_care` 还含“路上没吃过任何药”，超出询问发现过程的直接意图。预登记输入把这些复合事实 ID 整体列为允许项，没有冻结句内拆分标准，因此自动报告的多答为 0，但这 6 轮存在待复核的语义多答风险，不能把它们按原子事实判为已过。未追改病例或冻结集，也未在本轮给出事后专家判定；下一次应另建原子事实级、事先冻结的评测，再由独立评判确认。

### 复跑命令与结果

为确保运行时记录的工作区干净且 HEAD 精确指向产品提交，调用方先在仓库外创建新的临时报告目录；两次运行结束后将报告归档到 `evaluation/reports/`。下面的 `<临时目录>` 需要替换成该实际目录，不要覆盖已有报告：

```powershell
py -3 -m pytest -q tests/test_natural_dialogue.py
py -3 -m compileall -q backend scripts tests
py -3 scripts/run_o3_spotcheck.py --dataset evaluation\o3_reproduction_probes_20261125.json --report <临时目录>\reproduction.json
py -3 scripts/run_o3_spotcheck.py --dataset evaluation\o3_engineering_spotcheck_20261125.json --report <临时目录>\spotcheck.json
```

| 验证 | 实际结果 | 说明 |
|---|---|---|
| O3 专项 `tests/test_natural_dialogue.py` | **35 passed** | 在产品提交后重跑；报告中的逻辑和 API 正反例均通过 |
| Python 编译 | `py -3 -m compileall -q backend scripts tests`：通过 | 运行后无编译错误 |
| 四探针学生 API | **4/4 正确** | HTTP 200；实际保存事实与 API event ID 对应；报告记录临时数据已清理 |
| 冻结 20 轮学生 API | **20/20 事实 ID 级规则通过** | 必答/允许事实 ID 匹配，回答精确渲染已保存事实或固定非事实答复；事件逐轮匹配；句内原子信息未评分 |
| 全量离线回归 | **754 passed / 3 failed，214.72s**（757 项） | 命令 `py -3 -m pytest -q`，在产品提交前的本机工作树运行；运行时 HEAD 为基线 `30028a0`，工作树产品源码与后续 `fd14073` 的源码内容一致，但不是在 `fd14073` SHA 上执行；失败见下，不能记为全绿 |
| 前端类型检查/构建 | 未重跑 | 本轮没有修改前端源码或构建产物 |
| GitHub Push/PR CI（产品 SHA `fd14073…`） | Push `45627535915`、PR `45627546222`：`tests` 与 `dependency-audit` 均通过 | CI 全绿不代表本机 Python 3.14.5 的三个失败已修复，也不代表官方验收 |

全量回归的三项失败均保留原断言，未因本轮 O3 修改处理：

1. `tests/test_dashboard_coverage.py::test_dashboard_page_renders_and_opens_score_review`：系统 Python 3.14.5 下 Altair 5.5.0 导入时 `TypedDict(closed=True)` 报 `TypeError: _TypedDictMeta.__new__() got an unexpected keyword argument 'closed'`；单测复跑 1 failed。
2. `tests/test_nexent_deadlines.py::test_deadline_cancels_submitted_turn_and_same_request_retries_once[patient]` 与 `[feedback]`：300ms 超时在测试模拟器 session 记录创建前已触发，随后 `records[0]` 抛 `IndexError`；专项复跑为 27 passed、2 failed，日志观测耗时 248–280ms。

### 判定边界与开放状态

修复前后都是本机 FastAPI 学生问诊 API、本地认证、临时合成数据库和确定性规则模拟器结果。20 轮由同一开发者预登记，不是独立盲测；病例均为草稿；没有真实模型运行、医学专家判定、真实学生样本或临床内容审核。自动规则 20/20 仅说明此冻结工程输入在提交代码上未复现已知错误，不替代正式官方 20 轮验收或 95% 判定；O3 仍标为“已实现但未达标”，第一阶段仍未准出。

## 2026-11-25：O3 复合病例事实原子回答（产品 SHA `b61fdb9`）

### 修前复现与冻结口径

- 接手基线：分支 `codex/feedback-protocol-fix`，`51901bd644519df4d57042eff4a684abaf2ee628`；产品提交依次为 `d4f66692fca12bd25fe36a9803f07e46874645ca` 和 `b61fdb9c8a83e4a2e83383927dc8fe3f9e70ec66`。本节不把后续文档提交冒充产品代码测试 SHA。
- 修前学生 API 探针 SHA-256 `4fe6b133c21e09ef90eb58e738f6433204ad9b06cd439b984742b088245130b5`：问 APPY 既往手术返回“既往体健，没有做过手术，没有药物过敏。”；问过敏也带出手术史。问 STROKE 家人何时发现时回答哥哥既往卒中，病例无具体发现时间。原始修前报告保存在 [`o3_atomic_reproduction_pre_51901bd.json`](../evaluation/reports/o3_atomic_reproduction_pre_51901bd.json)。
- 原有 20 轮逐句复核另存于 [`o3_atomic_old20_semantic_review_20261125.md`](../evaluation/reports/o3_atomic_old20_semantic_review_20261125.md)，未改写旧报告和输入。该复核补足原自动报告未标的句内附带披露：STEMI R04 回答父亲冠心病时夹带“常年吃药”；CAP R04 除“没有类似的病”外还披露家庭近期患病、旅居/禽类接触及结核；五个 R02 复合病史都有超出单问项目的附带信息；STROKE R03 还带出途中用药。此复核是开发者工程复核，不是医学专家判定。
- 产品代码修改前冻结新的 20 轮语义集 [`o3_atomic_semantic_spotcheck_20261125.json`](../evaluation/o3_atomic_semantic_spotcheck_20261125.json)，SHA-256 `425a0303bcfc9b7d9aaf341bb6ad0fb330aad0d033e8463b18878420fff59e01`。五病例版本为 STEMI-001@3、APPY-001@2、STROKE-001@2、CAP-001@2、DKA-001@2；每例四轮共享一会话，另一个病例使用新会话。题集逐轮冻结必答原子、禁止披露片段、否定/主体/时间范围要求与允许的澄清回答。题目由同一开发者预登记，不是盲测；原 20 轮是已知回归集，新集另行冻结；历史 40 条留出集未用于调参。

### 修后逐轮摘要与保存验证

修后学生 API 的 20 轮逐条结果、答案、原子 ID、`persisted_answer`、事件 ID、重放回答和主判定均在 [`o3_atomic_semantic_spotcheck_post_b61fdb9.json`](../evaluation/reports/o3_atomic_semantic_spotcheck_post_b61fdb9.json)。所有轮次 HTTP 200；逐轮结果均为 correct；API 回答与已保存答案一致，重放回答与原响应一致、事件 ID 相同且无重复保存事件。汇总仍保留全部 20 轮分母：正确 20/20；漏答 0，多答 0，越权披露 0，不当拒答 0，接口失败 0。该数值是按开发者冻结规则自动判定的工程样本结果。

| 病例/轮次 | 修前问题/答案摘要 | `b61fdb9` 实际回答 | 工程判定 |
|---|---|---|---|
| STEMI R01 单问手术 | 同类旧问曾将过敏与手术并答 | “没做过手术。” | 只答被问到的否定手术史 |
| STEMI R02 单问过敏 | 复合回答会夹带手术/家族史 | “没有药物过敏。” | 手术追问未被阻断 |
| STEMI R03 同时问两项 | — | “没有药物过敏，没做过手术。” | 两个原子均回答 |
| STEMI R04 单问家族病史 | “我父亲有冠心病，常年吃药。” | “我父亲有冠心病。” | 保留主体，未带出用药 |
| APPY R01/R02 单项追问 | “既往体健，没有做过手术，没有药物过敏。” | 分别为“没有做过手术。”、“没有药物过敏。” | 回答最小范围；原子续问成功 |
| APPY R03/R04 | — | “没有做过手术，没有药物过敏。”；“既往体健。” | 同问两项时全答，另一轮仅答既往健康 |
| STROKE R01 最近手术 | 旧复合答含抗凝、外伤、出血等 | “最近没有手术。” | 保留时间范围和否定 |
| STROKE R02 单问过敏 | 旧复合病史可能夹带抗凝/手术信息 | “没有药物过敏。” | 无额外披露 |
| STROKE R03 同时问多项 | 旧记录可能扩成无时间限制的结论 | “没有吃抗凝药，最近没有手术，最近没有外伤，没有出血病史。” | 同问项均答，保留“最近”范围 |
| STROKE R04 发现时间 | “我哥哥前几年也中过风。” | “具体是什么时候发现的我说不准。” | 不用家族史或后续处置代答；病例无具体时间 |
| CAP R01/R02 单问 | 手术回答曾夹带外伤；过敏问题曾匹配其他病史 | 分别为“没做过手术。”、“没有药物过敏。” | 不带出外伤、吸烟或慢支 |
| CAP R03/R04 | — | “没做过手术，最近也没受过伤。”；“家里人没有类似的病。” | 两项同问均答；家族问题不带结核/暴露 |
| DKA R01/R02 单项追问 | 既往整条复合答会夹带另一项 | 分别为“没做过手术。”、“没有药物过敏。” | 另一原子可在后续回答 |
| DKA R03/R04 | — | “没有药物过敏，没做过手术。”；“家里人没有糖尿病。” | 同时问和家族问均按范围作答 |

上述“修前同类”来自原始 20 轮 [`o3_spotcheck_pre_20261125.json`](../evaluation/reports/o3_spotcheck_pre_20261125.json) 和逐句复核，冻结的新集与旧集不是同一输入。表中有些轮次的修前问法本身不同，不能解释为同一句逐项 A/B 测试。尚无外部独立人员判定题目必答/禁答设置的医学适当性；因此没有任何轮次可据此宣布临床正确或正式官方通过。特别是 STROKE R04 的“说不准”只符合病例未记载时间的工程规则，不证明真实临床沟通质量。

初版 `d4f6669` 的同一四探针后报告按旧父事实 ID 口径显示 0/4，实因原子 ID/父事实 ID 判定口径不兼容，不是产品答案语义 0/4；证据保留在 [`o3_atomic_reproduction_post_d4f6669.json`](../evaluation/reports/o3_atomic_reproduction_post_d4f6669.json)。该提交第一次全量回归为 744 passed / 18 failed，后续根因涉及原子候选匹配为空、续问上下文丢失及旧夹具只问部分原子却期待父事实分。原始 18 个失败节点/控制台日志未归档，不补造节点名；摘要见 [`o3_atomic_full_regression_iteration_d4f6669.md`](../evaluation/reports/o3_atomic_full_regression_iteration_d4f6669.md)。`b61fdb9` 修复后未放宽断言或改超时测试。

### 执行环境和命令

- 系统：Windows 11 build `10.0.26300`；项目锁定 Python `3.11.18`；本地认证，`NEXENT_MODE=simulator`，临时隔离 `APP_DATA_DIR` / `INTEGRATION_DB_PATH`；草稿可见性只在隔离合成环境启用。报告注明运行后临时数据清理。真实模型/供应商调用 0 次；未读凭据。
- `./.venv/Scripts/python.exe -m pytest -q tests/test_natural_dialogue.py`：**40 passed / 0 failed，5.33s**。
- `./.venv/Scripts/python.exe -m pytest -q`：**762 passed / 0 failed，162.62s（2:42）**。
- `./.venv/Scripts/python.exe -m compileall -q backend/app frontend tests scripts`：通过。
- `web/` 未改，前端类型检查/构建未重跑。
- 本轮保留上一轮 Python 3.14.5 的 3 项历史失败记录，不修改超时断言；本次专项与全量运行使用 Python 3.11.18。

**状态：** O3 原子回答路径已有服务端实现、规则模拟器学生 API 回归和可复跑报告，但结果由同一开发者撰写/判定、病例为合成草稿、没有真实模型、医学专家或真实学生。20/20 不能替代官方 20 轮≥95%验收；O3 仍未达标，第一阶段仍未准出。


## 2026-11-26：当前 O3 代码与文档状态复核

- 复核基线：本地/远端分支 `codex/feedback-protocol-fix` 均为文档提交前的 `f83002077116387ce116a564234442c17864c373`，工作区干净；产品代码沿用 `b61fdb9`。
- 使用项目 `.venv` Python 3.11.18：O3及相邻问诊回归 `151 passed`；全量 `762 passed`（227.67s）；`compileall -q backend/app frontend tests scripts` 与 `git diff --check` 通过。全量结果覆盖当前产品代码，不是 GitHub runner 结果。
- GitHub 最近可见 Push/PR 检查仍对应旧 SHA `51901bd`；当前 `b61fdb9` 与文档 HEAD `f830020` 无检查记录。尝试手动 dispatch 被 GitHub HTTP 403 拒绝，因此本报告不宣称当前 SHA 的远程 CI 已通过。
- 本轮复核未修改产品代码、病例、数据库或历史评测证据；未读取 `KEY.txt`、未调用真实模型。真实学生/专家评测及官方验收仍未完成。


## 2026-11-26：起病量表片段计分及完整交接复核

产品提交 `d05450df6eec8abf2e912d9dd72f4774a4c23af9` 已推送。接手时 `b95ad9a` 的 Push/PR CI 已成功（run 45775252395 / 45775260054）；不将这些结果用于证明后续提交 CI。

- 项目 Python 3.11.18；最终全量 **796 passed，0 failed，144.14s**。运行从提交前最终源码工作树开始，产品/测试源码与 `d05450d` 相同。新评分专项 26 passed。
- 首轮全量 **795 passed，1 failed，146.29s**，节点 `tests/test_natural_dialogue.py::test_frozen_atomic_semantic_spotcheck_regression`。原冻结输入的病例哈希与新增量表配置后的文件不同；新增独立复跑清单并更新调用方，仍校验原题 SHA-256、所有题目及判定不变。没有改写历史冻结题集、放宽断言或删失败用例。
- 在产品提交上复跑：12 组工程样本全部通过；20 轮原子语义复跑 20/20；110 评分场景 78 符合工程预期、22 偏差、10 待教学审核。110 组问答对照不变，仅 6 个起病评分转正；重复请求和封存重放检查通过。
- `npm run typecheck`、`npm run build`、Python compileall、git diff 检查通过。前端构建没有需提交的产物变化；未新增浏览器交互测试。工程样本确认原业务数据库哈希不变。
- 新评分保留旧事件兼容；旧封存零分在升级规则后仍原样返回。原子 ID 按位置生成，病例文字/拆分调整仍须人工核对。教师复制病例未迁移。

[本轮复核记录](evidence/2026-11-26-handoff-review.json)、[工程样本摘要](evidence/2026-11-26-engineering-samples.json)、[当前接手与剩余偏差](工程接手.md)。全部为本机合成病例/规则模拟器工程检查，不是临床、真实学生、真实模型或官方验收；O3 与第一阶段仍未准出。

远程补核：产品 `d05450d` 的 Push run `45787364377`、PR run `45787372710` 均完成且成功；与上述本机结果分开记录。后续文档提交仍按各自 SHA 查验。
