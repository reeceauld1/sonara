"""Local AI image upscaling via a bundled Real-ESRGAN ONNX model (4x), run
fully offline on CPU through onnxruntime. The model itself isn't shipped with
the app; it's downloaded once into the app-data folder on first use and
cached there after (see `ensure_model`).

Qualcomm's published export (qualcomm/Real-ESRGAN-x4plus on Hugging Face)
doesn't host the .onnx file on the Hub itself - the repo's release_assets.json
points at a public S3 bucket serving a zip with the graph split into a small
.onnx file plus its weights in a separate .data file (ONNX's "external data"
convention for large tensors) that must sit next to it. `ensure_model` pulls
that zip and unpacks both into place; `is_model_downloaded` checks for both.

The model's own input tile is small and fixed-size (128x128 for this
export), so a full image is upscaled by sliding that tile across it with
overlap and feathering the overlaps back together (`_feather_mask`) so tile
seams don't show in the result.
"""
from __future__ import annotations

import shutil
import urllib.request
import zipfile
from typing import Callable, Optional

import numpy as np
from PIL import Image

from core.config import MODELS_DIR

MODEL_URL = (
    "https://qaihub-public-assets.s3.us-west-2.amazonaws.com/qai-hub-models/"
    "models/real_esrgan_x4plus/releases/v0.61.0/real_esrgan_x4plus-onnx-float.zip"
)
MODEL_ONNX_FILENAME = "real_esrgan_x4plus.onnx"
MODEL_DATA_FILENAME = "real_esrgan_x4plus.data"
# Paths of the two files inside the downloaded zip's single top-level folder.
_ZIP_ONNX_MEMBER = "real_esrgan_x4plus-onnx-float/real_esrgan_x4plus.onnx"
_ZIP_DATA_MEMBER = "real_esrgan_x4plus-onnx-float/real_esrgan_x4plus.data"

DEFAULT_TILE_SIZE = 128  # fallback net input size if the model doesn't report a fixed one
DEFAULT_SCALE = 4
TILE_OVERLAP = 8  # px of overlap (in input-tile space) feathered at tile borders

ProgressCallback = Optional[Callable[[float], None]]


class UpscaleError(RuntimeError):
    pass


def model_path():
    """Path to the .onnx graph file. Its sibling .data weights file
    (`_data_path`) must be downloaded alongside it for this to load."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    return MODELS_DIR / MODEL_ONNX_FILENAME


def _data_path():
    return MODELS_DIR / MODEL_DATA_FILENAME


def is_model_downloaded() -> bool:
    return model_path().exists() and _data_path().exists()


def ensure_model(on_progress: ProgressCallback = None):
    """Downloads and unpacks the upscale model into the app-data folder if
    it isn't already there. Safe to call every time; a no-op once cached."""
    onnx_dest = model_path()
    data_dest = _data_path()
    if onnx_dest.exists() and data_dest.exists():
        if on_progress:
            on_progress(1.0)
        return onnx_dest

    zip_path = MODELS_DIR / "real_esrgan_x4plus.zip.part"
    request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "Sonara"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response, open(zip_path, "wb") as out:
            total = int(response.headers.get("Content-Length", 0))
            read = 0
            while True:
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                read += len(chunk)
                # Downloading the zip is the bulk of the wait; leave headroom
                # in the reported fraction for unpacking it afterward.
                if on_progress and total > 0:
                    on_progress(min(read / total, 1.0) * 0.95)

        onnx_tmp = onnx_dest.with_name(onnx_dest.name + ".part")
        data_tmp = data_dest.with_name(data_dest.name + ".part")
        with zipfile.ZipFile(zip_path) as archive:
            with archive.open(_ZIP_ONNX_MEMBER) as src, open(onnx_tmp, "wb") as out:
                shutil.copyfileobj(src, out)
            with archive.open(_ZIP_DATA_MEMBER) as src, open(data_tmp, "wb") as out:
                shutil.copyfileobj(src, out)
        onnx_tmp.replace(onnx_dest)
        data_tmp.replace(data_dest)
    except Exception as exc:  # noqa: BLE001
        raise UpscaleError(f"Downloading the AI upscale model failed: {exc}") from exc
    finally:
        zip_path.unlink(missing_ok=True)

    if on_progress:
        on_progress(1.0)
    return onnx_dest

    tmp_dest.replace(dest)
    return dest


def _tile_starts(total: int, tile: int, stride: int) -> list[int]:
    """Tile offsets covering [0, total) with the given tile size and stride,
    always ending with a final tile flush against the far edge."""
    if total <= tile:
        return [0]
    starts = list(range(0, total - tile + 1, stride))
    if starts[-1] + tile < total:
        starts.append(total - tile)
    return starts


