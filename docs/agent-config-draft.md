# Nexent配置字段清单（启动草案）

本文件供启动阶段设计使用，不是可导入的agent.json。最终格式和资源ID必须由目标版本Nexent真实导出确认，不手工制造平台ID。

| 配置目的 | 官方现有样例中的候选字段 | 本项目计划 |
|---|---|---|
| 名称及显示 | name / display_name | 显示名：AI数字病人临床思维训练；唯一内部名称待创建 |
| 职责 | description / duty_prompt | 教学模拟、受限问诊、提交后反馈 |
| 约束 | constraint_prompt | 只回答获准事实，禁止答案和内部状态泄露 |
| 示例 | few_shots_prompt等 | 自造问题与安全边界，待整理 |
| 模型 | model_id / model_name等 | 实例、模型、资源ID待确认 |
| 步数 | max_steps | 以实际闭环及超时验证确定 |
| 工具 | tools | 病例/状态/评分按权限授权，真实注册待完成 |
| 子智能体 | managed_agents等 | 问诊与封存后反馈隔离，待联调 |
| MCP | mcp_info等 | 可信服务已实现：病人侧 get_turn_context / submit_fact_selection；反馈侧 get_sealed_review / search_teaching_basis / submit_feedback；URL、传输与授权头待实例验证，不写密钥。提示词要点见 nexent-integration.md |
| 知识挂载 | 目标版本实际导出为准 | 教学依据与隐藏完整病例分离 |

最终顺序：配置依赖→创建调试→真实验证→平台导出→检查敏感信息与完整性→干净实例重新导入→保存最终agent.json到官方课题目录。

参考：[官方仓库](https://atomgit.com/ModelEngine/AgentsHub)、[操作指导](https://atomgit.com/ModelEngine/AgentsHub/blob/main/操作指导.md)。
