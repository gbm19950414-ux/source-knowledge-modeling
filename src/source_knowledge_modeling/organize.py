"""Reviewed discourse organization over an immutable semantic export.

Authors supply meaning and independent reviewers judge it. This module checks
identities, coverage, provenance and replay; it never generates an acceptance.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import shlex
from typing import Any

from jsonschema import Draft202012Validator

from .core import KnowledgeFoundationError, load_json
from .semantic_v3 import digest, validate_projection

RESOURCES = Path(__file__).resolve().parent
DRAFT = "semantic-discourse-draft/v1"
MODEL = "semantic-discourse-model/v1"
REVIEW = "semantic-discourse-review/v1"
RUN = "semantic-discourse-run/v1"
GUIDE = RESOURCES / "templates" / "discourse-modeling-v1.md"
GUIDE_V2 = RESOURCES / "templates" / "discourse-modeling-v2.md"
LABELS = {"supports": "提供理由", "conditions": "适用条件", "qualifies": "限定范围",
          "causes": "解释原因", "contrasts_with": "形成对照", "elaborates": "进一步阐释",
          "precedes": "步骤先后", "quantifies": "量值归属", "example_of": "举例说明",
          "exception_to": "规则例外", "replaces": "替代"}


def fail(message: str) -> None:
    raise KnowledgeFoundationError("Organization: " + message)


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def write(path: Path, value: Any, *, mutable: bool = False) -> bool:
    data = value.encode() if isinstance(value, str) else json_bytes(value)
    if path.exists():
        if path.read_bytes() == data:
            return True
        if not mutable:
            fail(f"Existing immutable file differs: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
    return False


def producer(format_version: int = 1) -> dict:
    if format_version == 4:
        from . import organize_v4
        return organize_v4.producer()
    if format_version == 3:
        from . import organize_v3
        return organize_v3.producer()
    names = [str(p.relative_to(RESOURCES)) for p in sorted(RESOURCES.glob("*.py"))] + ["templates/discourse-modeling-v1.md",
             "schemas/knowledge-semantic-projection-v3.schema.json",
             "profiles/instructional-source-3.0.0.json",
             "schemas/semantic-discourse-draft-v1.schema.json",
             "schemas/semantic-discourse-review-v1.schema.json",
             "schemas/semantic-discourse-model-v1.schema.json"]
    if format_version == 2:
        names += ["templates/discourse-modeling-v2.md", "schemas/semantic-discourse-draft-v2.schema.json",
                  "schemas/semantic-discourse-model-v2.schema.json"]
    return {"name": "source-knowledge-modeling", "component": f"organize/v{format_version}",
            "implementation_sha256": digest({n: file_hash(RESOURCES / n) for n in names}),
            "guide_sha256": file_hash(guide(format_version))}


def guide(format_version: int) -> Path:
    if format_version == 4:
        from . import organize_v4
        return organize_v4.GUIDE
    if format_version == 3:
        from . import organize_v3
        return organize_v3.GUIDE
    if format_version not in (1, 2):
        fail(f"Unsupported discourse format version: {format_version}")
    return GUIDE if format_version == 1 else GUIDE_V2


def version_of(value: dict) -> int:
    name = value.get("schema_version", "")
    if name in (DRAFT, MODEL):
        return 1
    if name in ("semantic-discourse-draft/v2", "semantic-discourse-model/v2"):
        return 2
    if name in ("semantic-discourse-draft/v3", "semantic-discourse-model/v3"):
        return 3
    if name in ("semantic-discourse-draft/v4", "semantic-discourse-model/v4"):
        return 4
    fail(f"Unsupported discourse schema version: {name}")


def schema(value: Any, name: str) -> None:
    errors = sorted(Draft202012Validator(load_json(RESOURCES / "schemas" / name)).iter_errors(value),
                    key=lambda error: str(error.path))
    if errors:
        fail(f"{name} at {list(errors[0].path)}: {errors[0].message}")


def index(values: list[dict], name: str) -> dict[str, dict]:
    result = {}
    for item in values:
        ident = item["id"]
        if ident in result or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", ident):
            fail(f"Duplicate or invalid {name} ID: {ident}")
        result[ident] = item
    return result


def source_index(source: dict) -> tuple[dict, dict, dict]:
    if source.get("schema_version") != "source-knowledge-export/v4":
        fail("prepare requires source-knowledge-export/v4")
    if source.get("artifact_schema_version") != "knowledge-semantic-projection/v3":
        fail("Only the explicit v3 source interpretation is supported")
    # Reconstruct only the portable projection envelope. resolve=False validates
    # references and saved review coverage without re-opening private source files.
    projection = {k: v for k, v in source.items() if k not in
                  {"producer", "artifact_schema_version", "artifact_revision_id"}}
    projection.update(schema_version="knowledge-semantic-projection/v3", legacy=None,
                      revision={"id": source.get("artifact_revision_id"), "parent_id": None,
                                "note": "Portable, read-only export validation."})
    validate_projection(projection)
    elements, relations = index(source["elements"], "source element"), index(source["relations"], "source relation")
    packages = index(source["packages"], "source package")
    for obj in [*elements.values(), *relations.values()]:
        if obj["review_state"] != "accepted" or obj["origin_type"] not in {
                "source_claim", "source_example", "project_interpretation"}:
            fail(f"Organization requires reviewed, identified source objects: {obj['id']}")
    return elements, relations, packages


def pointer(value: Any, path: str) -> Any:
    if not path.startswith("/"):
        fail(f"Expected a semantic JSON field pointer: {path}")
    try:
        for part in path[1:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            value = value[int(part)] if isinstance(value, list) else value[part]
        return value
    except (KeyError, IndexError, ValueError, TypeError):
        fail(f"Absent semantic field: {path}")


def normalize(source: dict, draft: dict) -> dict:
    format_version = version_of(draft)
    schema(draft, f"semantic-discourse-draft-v{format_version}.schema.json")
    elements, relations, packages = source_index(source)
    objects = {**elements, **relations}
    if draft["source_sha256"] != digest(source):
        fail("Draft names another source revision")
    result = deepcopy(draft)
    if draft["author"].strip() == "unknown":
        fail("Actual author identity is required")
    topics, blocks = index(result["topics"], "topic"), index(result["blocks"], "block")
    ids = [result["chapter"]["id"], *topics, *blocks]
    reserved = (set(objects) | {"element:" + x for x in elements}
                | {"relation:" + x for x in relations} | {"source:" + x for x in objects})
    if len(ids) != len(set(ids)) or set(ids) & reserved:
        fail("Organization IDs must be distinct from one another and source IDs")
    if {t["package_id"] for t in topics.values()} != set(packages) or len(topics) != len(packages):
        fail("Exactly one topic must cover each source package")
    if not any(t["role"] == "mainline" for t in topics.values()):
        fail("At least one topic must be an explicit reading mainline")

    def refs(values: list[dict], *, origin: str = "interpretation") -> list[dict]:
        normalized = []
        for ref in values:
            obj = objects.get(ref["object_id"])
            if obj is None:
                fail(f"Absent source reference: {ref['object_id']}")
            field = ref["field_path"]
            if not field.startswith("/") or field.split("/")[1] not in {"text", "quantity", "modality", "negated", "example_scope", "type", "note", "rationale", "uncertainty", "from_id", "to_id"}:
                fail(f"Reference must address meaning, not an ID, anchor or hash: {field}")
            pointer(obj, field)
            if ref.get("content_revision", obj["content_revision"]) != obj["content_revision"]:
                fail(f"Stale reference: {obj['id']}")
            if origin == "source" and (obj["origin_type"] not in ("source_claim", "source_example") or obj["review_state"] != "accepted"):
                fail(f"Source summary cites interpretation or unreviewed content: {obj['id']}")
            normalized.append({"object_id": obj["id"], "content_revision": obj["content_revision"], "field_path": field})
        if len({digest(r) for r in normalized}) != len(normalized):
            fail("Repeated source reference")
        return normalized

    all_ids = set(ids)
    claim_ids = set()
    for node in [result["chapter"], *topics.values(), *blocks.values()]:
        for claim in node["summary"]:
            if claim["id"] in claim_ids or claim["id"] in all_ids or claim["id"] in reserved:
                fail(f"Duplicate summary ID: {claim['id']}")
            claim_ids.add(claim["id"])
            claim["refs"] = refs(claim["refs"], origin=claim["origin"])
            if claim["origin"] == "interpretation" and (not claim.get("rationale", "").strip() or not claim.get("uncertainty", "").strip()):
                fail(f"Interpretive summary needs rationale and uncertainty: {claim['id']}")
    covered: dict[str, set] = {ident: set() for ident in topics}
    for block in blocks.values():
        topic = topics.get(block["topic_id"])
        if topic is None:
            fail(f"Block has absent topic: {block['id']}")
        owned = set(packages[topic["package_id"]]["element_ids"])
        members = block["element_ids"]
        if len(members) != len(set(members)) or not set(members) <= owned:
            fail(f"Block members must be unique owned source elements: {block['id']}")
        if covered[topic["id"]] & set(members):
            fail(f"Primary membership is ambiguous within topic: {block['id']}; use related_element_ids")
        covered[topic["id"]].update(members)
        related = block.get("related_element_ids", [])
        if len(related) != len(set(related)) or not set(related) <= set(elements) or set(related) & set(members):
            fail(f"Invalid related references: {block['id']}")
    for ident, topic in topics.items():
        if covered[ident] != set(packages[topic["package_id"]]["element_ids"]):
            fail(f"Topic primary coverage incomplete: {ident}")
    edges = index(result["edges"], "organization edge")
    if set(edges) & (all_ids | claim_ids | reserved):
        fail("Organization edge ID collision")
    membership = {b["id"]: set(b["element_ids"]) for b in blocks.values()}
    membership.update({t["id"]: covered[t["id"]] for t in topics.values()})
    for edge in edges.values():
        if edge["from_id"] not in membership or edge["to_id"] not in membership or edge["from_id"] == edge["to_id"]:
            fail(f"Higher relationship must connect distinct topics or blocks: {edge['id']}")
        edge["premises"] = refs(edge["premises"], origin=edge["origin"])
        support = edge.get("source_relation_ids", [])
        if not set(support) <= set(relations) or len(support) != len(set(support)):
            fail(f"Absent/repeated supporting source relation: {edge['id']}")
        if edge["origin"] == "source":
            if not support or not edge["scope_note"].strip():
                fail(f"Source roll-up needs exact edges and a bounded scope: {edge['id']}")
            for rid in support:
                r = relations[rid]
                if edge["direction"] != ("symmetric" if r["type"] == "contrasts_with" else "directed"):
                    fail(f"Source roll-up changes relation direction: {edge['id']}/{rid}")
                pairs = [(r["from_id"], r["to_id"])]
                if r["type"] == "contrasts_with":
                    pairs.append((r["to_id"], r["from_id"]))
                if r["origin_type"] not in ("source_claim", "source_example") or r["review_state"] != "accepted" or not any(a in membership[edge["from_id"]] and b in membership[edge["to_id"]] for a, b in pairs):
                    fail(f"Source roll-up changes source endpoints or identity: {edge['id']}/{rid}")
        elif not edge["rationale"].strip() or not edge["uncertainty"].strip():
            fail(f"Interpretation needs premises, rationale and uncertainty: {edge['id']}")
    if format_version == 2:
        structures = index(result["reading_structures"], "reading structure")
        if set(structures) & (all_ids | claim_ids | set(edges) | reserved):
            fail("Reading structure ID collision")
        if len({item["scope_id"] for item in structures.values()}) != len(structures):
            fail("At most one reading structure may organize each scope")
        for structure in structures.values():
            scope = structure["scope_id"]
            if scope == result["chapter"]["id"]:
                universe = set(topics)
            elif scope in topics:
                universe = {b["id"] for b in blocks.values() if b["topic_id"] == scope}
            elif scope in blocks:
                block = blocks[scope]
                universe = set(block["element_ids"] + block.get("related_element_ids", []))
            else:
                fail(f"Absent reading structure scope: {scope}")
            core = structure["core_ids"]
            supports = structure["supports"]
            others = structure["independent_ids"]
            members = [*core, *(s["node_id"] for s in supports), *others]
            if len(members) != len(set(members)) or set(members) != universe:
                fail(f"Reading structure must partition exact scope members: {structure['id']}")
            if len(structure["reading_order"]) != len(universe) or set(structure["reading_order"]) != universe:
                fail(f"Reading order must cover exact scope members: {structure['id']}")
            if any(s["anchor_id"] not in core or s["anchor_id"] == s["node_id"] for s in supports):
                fail(f"Support anchor must name a distinct core: {structure['id']}")
            if structure["pattern"] in ("sequence", "comparison") and len(core) < 2:
                fail(f"Sequence/comparison requires two cores: {structure['id']}")
            if structure["pattern"] == "explanation" and not core:
                fail(f"Explanation requires a core: {structure['id']}")
            if not structure["rationale"].strip():
                fail(f"Reading structure needs rationale: {structure['id']}")
            structure["refs"] = refs(structure["refs"])
            cited_elements = {r["object_id"] for r in structure["refs"] if r["object_id"] in elements}
            for member in [*core, *(s["node_id"] for s in supports)]:
                if scope in blocks:
                    underlying = {member}
                elif scope in topics:
                    underlying = set(blocks[member]["element_ids"])
                else:
                    underlying = covered[member]
                if not cited_elements.intersection(underlying):
                    fail(f"Core/support choice lacks source element reference: {structure['id']}/{member}")
        result["reading_structures"] = sorted(structures.values(), key=lambda x: x["id"])
    result["schema_version"] = f"semantic-discourse-model/v{format_version}"
    result["source_revision"] = source["artifact_revision_id"]
    result["guide_sha256"] = file_hash(guide(format_version))
    result["topics"] = sorted(topics.values(), key=lambda x: (x["order"], x["id"]))
    result["blocks"] = sorted(blocks.values(), key=lambda x: (x["topic_id"], x["order"], x["id"]))
    result["edges"] = list(edges.values())
    result["revision"] = digest(result)
    schema(result, f"semantic-discourse-model-v{format_version}.schema.json")
    return result


def review_units(model: dict) -> list[dict]:
    units = []
    for item in [model["chapter"], *model["topics"], *model["blocks"], *model["edges"]]:
        units.append({"target_id": item["id"], "content_sha256": digest(item)})
        for claim in item.get("summary", []):
            units.append({"target_id": claim["id"], "content_sha256": digest(claim)})
    for item in model.get("reading_structures", []):
        units.append({"target_id": item["id"], "content_sha256": digest(item)})
    return units


def review_template(model: dict) -> dict:
    return {"schema_version": REVIEW, "source_sha256": model["source_sha256"], "model_revision": model["revision"],
            "guide_sha256": model["guide_sha256"], "reviewer": "unknown", "reviewer_type": "ai", "model": "unknown",
            "checked_at": "unknown", "method": "Compare source meaning, membership and interpretation boundaries",
            "judgments": [{**u, "verdict": "pending", "reason": "Not reviewed"} for u in review_units(model)]}


def reuse_review(run: Path, previous_review: Path) -> dict:
    if (run / "work-package.json").exists() and load_json(run / "work-package.json")["inputs"]["producer"]["component"] == "organize/v4":
        from . import organize_v4
        return organize_v4.reuse_review(run, previous_review)
    if (run / "work-package.json").exists() and load_json(run / "work-package.json")["inputs"]["producer"]["component"] == "organize/v3":
        from . import organize_v3
        return organize_v3.reuse_review(run, previous_review)
    """Prepare an explicit, attributable carry-forward for an independent reviewer.

    This never writes review.json or passes a new unit. The reviewer must complete
    the new record, including their identity and time, before assess can pass.
    """
    run = run.resolve()
    model = load_json(run / "candidate.json")
    prior = load_json(previous_review)
    schema(prior, "semantic-discourse-review-v1.schema.json")
    if prior["source_sha256"] != model["source_sha256"] or prior["reviewer"] == "unknown" or prior["checked_at"] == "unknown":
        fail("Prior review must identify a real reviewer and the same source")
    requirements = load_json(run / "requirements.json") if (run / "requirements.json").exists() else None
    if prior.get("requirements_sha256") != (digest(requirements) if requirements is not None else None):
        fail("Prior review uses different independent requirements")
    template = load_json(run / "review-template.json")
    if template["model_revision"] != model["revision"]:
        fail("Assess current draft before preparing review reuse")
    judgments = {(x["target_id"], x["content_sha256"]): x for x in prior["judgments"] if x["verdict"] == "pass"}
    reused = []
    for item in template["judgments"]:
        earlier = judgments.get((item["target_id"], item["content_sha256"]))
        if earlier is not None:
            item.update(verdict="pass", reason=earlier["reason"])
            reused.append(item["target_id"])
    template["method"] = "Independently verify pending units; explicitly carry forward exact unchanged prior judgments"
    write(run / "reused-review.json", prior)
    write(run / "review-reuse-template.json", template, mutable=True)
    write(run / "review-reuse-provenance.json", {"prior_review_sha256": digest(prior),
          "prior_model_revision": prior["model_revision"], "prior_guide_sha256": prior["guide_sha256"],
          "reused_target_ids": reused}, mutable=True)
    return {"template": str(run / "review-reuse-template.json"), "reused": reused,
            "pending": len(template["judgments"]) - len(reused)}


def prepare(export_path: Path, output_dir: Path, requirements_path: Path | None = None,
            format_version: int = 1, root: Path | None = None, document_map: Path | None = None) -> dict:
    if format_version == 4:
        from . import organize_v4
        if root is None: fail("V4 prepare requires --root")
        return organize_v4.prepare(export_path, output_dir, requirements_path, root, document_map)
    if format_version == 3:
        from . import organize_v3
        if root is None: fail("V3 prepare requires --root")
        return organize_v3.prepare(export_path, output_dir, requirements_path, root, document_map)
    selected_guide = guide(format_version)
    source = load_json(export_path)
    _, _, packages = source_index(source)
    run = output_dir.resolve()
    requirements = load_json(requirements_path) if requirements_path is not None else None
    identity = {"source_sha256": digest(source), "source_file_sha256": file_hash(export_path), "producer": producer(format_version),
                "requirements_sha256": digest(requirements) if requirements is not None else None}
    if (run / "work-package.json").exists():
        package = load_json(run / "work-package.json")
        if package["inputs"] != identity or digest(load_json(run / "source.json")) != identity["source_sha256"] or file_hash(run / "MODELING.md") != file_hash(selected_guide):
            fail("Workspace inputs/guide changed; prepare a new directory")
        return {"run_dir": str(run), "reused": True, "input_fingerprint": digest(identity)}
    if run.exists() and any(run.iterdir()):
        fail("New workspace must be empty")
    draft = {"schema_version": f"semantic-discourse-draft/v{format_version}", "source_sha256": digest(source), "author": "unknown",
             "chapter": {"id": "chapter", "title": source["source"]["title"], "summary": []},
             "topics": [{"id": "topic:" + p["id"], "package_id": p["id"], "title": p["summary"],
                         "role": "supporting", "order": i, "summary": []} for i, p in enumerate(packages.values())],
             "blocks": [], "edges": []}
    if format_version == 2:
        draft["reading_structures"] = []
    write(run / "source.json", source)
    write(run / "draft.json", draft)
    write(run / "MODELING.md", selected_guide.read_text())
    if requirements is not None:
        write(run / "requirements.json", requirements)
    write(run / "work-package.json", {"schema_version": "semantic-discourse-work-package/v1", "inputs": identity,
          "input_fingerprint": digest(identity), "topic_ids": list(packages),
          "requirements": "Read source before organizing; actual independent review is mandatory. Membership is not inferred by the browser."})
    quoted = shlex.quote(str(run))
    write(run / "RUNBOOK.md", "# 篇章组织工作区\n\n编辑 draft.json；assess 生成候选和待审模板，不生成批准。独立审阅写 review.json。\n\n```sh\n"
          + f"skm organize prepare {shlex.quote(str(export_path.resolve()))} --output-dir {quoted}"
          + (f" --requirements {shlex.quote(str(requirements_path.resolve()))}" if requirements_path else "")
          + (" --format-version 2" if format_version == 2 else "") + "\n"
          + f"skm organize assess {quoted}\nskm organize publish {quoted}\n```\n\n发布后本文件更新确切重放与绘图命令。\n")
    return {"run_dir": str(run), "reused": False, "input_fingerprint": digest(identity)}


def assess(run: Path) -> dict:
    if load_json(run / "work-package.json")["inputs"]["producer"]["component"] == "organize/v4":
        from . import organize_v4
        return organize_v4.assess(run)
    if load_json(run / "work-package.json")["inputs"]["producer"]["component"] == "organize/v3":
        from . import organize_v3
        return organize_v3.assess(run)
    run = run.resolve()
    source = load_json(run / "source.json")
    package = load_json(run / "work-package.json")
    format_version = int(package["inputs"]["producer"]["component"].split("/v")[-1])
    if digest(source) != package["inputs"]["source_sha256"] or producer(format_version) != package["inputs"]["producer"] or file_hash(run / "MODELING.md") != file_hash(guide(format_version)):
        fail("Frozen source, guide or installed implementation changed")
    draft = load_json(run / "draft.json")
    if version_of(draft) != format_version:
        fail("Draft format differs from frozen work package")
    model = normalize(source, draft)
    requirements = load_json(run / "requirements.json") if (run / "requirements.json").exists() else None
    requirements_hash = digest(requirements) if requirements is not None else None
    if requirements_hash != package["inputs"]["requirements_sha256"]:
        fail("Frozen independent requirements changed")
    write(run / "candidates" / model["revision"] / "organization.json", model)
    write(run / "candidate.json", model, mutable=True)
    template = review_template(model)
    template["requirements_sha256"] = requirements_hash
    write(run / "review-template.json", template, mutable=True)
    issues = []
    review = load_json(run / "review.json") if (run / "review.json").exists() else None
    if review is None:
        issues.append({"kind": "review", "reason": "Independent review is required"})
    else:
        schema(review, "semantic-discourse-review-v1.schema.json")
        if review.get("requirements_sha256") != requirements_hash:
            issues.append({"kind": "dependency", "reason": "Review has stale independent requirements"})
        for key, expected in (("source_sha256", model["source_sha256"]), ("model_revision", model["revision"]), ("guide_sha256", model["guide_sha256"])):
            if review[key] != expected:
                issues.append({"kind": "dependency", "reason": f"Review has stale {key}"})
        if review["reviewer"] in ("unknown", model["author"]) or review["checked_at"] == "unknown":
            issues.append({"kind": "review", "reason": "Actual independent reviewer and time required"})
        entries = review["judgments"]
        if len({x["target_id"] for x in entries}) != len(entries):
            fail("Duplicate review target")
        judgments = {x["target_id"]: x for x in entries}
        units = review_units(model)
        if set(judgments) != {u["target_id"] for u in units}:
            issues.append({"kind": "coverage", "reason": "Review must cover exactly every boundary, summary, higher relation and reading structure"})
        for unit in units:
            item = judgments.get(unit["target_id"], {})
            if item.get("content_sha256") != unit["content_sha256"]:
                issues.append({"kind": "dependency", "target_id": unit["target_id"], "reason": "Reviewed content differs"})
            elif item.get("verdict") != "pass":
                issues.append({"kind": "semantic_review", "target_id": unit["target_id"], "reason": item.get("reason", "Not reviewed")})
    quality = {"schema_version": "semantic-discourse-quality/v1", "model_revision": model["revision"],
               "source_sha256": digest(source), "review_sha256": digest(review), "status": "pass" if not issues else "fail",
               "requirements_sha256": requirements_hash,
               "program_checks": {"source_references": "pass", "membership_coverage": "pass", "rollup_provenance": "pass"},
               "semantic_review": "pass" if not issues else "not_passed", "issues": issues,
               "limits": "Program checks do not prove natural-language faithfulness, readability, scientific validity or suitability."}
    write(run / "assessments" / (digest(quality) + ".json"), quality)
    write(run / "quality.json", quality, mutable=True)
    return quality


def derived(source: dict, model: dict) -> dict[str, Any]:
    format_version = version_of(model)
    elements, relations, packages = source_index(source)
    objects = {**elements, **relations}
    evidence, artifacts = {}, {}

    def add_evidence(key: str, title: str, body: str, excerpt: str = "", location: str = "") -> list[str]:
        ident = "evidence:" + key
        path = "evidence/" + digest(key)[:24] + ".md"
        evidence[ident] = {"path": path, "label": title, "excerpt": excerpt or body, "location": location}
        artifacts[path] = f"# {title}\n\n{body}\n"
        return [ident]

    object_evidence = {}
    for obj in [*elements.values(), *relations.values()]:
        anchors = obj.get("anchors", [])
        quotes = "\n\n".join(a["quote"] for a in anchors)
        locations = "、".join(dict.fromkeys(a["locator"] for a in anchors))
        text = obj.get("text", obj.get("note", ""))
        body = f"## 表述\n\n{text}\n\n身份：{obj['origin_type']}；修订：`{obj['content_revision']}`\n\n## 原文\n\n{quotes}\n\n出处：{locations}"
        if obj.get("quantity"):
            body += "\n\n## 量值及归属\n\n" + "\n".join(f"- {k}：{v}" for k, v in obj["quantity"].items())
        object_evidence[obj["id"]] = add_evidence("source:" + obj["id"], text[:90] or obj["id"], body, quotes, locations)

    def organization_evidence(item: dict) -> list[str]:
        refs = item.get("refs", item.get("premises", []))
        body = item.get("text", item.get("title", item.get("label", "")))
        for key in ("origin", "rationale", "uncertainty", "scope_note", "element_ids", "related_element_ids"):
            if item.get(key):
                body += f"\n\n{key}：{item[key]}"
        if refs:
            body += "\n\n## 对照依据\n\n" + "\n".join(f"- `{r['object_id']}` `{r['field_path']}` @ `{r['content_revision']}`" for r in refs)
        return add_evidence(item["id"], item.get("title", item.get("label", "摘要依据")), body)

    def summaries(node: dict) -> list[dict]:
        return [{"id": c["id"], "text": c["text"], "origin": c["origin"],
                 "evidence": organization_evidence(c) + list(dict.fromkeys(e for r in c["refs"] for e in object_evidence[r["object_id"]]))}
                for c in node["summary"]]

    ordered = list(dict.fromkeys(x["element_id"] for x in source["occurrences"]))
    ordered.extend(ident for ident in elements if ident not in ordered)
    rank = {ident: i for i, ident in enumerate(ordered)}
    root = model["chapter"]
    nodes = [{"id": root["id"], "label": root["title"], "kind": "chapter", "origin": "organization",
              "children": [t["id"] for t in model["topics"]], "summary": summaries(root), "evidence": organization_evidence(root)}]
    for topic in model["topics"]:
        nodes.append({"id": topic["id"], "label": topic["title"], "kind": "topic", "origin": "organization",
                      "order": topic["order"], "role": topic["role"], "summary": summaries(topic),
                      "children": [b["id"] for b in model["blocks"] if b["topic_id"] == topic["id"]],
                      "evidence": organization_evidence(topic)})
    for block in model["blocks"]:
        nodes.append({"id": block["id"], "label": block["title"], "kind": "block", "origin": "organization",
                      "children": ["element:" + x for x in sorted(block["element_ids"], key=lambda x: rank[x])],
                      "related_element_ids": ["element:" + x for x in block.get("related_element_ids", [])],
                      "order": block["order"], "summary": summaries(block), "evidence": organization_evidence(block)})
    for ident in ordered:
        obj = elements[ident]
        nodes.append({"id": "element:" + ident, "label": obj["text"], "kind": obj["kind"], "order": rank[ident],
                      "origin": "interpretation" if obj["origin_type"] == "project_interpretation" else "source",
                      "evidence": object_evidence[ident], "source_revision": obj["content_revision"],
                      "details": "；".join([obj["text"], "来源身份：" + obj["origin_type"], "语气：" + obj["modality"],
                                             "否定：" + str(obj["negated"]), "举例范围：" + str(obj.get("example_scope") or "不适用")])})
    edges = []
    for obj in relations.values():
        edges.append({"id": "relation:" + obj["id"], "label": LABELS.get(obj["type"], obj["type"]),
                      "from": "element:" + obj["from_id"], "to": "element:" + obj["to_id"],
                      "origin": "interpretation" if obj["origin_type"] == "project_interpretation" else "source",
                      "direction": "symmetric" if obj["type"] == "contrasts_with" else "directed",
                      "details": obj.get("note", ""), "basis": obj["basis"], "rationale": obj["rationale"],
                      "uncertainty": obj["uncertainty"],
                      "premises": [{"object_id": p, "content_revision": objects[p]["content_revision"],
                                    "field_path": "/text" if p in elements else "/note"}
                                   for p in obj["premises"]], "evidence": object_evidence[obj["id"]]})
    for obj in model["edges"]:
        edges.append({"id": obj["id"], "label": obj["label"], "from": obj["from_id"], "to": obj["to_id"],
                      "direction": obj["direction"], "origin": obj["origin"], "details": obj["rationale"],
                      "rationale": obj["rationale"], "premises": obj["premises"],
                      "scope_note": obj["scope_note"], "uncertainty": obj["uncertainty"],
                      "supporting_edge_ids": ["relation:" + r for r in obj.get("source_relation_ids", [])],
                      "evidence": organization_evidence(obj) + list(dict.fromkeys(e for r in obj["premises"] for e in object_evidence[r["object_id"]]))})
    domain = {"schema_version": f"diagram-domain-model/v{format_version + 1}", "title": root["title"],
                                    "root_id": root["id"], "nodes": nodes, "edges": edges,
                                    "reading_order": ["element:" + x for x in ordered]}
    if format_version == 2:
        domain["reading_structures"] = []
        for item in model["reading_structures"]:
            convert = lambda ident: "element:" + ident if ident in elements else ident
            domain["reading_structures"].append({
                "id": item["id"], "scope_id": item["scope_id"], "pattern": item["pattern"],
                "core_ids": [convert(x) for x in item["core_ids"]],
                "supports": [{**support, "node_id": convert(support["node_id"]),
                              "anchor_id": convert(support["anchor_id"])} for support in item["supports"]],
                "independent_ids": [convert(x) for x in item["independent_ids"]],
                "reading_order": [convert(x) for x in item["reading_order"]],
                "rationale": item["rationale"],
                "evidence": organization_evidence(item) + list(dict.fromkeys(
                    e for ref in item["refs"] for e in object_evidence[ref["object_id"]]))})
    artifacts.update({"model.json": domain,
                      "evidence.json": {"sources": evidence},
                      "diagram.config.json": {"schema": 1, "target": "domain-model", "model": "model.json", "evidence": "evidence.json",
                                              "review": "diagram-review.json", "output": ".diagrams", "inputs": ["organization.json", "quality.json", "review.json"], "watch": []}})
    return artifacts


def publish(run: Path) -> dict:
    if load_json(run / "work-package.json")["inputs"]["producer"]["component"] == "organize/v4":
        from . import organize_v4
        return organize_v4.publish(run)
    if load_json(run / "work-package.json")["inputs"]["producer"]["component"] == "organize/v3":
        from . import organize_v3
        return organize_v3.publish(run)
    run = run.resolve()
    quality = assess(run)
    if quality["status"] != "pass":
        fail("Independent semantic assessment has not passed; publication refused")
    model, source, review = (load_json(run / name) for name in ("candidate.json", "source.json", "review.json"))
    format_version = version_of(model)
    payload = {"organization.json": model, "source.json": source, "draft.json": load_json(run / "draft.json"),
               "review.json": review, "quality.json": quality, "MODELING.md": guide(format_version).read_text()}
    if (run / "requirements.json").exists():
        payload["requirements.json"] = load_json(run / "requirements.json")
    if (run / "reused-review.json").exists():
        payload["reused-review.json"] = load_json(run / "reused-review.json")
        payload["review-reuse-provenance.json"] = load_json(run / "review-reuse-provenance.json")
    revision = digest({"model": model["revision"], "review": review, "quality": quality, "producer": producer(format_version)})
    out = run / "releases" / revision
    existed = out.exists()
    payload.update(derived(source, model))
    for name, value in payload.items():
        write(out / name, value)
    manifest = {"schema_version": RUN, "revision": revision, "producer": producer(format_version),
                "source_sha256": digest(source), "artifacts": {name: file_hash(out / name) for name in sorted(payload)}}
    write(out / "manifest.json", manifest)
    qrun, qout = shlex.quote(str(run)), shlex.quote(str(out))
    write(run / "RUNBOOK.md", "# 篇章组织确切命令\n\n编辑源草稿后重新独立核对，不直接编辑发布目录。\n\n```sh\n"
          + f"skm organize assess {qrun}\nskm organize publish {qrun}\n"
          + f"skm organize replay {shlex.quote(str(out / 'manifest.json'))} --output-dir {shlex.quote(str(run.parent / (run.name + '-replay')))}\n"
          + f"diagram-workbench build --project {qout}\ndiagram-workbench check --project {qout}\ndiagram-workbench preview --project {qout}\n```\n"
          + "\n首次绘图构建前，需要真实检查者通过 diagram-workbench review 记录输入与证据检查。独立语义审阅不会自动产生绘图审阅；实际执行命令另存运行记录。\n", mutable=True)
    return {"release": str(out), "manifest": str(out / "manifest.json"), "revision": revision, "reused": existed}


def replay(manifest_path: Path, output_dir: Path) -> dict:
    if load_json(manifest_path)["producer"]["component"] == "organize/v4":
        from . import organize_v4
        return organize_v4.replay(manifest_path, output_dir)
    if load_json(manifest_path)["producer"]["component"] == "organize/v3":
        from . import organize_v3
        return organize_v3.replay(manifest_path, output_dir)
    manifest = load_json(manifest_path)
    format_version = int(manifest["producer"]["component"].split("/v")[-1])
    if manifest.get("schema_version") != RUN or manifest["producer"] != producer(format_version):
        fail("Replay requires the exact organization implementation and guide")
    parent = manifest_path.resolve().parent
    for name, expected in manifest["artifacts"].items():
        p = (parent / name).resolve()
        if not p.is_relative_to(parent) or not p.is_file() or file_hash(p) != expected:
            fail(f"Replay input missing, changed or escaped: {name}")
    prepare(parent / "source.json", output_dir, parent / "requirements.json" if "requirements.json" in manifest["artifacts"] else None,
            format_version=format_version)
    for name in ("reused-review.json", "review-reuse-provenance.json"):
        if name in manifest["artifacts"]:
            write(output_dir / name, load_json(parent / name))
    for name in ("draft.json", "review.json"):
        target = output_dir / name
        if name == "draft.json" and target.exists() and not load_json(target).get("blocks"):
            write(target, load_json(parent / name), mutable=True)
        else:
            write(target, load_json(parent / name))
    result = publish(output_dir)
    actual = load_json(Path(result["manifest"]))
    if actual != manifest:
        fail("Rebuilt release differs from frozen manifest")
    return {**result, "replayed_artifacts": len(manifest["artifacts"]), "new_review_events": 0}
