"""Small, source-faithful document annotations and read-only derived views.

The PDF/text and transcription snapshot own content. This module owns neither
training rules nor scientific truth; it checks references and renders reviewed
annotations without making unknown units disappear.
"""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "knowledge-foundation/v1"


class KnowledgeFoundationError(ValueError):
    """An artifact cannot be read without losing an identity or reference."""


def digest_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def digest_text(value: str) -> str:
    return digest_bytes(value.encode("utf-8"))


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KnowledgeFoundationError(f"Cannot read JSON: {path}") from exc
    if not isinstance(value, dict):
        raise KnowledgeFoundationError(f"Expected JSON object: {path}")
    return value


def _schema_path() -> Path:
    return Path(__file__).resolve().parent / "schemas/knowledge-foundation.schema.json"


def _snapshot_units(snapshot: dict[str, Any]) -> dict[str, str]:
    """Read either a compact text fixture or an existing book-knowledge document."""
    if isinstance(snapshot.get("units"), list):
        pairs = [(unit.get("locator"), unit.get("text")) for unit in snapshot["units"]]
    elif isinstance(snapshot.get("pages"), list):
        pairs = [
            (block.get("locator"), block.get("text"))
            for page in snapshot["pages"]
            for block in page.get("blocks", [])
        ]
    else:
        raise KnowledgeFoundationError("Snapshot must contain units or pages.")
    if any(not isinstance(locator, str) or not isinstance(content, str) for locator, content in pairs):
        raise KnowledgeFoundationError("Snapshot contains a unit without locator or text.")
    locators = [locator for locator, _ in pairs]
    if len(locators) != len(set(locators)):
        raise KnowledgeFoundationError("Snapshot has duplicate locators.")
    return dict(pairs)


def validate(artifact: dict[str, Any], *, root: Path | None = None, resolve: bool = False) -> dict[str, Any]:
    """Check shape, references, coverage, and optionally the immutable source files."""
    # jsonschema is available in the repository's dev extra. Keep read-only
    # query/render imports usable where authoring validators are not installed.
    from jsonschema import Draft202012Validator

    schema = load_json(_schema_path())
    errors = sorted(Draft202012Validator(schema).iter_errors(artifact), key=lambda error: str(error.path))
    if errors:
        error = errors[0]
        raise KnowledgeFoundationError(f"Schema error at {list(error.path)}: {error.message}")
    units, annotations, relations = (artifact[name] for name in ("units", "annotations", "relations"))
    for name, items in (("unit", units), ("annotation", annotations), ("relation", relations)):
        ids = [item["id"] for item in items]
        if len(ids) != len(set(ids)):
            raise KnowledgeFoundationError(f"Duplicate {name} ID.")
    orders = [unit["order"] for unit in units]
    if orders != list(range(len(units))):
        raise KnowledgeFoundationError("Unit order must describe a complete reading sequence.")
    unit_ids = {unit["id"] for unit in units}
    locator_by_id = {unit["id"]: unit["locator"] for unit in units}
    model_locators = [unit["locator"] for unit in units]
    if len(model_locators) != len(set(model_locators)):
        raise KnowledgeFoundationError("Duplicate unit locator.")
    if not set(artifact["scope"]["included_locators"]) <= set(model_locators):
        raise KnowledgeFoundationError("Included scope locator is absent from the unit inventory.")
    annotation_ids = {annotation["id"] for annotation in annotations}
    for annotation in annotations:
        if not set(annotation["unit_ids"]) <= unit_ids:
            raise KnowledgeFoundationError(f"Unknown unit in annotation {annotation['id']}.")
        if annotation["origin_type"] in ("source_claim", "source_example") and not annotation["source_checked"]:
            raise KnowledgeFoundationError(f"Unchecked statement cannot be a reviewed source claim: {annotation['id']}.")
        if "review" in annotation and not set(locator_by_id[uid] for uid in annotation["unit_ids"]) <= set(annotation["review"]["checked_locators"]):
            raise KnowledgeFoundationError(f"Review does not cover annotation sources: {annotation['id']}.")
    for relation in relations:
        if relation["from_id"] not in annotation_ids or relation["to_id"] not in annotation_ids:
            raise KnowledgeFoundationError(f"Unknown endpoint in relation {relation['id']}.")
        if not set(relation["support_unit_ids"]) <= unit_ids:
            raise KnowledgeFoundationError(f"Unknown support unit in relation {relation['id']}.")
        if "review" in relation and not set(locator_by_id[uid] for uid in relation["support_unit_ids"]) <= set(relation["review"]["checked_locators"]):
            raise KnowledgeFoundationError(f"Review does not cover relation sources: {relation['id']}.")
    represented = {unit_id for annotation in annotations for unit_id in annotation["unit_ids"]}
    for unit in units:
        if unit["interpretation_status"] in ("interpreted", "partially_interpreted") and unit["id"] not in represented:
            raise KnowledgeFoundationError(f"Interpreted unit without annotation: {unit['id']}.")
        if unit["interpretation_status"] in ("unreviewed", "partially_interpreted") and not unit["review_note"]:
            raise KnowledgeFoundationError(f"Unreviewed or partial unit needs a reason: {unit['id']}.")
        if unit["transcription_status"] in ("failed", "needs_correction", "machine_extracted") and not unit["review_note"]:
            raise KnowledgeFoundationError(f"Transcription gap needs a reason: {unit['id']}.")
    if resolve:
        if root is None:
            raise KnowledgeFoundationError("Source resolution needs a repository root.")
        source = artifact["source"]
        original = root / source["path"]
        snapshot_path = root / source["snapshot_path"]
        for path, expected in ((original, source["sha256"]), (snapshot_path, source["snapshot_sha256"])):
            try:
                actual = digest_bytes(path.read_bytes())
            except OSError as exc:
                raise KnowledgeFoundationError(f"Source file unavailable: {path}") from exc
            if actual != expected:
                raise KnowledgeFoundationError(f"Source revision mismatch: {path}")
        snapshot_units = _snapshot_units(load_json(snapshot_path))
        if set(model_locators) != set(snapshot_units):
            raise KnowledgeFoundationError("Unit inventory differs from the source snapshot.")
        for unit in units:
            text = snapshot_units[unit["locator"]]
            if unit["content_sha256"] != (digest_text(text) if text else None):
                raise KnowledgeFoundationError(f"Transcription mismatch at {unit['locator']}.")
    return coverage(artifact)


