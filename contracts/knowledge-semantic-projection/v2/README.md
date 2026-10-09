# Semantic projection v2 synthetic contract

[`community-garden.scope.json`](examples/community-garden.scope.json) selects the non-training source in `contracts/knowledge-foundation/v1/examples/`. It exercises `skm prepare` with source locators and necessary context. `tests/test_semantic_v2.py` creates a proposal with an explicit missing amount, a separate source check and proposal decision, then checks migration, publish replay, cross-package relations, isolated elements, HTML identity and v1 compatibility.

The published v2 format has a [separate schema identity](../../../src/source_knowledge_modeling/schemas/knowledge-semantic-projection-v2.schema.json). A package references global objects; relation endpoints carry exact object content revisions. A v1 proposal review remains historical on migration, while new v2 accepted objects require both a source check and proposal-level semantic review.
