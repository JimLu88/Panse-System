"""数字人服务：建数字分身 + 口播脚本→成片 + 个性化私信视频。

渲染 provider 可插拔：配 runtime_config 的 avatar_provider_url 走真实数字人服务
(InfiniteTalk/Duix-Avatar 自托管 或 HeyGen 类 API)，否则 mock 返回演示成片。
⚠️ 合规闸：未授权(authorized=False)的数字人禁止渲染。
"""
from __future__ import annotations

import datetime as dt

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AvatarProfile, Draft, VideoJob
from . import runtime_config


def create_avatar(db: Session, *, name: str, real_person: str = "",
                  bound_account_id: int | None = None, voice_sample_ref: str = "",
                  face_ref: str = "", authorized: bool = False,
                  persona: dict | None = None) -> AvatarProfile:
    a = AvatarProfile(
        name=name, real_person=real_person, bound_account_id=bound_account_id,
        voice_sample_ref=voice_sample_ref, face_ref=face_ref, authorized=authorized,
        persona=persona or {}, status="ready" if (authorized and face_ref) else "draft",
    )
    db.add(a)
    db.commit()
    return a


def list_avatars(db: Session) -> list[dict]:
    return [{"id": a.id, "name": a.name, "real_person": a.real_person,
             "bound_account_id": a.bound_account_id, "authorized": a.authorized,
             "has_voice": bool(a.voice_sample_ref), "has_face": bool(a.face_ref),
             "status": a.status}
            for a in db.scalars(select(AvatarProfile).order_by(AvatarProfile.id))]


def authorize(db: Session, avatar_id: int) -> AvatarProfile:
    """登记真人书面授权(合规闸)。授权且有肖像 → ready。"""
    a = db.get(AvatarProfile, avatar_id)
    if a is None:
        raise ValueError("数字人不存在")
    a.authorized = True
    if a.face_ref:
        a.status = "ready"
    db.commit()
    return a


def render_from_draft(db: Session, draft_id: int, avatar_id: int) -> VideoJob:
    """把口播脚本草稿(content_type=video)交给数字人渲染成片。"""
    draft = db.get(Draft, draft_id)
    if draft is None:
        raise ValueError("草稿不存在")
    return _submit(db, avatar_id, script=draft.body, content_id=draft.id, job_type="note")


def render_dm_video(db: Session, avatar_id: int, target: str, script: str) -> VideoJob:
    """#6 数字分身：给某个客户出个性化私信视频(点名)。"""
    personalized = f"{target}你好～{script}"
    return _submit(db, avatar_id, script=personalized, job_type="dm", target=target)


def _submit(db: Session, avatar_id: int, *, script: str, content_id: int | None = None,
            job_type: str = "note", target: str = "") -> VideoJob:
    avatar = db.get(AvatarProfile, avatar_id)
    if avatar is None:
        raise ValueError("数字人不存在")
    if not avatar.authorized:
        raise ValueError("该数字人未登记真人授权，禁止渲染(合规要求)")
    provider = "real" if runtime_config.get("avatar_provider_url") else "mock"
    job = VideoJob(avatar_id=avatar_id, content_id=content_id, job_type=job_type,
                   script=script, target=target, status="pending", provider=provider)
    db.add(job)
    db.commit()
    _dispatch(db, job)
    return job


def _dispatch(db: Session, job: VideoJob) -> None:
    """提交渲染。真实 provider 异步回调；mock 直接标完成。"""
    url = runtime_config.get("avatar_provider_url")
    job.status = "rendering"
    db.commit()
    if url:
        try:
            resp = httpx.post(f"{url.rstrip('/')}/render",
                              json={"job_id": job.id, "script": job.script,
                                    "avatar_id": job.avatar_id}, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            job.output_url = data.get("output_url", "")
            job.status = "done" if job.output_url else "rendering"
        except httpx.HTTPError:
            job.status = "failed"
    else:
        # mock：演示成片
        job.output_url = f"https://demo.local/avatar/{job.id}.mp4"
        job.status = "done"
        job.finished_at = dt.datetime.now(dt.timezone.utc)
    db.commit()


def list_jobs(db: Session) -> list[dict]:
    rows = db.execute(
        select(VideoJob, AvatarProfile.name).join(
            AvatarProfile, AvatarProfile.id == VideoJob.avatar_id)
        .order_by(VideoJob.id.desc())
    ).all()
    return [{"id": j.id, "avatar": name, "type": j.job_type, "target": j.target,
             "status": j.status, "output_url": j.output_url,
             "provider": j.provider, "content_id": j.content_id} for j, name in rows]


def callback(db: Session, job_id: int, output_url: str, status: str = "done") -> VideoJob:
    """真实 provider 渲染完成回调。"""
    j = db.get(VideoJob, job_id)
    if j is None:
        raise ValueError("任务不存在")
    j.output_url = output_url
    j.status = status
    j.finished_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    return j
