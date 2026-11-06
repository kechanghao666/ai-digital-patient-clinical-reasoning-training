# AI数字病人-课题1（本地闭环基线）

这是一个用于教学流程验证的本地 STEMI 数字病人模拟器，不是医疗诊断系统，也不是正式 Nexent/MCP 部署。

## 启动
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:AI_PATIENT_DB = "data/ai_patient.sqlite3"
uvicorn backend.app.main:app --reload
streamlit run frontend/app.py
```

SQLite 数据默认保存在 `data/ai_patient.sqlite3`，可用 `AI_PATIENT_DB` 指定其他路径。生产部署前应替换为正式数据库、认证和审计方案。

## 测试
```powershell
pytest -q
```

## 已落实的工程约束
- 复合、否定、不确定、空内容和量表未覆盖诊断统一进入 `needs_review`；只有完整明确的 STEMI 匹配才评分完成。
- 同一 `request_id` + 同一 payload 可安全重放；同一 `request_id` + 不同 payload 返回冲突错误，且由 SQLite 唯一约束和事务保护。
- 前端失败重试复用同一 `request_id`，避免网络超时后重复执行动作。
- 成功重试返回新结果并清除旧错误；前端以请求成功回调覆盖错误状态。
- 公开 API 不暴露内部事实 ID、所有者 ID、request hash 或 replay 数据。
- 所有者隔离、封存后拒绝、重启恢复均有回归测试。