def coverage(artifact: dict[str, Any]) -> dict[str, Any]:
    units = artifact["units"]
    return {
        "artifact_id": artifact["artifact_id"],
        "revision_id": artifact["revision"]["id"],
        "unit_count": len(units),
        "transcription": dict(Counter(unit["transcription_status"] for unit in units)),
        "interpretation": dict(Counter(unit["interpretation_status"] for unit in units)),
        "unreviewed_locators": [unit["locator"] for unit in units if unit["interpretation_status"] == "unreviewed"],
        "partial_locators": [unit["locator"] for unit in units if unit["interpretation_status"] == "partially_interpreted"],
        "transcription_gaps": [unit["locator"] for unit in units if unit["transcription_status"] in ("needs_correction", "failed", "machine_extracted")],
    }


def query(artifact: dict[str, Any], *, term: str = "") -> dict[str, Any]:
    """Return bounded, traceable claims, relationships, and the sample's gaps."""
    term = term.casefold().strip()
    units = {unit["id"]: unit for unit in artifact["units"]}
    selected = [
        annotation for annotation in artifact["annotations"]
        if not term or term in " ".join((annotation["summary"], annotation["role"], *annotation["conditions"], *annotation["exceptions"])).casefold()
    ]
    selected_ids = {annotation["id"] for annotation in selected}
    related = [relation for relation in artifact["relations"] if relation["from_id"] in selected_ids or relation["to_id"] in selected_ids]
    context_ids = {endpoint for relation in related for endpoint in (relation["from_id"], relation["to_id"])} - selected_ids
    def with_locators(annotation: dict[str, Any]) -> dict[str, Any]:
        return {**annotation, "locators": [units[unit_id]["locator"] for unit_id in annotation["unit_ids"]]}
    return {
        "artifact_id": artifact["artifact_id"],
        "revision_id": artifact["revision"]["id"],
        "source_id": artifact["source"]["id"],
        "source_sha256": artifact["source"]["sha256"],
        "annotations": [with_locators(annotation) for annotation in selected],
        "context_annotations": [with_locators(annotation) for annotation in artifact["annotations"] if annotation["id"] in context_ids],
        "relations": [{**relation, "support_locators": [units[unit_id]["locator"] for unit_id in relation["support_unit_ids"]]} for relation in related],
        "unreviewed_locators": coverage(artifact)["unreviewed_locators"],
        "partial_locators": coverage(artifact)["partial_locators"],
    }


