# 实现与规格对应关系（首期）

| 规格要求 | 首期实现 | 证据/状态 |
|---|---|---|
| 确定性底座在业务后端 | `state_machine.py`、`storage.py` | 已实现并有单元测试 |
| owner/session/case/version 绑定 | `main.py`、`mcp_tools/context.py` | 本地开发身份门禁与会话归属已实现；正式登录凭据、真实平台运行上下文待联调 |
| 原子事实披露 | `disclosure.py`、`patient_state_api.py` | 已实现；低置信 NLP 分类待增强 |
| 未配置/未开单/待返回/已返回/失败分离 | `ExamState`、`OrderInstance` | 已实现 pending/ready；失败和复查 UI 待补 |
| 订单实例与病例定义分离 | `ExamDefinition` / `OrderInstance` | 已实现 |
| 治疗计划与确认执行分离 | `PLAN_TREATMENT` / `EXECUTE_TREATMENT` | 已实现并测试 |
| 事件幂等与同会话持久化 | `SessionRepository`、`StateMachine._check_request`、`frontend/app.py` | 已实现；后端保存带 state_version 冲突检查，前端保存待决操作的 payload/request_id，覆盖同 ID 重放、旧快照竞争和丢失应答后的安全重试 |
| 封存截止点与旧运行拒绝 | `/seal`、`StateMachine.apply` | 所有允许入口统一进入封存逻辑；封存时绑定事件截止点和评分快照，空训练使用 `seal_seq=0` |
| 五环节规则评分 | `scoring.py`、`models.py`、`cases/stemi_case_v1.json` | 自动给分仅限明确、有限且完整匹配的目标诊断肯定表达；否定、未证实、历史描述、前后冲突和无法识别文本不默认给分；检查优先级按 rubric 显式顺序约束审计，医学 rubric 审核待完成 |
| 鉴别诊断证据 | `DifferentialCandidate`、`scoring.py` | 仅接受本会话封存前且已实际可用的事件；检查结果必须来自等待后已返回的结果，医学支持/反对关系未配置可审计规则时统一为 `needs_review` |
| 出站白名单过滤 | `sse_filter.py`、`main.py` | 学生响应已使用公开投影；真实 Nexent SSE 联调待完成 |
| 本地与真实 Nexent 分离 | `adapters.py` | 本地适配器已验证；真实适配器待平台凭据 |
| 产品免责和状态文案 | `frontend/app.py` | 已实现首期学生流程、封存后只读反馈、临时故障保留训练入口、失败当轮提示、按请求类型清理旧错误并在本轮最终状态统一渲染、危急病情变化展示和待复核成绩标识 |
| 学生闭环辅助流程 | `frontend/app.py`、`main.py`、`storage.py`、`state_machine.py` | 已接入鉴别诊断、病历记录、训练回放、已有训练恢复和封存后新建训练；回放使用公开事件投影，病历记录以可审计事件保存 |
| 正式交付 11 项 | `README.md`、`docs/` | 首期工程材料已建；五专科、视频、真实 agent 导出和 PR 待完成 |

## 未宣称完成的证据

医学审核、主办方评分口径、真实 Nexent/知识库导入导出、20 轮正式抽检、3 名以上学生/专家盲评、独立留出集、官方性能门槛和五专科资产均未由本地代码测试替代。
