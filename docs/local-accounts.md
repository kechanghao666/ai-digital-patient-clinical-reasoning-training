# 独立账号与登录

2026-11-27权限收紧：已有学生仅其当前任教教师可加入另一个自有班级。教师不能认领无归属/其他教师的已有学生，移出全部自有班级后也不能凭用户名重新认领；跨教师移交/重新分配流程未实现。已有业务库关系需部署方在授权下核对，本轮不会自动更改。

> 2026-11-13 启用更新：用户已授权并启动独立账号预览（本机 8508，API 8006）。学生、教师实际浏览器登录/退出、后端角色权限与退出令牌失效均已验证；问诊使用本地模拟。自动打开浏览器和账号文件被策略拦截，可手动访问。见[运行证据](evidence/2026-11-13-account-preview-live.json)。下方“待确认/旧后台尚未启用”等为启动前记录。

2026-11-13：已实现本项目独立账号，分学生、教师两种角色。账号库与训练库分离；无公开注册入口，教师权限只能由本机管理员分配。学校统一身份、邮箱找回、多因素认证和公网部署验收不在本次交付范围。

## 启用和开通

在项目根目录中设置与目标 API 相同的 `APP_DATA_DIR`，随后创建账号。密码交互输入，不放入命令行、源码或截图。

```powershell
$env:APP_DATA_DIR = 'E:\AI数字病人临床思维训练\local-only\account-data'
$env:APP_AUTH_MODE = 'local'
& .\.venv\Scripts\python.exe scripts/manage_accounts.py create student01 --role student --name '学生一'
& .\.venv\Scripts\python.exe scripts/manage_accounts.py create teacher01 --role teacher --name '教师一'
```

以上示例使用新数据目录；这不会迁移旧训练。对现有部署启用账号时，应使用该部署实际的数据目录，不更换训练库。`accounts.db` 与 `app.db`、集成库、教师病例一起备份。每个账号拥有稳定的内部编号，不能通过修改登录名接管旧演示身份的训练；本次不迁移历史身份归属。

API 使用 `APP_AUTH_MODE=local`。`APP_ENV=development` 仍控制未审核病例的本机演练，不代表允许免登录；只有 development 与 demo 同时启用时才接受原来的演示身份。生产环境即使误设 demo 也拒绝演示身份。未审核病例在生产仍受原发布规则限制。

前端通过 `/api/auth/config` 确认登录模式。local 模式默认显示登录页；验证成功后展示角色允许的工作区。教师也可进行学生训练，学生不能选择教师工作区。前后端配置与启动命令见[部署指南](deployment.md)。旧版回环地址 API 没有账号端点时，保留本机演示入口，并将独立账号登录明确禁用；不会伪造成功或把手填身份变为正式账号。

## 密码重置和停用

```powershell
& .\.venv\Scripts\python.exe scripts/manage_accounts.py reset-password student01
& .\.venv\Scripts\python.exe scripts/manage_accounts.py disable student01
```

密码重置和停用会立即撤销该账号已有登录，保留训练记录。管理员运行命令前须核对 `APP_DATA_DIR`。账号为 3–64 位 ASCII 字母、数字、点、下划线、短横线，不区分大小写；密码为 12–128 个字符。当前 CLI 提供管理员开通、重置和停用；教师端另有面向本班学生的重置、停用和重新启用路径。没有自助提权或默认密码。

账号创建、管理者密码重置、停用及教师端重新启用会在 `accounts.db` 同一事务写入精简审计；新增学生账号记 `account_created`，仅将已有学生加入另一个班级不会记为创建。教师操作记录认证账号内部 ID 和 `teacher_api` 入口；命令行记录 `local-ops` 和 `cli`，不伪称为教师身份。记录只含 UTC 时间、操作、目标内部 ID、操作者 ID 和入口，不含密码、临时密码、哈希或令牌；不追补历史，也不记录登录/登出和学生自助改密。重复停用若状态未变化不追加审计。

运维人员可在受限的服务器命令行环境核对最近记录；此命令不提供教师/学生 API，也没有应用内角色认证。部署方必须限制可执行该 CLI 的主机账号及 `accounts.db` 的文件读取权限，不能把“仅运维使用”误认为工具自身实施了操作系统 ACL：

```powershell
& .\.venv\Scripts\python.exe scripts\manage_accounts.py audit-log --limit 100
```

该命令只从当前 `APP_DATA_DIR/accounts.db` 输出最近 100 条精简记录（上限 500），无写入能力；运维须先核对所选数据目录，并依主机文件权限限制数据库和输出。该日志可随 `accounts.db` 纳入既有离线备份恢复。

## 已实现的边界

