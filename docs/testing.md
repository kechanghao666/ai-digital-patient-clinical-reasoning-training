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

当前189项包括原有69项、Nexent契约测试46项（脚本化桩）、套接字演练3项（后端→假Nexent HTTP/SSE→HTTP MCP，含Bearer），以及病例配置、教师流程、五专科脚本回放和前端渲染冒烟。2026-11-08目录整理后，Python 3.11.18下189项回归通过。此前f41eb1f提交的远端测试与依赖审计均通过，链接见[对应Actions](https://github.com/kechanghao666/ai-digital-patient-clinical-reasoning-training/actions/runs/42392359582)；这些结果不替代后续提交的检查。

以上均为本地模拟或契约验证，**不是真实Nexent实例、真实模型、浏览器验收或人工评测结果**。额外探测发现的评分、教师病例、草稿版本及契约缺口见[优先修复](milestones.md#优先修复)，不能由现有测试通过推断已关闭。

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
- 正式浏览器操作、正式身份认证（教师接口目前仅开发模式身份）。
- 五专科医学审核、问答一致性；错误分支目前仅有每例一条延误规则的工程脚本验证。
- 学生/教师评分对照、真实响应时延及三次重复性。
- 最终配置导出/重新导入、干净部署和现场用例。

以上按[验收计划](acceptance-plan.md)采集独立证据，不能用本地测试数量替代。
