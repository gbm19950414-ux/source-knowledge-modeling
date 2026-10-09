"""Original synthetic public lifecycle; no book or private evaluation material.

The fixture declarations are simulated reviewer inputs, never a claim of real
independent semantic review. Only mechanical task/event savings are measured.
"""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import time
import pytest

from source_knowledge_modeling import semantic_v3 as v3, semantic_workflow as workflow
from source_knowledge_modeling import semantic_dependency_cert as cert
from source_knowledge_modeling.core import load_json, KnowledgeFoundationError
from source_knowledge_modeling.ingest import ingest, TextAdapter


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(workflow.json_bytes(value))


def cli(root, *args, expect=0):
    command = [sys.executable, '-m', 'source_knowledge_modeling.cli', *map(str,args), '--root', str(root)]
    started = time.monotonic()
    result = subprocess.run(command, capture_output=True, text=True)
    elapsed = time.monotonic() - started
    log = root / 'command-evidence.jsonl'
    with log.open('a') as out:
        out.write(json.dumps({'command':command,'seconds':elapsed,'exit':result.returncode,
                              'stdout':result.stdout,'stderr':result.stderr})+'\n')
    assert result.returncode == expect, result.stdout + result.stderr
    return result


def assess_inputs(run, candidate, stamp='old'):
    judgments = []
    for (kind, ident), item in cert.targets(candidate).items():
        check = {'target_kind':kind,'target_id':ident,'verdict':'pass',
                 'reason':f'Synthetic {stamp} source meaning check for {ident}; current frozen source and action-A change/dependency closure specifically rechecked.'}
        if kind in ('element','relation'):
            check['content_revision'] = item['content_revision']
        judgments.append(check)
    return {'schema_version':workflow.ASSESSMENT_VERSION,'draft_sha256':v3.digest(load_json(run/'checked-drafts.json')),
            'inventory_sha256':workflow.file_hash(run/'source-information.json'),
            'discourse_sha256':workflow.file_hash(run/'discourse-audit.json'),
            'reviewer':'synthetic '+stamp+' independent reviewer','reviewer_type':'human','model':'unknown',
            'check_method':'source_text','checked_at':'2026-10-05T00:00:00Z' if stamp=='old' else '2026-10-05T00:01:00Z',
            'checks':judgments}


def accept(root, run, revision, checks_path):
    drafts = load_json(run/'checked-drafts.json')
    anchors = sorted({v3.anchor_key(a) for p in drafts['proposals'] for key in ('upsert_elements','upsert_relations') for x in p[key] for a in x['anchors']})
    flags = [value for key in anchors for value in ('--checked-anchor',key)]
    ledger = run/'ledger-initial.json'
    for n, p in enumerate(drafts['proposals']):
        out = run/'reviews'/f'{n}.json'
        cli(root,'review',run/'base.json','--drafts',run/'checked-drafts.json','--ledger',ledger,
            '--source-checks',checks_path,'--assessment',run/'quality.json','--proposal-id',p['id'],
            '--decision','accept','--reviewer','synthetic acceptance reviewer','--check-method','source_text',
            *flags,'--output',out,'--run-dir',run)
        ledger = out
    cli(root,'publish',run/'base.json','--drafts',run/'checked-drafts.json','--ledger',ledger,
        '--source-checks',checks_path,'--assessment',run/'quality.json','--revision-id',revision,
        '--output',run/'published.json','--run-dir',run)
    return load_json(run/'published.json')


