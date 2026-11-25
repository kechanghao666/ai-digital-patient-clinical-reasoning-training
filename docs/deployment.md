# 从零启动与隔离复现

本指南针对单API进程、本机教学演练。项目已提供[独立账号与角色权限](local-accounts.md)，服务只监听本机；不将development自填账号用于公开部署。真实Nexent联调与本地模拟分别验收。

## 安装

取得完整源码后在仓库根目录使用Python 3.11。Windows PowerShell：

```powershell
py -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe scripts/run_engineering_samples.py
```

Linux/macOS使用`python3.11 -m venv .venv`，后续解释器路径为`.venv/bin/python`。十二组演练输出到`local-only/engineering-samples/时间戳/`，含逐次输入输出、代码/病例/依据哈希和实际判定。也可在脚本后传一个尚不存在的输出目录；不会覆盖已有报告。脚本复用仓库测试夹具，必须保留tests目录；病例、评分和反馈均为未经医学审核的本地工程模拟。

## 本地演练

在仓库根目录打开一个终端设置环境后启动API：

```powershell
$env:APP_ENV = 'development'
$env:APP_AUTH_MODE = 'demo'
$env:NEXENT_MODE = 'simulator'
$env:APP_DATA_DIR = Join-Path (Get-Location) 'local-only\demo-data'
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

另开终端，在仓库根目录启动界面：

```powershell
& .\.venv\Scripts\python.exe -m streamlit run frontend/app.py --server.address 127.0.0.1 --server.port 8501
```

浏览器访问`http://127.0.0.1:8501`，连接设置填`http://127.0.0.1:8000`。学生演示账号须以`demo-`或`student-`开头；教师演示账号以`teacher-`开头。API健康地址为`http://127.0.0.1:8000/health`；`status=ok`只证明服务存活，`patient_adapter=simulator`明确表示未调用真实模型。

## 数据目录与备份

界面默认连接`http://127.0.0.1:8000`。使用其他API端口时，可在启动界面的终端先设置`$env:FRONTEND_API_URL = 'http://127.0.0.1:8005'`，避免首次打开页面时连接错误服务；页面“连接设置”仍可修改当前连接。不要把模型密钥放入此地址。

`APP_DATA_DIR`包含`app.db`和`teacher_cases/`，默认集成记录在同目录`integration.db`。不设置时兼容原目录`backend/data`。相对路径始终从仓库根目录解析。显式`INTEGRATION_DB_PATH`优先于默认路径，API与MCP必须指向同一业务目录和同一集成库；知识目录亦须一致。

环境变量在进程启动时生效，修改后须重启对应服务。示例`.env`不会自动加载。更换`APP_DATA_DIR`只是使用另一个目录，不会迁移、清空或覆盖旧训练。保留旧目录及原封存记录。当前备份工具只支持所有写入者停止后的离线备份；虽逐库使用 SQLite backup API，但**不提供或声称在线热备份**，也不能用简单文件复制代替。操作步骤和边界见下文。不要直接把旧格式数据库改名覆盖当前库。

测试启动时自动选择临时存储，样本复跑每组也使用独立数据库。真实平台演练仍应显式配置专用`APP_DATA_DIR`，不以测试隔离代替部署隔离。

## 科室参考目录启用（部署管理员）

病例的科室编码使用仓库内受控的参考目录子集，不代表完整国家目录或院校实际建制。生产数据目录缺少 `department-catalog.json` 时默认不启用目录项，因此学生病例列表为空、不能新建训练；先由部署管理员核对本校实际开设范围，再配置启用项。教师账号不能修改这份部署配置。

```powershell
# 查看当前状态；只读配置文件，不读取业务数据库
& .\.venv\Scripts\python.exe scripts\configure_department_catalog.py --show

# 示例：按本校确认结果完整替换启用项（不是推荐的通用医院建制）
& .\.venv\Scripts\python.exe scripts\configure_department_catalog.py `
  --enabled-departments 03,04 `
  --enabled-subspecialties 03.01,03.03,03.04,03.07,04.01

# 恢复上一份有效配置
& .\.venv\Scripts\python.exe scripts\configure_department_catalog.py --restore-previous
```

