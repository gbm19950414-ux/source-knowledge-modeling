"""Synthetic import failures identify both membership and reference mistakes."""
from copy import deepcopy
import pytest
from source_knowledge_modeling import semantic_v3 as v3
from source_knowledge_modeling.core import KnowledgeFoundationError, load_json
from test_semantic import ROOT
from test_semantic_v3 import _workspace


def test_import_lists_unreferenced_objects_and_rejects_context_as_membership(tmp_path):
    run, _ = _workspace(tmp_path)
    authored = load_json(run / 'drafts.json')
    package = authored['proposals'][0]['upsert_packages'][0]
    package['element_ids'] = []
    package['context_ids'] = ['no-amount']
    with pytest.raises(KnowledgeFoundationError) as error:
        v3.normalize_drafts(load_json(run / 'base.json'), authored, root=ROOT)
    assert "Unreferenced element_ids=['no-amount']" in str(error.value)
    assert 'context_ids do not establish package membership' in str(error.value)
    assert not (run / 'candidate.json').exists()
    package['element_ids'] = ['no-amount']
    canonical = v3.normalize_drafts(load_json(run / 'base.json'), authored, root=ROOT)
    candidate = v3.candidate_preview(load_json(run / 'base.json'), canonical, root=ROOT)
    assert candidate['reviews'] == []


@pytest.mark.parametrize('field,unknown', [('element_ids','unknown-element'),('relation_ids','unknown-relation')])
def test_import_identifies_unknown_member_without_repair(tmp_path, field, unknown):
    run, _ = _workspace(tmp_path)
    authored = load_json(run / 'drafts.json')
    authored['proposals'][0]['upsert_packages'][0][field].append(unknown)
    preserved = deepcopy(authored)
    with pytest.raises(KnowledgeFoundationError) as error:
        v3.normalize_drafts(load_json(run / 'base.json'), authored, root=ROOT)
    assert 'amount-scope' in str(error.value)
    assert f"{field}=['{unknown}']" in str(error.value)
    assert authored == preserved
