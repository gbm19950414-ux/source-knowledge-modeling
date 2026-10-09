# 篇章组织与独立核对 v2

本指引随工具交付并绑定审阅。AI/人工决定含义；程序不生成摘要、推测或接受记录。输入是冻结的source-knowledge-export/v4，发布是独立semantic-discourse-model/v2。来源忠实性不等于科学或个人适用性。

## 作者流程

1. 先读完整来源对象、关系、出处、信息及论述缺口；识别核心问题、必要条件、否定、语气、举例范围、数值归属及作者未给内容。
2. 再填draft.json。chapter是阅读入口；每个package对应一个topic。topic.role mainline/supporting表示项目阅读组织，不自动表示原文执行顺序或因果。题名应简短且准确，summary用多个可核对短项，不把长段全部塞进一张卡。
3. block表达一个相对完整意思。连通分量仅是候选；按语义确认拆并。每个topic的primary element恰好有一个主块；共享信息用related_element_ids，跨主题关联不能偷换归属。孤立要素也保留。不要为了块数量少而混合不同条件或例子。
4. 每项summary包含id/text/origin/refs。refs指向source object_id及具体语义field_path（例如/text、/quantity/value）；工具填content_revision。origin=source只可忠实概括已审作者主张/举例；不要把例子变成通用处方。origin=interpretation额外填rationale和uncertainty，以refs列出的来源作前提。关闭解释层时整项隐藏，所以不可把来源和新推测混在同一summary项。
5. 高层edges连接topic或block：origin=source需列确切source_relation_ids，其端点确实属于对应两端，并填写scope_note约束到实际下层内容。方向/对称不能被聚合改变。origin=interpretation以premises列确切来源字段，rationale说明重建理由，uncertainty说明未获来源确认之处。两种都要具体label，不为整图连通而造“相关”线。
6. 来源漏建的真实含义/关系先通过原v3草稿、来源核验、独立审阅和发布修正，然后准备新组织工作区。不得把来源遗漏藏在推测层或在最终HTML修补。
7. 运行公开assess取得规范化candidate.json和review-template.json；这一步不会批准。作者不能填写自己的独立pass。把源、候选、要求及模板交给独立实际审阅者，保存失败后再修订。

## 阅读结构

`reading_structures` 为可选覆盖范围的显式组织：每项覆盖一个 chapter、topic 或 block 的全部直接成员。chapter 成员为 topics，topic 成员为 blocks，block 成员为自有 elements 与显式 related elements。结构字段为 `id/scope_id/pattern/core_ids/supports/independent_ids/reading_order/refs/rationale`。四种 pattern：`sequence`（至少两个核心，阅读次序不等于执行顺序）、`explanation`（至少一个核心）、`comparison`（至少两个核心）、`multiple`（可无核心）。

`core_ids` 顺序列核心，`supports` 每项填 `node_id/anchor_id/role`，anchor 必须为一个核心，role 为 condition、parameter、reason、example、detail、exception。`independent_ids` 保留不依附核心的内容。这三类恰好且无重叠地覆盖该容器全部成员。`reading_order` 再列同一成员全集一次；其顺序与原文顺序、已发布关系方向均独立。不能由排列暗示作者论证、因果或行动顺序，也不能据此新增关系。

每项写非空 `rationale` 与准确来源 `refs`；每个核心和支撑选择至少有一条其底层 source element 的字段引用。程序核对范围、成员、锚点、引用和修订，独立审阅者核对结构所表达的语义，包括是否遗漏条件、把举例当规则、把解释冒充作者。结构有独立 target/hash；未变旧判断可由实际审阅者明确沿用，新项必须新审。

## 独立核对

先根据来源冻结应保留的含义和可读任务；不以候选图决定标准。逐个核对chapter/topic/block的范围和主次、每项summary及每条高层edge。阅读顺序与原文顺序分别看待，不能因排版相邻接受因果。原文/模型冲突或不确定不能通过空理由的pass。

review.json绑定source、model_revision、guide和每个target的content_sha256，记录实际reviewer、AI/human身份、可得model（未知写unknown）、时间、方法、逐项pass/fail/pending及具体原因。未变内容可以显式沿用真实旧判断；新内容重新核对，不能脚本生成接受。public assess/publish拒绝未覆盖或失效审阅。

必要负例包括：漏条件/否定/正常限定、数值错归、例子泛化、局部关系升成主题整体关系、项目解释冒充作者、仅来源视图仍显示解释摘要。结构/引用合法也需独立核对。程序只证明可验证的结构，不自动证明自然语言同义。

## 阅读与发布

新图默认显示两层，明确实线来源/虚线推测，并标记阅读组织。层级成员不随筛层改变。每项摘要可独立查出处；原文未明、待核和解释不确定分别保留。主干卡片服务审阅，不能直接当训练处方。

通过assess才publish。发布另存新修订，含组织模型、来源副本、审阅/质量、图输入、可读Markdown证据和manifest。Workbench读取声明模型，不补关系。replay验证原工件和指引并复用审阅，不产生新接受。实际浏览器与用户阅读反馈单独记录；生成或测试通过不能替代可读性。
