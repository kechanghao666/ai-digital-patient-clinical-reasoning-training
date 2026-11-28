# AI 数字病人临床思维训练 — Nexent 导入说明

> 本说明供在独立 Nexent 实例复现使用。`agent.json` 必须由目标平台实际导出，不能手写替代。

## 前置条件

1. 可访问的目标 Nexent 实例及版本号（记录于 `CHECKLIST.md`）。
2. 实例上已配置可用模型：`spark-x2.6-4b`（病人/反馈）、`qwen3.7-text-embedding`（知识库，如使用）。
3. 实例网络可访问本项目自定义 MCP 服务。
4. 已准备环境变量（仅保存在受保护的服务配置，不进入源码）：
   - `NEXENT_BASE_URL`
   - `NEXENT_API_KEY`
   - `NEXENT_PATIENT_AGENT`
   - `NEXENT_FEEDBACK_AGENT`
   - `MCP_SERVICE_TOKEN`

## 步骤 1：部署并注册自定义 MCP

1. 在目标环境安装 Python 3.11 并安装锁定依赖：
   ```bash
   python3.11 -m venv .venv
   .venv/bin/python -m pip install --require-hashes -r requirements.lock
   ```
2. 设置 `MCP_SERVICE_TOKEN`（≥16 字符）。
3. 启动 MCP 服务：
   ```bash
   export MCP_SERVICE_TOKEN="<token>"
   .venv/bin/python -m app.mcp_server
   ```
   默认端口 `8765`，streamable-http 路径 `/mcp`。
4. 在 Nexent 平台“MCP 仓库 → 我的 MCP → 远程 URL”添加 MCP：
   - URL：`http://<mcp-host>:8765/mcp`
   - 传输：`streamable-http`
   - 自定义请求头：`Authorization: Bearer <MCP_SERVICE_TOKEN>`
5. 执行连通性测试，确认平台可扫描工具列表。

## 步骤 2：创建/导入智能体

### 病人智能体

- 内部名称示例：`digital_patient_stemi`（按目标实例命名）。
- 启用工具：仅 `get_turn_context`、`submit_fact_selection`。
- 不挂载教学知识库。
- 必须设置 `allow_chat_metadata=true`。
- 提示词要点：见 [`docs/nexent-integration.md`](../../../../../docs/nexent-integration.md) 第 5 节。

### 反馈智能体（二选一）

#### review_tool 协议
- 启用工具：`get_sealed_review`、`search_teaching_basis`、`submit_feedback`。
- 挂载教学依据知识库（`knowledge/teaching_basis/`）。
- 设置 `allow_chat_metadata=true`。
- 后端环境：`NEXENT_FEEDBACK_PROTOCOL=review_tool`（默认）。

#### compact 协议
- 启用工具：仅 `submit_feedback`。
- 挂载教学依据知识库。
- 设置 `allow_chat_metadata=true`。
- 后端环境：`NEXENT_FEEDBACK_PROTOCOL=compact`。
- 提示词要点：模型只提交 stage/comment/improvement，由服务端绑定依据与证据。

## 步骤 3：上传知识库

1. 将 `knowledge/teaching_basis/` 下的 9 份教学草稿上传为 Nexent 知识库。
2. 只向反馈智能体挂载该库；病人智能体不得挂载。
3. 记录实际知识库名称/ID，填入 `CHECKLIST.md`。

## 步骤 4：导出并脱敏

1. 在平台导出病人与反馈智能体，得到 `agent.json`。
2. 检查导出内容，移除或替换以下敏感字段后再用于公开导入：
   - API Key、MCP 令牌、模型密钥
   - 带访问凭证的 URL
   - 学生记录、个人字段
   - 本机专属资源 ID（目标实例导入时会重新解析）
3. 将脱敏后的 `agent.json` 放入本目录，覆盖 `README.md` 中的缺失说明。

## 步骤 5：在干净实例导入并验证

1. 在新实例注册同一 MCP（步骤 1）。
2. 导入脱敏后的 `agent.json`。
3. 逐项核对映射：模型 ID、工具列表、知识库挂载、`allow_chat_metadata`。
4. 重新发布两个智能体。
5. 配置后端环境变量指向新实例智能体名称，启动 API。
6. 运行五病例完整闭环（见 `CHECKLIST.md`）。

## 参考文档

- [`docs/nexent-integration.md`](../../../../../docs/nexent-integration.md)
- [`docs/agent-config-draft.md`](../../../../../docs/agent-config-draft.md)
- [`docs/deployment.md`](../../../../../docs/deployment.md)
