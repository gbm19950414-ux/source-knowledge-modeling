# 最小必要篇章阅读组织 v3

此工作区引用冻结的 `source-knowledge-export/v4` 与经来源 snapshot 逐段核验的 document map。`source.json` 保存完整来源；发布图只展示 document map 的 section ranges 中实际出现的来源对象。其他内容不得因同属一章自动并入。已发布要素正文保持原样；它可能是已审语义表述，不能冒称原文引文。旧 map 的 block、summary、object 映射与路线不继承。

## 填写

编辑 `draft.json`：填写实际 `author` 身份；核对章/节标题、parent、order、range。无输入 map 时目录为 `pending`，可修改目录并由独立审阅者逐项核对后发布。`blocks`、`edges`、`reading_structures` 都可为空。无需给每个元素造一个块或摘要。只为阅读必要性明确、短名且范围准确的分组添加 block，其 `element_ids` 必须在父节实际出现。原文表格按来源内容保留，不在组名或依据中整表重写。

高层边必须指定 11 种来源关系之一的 `type`、真实方向、明确的 `from_element_ids` / `to_element_ids`、来源字段 `premises`。来源 rollup 还要列出同类型、同端点范围的 `source_relation_ids` 与有限 `scope_note`；新解释边需简短 `rationale`，可列真实不确定性。每条新增推测须在简短 rationale 中说明具体歧义、支持依据和删除后会造成的误读；主题相近、位置相邻、画面断开均不足以建边。不要凭阅读顺序生成关系。无唯一核心时不填阅读结构；部分结构只给确实分配的直属成员，未分配成员仍按原来源顺序展示。多次出现的同一来源对象保持同一个 ID。

## 独立审阅

`assess` 生成 `candidate.json`、`review-template.json`、`quality.json`；不会产生接受记录。独立审阅者须逐项核对目录、分组、关系、阅读结构和预先冻结要求，对每项填写 pass/fail、具体原因、实际身份、时间与实际模型（未知填 `unknown`）。结构合法的语义错误必须在审阅中拒绝。作者不能自审。Humanizer 固定资源在 `resources/humanizer/`，其字节哈希绑定工作包、指引发布依赖；它仅辅助措辞，不覆盖来源事实和审阅判断。

发布生成 `diagram-domain-model/v4` 与 `source-reading-map/v2`。根/节为原书结构；图可无 block、无高层边、无阅读结构。来源边仅用固定类型短标签。图和 map 通过文件哈希绑定。重放只复用原有 review，不增加审阅事件。旧版已发布物请用其原固定安装包重放。
