"""Synthetic v3 contract: no required blocks, reviewed directory and deterministic replay."""
from copy import deepcopy
from pathlib import Path

import pytest

from source_knowledge_modeling import organize as o
from source_knowledge_modeling.cli import main
from source_knowledge_modeling.core import load_json

ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / 'contracts/semantic-discourse/v1/examples/garden.export.json'


def source_map():
    source = load_json(EXPORT)
    units = {u['locator']:u['text'] for u in load_json(ROOT / source['source']['snapshot_path'])['units']}
    paragraphs = [{'id':x['locator'],'locator':x['locator'],'order':i,'text':units[x['locator']],'evidence':[]}
                  for i,x in enumerate(source['source_units'])]
    return {'schema_version':'source-reading-map/v1','title':'Synthetic garden','root_id':'chapter:garden',
            'containers':[{'id':'chapter:garden','parent_id':None,'kind':'chapter','title':'Synthetic garden','order':0,'evidence':[]},
                          {'id':'section:garden','parent_id':'chapter:garden','kind':'section','title':'Watering','order':1,
                           'range':{'start':{'paragraph_id':paragraphs[0]['id'],'offset':0},
                                    'end':{'paragraph_id':paragraphs[-1]['id'],'offset':len(paragraphs[-1]['text'])}},'evidence':[]}],
            'paragraphs':paragraphs,'occurrences':[],'edge_routes':[]}


def prepare(tmp_path, *, mapped=True, requirements=False):
    run=tmp_path/'run';args=['organize','prepare',str(EXPORT),'--root',str(ROOT),'--output-dir',str(run),'--format-version','3']
    if mapped:
        o.write(tmp_path/'map.json',source_map());args+=['--document-map',str(tmp_path/'map.json')]
    if requirements:
        o.write(tmp_path/'requirements.json',{'must_preserve':['dry and no rain','no watering volume']})
        args+=['--requirements',str(tmp_path/'requirements.json')]
    assert main(args)==0
    draft=load_json(run/'draft.json');draft['author']='synthetic author';o.write(run/'draft.json',draft,mutable=True)
    assert main(args)==0
    assert load_json(run/'draft.json')['author']=='synthetic author'
    return run,args


def review(run, failures=None):
    assert main(['organize','assess',str(run)])==1
    value=load_json(run/'review-template.json')
    value.update(reviewer='independent synthetic reviewer',reviewer_type='synthetic',checked_at='2026-09-29T00:00:00Z',
                 method='Synthetic expected judgments',model='unknown')
    for item in value['judgments']:
        reason=(failures or {}).get(item['target_id'])
        item.update(verdict='fail' if reason else 'pass',reason=reason or 'Synthetic expected faithful case.')
    o.write(run/'review.json',value)


def test_v3_minimal_map_publish_replay(tmp_path):
    run,_=prepare(tmp_path,requirements=True)
    draft=load_json(run/'draft.json')
    assert draft['blocks']==draft['edges']==draft['reading_structures']==[]
    review(run)
    assert main(['organize','publish',str(run)])==0
    release,=(run/'releases').iterdir()
    domain=load_json(release/'model.json');reading=load_json(release/'reading-map.json')
    assert domain['schema_version']=='diagram-domain-model/v4'
    assert reading['schema_version']=='source-reading-map/v2'
    assert all(e['type'] in {'conditions','exception_to','replaces','supports','precedes','quantifies','example_of','qualifies','causes','contrasts_with','elaborates'} for e in domain['edges'])
    assert all(x['parent_id']=='section:garden' for x in reading['occurrences'])
    assert len([x for x in load_json(run/'review-template.json')['judgments'] if x['target_id'].startswith('requirement:')])==2
    before={str(p.relative_to(release)):p.read_bytes() for p in release.rglob('*') if p.is_file()}
    assert main(['organize','replay',str(release/'manifest.json'),'--output-dir',str(tmp_path/'clean')])==0
    rebuilt,=(tmp_path/'clean'/'releases').iterdir()
    assert before=={str(p.relative_to(rebuilt)):p.read_bytes() for p in rebuilt.rglob('*') if p.is_file()}


def test_v3_pending_directory_can_be_independently_reviewed(tmp_path):
    run,_=prepare(tmp_path,mapped=False)
    assert load_json(run/'draft.json')['directory_status']=='pending'
    assert main(['organize','publish',str(run)])==2
    review(run)
    assert 'directory:verification' in {x['target_id'] for x in load_json(run/'review.json')['judgments']}
    assert main(['organize','publish',str(run)])==0


