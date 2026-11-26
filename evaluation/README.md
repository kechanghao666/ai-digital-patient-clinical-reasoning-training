# 评测集与评测报告

本目录存放课题1“问答一致性”指标的工程评测资产。全部病例为自造模拟数据，标注由开发者完成，**未经医学专家复核**；结果不能替代官方要求的“20轮抽检≥95%”正式判定，也不代表真实大模型的表现。

| 文件 | 内容 |
|---|---|
| `dialogue_eval_set.json` | 主评测集：五科各 20 条，共 100 条（含 10 条泄露探针）；每条标注必须回答的事实（expected）和回答了也不算多答的事实（optional） |
| `dialogue_holdout_set.json` | 留出集：五科各 8 条，共 40 条（含 5 条探针）。在根据主评测集修改问诊层之前写定，修改期间不运行 |
| `reports/` | 每次运行的 `report.md`（汇总和未通过条目）与 `results.json`（逐条输入、输出和判定） |

## 判定规则

一条问句通过，需要同时满足以下条件：必须事实全部出现；没有出现 expected 和 optional 以外的事实；回答中不含诊断词；没有披露尚未发生的危急变化。泄露探针（索要诊断或检查结果）只能得到固定的非事实回答。每条问句新建一次训练，重复 3 次，比较回答与事实是否完全一致。

## 运行

```powershell
.\.venv\Scripts\python scripts\run_dialogue_eval.py
.\.venv\Scripts\python scripts\run_dialogue_eval.py --set evaluation\dialogue_holdout_set.json
```

脚本只使用临时目录，不读写项目数据库。

## 2026-11-13 结果

| 评测集 | 修改前 | 修改后 | 说明 |
|---|---|---|---|
| 主评测集（100条） | 通过 77%，召回 84%，精确 91% | 通过 100%，召回 100%，精确 100% | 修改时参考了这组的失败条目，属于调参集，存在过拟合 |
| 留出集（40条） | 通过 55%，召回 71%，精确 75% | 通过 70%，召回 86%，精确 82% | 修改期间未运行。修改后发现一个与留出集无关的“有过敏”误判，修复后又运行一次，数字未变 |

两组都没有发生泄露，三次重复的一致率都是 100%，进程内接口耗时为毫秒级。

留出集上 70% 的通过率，更接近对未见问法的真实能力估计。尚未通过的问题主要有四类：

- **同一句里问了多个方面时多答。** 例如“咳嗽的时候肚子疼得更厉害吗”，连同咳嗽的系统回顾一起答了。
- **治疗类问题被当成病史。** 例如“需要住院吗”“需要做手术吗”，触发了既往史或手术史的回答。
- **同义说法还没覆盖。** 例如“出虚汗”“爬楼梯不舒服”“疼的地方变没变”。
- **主语是家人的时间问题。** 例如“家里人什么时候发现的”，带出了家族史。

按留出集的规则，不能再拿这组问句调参；下一轮应另写一组新的留出集，再统计改进效果。

## 2026-11-25：O3 问答路由修复工程复验

本轮修复了两类学生问诊 API 可复现误路由：当前手术处置问题不再触发既往手术史；亲属发现症状的问题只在病例叙事有依据时返回“就诊经过”，而不泄露家族史。明确问家族史时只返回该字段，避免按共享词语带出暴露史。回归覆盖相同关键词的不同意图正反例。

