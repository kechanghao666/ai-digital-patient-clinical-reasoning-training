# AI数字病人临床思维训练

基于结构化模拟病例的医学教学训练项目，参加[魔擎开发者激励1期-基于Nexent的智能体开发](https://gitcode.com/org/ModelEngine/discussions/26)，对应课题1「AI数字病人临床思维训练」。AtomGit账号：`kechanghao`。

**当前版本：本地多专科原型。** 五个自造draft病例（心内、普外、神经内、呼吸内、内分泌），支持问诊、检查开单与回报、病历、鉴别与诊断提交、模拟治疗、封存评分（含鉴别规则评分、扣分说明和改进建议）及回放；教师可校验、保存、送审和发布病例。真实Nexent适配器、可信MCP服务和封存后反馈链已按官方公开协议实现并通过本地契约测试，**但尚无真实Nexent实例，真实平台联调未完成**。全部病例未经医学审核，仅用于工程验证；正式身份、正式评测尚未完成。

> 教学模拟环境，非真实患者，不用于真实诊疗。

2026-11-08质量加固：已修复本轮复盘中的9项本地边界问题，新增反馈来源展示；评分对不明确文字及无效引用保留复核，不自动宣布通过。当前继续完善专科教学依据、教师复核和正式环境，尚不宜开展正式评测；状态与通过条件统一见[开发计划](docs/milestones.md#优先修复)。

## 文档

| 用途 | 文档 |
|---|---|
| 理解架构与约束 | [技术方案](docs/technical-plan.md) |
| 继续开发、处理已知问题及官方节点 | [开发计划](docs/milestones.md) |
| 核对指标与最终交付物 | [验收计划](docs/acceptance-plan.md) |
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
| docs/evidence | 仅保存脱敏的真实平台验证证据，当前暂无 |
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
5. 提交并封存训练，查看分项反馈和回放；封存后只读，可另开新训练。

检查、治疗和鉴别候选使用中文选项，证据通过事件列表选择。学生闭环、教师草稿送审和手机布局已完成本地浏览器检查；正式人员评测尚未完成，当前分数不能视为正式成绩。

## 教师操作

侧栏切换到「教师病例管理」，使用本地开发账号 `teacher-demo`。选择模板并新建草稿，编辑症状、触发词、检查及回报；复杂配置可在高级配置中修改，也可导入、下载病例JSON。每次保存产生新版本，不改写历史训练引用的内容。

病例先通过结构校验，再提交医学审核；只有真实审核完成后才能填写审核人、日期并明确确认发布。评分证据类型使用固定集合，事实不能为空；当前不支持检查前置表达式、免开单检查和评分阶段截止配置，相应非默认值会被拒绝。披露级别与主动回答标记仅作编写备注，不绕过逐问披露。程序校验不代表医学审核。切换账号、工作空间或后端地址会清除缓存的病例和训练内容。封存反馈展示适用教学依据的题名和来源，含参考答案的内容受保护；不符合当前规则的历史反馈返回复核提示，原记录保留，授权复核与修订流程尚未实现。

## 验证与依赖维护

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m compileall -q backend/app frontend tests
```

直接依赖在 `backend/requirements.txt`，根 `requirements.txt`引用该清单；实际安装及CI使用含传递依赖和哈希的 `requirements.lock`。更新依赖后重新生成锁文件，并运行测试和安全检查；详见[测试说明](docs/testing.md)。

## 交付与许可证

过程仓库：[GitHub](https://github.com/kechanghao666/ai-digital-patient-clinical-reasoning-training)。官方Fork：[kechanghao/AgentsHub](https://gitcode.com/kechanghao/AgentsHub)，课题目录为 `行业智能体/医疗/AI数字病人临床思维训练/`。

完整报名提交后可等待统一核验，无需重复报名或催审。报名成功日期、过程登记和小助手权限尚未核实。启动材料上传不代表官方验收；最终须完成真实平台、评测及完整交付，再向官方仓库提交PR并合入。具体节点见[开发计划](docs/milestones.md)。

项目使用[MIT许可证](LICENSE)。官方依据：[活动公告](https://gitcode.com/org/ModelEngine/discussions/26)、[需求包](https://atomgit.com/ModelEngine/AgentsHub/blob/main/需求包详情与交付要求.md)。