@pytest.fixture(scope='module')
def prior(tmp_path_factory):
    root = tmp_path_factory.mktemp('dependency-original-synthetic')
    source = root/'source.txt'
    source.write_text('If green, inspect panel A.\nIf blue, inspect panel B.\n')
    foundation, _ = ingest(source, root=root, workspace=root/'source-run', adapter=TextAdapter())
    scope = root/'scope.json'
    write(scope,{'included_locators':['line:1','line:2'],'description':'Two independent synthetic operating statements'})
    run = root/'prior'
    cli(root,'prepare',foundation,'--profile','instructional-source@3.0.0','--scope',scope,'--output-dir',run)
    base = load_json(run/'base.json')
    elements, relations, info, discourse, inventory, discourse_inventory, packages = [], [], [], [], [], [], []
    for n, group in enumerate(('A','B'),1):
        quote = f'If {"green" if group=="A" else "blue"}, inspect panel {group}.'
        anchor = {'locator':f'line:{n}','quote':quote}
        elements.extend([{'id':'condition-'+group,'kind':'condition','text':quote.split(',')[0],
                          'origin_type':'source_claim','modality':'asserted','anchors':[anchor]},
                         {'id':'action-'+group,'kind':'action','text':'Inspect panel '+group,
                          'origin_type':'source_claim','modality':'asserted','anchors':[anchor]}])
        relations.append({'id':'relation-'+group,'type':'conditions','from_id':'condition-'+group,
                          'to_id':'action-'+group,'origin_type':'source_claim','basis':'explicit','anchors':[anchor]})
        packages.append({'id':'package-'+group,'summary':'Synthetic group '+group,'origin_type':'source_claim',
                         'modality':'asserted','element_ids':['condition-'+group,'action-'+group],
                         'relation_ids':['relation-'+group],'context_ids':[],'legacy_review':None})
        for prefix, collection, independent, bindings in (
            ('meaning-',info,inventory,[{'object_id':'action-'+group,'field_path':'/text'},
                                       {'object_id':'condition-'+group,'field_path':'/text'}]),
            ('connection-',discourse,discourse_inventory,[{'object_id':'relation-'+group,'field_path':'/type'}])):
            meaning = f'Synthetic source statement {group} '+ ('conditional instruction' if prefix=='meaning-' else 'explicit condition relation')
            collection.append({'id':prefix+group,'anchor':anchor,'meaning':meaning,'status':'modeled',
                               'bindings':bindings,'reason':'Original synthetic fixture'})
            independent.append({'id':prefix+group,'meaning':meaning,'status':'required','critical':True,
                                'anchors':[anchor],'reason':'Source-first synthetic fixture'})
    identity = {k:base['source'][k] for k in ('sha256','snapshot_sha256')}
    for name, version, rows in (('source-information.json',workflow.INFORMATION_VERSION,inventory),
                                ('discourse-audit.json',workflow.DISCOURSE_VERSION,discourse_inventory)):
        write(run/name,{'schema_version':version,'source_sha256':identity['sha256'],'snapshot_sha256':identity['snapshot_sha256'],
                        'reviewer':'synthetic source reviewer','checked_at':'2026-10-05T00:00:00Z','items':rows})
    write(run/'drafts.json',{'schema_version':v3.DRAFT_VERSION,'artifact_id':base['artifact_id'],
          'base_revision_id':base['revision']['id'],'proposals':[{'id':'initial','reason':'Original synthetic lifecycle',
          'rule_ids':['IS3-03'],'upsert_elements':elements,'upsert_relations':relations,'upsert_packages':packages,
          'upsert_information':info,'upsert_discourse':discourse}]})
    cli(root,'draft-import',run/'base.json','--drafts',run/'drafts.json','--output',run/'checked-drafts.json','--run-dir',run)
    candidate = load_json(run/'candidate.json')
    write(run/'semantic-assessment.json',assess_inputs(run,candidate))
    cli(root,'assess',run,'--output',run/'quality.json')
    anchors = [v3.resolve_anchor(base,{'locator':f'line:{n}','quote':line},root=root)
               for n,line in enumerate(source.read_text().splitlines(),1)]
    write(run/'anchors.json',anchors)
    cli(root,'source-check',run/'base.json','--ledger',run/'source-checks.json','--anchors',run/'anchors.json',
        '--check-id','original-check','--reviewer','synthetic source reviewer','--check-method','source_text',
        '--output',run/'source-checks.json','--run-dir',run)
    accepted = accept(root,run,'accepted-original',run/'source-checks.json')
    return root, foundation, scope, run, accepted


def authored_object(item):
    return {k:deepcopy(v) for k,v in item.items() if k not in ('content_revision','review_state','review_id','source_check_ids','legacy_review_package_id')}