- 产品提交：`fd140738023b9a2daac8dac2cd1255bc80e02693`；代码从 `30028a042f7599f4fb94a4ea1cb28288baba362c` 起修改。
- 复现探针 SHA-256：`c5383a0bf983b96c785a0cb47348510d4d11f74f90f4f5b72f8ccd4386f845c4`。修前学生 API 4 轮中 2 轮正确、2 轮越权披露；修后 4/4 正确，完整前后报告见 `reports/o3_reproduction_pre_20261125.json`、`reports/o3_reproduction_postcommit_20261125.json`。
- 五病例 20 轮开发者预登记抽检 SHA-256：`827f61361d892e474fd703ad6e7bda09813a1355ad24512a5516942f66719ada`。输入、五例版本/来源哈希、轮次意图、必答/允许事实、禁止披露项、可接受非事实回答和共享会话策略在 `o3_engineering_spotcheck_20261125.json` 冻结；本轮没有改写或调参这组输入。修前 9/20，11 轮含越权事实；提交后 20/20。修前与修后逐轮报告分别为 `reports/o3_spotcheck_pre_20261125.json`、`reports/o3_spotcheck_postcommit_20261125.json`，报告包含每轮原问题、准确回答、事实 ID、事件回执匹配、判定标记。
- 判定按 API 实际保存事件中的事实 ID 与预登记集合比较，并核对回答由获授权事实精确渲染；另记录漏答、多答、越权披露、不当拒答和接口失败。类别可重叠，每轮仍保留在分母。历史 40 条留出集没有用于调参或本轮判定。
- **判定粒度限制：** 20/20 是事实 ID 级自动规则结果，不代表句内原子事实均与问句意图一致。五例的 R02 都返回整个复合事实，含过敏、既往健康、抗凝/出血风险或外伤信息；此外 STROKE-001-R03 的 `f_care` 还披露“路上没吃过任何药”，超出该轮问发现经过的直接意图。冻结规则没有预登记句内拆分与判定，故这 6 轮潜在语义多答不计入自动 `over_answer` 数；需另行预登记原子事实评测，不能据此称语义问答已达标。
- 评测由同一开发者编写与预登记，是本机规则模拟器/服务端工程抽检，不是独立盲测。所用病例版本均为 draft，临时开发环境显式开放草稿；本机 Windows 11 10.0.26300、Python 3.14.5、本地认证、隔离合成数据库。真实模型/供应商调用 0 次，测试临时数据已清理。20/20 不能替代真实模型、临床专家、学生或官方“20 轮≥95%”验收，O3 仍为“已实现但未达标”。

复跑命令：先在仓库外创建新的临时输出目录，再将该目录传给 `--report`；归档时使用新文件名，避免覆盖已有报告。下面的 `<临时目录>` 是需要替换的路径：

```powershell
py -3 scripts/run_o3_spotcheck.py --dataset evaluation\o3_reproduction_probes_20261125.json --report <临时目录>\reproduction.json
py -3 scripts/run_o3_spotcheck.py --dataset evaluation\o3_engineering_spotcheck_20261125.json --report <临时目录>\spotcheck.json
```

## 2026-11-25：O3 复合事实原子问答

产品代码在 `d4f6669` 起实现原子级回答与评分，在 `b61fdb9` 修复原子候选投影、续问上下文和父事实计分回归。固定的开发者预登记集 `o3_atomic_semantic_spotcheck_20261125.json` 在改产品代码前冻结，SHA-256 为 `425a0303bcfc9b7d9aaf341bb6ad0fb330aad0d033e8463b18878420fff59e01`；覆盖五病例各 4 轮、每病例一个共享会话，病例版本和来源哈希、必答/禁答内容及可接受澄清均写在题集内。历史 40 条留出集没有用于调参。

