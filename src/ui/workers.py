"""Background QThread workers so the UI never blocks on ffmpeg or network I/O."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal

from core import type_beat
from core import updater
from core import video as video_core
from core import youtube_auth
from core import youtube_market
from core.audio_tag import AudioTagSpec, apply_audio_tag
from core.youtube_discovery import fetch_discovery_info
from core.youtube_stats import download_thumbnail, fetch_video_stats
from core.youtube_upload import UploadRequest, set_thumbnail, upload_video


@dataclass
class PublishJob:
    audio_path: str
    image_path: str
    upload_request: UploadRequest
    set_custom_thumbnail: bool = True
    watermark: Optional[video_core.Watermark] = None
    audio_tag: Optional[AudioTagSpec] = None


class PublishWorker(QObject):
    stage_changed = Signal(str)
    progress = Signal(float)          # 0..1 within the current stage
    finished = Signal(str)            # video id
    failed = Signal(str)

    def __init__(self, job: PublishJob, work_dir: Path):
        super().__init__()
        self._job = job
        self._work_dir = work_dir

    def run(self) -> None:
        try:
            audio_path = Path(self._job.audio_path)
            if self._job.audio_tag is not None:
                self.stage_changed.emit("Adding audio tag")
                tag = self._job.audio_tag
                audio_path = apply_audio_tag(
                    audio_path,
                    Path(tag.tag_path),
                    tag.bpm,
                    tag.bars_per_tag,
                    self._work_dir / "tagged_audio.wav",
                    second_tag_path=Path(tag.second_tag_path) if tag.second_tag_path else None,
                )

            self.stage_changed.emit("Rendering video")
            out_path = self._work_dir / "render.mp4"
            video_core.build_video(
                Path(self._job.image_path),
                audio_path,
                out_path,
                watermark=self._job.watermark,
                on_progress=self.progress.emit,
            )

            self.stage_changed.emit("Signing in")
            creds = youtube_auth.get_credentials(interactive=True)

            self.stage_changed.emit("Uploading to YouTube")
            self._job.upload_request.video_path = str(out_path)
            video_id = upload_video(creds, self._job.upload_request, on_progress=self.progress.emit)

            if self._job.set_custom_thumbnail:
                self.stage_changed.emit("Setting thumbnail")
                try:
                    set_thumbnail(creds, video_id, self._job.image_path)
                except Exception:
                    # Custom thumbnails require a phone-verified channel; not fatal.
                    pass

            self.finished.emit(video_id)
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            self.failed.emit(str(exc))


class SignInWorker(QObject):
    finished = Signal(dict)
    failed = Signal(str)

    def run(self) -> None:
        try:
            creds = youtube_auth.run_oauth_flow()
            summary = youtube_auth.get_channel_summary(creds)
            self.finished.emit(summary)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class AnalyticsWorker(QObject):
    # list[VideoStats], {video_id: thumbnail bytes}, {video_id: Discovery}
    finished = Signal(list, dict, dict)
    failed = Signal(str)

    def run(self) -> None:
        try:
            creds = youtube_auth.get_credentials(interactive=False)
            stats = fetch_video_stats(creds)

            thumbnails = {}
            for v in stats:
                data = download_thumbnail(v.thumbnail_url)
                if data:
                    thumbnails[v.video_id] = data

            try:
                discovery = fetch_discovery_info(creds, [v.video_id for v in stats])
            except Exception:
                # Needs the yt-analytics.readonly scope + the YouTube Analytics
                # API enabled in the user's Cloud project — both are optional
                # extras, so a missing one degrades to "no discovery data"
                # rather than failing the whole analytics load.
                discovery = {}

            self.finished.emit(stats, thumbnails, discovery)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class SuggestionsWorker(QObject):
    finished = Signal(object)  # type_beat.SuggestionReport
    failed = Signal(str)

    def __init__(self, scene: str = type_beat.DEFAULT_SCENE, scan_market: bool = True):
        super().__init__()
        self._scene = scene
        self._scan_market = scan_market

    def run(self) -> None:
        try:
            creds = youtube_auth.get_credentials(interactive=False)
            stats = fetch_video_stats(creds)
            # Only your public uploads — private/unlisted drafts shouldn't shape
            # what the channel looks like it covers.
            entries = [
                type_beat.TitleEntry(title=s.title, views=s.views)
                for s in stats
                if s.privacy_status == "public"
            ]

            market_titles: list[str] = []
            if self._scan_market:
                try:
                    market_titles = youtube_market.fetch_market_titles(
                        creds, youtube_market.scene_query(self._scene)
                    )
                except Exception:  # noqa: BLE001 - scan is a bonus, never fatal
                    market_titles = []

            self.finished.emit(
                type_beat.analyze(entries, scene=self._scene, market_titles=market_titles)
            )
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class MarketWorker(QObject):
    """Scan-only: what other channels in the scene are posting (no account
    data). Powers the Trends page."""

    finished = Signal(object)  # type_beat.MarketScan
    failed = Signal(str)

    def __init__(self, scene: str = type_beat.DEFAULT_SCENE):
        super().__init__()
        self._scene = scene

    def run(self) -> None:
        try:
            creds = youtube_auth.get_credentials(interactive=False)
            titles = youtube_market.fetch_market_titles(
                creds, youtube_market.scene_query(self._scene)
            )
            self.finished.emit(type_beat.scan_market(titles))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class UpdateCheckWorker(QObject):
    found = Signal(object)  # updater.UpdateInfo
    none_found = Signal()
    failed = Signal(str)

    def run(self) -> None:
        try:
            info = updater.check_for_update()
        except Exception as exc:  # noqa: BLE001 - a failed check should never bother the user
            self.failed.emit(str(exc))
            return
        if info:
            self.found.emit(info)
        else:
            self.none_found.emit()


class UpdateDownloadWorker(QObject):
    progress = Signal(float)
    ready = Signal(Path)  # path to the downloaded exe, ready to apply
    failed = Signal(str)

    def __init__(self, info: updater.UpdateInfo):
        super().__init__()
        self._info = info

    def run(self) -> None:
        try:
            path = updater.download_update(self._info, on_progress=self.progress.emit)
            self.ready.emit(path)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


def run_worker_in_thread(worker: QObject, parent: Optional[QObject] = None) -> QThread:
    thread = QThread(parent)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    return thread
