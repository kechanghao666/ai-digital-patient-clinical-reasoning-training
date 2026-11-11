# Nexent配置字段清单（启动草案）

2026-11-11更新。本文件记录当前真实隔离实例的配置，仍不是最终可导入agent.json；本机资源ID不能复制为另一实例的资源ID。

| 项目 | 已核实配置与约束 |
|---|---|
| 平台 | 实际镜像v2.8.4；界面About版本与镜像标签分别记录 |
| 病人智能体 | 内部名称digital_patient_stemi，已发布v9；只启用get_turn_context、submit_fact_selection；不挂载教学知识库 |
| 反馈智能体 | 内部名称clinical_feedback_stemi，已发布v15；启用get_sealed_review、search_teaching_basis、submit_feedback及knowledge_base_search；仅封存后调用 |
| 模型 | 病人与反馈均使用 spark-x2.6-4b；嵌入 qwen3.7-text-embedding（1024维）。四个星火候选已配置本地上下文16384、输入14336、输出及预留2048，运行日志已验证实际输出预算2048 |
| 运行metadata | 两智能体必须allow_chat_metadata=true；否则当前北向请求会返回400 / 010105。回合凭证可进入模型上下文，授权由后端校验，不以提示词保密代替鉴权 |
| 提示词 | 通过目标版本实际支持的prompt字段配置；病人仅选择候选事实；反馈五环节使用中文名称，结合真实事件、依据和非空改进建议，禁止改分或泄露答案 |
| MCP | streamable-http；宿主机服务通过host.docker.internal供容器访问；Authorization携带服务Bearer令牌。凭据仅保存在受保护配置 |
| 反馈知识库 | 本机已上传9份未审核教学草稿，混合检索返回3条结果；只挂载反馈智能体。v15真实反馈调用了平台混合检索，随后成功提交五环节反馈 |
| 记忆边界 | 已核实MEMORY_SWITCH=N、MEMORY_AGENT_SHARE=never、DREAMING_SWITCH=N；不可开启跨学生共享记忆 |
| 步数与时限 | 当前病人6步、反馈14步；总预算55秒。隔离联调的读取空闲等待调至55秒，仍受总截止约束；此调整不代表≤5秒/≤60秒性能验收通过 |
| 业务状态 | 身份、病例版本、候选事实、分数及封存状态由后端确定，模型不得传入权威状态。一次成功提交，失败/超时取消凭证，过期提交不能最终接收 |

当前配置只证明部分业务实际联调；平台显示模型available不等于当次调用有权限或能够完成。

复现步骤：配置平台和模型权限→注册鉴权MCP并扫描工具→分别创建两个智能体及其工具白名单→上传并只向反馈智能体挂载草稿知识库→关闭共享记忆→开启运行metadata并发布版本→运行真实业务与失败边界→导出并检查敏感信息→在干净实例重新导入验证。最终导出不得含服务令牌、模型密钥、学生记录或本机专属地址凭据；资源映射及依赖配置须有操作说明。

11月11日平台导出预检已取得两个原始草稿。导入文件对应导出响应的 `data` 对象，含 `agent_id`、`agent_info` 和 `mcp_info`。两个草稿均保留 `allow_chat_metadata=true`，分别包含2项和4项工具。MCP条目只有名称和地址，目标环境需另配鉴权，并映射模型与反馈知识库。原始草稿保存在受保护的本机目录，已检查未包含本次已知北向密钥与MCP令牌。新实例导入及导入后的真实联调待执行。

工具和字段以目标实例OpenAPI及实际导出为准，不手工制造最终平台ID。详见[Nexent集成](nexent-integration.md)、[实测证据](evidence/2026-11-11-real-stemi.json)。

官方来源：[AgentsHub](https://gitcode.com/ModelEngine/AgentsHub)、[操作指导](https://gitcode.com/ModelEngine/AgentsHub/blob/main/操作指导.md)。

当前导出预检版本为病人v9与反馈v15，两者的模型、MCP及知识库挂载按角色复核。部署反馈工具后，教学依据检索项提供 stages 字段。stage=None 时额外返回按环节分组的 stage_basis，每环节最多一条简短候选。反馈须引用适用环节的依据。

反馈v15从 get_sealed_review 一次读取评分、事件与 stage_basis。Nexent 运行时把 MCP 返回交给动作代码时使用 JSON 字符串，动作直接打印结果供模型阅读。v14按字典取值触发类型错误，v15修正后真实反馈51.53秒成功。
