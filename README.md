# AI数字病人临床思维训练系统

基于结构化模拟病例的医学教学训练项目，参与 ModelEngine/Nexent 开发者活动课题1。AtomGit账号：`kechanghao`。

**当前版本：本地多专科原型。** 五个自造draft病例（心内、普外、神经内、呼吸内、内分泌），支持问诊、检查开单与回报、病历、鉴别与诊断提交、模拟治疗、封存评分（含鉴别规则评分、扣分说明和改进建议）及回放；教师可校验、保存、送审和发布病例。真实Nexent适配器、可信MCP服务和封存后反馈链已按官方公开协议实现并通过本地契约测试，**但尚无真实Nexent实例，真实平台联调未完成**。全部病例未经医学审核，仅用于工程验证；正式身份、正式评测尚未完成。

> 教学模拟环境，非真实患者，不用于真实诊疗。

## 文档

- [技术方案与实施边界](docs/technical-plan.md)
- [开发计划与官方流程](docs/milestones.md)
- [验收指标与交付清单](docs/acceptance-plan.md)
- [Nexent集成：契约、环境核实、资源方案](docs/nexent-integration.md)
- [Nexent配置字段草案](docs/agent-config-draft.md)
- [测试说明](docs/testing.md)
- [第三方资源与AI工具披露](docs/third-party-and-ai-disclosure.md)
- [更新记录](CHANGELOG.md)

## 目录

| 目录 | 内容 |
|---|---|
| backend/app | API、病例校验、状态机、存储、评分及适配器 |
| frontend | Streamlit学生训练界面 |
| cases | 自造结构化模拟病例 |
| tests | 核心规则及API回归测试 |
| docs | 技术、计划、验收、配置、测试及许可文档 |
| knowledge | 封存后反馈用教学依据草稿（未审核） |
| deliverables/AgentsHub | 最终智能体提交目录，当前为骨架 |

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

SQLite当前固定保存在 `backend/data/app.db`。`backend/.env.example`仅列配置，不会自动加载，其中 `DATABASE_URL`、`SHOW_REFERENCE_DIAGNOSIS`尚未接入实现。不要用旧版 `data/ai_patient.sqlite3` 覆盖当前数据库：两者格式及API不同，旧版 `AI_PATIENT_DB` 参数不再适用；旧数据应另存，不直接迁移。

默认 `NEXENT_MODE=simulator`（本地确定性模拟）。`NEXENT_MODE=nexent` 需要真实Nexent实例、已发布的病人/反馈智能体、API Key，并另起可信MCP服务：设置 `MCP_SERVICE_TOKEN` 后在 `backend` 目录执行 `python -m app.mcp_server`，再按[集成文档](docs/nexent-integration.md)在Nexent注册。未配置完整时后端拒绝而不是回退伪造结果。教师接口 `/api/teacher/*` 与正式身份认证尚未接入真实身份服务，开发模式下以 `teacher-` 前缀身份代替。

## 学生操作

1. 新建病例训练，或从列表恢复自己的已有训练。
2. 逐项问诊；开立心电图、肌钙蛋白，推进模拟时间后查看结果。
3. 保存病历，填写鉴别诊断与支持/反对证据，提交诊断。
4. 按页面明确确认模拟治疗执行。等待和治疗会影响轨迹，请区分计划与实际执行。
5. 提交并封存训练，查看分项反馈和回放；封存后只读，可另开新训练。

当前检查/治疗选项仍有内部标识，鉴别诊断需引用回放中的事件ID；尚未达到正式学生端体验。部分评分显示待人工复核，不能视为正式成绩。

## 验证与依赖维护

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m compileall -q backend/app frontend tests
```

直接依赖在 `backend/requirements.txt`，根 `requirements.txt`引用该清单；实际安装及CI使用含传递依赖和哈希的 `requirements.lock`。更新依赖后重新生成锁文件，并运行测试和安全检查；详见[测试说明](docs/testing.md)。

## 交付与许可证

过程仓库：[GitHub](https://github.com/kechanghao666/ai-digital-patient-topic1)。官方Fork：[kechanghao/AgentsHub](https://gitcode.com/kechanghao/AgentsHub)，课题目录为 `行业智能体/医疗/AI数字病人临床思维训练/`。

完整报名提交后可等待统一核验，无需重复报名或催审。报名成功日期、过程登记和小助手权限尚未核实。启动材料上传不代表官方验收；最终须完成真实平台、评测及完整交付，再向官方仓库提交PR并合入。具体节点见[开发计划](docs/milestones.md)。

项目使用[MIT许可证](LICENSE)。官方依据：[活动公告](https://gitcode.com/org/ModelEngine/discussions/26)、[需求包](https://atomgit.com/ModelEngine/AgentsHub/blob/main/需求包详情与交付要求.md)。
