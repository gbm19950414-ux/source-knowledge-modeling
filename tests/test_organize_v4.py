"""Public v4 workflow and precise higher relation contracts."""
from copy import deepcopy
from pathlib import Path
import hashlib
import shutil
import shlex

import pytest

from source_knowledge_modeling import organize as o
from source_knowledge_modeling.cli import main
from source_knowledge_modeling.core import load_json

ROOT=Path(__file__).resolve().parents[1]
EXPORT=ROOT/'contracts/semantic-discourse/v1/examples/garden.export.json'


def test_v4_explicit_chapter_preserves_preamble_with_sections(tmp_path):
    from test_organize_v3 import source_map
    directory=source_map()
    paragraphs=directory['paragraphs']
    directory['containers'][0]['range']={
        'start':{'paragraph_id':paragraphs[0]['id'],'offset':0},
        'end':{'paragraph_id':paragraphs[-1]['id'],'offset':len(paragraphs[-1]['text'])}}
    directory['containers'][1]['range']['start']={'paragraph_id':'line:3','offset':0}
    o.write(tmp_path/'map.json',directory)
    run=tmp_path/'run'
    assert main(['organize','prepare',str(EXPORT),'--format-version','4','--root',str(ROOT),
                 '--document-map',str(tmp_path/'map.json'),'--output-dir',str(run)])==0
    draft=load_json(run/'draft.json');draft['author']='synthetic author'
    o.write(run/'draft.json',draft,mutable=True);review(run)
    assert main(['organize','publish',str(run)])==0
    release,=(run/'releases').iterdir()
    reading=load_json(release/'reading-map.json')
    assert next(x for x in reading['occurrences'] if x['object_id']=='element:check')['parent_id']=='chapter:garden'
    assert next(x for x in reading['occurrences'] if x['object_id']=='element:dry')['parent_id']=='section:garden'
    assert load_json(release/'quality.json')['scope']['selected_elements']==4


def test_v4_nontext_source_unit_is_preserved_without_fabricated_paragraph(tmp_path):
    source=load_json(EXPORT)
    for key in ['path','snapshot_path']:
        destination=tmp_path/source['source'][key]
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/source['source'][key],destination)
    snapshot_path=tmp_path/source['source']['snapshot_path']
    snapshot=load_json(snapshot_path)
    snapshot['units'].append({'locator':'image:1','text':''})
    o.write(snapshot_path,snapshot,mutable=True)
    source['source']['snapshot_sha256']=hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    for check in source['source_checks']:
        check['snapshot_sha256']=source['source']['snapshot_sha256']
    source['source_units'].append({'locator':'image:1','order':5,'scope_role':'primary'})
    source['scope']['included_locators'].append('image:1')
    source['screening'].append({'locator':'image:1','status':'unmodeled','reason':'Synthetic nontext source unit.'})
    o.write(tmp_path/'export.json',source)
    run=tmp_path/'run'
    assert main(['organize','prepare',str(tmp_path/'export.json'),'--format-version','4',
                 '--root',str(tmp_path),'--output-dir',str(run)])==0
    draft=load_json(run/'draft.json');draft['author']='synthetic author'
    o.write(run/'draft.json',draft,mutable=True);review(run)
    assert main(['organize','publish',str(run)])==0
    release,=(run/'releases').iterdir()
    assert any(x['locator']=='image:1' and x['text']=='' for x in load_json(release/'document-map.json')['paragraphs'])
    assert any(x['locator']=='image:1' for x in load_json(release/'source.json')['source_units'])
    assert all(x['text'] for x in load_json(release/'reading-map.json')['paragraphs'])
    assert len(load_json(release/'model.json')['reading_order'])==4


def prepared(tmp_path, requirements=None):
    run=tmp_path/'run'
    args=['organize','prepare',str(EXPORT),'--root',str(ROOT),'--output-dir',str(run),'--format-version','4']
    if requirements is not None:
        o.write(tmp_path/'requirements.json',requirements)
        args+=['--requirements',str(tmp_path/'requirements.json')]
    assert main(args)==0
    draft=load_json(run/'draft.json');draft['author']='garden author'
    o.write(run/'draft.json',draft,mutable=True)
    return run


