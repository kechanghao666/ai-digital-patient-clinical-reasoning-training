# 同类优秀软件设计研究与取舍（2026-11-13）

此前几轮研究集中在视觉风格。本轮研究的是另一个问题：成熟的虚拟病人和教学软件，靠哪些功能让训练有效、让教师用得起来。只吸收与官方课题1要求直接相关、能在现有安全边界内实现的设计。来源均为公开资料，未复制任何产品界面或内容。

## 研究对象与结论

| 对象 | 关键设计 | 本项目取舍 |
|---|---|---|
| Body Interact（虚拟病人模拟器） | 问诊按结构化类别组织：问候与自我介绍、主诉、现病史、既往史、用药、过敏、家族史、社会史；2025 年起绩效区按临床推理阶段重组，问诊（History Taking、SAMPLE、OPQRST）计入沟通分；提供时间线报告和即时复盘 | 采纳“结构化问诊类别”：学生端显示问诊结构进度，只依据学生自己问了什么，不依据病例答案 |
| DxR Clinician（SIU 医学院） | 不用选择题，学生自由完成问诊、查体、检查、鉴别和计划；结束后与专家推理对比，指出遗漏发现、不必要检查和过早闭合；教师可看个人和群体表现 | 已有自由问诊、五环节评分和扣分建议；本轮补群体视角，即教师看板 |
| OSCE 问诊清单（Geeky Medics 等） | 固定骨架：介绍与同意、主诉、现病史（疼痛用 SOCRATES：部位、起病、性质、放射、伴随、时间、加重缓解、程度）、ICE、系统回顾、既往/用药/过敏、家族史、社会史、总结 | 进度面板按此骨架划分 8 个领域，现病史再细分 SOCRATES 要素 |
| 学习分析教师看板研究 | 概览页满足日常决策、详情页用于深入；只展示能引出教学行动的维度；多数看板缺少可执行洞察 | 看板分三层：总体、按病例的环节得分与高频失分项、逐条训练；高频失分项直接给出“下次课重点” |
| LLM 虚拟病人系统综述（JMIR 2026） | 幻觉仍是首要风险；评价指标不统一 | 保持“后端授权事实、模型只选择”的边界；新增可复跑的问诊评测集，分别统计命中、漏答、多答和泄露 |

## 落地清单

1. **问诊结构进度（学生端）**：8 个领域加现病史要素。依据只来自学生自己的提问文本，因此不会提示病例里有什么、没有什么，也不改变评分。封存后在复盘页保留同一视图，方便对照 OSCE 骨架查漏。
2. **班级训练看板（教师端）**：训练数、封存数、平均得分率、待复核数；按病例看五环节得分率和高频失分项；逐条训练列表可跳到现有评分复核。只读，展示的是原规则分；教师裁定另行保存，不覆盖原分。
3. **问诊评测集与自动报告（官方交付物）**：五科各 20 条带期望事实的问句，外加泄露探针和三次重复一致性；脚本输出逐条判定和汇总指标。标注者是开发者本人，属于工程标注，不等于官方要求的专家评测。
4. **交付文档**：使用手册、演示视频脚本、测试报告，以及更新后的 AgentsHub 交付目录说明。

## 有意不做

- **不显示“还差哪些关键问题”。** 这会直接泄露评分量表，学生可以照单问完。
- **不引入语音和三维人体。** 官方列为可选加分项，当前优先解决有效性和交付证据。
- **不把看板当成绩系统。** 现有分数没有经过专家一致性验证。

## 来源

- [Body Interact 厂商介绍（HealthySimulation）](https://www.healthysimulation.com/vendors/body-interact/)
- [Body Interact：简化问诊流程](https://bodyinteract.com/?p=28931)
- [Body Interact：沟通技能的教学与评估](https://bodyinteract.com/?p=33837)
- [Body Interact 帮助：与虚拟病人沟通](https://help.bodyinteract.com/body-interact-simulator-app/using-the-simulation/communicate-virtual-patient/)
- [DxR Clinician（HealthySimulation）](https://www.healthysimulation.com/vendor-product/dxr-clinician/)
- [DxR 学生报告与推理轨迹（ascilite 1997）](https://ascilite.org/conferences/perth97/papers/Bryce/Bryce.html)
- [Geeky Medics 胸痛问诊 OSCE 清单](https://geekymedics.com/wp-content/uploads/2020/09/OSCE-Checklist-Chest-Pain-History-Taking.pdf)
- [Geeky Medics 腹痛问诊 OSCE 清单](https://geekymedics.com/wp-content/uploads/2023/09/OSCE-Checklist-Abdominal-Pain-History-Taking.pdf)
- [教师看板设计洞察（arXiv 2305.16851）](https://arxiv.org/pdf/2305.16851)
- [教师看板系统综述与清单（Kaliisa 等）](https://Educationaltechnologyjournal.springeropen.com/counter/pdf/10.1186/s41239-023-00394-6.pdf)
- [LLM 虚拟病人问诊系统综述（JMIR Med Inform 2026）](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12811743/)
