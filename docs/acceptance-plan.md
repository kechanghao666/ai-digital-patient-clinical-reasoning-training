# 官方验收与证据计划

依据：[公告](https://gitcode.com/org/ModelEngine/discussions/26)、[通用及课题1标准](https://atomgit.com/ModelEngine/AgentsHub/blob/main/需求包详情与交付要求.md)。2026-11-08重新核对官方AgentsHub仓库`c5a9e483d2c5215d7541fd651f0540845e5b3a01`的通用及课题1要求；本轮公告网页未返回可读取正文，未据此更新报名、私信或内部登记状态。

| 指标 | 官方目标 | 证据及当前状态 |
|---|---|---|
| 平台底座 | 真实Nexent编排、工具调用、知识库挂载 | 本地适配器、MCP及反馈契约已有测试；配置路径本批已修复；真实实例证据待完成 |
| 专科与闭环 | ≥5不同专科，全部走通问诊→开单→回报→病历→评分 | 5个draft病例已通过本地脚本回放；医学审核及真实平台走通待完成 |
| 教师新增 | 页面可新增结构化病例 | 页面、接口、持久化及草稿送审已本地验证；正式身份与人员验收待完成 |
| 问答一致性 | 20轮抽检≥95% | 冻结设定表、原问答及逐条判定，待正式评测 |
| 评分一致性 | ≥3名真实学生，与专家分项评分一致率≥85% | 学生轨迹、教师评分、分母/容差/聚合口径，待确认与采集 |
| 错误演进 | 明显错误决策分支触发100%，反馈准确 | 预列错误用例、状态与反馈，现有本地夹具不替代正式覆盖 |
| 五环节与回放 | 分项分数、扣分、建议、轨迹回放 | 本地规则已有，产品及专业验证待完成 |
| 模拟数据 | ≥10组正常、异常和边界样本 | 样本组数与专科病例数分别统计，待完整整理 |
| 重复性 | 同输入3次结论方向一致 | 三次真实执行，待正式验证 |
| 时延 | 首字≤5s，端到端≤60s | 实际环境、计时边界和日志，待真实平台测量；占位状态不能冒充病人首字 |
| 冷部署与现场 | 干净环境部署成功，随机≥3条用例 | 同机新虚拟环境锁定安装、服务启动与3例HTTP回放已通过；异机部署、平台导入及现场演示待完成 |
| 专属现场演示 | 五例均可用，随机1病例 | 与通用≥3条用例同时满足，不相互替代 |
| 安全合规 | 教学提示、自造/脱敏数据、引用、许可、无高危告警 | 页面、来源、许可及实际CI结果，正式交付前复核 |

先向主办方确认评分一致率具体口径。合成轨迹、开发者自测、真实学生样本分开报告，不伪造人工评分或评测通过率。实施批次见[开发计划](milestones.md)，技术边界见[技术方案](technical-plan.md)。

## 正式采样前需要确定

- 评分85%的一致率分母、分项分数容差和汇总方式，20轮问答是总量还是逐病例统计，10组业务样本的计数方式；未确认时不得自定口径宣称达标。
- 冻结病例/量表/依据修订/代码/平台与模型配置，记录学生轨迹、独立教师评分及系统输出；预先预约人员，不能用合成轨迹替代学生样本。专业专家对照须独立于系统分数及教师事后裁定，避免用同源修正结果验证自身。
- 逐例列出适用的错误检查、延误和处置分支，验证触发和反馈；当前每例单条延误路径的工程测试不足以覆盖100%指标。
- 已修复范围和剩余工作见[开发计划](milestones.md)；评分、配置及演进边界已修复，反馈文字的授权复核已实现，独立评分裁定已本地实现；正式采样前仍须完成专业依据、错误分支矩阵及正式身份。五环节结构已有本地验证，仍须正式人员对照评测。needs_review或只显示分数表不等于已完成正式评分。

2026-11-08当前源码独立复测339项通过（54.91秒），N01依据历史留存再次验证稳定。N02–N05仍待修复；本轮新增N06：五例未开检查等待240分钟均无演进，说明现有检查回报后规则未覆盖此路径，具体医学阈值须审核；N07：时间配置接受NaN/Infinity，NaN TTL可导致回合写库失败。总超时另补无换行慢流反例，读取超时等于总预算也不足以保证截止。具体输入、影响、最小修复及门槛见[现状复盘](status-review.md#本轮新增反例与最小修复方向)。339项只支持已有工程场景，不能替代N02–N07关闭、专业审核或真实平台评测。

正式性能记录至少区分请求开始、平台首事件、校验后患者回答首次展示和请求完成，前端加载/思考提示不能作为首字。当前first_event_ms记录过程事件，尚不能用于证明≤5秒。若不做流式患者回答，则以学生实际收到完整有效回答的时刻判定首字；后续若增加流式显示，每段仍须满足披露约束。超时反例只证明工程计时缺陷，不是实际平台性能成绩。

后续截止控制批已在本地关闭N03/N07：可取消截止、停止预算及有限时间参数有真实localhost回归，完整365项通过。N04部分完成：平台首过程/最终文本事件、后端安全校验/API准备、前端接收分别记录；前端接收不是浏览器绘制，真实首字和端到端采样仍待进行。N02/N05/N06仍未关闭，候选范围见[决策审查表](clinical-decision-review.md)。以上更新取代前文对N03/N07“仍待修复”的当前判断，前文保留审计输入以便追溯。

## 已有工程场景索引

下表为可执行回归的准备索引，**不作为官方≥10组完整业务样本、医学评测或真实平台证据计数**。正式样本仍须记录版本、完整输入、预期、实际输出和逐项判定，并经专业核对。

| 编号 | 场景 | 自动验证入口（tests目录） |
|---|---|---|
| ENG-01 | 正常五环节闭环 | test_cases_config.py::test_second_specialty_runs_the_full_loop_with_five_stage_scoring |
| ENG-02 | 相同请求重放 | test_core.py::test_idempotency_same_request_returns_original |
| ENG-03 | 同编号更换内容 | test_core.py::test_idempotency_rejects_changed_payload |
| ENG-04 | 治疗须明确确认 | test_core.py::test_treatment_requires_explicit_confirmation |
| ENG-05 | 封存后写入拒绝 | test_core.py::test_sealed_state_rejects_new_actions |
| ENG-06 | 零延迟检查可引用 | test_quality_boundaries.py::test_immediate_results_are_selectable_and_scoreable |
| ENG-07 | 延误规则与处置取消 | test_cases_config.py::test_delay_rule_is_bound_to_configured_exam_and_treatment |
| ENG-08 | 治疗触发公开演进 | test_quality_boundaries.py::test_treatment_evolution_has_public_updates_and_replay |
| ENG-09 | 非法或重复鉴别候选 | test_current_audit.py::test_unknown_or_duplicate_differential_is_rejected_without_writing |
| ENG-10 | 教师裁定保留原分 | test_score_reviews.py::test_score_review_keeps_original_and_exposes_separate_student_projection |
| ENG-11 | 裁定并发与重试 | test_score_reviews.py::test_score_review_is_idempotent_and_stale_revision_conflicts |
| ENG-12 | 写作规范不补临床覆盖 | test_knowledge_coverage.py::test_general_feedback_rules_do_not_fill_missing_clinical_coverage |

## 必需交付物

- [ ] 官方PR合入main的课题目录；最终agent.json由平台导出。
- [ ] 前端/后端源码、依赖记录与通过的CI。
- [ ] 知识资产及来源/授权。
- [ ] 可复现模拟数据。
- [ ] 评测集、标注、样本量。
- [ ] 从零部署文档。
- [ ] 使用说明与截图。
- [ ] 3～8分钟闭环视频。
- [ ] 逐项测试报告。
- [ ] LICENSE、README、CHANGELOG及第三方/AI披露。

官方将设计说明（架构、分工、Prompt和工具清单）列为**建议项**，本项目保留作为质量要求，不把它写成官方必需项。

常规综合通过需加权≥80，验收指标维度≥70%，单项不能以其他维度补分，合规安全不得为0。通过仍需择优入额，不能提前承诺奖金。