def delta_workspace(prior, name='current', all_fresh=False):
    root, foundation, scope, oldrun, base = prior
    run = root/name
    cli(root,'prepare',foundation,'--profile','instructional-source@3.0.0','--scope',scope,
        '--existing',oldrun/'published.json','--output-dir',run)
    for file in ('source-information.json','discourse-audit.json'):
        (run/file).write_bytes((oldrun/file).read_bytes())
    changed = deepcopy(base)
    next(x for x in changed['elements'] if x['id']=='action-A')['text'] = 'Check panel A'
    groups = ('A','B') if all_fresh else ('A',)
    es = [authored_object(x) for x in changed['elements'] if x['id'][-1] in groups]
    rs = [authored_object(x) for x in changed['relations'] if x['id'][-1] in groups]
    p = {'id':'delta-'+name,'reason':'Synthetic bounded paraphrase of existing instruction','rule_ids':['IS3-03'],
         'upsert_elements':es,'upsert_relations':rs,
         'upsert_information':[deepcopy(x) for x in base['information'] if x['id'][-1] in groups],
         'upsert_discourse':[deepcopy(x) for x in base['discourse'] if x['id'][-1] in groups],
         'lineage':[{'old_kind':'element','old_id':'action-A','new_kind':'element','new_ids':['action-A'],
                     'change':'replaced','reason':'Synthetic paraphrase'},
                    {'old_kind':'relation','old_id':'relation-A','new_kind':'relation','new_ids':['relation-A'],
                     'change':'replaced','reason':'Exact endpoint revision synchronization'}]}
    write(run/'drafts.json',{'schema_version':v3.DRAFT_VERSION,'artifact_id':base['artifact_id'],
                           'base_revision_id':base['revision']['id'],'proposals':[p]})
    cli(root,'draft-import',run/'base.json','--drafts',run/'drafts.json','--output',run/'checked-drafts.json','--run-dir',run)
    return run, load_json(run/'candidate.json')


def certificate(prior, candidate):
    root,_,_,oldrun,base = prior
    proof = cert.verify_prior_run(oldrun,base,root=root)
    statement = {'status':'complete','reviewer':{'actor':'synthetic dependency inspector','type':'human','model':'unknown',
        'method':'source_text','checked_at':'2026-10-05T00:00:30Z'},'covered_locators':['line:1','line:2'],
        'dimensions':{d:{'checked_scope':['line:1','line:2'],'reason':'Synthetic dimension inspection: '+d} for d in cert.DIMENSIONS}}
    objects = {x['id']:(kind,x) for kind,plural in cert.KINDS[:2] for x in base[plural]}
    mandatory = cert.mandatory_edges(base)
    return {'schema_version':cert.CERT_VERSION,'binding':cert.base_binding(base),
            'candidate_sha256':cert.semantic_state(candidate),
            'prior':{k:proof[k] for k in ('manifest_sha256','quality_sha256','assessment_sha256','inventory_sha256',
                     'discourse_sha256','published_sha256','producer_sha256')},
            'scope_inspection':deepcopy(statement),'delta_inspection':deepcopy(statement),
            'objects':[{'id':ident,'facts':cert.object_facts(base,kind,item),'inspection':deepcopy(statement),
                        'dependencies':[{'object_id':dep,'kind':objects[dep][0],'content_revision':objects[dep][1]['content_revision'],
                          'relation':'synthetic explicit mechanical dependency','reason':'Synthetic exact structural dependency',
                          'evidence_locators':['line:1','line:2']} for dep in sorted(mandatory[ident])]}
                       for ident,(kind,item) in objects.items()]}


def prepare_reuse(prior, run, candidate):
    root,_,_,oldrun,_ = prior
    path=run/'provided-certificate.json';write(path,certificate(prior,candidate))
    cli(root,'assessment-reuse',run,'--from-run',oldrun,'--from-projection',oldrun/'published.json',
        '--dependency-certificate',path)


def supply_fresh(prior, run, candidate):
    review=load_json(run/'semantic-assessment.json')
    fresh=assess_inputs(run,candidate,'fresh')
    supplied={(x['target_kind'],x['target_id']):x for x in fresh['checks']}
    for row in review['checks']:
        if row['verdict']=='pending':
            row.update(supplied[(row['target_kind'],row['target_id'])])
    for k in ('reviewer','reviewer_type','model','check_method','checked_at'):
        review[k]=fresh[k]
    write(run/'semantic-assessment.json',review)
    cli(prior[0],'assessment-reuse',run,'--seal')