- 修前 API 复现：`reports/o3_atomic_reproduction_pre_51901bd.json`。例如 APPY 手术史被回答为“既往体健，没有做过手术，没有药物过敏。”，过敏问题同样带出其他病史；STROKE 询问家人发现时间时回答家族史，没给出病例未记录的具体时间。
- 初版 `d4f6669` 的四探针后报告按旧父事实 ID 口径误计为 0/4；这是分类口径不兼容（修复已按原子片段记录），不是产品语义 0/4。保留原报告 `reports/o3_atomic_reproduction_post_d4f6669.json`，不得覆盖或引用为语义结论。
- `b61fdb9` 的实际学生 API 20 轮：正确 20，漏答 0，多答 0，越权披露 0，不当拒答 0，接口失败 0。完整逐轮问答、返回/持久答案、原子 ID、事件与重放匹配见 `reports/o3_atomic_semantic_spotcheck_post_b61fdb9.json`；完整旧 20 轮人工工程复核见 `reports/o3_atomic_old20_semantic_review_20261125.md`。新 20 轮不是把旧 20 轮重新包装。
- 同一开发者撰写和预登记，Windows 11 build `10.0.26300`、Python 3.11.18、本地认证、模拟器和临时隔离数据库；病例均为草稿，仅在隔离开发运行开放可见性。临时数据已清理，真实供应商调用 0。非独立盲测、临床审核、学生评测或官方验收。O3 仍未达标。
- `d4f6669` 初版全量回归的历史结果为 744 passed / 18 failed；原始控制台输出和 18 个失败节点列表未归档。共同根因和证据限制见 `reports/o3_atomic_full_regression_iteration_d4f6669.md`。后续 `b61fdb9` 专项 40 passed、全量 762 passed，未改超时断言。
- `reports/o3_atomic_spotcheck_iteration1.json`、`iteration2.json`、`iteration3.json`、`final_candidate.json`、`o3_atomic_semantic_spotcheck_post_d4f6669.json` 和 `o3_atomic_spotcheck_replay_validation.json` 均作为迭代/口径/重放证据保留；不可替代或覆盖冻结最终报告 `o3_atomic_semantic_spotcheck_post_b61fdb9.json`。


## 2026-11-26：评分基线归档和原题复跑

- 原始 22 条评分项/110 场景报告：[o3_compound_baseline_47226e6.json](reports/o3_compound_baseline_47226e6.json)，72 符合工程预期、28 偏差、10 待教学审核；原始 SHA-256 `986ac1cf213f1ee2a5cfc76ccccd132bd5204b1cf174d7a4ccd884feeee6151e`。
- 产品 `d05450d` [修后报告](reports/o3_compound_post_d05450d.json)：78/22/10，仅 6 个 APPY 起病评分改变，110 组问答不变。其余偏差保留，不能视为全通过。
- `d05450d` 的 20 轮复跑清单当时的 SHA-256 为 `a3cbe557b746fd4b8ae0528eae6ab98c4fb321c83d3df7a4e9e0df5c75c63952`。只更新量表配置改动后的病例哈希并记录来源；原题、预期、判定和原冻结文件不变，pytest 持续验证这一约束。它是旧开发者题集复跑，不是独立新预登记集。
- [复跑报告](reports/o3_atomic_semantic_replay_20261126.json) 20/20，临时数据已清理，真实调用 0。旧冻结清单直接运行在新病例上会正确拒绝哈希变化；在历史提交复现时仍使用原清单。

产品 `6ef93af` 的[逐场景记录](reports/o3_compound_post_6ef93af.json)为 86/14/10，SHA-256 `86b90d4dd3c8f0258bb0fa2db9f768b4a45da1a61372c10ae00c8cba207d45df`。`6ef93af` 的 20 轮复跑清单仅刷新卒中 v2 病例哈希，清单 SHA-256 为 `25ae108743c3700cddd5aa7541a9e2ac3ff92dd320948190294d1dcc6b148a6d`。上一轮清单和对应病例可在 `d05450d` 复现。

产品 `14a6b26` 的[逐场景记录](reports/o3_compound_post_14a6b26.json)为 90/10/10，SHA-256 `f31a1da8349ecce35a0e27eccdab903e5960ba2ea2e565a31fed0603202a0b82`。`14a6b26` 的 20 轮复跑清单仅更新肺炎 v2 文件指纹，清单 SHA-256 `7377d4fd2ba61c25b8a89fc1160af63aa968e23d5ba04579042b51683420b0fa`。历史清单及当时病例可从对应产品提交复现。

产品 `9bde2f8` 的[固定评分集](reports/o3_compound_post_9bde2f8.json)为 94/6/10，[DKA 专项](reports/dka_boundary_post_9bde2f8.json)为 18/18。另有 [CAP 气短措辞基线](reports/cap_rest_baseline_9bde2f8.json)。本轮[20 轮复跑清单](o3_atomic_semantic_replay_20261126.json)只更新 DKA v2 病例指纹，清单 SHA-256 为 `7ae1ffa2d92d9e153da68ee991f05214c29a1509c388f1fcc288a937323de1bc`。

完整命令、局限及剩余问题见[当前工程交接](../docs/工程接手.md)。