配置写入 `APP_DATA_DIR/department-catalog.json`，每次替换前保留 `.previous`；部署前应备份数据目录并由管理员核对操作者与启用范围。验证：运行 `--show`，检查教师病例表单选项与学生病例筛选；停用项应阻止新布置和新开训，但不得删除既有训练、事件或封存评分，已开始训练仍可继续。科室启用不等于病例医学审核或发布。

## 真实Nexent配置

1. 在目标Nexent实例配置可用模型，实际调用验证，不能只凭模型列表显示available。目标实例模型/工具资源ID须重新绑定。
2. 从受保护的服务配置加载`NEXENT_BASE_URL`、`NEXENT_API_KEY`、`NEXENT_PATIENT_AGENT`、`NEXENT_FEEDBACK_AGENT`和`MCP_SERVICE_TOKEN`；不得放入源码或截图。
3. API与MCP使用相同`APP_DATA_DIR`、`INTEGRATION_DB_PATH`及`KNOWLEDGE_DIR`。MCP令牌至少16字符，配置详见[字段清单](agent-config-draft.md)和[Nexent集成](nexent-integration.md)。
4. 在已设置上述环境的终端执行`python -m app.mcp_server`，工作目录为`backend`；默认监听127.0.0.1:8765。平台容器需使用可达地址和Bearer鉴权，按现有部署的端口转发方式验证工具可达。
5. 导入平台导出的病人与反馈配置，重新扫描工具并发布。病人只分配get_turn_context与submit_fact_selection。compact反馈只分配submit_feedback，设置`NEXENT_FEEDBACK_PROTOCOL=compact`；另一review_tool流程依独立说明配置，不能混用。
6. 设置`NEXENT_MODE=nexent`并重启API。执行[中期演示](stemi-demo.md)及五科场景，检查可信工具、事件、重复请求和首个有效回答时间。平台健康探测、HTTP 200或一个成功问题不能证明完整验收。

此前已验证隔离目录下API与MCP共享数据及十二组模拟闭环，真实五科各一次问诊成功。独立账号的启用、开通及撤销见[账号指南](local-accounts.md)。异机安装、独立Nexent实例导入、随机现场用例和公网身份部署仍待验证，不能将本机结果写成这些门槛已完成。

## 病例审计和恢复边界（2026-11-25 补充）

病例教师资产目录现在还包含 `case-audit.jsonl`：记录草稿保存、结构校验、送审、撤回、发布等病例生命周期动作的操作者 ID、时间、状态、科室及来源编号；停止新训练记录来自集成数据库并在教师时间线合并显示。审计不写入病例事实正文或患者问答。复制来源保存在病例版本字段中，修订时来源保持不变。

- 备份教师病例时必须将 `teacher_cases/` 内的病例 JSON 与 `case-audit.jsonl` 作为同一批次保留；同时备份 `department-catalog.json` 及其 `.previous`。当前可复现方式见下文离线白名单工具；旧说明中的“在线 backup”不代表已实现或验证热备份。
- 当前审计不是防篡改日志，也不覆盖账号创建/重置/停用、一般敏感数据访问；功能启用前的历史操作不会回填。审核人字段仍是教师填写的声明，没有资质核验。不得只凭此日志声称全站审计或已完成医学审核。
- 审计追加失败时应用会尝试回滚本次病例文件及 JSONL 尾部；若回滚本身失败会返回需停止操作并从备份恢复的错误。上线前仍需在隔离副本验证故障恢复，不可在现有业务目录注入故障或执行覆盖式恢复。
- **早期演练计划（编写时尚未执行）**：停掉隔离 API → 在新目录恢复 → 核对教师病例、来源、审计、科室配置、训练事件和封存评分。其后实际合成数据演练见下文；它不等于生产恢复演练。禁止将备份直接覆盖现有目录；有差异时保留两份并先调查。
- 当前生产部署、异机冷部署、数据库迁移/生产恢复演练、管理员权限和反向代理/HTTPS 均未由本轮验证。完整开放项及本轮实际测试见[第一阶段覆盖表](第一阶段覆盖表.md)、[官方要求核对](官方要求核对.md)和[测试报告](test-report.md)。

