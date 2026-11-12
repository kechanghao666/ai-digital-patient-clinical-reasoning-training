# AI数字病人临床思维训练

基于结构化模拟病例的医学教学训练项目，参加[魔擎开发者激励1期-基于Nexent的智能体开发](https://gitcode.com/org/ModelEngine/discussions/26)，对应课题1「AI数字病人临床思维训练」。AtomGit账号：`kechanghao`。

**当前版本：本地多专科原型。** 五个自造draft病例（心内、普外、神经内、呼吸内、内分泌），支持问诊、检查开单与回报、病历、鉴别与诊断提交、模拟治疗、封存评分（含鉴别规则评分、扣分说明和改进建议）及回放；教师可校验、保存、送审和发布病例。真实Nexent适配器、可信MCP服务和封存后反馈链已实现，**真实问诊至封存反馈已在新合成训练重试后贯通；模型草稿须教师复核后展示，连接稳定性、语义质量及完整验收仍待完成**。全部病例未经医学审核，仅用于工程验证；正式身份、正式评测尚未完成。

> 教学模拟环境，非真实患者，不用于真实诊疗。

当前界面已优化为四阶段工作台，包含未保存病历封存保护及教师可读证据。全量462项通过，Edge新合成训练连续走通真实问诊至封存、反馈待复核和教师只读原件；桌面/390px及键盘检查留证。最后回答可见6.203秒，前次出现54秒长尾，仍未达到5秒目标。见[界面说明](docs/ui-workspace.md)、[当前复盘](docs/status-review.md)、[最终缺口](docs/release-readiness.md)与[开发/官方流程](docs/milestones.md)。五专科病例仍待医学审核，过程仓库工程PR #1不是最终官方验收PR。

## 文档

| 用途 | 文档 |
|---|---|
| 查看当前完成项、不足与下一步 | [现状复盘](docs/status-review.md) |
| 理解架构与约束 | [技术方案](docs/technical-plan.md) |
| 继续开发、处理已知问题及官方节点 | [开发计划](docs/milestones.md) |
| 核对指标与最终交付物 | [验收计划](docs/acceptance-plan.md) |
| 核对五专科决策、时轴与待医学审核事项 | [决策审查表](docs/clinical-decision-review.md) |
| 中期演示与医学审核 | [演示与独立复现](docs/stemi-demo.md)、[审核与验收记录](docs/clinical-review-and-evaluation.md) |
| 配置平台、MCP及反馈链 | [Nexent集成](docs/nexent-integration.md)、[配置字段草案](docs/agent-config-draft.md) |
| 运行验证及维护依赖 | [测试说明](docs/testing.md) |
| 核对来源、许可与AI使用 | [第三方资源与AI工具披露](docs/third-party-and-ai-disclosure.md) |
| 查看版本变化 | [更新记录](CHANGELOG.md) |

日常开发以本README和上述正式文档为入口。旧方案、历轮复核、日志、临时脚本和打包副本不放在项目目录；尚未解决的工作统一记录在开发计划中。

## 目录

| 目录 | 内容 |
|---|---|
| backend/app | API、病例校验、状态机、存储、评分及适配器 |
| backend/data | 本地数据库、备份及教师病例，保留且不提交 |
| frontend | Streamlit学生训练及教师病例管理界面 |
| cases | 自造结构化模拟病例 |
| tests | 核心规则及API回归测试 |
| docs | 技术、计划、验收、配置、测试及许可文档 |
| docs/evidence | 仅保存脱敏的真实平台验证证据，含真实问诊、独立反馈成功及同一训练的超时复测 |
| knowledge | 封存后反馈用教学依据草稿（未审核） |
| deliverables/AgentsHub | 最终智能体提交目录，当前为骨架 |
| .github/workflows | 自动测试与依赖审计 |
| .venv | 本地Python运行环境，不提交 |

## 本地运行

使用Python 3.11及锁定依赖；CI采用Python 3.11。本地开发模式允许自填演示身份，不用于公开部署。

在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --require-hashes -r requirements.lock
$env:APP_ENV = "development"
.\.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --reload
```

另开终端，在同一目录执行：

```powershell
.\.venv\Scripts\streamlit run frontend\app.py
```

浏览器打开Streamlit显示的地址（通常为本机8501端口），后端默认地址为 `http://127.0.0.1:8000`。页面使用 `demo-` 或 `student-` 开头的演示身份；未设置 `APP_ENV=development` 时后端拒绝自填身份。

SQLite当前固定保存在 `backend/data/app.db`。`backend/.env.example`仅列配置，不会自动加载，其中 `DATABASE_URL`、`SHOW_REFERENCE_DIAGNOSIS`尚未接入实现。`INTEGRATION_DB_PATH`、`KNOWLEDGE_DIR`的相对路径统一从项目根目录解析，示例分别为`backend/data/integration.db`和`knowledge`，与启动目录无关。不要用旧版 `data/ai_patient.sqlite3` 覆盖当前数据库：两者格式及API不同，旧版 `AI_PATIENT_DB` 参数不再适用；旧数据应另存，不直接迁移。

默认 `NEXENT_MODE=simulator`（本地确定性模拟）。`NEXENT_MODE=nexent` 需要真实Nexent实例、已发布的病人/反馈智能体、API Key，并另起可信MCP服务：设置 `MCP_SERVICE_TOKEN` 后在 `backend` 目录执行 `python -m app.mcp_server`，再按[集成文档](docs/nexent-integration.md)在Nexent注册。未配置完整时后端拒绝而不是回退伪造结果。教师接口 `/api/teacher/*` 与正式身份认证尚未接入真实身份服务，开发模式下以 `teacher-` 前缀身份代替。

## 学生操作

1. 新建病例训练，或从列表恢复自己的已有训练。
2. 逐项问诊，按当前病例选择检查，推进模拟时间后查看结果。
3. 保存病历，填写鉴别诊断与支持/反对证据，提交诊断；返回病例库前会提醒处理未保存病历。证据请引用已实际获得的信息，过短、含糊或无法可靠对应的文字进入复核。
4. 按页面明确确认模拟治疗执行。改选治疗或成功执行后须重新确认。等待和治疗会影响轨迹，请区分计划与实际执行。
5. 提交并封存训练，查看规则评分和回放。真实模型反馈生成后显示待教师复核，将训练编号提供给教师，复核后点击刷新；封存后只读，可另开新训练。

检查、治疗和鉴别候选使用中文选项，证据通过事件列表选择。2026-11-12用Edge自动操作同一合成训练，分段恢复完成问诊至封存、反馈待复核、教师读取及学生恢复，另验证提交后即时刷新和390px布局。完整人工操作、无障碍及五科页面验收仍未完成。正式人员评测尚未完成，当前分数不能视为正式成绩。

## 教师操作

侧栏切换到「教师病例管理」，使用本地开发账号 `teacher-demo`。选择模板并新建草稿，编辑症状、触发词、检查及回报；复杂配置可在高级配置中修改，也可导入、下载病例JSON。每次保存产生新版本，不改写历史训练引用的内容。

病例先通过结构校验，再提交医学审核；只有真实审核完成后才能填写审核人、日期并明确确认发布。评分证据类型使用固定集合，事实不能为空；当前不支持检查前置表达式、免开单检查和评分阶段截止配置，相应非默认值会被拒绝。披露级别与主动回答标记仅作编写备注，不绕过逐问披露。即时规则的anchor_event须匹配本病例治疗trigger；延误规则须指定已配置检查。取消条件中的治疗ID或trigger仅匹配已执行动作；明确配置plan_treatment才按计划取消。未知名称、即时规则混用延误取消字段以及动作/治疗/规则命名歧义会被拒绝。程序校验不代表医学审核。切换账号、工作空间或后端地址会清除缓存的病例和训练内容。封存反馈展示适用教学依据的题名和来源，含参考答案的内容受保护。

处置评分量表可在高级JSON中填写`required_prior_exam_ids`检查ID列表。只有首次实际执行前已回报全部指定结果，该项才自动计分；开单、待回报、事后补查或再次执行不补回事前证据。缺证据的已执行项暂计0并进入教师复核；未配置此字段的旧版本行为保持。此机制只核对证据时序，不判定检查数值、复测新鲜度或治疗适用性，不能单独解决DKA医学恰当性问题。

真实模型草稿（包括历史记录）只向教师开放；结构校验通过不等于评语正确，须逐项核对证据后保存修订。教师任务切换到「训练反馈复核」，填写学生账号和完整训练编号（学生在封存页面「向教师提供复核信息」中复制）。载入后核对原始反馈、封存评分、学生提交、事件及教学依据，逐环节填写评语、建议、真实事件引用与适用依据，并填写复核原因。保存形成独立复核版本，记录教师账号、时间、原因与修订内容；原始反馈、训练轨迹及分数不改写。尚未生成原始反馈的封存训练也可提交教师反馈。重复请求不重复保存，旧版本并发修改返回409，须重新载入并合并修改。

学生点击「刷新已存反馈与教师复核」后查看最新通过安全校验的复核版本。复核理由和教师内部原件不向学生公开；新版本仍不符合当前校验时继续提示复核，不回退展示旧版本。此流程只复核反馈文字与引用，**不调整原规则分数或其 `needs_review` 状态**。开发演示教师账号不属于正式身份认证。

教师任务「评分复核与裁定」用于处理已封存且原规则评分为 `needs_review` 或 `complete` 的训练。`complete`仅表示规则计算完成，发现争议时仍可复核；页面显示原始状态，必须填写具体复核原因。未封存、缺失评分或评分失败的训练不能裁定。逐项核对原量表和封存前事件，填写分数、学生可见说明、证据和内部原因，明确确认后另存裁定版本。正分必须引用真实事件，禁止超出原分项上限或公开隐藏参考答案；重复提交幂等，旧版本提交返回409。学生恢复训练或刷新后可同时看到原规则分和最新教师裁定。裁定独立保存在集成库，不修改原始分数或反馈；其用途是教学复盘，不代表正式成绩、医学审核或独立专家评测。

教师病例、反馈及评分入口展示专科临床依据覆盖情况。通用反馈写作规范不计入临床覆盖；有条目也不表示已医学审核。临床依据按病例ID及明确列出的病例版本匹配；保存新病例版本或复制新ID不会自动继承专科覆盖。新增反馈及教师复核独立保存当时的依据正文、题名、来源、修订与内容哈希；学生仅接收经过安全过滤的题名/来源和修订。历史记录缺少快照时不回填当前来源，教师核对后可保存新的复核版本。

## 验证与依赖维护

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m compileall -q backend/app frontend tests
```

直接依赖在 `backend/requirements.txt`，根 `requirements.txt`引用该清单；实际安装及CI使用含传递依赖和哈希的 `requirements.lock`。更新依赖后重新生成锁文件，并运行测试和安全检查；详见[测试说明](docs/testing.md)。

## 交付与许可证

过程仓库：[GitHub](https://github.com/kechanghao666/ai-digital-patient-clinical-reasoning-training)。官方Fork：[kechanghao/AgentsHub](https://gitcode.com/kechanghao/AgentsHub)，课题目录为 `行业智能体/医疗/AI数字病人临床思维训练/`。

2026-11-09 09:17已收到报名成功公开回复（atomgit_01），D按11月9日安排；按D+3准备11月12日前完成启动手续、D+30准备12月9日前Demo。[11月10日03:47启动反馈](https://gitcode.com/org/ModelEngine/discussions/26#discussion-reply-fe588053eddc46e0b4e1d71c4e8fb863)已公开核实；已向atomgit_01发出维护者邀请（负责人陈述），邀请接受、角色认可、过程登记及GitHub CI认可仍待确认。启动材料上传不代表官方验收；最终须完成真实平台、评测及完整交付，再向官方仓库提交PR并合入。具体节点见[开发计划](docs/milestones.md)。

项目使用[MIT许可证](LICENSE)。官方依据：[活动公告](https://gitcode.com/org/ModelEngine/discussions/26)、[需求包](https://atomgit.com/ModelEngine/AgentsHub/blob/main/需求包详情与交付要求.md)。
