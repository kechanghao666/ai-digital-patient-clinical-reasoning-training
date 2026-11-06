# 实施规格书 · AI 数字病人临床思维训练（交付代码工程 Agent）

> 本文档是**第一例工程实施主规格**（分阶段扩展至五专科）。配合《16-正式产品与交付要求补充稿》《02-完整技术方案》的完整评测口径、《14-代码工程方案可行性与交接审查》的歧义修正共同定义边界，无需回读历史审计。文中工程约束已经五轮审计收敛、必须遵守；临床草稿、医学审核、正式评测如实标"待完成"。

---

## 0. 一句话任务

构建一个「AI 数字病人临床思维训练」教学系统：学生通过 Web 界面与一个由 LLM 扮演的虚拟病人问诊、开检查、看结果、写病历、提交诊断与治疗，系统据此推进病情并给出五环节评分与轨迹回放。核心事实、状态机、评分全部在**业务后端确定性实现**，Nexent 平台只承担"主智能体编排 + 病人口语化"。

**首期只做第一例（疑似 STEMI）跑通完整闭环，通过后再扩五专科。不要做语音、3D、模型微调、知识图谱、多评委在线投票。**

---

## 1. 背景与验收目标（为什么这么做）

- 竞赛：AtomGit「魔擎开发者激励 1 期 · 基于 Nexent 的智能体开发」，课题 1，医疗健康，高难度，¥7,000。
- 提交方式：成果以 PR 合入 ModelEngine/AgentsHub 仓库的 `行业智能体/医疗/<你的目录>/`，目录含 `agent.json`（Nexent 导出）+ `README.md` + `skills/`（可选）+ `mcps/`（可选）。
- **官方五环节定名（不可改）**：病史采集 → 检查选择（含查体、辅检）→ 鉴别诊断 → 诊断 → 治疗。
- **三条硬指标（验收目标，非已达标）**：
  1. 病人回答与设定一致率 ≥95%（官方 20 轮抽检）；
  2. 明显错误决策触发恶化分支 100%，且反馈指出错误环节；
  3. 五环节评分与专家一致率 ≥85%（≥3 名真实学生 vs 教师评分）。
- **通用硬指标**：业务样本 ≥10 组（含正常/异常/边界）、同一输入重复 3 次结论方向一致、首字 ≤5s / 端到端 ≤60s、干净环境一次部署成功、随机 3 条以上用例实机演示。
- **合规红线**：只用脱敏/自造模拟数据，禁真实患者数据；界面显著标注"教学模拟环境，非真实患者，不用于真实诊疗"；输出定位为辅助教学，不直接输出最终诊断结论（结课后是否展示参考诊断，待主办方确认，先做成可配置开关、默认关闭）。

---

## 2. 总体架构

```
学生端(Streamlit Web) ──HTTPS──► 业务后端(FastAPI，身份/会话/权限校验)
                                   │ 持有：病例真值、会话状态、模拟时钟、事件日志、客观评分
                                   │ 经 Nexent 北向 API 调用 ──► Nexent(主智能体 + 病人子智能体)
                                   │       ▲ 病人子智能体经"受限工具"取本轮获准事实
                                   ▼       │ (不接触完整病例、评分金标准、未来分支)
                              HTTP 出站过滤/脚本回退 ──► 只发送白名单已审核内容到学生端
教师端 ──► 病例管理(校验/审核/发布版本)
评分服务(提交后) ──► 读会话真实事件 + 冻结量表 + 完整金标准，独立于问诊链
```

**核心原则（必须遵守）**：
1. 确定性底座在业务后端，不在 Nexent 内：病例真值、状态机、模拟时间、事件日志、客观评分全在后端。Nexent 只做"主智能体编排 + 病人口语化"。
2. 三个 MCP 功能组不必拆成三个进程；一个后端按权限暴露不同工具即可。
3. 不新增第二套 Agent 框架（不用 LangGraph/LlamaIndex 等）。