### 审核人资质登记（2026-11-27）

教师角色不代表医学资质。病例发布必须由当前登录教师完成，服务器绑定账号及资质记录，前端不再填写任意审核人姓名。资质要求：启用、已登记外部人工核验、当前日期和审核日期均在有效期内、科室与亚专科与病例精确匹配。仅有父科室资质不自动覆盖其亚专科。日期采用 API 所在主机的本地日，审计时间采用 UTC；部署时应保持主机时区一致。

获授权的本机运维人员先在系统外核实真实人员身份、专业范围和有效期，再使用与 API 相同的数据目录登记。以下编号/日期仅说明命令格式，不能直接作为真实资质：

```powershell
# 登记后仍为 pending_verification，不能发布
& .\.venv\Scripts\python.exe scripts/manage_accounts.py qualification-create teacher01 --department-code 04 --subspecialty-code 04.01 --valid-from 2026-01-01 --valid-until 2027-12-31
# 使用上一步实际返回的编号；只有完成外部人工核验后才可执行启用
& .\.venv\Scripts\python.exe scripts/manage_accounts.py qualification-status '<资质记录编号>' --status active --confirm-external-verification
& .\.venv\Scripts\python.exe scripts/manage_accounts.py qualification-status '<资质记录编号>' --status suspended
& .\.venv\Scripts\python.exe scripts/manage_accounts.py qualification-status '<资质记录编号>' --status revoked
& .\.venv\Scripts\python.exe scripts/manage_accounts.py qualification-audit --limit 100
```

范围和有效期不能原地修改；纠错或续期须新建记录并撤销旧记录。暂停后恢复启用需重新确认外部核验，撤销不可恢复。账号停用或资质撤销阻止后续发布；已经发布的病例、已有训练和评分保留，要停止该病例接受新训练需使用既有“停止新训练”功能。发布期间持有账号库事务锁，使资质撤销/账号停用与发布有确定的先后顺序。

资质创建/状态变更与审计同事务提交；失败回滚，重复状态不追加。`qualification-audit` 使用只读连接，缺库或旧库无资质表时不创建、不迁移。记录随 `accounts.db` 离线备份恢复。教师仅可通过 `/api/teacher/reviewer-qualifications` 查看自身记录；React 病例管理页显示状态、范围、有效期并可刷新。旧 Streamlit 发布流程也使用绑定身份。

此登记不是自动执业资格认证；目前只记录本机 `local-ops` 的外部核验确认及时间，未存证件、机构回执或独立核验人的实名证明，也没有应用内管理员角色。主机账号与数据库文件权限由部署方管理。真实核验手续和证据归档仍需外部完成。旧病例没有绑定记录时继续显示为历史声明，不能自动补成“已核验”。本轮所有测试和浏览器发布均使用临时合成身份，真实医学审核数量为零。

- 密码使用独立随机盐与 PBKDF2-HMAC-SHA256（600,000 次）；账号库只保存派生结果。
- 登录令牌随机生成，数据库只保存 SHA-256 摘要；绝对有效期 8 小时。每次请求重新核对账号和会话，退出、停用、重置后旧令牌失效。
- 登录失败按账号和来源限制频率，限制持久化；未知账号也受限制，错误提示不区分账号不存在、密码错误与停用。
- 令牌只留在 Streamlit 服务端会话，不加入 URL、HTML、本地浏览器存储或日志。切换服务地址不向新地址发送旧令牌；凭证请求及业务请求不跟随重定向。远程账号服务只接受 HTTPS，本机回环 HTTP 仅用于本机运行。
- 学生/教师权限和训练归属由后端验证；更改 `X-Owner-Id` 不影响已验证账号。未登录返回 401；已登录学生访问教师接口返回 403。
- 登录过期后隐藏工作区；同一个浏览器会话、同一账号重新登录可恢复未提交病历等编辑内容。更换账号清除身份缓存。退出会清除本页未提交内容，应先保存；关闭页面或刷新导致 Streamlit 会话重建时，未提交内容不保证恢复。

这是一套单 API 进程的本机教学账号实现。公网交付仍须验证 HTTPS、访问入口、备份恢复、账号管理流程、真实人员授权与独立安全审查；工程测试通过不等于完成这些验收。

## 本轮验证

本次完整回归 488 项通过，包含真实后端与 Streamlit 测试运行器中的登录、角色切换、过期后恢复、退出、跨用户读取、错误身份、密码重置、停用、持久限流和摘要存储检查。运行中的旧预览后台没有热加载，不能把测试运行器的结果表述为旧服务已经支持账号。浏览器光标及布局证据见[动态界面记录](evidence/cursor-accounts-20261113/browser-check.json)。