def test_v3_semantic_negative_requires_real_verdict(tmp_path):
    run,_=prepare(tmp_path)
    draft=load_json(run/'draft.json')
    draft['containers'][1]['title']='Unconditional watering every six hours'
    o.write(run/'draft.json',draft,mutable=True)
    review(run,{'section:garden':'Synthetic semantic rejection: scope is conditional'})
    assert main(['organize','assess',str(run)])==1
    assert any(x['target_id']=='section:garden' and x['kind']=='semantic_review' for x in load_json(run/'quality.json')['issues'])
    assert main(['organize','publish',str(run)])==2


@pytest.mark.parametrize('mutation',['snapshot','paragraph','range','scope','type','direction','endpoint'])
def test_v3_rejects_structural_errors(tmp_path,mutation):
    run,_=prepare(tmp_path)
    draft=load_json(run/'draft.json')
    if mutation=='snapshot':
        source=load_json(run/'source.json');source['source']['snapshot_sha256']='0'*64
        o.write(run/'source.json',source,mutable=True)
    elif mutation=='paragraph':
        directory=load_json(run/'document-map.json');directory['paragraphs'][0]['text']='wrong'
        o.write(run/'document-map.json',directory,mutable=True)
    elif mutation=='range':draft['containers'][1]['range']['end']['paragraph_id']='absent'
    else:
        edge={'id':'higher:test','type':'conditions','from_id':'section:garden','to_id':'chapter:garden',
              'from_element_ids':['dry'],'to_element_ids':['water'],'direction':'directed','origin':'interpretation',
              'premises':[{'object_id':'dry','field_path':'/text'}],'rationale':'Synthetic inference'}
        if mutation=='scope':edge['from_id']='absent'
        if mutation=='type':edge['type']='generic'
        if mutation=='direction':edge['direction']='symmetric'
        if mutation=='endpoint':edge['from_element_ids']=['absent']
        draft['edges']=[edge]
    if mutation not in {'snapshot','paragraph'}:o.write(run/'draft.json',draft,mutable=True)
    assert main(['organize','assess',str(run)])==2


def test_v3_selected_section_scope_excludes_remaining_export(tmp_path):
    map_value=source_map()
    map_value['containers'][1]['range']['end']={'paragraph_id':'line:5','offset':0}
    o.write(tmp_path/'map.json',map_value)
    run=tmp_path/'run'
    args=['organize','prepare',str(EXPORT),'--root',str(ROOT),'--output-dir',str(run),'--format-version','3',
          '--document-map',str(tmp_path/'map.json')]
    assert main(args)==0
    draft=load_json(run/'draft.json');draft['author']='synthetic author'
    o.write(run/'draft.json',draft,mutable=True)
    review(run)
    assert main(['organize','publish',str(run)])==0
    release,=(run/'releases').iterdir()
    domain=load_json(release/'model.json')
    assert set(domain['reading_order'])=={'element:check','element:dry','element:water'}
    quality=load_json(release/'quality.json')
    assert quality['scope']['selected_elements']==3
    assert quality['scope']['export_elements']==4


def test_v3_typed_rollup_and_partial_reading_structure(tmp_path):
    run,_=prepare(tmp_path)
    draft=load_json(run/'draft.json')
    draft['edges']=[{'id':'higher:condition','type':'conditions','from_id':'section:garden','to_id':'chapter:garden',
                     'from_element_ids':['dry'],'to_element_ids':['water'],'direction':'directed','origin':'source',
                     'premises':[{'object_id':'dry-conditions-water','field_path':'/type'}],
                     'source_relation_ids':['dry-conditions-water'],'scope_note':'This exact condition relation only.'}]
    draft['reading_structures']=[{'id':'structure:partial','scope_id':'section:garden','pattern':'explanation',
                                   'core_ids':['dry'],'supports':[],'independent_ids':[],'reading_order':['dry'],
                                   'refs':[{'object_id':'dry','field_path':'/text'}]}]
    o.write(run/'draft.json',draft,mutable=True)
    review(run)
    assert main(['organize','publish',str(run)])==0
    release,=(run/'releases').iterdir()
    domain=load_json(release/'model.json')
    higher=next(e for e in domain['edges'] if e['id']=='higher:condition')
    assert higher['type']=='conditions' and higher['label']=='条件'
    assert higher['from_element_ids']==['element:dry']
    assert domain['reading_structures'][0]['core_ids']==['element:dry']
    assert 'rationale' not in domain['reading_structures'][0]