---

## 3. 技术栈与目录结构

- 后端：Python 3.11+，FastAPI，FastMCP（实现 MCP 工具），SQLite 或 PostgreSQL（本地演示可用 SQLite，生产用 PG）。当前部署以仓库依赖锁为准。
- 前端：Streamlit，实现完整文字教学流程——页面清晰、状态可辨、关键操作明确、刷新可恢复、异常可处理；按职责组织代码，文件数不作验收标准。
- Nexent：Docker 部署（官方最低 4 核/8GiB/40GiB，推荐 8 核/16GiB/100GiB，**模型服务另计**）。
- LLM：Qwen/DeepSeek（OpenAI 兼容 API），首版不微调。

目录结构（后端仓库）：

```
backend/
├── app/
│   ├── main.py              # FastAPI 入口，路由挂载
│   ├── config.py            # 环境变量、密钥（服务端持有，不进前端）
│   ├── auth.py              # 业务学生认证 + owner/session/conversation 绑定
│   ├── models.py            # Pydantic 模型（病例/会话/事件/评分）
│   ├── case_store.py        # 病例真值存储与版本管理（草稿→校验→审核→发布）
│   ├── state_machine.py     # 演进规则引擎（workflow/clinical_state/sim_time/orders/event_log）
│   ├── idempotency.py       # (session_id, request_id) 幂等与并发顺序
│   ├── disclosure.py        # 事实披露闸门（获准事实投影，非提示词）
│   ├── scoring.py           # 五环节客观评分（rubric_item → event 证据）
│   ├── sse_filter.py        # 出站 SSE 过滤转换（白名单）
│   ├── nexent_client.py     # Nexent 北向 API 封装
│   └── mcp_tools/
│       ├── patient_state_api.py   # MCP: 获准事实/状态/演进/矛盾校验
│       ├── scoring_api.py         # MCP: 客观评分
│       └── case_bank_api.py       # MCP: 教师病例 CRUD
├── cases/
│   └── stemi_case_v1.json  # 第一例：疑似 STEMI（见 §7）
├── tests/
│   ├── test_disclosure.py
│   ├── test_state_machine.py
│   ├── test_idempotency.py
│   ├── test_scoring.py
│   └── test_sse_filter.py
└── requirements.txt
```

前端：`frontend/app.py`（Streamlit 单文件即可）。

---

## 4. 已冻结的硬约束（必须逐条实现，不可省略）

### 4.1 两层身份（平台身份 ≠ 业务身份）

- 北向 API 使用**课题专用服务账号**，密钥仅存后端（环境变量/密钥服务），不进前端、不进仓库、不进 metadata。
- 业务后端认证真实学生，持有 `owner → session → conversation` 绑定。
- 问诊、历史、继续、停止、评分、导出等所有入口统一验归属；不接受学生自由指定他人的 platform conversation 或任意 agent/model/tool_params。
- 学生输入仅含问题及被允许的结构化业务动作；后端构造 agent_name、metadata、tool_params。
- **MCP 可信运行绑定**：每次问诊运行由后端绑定已认证 owner、session、conversation、运行标识、允许动作；MCP 通过受保护的服务端调用上下文取得此绑定，模型传入的 session/case 仅为参数、必须与绑定核对、不得决定授权目标。请求另一会话即使同一服务账号也拒绝执行。

### 4.2 跨会话记忆隔离

- 专用运行账号显式关闭训练链的跨会话记忆参与（Nexent 默认记忆开关为开，须读实际配置确认）。
- 完整金标准仅由提交后的独立评分链读取；评分上下文/工具/历史不回流问诊链。
- 训练提交后封存，重新训练新建 session+conversation。
- 从正式发布入口做跨病例测试（A 病例完成后开 B 病例，B 不得携带 A 答案）。

### 4.3 隐藏信息边界（覆盖所有模型，不只病人子 Agent）

