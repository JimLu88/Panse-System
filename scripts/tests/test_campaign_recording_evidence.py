import hashlib
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_recording_evidence import collect


def test_recovered_export_keeps_recording_for_every_original_page(tmp_path):
    files=[]
    for i in range(2):
        root=tmp_path/str(i);root.mkdir()
        export=root/'export.xlsx';export.write_bytes(b'test-only')
        video=root/'recording.mp4';video.write_bytes(bytes([i]))
        record={'video':str(video),'video_sha256':hashlib.sha256(video.read_bytes()).hexdigest(),
                'active':False,'frames':1,'capture_errors':0}
        (root/'recording-result.json').write_text(json.dumps(record))
        files.append({'path':str(export)})
    job={'job_id':'a'*64,'operation':'product_export','result':{'files':files}}
    assert len(collect(job,[tmp_path]))==2
    video.write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'):collect(job,[tmp_path])
