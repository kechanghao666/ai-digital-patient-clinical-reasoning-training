# AI 数字病人临床思维训练

本项目基于 Nexent 提供模拟问诊、检查开单、病历、评分与教师复核。病例均为教学模拟数据，不用于真实诊疗。

按[部署指南](docs/deployment.md)安装后端，按[账号说明](docs/local-accounts.md)创建教师账号。启动 API 后打开其地址（例如 `http://127.0.0.1:8000/`），主界面由 [React 前端](web/README.md)提供。`frontend/` 是兼容的 Streamlit 前端。默认规则模拟和真实 Nexent 的区别见[集成说明](docs/nexent-integration.md)。

开发从[工程交接方案](docs/2026-11-22-工程Agent交接.md)开始。交接方案描述核查当时的状态，本次同步已将 React 源码纳入工程。正式验收仍需独立部署、真实学生与教师评测及上游合入记录。

## 文档

| 用途 | 文档 |
|---|---|
| 学生/教师日常操作 | [使用手册](docs/user-guide.md) |
| 新版前端设计与构建 | [设计规范](docs/web-redesign-spec.md)、[web/README](web/README.md) |
| 评测集、测试报告与演示脚本 | [问诊评测](evaluation/README.md)、[测试报告](docs/test-report.md)、[演示视频脚本](docs/demo-video-script.md) |
| 同类软件设计研究 | [设计研究与取舍](docs/software-design-study.md) |
| 查看当前完成项、不足与下一步 | [现状复盘](docs/status-review.md)、[11-13全面复盘](docs/project-review-2026-11-13.md) |
| 理解架构与约束 | [技术方案](docs/technical-plan.md) |
| 继续开发、处理已知问题及官方节点 | [开发计划](docs/milestones.md) |
| 核对指标与最终交付物 | [验收计划](docs/acceptance-plan.md) |
| 核对五专科决策、时轴与待医学审核事项 | [决策审查表](docs/clinical-decision-review.md) |
| 中期演示与医学审核 | [演示与独立复现](docs/stemi-demo.md)、[审核与验收记录](docs/clinical-review-and-evaluation.md) |
| 配置平台、MCP及反馈链 | [Nexent集成](docs/nexent-integration.md)、[配置字段草案](docs/agent-config-draft.md) |
| 运行验证及维护依赖 | [测试说明](docs/testing.md) |
| 核对来源、许可与AI使用 | [第三方资源与AI工具披露](docs/third-party-and-ai-disclosure.md) |
| 查看版本变化 | [更新记录](CHANGELOG.md) |

源码采用 [MIT License](LICENSE)，第三方资源依各自许可。