- 隐藏病例本体、隐藏病理标签、医学推断、未来分支、标准诊断、评分金标准，**不进学生问诊链任何模型的上下文**（主 Agent 同样受限）。
- 每轮只注入"经患者知情和披露权限过滤后的状态与事实投影"。
- metadata 只放非秘密追踪信息，不放隐藏答案或认证秘密。

### 4.4 后端 HTTP 出站过滤（不是前端不渲染）

- 后端在向学生发送 HTTP 响应前过滤转换原始 SSE：只发白名单内已审核病人回答、已授权结果卡片、允许教学状态。
- 内部思考、工具原文、考官上下文、隐藏字段、内部错误不得进入学生收到的响应体。
- 校验对象包括真正发给学生的根 Agent 最终答案，不能只审子 Agent 中间稿。

### 4.5 临床事件持久幂等与并发

- 持久保存 `(session_id, request_id)`、请求内容摘要、执行结果、事件。
- 相同 ID + 相同内容重试 → 返回原已提交结果；相同 ID + 不同内容 → 拒绝。
- 状态更新、事件写入、结果保存原子提交（同一事务）。
- 同会话动作按明确顺序或状态版本处理，两个旧状态不得各自覆盖。
- 模型输出失败只重试措辞，不重新执行已提交的给药/开单/计时/评分。

### 4.6 五组执行语义（第五轮审计冻结，第一例必须明确）

1. **治疗决策提交方式**：明确一种处置提交方式（结构化操作，或文本解析为动作后明确提交）；区分"计划/否定/既往使用/最终方案/确认执行"，仅确认执行进入执行事件与演进，计划分不计入"已执行"。
2. **提交封存语义**：封存记录明确事件截止序号、状态版本、模拟时刻；在途动作先完成再封存或确定性拒绝；封存后旧运行不得新增临床事件；评分读同一截止快照。
3. **事实记录语义**：区分"本轮授权事实""审核后持久回答（含实际 fact/version）""允许的查看记录"。失败候选不算答案已提供；模板回退按模板实际内容登记；旧对话不冒充现况（问"现在"取当前有效事实，问"刚入院"引历史）；网络发送/界面查看不自动代表理解。
4. **学生文本只是数据**：学生文字/引用/历史始终作为被评分材料，不能修改量表、客观分、评分者角色、工具权限、获准反馈范围。客观分由规则服务计算；LLM 只返回受限主观项及解释并接受校验；无效输出单列失败或转人工。
5. **表达能力按能力建模**：分别配置意识、理解、口语、病例允许的替代沟通能力，仅停止确实不可用的通道（允许时可用受限简答/指认，不自动恢复流利自述）；无法参与者用观察或旁人信息。

---

---

## 5. 数据契约（字段契约与示例；机器 Schema、语义校验、完整病例待实现）

> 以下为第一例必须落地的字段契约。机器校验用 Pydantic/JSON Schema；规则表达式只做受限数据解释，**不执行任意代码（禁止 eval）**。发布前检查不可达状态、引用缺失、条件冲突、重复触发、量表可评分条件。

### 5.1 病例版本 case

```json
{
  "case_id": "STEMI-001",
  "version": 1,
  "specialty": "心内科/急诊",
  "learning_goals": ["识别典型缺血性胸痛", "正确开立并解读心电图+肌钙蛋白", "识别需紧急再灌注", "完成鉴别诊断（主动脉夹层/肺栓塞/心包炎）"],
  "target_population": "本科临床医学教学，模拟成人患者",
  "review_status": "draft",        // draft | validated | under_review | published
  "reviewer": null,                 // 医学审核者姓名（不得占位伪造）
  "review_date": null,
  "sources": ["2023 ESC ACS 指南要点（仅摘要引用，不打包全文）"],
  "chief_complaint": {"text": "突发胸痛2小时", "auto_volunteer": true},
  "facts": [],       // 见 5.2
  "exams": [],       // 见 5.3
  "rules": [],       // 见 5.4
  "rubric": {}       // 见 5.6
}
```

