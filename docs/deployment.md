# 从零启动与隔离复现

本指南针对单API进程、本机教学演练。生产身份尚未接入，服务只监听本机；不将development自填账号用于公开部署。真实Nexent联调与本地模拟分别验收。

## 安装

取得完整源码后在仓库根目录使用Python 3.11。Windows PowerShell：

```powershell
py -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe scripts/run_engineering_samples.py
```

Linux/macOS使用`python3.11 -m venv .venv`，后续解释器路径为`.venv/bin/python`。十二组演练输出到`local-only/engineering-samples/时间戳/`，含逐次输入输出、代码/病例/依据哈希和实际判定。也可在脚本后传一个尚不存在的输出目录；不会覆盖已有报告。脚本复用仓库测试夹具，必须保留tests目录；病例、评分和反馈均为未经医学审核的本地工程模拟。

## 本地演练

在仓库根目录打开一个终端设置环境后启动API：

```powershell
$env:APP_ENV = 'development'
$env:NEXENT_MODE = 'simulator'
$env:APP_DATA_DIR = Join-Path (Get-Location) 'local-only\demo-data'
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

另开终端，在仓库根目录启动界面：

```powershell
& .\.venv\Scripts\python.exe -m streamlit run frontend/app.py --server.address 127.0.0.1 --server.port 8501
```

浏览器访问`http://127.0.0.1:8501`，连接设置填`http://127.0.0.1:8000`。学生演示账号须以`demo-`或`student-`开头；教师演示账号以`teacher-`开头。API健康地址为`http://127.0.0.1:8000/health`；`status=ok`只证明服务存活，`patient_adapter=simulator`明确表示未调用真实模型。

## 数据目录与备份

`APP_DATA_DIR`包含`app.db`和`teacher_cases/`，默认集成记录在同目录`integration.db`。不设置时兼容原目录`backend/data`。相对路径始终从仓库根目录解析。显式`INTEGRATION_DB_PATH`优先于默认路径，API与MCP必须指向同一业务目录和同一集成库；知识目录亦须一致。

环境变量在进程启动时生效，修改后须重启对应服务。示例`.env`不会自动加载。更换`APP_DATA_DIR`只是使用另一个目录，不会迁移、清空或覆盖旧训练。保留旧目录及原封存记录；备份运行中的SQLite使用其backup接口，停机备份则同时保留业务库、集成库和教师病例目录。不要直接把旧格式数据库改名覆盖当前库。

测试启动时自动选择临时存储，样本复跑每组也使用独立数据库。真实平台演练仍应显式配置专用`APP_DATA_DIR`，不以测试隔离代替部署隔离。

## 真实Nexent配置

1. 在目标Nexent实例配置可用模型，实际调用验证，不能只凭模型列表显示available。目标实例模型/工具资源ID须重新绑定。
2. 从受保护的服务配置加载`NEXENT_BASE_URL`、`NEXENT_API_KEY`、`NEXENT_PATIENT_AGENT`、`NEXENT_FEEDBACK_AGENT`和`MCP_SERVICE_TOKEN`；不得放入源码或截图。
3. API与MCP使用相同`APP_DATA_DIR`、`INTEGRATION_DB_PATH`及`KNOWLEDGE_DIR`。MCP令牌至少16字符，配置详见[字段清单](agent-config-draft.md)和[Nexent集成](nexent-integration.md)。
4. 在已设置上述环境的终端执行`python -m app.mcp_server`，工作目录为`backend`；默认监听127.0.0.1:8765。平台容器需使用可达地址和Bearer鉴权，按现有部署的端口转发方式验证工具可达。
5. 导入平台导出的病人与反馈配置，重新扫描工具并发布。病人只分配get_turn_context与submit_fact_selection。compact反馈只分配submit_feedback，设置`NEXENT_FEEDBACK_PROTOCOL=compact`；另一review_tool流程依独立说明配置，不能混用。
6. 设置`NEXENT_MODE=nexent`并重启API。执行[中期演示](stemi-demo.md)及五科场景，检查可信工具、事件、重复请求和首个有效回答时间。平台健康探测、HTTP 200或一个成功问题不能证明完整验收。

本轮已验证隔离目录下API与MCP共享数据及十二组模拟闭环，真实五科各一次问诊成功。异机安装、独立Nexent实例导入、随机现场用例和正式身份仍待验证，不能将本机结果写成这些门槛已完成。
