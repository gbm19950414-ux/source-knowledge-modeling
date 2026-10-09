"""Portable, declarative semantic workspaces, quality assessment and frozen replay.

No function here invents source meanings or reviewer decisions. Authoring inputs
are data; semantic judgments must be supplied by an identified actual reviewer.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import json
import re
from pathlib import Path
import shlex
from typing import Any

from jsonschema import Draft202012Validator

from .core import KnowledgeFoundationError, load_json, validate
from .semantic import profile_manifest
from . import semantic_v3 as v3

MANIFEST_VERSION = "knowledge-semantic-run/v1"
WORK_VERSION = "knowledge-semantic-work-package/v2"
QUALITY_VERSION = "knowledge-semantic-quality/v1"
INFORMATION_VERSION = "knowledge-source-information/v1"
DISCOURSE_VERSION = "knowledge-discourse-audit/v1"
ASSESSMENT_VERSION = "knowledge-semantic-assessment/v1"
EXPECTATIONS_VERSION = "knowledge-semantic-expectations/v1"
RESOURCES = Path(__file__).resolve().parent


def schema_errors(value: Any, name: str) -> list[str]:
    schema = load_json(RESOURCES / "schemas" / name)
    return [f"{list(error.path)}: {error.message}" for error in Draft202012Validator(schema).iter_errors(value)]


def check_schema(value: Any, name: str) -> None:
    errors = schema_errors(value, name)
    if errors:
        raise KnowledgeFoundationError(f"{name}: {errors[0]}")


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def write_immutable(path: Path, value: Any) -> bool:
    content = value if isinstance(value, bytes) else value.encode() if isinstance(value, str) else json_bytes(value)
    if path.exists():
        if path.read_bytes() == content:
            return True
        raise KnowledgeFoundationError(f"Output already has different content: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(content)
    return False


def _replace_index(path: Path, value: Any) -> None:
    """Only the run index is mutable; every recorded result stays immutable."""
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(json_bytes(value))
    temporary.replace(path)


def guide_hash() -> str:
    return v3.digest({p.name: file_hash(p) for p in sorted((RESOURCES / "templates").glob("semantic-*-v3.md"))})


def resolve_profile(value: str) -> dict[str, str]:
    path = Path(value)
    if path.is_file():
        config = load_json(path)
        bundled = RESOURCES / "profiles" / f"{config['id']}-{config['version']}.json"
        if not bundled.is_file() or path.read_bytes() != bundled.read_bytes():
            raise KnowledgeFoundationError("Profile differs from the installed versioned definition.")
        return profile_manifest(config["id"], config["version"])
    if "@" not in value:
        raise KnowledgeFoundationError("Profile must be a bundled id@version or its exact JSON file.")
    ident, revision = value.rsplit("@", 1)
    return profile_manifest(ident, revision)


def _tool_identity() -> dict[str, str]:
    try:
        release = version("source-knowledge-modeling")
    except PackageNotFoundError:
        release = "uninstalled"
    # Hash shipped source/resources too: editable changes must not masquerade as
    # the same producer merely because the package version has not been bumped.
    files = sorted(p for p in RESOURCES.rglob("*") if p.is_file() and p.suffix in (".py", ".json", ".md", ".html"))
    return {"name": "source-knowledge-modeling", "version": release,
            "implementation_sha256": v3.digest({str(p.relative_to(RESOURCES)): file_hash(p) for p in files})}


def _path_ref(path: Path, run_dir: Path, root: Path) -> dict[str, str]:
    path = path.resolve()
    for kind, parent in (("run", run_dir.resolve()), ("root", root.resolve())):
        try:
            return {"location": kind, "path": str(path.relative_to(parent))}
        except ValueError:
            pass
    raise KnowledgeFoundationError(f"Run input/output must be inside source root or run directory: {path}")


def _resolve_ref(ref: dict[str, str], run_dir: Path, root: Path) -> Path:
    if ref.get("location") not in ("run", "root"):
        raise KnowledgeFoundationError("Unknown run path location.")
    parent = (run_dir if ref["location"] == "run" else root).resolve()
    path = (parent / ref["path"]).resolve()
    if not path.is_relative_to(parent):
        raise KnowledgeFoundationError("Run path escapes its declared directory.")
    return path


def prepare_workspace(foundation_path: Path, *, root: Path, profile: dict[str, str], scope_path: Path,
                      output_dir: Path, existing_path: Path | None = None,
                      executor: str = "unknown", model: str = "unknown") -> dict[str, Any]:
    root, output_dir = root.resolve(), output_dir.resolve()
    foundation = load_json(foundation_path)
    validate(foundation, root=root, resolve=True)
    if profile["version"] != "3.0.0":
        raise KnowledgeFoundationError("Directory workspaces require instructional-source@3.0.0.")
    scope = load_json(scope_path)
    existing = load_json(existing_path) if existing_path else None
    tool = _tool_identity()
    inputs = {"foundation_sha256": file_hash(foundation_path), "scope_sha256": file_hash(scope_path),
              "existing_sha256": file_hash(existing_path) if existing_path else None,
              "profile_sha256": profile["definition_sha256"], "guide_sha256": guide_hash(),
              "tool_sha256": tool["implementation_sha256"],
              "source_sha256": foundation["source"]["sha256"], "snapshot_sha256": foundation["source"]["snapshot_sha256"]}
    fingerprint = v3.digest(inputs)
    index = output_dir / "run-manifest.json"
    if index.exists():
        prior = load_json(index)
        check_schema(prior, "knowledge-semantic-run-v1.schema.json")
        if prior.get("input_fingerprint") != fingerprint or prior.get("inputs") != inputs:
            raise KnowledgeFoundationError("Workspace inputs changed; prepare a new directory.")
        for name, ref in prior["prepare_inputs"].items():
            path = _resolve_ref(ref, output_dir, root)
            if not path.is_file() or file_hash(path) != ref["sha256"]:
                raise KnowledgeFoundationError(f"Workspace frozen input changed: {name}")
        work_package = load_json(output_dir / "work-package.json")
        check_schema(work_package, "knowledge-semantic-work-package-v2.schema.json")
        if work_package["input_fingerprint"] != fingerprint or work_package["inputs"] != inputs:
            raise KnowledgeFoundationError("Workspace source package changed; prepare a new directory.")
        v3.validate_projection(load_json(output_dir / "base.json"), root=root, resolve=True)
        # Rebuild mechanical instructions even after a partial prepare failure.
        # Authoring templates stay untouched; immutable derived inputs may fail
        # safely if the existing checked candidate is no longer consistent.
        write_runbook(output_dir, root)
        # Never restore templates over the author's completed inputs on resume.
        return {"output_dir": str(output_dir), "input_fingerprint": fingerprint, "reused": True}
    base = v3.new_projection(foundation, foundation_path.read_bytes(), scope, profile, root=root)
    if existing is not None:
        if existing.get("schema_version") == "knowledge-semantic-projection/v2":
            base = v3.migrate_v2(existing, existing_path.read_bytes(), root=root, foundation=foundation, revision_id="v3-migrated-base")
            if set(base["scope"]["included_locators"]) != set(scope["included_locators"]):
                raise KnowledgeFoundationError("Migration scope differs; select the original scope or start a fresh workspace.")
        elif existing.get("schema_version") == v3.VERSION:
            v3.validate_projection(existing, root=root, resolve=True)
            if existing["source"] != base["source"] or existing["scope"] != base["scope"]:
                raise KnowledgeFoundationError("Existing projection source/scope differs; explicitly align before reuse.")
            base = deepcopy(existing)
        else:
            raise KnowledgeFoundationError("Directory workspace existing input must be v2 or v3.")
    snapshot = load_json(root / foundation["source"]["snapshot_path"])
    texts = {u["locator"]: u for u in snapshot["units"]}
    selected = set(scope["included_locators"] + scope.get("context_locators", []))
    source_units = [{**deepcopy(texts[u["locator"]]), "order": u["order"],
                     "scope_role": "primary" if u["locator"] in scope["included_locators"] else "context"}
                    for u in sorted(foundation["units"], key=lambda x: x["order"]) if u["locator"] in selected]
    package = {"schema_version": WORK_VERSION, "artifact_id": base["artifact_id"], "input_fingerprint": fingerprint,
               "inputs": inputs, "profile": profile, "scope": scope, "source_units": source_units,
               "steps": {name: "pending" for name in ("source_information", "discourse", "draft", "source_check", "independent_assessment", "review", "publish", "derive")}}
    paths = {"foundation": foundation_path, "scope": scope_path}
    if existing_path:
        paths["existing"] = existing_path
    manifest = {"schema_version": MANIFEST_VERSION, "input_fingerprint": fingerprint, "inputs": inputs,
                "tool": tool, "profile": profile, "prepare_inputs": {}, "records": [],
                "execution": {"executor": executor.strip() or "unknown", "model": model.strip() or "unknown",
                              "interventions": [], "retries": []}}
    check_schema(package, "knowledge-semantic-work-package-v2.schema.json")
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, path in paths.items():
        # Capture caller configuration as data so relocating a run is sufficient.
        copied = output_dir / "inputs" / f"{name}.json"
        write_immutable(copied, path.read_bytes())
        manifest["prepare_inputs"][name] = {**_path_ref(copied, output_dir, root), "sha256": file_hash(copied)}
    check_schema(manifest, "knowledge-semantic-run-v1.schema.json")
    write_immutable(output_dir / "base.json", base)
    write_immutable(output_dir / "work-package.json", package)
    # This catalog identifies frozen native regions, not inspected meanings.
    # Authors retain unresolved until an actual original-page inspection.
    write_immutable(output_dir / "visual-regions.json", [
        {"locator": u["locator"], "bbox": u["bbox"], "native_kind": u["kind"],
         "native_structure_sha256": v3.digest(u["raw_content"]),
         "interpretation_status": "unresolved", "note": "Not yet visually interpreted."}
        for u in source_units if u.get("kind") in ("image", "table", "chart")])
    identity = {"source_sha256": base["source"]["sha256"], "snapshot_sha256": base["source"]["snapshot_sha256"]}
    write_immutable(output_dir / "source-information.json", {"schema_version": INFORMATION_VERSION, **identity, "reviewer": "", "checked_at": "", "items": []})
    write_immutable(output_dir / "discourse-audit.json", {"schema_version": DISCOURSE_VERSION, **identity, "reviewer": "", "checked_at": "", "items": []})
    write_immutable(output_dir / "drafts.json", {"schema_version": v3.DRAFT_VERSION, "artifact_id": base["artifact_id"], "base_revision_id": base["revision"]["id"], "proposals": []})
    write_immutable(output_dir / "semantic-assessment.json", {"schema_version": ASSESSMENT_VERSION, "draft_sha256": "", "inventory_sha256": "", "discourse_sha256": "", "reviewer": "", "reviewer_type": "AI", "model": "unknown", "check_method": "original_visual" if base["source"]["kind"] == "pdf" else "source_text", "checked_at": "", "checks": []})
    for name in ("modeling", "review"):
        write_immutable(output_dir / f"{name.upper()}.md", (RESOURCES / "templates" / f"semantic-{name}-v3.md").read_text())
    write_immutable(index, manifest)
    write_runbook(output_dir, root)
    return {"output_dir": str(output_dir), "input_fingerprint": fingerprint, "reused": False}


def write_runbook(run_dir: Path, root: Path) -> None:
    q = lambda name: shlex.quote(str((run_dir / name).resolve()))
    ref_path = lambda ref: _resolve_ref(ref, run_dir, root)
    ref_arg = lambda ref: shlex.quote(str(ref_path(ref)))
    source = shlex.quote(str(root.resolve()))
    run = shlex.quote(str(run_dir.resolve()))
    manifest = load_json(run_dir / "run-manifest.json")
    assessment_record = next((x for x in reversed(manifest["records"]) if x["command"] == "assess"), None)
    expectation_flags = (" --expectations " + ref_arg(assessment_record["inputs"]["expectations"])
                         if assessment_record and "expectations" in assessment_record["inputs"] else "")
    report = ref_path(assessment_record["output"]) if assessment_record else run_dir / "quality-report.json"
    report_arg = shlex.quote(str(report.resolve()))
    scope = load_json(run_dir / "work-package.json")["scope"]
    schema_path = RESOURCES / "schemas" / "knowledge-semantic-drafts-v3.schema.json"
    schema = load_json(schema_path)
    lines = ["# 本工作区运行说明", "",
             "主范围 included_locators 必须逐项覆盖；context_locators 只提供明确指定的证据背景，不计作主范围覆盖。未声明的来源单元不自动加入范围。",
             "依赖与复核按完整主范围及上下文保守处理；本入口不计算细粒度依赖。", "", "```json",
             json.dumps(scope, ensure_ascii=False, indent=2), "```", "",
             "1. 按 MODELING.md 阅读原件及 work-package.json，实际作者填写 source-information.json、discourse-audit.json 和 drafts.json。空模板不是已完成判断。",
             "2. 先执行 assess --inputs-only，valid 仅是输入合同有效，不是语义 pass；失败先按条目修正实际输入。再执行下面 draft-import；checked-drafts.json 是规范化提案，candidate.json 是待审语义投影，不能把 candidate.json 用作 --drafts。",
             "3. 导入后，独立检查者按 REVIEW.md 检查 candidate.json、review-targets.json 和原件，填写 semantic-assessment.json；身份绑定见 review-input.json。",
             "4. 填好独立评估后再执行同一 assess --inputs-only 核对身份与 raw/canonical hash，避免到正式质量评估才发现合同错误；不重复语义判断。完成实际独立核验后执行完整 assess。来源检查、逐项 review 与 publish 命令只在所需真实审计信息齐备时追加到本 RUNBOOK。", "",
             "合同索引（来自当前安装的 canonical schema；不替代 normalize_drafts 或验证器）：",
             f"- 草稿/提案 schema：[{schema_path.name}]({schema_path})",
             f"- 投影 schema：[knowledge-semantic-projection-v3.schema.json]({RESOURCES / 'schemas' / 'knowledge-semantic-projection-v3.schema.json'})",
             f"- 关系端点 kind 的完整规则：[semantic_v3.py validate_projection]({RESOURCES / 'semantic_v3.py'})，包括 source-kind 和 target-kind 检查。",
             "- 规范化后的必填字段如下；作者输入可以使用 normalize_drafts 已支持的默认值/anchor 简写，不能据此省略未提供默认值的字段。"]
    for kind in ("element", "relation", "package"):
        lines.append(f"  - {kind}: " + ", ".join(f"`{name}`" for name in schema["$defs"][kind]["required"]))
    lines += ["- package 的 `legacy_review` 是必填可空字段；没有历史审阅时显式填写 `null`，不生成历史判断。",
              "- 所有最终 element 和 relation 必须至少属于一个 package，分别在 element_ids / relation_ids 中引用；context_ids 不能代替包成员。包成员必须存在；import 会列出遗漏或未知 ID。按原文组织含义归包，不为通过结构检查改写来源或凭空添加关系。",
              "- 逐项/论述审计只列 primary。投影审计沿用来源清单同 ID 的 meaning 与精确证据；not_stated 还须保留实际检查的 checked_scope。context 含义保留在来源笔记及有出处的上下文对象。选中的 direct-text 真空白行由 frozen snapshot 核验后记录 structural-blank；不要编造非空quote。此规则不豁免文字或视觉内容。",
              "- 关系 source.kind 映射（直接读取 canonical `_RELATION_SOURCE_KINDS`）：" +
              "; ".join(f"`{name}` → `{kind}`" for name, kind in sorted(v3._RELATION_SOURCE_KINDS.items())),
              "", "```sh",
             f"skm assess {run} --root {source} --inputs-only --output {q('input-check-before-review.json')}",
             f"skm draft-import {q('base.json')} --root {source} --drafts {q('drafts.json')} --output {q('checked-drafts.json')} --run-dir {run}",
             "# 独立检查者实际完成语义判断并填好 semantic-assessment.json 后：",
             f"skm assess {run} --root {source} --inputs-only --output {q('input-check-after-review.json')}",
             f"skm assess {run} --root {source}{expectation_flags} --output {report_arg}",
             "```", "", "导入成功后，candidate.json、review-targets.json、anchors.json、regions.json 和 review-input.json 由工具生成；候选与待审目标不含接受判断，供独立核验者逐项检查。"]
    draft_path = run_dir / "checked-drafts.json"
    if draft_path.exists():
        drafts = load_json(draft_path)
        base = load_json(run_dir / "base.json")
        anchors = list({v3.anchor_key(a): a for p in drafts["proposals"] for a in v3._proposal_anchors(base, p)}.values())
        regions = list({v3.region_key(r): r for p in drafts["proposals"] for r in v3._proposal_regions(base, p)}.values())
        write_immutable(run_dir / "anchors.json", anchors)
        write_immutable(run_dir / "regions.json", regions)
        write_immutable(run_dir / "review-input.json", {"draft_sha256": v3.digest(drafts), "inventory_sha256": file_hash(run_dir / "source-information.json"), "discourse_sha256": file_hash(run_dir / "discourse-audit.json"), "note": "Copy these exact identities only after reviewing this candidate. draft_sha256 is canonical normalized JSON digest; inventory_sha256/discourse_sha256 are raw file SHA-256; guide identity is digest of template filename→raw SHA-256 mapping."})
        method = "original_visual" if base["source"]["kind"] == "pdf" else "source_text"
        source_actor = load_json(run_dir / "source-information.json").get("reviewer", "").strip()
        assessment = load_json(run_dir / "semantic-assessment.json")
        semantic_actor = assessment.get("reviewer", "").strip()
        source_records = [x for x in manifest["records"] if x["command"] == "source-check"]
        source_ledger_arg = ref_arg(source_records[-1]["output"]) if source_records else q("source-checks.json")
        if source_records:
            lines += ["", "以下命令复用已记录的实际来源检查者、检查范围与结果，不产生新的核验事件。", "", "```sh"]
            for record in source_records:
                ledger = load_json(ref_path(record["output"]))
                checked = next(x for x in ledger["checks"] if x["id"] == record["arguments"]["check_id"])
                region_flag = " --regions " + ref_arg(record["inputs"]["regions"]) if "regions" in record["inputs"] else ""
                output_arg = ref_arg(record["output"])
                lines.append(f"skm source-check {ref_arg(record['inputs']['artifact'])} --root {source} --ledger {output_arg} --anchors {ref_arg(record['inputs']['anchors'])}{region_flag} --check-id {shlex.quote(checked['id'])} --reviewer {shlex.quote(checked['reviewer'])} --check-method {checked['check_method']} --note {shlex.quote(checked['note'])} --output {output_arg} --run-dir {run}")
            lines += ["```"]
        elif source_actor:
            lines += ["", "来源核验命令采用已填写来源审计的检查者身份；实际执行者仍须检查原件和所有列出的证据。", "", "```sh",
                      f"skm source-check {q('base.json')} --root {source} --ledger {q('source-checks.json')} --anchors {q('anchors.json')} --regions {q('regions.json')} --check-id source-check-1 --reviewer {shlex.quote(source_actor)} --check-method {method} --note 'Original source spans checked; not scientific validation' --output {q('source-checks.json')} --run-dir {run}", "```"]
        else:
            lines += ["", "填写来源审计的实际检查者后，再生成来源核验命令。"]
        if report.exists() and load_json(report).get("status") == "pass" and semantic_actor:
            lines += ["", "以下接受命令采用已填写独立语义核对的检查者身份；逐项人工核对后执行。", "", "```sh"]
            for p in drafts["proposals"]:
                keys = sorted({v3.anchor_key(a) for a in v3._proposal_anchors(base, p)})
                rkeys = sorted({v3.region_key(r) for r in v3._proposal_regions(base, p)})
                flags = " ".join(f"--checked-anchor {key}" for key in keys) + " " + " ".join(f"--checked-region {key}" for key in rkeys)
                out = f"reviews/{p['id']}.json"
                previous = "review-ledger.json" if p is drafts["proposals"][0] else f"reviews/{drafts['proposals'][drafts['proposals'].index(p)-1]['id']}.json"
                lines.append(f"skm review {q('base.json')} --root {source} --drafts {q('checked-drafts.json')} --source-checks {source_ledger_arg} --ledger {q(previous)} --proposal-id {shlex.quote(p['id'])} --decision accept --reviewer {shlex.quote(semantic_actor)} --check-method {method} {flags.strip()} --assessment {report_arg} --output {q(out)} --run-dir {run}")
            last = f"reviews/{drafts['proposals'][-1]['id']}.json"
            tool = load_json(run_dir / "run-manifest.json")["tool"]
            producer = f"{tool['version']}+{tool['implementation_sha256'][:12]}"
            lines += [f"skm publish {q('base.json')} --root {source} --drafts {q('checked-drafts.json')} --source-checks {source_ledger_arg} --ledger {q(last)} --assessment {report_arg} --revision-id semantic-v3-reviewed --output {q('published.json')} --run-dir {run}",
                  f"skm query {q('published.json')} --root {source} --layer all --output {q('query.json')} --run-dir {run}",
                  f"skm render {q('published.json')} --root {source} --output {q('reading.md')} --run-dir {run}",
                  f"skm render {q('published.json')} --root {source} --format html --output {q('reading.html')} --run-dir {run}",
                  f"skm export {q('published.json')} --root {source} --producer-commit {shlex.quote(producer)} --output {q('export.json')} --run-dir {run}",
                  f"skm replay {q('run-manifest.json')} --root {source} --output-dir {shlex.quote(str(run_dir.resolve()) + '-replay')}", "```"]
        else:
            lines += ["", "独立语义核对及质量评测通过后，本说明会列出接受、发布与派生命令。"]
    (run_dir / "RUNBOOK.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def record_step(run_dir: Path, root: Path, command: str, arguments: dict[str, Any], *, inputs: dict[str, Path], output: Path) -> None:
    manifest_path = run_dir / "run-manifest.json"
    manifest = load_json(manifest_path)
    record = {"command": command, "arguments": arguments,
              "inputs": {key: {**_path_ref(path, run_dir, root), "sha256": file_hash(path)} for key, path in inputs.items() if path.is_file()},
              "output": {**_path_ref(output, run_dir, root), "sha256": file_hash(output)}}
    prior = next((x for x in manifest["records"] if x["output"]["location"] == record["output"]["location"] and x["output"]["path"] == record["output"]["path"]), None)
    if prior is not None:
        # Retry may now read its own prior output as the ledger. The event bytes
        # are authoritative; retain the original recorded transaction.
        if prior["output"]["sha256"] != record["output"]["sha256"] or prior["command"] != command:
            raise KnowledgeFoundationError("A recorded output changed; use a new output path.")
        manifest["execution"]["retries"].append({"command": command, "output": record["output"]["path"],
                                                 "reason": "identical output reused"})
        _replace_index(manifest_path, manifest)
        return
    # Authoring and assessment templates can be filled between recorded steps.
    # Keep the exact input bytes for each transaction without changing the
    # user-facing paths used by RUNBOOK or the public commands.
    snapshots = {}
    for name, path in inputs.items():
        if not path.is_file() or not path.resolve().is_relative_to(run_dir.resolve()):
            continue
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            raise KnowledgeFoundationError(f"Invalid recorded input name: {name}")
        frozen = run_dir / "input-snapshots" / f"{len(manifest['records']):04d}" / f"{name}.bin"
        write_immutable(frozen, path.read_bytes())
        snapshots[name] = {**_path_ref(frozen, run_dir, root), "sha256": file_hash(frozen)}
    record["input_snapshots"] = snapshots
    if command == "draft-import":
        base = load_json(inputs["artifact"])
        normalized = load_json(output)
        candidate = v3.candidate_preview(base, normalized, root=root)
        targets = []
        for kind, plural in (("information", "information"), ("discourse", "discourse"),
                             ("element", "elements"), ("relation", "relations")):
            for item in candidate[plural]:
                target = {"target_kind": kind, "target_id": item["id"], "status": "pending"}
                if kind in ("element", "relation"):
                    target["content_revision"] = item["content_revision"]
                targets.append(target)
        derived = {"candidate": (run_dir / "candidate.json", candidate),
                   "review_targets": (run_dir / "review-targets.json", {
                       "schema_version": "knowledge-semantic-review-targets/v1",
                       "base_revision_id": base["revision"]["id"],
                       "draft_sha256": v3.digest(normalized), "targets": targets,
                       "note": "Pending targets are not semantic approval."})}
        record["derived"] = {}
        for name, (path, value) in derived.items():
            write_immutable(path, value)
            record["derived"][name] = {**_path_ref(path, run_dir, root), "sha256": file_hash(path)}
    manifest["records"].append(record)
    check_schema(manifest, "knowledge-semantic-run-v1.schema.json")
    _replace_index(manifest_path, manifest)
    if command in ("draft-import", "assess", "source-check"):
        write_runbook(run_dir, root)


def _get_path(value: Any, path: str) -> Any:
    if not path.startswith("/"):
        raise KnowledgeFoundationError(f"Expected JSON Pointer: {path}")
    try:
        for part in path[1:].split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
        return value
    except (KeyError, IndexError, ValueError, TypeError):
        return None


def _matches(item: dict[str, Any], selector: dict[str, Any], elements: dict[str, Any]) -> bool:
    for key, expected in selector.items():
        if key in ("from", "to"):
            if not _matches(elements.get(item.get(key + "_id"), {}), expected, elements):
                return False
        elif key == "anchor_contains":
            if not any(expected in a.get("quote", "") for a in item.get("anchors", [])):
                return False
        elif key == "locator":
            if not any(a.get("locator") == expected for a in item.get("anchors", [])) and item.get("locator") != expected:
                return False
        elif key == "text_contains":
            if expected not in item.get("text", item.get("meaning", "")):
                return False
        elif item.get(key) != expected:
            return False
    return True


def expectation_checks(candidate: dict[str, Any], expectations: dict[str, Any],
                       source_audits: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    check_schema(expectations, "knowledge-semantic-expectations-v1.schema.json")
    if expectations.get("schema_version") != EXPECTATIONS_VERSION:
        raise KnowledgeFoundationError("Unknown expectation format.")
    if expectations.get("source_sha256") not in (None, candidate["source"]["sha256"]):
        raise KnowledgeFoundationError("Expectation source identity mismatch.")
    elements = {x["id"]: x for x in candidate["elements"]}
    checks = []
    seen = set()
    for case in expectations.get("cases", []):
        if not case.get("id") or case["id"] in seen or case.get("collection") not in ("elements", "relations", "information", "discourse"):
            raise KnowledgeFoundationError("Expectation cases need unique IDs and a supported collection.")
        seen.add(case["id"])
        collection = case["collection"]
        audit_items = {x["id"]: x for x in source_audits.get(collection, {}).get("items", [])} if source_audits else {}
        found = []
        for item in candidate[collection]:
            audited = audit_items.get(item["id"])
            # A modeled meaning can span source units while its primary audit
            # location remains the first unit. Match all independently recorded
            # source anchors, but only when the audited meaning is identical.
            located = {**item, "anchors": audited["anchors"]} if audited and audited.get("meaning") == item.get("meaning") else item
            if _matches(located, case.get("where", {}), elements):
                found.append(item)
        valid = len(found) >= case.get("min_count", 1) and (case.get("max_count") is None or len(found) <= case["max_count"])
        def matches_requirement(requirement: dict[str, Any], item: dict[str, Any]) -> bool:
            value = _get_path(item, requirement["path"])
            return (
                ("equals" not in requirement or value == requirement["equals"])
                and ("contains" not in requirement or isinstance(value, (str, list)) and requirement["contains"] in value)
                and ("contains_any" not in requirement or isinstance(value, str) and any(t in value for t in requirement["contains_any"]))
                and ("not_equals" not in requirement or value != requirement["not_equals"])
            )
        any_requirements = []
        for requirement in case.get("require", []):
            if requirement.get("match") != "any":
                valid = valid and bool(found) and all(matches_requirement(requirement, item) for item in found)
            else:
                any_requirements.append(requirement)
        if any_requirements:
            # A single model location must satisfy a compound expectation;
            # separate partial matches cannot jointly masquerade as one claim.
            valid = valid and any(all(matches_requirement(req, item) for req in any_requirements) for item in found)
        checks.append({"id": "expectation:" + case["id"], "category": "frozen_expectation", "status": "pass" if valid else "fail", "detail": case.get("meaning", case["id"]), "matched_ids": [x["id"] for x in found]})
    if not seen:
        raise KnowledgeFoundationError("An expectation file must contain actual cases.")
    return checks


def structural_blank_locators(base: dict[str, Any], *, root: Path) -> set[str]:
    """Only verified empty direct-text lines have no possible semantic span.

    A blank label alone cannot exempt substantive text or native visual content.
    """
    if base["source"]["kind"] != "text" or base["source"]["transcription_method"] != "direct-text 1; local":
        return set()
    snapshot_units = v3._source_units(base, root, verify=True)
    return {unit["locator"] for unit in base["source_units"]
            if unit["scope_role"] == "primary"
            and (native := snapshot_units[unit["locator"]])["kind"] == "blank"
            and native.get("text") == ""
            and native.get("bbox") is None and native.get("visual_ref") is None
            and native.get("raw_content") is None}


def assess_workspace(run_dir: Path, *, root: Path, expectations_path: Path | None = None, inputs_only: bool = False) -> dict[str, Any]:
    if inputs_only and expectations_path is not None:
        raise KnowledgeFoundationError("Input checks do not assess frozen semantic expectations; use full assess.")
    reuse_context = None
    if not inputs_only and reuse_requested(run_dir):
        from .semantic_dependency_cert import verify_assessment_reuse
        reuse_context = verify_assessment_reuse(run_dir, load_json(run_dir / "reuse-provenance.json"), root=root)
    base = load_json(run_dir / "base.json")
    if inputs_only:
        try:
            drafts = v3.normalize_drafts(base, load_json(run_dir / "drafts.json"), root=root)
        except KnowledgeFoundationError as exc:
            hint = "; not_stated requires checked_scope filled from 实际已读 scope, never inferred from reason." if "not-stated" in str(exc) else ""
            raise KnowledgeFoundationError(f"Input check drafts.json: {exc}{hint}") from exc
        checked_path = run_dir / "checked-drafts.json"
        if checked_path.exists() and load_json(checked_path) != drafts:
            raise KnowledgeFoundationError("Input check: checked-drafts.json differs from normalized drafts.json; import the current authored draft into a new output before assessment.")
    else:
        drafts = load_json(run_dir / "checked-drafts.json")
        v3.validate_drafts(base, drafts, root=root)
    candidate = v3.candidate_preview(base, drafts, root=root)
    inventory_path, discourse_path, review_path = [run_dir / x for x in ("source-information.json", "discourse-audit.json", "semantic-assessment.json")]
    inventory, discourse, review = map(load_json, (inventory_path, discourse_path, review_path))
    checks: list[dict[str, Any]] = []
    def check(ident: str, condition: bool, detail: str, category: str = "structure") -> None:
        checks.append({"id": ident, "category": category, "status": "pass" if condition else "fail", "detail": detail})
    candidate_path = run_dir / "candidate.json"
    if inputs_only and candidate_path.exists():
        check("candidate:identity", load_json(candidate_path) == candidate,
              "candidate.json must exactly match the candidate reconstructed from current base and normalized authored drafts; stale or edited candidates require a new import, never automatic repair or invented semantic judgment.")
    check("work-package:format", not schema_errors(load_json(run_dir / "work-package.json"), "knowledge-semantic-work-package-v2.schema.json"), "Versioned source work package has the required fields.", "format")
    review_supplied = any(review.get(key) for key in ("draft_sha256", "inventory_sha256", "discourse_sha256", "reviewer", "checked_at", "checks"))
    if not inputs_only or review_supplied:
        check("assessment:format", not schema_errors(review, "knowledge-semantic-assessment-v1.schema.json"), "Independent assessment follows its versioned format.", "format")
    resolve = v3.anchor_resolver(base, root=root)
    resolve_region = v3.region_resolver(base, root=root)
    evidence_by_item: dict[tuple[str, str], tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    for name, audit, expected in (("information", inventory, INFORMATION_VERSION), ("discourse", discourse, DISCOURSE_VERSION)):
        check(name + ":format", not schema_errors(audit, "knowledge-source-audit-v1.schema.json"), "Source-first inventory follows its versioned format.", "format")
        check(name + ":identity", audit.get("schema_version") == expected and audit.get("source_sha256") == base["source"]["sha256"] and audit.get("snapshot_sha256") == base["source"]["snapshot_sha256"], "Source audit is bound to this original and snapshot.")
        check(name + ":reviewer", bool(audit.get("reviewer", "").strip() and audit.get("checked_at", "").strip()), "A real source-only inspection is identified; template blanks are not a review.")
        items = audit.get("items", [])
        check(name + ":items", isinstance(items, list) and bool(items) and len({x.get("id") for x in items}) == len(items), "Source inventory has unique independently judged items.")
        model_items = {x["id"]: x for x in candidate[name]}
        for item in items:
            ident = item.get("id", "missing")
            target = model_items.get(ident)
            check(name + ":" + ident, bool(target) and bool(item.get("meaning")) and target["meaning"] == item["meaning"], "Model mapping preserves the independently inventoried meaning.")
            if target:
                try:
                    anchors = [resolve(a) for a in item.get("anchors", [])]
                    regions = [resolve_region(r) for r in item.get("regions", [])]
                except KnowledgeFoundationError:
                    anchors, regions = [], []
                evidence_by_item[(name, ident)] = (anchors, regions)
                fields = ("locator", "bbox", "native_kind", "native_structure_sha256") if "region" in target else ("locator", "start", "end")
                model_evidence = target.get("region", target)
                independent_evidence = regions if "region" in target else anchors
                check(name + ":evidence:" + ident,
                      any(all(model_evidence.get(key) == anchor.get(key) for key in fields) for anchor in independent_evidence),
                      "Model audit evidence matches an exact independently inventoried source location.")
                declared = item.get("status")
                unresolved_allowed = (
                    bool(item.get("reason"))
                    and (
                        (declared in ("uncertain", "not_applicable") and item.get("critical") is False)
                        or (declared == "not_stated" and bool(item.get("checked_scope")))
                    )
                )
                check(name + ":coverage:" + ident, target.get("status") == "modeled" or unresolved_allowed and target.get("status") == declared, "Required meaning is modeled; confirmed source silence needs an explicit checked scope.")
                if declared == "not_stated" or target.get("status") == "not_stated":
                    check(name + ":checked-scope:" + ident,
                          bool(item.get("checked_scope")) and set(target.get("checked_scope", [])) == set(item["checked_scope"]),
                          f"{name}: {ident}; {'source-information.json' if name == 'information' else 'discourse-audit.json'} and drafts.json not_stated.checked_scope must preserve the same 实际已读 scope; fill both explicitly, reason does not substitute for checked_scope.")
        check(name + ":no-extra", set(model_items) <= {x.get("id") for x in items}, "Every model audit item belongs to the source inventory.")
    # Empty direct-text lines have no legal nonempty quote. Retain them in scope
    # and prove their structural identity. Native visual content is not exempt.
    covered: set[str] = set()
    for name, audit in (("information", inventory), ("discourse", discourse)):
        for item in audit.get("items", []):
            try:
                key = (name, item.get("id", "missing"))
                if key in evidence_by_item:
                    resolved, regions = evidence_by_item[key]
                else:
                    resolved = [resolve(a) for a in item.get("anchors", [])]
                    regions = [resolve_region(r) for r in item.get("regions", [])]
                valid = bool(resolved or regions)
                if name == "information" and valid:
                    covered.update(x["locator"] for x in resolved + regions)
            except KnowledgeFoundationError:
                valid = False
            check(name + ":anchors:" + str(item.get("id")), valid, "Inventory text or native visual evidence resolves to the frozen source.")
    blanks = structural_blank_locators(base, root=root)
    for locator in sorted(blanks):
        check("structural-blank:" + locator, True, "Verified frozen direct-text empty line remains in scope; it carries no semantic text span and needs no invented quote.")
    check("primary-scope", set(base["scope"]["included_locators"]) - blanks <= covered, "Every substantive primary unit has valid text or native visual inventory evidence; only proven empty direct-text lines are exempt. Context does not count as primary coverage.")
    identity = {"draft_sha256": v3.digest(drafts), "inventory_sha256": file_hash(inventory_path),
                "discourse_sha256": file_hash(discourse_path), "guide_sha256": guide_hash()}
    if not inputs_only or review_supplied:
        mismatches = [key for key in ("draft_sha256", "inventory_sha256", "discourse_sha256") if review.get(key) != identity[key]]
        check("assessment:identity", review.get("schema_version") == ASSESSMENT_VERSION and not mismatches,
              "semantic-assessment.json binds draft_sha256 to canonical digest of normalized checked-drafts JSON; inventory_sha256 and discourse_sha256 use raw file SHA-256. Mismatched fields: " + ", ".join(mismatches) + "; expected " + json.dumps({key: identity[key] for key in mismatches}, sort_keys=True) + ". Copy bindings only after actual independent checking; do not alter verdicts to pass.")
        check("assessment:actor", bool(review.get("reviewer", "").strip() and review.get("checked_at", "").strip()) and review.get("reviewer_type") in ("AI", "human") and review.get("check_method") in (("original_visual",) if base["source"]["kind"] == "pdf" else ("source_text", "original_visual")), "Semantic reviewer and actual original-source check method are recorded.")
    verdicts = [] if inputs_only else review.get("checks", [])
    keyed = {(x.get("target_kind"), x.get("target_id")): x for x in verdicts}
    if not inputs_only:
        check("assessment:unique", len(keyed) == len(verdicts), "Review items cannot overwrite a prior verdict by duplicate ID.")
    expected_targets = []
    for singular, plural in (("information", "information"), ("discourse", "discourse"), ("element", "elements"), ("relation", "relations")):
        for item in candidate[plural]:
            expected_targets.append((singular, item["id"]))
            verdict = keyed.get((singular, item["id"]), {})
            allowed = "pass" if singular in ("element", "relation") or item.get("status") == "modeled" else item.get("status")
            if not inputs_only:
                check("semantic:" + singular + ":" + item["id"], verdict.get("verdict") == allowed and bool(verdict.get("reason", "").strip()), "Recorded independent meaning/connection judgment: " + verdict.get("reason", "missing"), "recorded_semantic_review")
                if singular in ("element", "relation"):
                    check("semantic-revision:" + item["id"], verdict.get("content_revision") == item["content_revision"], "Semantic judgment is invalidated when object content or dependencies change.")
            if singular in ("element", "relation"):
                identity_resolved = item.get("origin_type") != "unresolved" and item.get("review_state") != "legacy_historical"
                if singular == "element":
                    identity_resolved = identity_resolved and item.get("modality") != "unresolved"
                check("identity:" + singular + ":" + item["id"], identity_resolved,
                      "Migrated object-level identity and voice must be explicitly revised before quality acceptance.")
    if inputs_only:
        return {"schema_version": "knowledge-semantic-input-check/v1", "status": "invalid" if any(x["status"] == "fail" for x in checks) else "valid",
                "semantic_status": "not_assessed", "semantic_review_required": True, "identity": identity, "checks": checks,
                "note": "仅输入合同 valid，不是语义 pass；no semantic verdict is accepted and no assess/review/publish event is recorded. draft uses canonical JSON digest; inventories use raw file SHA-256; guide is canonical digest of template filename→raw SHA-256 mapping."}
    check("assessment:no-extra", set(keyed) == set(expected_targets), "Assessment covers the candidate, not unrelated or missing objects.")
    input_hashes = {"inventory": file_hash(inventory_path), "discourse": file_hash(discourse_path), "semantic_assessment": file_hash(review_path), "guide": guide_hash(), "work_package": file_hash(run_dir / "work-package.json")}
    if reuse_context is not None:
        for key, name in (("reuse_provenance", "reuse-provenance.json"), ("dependency_certificate", "dependency-certificate.json"), ("review_plan", "review-plan.json")):
            input_hashes[key] = file_hash(run_dir / name)
        check("assessment:reuse-proof", True, "Exact prior public chain and every fresh/reused target are independently recomputed.")
    if expectations_path:
        checks.extend(expectation_checks(candidate, load_json(expectations_path),
                                         {"information": inventory, "discourse": discourse}))
        input_hashes["expectations"] = file_hash(expectations_path)
    gaps = [x for x in candidate["information"] + candidate["discourse"] if x["status"] != "modeled"]
    report = {"schema_version": QUALITY_VERSION, "artifact_id": base["artifact_id"], "base_sha256": v3.digest(base), "draft_sha256": v3.digest(drafts), "profile_sha256": base["profile"]["definition_sha256"],
              "input_hashes": input_hashes, "status": "fail" if any(x["status"] == "fail" for x in checks) else "pass", "checks": checks,
              "gaps": [{"id": x["id"], "status": x["status"], "meaning": x["meaning"]} for x in gaps],
              "reviewer": {k: review.get(k, "unknown") for k in ("reviewer", "reviewer_type", "model", "check_method", "checked_at")},
              "note": "Structure and frozen expectations are programmatic checks; source meaning judgments are the identified reviewer's recorded work, not automatic semantic proof."}
    if reuse_context is not None:
        from .semantic_dependency_cert import plan_delta
        plan = plan_delta(base, candidate, reuse_context)
        report["reuse_binding"] = {"certificate_sha256": plan["certificate_sha256"], "plan_sha256": plan["plan_sha256"]}
    report["report_sha256"] = v3.digest(report)
    check_schema(report, "knowledge-semantic-quality-v1.schema.json")
    return report


def reuse_requested(run_dir: Path) -> bool:
    if any((run_dir / name).exists() for name in ("reuse-template.json", "reuse-provenance.json", "dependency-certificate.json")):
        return True
    manifest = load_json(run_dir / "run-manifest.json")
    return any(row["command"] in ("assessment-reuse", "assessment-reuse-seal") for row in manifest["records"])


def publication_certificate(run_dir: Path, quality: dict[str, Any], *, root: Path) -> Any:
    if not quality.get("reuse_binding"):
        if reuse_requested(run_dir):
            raise KnowledgeFoundationError("Reuse workspaces require a quality report bound to their provenance.")
        return None
    from .semantic_dependency_cert import verify_assessment_reuse
    return verify_assessment_reuse(run_dir, load_json(run_dir / "reuse-provenance.json"), root=root)


def verify_assessment(run_dir: Path, report_path: Path, *, root: Path) -> dict[str, Any]:
    recorded = load_json(report_path)
    check_schema(recorded, "knowledge-semantic-quality-v1.schema.json")
    manifest = load_json(run_dir / "run-manifest.json")
    assessment_record = next((x for x in reversed(manifest["records"]) if x["command"] == "assess" and x["output"]["sha256"] == file_hash(report_path)), None)
    expectations = None
    if recorded.get("input_hashes", {}).get("expectations"):
        if not assessment_record or "expectations" not in assessment_record["inputs"]:
            raise KnowledgeFoundationError("Assessment with expectations requires its recorded input identity.")
        expectations = _resolve_ref(assessment_record["inputs"]["expectations"], run_dir, root)
    current = assess_workspace(run_dir, root=root, expectations_path=expectations)
    if current != recorded:
        raise KnowledgeFoundationError("Quality assessment inputs changed or the report was edited; reassess before review/publication.")
    return recorded


def replay_run(manifest_path: Path, *, root: Path, output_dir: Path) -> dict[str, Any]:
    """Re-execute deterministic stages; copy and validate frozen judgment events."""
    from .reading_view_v3 import render_reading_view_v3
    manifest = load_json(manifest_path)
    check_schema(manifest, "knowledge-semantic-run-v1.schema.json")
    if manifest.get("schema_version") != MANIFEST_VERSION:
        raise KnowledgeFoundationError("Unknown replay manifest.")
    old_dir, output_dir = manifest_path.parent.resolve(), output_dir.resolve()
    if old_dir == output_dir:
        raise KnowledgeFoundationError("Replay needs a separate output directory.")
    if manifest["tool"]["implementation_sha256"] != _tool_identity()["implementation_sha256"] or manifest["inputs"]["guide_sha256"] != guide_hash():
        raise KnowledgeFoundationError("Replay tool/resources differ; use the recorded release or create a new run.")
    refs = list(manifest["prepare_inputs"].values())
    for record in manifest["records"]:
        refs.append(record["output"])
        refs.extend(record.get("derived", {}).values())
        snapshots = record.get("input_snapshots")
        if snapshots is None:
            refs.extend(record["inputs"].values())  # Original v1 workspaces.
        else:
            if set(snapshots) != {key for key, ref in record["inputs"].items() if ref["location"] == "run"}:
                raise KnowledgeFoundationError("Replay input snapshots do not cover exact run-local inputs.")
            refs.extend(snapshots.values())
            refs.extend(ref for ref in record["inputs"].values() if ref["location"] == "root")
    for ref in refs:
        if file_hash(_resolve_ref(ref, old_dir, root)) != ref["sha256"]:
            raise KnowledgeFoundationError(f"Frozen replay input changed: {ref['path']}")
    prepare = {k: _resolve_ref(r, old_dir, root) for k, r in manifest["prepare_inputs"].items()}
    prepare_workspace(prepare["foundation"], root=root, profile=manifest["profile"], scope_path=prepare["scope"], output_dir=output_dir, existing_path=prepare.get("existing"))
    generated = {(r["output"]["location"], r["output"]["path"]) for r in manifest["records"]}
    # Preserve old v1 behavior where no per-step snapshot exists. New records
    # restore each authoring state only when that step is replayed.
    for record in manifest["records"]:
        if "input_snapshots" in record:
            continue
        for ref in record["inputs"].values():
            if ref["location"] == "run" and (ref["location"], ref["path"]) not in generated:
                old = _resolve_ref(ref, old_dir, root)
                new = _resolve_ref(ref, output_dir, root)
                new.parent.mkdir(parents=True, exist_ok=True)
                if new.exists() and new.read_bytes() != old.read_bytes() and new.name not in ("source-information.json", "discourse-audit.json", "semantic-assessment.json", "drafts.json", "reuse-provenance.json"):
                    raise KnowledgeFoundationError("Replay would overwrite a non-template input.")
                new.write_bytes(old.read_bytes())
    completed = []
    generated_so_far = set()
    for record in manifest["records"]:
        for name, frozen_ref in record.get("input_snapshots", {}).items():
            ref = record["inputs"][name]
            target = _resolve_ref(ref, output_dir, root)
            frozen = _resolve_ref(frozen_ref, old_dir, root)
            content = frozen.read_bytes()
            if (ref["location"], ref["path"]) in generated_so_far:
                if not target.is_file() or target.read_bytes() != content:
                    raise KnowledgeFoundationError(f"Replay generated input differs: {ref['path']}")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() and target.read_bytes() != content and target.name not in ("source-information.json", "discourse-audit.json", "semantic-assessment.json", "drafts.json", "reuse-provenance.json"):
                    raise KnowledgeFoundationError("Replay would overwrite a non-template input.")
                target.write_bytes(content)
        paths = {key: _resolve_ref(ref, output_dir if ref["location"] == "run" else old_dir, root) for key, ref in record["inputs"].items()}
        args, command = record["arguments"], record["command"]
        out = _resolve_ref(record["output"], output_dir, root)
        if record["output"]["location"] != "run":
            raise KnowledgeFoundationError("Replay may write only into its destination workspace.")
        artifact = load_json(paths["artifact"]) if "artifact" in paths else None
        if command in ("source-check", "review"):
            # Replaying an old event is not a new human/AI act of review.
            value = load_json(_resolve_ref(record["output"], old_dir, root))
        elif command == "draft-import":
            value = v3.normalize_drafts(artifact, load_json(paths["drafts"]), root=root)
        elif command == "assess":
            value = assess_workspace(output_dir, root=root, expectations_path=paths.get("expectations"))
        elif command == "publish":
            quality = verify_assessment(output_dir, paths["assessment"], root=root)
            value = v3.publish(artifact, load_json(paths["drafts"]), load_json(paths["ledger"]), load_json(paths["source_checks"]), quality, revision_id=args["revision_id"], root=root, dependency_certificate=publication_certificate(output_dir, quality, root=root))
        elif command == "query":
            value = v3.query(artifact, term=args.get("term", ""), package_id=args.get("statement_id"), role=args.get("role"), layer=args.get("layer", "source"))
        elif command == "render":
            value = (render_reading_view_v3(artifact, paths["artifact"].read_bytes(), load_json(paths["view_spec"]) if "view_spec" in paths else None)
                     if args.get("format") == "html" else v3.render_markdown(artifact, layer=args.get("layer", "source")))
        elif command == "export":
            value = v3.export(artifact, root=root, producer_commit=args["producer_commit"])
        elif command == "assessment-reuse":
            from .semantic_dependency_cert import prepare_assessment_reuse
            value = prepare_assessment_reuse(output_dir, root=root, prior_run=output_dir / "prior-evidence",
                    prior_projection_path=output_dir / "prior-accepted.json", certificate_path=output_dir / "dependency-certificate.json")
        elif command == "assessment-reuse-seal":
            from .semantic_dependency_cert import seal_assessment_reuse
            value = seal_assessment_reuse(output_dir, root=root)
        elif command == "impact":
            context = None
            certificate_error = None
            if "dependency_certificate" in paths:
                from .semantic_dependency_cert import verify_certificate
                try:
                    context = verify_certificate(artifact, load_json(paths["dependency_certificate"]), root=root,
                                                 prior_run=output_dir / "prior-evidence")
                except KnowledgeFoundationError as error:
                    certificate_error = str(error)
            value = v3.impact(artifact, load_json(paths["new"]), dependency_certificate=context)
            if certificate_error:
                value["certificate_fallback_reason"] = certificate_error
        else:
            raise KnowledgeFoundationError(f"Unsupported replay operation: {command}")
        write_immutable(out, value)
        if file_hash(out) != record["output"]["sha256"]:
            raise KnowledgeFoundationError(f"Replay differs from the frozen result: {record['output']['path']}")
        record_step(output_dir, root, command, args, inputs=paths, output=out)
        for name, ref in record.get("derived", {}).items():
            rebuilt = _resolve_ref(ref, output_dir, root)
            if not rebuilt.is_file() or file_hash(rebuilt) != ref["sha256"]:
                raise KnowledgeFoundationError(f"Replay derived {name} differs from frozen result.")
        generated_so_far.add((record["output"]["location"], record["output"]["path"]))
        completed.append({"command": command, "path": record["output"]["path"], "sha256": file_hash(out), "review_event_reused": command in ("source-check", "review")})
    return {"schema_version": "knowledge-semantic-replay/v1", "input_fingerprint": manifest["input_fingerprint"], "steps": completed, "new_review_events": 0, "status": "pass"}