### 5.2 事实 fact（每条一个原子事实，性质与放射必须拆成两条）

```json
{
  "fact_id": "f_pain_character",
  "content": "胸痛为压榨样",
  "patient_known": true,            // 患者是否知晓（可自述）
  "disclosure_condition": {"field": "胸痛性质", "triggers": ["怎么疼", "性质", "什么样的痛", "压榨"]},
  "auto_volunteer": false,          // true=主动说（如主诉），false=问到才说
  "status_valid": {"from_sim_time": 0, "to_sim_time": null},  // 时间有效区间，null=当前仍有效
  "polarity": "positive"            // positive | negative | unknown
}
```

**披露粒度规则**：问"疼痛性质"不得放行同字段中未问的放射/持续时间；对同义、否定、复合、模糊问法分别测，无法可靠映射时澄清，不默认扩大披露。

### 5.3 检查 exam（区分"病例检查定义"与"会话订单实例"）

> 病例模板中的 exam 是**不可变的项目定义**；运行时每个学生开单生成独立订单实例（order_id），同一次复查保留多个采样/返回版本。学生 A 开单不改变 B 或病例模板。

```json
{
  "exam_id": "ex_ecg",
  "standard_name": "心电图",
  "order_required": true,
  "precondition": null,             // 前置条件（如"需先建立静脉通路"）
  "state": "not_ordered",           // not_configured | not_ordered | pending | ready | failed
  "sample_sim_time": null,          // 采样模拟时刻
  "return_sim_time": null,          // 回报模拟时刻
  "result": null,                   // 显式结果；null 表示未返回
  "unit": null,
  "allowed_values": ["ST段抬高(II/III/aVF)", "ST段压低", "正常", "T波改变"],
  "negative_explicit": "正常"       // 只有该显式阴性值才显示"正常"，其余状态不得显示阴性
}
```

**检查结果状态必须区分**：未配置/未开单/待返回/已返回/失败。**禁止"查不到返回无异常"**——那会制造不存在的阴性证据。

### 5.4 演进规则 rule

```json
{
  "rule_id": "r_stemi_delay",
  "from_state": "initial",
  "trigger": {"action": "延误再灌注", "or_sim_time_ge": 60},  // 动作或时间条件
  "exclusions": ["已启动紧急再灌注路径"],
  "delay_minutes": 0,
  "effect": {"to_state": "deterioration", "new_facts": ["血压骤降", "意识模糊"], "severity": "高危"},
  "repeatable": false,
  "recovery_conditions": ["立即补液升压并启动再灌注"]
}
```

**演进规则必须医学审核通过**；恶化由具体动作+时间+医学规则触发，**不由评分决定生理变化**。规则条件用明确的事件/状态/时间锚点（如"心电图回报且提示 ST 抬高后，再未启动再灌注路径达 X 模拟分钟"），不用绝对 `sim_time>=N` 与文字描述混用；阈值、排除、恢复、临床效果均为**待医学审核的病例配置**，先以非临床测试夹具验证引擎，不把示例数字当已批准医学事实。

### 5.5 会话 session 与事件 event

```json
{
  "session_id": "uuid",
  "owner_id": "业务学生ID",
  "case_version": "STEMI-001@v1",
  "conversation_id": "Nexent conversation id",
  "workflow": "history_taking",      // history_taking | exam_ordering | differential | diagnosis | treatment | submitted
  "clinical_state": "initial",       // 仅生理状态：initial | deteriorating | deteriorated | stabilized | resolved ...（业务阶段见 workflow，二者不混）
  "sim_time_minutes": 0,
  "disclosed_fact_ids": [],
  "orders": [],
  "hint_usage": [],
  "sealed": false,
  "seal_seq": null,                  // 封存时的事件截止序号
  "seal_sim_time": null
}
```