def test_v3_keyed_requirements_are_reviewed_per_item(tmp_path):
    requirements={'review':{'reviewer':'frozen synthetic metadata','created_at':'synthetic'},
                  'requirements':[{'id':'L01','required_meaning':'Condition remains local','prohibited_readings':['unconditional']},
                                  {'id':'L02','required_meaning':'Volume unspecified','prohibited_readings':['quantity invented']} ]}
    o.write(tmp_path/'requirements.json',requirements)
    run=tmp_path/'run'
    args=['organize','prepare',str(EXPORT),'--root',str(ROOT),'--output-dir',str(run),'--format-version','3',
          '--requirements',str(tmp_path/'requirements.json')]
    assert main(args)==0
    draft=load_json(run/'draft.json');draft['author']='synthetic author';o.write(run/'draft.json',draft,mutable=True)
    assert main(['organize','assess',str(run)])==1
    targets={x['target_id'] for x in load_json(run/'review-template.json')['judgments']}
    assert {x for x in targets if x.startswith('requirement:')}=={'requirement:L01','requirement:L02'}


@pytest.mark.parametrize(('mutation','target'),[
    ('adjacency-cause','higher:inference'),('broadened-scope','section:garden'),
    ('origin-impersonation','higher:inference'),('wrong-merge','block:merged')])
def test_v3_legal_semantic_errors_require_independent_rejection(tmp_path,mutation,target):
    run,_=prepare(tmp_path)
    draft=load_json(run/'draft.json')
    if mutation=='broadened-scope':
        draft['containers'][1]['title']='All watering is unconditional and must happen every six hours'
    elif mutation=='wrong-merge':
        draft['blocks']=[{'id':'block:merged','parent_id':'section:garden','title':'One causal rule with known water amount',
                          'order':10,'element_ids':['dry','unknown-volume']}]
    else:
        draft['edges']=[{'id':'higher:inference','type':'causes','from_id':'section:garden','to_id':'chapter:garden',
                        'from_element_ids':['check'],'to_element_ids':['water'],'direction':'directed',
                        'origin':'interpretation','premises':[{'object_id':'check','field_path':'/text'}],
                        'rationale':'Direct original source claim of causation' if mutation=='origin-impersonation' else
                                    'Adjacent mentions prove checking causes watering'}]
    o.write(run/'draft.json',draft,mutable=True)
    review(run,{target:'Synthetic semantic rejection of this exact legal draft'})
    assert main(['organize','assess',str(run)])==1
    quality=load_json(run/'quality.json')
    assert quality['program_checks']['source_references']=='pass'
    assert any(i['kind']=='semantic_review' and i['target_id']==target for i in quality['issues'])
    assert main(['organize','publish',str(run)])==2


def test_v3_explicit_out_of_scope_context_is_evidence_not_occurrence(tmp_path,monkeypatch):
    from source_knowledge_modeling import organize_v3 as v3
    from source_knowledge_modeling.semantic_v3 import digest
    map_value=source_map()
    map_value['containers'][1]['range']['end']={'paragraph_id':'line:5','offset':0}
    o.write(tmp_path/'map.json',map_value)
    run=tmp_path/'run'
    assert main(['organize','prepare',str(EXPORT),'--root',str(ROOT),'--output-dir',str(run),
                 '--format-version','3','--document-map',str(tmp_path/'map.json')])==0
    draft=load_json(run/'draft.json');draft['author']='synthetic author'
    o.write(run/'draft.json',draft,mutable=True)
    assert main(['organize','assess',str(run)])==1
    source=load_json(run/'source.json');model=load_json(run/'candidate.json')
    elements,relations,packages=o.source_index(source)
    elements=deepcopy(elements)
    elements['water']['context_ids']=['unknown-volume']
    monkeypatch.setattr(o,'source_index',lambda _source:(elements,relations,packages))
    artifacts=v3.derived(source,model,load_json(run/'work-package.json'))
    domain,reading,evidence=(artifacts[x] for x in ('model.json','reading-map.json','evidence.json'))
    assert 'element:unknown-volume' not in {n['id'] for n in domain['nodes']}
    assert 'element:unknown-volume' not in {x['object_id'] for x in reading['occurrences']}
    water=next(n for n in domain['nodes'] if n['id']=='element:water')
    context_evidence='evidence:'+digest('source:unknown-volume')[:24]
    assert context_evidence in water['evidence']
    assert context_evidence in evidence['sources']


