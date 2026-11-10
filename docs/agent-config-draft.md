# Nexent配置字段清单（启动草案）

2026-11-11更新。本文件记录当前真实隔离实例的配置，仍不是最终可导入agent.json；本机资源ID不能复制为另一实例的资源ID。

| 项目 | 已核实配置与约束 |
|---|---|
| 平台 | 实际镜像v2.8.4；界面About版本与镜像标签分别记录 |
| 病人智能体 | 内部名称digital_patient_stemi，已发布v2；只启用get_turn_context、submit_fact_selection；不挂载教学知识库 |
| 反馈智能体 | 内部名称clinical_feedback_stemi，已发布v3；启用get_sealed_review、search_teaching_basis、submit_feedback及knowledge_base_search；仅封存后调用 |
| 模型 | qwen-plus；嵌入qwen3.7-text-embedding，1024维。本轮真实问诊后调用返回403 / AccessDenied.Unpurchased，持续可用性未通过 |
| 运行metadata | 两智能体必须allow_chat_metadata=true；否则当前北向请求会返回400 / 010105。回合凭证可进入模型上下文，授权由后端校验，不以提示词保密代替鉴权 |
| 提示词 | 通过目标版本实际支持的prompt字段配置；病人仅选择候选事实；反馈五环节使用中文名称，结合真实事件、依据和非空改进建议，禁止改分或泄露答案 |
| MCP | streamable-http；宿主机服务通过host.docker.internal供容器访问；Authorization携带服务Bearer令牌。凭据仅保存在受保护配置 |
| 反馈知识库 | 本机已上传9份未审核教学草稿，混合检索返回3条结果；只挂载反馈智能体。实际模型反馈中的检索与最终提交尚未成功 |
| 记忆边界 | 已核实MEMORY_SWITCH=N、MEMORY_AGENT_SHARE=never、DREAMING_SWITCH=N；不可开启跨学生共享记忆 |
| 步数与时限 | 当前病人6步、反馈14步；总预算55秒。隔离联调的读取空闲等待调至55秒，仍受总截止约束；此调整不代表≤5秒/≤60秒性能验收通过 |
| 业务状态 | 身份、病例版本、候选事实、分数及封存状态由后端确定，模型不得传入权威状态。一次成功提交，失败/超时取消凭证，过期提交不能最终接收 |

当前配置只证明部分业务实际联调；平台显示模型available不等于当次调用有权限或能够完成。

复现步骤：配置平台和模型权限→注册鉴权MCP并扫描工具→分别创建两个智能体及其工具白名单→上传并只向反馈智能体挂载草稿知识库→关闭共享记忆→开启运行metadata并发布版本→运行真实业务与失败边界→导出并检查敏感信息→在干净实例重新导入验证。最终导出不得含服务令牌、模型密钥、学生记录或本机专属地址凭据；资源映射及依赖配置须有操作说明。

工具和字段以目标实例OpenAPI及实际导出为准，不手工制造最终平台ID。详见[Nexent集成](nexent-integration.md)、[实测证据](evidence/2026-11-11-real-stemi.json)。

官方来源：[AgentsHub](https://gitcode.com/ModelEngine/AgentsHub)、[操作指导](https://gitcode.com/ModelEngine/AgentsHub/blob/main/操作指导.md)。
