"""Google OAuth (installed-app flow) for YouTube Data API access."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .config import CLIENT_SECRETS_PATH, TOKEN_PATH

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


class AuthError(RuntimeError):
    pass


def has_client_secrets() -> bool:
    return CLIENT_SECRETS_PATH.exists()


def install_client_secrets(source_path: Path) -> None:
    CLIENT_SECRETS_PATH.write_bytes(Path(source_path).read_bytes())


def load_cached_credentials() -> Optional[Credentials]:
    if not TOKEN_PATH.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        except RefreshError:
            return None
    return creds if creds and creds.valid else None


def run_oauth_flow() -> Credentials:
    if not has_client_secrets():
        raise AuthError(
            "No Google client secret configured. Add your OAuth client "
            "(Desktop app) JSON file in Settings first."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_PATH), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds


def sign_out() -> None:
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()


def get_credentials(interactive: bool = True) -> Credentials:
    creds = load_cached_credentials()
    if creds:
        return creds
    if not interactive:
        raise AuthError("Not signed in.")
    return run_oauth_flow()


def get_channel_summary(creds: Credentials) -> dict:
    youtube = build("youtube", "v3", credentials=creds)
    response = youtube.channels().list(part="snippet,statistics", mine=True).execute()
    items = response.get("items", [])
    if not items:
        raise AuthError("No YouTube channel found for this Google account.")
    channel = items[0]
    return {
        "id": channel["id"],
        "title": channel["snippet"]["title"],
        "thumbnail": channel["snippet"]["thumbnails"]["default"]["url"],
        "subscriber_count": channel.get("statistics", {}).get("subscriberCount"),
    }