def test_public_partial_lifecycle_and_zero_event_replay(prior):
    root,_,_,oldrun,base = prior
    baseline, fullcandidate=delta_workspace(prior,'baseline',all_fresh=True)
    write(baseline/'semantic-assessment.json',assess_inputs(baseline,fullcandidate,'baseline'))
    cli(root,'assess',baseline,'--output',baseline/'quality.json')
    fullpublished=accept(root,baseline,'baseline-published',oldrun/'source-checks.json')
    run,candidate=delta_workspace(prior,'partial')
    prepare_reuse(prior,run,candidate)
    plan=load_json(run/'review-plan.json')
    assert plan['counts']=={'total':10,'fresh':5,'reused':5}
    assert sum(x['verdict']=='pending' for x in load_json(run/'semantic-assessment.json')['checks'])==5
    with pytest.raises(KnowledgeFoundationError):
        workflow.assess_workspace(run,root=root)
    supply_fresh(prior,run,candidate)
    cli(root,'assess',run,'--output',run/'quality.json')
    published=accept(root,run,'partial-published',run/'source-checks-reused.json')
    before={x['id']:x for x in base['elements']+base['relations']}
    after={x['id']:x for x in published['elements']+published['relations']}
    for ident in ('action-B','condition-B','relation-B'):
        assert after[ident]==before[ident]
    oldchecks={(x['target_kind'],x['target_id']):x for x in load_json(oldrun/'semantic-assessment.json')['checks']}
    checks={(x['target_kind'],x['target_id']):x for x in load_json(run/'semantic-assessment.json')['checks']}
    for row in load_json(run/'reuse-provenance.json')['targets']:
        if row['mode']=='reused':
            key=row['target_kind'],row['target_id'];assert checks[key]==oldchecks[key]
    assert published['source_checks']==base['source_checks']
    assert len(published['reviews'])-len(base['reviews'])==1
    assert sorted(x['content_revision'] for x in published['elements']+published['relations'])==sorted(x['content_revision'] for x in fullpublished['elements']+fullpublished['relations'])
    cli(root,'impact',run/'base.json','--new',run/'published.json','--dependency-certificate',run/'dependency-certificate.json',
        '--prior-run',run/'prior-evidence','--output',run/'certified-impact.json','--run-dir',run)
    assert len(load_json(run/'certified-impact.json')['recheck_object_ids'])==3
    replay=root/'partial-replay' 
    cli(root,'replay',run/'run-manifest.json','--output-dir',replay)
    assert (replay/'published.json').read_bytes()==(run/'published.json').read_bytes()
    assert (replay/'reuse-provenance.json').read_bytes()==(run/'reuse-provenance.json').read_bytes()
    # Retry identical frozen replay has no new judgment events.
    cli(root,'replay',run/'run-manifest.json','--output-dir',replay)
    report=workflow.replay_run(run/'run-manifest.json',root=root,output_dir=root/'partial-replay-2')
    assert report['new_review_events']==0
    metrics={'total_targets':10,'baseline_fresh_targets':10,'certified_fresh_targets':5,'reused_targets':5,
        'baseline_fresh_objects':6,'certified_fresh_objects':3,'new_source_events':0,
        'baseline_new_acceptance_events':len(fullpublished['reviews'])-len(base['reviews']),
        'certified_new_acceptance_events':len(published['reviews'])-len(base['reviews']),
        'baseline_evidence_locators':2,'certified_evidence_locators':1,
        'baseline_prepublication_cli_commands':5,'certified_prepublication_cli_commands':7,
        'baseline_objects_with_new_acceptance_identity':6,'certified_objects_with_new_acceptance_identity':3,
        'certificate_dimension_statement_count':56,
        'baseline_evidence_characters':sum(len(x['anchors'][0]['quote']) for x in fullcandidate['elements'][::2]),
        'certified_evidence_characters':len(candidate['elements'][0]['anchors'][0]['quote']),
        'certificate_initial_inspection_cost':'unknown','tokens':'unknown','AI_time':'unknown',
        'note':'Simulated synthetic reviewer inputs; measured public mechanical task requests and events only.'}
    write(root/'metrics.json',metrics)