def test_v3_map_merges_same_parent_anchors_but_keeps_cross_parent_identity(tmp_path,monkeypatch):
    from source_knowledge_modeling import organize_v3 as v3
    run,_=prepare(tmp_path)
    assert main(['organize','assess',str(run)])==1
    source=load_json(run/'source.json');model=load_json(run/'candidate.json')
    indexed=o.source_index(source)
    monkeypatch.setattr(o,'source_index',lambda _source:indexed)
    text=next(p['text'] for p in model['paragraphs'] if p['locator']=='line:5')
    start=text.index('也没有证明')
    source['occurrences'].append({**source['occurrences'][0],'id':'occ:check-again','locator':'line:5',
                                  'start':start,'end':start+len('也没有证明')})
    package=load_json(run/'work-package.json')
    same=v3.derived(source,model,package)['reading-map.json']
    check=[x for x in same['occurrences'] if x['object_id']=='element:check']
    assert len(check)==1 and len(check[0]['spans'])==2
    assert check[0]['id']=='occ:check'
    second=deepcopy(model)
    first_section=next(x for x in second['containers'] if x['id']=='section:garden')
    first_section['range']['end']={'paragraph_id':'line:5','offset':0}
    second['containers'].append({'id':'section:other','parent_id':'chapter:garden','kind':'section',
                                 'title':'Second synthetic section','order':2,
                                 'range':{'start':{'paragraph_id':'line:5','offset':0},
                                          'end':{'paragraph_id':'line:5','offset':len(next(p['text'] for p in second['paragraphs'] if p['id']=='line:5'))}}})
    across=v3.derived(source,second,package)['reading-map.json']
    check=[x for x in across['occurrences'] if x['object_id']=='element:check']
    assert len(check)==2 and {x['parent_id'] for x in check}=={'section:garden','section:other'}
    assert {x['id'] for x in check}=={'occ:check','occ:check-again'}


def test_v3_related_cross_section_reference_preserves_source_identity(tmp_path):
    map_value=source_map()
    first=map_value['containers'][1]
    first['range']['end']={'paragraph_id':'line:5','offset':0}
    map_value['containers'].append({'id':'section:other','parent_id':'chapter:garden','kind':'section',
                                    'title':'Other synthetic section','order':2,'evidence':[],
                                    'range':{'start':{'paragraph_id':'line:5','offset':0},
                                             'end':{'paragraph_id':'line:5','offset':len(map_value['paragraphs'][-1]['text'])}}})
    o.write(tmp_path/'map.json',map_value)
    run=tmp_path/'run'
    assert main(['organize','prepare',str(EXPORT),'--root',str(ROOT),'--output-dir',str(run),
                 '--format-version','3','--document-map',str(tmp_path/'map.json')])==0
    draft=load_json(run/'draft.json');draft['author']='synthetic author'
    draft['blocks']=[{'id':'block:owner','parent_id':'section:garden','title':'Original check','order':10,
                      'element_ids':['check']},
                     {'id':'block:reference','parent_id':'section:other','title':'Other rule','order':11,
                      'element_ids':['unknown-volume'],'related_element_ids':['check']}]
    o.write(run/'draft.json',draft,mutable=True)
    review(run)
    assert main(['organize','publish',str(run)])==0
    release,=(run/'releases').iterdir()
    domain=load_json(release/'model.json');reading=load_json(release/'reading-map.json')
    owner=next(x for x in domain['nodes'] if x['id']=='block:owner')
    reference=next(x for x in domain['nodes'] if x['id']=='block:reference')
    assert 'element:check' in owner['children']
    assert 'element:check' not in reference['children']
    assert reference['related_element_ids']==['element:check']
    checks=[x for x in reading['occurrences'] if x['object_id']=='element:check']
    assert len(checks)==2 and {x['parent_id'] for x in checks}=={'block:owner','block:reference'}
    assert checks[0]['spans']==checks[1]['spans']
    assert len([x for x in domain['nodes'] if x['id']=='element:check'])==1
    assert main(['organize','replay',str(release/'manifest.json'),'--output-dir',str(tmp_path/'clean')])==0


def test_v3_multiple_owning_blocks_fail_at_assess(tmp_path):
    run,_=prepare(tmp_path)
    draft=load_json(run/'draft.json')
    draft['blocks']=[{'id':f'block:{i}','parent_id':'section:garden','title':f'Block {i}',
                      'order':i+10,'element_ids':['check']} for i in range(2)]
    o.write(run/'draft.json',draft,mutable=True)
    assert main(['organize','assess',str(run)])==2
