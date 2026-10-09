"""Local source ingestion, review, validation, views, and read-only export."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from .core import KnowledgeFoundationError, coverage, impact, load_json, query, render_markdown, validate
from .export import export_manifest
from .ingest import MineruPdfAdapter, TextAdapter, ingest
from .review import new_review_ledger, publish, record_decision, validate_drafts
from .reading_view import render_reading_view
from . import semantic_v2 as v2
from . import semantic_v3 as v3
from . import semantic_workflow as workflow
from .semantic import (
    VERSION as SEMANTIC_VERSION, export_projection, impact_projection,
    new_semantic_ledger, publish_semantic, query_projection, record_semantic_decision,
    render_projection, validate_projection, validate_semantic_drafts,
)


def write_new(path: Path, value: dict | str, *, reuse_equal: bool = False) -> bool:
    if isinstance(value, str):
        content = value
    else:
        content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    if path.exists() and reuse_equal and path.read_text(encoding='utf-8') == content:
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(content)
    return False


def load_json_array(path: Path) -> list[dict]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise KnowledgeFoundationError(f'Cannot read JSON array: {path}') from exc
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise KnowledgeFoundationError(f'Expected JSON object array: {path}')
    return value


def source_adapter(args: argparse.Namespace, parser: argparse.ArgumentParser):
    kind = args.adapter if args.adapter != 'auto' else ('mineru' if args.source.suffix.lower() == '.pdf' else 'text')
    if kind == 'mineru':
        if not args.middle_json and (not args.mineru_cli or not args.mineru_home):
            parser.error('Active PDF parsing needs --mineru-cli and --mineru-home')
        return MineruPdfAdapter(executable=args.mineru_cli, home=args.mineru_home, middle_json=args.middle_json,
                               middle_sha256=args.middle_sha256, middle_source_sha256=args.middle_source_sha256,
                               middle_tier=args.middle_tier)
    if args.source.suffix.lower() == '.pdf':
        parser.error('PDF input requires the MinerU adapter')
    return TextAdapter()


def handle_v3(args: argparse.Namespace, artifact: dict, parser: argparse.ArgumentParser) -> dict:
    command = args.command
    if command == 'export' and not (args.producer_commit or '').strip():
        raise KnowledgeFoundationError('export input: V3 requires explicit --producer-commit (frozen commit or release identity), before source resolution or export')
    resolve = command in ('draft-import', 'review', 'publish', 'export') or getattr(args, 'resolve', False)
    report = v3.validate_projection(artifact, root=args.root, resolve=resolve)
    inputs: dict[str, Path] = {'artifact': args.artifact}
    if command == 'validate':
        return {'valid': True, 'resolved': resolve, **report}
    if command == 'coverage':
        return report
    if command == 'query':
        value = v3.query(artifact, term=args.term, package_id=args.statement_id, role=args.role, layer=args.layer)
        if args.output:
            reused = write_new(args.output, value, reuse_equal=True)
            if args.run_dir:
                workflow.record_step(args.run_dir, args.root, command,
                                     {'term': args.term, 'statement_id': args.statement_id, 'role': args.role, 'layer': args.layer},
                                     inputs=inputs, output=args.output)
            return {'output': str(args.output), 'element_count': len(value['elements']), 'relation_count': len(value['relations']), 'reused': reused}
        if args.run_dir:
            parser.error('V3 workspace query needs --output')
        return value
    if command == 'impact':
        if args.old_view_spec or args.new_view_spec or args.foundation:
            parser.error('V3 impact compares two v3 revisions; display specs and foundation are separate inputs')
        later = load_json(args.new)
        v3.validate_projection(later, root=args.root, resolve=args.resolve)
        inputs['new'] = args.new
        certificate_context = None
        certificate_error = None
        if args.dependency_certificate:
            from .semantic_dependency_cert import verify_certificate, reuse_inputs
            try:
                if not args.prior_run:
                    raise KnowledgeFoundationError('A certificate requires the complete --prior-run')
                certificate_context = verify_certificate(artifact, load_json(args.dependency_certificate), root=args.root, prior_run=args.prior_run)
            except KnowledgeFoundationError as error:
                certificate_error = str(error)
            if args.run_dir:
                if args.prior_run is None or args.prior_run.resolve() != (args.run_dir / 'prior-evidence').resolve():
                    raise KnowledgeFoundationError('Recorded certificate impact requires the run-local frozen prior-evidence')
                inputs.update(reuse_inputs(args.run_dir))
                inputs['dependency_certificate'] = args.dependency_certificate
        result = v3.impact(artifact, later, dependency_certificate=certificate_context)
        if certificate_error:
            result['certificate_fallback_reason'] = certificate_error
        if args.output:
            reused = write_new(args.output, result, reuse_equal=True)
            if args.run_dir:
                workflow.record_step(args.run_dir, args.root, command, {}, inputs=inputs, output=args.output)
            return {'output': str(args.output), 'reused': reused, **result}
        if args.run_dir:
            parser.error('V3 workspace impact needs --output')
        return result
    if command == 'render':
        if args.format == 'html':
            from .reading_view_v3 import render_reading_view_v3
            rendered = render_reading_view_v3(artifact, args.artifact.read_bytes(), load_json(args.view_spec) if args.view_spec else None)
            if args.view_spec:
                inputs['view_spec'] = args.view_spec
        else:
            if args.view_spec is not None:
                parser.error('--view-spec requires --format html')
            rendered = v3.render_markdown(artifact, layer=args.layer)
        reused = write_new(args.output, rendered, reuse_equal=True)
        if args.run_dir:
            workflow.record_step(args.run_dir, args.root, command, {'format': args.format, 'layer': args.layer}, inputs=inputs, output=args.output)
        return {'output': str(args.output), 'revision_id': artifact['revision']['id'], 'reused': reused}
    if command == 'draft-import':
        inputs['drafts'] = args.drafts
        if args.run_dir:
            # The runbook generated for this step binds both author audits.
            inputs.update({'information': args.run_dir / 'source-information.json',
                           'discourse': args.run_dir / 'discourse-audit.json'})
        value = v3.normalize_drafts(artifact, load_json(args.drafts), root=args.root)
        reused = write_new(args.output, value, reuse_equal=True)
        if args.run_dir:
            workflow.record_step(args.run_dir, args.root, command, {}, inputs=inputs, output=args.output)
        return {'output': str(args.output), 'proposal_count': len(value['proposals']), 'reused': reused}
    if command in ('review', 'publish'):
        if not args.source_checks or not args.assessment:
            parser.error('V3 review/publish need --source-checks and --assessment')
        if not args.run_dir:
            parser.error('V3 review/publish need --run-dir to verify assessment inputs')
        quality = workflow.verify_assessment(args.run_dir, args.assessment, root=args.root)
        drafts = load_json(args.drafts)
        checks = load_json(args.source_checks)
        inputs.update({'drafts': args.drafts, 'source_checks': args.source_checks, 'assessment': args.assessment})
        if command == 'review':
            ledger = load_json(args.output) if args.output.exists() else load_json(args.ledger) if args.ledger.exists() else v3.new_review_ledger(artifact, drafts, quality)
            later = v3.record_decision(artifact, drafts, ledger, checks, quality, proposal_id=args.proposal_id,
                                       decision=args.decision, reviewer=args.reviewer, checked_anchors=args.checked_anchor,
                                       checked_regions=args.checked_region, check_method=args.check_method,
                                       note=args.note, root=args.root)
            reused = write_new(args.output, later, reuse_equal=True)
            if args.ledger.exists() and args.ledger != args.output:
                inputs['ledger'] = args.ledger
            workflow.record_step(args.run_dir, args.root, command, {'proposal_id': args.proposal_id, 'decision': args.decision}, inputs=inputs, output=args.output)
            return {'output': str(args.output), 'decision_count': len(later['decisions']), 'reused': reused}
        inputs['ledger'] = args.ledger
        from .semantic_dependency_cert import reuse_inputs
        inputs.update(reuse_inputs(args.run_dir))
        later = v3.publish(artifact, drafts, load_json(args.ledger), checks, quality, revision_id=args.revision_id, root=args.root, dependency_certificate=workflow.publication_certificate(args.run_dir, quality, root=args.root))
        reused = write_new(args.output, later, reuse_equal=True)
        workflow.record_step(args.run_dir, args.root, command, {'revision_id': args.revision_id}, inputs=inputs, output=args.output)
        return {'output': str(args.output), 'revision_id': later['revision']['id'], 'reused': reused}
    if command == 'export':
        commit = args.producer_commit
        value = v3.export(artifact, root=args.root, producer_commit=commit)
        reused = write_new(args.output, value, reuse_equal=True)
        if args.run_dir:
            workflow.record_step(args.run_dir, args.root, command, {'producer_commit': commit}, inputs=inputs, output=args.output)
        return {'output': str(args.output), 'producer': value['producer'], 'reused': reused}
    raise KnowledgeFoundationError(f'Unsupported v3 command: {command}')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    organize_p = sub.add_parser('organize', help='Prepare, review, publish and replay a discourse organization')
    organize_sub = organize_p.add_subparsers(dest='organize_command', required=True)
    organize_prepare = organize_sub.add_parser('prepare')
    organize_prepare.add_argument('export', type=Path)
    organize_prepare.add_argument('--output-dir', type=Path, required=True)
    organize_prepare.add_argument('--requirements', type=Path, help='Freeze independent semantic/readability requirements')
    organize_prepare.add_argument('--root', type=Path, help='Required for v3/v4: root of frozen source paths')
    organize_prepare.add_argument('--document-map', type=Path, help='Previously verified source-reading-map/v1 or v2')
    organize_prepare.add_argument('--format-version', type=int, choices=(1, 2, 3, 4), default=1,
                                  help='Discourse format (default: 1)')
    for stage in ('assess', 'publish'):
        organize_stage = organize_sub.add_parser(stage)
        organize_stage.add_argument('run_dir', type=Path)
    organize_replay = organize_sub.add_parser('replay')
    organize_replay.add_argument('manifest', type=Path)
    organize_replay.add_argument('--output-dir', type=Path, required=True)
    organize_reuse = organize_sub.add_parser('reuse-review', help='Prepare explicit unchanged prior judgments for a new independent review')
    organize_reuse.add_argument('run_dir', type=Path)
    organize_reuse.add_argument('--from-review', type=Path, required=True)
    organize_query = organize_sub.add_parser('query', help='Query an exact published v4 object')
    organize_query.add_argument('release', type=Path)
    organize_query.add_argument('--id', required=True)
    organize_query.add_argument('--output', type=Path)
    for command in ('ingest', 'start'):
        ingest_p = sub.add_parser(command, help='Create a local content inventory' if command == 'ingest' else 'Ingest and prepare an identity-bound production workspace')
        ingest_p.add_argument('source', type=Path)
        ingest_p.add_argument('--root', type=Path, required=True)
        ingest_p.add_argument('--adapter', choices=('auto', 'text', 'mineru'), default='auto')
        ingest_p.add_argument('--pages')
        ingest_p.add_argument('--mineru-cli', type=Path)
        ingest_p.add_argument('--mineru-home', type=Path)
        ingest_p.add_argument('--middle-json', type=Path, help='Use existing local MinerU middle JSON')
        ingest_p.add_argument('--middle-sha256', help='Expected SHA-256 of an existing middle JSON')
        ingest_p.add_argument('--middle-source-sha256', help='Expected SHA-256 of the original PDF attributed to the middle JSON')
        ingest_p.add_argument('--middle-tier', choices=('basic', 'standard', 'advanced'), help='Tier of an existing middle JSON')
        ingest_p.add_argument('--order-overrides', type=Path, help='Audited complete per-page reading-order corrections')
        if command == 'ingest':
            ingest_p.add_argument('--workspace', type=Path, required=True)
        else:
            ingest_p.add_argument('--output-dir', type=Path, required=True)
            ingest_p.add_argument('--scope', type=Path, required=True, help='Explicit primary/context locator JSON; never inferred')
            ingest_p.add_argument('--profile', required=True)
            ingest_p.add_argument('--existing', type=Path)
            ingest_p.add_argument('--executor', default='unknown')
            ingest_p.add_argument('--model', default='unknown')
    correction_p = sub.add_parser('revise-transcription', help='Create a new snapshot from declared original-source corrections')
    correction_p.add_argument('artifact', type=Path)
    correction_p.add_argument('--root', type=Path, required=True)
    correction_p.add_argument('--corrections', type=Path, required=True)
    correction_p.add_argument('--output-dir', type=Path, required=True)
    prepare_p = sub.add_parser('prepare', help='Prepare a semantic work package or v3 workspace')
    prepare_p.add_argument('artifact', type=Path, help='Foundation v1')
    prepare_p.add_argument('--root', type=Path, required=True)
    prepare_p.add_argument('--profile', required=True)
    prepare_p.add_argument('--scope', type=Path, required=True)
    prepare_p.add_argument('--existing', type=Path)
    prepare_p.add_argument('--executor', default='unknown', help='Actual workspace executor; unknown if unavailable')
    prepare_p.add_argument('--model', default='unknown', help='Actual model identifier; unknown if unavailable')
    prepare_dest = prepare_p.add_mutually_exclusive_group(required=True)
    prepare_dest.add_argument('--output', type=Path)
    prepare_dest.add_argument('--output-dir', type=Path)
    assess_p = sub.add_parser('assess', help='Evaluate a v3 workspace against source audits and frozen expectations')
    assess_p.add_argument('run_dir', type=Path)
    assess_p.add_argument('--root', type=Path, required=True)
    assess_p.add_argument('--expectations', type=Path)
    assess_p.add_argument('--output', type=Path, required=True)
    assess_p.add_argument('--inputs-only', action='store_true', help='Check input contracts before semantic review; valid is not semantic pass and cannot authorize publication')
    reuse_p = sub.add_parser('assessment-reuse', help='Prepare exact prior judgments and pending fresh targets, or seal supplied fresh judgments')
    reuse_p.add_argument('run_dir', type=Path)
    reuse_p.add_argument('--root', type=Path, required=True)
    reuse_p.add_argument('--from-run', type=Path)
    reuse_p.add_argument('--from-projection', type=Path)
    reuse_p.add_argument('--dependency-certificate', type=Path)
    reuse_p.add_argument('--seal', action='store_true')
    replay_p = sub.add_parser('replay' , help='Verify and rebuild a frozen v3 run')
    replay_p.add_argument('manifest', type=Path)
    replay_p.add_argument('--root', type=Path, required=True)
    replay_p.add_argument('--output-dir', type=Path, required=True)
    migrate_p = sub.add_parser('migrate-semantic', help='Explicitly map a v1 semantic projection to v2')
    migrate_p.add_argument('artifact', type=Path)
    migrate_p.add_argument('--root', type=Path, required=True)
    migrate_p.add_argument('--revision-id', required=True)
    migrate_p.add_argument('--output', type=Path, required=True)
    check_p = sub.add_parser('source-check', help='Record independent source verification for v2')
    check_p.add_argument('artifact', type=Path)
    check_p.add_argument('--root', type=Path, required=True)
    check_p.add_argument('--ledger', type=Path, required=True)
    check_p.add_argument('--anchors', type=Path, required=True, help='JSON array of exact source anchors')
    check_p.add_argument('--regions', type=Path, help='JSON array of frozen original-page regions')
    check_p.add_argument('--check-id', required=True)
    check_p.add_argument('--reviewer', required=True)
    check_p.add_argument('--check-method', choices=('original_visual', 'source_text'), required=True)
    check_p.add_argument('--note', default='')
    check_p.add_argument('--output', type=Path, required=True)
    check_p.add_argument('--run-dir', type=Path)
    for command in ('validate', 'coverage', 'query', 'render', 'impact', 'draft-import', 'review', 'publish', 'export'):
        p = sub.add_parser(command)
        p.add_argument('artifact', type=Path)
        p.add_argument('--root', type=Path, default=Path.cwd())
        p.add_argument('--run-dir', type=Path, help='Record this v3 step in a prepared workspace')
        if command in ('validate', 'coverage', 'query', 'render', 'impact'):
            p.add_argument('--resolve', action='store_true')
        if command == 'query':
            p.add_argument('--term', default='')
            p.add_argument('--id', dest='statement_id')
            p.add_argument('--role')
            p.add_argument('--output', type=Path, help='Write query JSON; required for workspace recording')
            p.add_argument('--layer', choices=('source', 'all', 'interpretation'), default='source')
        if command == 'impact':
            p.add_argument('--new', type=Path, required=True)
            p.add_argument('--output', type=Path, help='Write v3 impact JSON; required for workspace recording')
            p.add_argument('--dependency-certificate', type=Path, help='Complete reviewer-attested semantic dependency certificate')
            p.add_argument('--prior-run', type=Path, help='Complete prior accepted publication workspace')
            p.add_argument('--foundation', type=Path, help='Frozen source foundation for semantic-to-semantic impact')
            p.add_argument('--old-view-spec', type=Path, help='Old v2 display spec for layout-only impact')
            p.add_argument('--new-view-spec', type=Path, help='New v2 display spec for layout-only impact')
        if command in ('render', 'draft-import', 'review', 'publish', 'export'):
            p.add_argument('--output', type=Path, required=True)
        if command == 'render':
            p.add_argument('--format', choices=('md', 'html'), default='md')
            p.add_argument('--view-spec', type=Path)
            p.add_argument('--layer', choices=('source', 'all', 'interpretation'), default='source')
        if command in ('draft-import', 'review', 'publish'):
            p.add_argument('--drafts', type=Path, required=True)
        if command in ('review', 'publish'):
            p.add_argument('--ledger', type=Path, required=True)
            p.add_argument('--source-checks', type=Path, help='Separate v2 source-check ledger')
            p.add_argument('--assessment', type=Path, help='Exact passing v3 quality report')
        if command == 'review':
            p.add_argument('--proposal-id', required=True)
            p.add_argument('--decision', choices=('accept', 'reject', 'pending'), required=True)
            p.add_argument('--reviewer', required=True)
            p.add_argument('--checked-locator', action='append', default=[])
            p.add_argument('--checked-anchor', action='append', default=[])
            p.add_argument('--checked-region', action='append', default=[])
            p.add_argument('--note', default='')
            p.add_argument('--check-method', choices=('original_visual', 'source_text', 'parsed_text'), default='source_text')
        if command == 'publish':
            p.add_argument('--revision-id', required=True)
        if command == 'export':
            p.add_argument('--producer-commit')
    args = parser.parse_args(argv)
    try:
        if args.command == 'organize':
            from . import organize
            if args.organize_command == 'prepare':
                result = organize.prepare(args.export, args.output_dir, args.requirements, args.format_version, args.root, args.document_map)
            elif args.organize_command == 'replay':
                result = organize.replay(args.manifest, args.output_dir)
            elif args.organize_command == 'reuse-review':
                result = organize.reuse_review(args.run_dir, args.from_review)
            elif args.organize_command == 'query':
                from . import organize_v4
                result = organize_v4.query(args.release, args.id)
                if args.output:
                    write_new(args.output,result,reuse_equal=True)
            else:
                result = getattr(organize, args.organize_command)(args.run_dir)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result.get('status') == 'fail' else 0
        elif args.command == 'revise-transcription':
            from .transcription_revision import revise_transcription
            result = revise_transcription(args.artifact, root=args.root, corrections_path=args.corrections, output_dir=args.output_dir)
        elif args.command == 'prepare':
            if args.output_dir is not None:
                profile = workflow.resolve_profile(args.profile)
                result = workflow.prepare_workspace(args.artifact, root=args.root, profile=profile,
                                                    scope_path=args.scope, output_dir=args.output_dir,
                                                    existing_path=args.existing, executor=args.executor,
                                                    model=args.model)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0
            foundation_bytes = args.artifact.read_bytes()
            existing_bytes = args.existing.read_bytes() if args.existing else None
            profile_path = Path(args.profile)
            profile_config = load_json(profile_path)
            bundled_profile = Path(v2.__file__).resolve().parent / 'profiles' / f"{profile_config['id']}-{profile_config['version']}.json"
            if profile_path.read_bytes() != bundled_profile.read_bytes():
                raise KnowledgeFoundationError('Prepare profile bytes differ from the installed versioned definition.')
            profile = v2.profile_manifest(profile_config['id'], profile_config['version'])
            foundation = load_json(args.artifact)
            validate(foundation, root=args.root, resolve=True)
            existing = load_json(args.existing) if args.existing else None
            if existing is not None:
                if existing.get('schema_version') == v2.VERSION:
                    v2.validate_projection(existing, root=args.root, resolve=True)
                elif existing.get('schema_version') == SEMANTIC_VERSION:
                    validate_projection(existing, root=args.root, resolve=True)
                else:
                    parser.error('--existing needs a semantic projection v1 or v2')
            package = v2.prepare(foundation, foundation_bytes, load_json(args.scope), profile,
                                 existing=existing, existing_bytes=existing_bytes)
            reused = write_new(args.output, package, reuse_equal=True)
            result = {'output': str(args.output), 'input_fingerprint': package['input_fingerprint'], 'reused': reused}
        elif args.command == 'assess':
            if args.inputs_only:
                if args.expectations:
                    parser.error('--inputs-only checks input contracts, not frozen semantic expectations; use full assess')
                report = workflow.assess_workspace(args.run_dir, root=args.root, inputs_only=True)
                reused = write_new(args.output, report, reuse_equal=True)
                print(json.dumps({'output': str(args.output), 'status': report['status'], 'semantic_status': report['semantic_status'], 'semantic_review_required': report['semantic_review_required'], 'reused': reused}, ensure_ascii=False, indent=2))
                return 0
            frozen_expectations = None
            if args.expectations:
                frozen_expectations = args.run_dir / 'inputs' / 'expectations.json'
                frozen_expectations.parent.mkdir(parents=True, exist_ok=True)
                content = args.expectations.read_bytes()
                if frozen_expectations.exists() and frozen_expectations.read_bytes() != content:
                    raise KnowledgeFoundationError('Frozen expectations changed; create a new run directory.')
                if not frozen_expectations.exists():
                    frozen_expectations.write_bytes(content)
            report = workflow.assess_workspace(args.run_dir, root=args.root, expectations_path=frozen_expectations)
            reused = write_new(args.output, report, reuse_equal=True)
            inputs = {name: args.run_dir / filename for name, filename in (('artifact', 'base.json'), ('drafts', 'checked-drafts.json'),
                      ('information', 'source-information.json'), ('discourse', 'discourse-audit.json'), ('semantic_assessment', 'semantic-assessment.json'))}
            if frozen_expectations:
                inputs['expectations'] = frozen_expectations
            from .semantic_dependency_cert import reuse_inputs
            inputs.update(reuse_inputs(args.run_dir))
            workflow.record_step(args.run_dir, args.root, 'assess', {}, inputs=inputs, output=args.output)
            result = {'output': str(args.output), 'status': report['status'], 'reused': reused}
        elif args.command == 'assessment-reuse':
            from .semantic_dependency_cert import prepare_assessment_reuse, seal_assessment_reuse, reuse_inputs
            if args.seal:
                value = seal_assessment_reuse(args.run_dir, root=args.root)
                command, output = 'assessment-reuse-seal', args.run_dir / 'reuse-provenance.json'
            else:
                if not all((args.from_run, args.from_projection, args.dependency_certificate)):
                    parser.error('assessment-reuse requires --from-run, --from-projection, --dependency-certificate, or --seal')
                value = prepare_assessment_reuse(args.run_dir, root=args.root, prior_run=args.from_run,
                            prior_projection_path=args.from_projection, certificate_path=args.dependency_certificate)
                command, output = 'assessment-reuse', args.run_dir / 'reuse-template.json'
            inputs = {**reuse_inputs(args.run_dir), **{name: args.run_dir / file for name, file in (
                ('artifact','base.json'), ('drafts','checked-drafts.json'), ('information','source-information.json'),
                ('discourse','discourse-audit.json'), ('semantic_assessment','semantic-assessment.json'))}}
            workflow.record_step(args.run_dir, args.root, command, {}, inputs=inputs, output=output)
            result = {'output':str(output), 'status':value['status'],
                      'fresh_targets':sum(x['mode']=='fresh-required' for x in value['targets']),
                      'reused_targets':sum(x['mode']=='reused' for x in value['targets'])}
        elif args.command == 'replay':
            result = workflow.replay_run(args.manifest, root=args.root, output_dir=args.output_dir)
        elif args.command == 'migrate-semantic':
            migrated = v2.migrate_v1(load_json(args.artifact), args.artifact.read_bytes(), root=args.root, revision_id=args.revision_id)
            reused = write_new(args.output, migrated, reuse_equal=True)
            result = {'output': str(args.output), 'revision_id': migrated['revision']['id'], 'reused': reused}
        elif args.command == 'source-check':
            artifact = load_json(args.artifact)
            if artifact.get('schema_version') not in (v2.VERSION, v3.VERSION):
                parser.error('source-check needs a v2 or v3 projection')
            is_v3 = artifact['schema_version'] == v3.VERSION
            ledger = load_json(args.output) if args.output.exists() else load_json(args.ledger) if args.ledger.exists() else (v3.new_source_check_ledger(artifact) if is_v3 else v2.new_source_check_ledger(artifact))
            recorder = v3.record_source_check if is_v3 else v2.record_source_check
            later = recorder(artifact, ledger, check_id=args.check_id, anchors=load_json_array(args.anchors),
                             regions=load_json_array(args.regions) if args.regions else [], reviewer=args.reviewer, method=args.check_method, note=args.note, root=args.root)
            reused = write_new(args.output, later, reuse_equal=True)
            if is_v3 and args.run_dir:
                workflow.record_step(args.run_dir, args.root, 'source-check', {'check_id': args.check_id},
                                     inputs={'artifact': args.artifact, 'anchors': args.anchors, **({'regions': args.regions} if args.regions else {})}, output=args.output)
            result = {'output': str(args.output), 'check_count': len(later['checks']), 'reused': reused}
        elif args.command in ('ingest', 'start'):
            adapter = source_adapter(args, parser)
            if args.command == 'start':
                from .production_entry import start
                adapter_options = {name: str(value.resolve()) if isinstance(value, Path) else value
                                   for name in ('mineru_cli', 'mineru_home', 'middle_sha256', 'middle_source_sha256', 'middle_tier')
                                   if (value := getattr(args, name)) is not None}
                result = start(args.source, root=args.root, output_dir=args.output_dir,
                               scope_path=args.scope, profile=workflow.resolve_profile(args.profile), adapter=adapter,
                               adapter_options=adapter_options, pages=args.pages, middle_json=args.middle_json,
                               order_overrides=args.order_overrides, existing_path=args.existing,
                               executor=args.executor, model=args.model)
            else:
                artifact_path, snapshot_path = ingest(args.source, root=args.root, workspace=args.workspace, adapter=adapter, pages=args.pages, order_overrides=args.order_overrides)
                result = {'artifact': str(artifact_path), 'snapshot': str(snapshot_path)}
        else:
            artifact = load_json(args.artifact)
            if artifact.get('schema_version') == v3.VERSION:
                result = handle_v3(args, artifact, parser)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0
            semantic = artifact.get('schema_version') == SEMANTIC_VERSION
            semantic_v2 = artifact.get('schema_version') == v2.VERSION
            resolve = args.command in ('draft-import', 'review', 'publish', 'export') or getattr(args, 'resolve', False)
            report = v2.validate_projection(artifact, root=args.root, resolve=resolve) if semantic_v2 else validate_projection(artifact, root=args.root, resolve=resolve) if semantic else validate(artifact, root=args.root, resolve=resolve)
            if args.command == 'validate':
                result = {'valid': True, 'resolved': resolve, **report}
            elif args.command == 'coverage':
                result = report if semantic or semantic_v2 else coverage(artifact)
            elif args.command == 'query':
                if not semantic and not semantic_v2 and (args.statement_id or args.role):
                    parser.error('--id and --role require a semantic projection')
                result = v2.query(artifact, term=args.term, package_id=args.statement_id, role=args.role) if semantic_v2 else query_projection(artifact, term=args.term, statement_id=args.statement_id, role=args.role) if semantic else query(artifact, term=args.term)
            elif args.command == 'impact':
                later = load_json(args.new)
                if args.old_view_spec or args.new_view_spec:
                    if not semantic_v2 or not args.old_view_spec or not args.new_view_spec or args.artifact != args.new:
                        parser.error('View impact needs one v2 artifact as OLD and NEW, plus both view specs')
                    result = v2.impact_view(load_json(args.old_view_spec), load_json(args.new_view_spec), artifact, args.artifact.read_bytes())
                elif later.get('schema_version') == v2.VERSION and semantic:
                    v2.validate_projection(later, root=args.root, resolve=args.resolve)
                    result = v2.impact_migration(artifact, later, args.artifact.read_bytes())
                elif semantic_v2 and later.get('schema_version') == v2.VERSION:
                    v2.validate_projection(later, root=args.root, resolve=args.resolve)
                    result = v2.impact(artifact, later)
                else:
                    later_semantic = later.get('schema_version') == SEMANTIC_VERSION
                    if later_semantic:
                        validate_projection(later, root=args.root, resolve=args.resolve)
                        if semantic and not args.foundation:
                            parser.error('Semantic-to-semantic impact needs --foundation')
                        foundation = artifact if not semantic else load_json(args.foundation)
                        validate(foundation, root=args.root, resolve=args.resolve)
                        result = impact_projection(artifact, later, foundation=foundation)
                    else:
                        if semantic or semantic_v2:
                            parser.error('A semantic projection cannot impact a legacy foundation')
                        validate(later, root=args.root, resolve=args.resolve)
                        result = impact(artifact, later)
            elif args.command == 'render':
                if args.format == 'html':
                    if not (semantic or semantic_v2) or args.view_spec is None:
                        parser.error('HTML reading view needs a semantic projection and --view-spec')
                    if semantic_v2:
                        from .reading_view_v2 import render_reading_view_v2
                        rendered = render_reading_view_v2(artifact, args.artifact.read_bytes(), load_json(args.view_spec))
                    else:
                        rendered = render_reading_view(artifact, args.artifact.read_bytes(), load_json(args.view_spec))
                else:
                    if args.view_spec is not None:
                        parser.error('--view-spec requires --format html')
                    rendered = v2.render_markdown(artifact) if semantic_v2 else render_projection(artifact) if semantic else render_markdown(artifact)
                reused = write_new(args.output, rendered, reuse_equal=semantic_v2)
                result = {'output': str(args.output), 'revision_id': artifact['revision']['id'], 'reused': reused} if semantic_v2 else {'output': str(args.output), 'revision_id': artifact['revision']['id']}
            elif args.command == 'draft-import':
                drafts = load_json(args.drafts)
                if semantic_v2:
                    v2.validate_drafts(artifact, drafts, root=args.root)
                elif semantic:
                    validate_semantic_drafts(artifact, drafts, root=args.root)
                else:
                    validate_drafts(artifact, drafts)
                reused = write_new(args.output, drafts, reuse_equal=semantic_v2)
                result = {'output': str(args.output), 'proposal_count': len(drafts['proposals']), 'reused': reused} if semantic_v2 else {'output': str(args.output), 'proposal_count': len(drafts['proposals'])}
            elif args.command == 'review':
                drafts = load_json(args.drafts)
                ledger = load_json(args.output) if semantic_v2 and args.output.exists() else load_json(args.ledger) if args.ledger.exists() else v2.new_review_ledger(artifact, drafts) if semantic_v2 else new_semantic_ledger(drafts) if semantic else new_review_ledger(drafts)
                if semantic_v2:
                    if not args.source_checks:
                        parser.error('V2 review needs --source-checks')
                    later = v2.record_decision(artifact, drafts, ledger, load_json(args.source_checks), proposal_id=args.proposal_id,
                                               decision=args.decision, reviewer=args.reviewer, checked_anchors=args.checked_anchor,
                                               checked_regions=args.checked_region, check_method=args.check_method, note=args.note, root=args.root)
                elif semantic:
                    later = record_semantic_decision(artifact, drafts, ledger, proposal_id=args.proposal_id, decision=args.decision,
                                                     reviewer=args.reviewer, checked_anchors=args.checked_anchor, note=args.note, check_method=args.check_method, root=args.root)
                else:
                    later = record_decision(artifact, drafts, ledger, proposal_id=args.proposal_id, decision=args.decision,
                                            reviewer=args.reviewer, checked_locators=args.checked_locator, note=args.note, check_method=args.check_method)
                reused = write_new(args.output, later, reuse_equal=semantic_v2)
                result = {'output': str(args.output), 'decision_count': len(later['decisions']), 'reused': reused} if semantic_v2 else {'output': str(args.output), 'decision_count': len(later['decisions'])}
            elif args.command == 'publish':
                if semantic_v2:
                    if not args.source_checks:
                        parser.error('V2 publish needs --source-checks')
                    later = v2.publish(artifact, load_json(args.drafts), load_json(args.ledger), load_json(args.source_checks), revision_id=args.revision_id, root=args.root)
                else:
                    later = publish_semantic(artifact, load_json(args.drafts), load_json(args.ledger), revision_id=args.revision_id, root=args.root) if semantic else publish(artifact, load_json(args.drafts), load_json(args.ledger), revision_id=args.revision_id, root=args.root)
                reused = write_new(args.output, later, reuse_equal=semantic_v2)
                result = {'output': str(args.output), 'revision_id': later['revision']['id'], 'reused': reused} if semantic_v2 else {'output': str(args.output), 'revision_id': later['revision']['id']}
            else:
                commit = args.producer_commit
                if not commit:
                    git_root = Path(__file__).resolve().parents[2]
                    completed = subprocess.run(['git', '-C', str(git_root), 'rev-parse', 'HEAD'], capture_output=True, text=True)
                    commit = completed.stdout.strip() if completed.returncode == 0 else ''
                result = v2.export(artifact, root=args.root, producer_commit=commit) if semantic_v2 else export_projection(artifact, root=args.root, producer_commit=commit) if semantic else export_manifest(artifact, root=args.root, producer_commit=commit)
                reused = write_new(args.output, result, reuse_equal=semantic_v2)
                result = {'output': str(args.output), 'producer': result['producer'], 'reused': reused} if semantic_v2 else {'output': str(args.output), 'producer': result['producer']}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (KnowledgeFoundationError, FileExistsError, OSError) as exc:
        print(f'skm: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