@pytest.mark.parametrize('mutation',['unknown','missing-object','missing-edge','wrong-hash','scope','coverage','audit-meaning','audit-status','audit-critical','audit-binding-field'])
def test_certificate_negatives_and_fullscope(prior,mutation):
    root,_,_,oldrun,base=prior
    run,candidate=delta_workspace(prior,'negative-'+mutation)
    value=certificate(prior,candidate)
    if mutation=='unknown':value['objects'][0]['inspection']['status']='unknown'
    elif mutation=='missing-object':value['objects'].pop()
    elif mutation=='missing-edge':value['objects'][0]['dependencies']=[]
    elif mutation=='wrong-hash':value['prior']['assessment_sha256']='0'*64
    elif mutation=='scope':candidate['scope']['description']+=' changed'
    elif mutation=='coverage':candidate['information'].pop()
    elif mutation.startswith('audit-'):
        key={'audit-meaning':'meaning','audit-status':'status','audit-critical':'critical','audit-binding-field':'field_path'}[mutation]
        if key=='field_path':candidate['information'][0]['bindings'][0][key]='/modality'
        else:candidate['information'][0][key]='changed'
    try:
        verified=cert.verify_certificate(base,value,root=root,prior_run=oldrun)
    except KnowledgeFoundationError:
        assert mutation in ('missing-object','missing-edge','wrong-hash')
    else:
        plan=cert.plan_delta(base,candidate,verified)
        assert plan['mode']=='full_scope' and plan['counts']['reused']==0
    assert set(v3._review_scope(base,candidate)[0])=={x['id'] for x in candidate['elements']+candidate['relations']}


@pytest.mark.parametrize('mutation',['removed-sidecar','affected-reused','omitted','extra','tampered-old-check','fresh-old-judgment','prior-fake-pass'])
def test_reuse_sidecar_cannot_bypass_actual_prior_and_fresh_partition(prior,mutation):
    root,_,_,_,base=prior
    run,candidate=delta_workspace(prior,'proof-negative-'+mutation)
    prepare_reuse(prior,run,candidate)
    if mutation=='fresh-old-judgment':
        review=load_json(run/'semantic-assessment.json');old=load_json(run/'prior-evidence/semantic-assessment.json')
        oldmap={(x['target_kind'],x['target_id']):x for x in old['checks']}
        review['reviewer']='synthetic new reviewer';review['checked_at']='2026-10-05T00:02:00Z'
        for row in review['checks']:
            if row['verdict']=='pending':row.update(oldmap[(row['target_kind'],row['target_id'])])
        write(run/'semantic-assessment.json',review)
        with pytest.raises(KnowledgeFoundationError):cert.seal_assessment_reuse(run,root=root)
        return
    supply_fresh(prior,run,candidate)
    provenance=load_json(run/'reuse-provenance.json')
    if mutation=='removed-sidecar':(run/'reuse-provenance.json').unlink()
    elif mutation=='affected-reused':next(x for x in provenance['targets'] if x['mode']=='fresh-required')['mode']='reused'
    elif mutation=='omitted':provenance['targets'].pop()
    elif mutation=='extra':provenance['targets'].append(deepcopy(provenance['targets'][0]))
    elif mutation=='tampered-old-check':
        review=load_json(run/'semantic-assessment.json');next(x for x in review['checks'] if x['target_id']=='action-B')['reason']='invented carry forward'
        write(run/'semantic-assessment.json',review)
    elif mutation=='prior-fake-pass':
        priorreview=load_json(run/'prior-evidence/semantic-assessment.json');priorreview['checks'][0]['reason']='forged'
        write(run/'prior-evidence/semantic-assessment.json',priorreview)
    if mutation not in ('removed-sidecar','tampered-old-check','prior-fake-pass'):write(run/'reuse-provenance.json',provenance)
    with pytest.raises((KnowledgeFoundationError,FileNotFoundError)):
        workflow.assess_workspace(run,root=root)


def test_runtime_certificate_and_transitive_unchanged_middle(prior):
    root,_,_,oldrun,base=prior
    run,candidate=delta_workspace(prior,'transitive')
    value=certificate(prior,candidate)
    # Extra conservative semantic edge: B condition depends on A condition.
    row=next(x for x in value['objects'] if x['id']=='condition-B')
    obj=next(x for x in base['elements'] if x['id']=='condition-A')
    row['dependencies'].append({'object_id':'condition-A','kind':'element','content_revision':obj['content_revision'],
        'relation':'synthetic additional premise','reason':'Declared synthetic premise scope', 'evidence_locators':['line:1','line:2']})
    verified=cert.verify_certificate(base,value,root=root,prior_run=oldrun)
    plan=cert.plan_delta(base,candidate,verified)
    assert 'condition-B' in plan['affected_object_ids'] and 'action-B' in plan['affected_object_ids']
    assert next(x for x in candidate['elements'] if x['id']=='condition-B')['content_revision']==next(x for x in base['elements'] if x['id']=='condition-B')['content_revision']
    assert set(v3._review_scope(base,candidate,{'verified':True})[0])=={x['id'] for x in candidate['elements']+candidate['relations']}
    verified.certificate['objects'][0]['dependencies']=[]
    with pytest.raises(KnowledgeFoundationError):cert.plan_delta(base,candidate,verified)


