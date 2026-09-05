"""Fetch view/like/comment stats for the signed-in channel's uploaded videos."""
from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from typing import Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

MAX_VIDEOS = 200


@dataclass
class VideoStats:
    video_id: str
    title: str
    published_at: str  # ISO 8601
    privacy_status: str
    thumbnail_url: str
    views: int
    likes: int
    comments: int

    @property
    def engagement_rate(self) -> float:
        """(likes + comments) / views, as a percentage. 0 if no views yet."""
        if not self.views:
            return 0.0
        return (self.likes + self.comments) / self.views * 100


def _uploads_playlist_id(youtube) -> str:
    response = youtube.channels().list(part="contentDetails", mine=True).execute()
    items = response.get("items", [])
    if not items:
        raise RuntimeError("No YouTube channel found for this Google account.")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def _list_video_ids(youtube, playlist_id: str, max_results: int) -> list[str]:
    video_ids: list[str] = []
    page_token = None
    while len(video_ids) < max_results:
        response = (
            youtube.playlistItems()
            .list(
                part="contentDetails",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=page_token,
            )
            .execute()
        )
        video_ids.extend(item["contentDetails"]["videoId"] for item in response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return video_ids[:max_results]


def fetch_video_stats(creds: Credentials, max_results: int = MAX_VIDEOS) -> list[VideoStats]:
    """Returns every uploaded video's stats, sorted by views (best first)."""
    youtube = build("youtube", "v3", credentials=creds)
    playlist_id = _uploads_playlist_id(youtube)
    video_ids = _list_video_ids(youtube, playlist_id, max_results)

    results: list[VideoStats] = []
    for start in range(0, len(video_ids), 50):
        chunk = video_ids[start : start + 50]
        response = (
            youtube.videos().list(part="snippet,statistics,status", id=",".join(chunk)).execute()
        )
        for item in response.get("items", []):
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            status = item.get("status", {})
            thumbnails = snippet.get("thumbnails", {})
            thumbnail = thumbnails.get("medium") or thumbnails.get("default") or {}
            results.append(
                VideoStats(
                    video_id=item["id"],
                    title=snippet.get("title", "(untitled)"),
                    published_at=snippet.get("publishedAt", ""),
                    privacy_status=status.get("privacyStatus", ""),
                    thumbnail_url=thumbnail.get("url", ""),
                    views=int(stats.get("viewCount", 0)),
                    likes=int(stats.get("likeCount", 0)),
                    comments=int(stats.get("commentCount", 0)),
                )
            )

    results.sort(key=lambda v: v.views, reverse=True)
    return results


def download_thumbnail(url: str, timeout: float = 5.0) -> Optional[bytes]:
    """Best-effort thumbnail download; returns None on any failure so a single
    bad/slow thumbnail can't break the whole analytics load."""
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except Exception:  # noqa: BLE001 - thumbnails are cosmetic, never fatal
        return None