def populate(run):
    draft=load_json(run/'draft.json')
    for i,ident in enumerate(('check','dry','water','unknown-volume')):
        draft['blocks'].append({'id':'block:'+ident,'parent_id':draft['root_id'],'title':ident,'order':i,
            'element_ids':[ident],'content_types':['plan_process' if ident in {'check','water'} else 'parameter_constraint'],
            'focus_refs':[{'object_id':ident,'field_path':'/text'}]})
    qualifier={'applies_to':'relation','dimension':'condition','source_ref':{'object_id':'dry','field_path':'/text'}}
    draft['relation_bindings']=[{'source_relation_id':'dry-conditions-water','qualifiers':[qualifier]}]
    common={'direction':'directed','premises':[{'object_id':'dry','field_path':'/text'}]}
    draft['edges']=[
        {'id':'higher:native','relation_form':'source_native','type':'precedes','from_id':'block:check','to_id':'block:dry',
         'from_element_ids':['check'],'to_element_ids':['dry'],'origin':'source','basis':'inferred_from_source',
         'scope_note':'Only the check and its possible finding.',**common},
        {'id':'higher:rollup','relation_form':'source_rollup','type':'conditions','from_id':'block:dry','to_id':'block:water',
         'from_element_ids':['dry'],'to_element_ids':['water'],'origin':'source','basis':'source_rollup',
         'scope_note':'The stated dry-soil condition only.','source_relation_ids':['dry-conditions-water'],
         'qualifiers':[qualifier],**common},
        {'id':'higher:inference','relation_form':'project_inference','type':'elaborates','from_id':'block:check','to_id':'block:water',
         'from_element_ids':['check'],'to_element_ids':['water'],'origin':'interpretation','basis':'project_interpretation',
         'overall_effect':'The check and action form a decision path.','rationale':'The check supplies the conditional result.',
         'scope_note':'Only these two actions; no watering volume is inferred.',**common}]
    o.write(run/'draft.json',draft,mutable=True)
    return draft


def review(run):
    assert main(['organize','assess',str(run)])==1
    template=load_json(run/'review-template.json')
    template.update(reviewer='independent garden reviewer',reviewer_type='synthetic',checked_at='2026-09-29T00:00:00Z',model='unknown')
    for item in template['judgments']:item.update(verdict='pass',reason='Checked against the synthetic source and intended scope.')
    o.write(run/'review.json',template,mutable=True)


def test_v4_public_workflow_three_forms_query_and_replay(tmp_path):
    requirements={'requirements':[{'id':'R1','claim':'Condition local'}],
                  'cross_block_requirements':[{'id':'X1','claim':'No invented pair'}],
                  'ambiguities':[{'id':'A1','claim':'Volume unknown'}]}
    run=prepared(tmp_path,requirements)
    populate(run)
    review(run)
    targets={x['target_id'] for x in load_json(run/'review-template.json')['judgments']}
    assert {'requirement:R1','requirement:cross_block_requirements:X1','requirement:ambiguities:A1',
            'binding:dry-conditions-water'}<=targets
    assert main(['organize','publish',str(run)])==0
    release,=(run/'releases').iterdir()
    runbook=(run/'RUNBOOK.md').read_text()
    assert 'skm organize prepare '+shlex.quote(str(EXPORT)) in runbook
    assert 'skm organize query '+shlex.quote(str(release)) in runbook
    assert 'skm organize reuse-review '+shlex.quote(str(run)) in runbook
    assert '--reviewer "$WB_REVIEWER" --decision updated --note "$WB_NOTE"' in runbook
    domain=load_json(release/'model.json')
    assert domain['schema_version']=='diagram-domain-model/v5'
    assert domain['content_types']['schema_version']=='semantic-content-types/v1'
    assert next(x for x in domain['nodes'] if x['id']=='block:check')['focus_refs'][0]['value']
    edges={x['id']:x for x in domain['edges']}
    assert edges['higher:native']['relation_form']=='source_native'
    assert 'supporting_edge_ids' not in edges['higher:native']
    assert edges['higher:rollup']['endpoint_pairs'][0]['from']=='element:dry'
    assert edges['higher:rollup']['endpoint_pairs'][0]['to']=='element:water'
    assert edges['higher:inference']['origin']=='interpretation'
    assert 'uncertainty' not in edges['higher:inference']
    assert edges['relation:dry-conditions-water']['qualifiers'][0]['source_ref']['value']
    assert main(['organize','query',str(release),'--id','higher:rollup','--output',str(tmp_path/'query.json')])==0
    assert load_json(tmp_path/'query.json')['object']['endpoint_pairs']==edges['higher:rollup']['endpoint_pairs']
    before={str(p.relative_to(release)):p.read_bytes() for p in release.rglob('*') if p.is_file()}
    assert main(['organize','replay',str(release/'manifest.json'),'--output-dir',str(tmp_path/'clean')])==0
    rebuilt,=(tmp_path/'clean'/'releases').iterdir()
    assert before=={str(p.relative_to(rebuilt)):p.read_bytes() for p in rebuilt.rglob('*') if p.is_file()}


