# AI数字病人-课题1（本地闭环基线）

这是一个用于教学流程验证的本地 STEMI 数字病人模拟器，不是医疗诊断系统，也不是正式 Nexent/MCP 部署。

## 启动
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn backend.app.main:app --reload
streamlit run frontend/app.py
```

## 测试
```powershell
pytest -q
```

## 已落实的工程约束
- 复合/不确定/冲突诊断不会被错误标记为“0 分、评分完成”，而是 `needs_review`。
- 同一 `request_id` + 同一 payload 可安全重放；同一 `request_id` + 不同 payload 返回冲突错误。
- 成功重试返回新结果并清除旧错误；前端以请求成功回调覆盖错误状态。
- 公开 API 不暴露内部事实 ID、所有者 ID 或 request hash。
- SQLite 持久化仅用于本地教学演示。

