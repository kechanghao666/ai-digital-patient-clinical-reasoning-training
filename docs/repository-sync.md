# E盘与GitHub开发基线同步

日期2026-11-07，原GitHub main为 `af5695d`。保留已有提交历史，以E盘完整首例为后续基线，不强制推送或抹除历史。

原GitHub只有9个跟踪文件，是问答/等待/诊断三类动作的独立简化实现，缺少E盘订单、时钟、演进、治疗确认、分项评分等模块。两者不能逐文件直接混用。本次对E盘第十四轮27项文件重新校验，全部哈希一致；隔离测试原GitHub 7项通过，E盘69项通过。测试数量不是官方指标或完成百分比。

| 范围 | 旧GitHub | 完整基线 |
|---|---|---|
| 后端启动 | uvicorn backend.app.main:app | uvicorn app.main:app --app-dir backend |
| API | /sessions及/actions | /api/sessions及问诊/动作/诊断/鉴别/治疗/封存等 |
| 身份头 | X-Owner | X-Owner-Id，自填身份只在development允许 |
| 数据库 | data/ai_patient.sqlite3，AI_PATIENT_DB可配置 | backend/data/app.db，当前固定路径 |
| 库格式 | 多表 | 会话快照及版本控制；格式不同 |
| 评分 | 单诊断匹配100分 | 五环节证据评分与needs_review |
| 依赖 | 根requirements独立版本 | 根入口引用backend/requirements.txt |

同步包含前端、测试、病例及启动文档。旧调用者需要重新接入；不要将旧格式数据库覆盖到新库。原数据不迁移或删除，未来确需迁移时另行设计和验证。

运行数据库、密钥、缓存、历史审计快照及无关报名者信息排除出Git。后续开发先检查工作区与远端差异，保护未提交改动再合并。当前GitHub过程仓库不自动成为官方AtomGit Fork。