def _feather_mask(size: int, overlap: int) -> np.ndarray:
    """A size x size weight map that ramps 0->1 across `overlap` px at each
    edge, so overlapping tiles blend smoothly instead of showing seams."""
    ramp = np.ones(size, dtype=np.float32)
    if overlap > 0:
        edge = np.linspace(0.0, 1.0, overlap, dtype=np.float32)
        ramp[:overlap] = edge
        ramp[-overlap:] = edge[::-1]
    return np.minimum.outer(ramp, ramp)[:, :, None]


class Upscaler:
    """Wraps an onnxruntime session for the bundled Real-ESRGAN model. Loading
    the session (a few hundred ms) happens once, lazily, on first use."""

    def __init__(self):
        self._session = None
        self._tile_in = DEFAULT_TILE_SIZE
        self._scale = DEFAULT_SCALE
        self._input_name = ""

    def _load(self) -> None:
        if self._session is not None:
            return
        import onnxruntime as ort  # deferred: heavy import, only needed here

        path = model_path()
        if not path.exists():
            raise UpscaleError("AI upscale model isn't downloaded yet.")
        try:
            session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        except Exception as exc:  # noqa: BLE001
            raise UpscaleError(f"Failed to load the AI upscale model: {exc}") from exc

        input_info = session.get_inputs()[0]
        output_info = session.get_outputs()[0]
        self._input_name = input_info.name

        in_dims = [d for d in input_info.shape if isinstance(d, int) and d > 0]
        if len(in_dims) >= 2:
            self._tile_in = min(in_dims[-2:])
        out_dims = [d for d in output_info.shape if isinstance(d, int) and d > 0]
        if len(out_dims) >= 2 and self._tile_in:
            self._scale = max(1, round(min(out_dims[-2:]) / self._tile_in))

        self._session = session

    @property
    def scale(self) -> int:
        self._load()
        return self._scale

    def upscale(self, image: Image.Image, on_progress: ProgressCallback = None) -> Image.Image:
        """Runs AI super-resolution over the whole image, tiling it through
        the (small, fixed-size) model input and feathering the seams back
        together. Returns a new RGB image at `self.scale`x the source
        resolution."""
        self._load()
        assert self._session is not None
        tile_in = self._tile_in
        scale = self._scale
        overlap = min(TILE_OVERLAP, tile_in // 4)
        stride = max(1, tile_in - overlap)

        rgb = image.convert("RGB")
        src_w, src_h = rgb.size

        padded_w, padded_h = max(src_w, tile_in), max(src_h, tile_in)
        pad_w, pad_h = padded_w - src_w, padded_h - src_h
        source = np.array(rgb, dtype=np.float32) / 255.0
        padded = np.pad(source, ((0, pad_h), (0, pad_w), (0, 0)), mode="reflect")

        xs = _tile_starts(padded_w, tile_in, stride)
        ys = _tile_starts(padded_h, tile_in, stride)

        out_h, out_w = padded_h * scale, padded_w * scale
        canvas = np.zeros((out_h, out_w, 3), dtype=np.float32)
        weight = np.zeros((out_h, out_w, 1), dtype=np.float32)
        blend = _feather_mask(tile_in * scale, overlap * scale)

        total = len(xs) * len(ys)
        done = 0
        for y in ys:
            for x in xs:
                tile = padded[y : y + tile_in, x : x + tile_in, :]
                tensor = tile.transpose(2, 0, 1)[None, ...].astype(np.float32)  # NCHW
                result = self._session.run(None, {self._input_name: tensor})[0]
                out_tile = np.clip(result[0].transpose(1, 2, 0), 0.0, 1.0)

                oy, ox = y * scale, x * scale
                oh, ow = out_tile.shape[:2]
                canvas[oy : oy + oh, ox : ox + ow, :] += out_tile * blend[:oh, :ow]
                weight[oy : oy + oh, ox : ox + ow, :] += blend[:oh, :ow]

                done += 1
                if on_progress:
                    on_progress(done / total)

        canvas = np.divide(canvas, weight, out=np.zeros_like(canvas), where=weight > 0)
        canvas = canvas[: src_h * scale, : src_w * scale, :]
        result_arr = np.clip(canvas * 255.0, 0, 255).astype(np.uint8)
        return Image.fromarray(result_arr, mode="RGB")


_shared_upscaler: Optional[Upscaler] = None


def get_upscaler() -> Upscaler:
    """A process-wide cached Upscaler so repeated upscales in one session
    don't reload the ONNX session (a few hundred ms) every time."""
    global _shared_upscaler
    if _shared_upscaler is None:
        _shared_upscaler = Upscaler()
    return _shared_upscaler
