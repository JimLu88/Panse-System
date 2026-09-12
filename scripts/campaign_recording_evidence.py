"""Collect each physical export recording, including preserved earlier pages."""
import hashlib
import json
from pathlib import Path


def collect(job, roots):
    allowed=[Path(p).resolve() for p in roots]
    records=[]
    def append(record):
        video=Path(record['video']).resolve(strict=True)
        if not any(video.is_relative_to(root) for root in allowed):
            raise ValueError('recording_outside_artifact_roots')
        if (record.get('active') is not False or not record.get('frames')
                or record.get('error') or record.get('video_error') or record.get('capture_errors')
                or hashlib.sha256(video.read_bytes()).hexdigest()!=record.get('video_sha256')):
            raise ValueError('recording_incomplete_or_changed')
        if not any(r['recording']['video']==record['video'] for r in records):
            records.append({'job_id':job['job_id'],'operation':job['operation'],'recording':record})
    result=job.get('result') or {}
    if job.get('operation')=='product_export':
        for entry in result.get('files',[]):
            source=Path(entry['path']).resolve(strict=True).parent/'recording-result.json'
            if not any(source.is_relative_to(root) for root in allowed):
                raise ValueError('recording_source_outside_artifact_roots')
            append(json.loads(source.read_text(encoding='utf-8')))
    elif result.get('recording'):
        source=Path(result.get('evidence_path','')).parent
        if (job.get('operation')=='discount' and source.name in ('form-recovery-1','rejected-upload-recovery-1')
                and source.parent.name==job['job_id']):
            previous=[source.parent/'recording-result.json']
            if source.name=='rejected-upload-recovery-1':previous.append(source.parent/'form-recovery-1'/'recording-result.json')
            for prior in previous:
                if not any(prior.resolve().is_relative_to(root) for root in allowed):
                    raise ValueError('prior_recording_outside_artifact_roots')
                append(json.loads(prior.read_text(encoding='utf-8')))
        append(result['recording'])
    if not records:raise ValueError('required_job_recording_missing')
    return records
