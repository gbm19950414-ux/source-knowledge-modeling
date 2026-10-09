# 知识块与精确关系组织 v4

此工作区使用冻结的 `source-knowledge-export/v4`、逐段核验的 document map、`semantic-content-types/v1` 注册词表。作者编辑 `draft.json`；公开 `assess` 规范化并产生候选和待审模板，不自动产生接受。独立审阅者实际核对后，才能发布。

## 块

块是围绕一个局部问题的语义整体，可多中心、非树状，成员不必图连通。短 `title`、准确 `element_ids`、一个或多个已注册 `content_types`、至少一个指向成员来源字段的 `focus_refs` 必填。需要解释共同焦点而前三者不足时写 `focus_rationale`。无法归类的候选保持待核，不写临时新类别；不为每个要素强建块。`reading_structures` 的 core、支持角色和阅读顺序仅用于阅读组织，不改变要素 kind 或块内容类型。

## 关系

每条高层边写一个现有 11 类型中的主 `type`、真实 `direction`、块或节端点和精确 `from_element_ids`、`to_element_ids`。`relation_form` 决定规则：

- `source_native`：`origin=source`，`basis=explicit` 或 `inferred_from_source`，直接来源 `premises`、有限 `scope_note`；无需底层关系 ID。已有同一来源边时引用而不重复。
- `source_rollup`：`origin=source`，`basis=source_rollup`，非空 `source_relation_ids`、有限 `scope_note`；成员必须是所列来源边实际端点并集。每对端点保持原样，不能推断交叉配对。它不产生新的语义事实。
- `project_inference`：`origin=interpretation`，`basis=project_interpretation`，来源 `premises`、具体 `overall_effect`、简短 `rationale`、有限 `scope_note` 必填。只有存在实际不确定项时填 `uncertainty`。无需底层同类型边；引用来源关系作前提也不会变成汇总。

引用格式为 `{"object_id":"...","field_path":"/text","content_revision":"..."}`；可省略修订，由公开工具填入精确来源修订。`qualifiers` 的 `applies_to` 为 from/to/relation，`dimension` 为 condition/time/modality/scope，`source_ref` 指向已存在的来源字段；不写值副本。给既有来源边补限定时用顶层 `relation_bindings`，仅保存 `source_relation_id` 和限定，不复制 type/端点。缺少独立含义时先回到来源草稿补模。

断开、相邻、外观需要线，都不能单独证明关系。一个来源字段的语气不能悄悄转移到另一个端点或关系。原文范围不是语义生效时间。

## 审阅与发布

审阅者逐项检查目录、块、边、限定、阅读结构及冻结要求；检查源前提、真实端点、限定作用域、推测范围和可能误读。程序的结构通过不代表语义通过。修改成员、源字段、来源修订、限定或词表会使相应审阅依赖过期。发布给出 `diagram-domain-model/v5` 和不改变形状的 `source-reading-map/v2`，并保留已核验来源身份。`query` 从确切发布修订返回对象和引用解析值；重放复用真实审阅，无新增接受事件。
