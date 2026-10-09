import hashlib
import pytest
from campaign_recording_evidence import collect

@pytest.mark.parametrize('tool,stage',[('sellable_product_export','product_export'),('activity_template','campaign_atomic')])
def test_native_stage_preserves_operation_and_real_media(tmp_path,tool,stage):
    video=tmp_path/'video.webm';video.write_bytes(b'fixture')
    rec={'video':str(video),'video_sha256':hashlib.sha256(video.read_bytes()).hexdigest(),'active':False,'frames':2}
    job={'job_id':'original','operation':'campaign_atomic','result':{'tool_id':tool,'recording':rec}}
    result=collect(job,[tmp_path])
    assert result[0]['operation']=='campaign_atomic' and result[0]['stage']==stage
    video.write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'):collect(job,[tmp_path])
