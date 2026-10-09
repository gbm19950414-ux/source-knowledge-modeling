"""Synthetic original-source corrections; no real review claims in fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from source_knowledge_modeling.cli import main
from source_knowledge_modeling.core import KnowledgeFoundationError,load_json,validate
from source_knowledge_modeling.ingest import ExtractedUnit,ingest
from source_knowledge_modeling.transcription_revision import revise_transcription


class FixtureAdapter:
    name='synthetic-test'
    def extract(self,source,*,pages=None):
        return [ExtractedUnit('pdf-page:1/block:0','text','OCR: 2O units',[0.1,0.1,0.9,0.2]),
                ExtractedUnit('pdf-page:1/block:1','text','Following explanation',[0.1,0.6,0.9,0.8])],{'name':self.name,'version':'1'}


def fixture(tmp_path):
    original=tmp_path/'source.pdf';original.write_bytes(b'Synthetic test source; no parser or visual review performed')
    foundation,snapshot=ingest(original,root=tmp_path,workspace=tmp_path/'ingest',adapter=FixtureAdapter(),pages='1')
    f=load_json(foundation);s=load_json(snapshot);unit=deepcopy(s['units'][0]);unit['text']='OCR: 20 units'
    figure={'locator':'pdf-page:1/manual:figure','kind':'image','text':'A to B','bbox':[0.1,0.25,0.9,0.5],
            'visual_ref':'pdf-page:1','raw_content':'A → B','continues_prev':False,'continues_from':None,'continuation_method':None}
    patch={'schema_version':'knowledge-transcription-corrections/v1','source_sha256':f['source']['sha256'],
        'snapshot_sha256':f['source']['snapshot_sha256'],'reviewer':'synthetic fixture actor','reviewer_type':'AI','model':'unknown',
        'checked_at':'2026-09-26T00:00:00Z','check_method':'original_visual','note':'Synthetic test assertion only, not a real original-page check',
        'operations':[{'op':'replace','unit':unit},{'op':'insert','after_locator':unit['locator'],'unit':figure}]}
    path=tmp_path/'corrections.json';path.write_text(json.dumps(patch));return foundation,snapshot,path,patch


def test_public_correction_preserves_old_source_and_stable_ids_and_recovers(tmp_path):
    foundation,snapshot,patch,_=fixture(tmp_path);old_bytes=snapshot.read_bytes();old=load_json(foundation)
    out=tmp_path/'corrected'
    cmd=['revise-transcription',str(foundation),'--root',str(tmp_path),'--corrections',str(patch),'--output-dir',str(out)]
    assert main(cmd)==0;first=(out/'foundation.json').read_bytes();assert main(cmd)==0
    assert first==(out/'foundation.json').read_bytes() and snapshot.read_bytes()==old_bytes
    revised=load_json(out/'foundation.json');validate(revised,root=tmp_path,resolve=True)
    assert revised['units'][0]['id']==old['units'][0]['id']
    assert revised['units'][2]['id']==old['units'][1]['id']
    assert revised['units'][1]['locator']=='pdf-page:1/manual:figure'
    assert revised['annotations']==revised['relations']==[]
    assert revised['source']['snapshot_sha256']!=old['source']['snapshot_sha256']
    assert load_json(out/'revision-manifest.json')['new_semantic_reviews']==0


def test_stale_patch_or_unverified_pdf_or_ambiguous_insert_is_rejected_before_writes(tmp_path):
    foundation,snapshot,path,patch=fixture(tmp_path)
    for mutate in [lambda p:p.update(snapshot_sha256='0'*64),lambda p:p.update(check_method='source_text'),
                   lambda p:p['operations'][1].update(after_locator='absent'),
                   lambda p:p['operations'].append(deepcopy(p['operations'][0]))]:
        changed=deepcopy(patch);mutate(changed);path.write_text(json.dumps(changed));out=tmp_path/'failed'
        with pytest.raises(KnowledgeFoundationError):revise_transcription(foundation,root=tmp_path,corrections_path=path,output_dir=out)
        assert not out.exists()


def test_old_snapshot_overwrite_and_changed_recovery_output_are_rejected(tmp_path):
    foundation,snapshot,path,_=fixture(tmp_path)
    with pytest.raises(KnowledgeFoundationError,match='overwrite'):
        revise_transcription(foundation,root=tmp_path,corrections_path=path,output_dir=snapshot.parent)
    out=tmp_path/'new';revise_transcription(foundation,root=tmp_path,corrections_path=path,output_dir=out)
    (out/'transcription.json').write_text('{}')
    with pytest.raises(KnowledgeFoundationError,match='differs'):
        revise_transcription(foundation,root=tmp_path,corrections_path=path,output_dir=out)
