# 来源优先建模指引 · instructional-source@3.0.0

只以 `work-package.json` 的 `source_units` 和原件开始。先填写 `source-information.json` 与 `discourse-audit.json`，再打开 `base.json` 对照旧模型。不要从旧图推断原文，也不要用对象 ID、锚点数量或连通程度代替含义核对。

## 1. 来源审计

逐个主范围单元读取原文；上下文单元可以帮助解释，但不计作主范围覆盖。信息清单逐项记录独立含义、否定、语气、数量及其基准、举例和适用范围。论述清单逐项记录主张、指代、条件、理由、转折、阐释、跨段衔接和明确缺失。每项填唯一 ID、`meaning`、`anchors`（`locator`、精确 `quote`，必要时 `start`/`end`）、`critical`、`status` 与 `reason`。来源阶段的 `status` 只能是 `required`、`uncertain`、`not_stated` 或 `not_applicable`；是否已建模要在后续投影审计中判断。清单顶部填写真实 `reviewer` 和 `checked_at`。`not_stated` 还必须在来源清单与模型审计同 ID 条目中明确填写相同的 `checked_scope` locator 列表，记录实际已读的范围；不能从 reason 推断、自动复制声明范围或把未查写成已查未述。

完成作者输入后，先按 RUNBOOK 执行 `skm assess --inputs-only`，再付独立语义评审成本。它复用公共校验器，在内存规范化草稿并检查包成员、来源范围与证据，不代填任何审计语义。输入报告的 `valid` 只指输入合同有效，不是语义 pass，不能用于 review/publish。填好独立评估后再执行同一预检检查身份，无需重复语义判断；使用另一个输出路径保留第一次报告。

评估身份中 `draft_sha256` 是规范化 `checked-drafts.json` 内容的 canonical JSON digest，不能用原文件 SHA；`inventory_sha256` 和 `discourse_sha256` 分别是 `source-information.json` 与 `discourse-audit.json` 原文件字节的 raw SHA-256，不能用 JSON canonical digest。guide 身份是 semantic-v3 指引模板文件名→各文件 raw SHA-256 映射的 canonical digest。预检输出期望身份供实际核验后绑定，不能把补 hash 当作语义审阅。V3 export 必须显式提供冻结 commit 或 release 的 `--producer-commit`；RUNBOOK 会保留明确 producer。

短引文在一个单元里出现多次时，必须写明 `start` 和 `end`；工具不会任取第一次。PDF 还要人工核对原页。图表和复杂表格要保存原生区域及未解决的解释，不能凭解析文字补结论。

明确选入主范围的 direct-text 空白行仍保留在 scope。程序只在核验原件/快照身份且该行确为空文本、无视觉/原生结构时记录 `structural-blank`；不要求为它虚构非空引文或语义含义。文字、标题、图表、空文本视觉单元和 PDF 空页不享有此豁免，仍须按其实际证据核验。来源审计及草稿审计只列主范围项；上下文含义可保存在来源笔记和有出处的模型上下文对象中，不冒充主范围覆盖。

来源清单可以另填 `regions`，格式与对象的原生区域证据相同：`locator`、`bbox`、`native_kind`、`native_structure_sha256`、`interpretation_status`、`note`。`visual-regions.json` 是程序从冻结来源生成的区域目录；其中的 `unresolved` 不代表已经看图。实际检查原页后再记录解释状态和必要说明。空文本图像的清单项使用 `anchors: []` 和非空 `regions`，不能伪造引文或字符位置。文字和图像同时支持同一含义时分别保存两种证据。图注只证明图注所说的内容，图中姿势、路径、刻度等含义必须有相应区域依据。清单中有效区域可覆盖该图像来源单元；区域身份校验通过不等于图义已正确建模。

## 2. 建模草稿

`drafts.json` 是声明式输入。每个 proposal 有 `id`、`reason`、`rule_ids`，并可填写 `upsert_elements`、`upsert_relations`、`upsert_packages`、`upsert_information`、`upsert_discourse`、`retire_elements`、`retire_relations`、`retire_packages`、`lineage`。导入工具补齐空数组、引用修订、对象内容哈希和出现位置。执行者不写私有 Python helper，也不填写接受审阅字段。

每个要素独立写 `kind`、`text`、`origin_type`、`modality`、`negated`、`example_scope`、`anchors`。量值要用参数对象的 `quantity.amount/unit/basis/scope` 表示，并指向正确对象。每个关系写 `type`、`from_id`、`to_id`、`origin_type`、`basis`、`anchors`，项目解释还须写 `premises`、`rationale`、`uncertainty`。来源前后只由出现位置表示，不能单凭相邻句补因果边。

审计项在草稿中以 `bindings` 指向对象 ID、内容修订和 JSON Pointer 语义字段；`modeled` 不能只靠锚点、摘要或任意对象引用。作者表述、举例和项目解释分开记录。真歧义保留为缺口，注明检查范围与原因。

文字审计项写单个 `anchor: {locator, quote}`；原生视觉审计项可写单个 `region` 代替 `anchor`，不写字符位置。`bindings` 声明 `{object_id, field_path}`，如 `/text` 或 `/quantity/unit`；内容修订由导入工具计算。视觉项要标为 `modeled`，其区域必须已实际解释，且至少一个绑定对象保存同一原生区域。多种证据放在来源清单及对象中，不为一次视觉检查制造文字位置。

要素或关系已经在区域 `note` 中保存具体图意时，可绑定 `/regions/0/note`（索引按实际区域位置填写），无需把同一图意再抄入正文。该区域必须为 `interpreted`，说明不能为空；`locator`、`bbox`、原生结构哈希、解释状态及整个区域记录不是语义落点。说明进入对象内容修订，改动后须重新核对相应审计与审阅依赖。程序只校验字段和证据身份；独立核对仍要判断说明是否准确表达图义及其范围。

## 3. 后续步骤

导入前按 RUNBOOK 中从 canonical schema 生成的必填字段索引检查输入。每个最终 element 和 relation 必须至少属于一个 package，分别由 `element_ids` 和 `relation_ids` 引用；`context_ids` 不是包成员。包成员只能引用最终存在的对象；没有历史审阅时必填 `legacy_review: null`。工具会列出遗漏或未知 ID。归包应表达原文组织或明确标识的项目组织，不得为通过检查改写来源含义、删除应保留对象或虚构关系。

按 `RUNBOOK.md` 导入、质量评测、独立来源核验、独立语义审阅、发布和派生。检查者的身份、方法和时间必须记录实际情况；模板空字段不代表已检查。科学有效性和个人适用性不属于本工作流的来源忠实性结论。
