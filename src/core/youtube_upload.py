"""Resumable video upload to YouTube via the Data API v3."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MiB


class UploadError(RuntimeError):
    pass


@dataclass
class UploadRequest:
    video_path: str
    title: str
    description: str
    tags: list[str]
    category_id: str = "10"  # Music
    privacy_status: str = "public"  # public | unlisted | private
    publish_at: Optional[datetime] = None  # if set, video is scheduled
    made_for_kids: bool = False


def _build_body(req: UploadRequest) -> dict:
    privacy_status = req.privacy_status
    status: dict = {
        "privacyStatus": privacy_status,
        "selfDeclaredMadeForKids": req.made_for_kids,
    }
    if req.publish_at is not None:
        # YouTube only honors publishAt when the video is uploaded as private;
        # it auto-flips to public at the scheduled time.
        status["privacyStatus"] = "private"
        status["publishAt"] = req.publish_at.astimezone().isoformat()

    return {
        "snippet": {
            "title": req.title,
            "description": req.description,
            "tags": req.tags,
            "categoryId": req.category_id,
        },
        "status": status,
    }


def upload_video(
    creds: Credentials,
    req: UploadRequest,
    on_progress: Optional[Callable[[float], None]] = None,
) -> str:
    """Uploads the rendered video, returns the resulting YouTube video ID."""
    youtube = build("youtube", "v3", credentials=creds)
    body = _build_body(req)
    media = MediaFileUpload(
        req.video_path, chunksize=UPLOAD_CHUNK_SIZE, resumable=True, mimetype="video/mp4"
    )
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    try:
        while response is None:
            status, response = request.next_chunk()
            if status and on_progress:
                on_progress(status.progress())
    except HttpError as exc:
        raise UploadError(f"YouTube upload failed: {exc}") from exc

    if on_progress:
        on_progress(1.0)
    return response["id"]


def set_thumbnail(creds: Credentials, video_id: str, image_path: str) -> None:
    youtube = build("youtube", "v3", credentials=creds)
    try:
        youtube.thumbnails().set(
            videoId=video_id, media_body=MediaFileUpload(image_path)
        ).execute()
    except HttpError as exc:
        raise UploadError(f"Setting thumbnail failed: {exc}") from exc
