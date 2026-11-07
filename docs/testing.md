# 测试说明

## 自动化检查

在仓库根目录、已安装锁定依赖的环境执行：

```powershell
python -m pytest -q
python -m compileall -q backend/app frontend tests
```

测试覆盖病例校验、披露、订单/时钟、治疗执行、幂等、状态冲突、封存评分和API公开投影等。API测试使用临时数据库替换业务存储；运行数据不提交。

CI使用Python 3.11、按哈希安装requirements.lock，并运行上述检查和独立依赖审计。结果以对应提交的[GitHub Actions](https://github.com/kechanghao666/ai-digital-patient-topic1/actions)为准，不沿用其他提交的通过结论。

## 最近本地结果

2026-11-07文档整理时：Python 3.14环境69项测试通过，编译通过；存在51条依赖弃用警告。业务源码和病例本次未修改。该结果不是干净Python 3.11部署或真实浏览器验收；远端CI另行检查。

## 依赖与安全检查

直接依赖维护于backend/requirements.txt。更新后重新锁定并验证：

```powershell
uv pip compile --python-version 3.11 --universal --generate-hashes backend/requirements.txt -o requirements.lock
python -m pip_audit --strict --require-hashes -r requirements.lock
```

审计工具在CI单独安装，不属于应用运行依赖。无已知漏洞不等于不存在安全风险；不关闭检查或忽略漏洞来取得通过结果。

## 尚未完成的验证

- 真实Nexent及MCP、模型错误与流式输出验证。
- 正式浏览器操作、正式身份和教师管理。
- 五专科医学审核、问答一致性及错误分支覆盖。
- 学生/教师评分对照、真实响应时延及三次重复性。
- 最终配置导出/重新导入、干净部署和现场用例。

以上按[验收计划](acceptance-plan.md)采集独立证据，不能用本地测试数量替代。