```json
{
  "event_id": "uuid",
  "request_id": "客户端请求ID（幂等键）",
  "seq": 1,                          // 会话内递增序号
  "actor": "student",                // student | patient_agent | exam_tool | scoring | system
  "action": "ask",                   // ask | order_exam | submit_treatment | submit_diagnosis | seal | ...
  "intent": "询问胸痛性质",           // 标准化意图（否定/时态/计划/执行分开）
  "executed": true,                  // false=仅计划/否定/提及
  "result": null,
  "state_before": {...},
  "state_after": {...},
  "rule_id": null,                   // 触发规则
  "fact_ids_revealed": []
}
```

### 5.6 评分量表 rubric（五环节）

```json
{
  "rubric_items": [
    {
      "item_id": "hx_pain_character",
      "stage": "病史采集",
      "condition": "询问胸痛性质",
      "evidence_type": "ask_event",        // 证据类型
      "subject": "student",                 // 主体
      "latest_time": "before_diagnosis",    // 最晚时点
      "weight": 1.0,
      "critical": false
    }
  ]
}
```

**评分证据语义**：每个条目声明可评分条件、证据类型、主体、最晚时点；冻结权重/部分分/关键项/合理替代/不适用项/重复动作/扣分上下限。问过问题 ≠ 正确理解/记录答案；最终病历可评总结与推理，但不能反向创建此前问诊/开单/治疗/早期鉴别事件。

---

## 6. 第一例：疑似 STEMI 的临床内容草稿（可作工程夹具；待补全与医学审核）

> 这是审核要点初稿，**必须经医学审核者批准后才能作为评分金标准**。三条路径都要实现：正确路径、危险/延误路径、有依据的替代路径。

### 6.1 隐藏事实（隐藏诊断，不给学生和病人子 Agent）

- 最终诊断：急性 ST 段抬高型心肌梗死（下壁）
- 危险替代诊断：主动脉夹层、肺栓塞（鉴别诊断需排除）

### 6.2 事实清单（部分示例，实现时补全）

| fact_id | 内容 | 触发（学生问什么才答） | 主动说 |
|---|---|---|---|
| f_cc | 突发胸痛 2 小时 | 主诉 | 是 |
| f_pain_character | 压榨样 | 性质/怎么疼 | 否 |
| f_pain_radiation | 向左肩及左上肢放射 | 放射/窜到哪 | 否 |
| f_pain_onset | 休息时突发，持续不缓解 | 何时开始/诱因 | 否 |
| f_hx_htn | 高血压 10 年 | 既往史/基础病 | 否 |
| f_hx_dm | 糖尿病 5 年 | 既往史 | 否 |
| f_hx_smoke | 吸烟 20 年，1 包/日 | 吸烟史/危险因素 | 否 |
| f_med_asa | 今晨自服阿司匹林 1 片（患者已知，可自述） | 用药/吃过药 | 否 |

### 6.3 检查结果（学生开单后由 exam_tool 返回）

| exam_id | 结果 | 返回时点（sim） |
|---|---|---|
| 心电图 | II/III/aVF 导联 ST 段抬高 | 开单后 5 分钟 |
| 肌钙蛋白 | 升高（结果回报较慢，30 分钟） | 开单后 30 分钟 |

**关键临床约束（已核实）**：疑似 STEMI 不得"等肌钙蛋白回报才行动"——应尽早判读心电图并启动紧急再灌注路径；"给阿司匹林/呼叫 PCI"须带适用条件与禁忌，不是无条件脚本。

### 6.4 演进规则

- 延误再灌注（如心电图已提示 ST 抬高后 60 分钟模拟时间未启动再灌注路径）→ 恶化：血压骤降、意识模糊、室颤风险。
- 无害冗余检查（如开胸部正侧位片）→ 效率扣分，**不触发恶化**。

