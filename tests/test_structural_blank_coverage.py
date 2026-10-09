"""Synthetic blank coverage keeps scope and blocks nonempty/visual omissions."""
from copy import deepcopy
import pytest
from source_knowledge_modeling import semantic_workflow as w
from source_knowledge_modeling.core import load_json, KnowledgeFoundationError
from source_knowledge_modeling.ingest import ingest, TextAdapter
from test_semantic_v3 import _write
from test_semantic_v3_safety import _candidate_workspace


def fixture(tmp_path, include_uncovered=False):
    seed,base,drafts,candidate=_candidate_workspace(tmp_path/'seed')
    # Replace the fixture source with own direct-text material; the reviewed
    # line:5 stays identical. Add a real blank line and a substantive extra unit.
    source=tmp_path/'source.txt'
    source.write_text('Context.\nContext.\nContext.\nContext.\n没有给出单次浇水量\n\nDo not skip east.\n')
    foundation,_=ingest(source,root=tmp_path,workspace=tmp_path/'inventory',adapter=TextAdapter())
    f=load_json(foundation)
    from source_knowledge_modeling.cli import main
    scope=tmp_path/'scope.json';run=tmp_path/'new-run'
    _write(scope,{'description':'Synthetic selected blank structure','included_locators':['line:5','line:6']+(['line:7'] if include_uncovered else [])})
    assert main(['prepare',str(foundation),'--root',str(tmp_path),'--profile','instructional-source@3.0.0','--scope',str(scope),'--output-dir',str(run)])==0
    base=load_json(run/'base.json')
    # Empty base has no existing semantic objects. Candidate draft remains same
    # authored claim, resealed solely for the new exact source/scope identity.
    from source_knowledge_modeling import semantic_v3 as v3
    raw=load_json(seed/'drafts.json');raw['artifact_id']=base['artifact_id']
    drafts=v3.normalize_drafts(base,raw,root=tmp_path)
    _write(run/'base.json',base);_write(run/'checked-drafts.json',drafts)
    for name in ['source-information.json','discourse-audit.json']:
        audit=load_json(seed/name);audit.update(source_sha256=base['source']['sha256'],snapshot_sha256=base['source']['snapshot_sha256']);_write(run/name,audit)
    assessment=load_json(seed/'semantic-assessment.json')
    new=v3.candidate_preview(base,drafts,root=tmp_path)
    assessment.update(draft_sha256=v3.digest(drafts),inventory_sha256=w.file_hash(run/'source-information.json'),discourse_sha256=w.file_hash(run/'discourse-audit.json'))
    for row in assessment['checks']:
        if row['target_kind'] in ('element','relation'):
            plural='elements' if row['target_kind']=='element' else 'relations'
            row['content_revision']=next(x['content_revision'] for x in new[plural] if x['id']==row['target_id'])
    _write(run/'semantic-assessment.json',assessment)
    return run,base


def test_selected_blank_requires_no_fake_anchor_and_keeps_scope(tmp_path):
    run,base=fixture(tmp_path)
    report=w.assess_workspace(run,root=tmp_path)
    assert report['status']=='pass'
    assert base['scope']['included_locators']==['line:5','line:6']
    assert w.structural_blank_locators(base,root=tmp_path)=={'line:6'}
    assert next(c for c in report['checks'] if c['id']=='structural-blank:line:6')['status']=='pass'


def test_uncovered_nonempty_primary_is_still_failure(tmp_path):
    run,base=fixture(tmp_path,True)
    report=w.assess_workspace(run,root=tmp_path)
    assert report['status']=='fail'
    assert next(c for c in report['checks'] if c['id']=='primary-scope')['status']=='fail'


@pytest.mark.parametrize('change',['nonempty','visual','structure','pdf','other-parser'])
def test_blank_label_alone_cannot_hide_meaning(tmp_path,change):
    run,base=fixture(tmp_path)
    if change=='pdf':base['source']['kind']='pdf'
    elif change=='other-parser':base['source']['transcription_method']='other-parser 1; local'
    else:
        p=tmp_path/base['source']['snapshot_path'];snap=load_json(p)
        unit=next(u for u in snap['units'] if u['locator']=='line:6')
        if change=='nonempty':unit['text']='Substantive text'
        elif change=='visual':unit.update(visual_ref='figure:1',bbox=[0,0,1,1])
        else:unit['raw_content']={'chart':['x','y']}
        _write(p,snap);base['source']['snapshot_sha256']=w.file_hash(p)
    assert w.structural_blank_locators(base,root=tmp_path)==set()


def test_changed_snapshot_not_accepted_as_blank(tmp_path):
    run,base=fixture(tmp_path)
    p=tmp_path/base['source']['snapshot_path'];p.write_bytes(p.read_bytes()+b'\n')
    with pytest.raises(KnowledgeFoundationError,match='hash mismatch'):
        w.structural_blank_locators(base,root=tmp_path)
