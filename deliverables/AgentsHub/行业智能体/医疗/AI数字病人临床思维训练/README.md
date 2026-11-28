# AI 数字病人临床思维训练

课题 1，医疗健康方向，报名账号 `kechanghao`。

> 本目录是官方 AgentsHub 预交付入口。**`agent.json`（病人）和 `feedback/agent.json`（反馈）尚未由 Nexent 平台最终导出，因此当前不是可公开导入的最终成果。** 两个角色各自从来源实例导出。只有在目标实例完成模型、工具与知识库映射并实测后，才能确认能独立导入。

## 工程状态

- 代码分支：`codex/feedback-protocol-fix`
- 产品源码固定版本：`c7460cd83ce2c09d627a35ee7d324bf8ef0ad32e`
- 本批模拟器演练：五个不同专科各自完成师生浏览器流程，其中每例均通过 13 项检查
- 真实模型/供应商调用：0（本目录准备阶段）
- 医学审核、正式学生/专家评测、官方验收：未完成

完整工程与验收状态见：
- [`docs/官方要求核对.md`](https://github.com/kechanghao666/ai-digital-patient-clinical-reasoning-training/blob/c7460cd83ce2c09d627a35ee7d324bf8ef0ad32e/docs/官方要求核对.md)
- [`docs/第一阶段覆盖表.md`](https://github.com/kechanghao666/ai-digital-patient-clinical-reasoning-training/blob/c7460cd83ce2c09d627a35ee7d324bf8ef0ad32e/docs/第一阶段覆盖表.md)
- [`docs/工程接手.md`](https://github.com/kechanghao666/ai-digital-patient-clinical-reasoning-training/blob/c7460cd83ce2c09d627a35ee7d324bf8ef0ad32e/docs/工程接手.md)
- [`docs/evidence/2026-11-29-boundary-review/verification.json`](https://github.com/kechanghao666/ai-digital-patient-clinical-reasoning-training/blob/c7460cd83ce2c09d627a35ee7d324bf8ef0ad32e/docs/evidence/2026-11-29-boundary-review/verification.json)

## 交付内容

| 文件/目录 | 说明 | 状态 |
|---|---|---|
| `README.md` | 本文件：实现概述、安装与导入入口 | 已提供 |
| `IMPORT.md` | 逐步导入说明 | 已提供 |
| `CHECKLIST.md` | 独立实例复验清单，含缺失项 | 已提供 |
| `mcps/mcp-config.example.json` | 自定义 MCP 注册示例（无凭据） | 已提供 |
| `agent.json`（病人）和 `feedback/agent.json`（反馈） | **平台导出的病人/反馈智能体配置** | **缺失，待平台导出并复验** |
| `skills/` | 仅当实际使用平台 Skill 时提供；本项目当前使用自定义 MCP，未使用 Skill | 不适用 |
| `mcps/` | 注册示例在本目录；实现随固定版本产品源码提供 | 已提供 |

## 实现与依赖

- 学生 React 工作台：问诊、选检查、看回报、写病历、提交鉴别/诊断/执行处置、查看封存评分。
- 教师 React 工作台：班级、病例、任务、完成情况、评分/反馈复核。
- 后端：FastAPI（`backend/app/main.py`）。
- 编排：Nexent 病人/反馈智能体（`backend/app/integration/nexent_client.py`）。
- 可信工具：自定义 FastMCP 服务（`backend/app/mcp_server.py`），负责回合读取、事实选择、反馈提交。
- 知识资产：按病例版本管理（`knowledge/teaching_basis/`），只挂载到反馈智能体。
- 规则模拟器：离线工程演示用（`NEXENT_MODE=simulator`），不能替代真实平台验收。

## 安装

从单独的 AgentsHub 目录开始时，先取得对应的产品源码。以下命令从产品仓库根目录运行。

```powershell
git clone https://github.com/kechanghao666/ai-digital-patient-clinical-reasoning-training.git patient-training
cd patient-training
git checkout --detach c7460cd83ce2c09d627a35ee7d324bf8ef0ad32e
```

Python 3.11，按锁文件安装依赖：

```powershell
py -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
& .\.venv\Scripts\python.exe -m pytest -q
```

前端构建（CI 使用 Node.js 24）：

```powershell
Push-Location web
npm ci --no-audit --no-fund
npm run typecheck
npm run build
Pop-Location
```

## 运行自定义 MCP

从产品根目录运行。将至少 16 字符的 `MCP_SERVICE_TOKEN` 注入受保护的运行环境，API 和 MCP 指向同一演示数据目录与可信回合库。

```powershell
& .\.venv\Scripts\python.exe -m backend.app.mcp_server
```

默认监听 `127.0.0.1:8765`，路径 `/mcp`（streamable-http）。跨容器时设置 `MCP_HOST` 为受控网络可达的绑定地址并在 MCP 注册时携带 Bearer 令牌。

## 导入 Nexent 智能体

见 [`IMPORT.md`](./IMPORT.md)。核心要求：

1. 在目标 Nexent 实例注册鉴权 MCP 并扫描工具。
2. 创建/导入病人智能体，仅启用 `get_turn_context`、`submit_fact_selection`，不挂载知识库。
3. 创建/导入反馈智能体，按协议启用 `get_sealed_review`、`search_teaching_basis`、`submit_feedback`（`review_tool`）或仅 `submit_feedback`（`compact`），挂载教学依据知识库。
4. 由平台实际导出 `agent.json`（病人）和 `feedback/agent.json`（反馈），不是手写；脱敏后在干净实例重新绑定并验证。
5. 设置环境变量后重启 API，运行五病例完整闭环验证。

## 验证清单

见 [`CHECKLIST.md`](./CHECKLIST.md)。文件检查覆盖范围及报告见清单；需要平台授权或评审现场抽取的项明确标注为“待完成”。

## 注意事项

- 不得将 API Key、MCP 令牌、模型密钥、学生记录、带凭据的 URL 或个人字段提交到仓库。
- 病例、量表与教学依据均为合成草稿，未经医学审核，不用于真实诊疗。
- 评分与反馈仅供教学参考，以教师/医学专家意见为准。
