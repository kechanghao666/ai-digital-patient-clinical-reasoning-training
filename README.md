# AI数字病人临床思维训练系统

本项目参与「魔擎开发者激励1期 · 基于 Nexent 的智能体开发」课题1「[教学] AI 数字病人临床思维训练」。AtomGit 参与账号为 `kechanghao`，本仓库为 GitHub 开发过程仓库；最终成果须另按官方流程合入 ModelEngine/AgentsHub。

首期实现第一例疑似 STEMI 的本地教学模拟闭环。所有病例和患者资料均为自造模拟内容；首例病例仍是工程夹具，未完成医学审核，不用于真实诊疗。当前不是完整五专科产品，也尚无真实 Nexent 联调通过或官方验收通过的证据。

## 开发与交付入口

- [启动方案与架构](docs/technical-plan.md)
- [里程碑与责任分工](docs/milestones.md)
- [官方验收指标及证据清单](docs/acceptance-plan.md)
- [Nexent 配置字段初稿](docs/agent-config-draft.md)
- [开发进度](docs/progress/2026-11-07.md)
- [本次验证结果](docs/verification-20261107.md)
- [官方启动登记待办](docs/official-handoff.md)
- [本地与 GitHub 基线同步说明](docs/repository-sync.md)
- [第三方依赖与 AI 工具披露](docs/third-party-and-ai-disclosure.md)
- [AgentsHub 课题目录骨架](deliverables/AgentsHub/行业智能体/医疗/AI数字病人临床思维训练/README.md)

完整设计见[技术方案](02-完整技术方案.md)、[实施规格](13-实施规格书-交付代码工程agent.md)与[正式产品要求](16-正式产品与交付要求补充稿_供开发agent采用.md)。设计目标与已实现功能分别以实现映射和实际证据判定。

## 本地启动

```powershell
# 在仓库根目录执行；Python 3.11 或以上
python -m venv .venv
.\.venv\Scripts\pip install --require-hashes -r requirements.lock
$env:APP_ENV = "development"
.\.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --reload
```

另开终端启动前端：

```powershell
.\.venv\Scripts\streamlit run frontend\app.py
```

运行测试：

```powershell
.\.venv\Scripts\pytest -q
```

直接依赖维护在 `backend/requirements.txt`，根 `requirements.txt` 引用该清单；实际部署与CI使用含传递依赖和文件哈希的 `requirements.lock`。更新依赖后重新生成锁文件并运行测试和安全检查：`uv pip compile --python-version 3.11 --universal --generate-hashes backend/requirements.txt -o requirements.lock`。

默认使用本地确定性病人适配器。设置 `NEXENT_MODE=nexent` 只会启用真实适配器边界；未配置目标平台凭据时会明确失败，不会伪装成真实联调成功。

当前 SQLite 固定存放在 `backend/data/app.db`；`backend/.env.example` 是配置清单，不会自动加载，其中 `DATABASE_URL` 尚未接入实现。旧版 `AI_PATIENT_DB` 参数不适用于当前版本。真实适配器仍待实现，填写凭据本身不会让它自动可用。

本地演示使用自填身份时，启动后端前显式设置 `APP_ENV=development`；未设置或设置为其他值时，后端拒绝自填 `X-Owner-Id`，正式身份必须由已验证登录凭据注入。

## 当前实现与待完成

已实现：病例严格校验、开发模式身份门禁、公开出站投影、受控原子披露、检查订单和模拟时钟、延误演进夹具、结构化治疗确认、SQLite 版本冲突控制、持久幂等、封存评分快照、五环节规则评分、回放所需事件、Streamlit 学生流程和本地适配器。

训练恢复使用真实 `session_id` 作为选择值，摘要只作为标签，因此相同病例、相同时间和相同事件数的多次训练仍可分别恢复。恢复后的公开状态会回填学生本人提交的诊断、鉴别候选及支持/反对证据、治疗计划/执行明细；这些投影不包含隐藏答案、事实 ID 或内部调试字段。历史快照目录通过 `pytest.ini` 排除，避免收集同名测试文件造成误判。

待完成：医学审核、真实 Nexent 北向联调及知识库导入/导出、五专科资产、真实学生/专家独立评测、主办方统计口径确认、正式 `agent.json` 导出和 AgentsHub PR 合入。

## 官方流程与许可证

报名确认后3个自然日内需完成官方 Fork、目录骨架、过程仓库登记、经核实的小助手管理权限和技术方案提交；每7个自然日至少更新一次过程仓库，30个自然日内提供中期演示。GitHub 仓库不能替代官方 AtomGit Fork 或最终 PR 合入，私有仓库还需落实官方审查访问；本次同步不改变仓库可见性。

使用本项目应遵守 [MIT License](LICENSE)。官方来源：[活动公告](https://gitcode.com/org/ModelEngine/discussions/26)、[交付要求](https://atomgit.com/ModelEngine/AgentsHub/blob/main/需求包详情与交付要求.md)。
