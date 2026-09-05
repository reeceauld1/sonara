"""Scan public YouTube uploads (Data API search) to see what other producers in
a scene are posting — used to spot artist names that trend right now and names
that keep showing up together in titles.

Note: search.list costs 100 quota units per call; the default is two calls
(~200 units) per refresh, well within a standard 10,000/day project quota.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

DEFAULT_LOOKBACK_DAYS = 180
DEFAULT_MAX_RESULTS = 100

SCENE_QUERIES = {
    "dmv": "dmv type beat",
    "atlanta": "atlanta type beat",
}


def scene_query(scene: str) -> str:
    return SCENE_QUERIES.get((scene or "").lower().strip(), f"{scene} type beat")


def fetch_market_titles(
    creds: Credentials,
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[str]:
    """Public video titles matching `query` uploaded within the lookback window,
    newest-relevant first. Best-effort: returns whatever it got if a page fails."""
    youtube = build("youtube", "v3", credentials=creds)
    published_after = (
        datetime.now(timezone.utc) - timedelta(days=lookback_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    titles: list[str] = []
    page_token = None
    while len(titles) < max_results:
        response = (
            youtube.search()
            .list(
                part="snippet",
                q=query,
                type="video",
                order="relevance",
                maxResults=50,
                publishedAfter=published_after,
                regionCode="US",
                relevanceLanguage="en",
                pageToken=page_token,
            )
            .execute()
        )
        for item in response.get("items", []):
            title = item.get("snippet", {}).get("title", "")
            if title:
                titles.append(title)
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return titles[:max_results]
