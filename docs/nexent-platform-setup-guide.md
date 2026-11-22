# Nexent 平台接入

1. 按 [Nexent 官方部署教程](https://modelengine-group.github.io/nexent/zh/quick-start/installation.html)安装平台。记录使用的版本；从自己有权使用的供应商开通模型，在平台内配置并测试连通性。
2. 参照[平台集成](nexent-integration.md)设置应用环境变量，API 与 MCP 使用同一训练数据目录。凭据仅在运行环境配置，不写入本仓库。
3. 按[配置清单](agent-config-draft.md)分别配置病人和反馈角色，注册所需工具、绑定教学依据并发布。不要把病例标准答案挂到学生可见的检索路径。
4. 设置真实 Nexent 模式后，使用自造病例完成问诊、检查回报、评分与教师复核。分别保存工具调用和依据命中证据；能通过模型连通测试不代表业务闭环成功。
5. 正式交付时从平台导出智能体，检查导出内容，在独立实例导入并复测，之后再按[验收计划](acceptance-plan.md)准备上游材料。
