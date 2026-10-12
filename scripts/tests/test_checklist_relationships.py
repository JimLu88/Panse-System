import sys
from pathlib import Path
import pytest

ROOT=Path(__file__).parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from app.services import business_relationship_service as service


def test_all_checklist_scripts_have_review_relationships_and_real_anchors():
    report=service.change_report(sorted(service.CHECKLIST_SCRIPTS))
    assert not report['unmapped_changes'] and not report['validation_errors']
    for entry in service.catalog.NODES+service.catalog.EDGES:
        for ref in entry['sources']:
            if ref['path'] in service.CHECKLIST_SCRIPTS:
                assert ref['anchor'] in service._path(service.APP_ROOT,ref).read_text(encoding='utf-8')


@pytest.mark.parametrize('path',['scripts/../../secrets.py','scripts/unregistered.py','C:/outside.py'])
def test_relationship_source_allowlist_does_not_expand_file_access(path):
    with pytest.raises(ValueError):service._path(service.APP_ROOT,{'path':path})