## 离线白名单备份与隔离恢复（2026-11-25）

`backend/app/backup_restore.py` 与 `scripts/backup_restore.py` 提供标准库实现的离线快照和新目录恢复。CLI 只读取命令行传入的目录路径，不加载 `.env`、进程模型密钥或其他应用设置。必须明确指定当前实际的 `APP_DATA_DIR`；若运行实例设置了自定义 `INTEGRATION_DB_PATH`，备份时也必须显式传入，否则工具会在缺少默认 `integration.db` 时拒绝，而不会猜路径。

```powershell
# 示例路径须替换为经核对的服务路径；所有 API、MCP 和其他写入者必须先停止。
$DataDir = 'D:\clinic-app\data'
$BackupDir = 'E:\restricted-backups\snapshot-2026-11-25'
& .\.venv\Scripts\python.exe scripts\backup_restore.py backup `
  --data-dir $DataDir `
  --destination $BackupDir `
  --confirm-services-stopped

# 如果服务使用自定义集成库，在备份命令中加：--integration-db 'D:\clinic-runtime\integration.db'

# 恢复只接受从未存在过的新目录；即使目标为空也拒绝覆盖。
$RestoreDir = 'D:\clinic-app\restore-2026-11-25'
& .\.venv\Scripts\python.exe scripts\backup_restore.py restore `
  --source $BackupDir `
  --target-data-dir $RestoreDir `
  --confirm-services-stopped

# 恢复后的集成库被集中放在 $RestoreDir\integration.db，启动服务时两项都须指向新目录。
$env:APP_DATA_DIR = $RestoreDir
$env:INTEGRATION_DB_PATH = Join-Path $RestoreDir 'integration.db'
```

备份包含 `app.db`、存在时的 `accounts.db`、配置的集成 SQLite 库、`teacher_cases/` 下格式有效的病例版本 JSON 与 `case-audit.jsonl`，以及存在且有效的 `department-catalog.json` 和 `.previous`。外置 `INTEGRATION_DB_PATH` 在备份中记为 `integration.db`，恢复时统一落到新数据目录下。未知数据目录项（包括上传目录、临时文件、任意密钥/配置文件）不进入快照，工具会**拒绝整个备份并报告未允许项目**；不要为通过备份而删掉未知源文件，先人工鉴别和另行保护。

SQLite backup API 对每个库分别产生可校验快照，完成 `PRAGMA integrity_check`；一致的跨库/病例文件时点依赖于备份前停止 API、MCP 和所有写入者。此实现不支持在线并发写入时点的一致性，也未验证热备份。恢复先校验清单 SHA-256、SQLite 完整性、病例 JSON/审计 JSONL/科室配置，再在目标父目录建立临时 staging 并发布为新目录；损坏、失败或已存在目标时不会合并或覆盖现存数据。专项还通过暂存复制故障注入验证受控报错、清理 staging 且不发布目标。恢复会清空副本 `accounts.db` 中的 `login_sessions`，避免在其他位置签发的有效 bearer 会话因还原而复活；账号、密码哈希和其他业务记录保留。

备份未加密、未签名；SHA-256 仅检测与清单不符的损坏，不证明来源真实性。文件白名单不包含模型密钥文件或上传图片，但备份含训练记录、病例草稿/审计，以及账号密码哈希和令牌哈希，按敏感数据保护。POSIX 文件模式会收紧到目录 `0700`/文件 `0600`；Windows 依赖备份父目录继承的 NTFS ACL，本轮未审计该 ACL。须将备份放在受控目录并由运维另行设置访问控制/静态加密。该工具不自动制定保留期限或轮转/删除备份；部署方须按院校的数据保留与隐私政策确定保留代数、保管位置和到期处置，不应在新备份及恢复校验完成前删除唯一可用副本。恢复前后仅在本机自动临时目录用合成训练、评分、病例来源、审计和科室当前/上一版配置复验（见[专项测试](../tests/test_data_backup_restore.py)）；未用现有业务目录、`backend/data`、真实数据库或病例原件，也未验证异机/生产恢复。
