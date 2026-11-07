# 测试说明

## 自动化检查

在仓库根目录、已安装锁定依赖的环境执行：

```powershell
python -m pytest -q
python -m compileall -q backend/app frontend tests
```

测试覆盖病例校验、披露、订单/时钟、治疗执行、幂等、状态冲突、封存评分和API公开投影等。API测试使用临时数据库替换业务存储；运行数据不提交。

CI使用Python 3.11、按哈希安装requirements.lock，并运行上述检查和独立依赖审计。结果以对应提交的[GitHub Actions](https://github.com/kechanghao666/ai-digital-patient-clinical-reasoning-training/actions)为准，不沿用其他提交的通过结论。

## 最近本地结果

2026-11-08，Python 3.11.18下完整回归 **218项通过**。覆盖核心规则、脚本化Nexent契约、后端→假Nexent HTTP/SSE→HTTP MCP套接字链路、五专科回放、教师生命周期、学生与教师AppTest。本轮新增鉴别混合证据、教师病例热加载、草稿版本固定、失败重试、五环节反馈、历史记录安全展示和账号切换隔离回归。

另在隔离副本中启动真实FastAPI与Streamlit服务，通过本地Chromium完成：普外病例问诊→开单→等待→诊断→确认治疗→封存→五环节反馈；教师复制模板→编辑→保存→结构校验→送审（未发布、未虚构审核）；桌面和390像素手机布局检查，无整页横向溢出，浏览器控制台无错误。自动化页面测试另验证封存只读、已存反馈恢复及身份切换清理。运行副本使用独立数据库，原`app.db`的SHA256与修改时间核对未变。

以上是本地工程与浏览器验证，**不是真实Nexent实例、真实模型、医学审核、正式人员评测或官方验收结果**。远端CI以当前提交的Actions为准，不沿用历史提交的通过结论。修复范围见[开发计划](milestones.md#优先修复)。

## 依赖与安全检查

直接依赖维护于backend/requirements.txt。更新后重新锁定并验证：

```powershell
uv pip compile --python-version 3.11 --universal --generate-hashes backend/requirements.txt -o requirements.lock
python -m pip_audit --strict --require-hashes -r requirements.lock
```

审计工具在CI单独安装，不属于应用运行依赖。无已知漏洞不等于不存在安全风险；不关闭检查或忽略漏洞来取得通过结果。

如本地虚拟环境由uv创建且未安装pip，可用 `uv pip check --python .venv/Scripts/python.exe` 检查依赖兼容性；无需为此重建运行环境。

## 尚未完成的验证

- 真实Nexent实例上的调用、MCP连通、知识库挂载和模型错误/流式输出验证（无实例）。
- 正式部署环境的浏览器与人员验收、正式身份认证（教师目前仅开发模式身份）。
- 五专科医学审核、问答一致性；错误分支目前仅有每例一条延误规则的工程脚本验证。
- 学生/教师评分对照、真实响应时延及三次重复性。
- 最终配置导出/重新导入、干净部署和现场用例。

以上按[验收计划](acceptance-plan.md)采集独立证据，不能用本地测试数量替代。
