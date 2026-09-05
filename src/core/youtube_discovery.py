"""How viewers found each video (traffic source + top search term), via the
YouTube Analytics API. Needs the yt-analytics.readonly scope — on an older
cached token that predates this scope, calls here raise; callers should treat
that as "no discovery data available yet" rather than a hard failure."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

LOOKBACK_DAYS = 90

TRAFFIC_SOURCE_LABELS = {
    "YT_SEARCH": "YouTube search",
    "SUGGESTED_VIDEO": "Suggested videos",
    "BROWSE": "Browse / Explore",
    "PLAYLIST": "Playlist",
    "EXTERNAL": "External site",
    "NOTIFICATION": "Notification",
    "SUBSCRIBER": "Subscription feed",
    "NO_LINK_OTHER": "Direct / unknown",
    "NO_LINK_EMBEDDED": "Embedded player",
    "ADVERTISING": "Advertising",
    "END_SCREEN": "End screen",
    "ANNOTATION": "Annotation",
    "CAMPAIGN_CARD": "Campaign card",
    "SOUND_PAGE": "Sound page",
    "SHORTS": "Shorts feed",
    "HASHTAGS": "Hashtag page",
    "LIVE_REDIRECT": "Live redirect",
    "PROMOTED": "Promoted",
}


@dataclass
class Discovery:
    source_label: str
    search_term: str = ""


def _label(source_type: str) -> str:
    return TRAFFIC_SOURCE_LABELS.get(source_type, source_type.replace("_", " ").title())


def _date_range() -> tuple[str, str]:
    end = date.today()
    start = end - timedelta(days=LOOKBACK_DAYS)
    return start.isoformat(), end.isoformat()


def fetch_discovery_info(creds: Credentials, video_ids: list[str]) -> dict[str, Discovery]:
    """Returns {video_id: Discovery} for whichever videos have traffic-source
    data in the lookback window (new/low-view videos may have none yet)."""
    if not video_ids:
        return {}

    analytics = build("youtubeAnalytics", "v2", credentials=creds)
    start_date, end_date = _date_range()
    video_filter = ",".join(video_ids)

    top_source: dict[str, str] = {}
    source_response = (
        analytics.reports()
        .query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            metrics="views",
            dimensions="video,insightTrafficSourceType",
            filters=f"video=={video_filter}",
            sort="-views",
            maxResults=10000,
        )
        .execute()
    )
    # Rows are sorted by views descending across all video+source pairs, so
    # the first row seen for a given video is necessarily its top source.
    for row in source_response.get("rows", []):
        video_id, source_type = row[0], row[1]
        top_source.setdefault(video_id, source_type)

    search_videos = [vid for vid, src in top_source.items() if src == "YT_SEARCH"]
    top_term: dict[str, str] = {}
    if search_videos:
        term_response = (
            analytics.reports()
            .query(
                ids="channel==MINE",
                startDate=start_date,
                endDate=end_date,
                metrics="views",
                dimensions="video,insightTrafficSourceDetail",
                filters=f"insightTrafficSourceType==YT_SEARCH;video=={','.join(search_videos)}",
                sort="-views",
                maxResults=10000,
            )
            .execute()
        )
        for row in term_response.get("rows", []):
            video_id, term = row[0], row[1]
            if term:
                top_term.setdefault(video_id, term)

    return {
        video_id: Discovery(source_label=_label(source_type), search_term=top_term.get(video_id, ""))
        for video_id, source_type in top_source.items()
    }
