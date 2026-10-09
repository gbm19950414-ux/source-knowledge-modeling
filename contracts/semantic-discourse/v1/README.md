# Discourse organization v1

`garden.export.json` 是社区花园合成来源导出；其 source check/review 明确标为 synthetic contract fixture。
`garden.draft.json` 是同一公开入口使用的声明式组织输入，含孤立块、关联端点、来源条件聚合及项目阅读解释。

执行 `skm organize prepare … --output-dir …` 后填入此 draft，运行 assess 可产生规范化候选和 pending 审阅模板。没有实际独立核对不能发布。测试中 synthetic 预期审阅仅用于回归。

完整合同与流程见 `docs/workflows/discourse-organization.md`；随包 schema 和 MODELING 指引才是程序所用版本。真实书籍工件不存放于此目录。