### 6.5 五环节 rubric 示例（部分）

- 病史采集：问性质 / 放射 / 起病时间 / 既往史 / 危险因素（各 1 分，共 5）
- 检查选择：开心电图（关键）+ 肌钙蛋白；不因等肌钙蛋白延误心电图
- 鉴别诊断：列出主动脉夹层、肺栓塞并说明排除依据（推理分，非关键词罗列）
- 诊断：急性 ST 段抬高型心肌梗死
- 治疗：阿司匹林 + 启动再灌注路径（PCI 或溶栓，注明适用条件）

---

## 7. 接口规范（MCP 工具 + 北向 API 封装）

### 7.1 patient_state_api（FastMCP）

```python
# 入参一律从服务端受保护上下文取绑定，不信任模型传入值；事实候选集由后端依据"本次真实学生问题"生成并冻结，模型只能请求集合内事实，不能用自写问题扩权
get_visible_facts(session_id: str) -> list[dict]
    # 返回：基于本轮问题授权的受限事实候选 + 当前状态投影（不含隐藏诊断/未来分支/完整 clinical_state）

classify_query(session_id: str, query: str) -> {"field": str | None, "confidence": float}
    # 元分类器：判定学生这一问索取哪个事实字段；低置信返回 None → 病人澄清

disclose(session_id: str, fact_id: str) -> {"allowed": bool, "content": str | None}
    # 按披露条件决定是否放行；同一字段内未问的原子事实不连带放行

apply_action(session_id: str, action: dict) -> dict
    # 校验前置条件 → 状态机转移 → 更新 sim_time/orders → 写 event（幂等）→ 返回新状态与可观察变化

check_contradiction(session_id: str, candidate_answer: str) -> {"pass": bool, "reason": str}
    # 键值/白名单/NLI 校验；pass=false 时丢弃候选文本、用获准事实模板回答
```

### 7.2 scoring_api

```python
score_stage(session_id: str, stage: str) -> dict
    # 按冻结 rubric + event 证据逐条打分；返回 {item_id: {hit, evidence_event_id, deduction_reason}}

score_ddx(session_id: str, ddx: list[dict]) -> dict
    # ddx 每项含 {candidate_id, supporting_evidence, opposing_evidence, evidence_event_ids}；
    # Recall@K 仅作候选覆盖的辅助项，鉴别推理分由支持/反对证据与当时事件引用判定，不靠病名命中
```

### 7.3 case_bank_api（仅教师/管理员权限）

```python
list_cases() / get_case(case_id, version) / create_case(case_json) / update_case(case_id, case_json) / publish_case(case_id, version)
    # 草稿→结构/语义校验→预览演练→医学审核→发布；运行中会话锁定旧版本
```

### 7.4 Nexent 北向 API 封装（nexent_client.py）

- 调用 `POST /nb/v1/chat/run`（SSE），Bearer 服务账号密钥（后端持有）。
- **每次请求都传目标 agent_name（官方该版本为必填）**；续聊另传后端绑定的 conversation_id（官方该版本为 integer，与业务 UUID 区分）。以实际采用版本的真实契约为准。
- `final_answer_mode=complete`；**后端过滤过程事件（thinking/工具原文/考官消息）**，只把根 Agent 最终答案经审核后发给学生。
- 病人子 Agent 的提示词：只基于注入的获准事实作答、只答被明确问到的、不主动补充、设定外检查按脚本回应、绝不编造矛盾数值；配 2–4 个 few-shot（问到 A 只答 A、拒答 B）。
- **反馈知识库**：独立于问诊链，提交后由反馈链检索有许可的指南摘要/评分依据，记录引用与版本；完整隐藏病例不挂病人可检索库，不生成空知识库目录充数。

---

## 8. 前端页面（Streamlit）