def test_public_invalid_certificate_impact_remains_full_scope(prior):
    root,_,_,oldrun,base=prior
    run,candidate=delta_workspace(prior,'impact-invalid')
    candidate['revision']={'id':'impact-delta','parent_id':base['revision']['id'],'note':'Synthetic pending delta'}
    write(run/'impact-candidate.json',candidate)
    value=certificate(prior,candidate);value['objects'].pop()
    write(run/'invalid-certificate.json',value)
    cli(root,'impact',run/'base.json','--new',run/'impact-candidate.json','--dependency-certificate',run/'invalid-certificate.json',
        '--prior-run',oldrun,'--output',run/'impact-fallback.json')
    impact=load_json(run/'impact-fallback.json')
    assert len(impact['recheck_object_ids'])==6 and 'certificate_fallback_reason' in impact


@pytest.mark.parametrize('mutation',['producer','profile','guide'])
def test_prior_self_attested_identity_cannot_enable_reuse(prior,mutation):
    import shutil
    root,_,_,oldrun,base=prior
    copied=root/('forged-identity-'+mutation);shutil.copytree(oldrun,copied)
    manifest=load_json(copied/'run-manifest.json')
    if mutation=='producer':
        manifest['tool']['implementation_sha256']='0'*64
        manifest['inputs']['tool_sha256']='0'*64
    elif mutation=='profile':manifest['profile']['definition_sha256']='0'*64
    else:manifest['inputs']['guide_sha256']='0'*64
    write(copied/'run-manifest.json',manifest)
    with pytest.raises(KnowledgeFoundationError):cert.verify_prior_run(copied,base,root=root)


def test_fresh_old_reason_with_only_revision_and_actor_sync_is_rejected(prior):
    root,_,_,_,_=prior
    run,candidate=delta_workspace(prior,'mechanical-fresh-copy')
    prepare_reuse(prior,run,candidate)
    review=load_json(run/'semantic-assessment.json')
    old=load_json(run/'prior-evidence/semantic-assessment.json')
    oldmap={(x['target_kind'],x['target_id']):x for x in old['checks']}
    supplied=assess_inputs(run,candidate,'fresh')
    newmap={(x['target_kind'],x['target_id']):x for x in supplied['checks']}
    for row in review['checks']:
        if row['verdict']=='pending':row.update(newmap[(row['target_kind'],row['target_id'])])
    stale=next(x for x in review['checks'] if x['target_id']=='action-A')
    stale['reason']=oldmap[('element','action-A')]['reason']
    for k in ('reviewer','reviewer_type','model','check_method','checked_at'):review[k]=supplied[k]
    write(run/'semantic-assessment.json',review)
    with pytest.raises(KnowledgeFoundationError,match='fresh-required'):
        cert.seal_assessment_reuse(run,root=root)


@pytest.mark.parametrize('mutation',['all-unrelated','covered-only','dimension-only','outside-scope'])
def test_object_inspection_must_cover_its_own_exact_evidence_locators(prior,mutation):
    root,_,_,oldrun,base=prior
    run,candidate=delta_workspace(prior,'own-evidence-'+mutation)
    value=certificate(prior,candidate)
    inspection=next(x for x in value['objects'] if x['id']=='action-A')['inspection']
    assert next(x for x in base['elements'] if x['id']=='action-A')['anchors'][0]['locator']=='line:1'
    if mutation in ('all-unrelated','covered-only'):inspection['covered_locators']=['line:2']
    if mutation=='all-unrelated':
        for row in inspection['dimensions'].values():row['checked_scope']=['line:2']
    if mutation=='dimension-only':inspection['dimensions']['claim_voice']['checked_scope']=['line:2']
    if mutation=='outside-scope':inspection['covered_locators'].append('line:999')
    with pytest.raises(KnowledgeFoundationError):cert.verify_certificate(base,value,root=root,prior_run=oldrun)