@pytest.mark.parametrize('mutation',[
    'unknown-type','focus-value','empty-focus','duplicate-source','native-support','native-origin','rollup-empty',
    'rollup-wrong-type','rollup-wrong-members','rollup-origin','inference-basis','inference-no-effect',
    'inference-support','stale-qualifier','wrong-qualifier-value','binding-copies-type'])
def test_v4_rejects_invalid_branches_and_refs(tmp_path,mutation):
    run=prepared(tmp_path);draft=populate(run)
    native,rollup,inference=draft['edges']
    if mutation=='unknown-type':draft['blocks'][0]['content_types']=['invented']
    elif mutation=='focus-value':draft['blocks'][0]['focus_refs'][0]['value']='copy'
    elif mutation=='empty-focus':draft['blocks'][0]['focus_refs']=[]
    elif mutation=='duplicate-source':native.update(type='conditions',from_id='block:dry',to_id='block:water',from_element_ids=['dry'],to_element_ids=['water'])
    elif mutation=='native-support':native['source_relation_ids']=['dry-conditions-water']
    elif mutation=='native-origin':native['origin']='interpretation'
    elif mutation=='rollup-empty':rollup['source_relation_ids']=[]
    elif mutation=='rollup-wrong-type':rollup['type']='supports'
    elif mutation=='rollup-wrong-members':rollup['from_element_ids']=['check']
    elif mutation=='rollup-origin':rollup['origin']='interpretation'
    elif mutation=='inference-basis':inference['basis']='explicit'
    elif mutation=='inference-no-effect':inference['overall_effect']=''
    elif mutation=='inference-support':inference['source_relation_ids']=['dry-conditions-water']
    elif mutation=='stale-qualifier':draft['relation_bindings'][0]['qualifiers'][0]['source_ref']['content_revision']='0'*64
    elif mutation=='wrong-qualifier-value':draft['relation_bindings'][0]['qualifiers'][0]['source_ref']['value']='copy'
    elif mutation=='binding-copies-type':draft['relation_bindings'][0]['type']='conditions'
    o.write(run/'draft.json',draft,mutable=True)
    assert main(['organize','assess',str(run)])==2


def test_v4_changed_binding_invalidates_review_and_query_requires_release(tmp_path):
    run=prepared(tmp_path);populate(run);review(run)
    prior=load_json(run/'review.json')
    draft=load_json(run/'draft.json')
    draft['relation_bindings'][0]['qualifiers'][0]['applies_to']='from'
    draft['edges'][1]['qualifiers'][0]['applies_to']='from'
    o.write(run/'draft.json',draft,mutable=True)
    assert main(['organize','assess',str(run)])==1
    assert any(x['kind']=='dependency' for x in load_json(run/'quality.json')['issues'])
    o.write(tmp_path/'prior.json',prior)
    assert main(['organize','reuse-review',str(run),'--from-review',str(tmp_path/'prior.json')])==0
    reused={x['target_id'] for x in load_json(run/'review-reuse-template.json')['judgments'] if x['verdict']=='pass'}
    assert 'binding:dry-conditions-water' not in reused
    assert main(['organize','query',str(run),'--id','higher:native'])==2


def test_v4_native_whole_scope_can_coexist_with_local_same_type_source_edge(tmp_path):
    run=prepared(tmp_path)
    draft=populate(run)
    native=draft['edges'][0]
    native.update(type='conditions',from_id=draft['root_id'],to_id='block:water',
                  from_element_ids=['check','dry'],to_element_ids=['water'])
    o.write(run/'draft.json',draft,mutable=True)
    assert main(['organize','assess',str(run)])==1
    assert load_json(run/'candidate.json')['edges'][0]['from_element_ids']==['check','dry']


def test_v4_reuse_rejects_prior_guide_change(tmp_path):
    run=prepared(tmp_path);populate(run);review(run)
    prior=load_json(run/'review.json');prior['guide_sha256']='0'*64
    o.write(tmp_path/'prior.json',prior)
    assert main(['organize','reuse-review',str(run),'--from-review',str(tmp_path/'prior.json')])==2