- **学生端流程**：选择病例 → 查看公开档案（"胸痛患者"等教学情景名，不含诊断）→ 问诊对话（文字）→ 检查开单（结构化按钮，避免"考虑做CT"误当执行）→ 结果卡片（状态区分 pending/ready/失败）→ 病历书写（结构化字段）→ 提交鉴别诊断/诊断/治疗（区分"计划"与"确认执行"）→ 展示五环节评分 + 扣分点 + 轨迹回放时间轴。
- **教师端**：病例新增表单 + 校验 + 审核发布、评分标准查看、成绩统计。
- **合规**：页面顶部显著"教学模拟环境，非真实患者，不用于真实诊疗"。
- **学生端只接收后端过滤后的响应**，不直接连 Nexent SSE。

---

## 9. 测试清单、里程碑与边界

### 9.1 必须通过的测试（对应 T01–T26 精简 + 五组执行语义反例）

1. **身份**：A/B 共享服务 Key 仍互不可读写；模型换 session 无效；病例 A 反馈不回流到 B。
2. **事实披露**：性质/放射分问（只问性质不得连放放射）；已知家测可答；未回报检查不编造；未配置不当正常；失败候选不算已提供。
3. **临床动作**：否定/计划/执行分开；断线重试仅一次（同 ID 同内容返原结果、同 ID 异内容拒绝）；并发有顺序；输出失败不回滚不重执行。
4. **时钟与表现**：并行检查、采样/返回/查看不同时点；预设危急警示可见；丧失表达通道后不继续流利问诊。
5. **评分与复现**：病历补写不造早期行为；替代路径获正确处理；固定初态新会话三次比较（独立复现≠幂等重试）；提示使用分层记录。
6. **出站与发布链**：浏览器收到的响应体只有白名单内容；根答案经审核；知识库角色与阶段正确；冷/热首字与完成时间实测并记录失败/回退。
7. **独立评测（必须带入）**：预定评分口径、各专家仲裁前原始分、系统原始分、获准参考、学生与合成轨迹分表、提示/既往接触记录、留出集、严重错误与失败记录；人工审核及学生实验未发生时如实标待完成。
8. **评分状态与前端一致性**：复合诊断（多诊断条目/多病灶/量表未覆盖组合）被判 0 分时转"待人工复核"而非"评分完成"；读取/诊断重试成功后当轮清除旧错误、立即显示新结果，不等到刷新（前端不得缓存过期错误）。

### 9.2 里程碑（对齐官方节点）

| 阶段 | 交付 |
|---|---|
| 第 1 周 | 第一例（疑似 STEMI）经初审，走通"真实 Nexent 调用 + 受限工具 + 问诊到评分"完整闭环 |
| 第 2–3 周 | 事件/时间/检查结果稳定，客观评分可复算，教师可新增并发布一例 |
| 第 4 周 | 1–2 例完整中期演示 + 一次冷环境导入（对齐报名后 30 天中期节点） |
| 第 5–6 周 | 五专科医学审核、独立留出测试、重复运行、权限攻击测试、文档视频 |

### 9.3 明确不要做（首期）

语音、3D 患者、模型微调、知识图谱、生成任意检查结果、多 LLM 逐轮投票、第二套前端、复杂事件平台（幂等用持久唯一约束+事务+顺序即可）。

### 9.4 交付物清单（官方 11 项）

成果目录(agent.json+README+skills/+mcps/)、源码(CI 通过)、知识库资产、模拟数据、评测集与标注、部署文档、使用文档、演示视频(3–8min)、测试报告、设计说明、LICENSE/CHANGELOG。另附：第三方代码/模型/数据许可表、AI 工具使用披露、一次干净环境部署记录。

---

## 10. 完成定义（第一例）

第一例"完成"不是"能聊"，而是同时满足：事实与检查有来源、角色与身份隔离、关键动作只执行一次、病情变化有规则、每项分能解释、回放与当时输出一致、样本与结果可复算、审核后真实首字/完成时间已测。满足后才扩五专科。