def render_markdown(artifact: dict[str, Any]) -> str:
    """A learning view generated solely from the same version used by query()."""
    result = query(artifact)
    lines = [
        f"# {artifact['source']['title']} — {artifact['scope']['description']}", "",
        f"来源：`{result['source_id']}` · SHA-256 `{result['source_sha256']}` · 标注版 `{result['revision_id']}`", "",
        "本视图记录来源主张与项目解释；来源核对不代表事实或科学验证，也不直接构成领域决策规则。", "",
        "## 内容与论证", "",
    ]
    for annotation in result["annotations"]:
        lines.append(f"- **{annotation['id']}** [{annotation['origin_type']} / {annotation['role']}] {annotation['summary']}")
        lines.append(f"  - 定位：{', '.join('`'+locator+'`' for locator in annotation['locators'])}")
        lines.append(f"  - 来源核对：{annotation['source_checked']}；科学评估：{annotation['scientific_status']}")
        if "review" in annotation:
            review = annotation["review"]
            lines.append(f"  - 来源审阅：{review['reviewer']} / {review['check_method']} / {review['reviewed_at']}")
        if "applicability_status" in annotation:
            lines.append(f"  - 适用性评估：{annotation['applicability_status']}")
        if annotation["conditions"]:
            lines.append(f"  - 条件：{'；'.join(annotation['conditions'])}")
        if annotation["exceptions"]:
            lines.append(f"  - 例外：{'；'.join(annotation['exceptions'])}")
    lines += ["", "## 关系", ""]
    for relation in result["relations"]:
        lines.append(f"- `{relation['from_id']}` —{relation['type']}→ `{relation['to_id']}`；依据：{', '.join(relation['support_locators'])}；{relation['origin_type']}；{relation['note']}")
        if "review" in relation:
            lines.append(f"  - 来源审阅：{relation['review']['reviewer']} / {relation['review']['check_method']}")
    if result["relations"]:
        node_ids = {annotation["id"]: f"n{index}" for index, annotation in enumerate(result["annotations"])}
        lines += ["", "## 关系图", "", "```mermaid", "flowchart LR"]
        for annotation in result["annotations"]:
            lines.append(f"    {node_ids[annotation['id']]}[\"{annotation['id']}\"]")
        for relation in result["relations"]:
            lines.append(f"    {node_ids[relation['from_id']]} -->|{relation['type']}| {node_ids[relation['to_id']]}")
        lines.append("```")
    lines += ["", "## 未解释或待纠正", ""]
    for unit in artifact["units"]:
        if unit["interpretation_status"] in ("unreviewed", "partially_interpreted") or unit["transcription_status"] in ("failed", "needs_correction", "machine_extracted"):
            lines.append(f"- `{unit['locator']}`：{unit['interpretation_status']} / {unit['transcription_status']}；{unit['review_note']}")
    if lines[-1] == "":
        lines.append("- 本样本无待处理单元。")
    return "\n".join(lines).rstrip() + "\n"


def impact(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Conservative stable-ID diff; never rewrites an earlier published revision."""
    if old["artifact_id"] != new["artifact_id"] or new["revision"]["parent_id"] != old["revision"]["id"]:
        raise KnowledgeFoundationError("Revision lineage mismatch.")
    def changed(name: str) -> set[str]:
        left = {item["id"]: item for item in old[name]}
        right = {item["id"]: item for item in new[name]}
        return {key for key in left.keys() | right.keys() if left.get(key) != right.get(key)}
    source_changed = old["source"]["sha256"] != new["source"]["sha256"]
    snapshot_changed = old["source"]["snapshot_sha256"] != new["source"]["snapshot_sha256"]
    unit_changes = changed("units")
    if source_changed or snapshot_changed:
        # Text digests locate changed text; visual-only material has no such
        # digest and must be treated as potentially changed with the source.
        unit_changes |= {unit["id"] for unit in old["units"] + new["units"] if unit["content_sha256"] is None}
        if not unit_changes:
            unit_changes = {unit["id"] for unit in old["units"] + new["units"]}
    annotation_changes = changed("annotations")
    affected_annotations = annotation_changes | {
        annotation["id"] for annotation in old["annotations"] + new["annotations"]
        if set(annotation["unit_ids"]) & unit_changes
    }
    relation_changes = changed("relations")
    affected_relations = relation_changes | {
        relation["id"] for relation in old["relations"] + new["relations"]
        if relation["from_id"] in affected_annotations or relation["to_id"] in affected_annotations
        or set(relation["support_unit_ids"]) & unit_changes
    }
    return {
        "from_revision": old["revision"]["id"], "to_revision": new["revision"]["id"],
        "source_changed": source_changed,
        "snapshot_changed": snapshot_changed,
        "changed_unit_ids": sorted(unit_changes),
        "affected_annotation_ids": sorted(affected_annotations),
        "affected_relation_ids": sorted(affected_relations),
        "affected_views": ["query", "learning_markdown"] if source_changed or snapshot_changed or unit_changes or affected_annotations or affected_relations else [],
    }
