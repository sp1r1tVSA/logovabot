"""
services/animation_sender.py

High-Quality Telegram Animation (GIF/MP4) Processing, Encoding & Dispatch Pipeline.

Features:
1. Pure H.264 MP4 conversion using FFmpeg (libx264, preset slow, crf 14-16, yuv420p, +faststart, -an).
2. Direct Frame-to-MP4 (PNG/WebP/PIL frames -> FFmpeg -> MP4) bypassing GIF palette degradation.
3. Adaptive multi-tier quality degradation ladder (CRF 16 -> CRF 18 -> CRF 20 -> Scale 0.85 -> Scale 0.75).
4. Aspect ratio, resolution, and FPS preservation via ffprobe probing.
5. Telegram file_id deduplication & SQLite caching via SHA-256 media hashing.
6. Automatic temp file cleanup in finally blocks.
7. Graceful fallback to OpenCV/Pillow if FFmpeg is not installed on the system.
"""

import os
import io
import json
import math
import shutil
import logging
import hashlib
import tempfile
import subprocess
from dataclasses import dataclass
from typing import Any, List, Union

from PIL import Image

try:
    import numpy as np
except ImportError:
    np = None

try:
    import cv2
except ImportError:
    cv2 = None

import database

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 1. METADATA & DETECTION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MediaMetadata:
    width: int
    height: int
    fps: float
    duration: float
    format: str = "mp4"
    size_bytes: int = 0


def is_ffmpeg_available() -> bool:
    """Check if ffmpeg is available in system PATH."""
    return shutil.which("ffmpeg") is not None


def is_ffprobe_available() -> bool:
    """Check if ffprobe is available in system PATH."""
    return shutil.which("ffprobe") is not None


def probe_media(input_source: Union[str, bytes, io.BytesIO, List[Image.Image]]) -> MediaMetadata:
    """
    Probe media metadata (width, height, FPS, duration) using ffprobe or PIL/cv2 fallback.
    """
    # 1. List of PIL Images
    if isinstance(input_source, list) and len(input_source) > 0 and isinstance(input_source[0], Image.Image):
        w, h = input_source[0].size
        fps = 24.0
        duration = len(input_source) / fps
        return MediaMetadata(width=w, height=h, fps=fps, duration=duration, format="frames")

    # 2. Extract to a temp file if bytes or BytesIO
    temp_probe_file = None
    if isinstance(input_source, (bytes, io.BytesIO)):
        data = input_source.getvalue() if isinstance(input_source, io.BytesIO) else input_source
        temp_probe_file = tempfile.NamedTemporaryFile(suffix=".tmp", delete=False)
        temp_probe_file.write(data)
        temp_probe_file.close()
        probe_path = temp_probe_file.name
    else:
        probe_path = str(input_source)

    try:
        if is_ffprobe_available() and os.path.exists(probe_path):
            cmd = [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate,duration,nb_frames",
                "-show_entries", "format=duration,size",
                "-of", "json",
                probe_path
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            info = json.loads(res.stdout)

            stream = info.get("streams", [{}])[0]
            fmt = info.get("format", {})

            w = int(stream.get("width", 0))
            h = int(stream.get("height", 0))

            # FPS parsing (e.g. "30/1", "24000/1001", "24")
            r_fps_str = stream.get("r_frame_rate", "24/1")
            if "/" in r_fps_str:
                num, den = r_fps_str.split("/")
                fps = float(num) / float(den) if float(den) != 0 else 24.0
            else:
                fps = float(r_fps_str) if r_fps_str else 24.0

            # Duration parsing
            dur_str = stream.get("duration") or fmt.get("duration")
            if dur_str:
                duration = float(dur_str)
            else:
                nb_frames = int(stream.get("nb_frames", 0) or 0)
                duration = nb_frames / fps if fps > 0 and nb_frames > 0 else 1.0

            size = int(fmt.get("size", os.path.getsize(probe_path) if os.path.exists(probe_path) else 0))
            return MediaMetadata(width=w, height=h, fps=fps, duration=duration, size_bytes=size)
    except Exception as e:
        logger.debug(f"ffprobe failed or unavailable ({e}), falling back to PIL/cv2")

    # 3. Fallback probing via PIL or cv2
    try:
        if os.path.exists(probe_path):
            # Try PIL (for GIF / image sequences)
            try:
                with Image.open(probe_path) as pil_img:
                    w, h = pil_img.size
                    n_frames = getattr(pil_img, "n_frames", 1)
                    duration_ms = pil_img.info.get("duration", 40) or 40
                    fps = 1000.0 / duration_ms if duration_ms > 0 else 24.0
                    duration = (n_frames * duration_ms) / 1000.0 if n_frames > 1 else 1.0
                    return MediaMetadata(width=w, height=h, fps=fps, duration=duration, size_bytes=os.path.getsize(probe_path))
            except Exception:
                pass

            # Try OpenCV (for MP4 / Video)
            if cv2 is not None:
                cap = cv2.VideoCapture(probe_path)
                if cap.isOpened():
                    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    fps = float(cap.get(cv2.CAP_PROP_FPS) or 24.0)
                    nb_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                    duration = nb_frames / fps if fps > 0 and nb_frames > 0 else 1.0
                    cap.release()
                    return MediaMetadata(width=w, height=h, fps=fps, duration=duration, size_bytes=os.path.getsize(probe_path))
    except Exception as e:
        logger.warning(f"Error in fallback probing: {e}")
    finally:
        if temp_probe_file and os.path.exists(temp_probe_file.name):
            try:
                os.remove(temp_probe_file.name)
            except Exception:
                pass

    return MediaMetadata(width=480, height=680, fps=24.0, duration=1.0)


# ─────────────────────────────────────────────────────────────────────────────
# 2. DETERMINISTIC SHA-256 MEDIA HASHING
# ─────────────────────────────────────────────────────────────────────────────

def compute_media_hash(input_source: Union[str, bytes, io.BytesIO, List[Image.Image]]) -> str:
    """
    Compute a deterministic SHA-256 digest of media input for Telegram file_id caching.
    """
    hasher = hashlib.sha256()

    if isinstance(input_source, str):
        if os.path.isfile(input_source):
            with open(input_source, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            return hasher.hexdigest()
        else:
            # Identifier or file_id string
            hasher.update(input_source.encode("utf-8"))
            return hasher.hexdigest()

    elif isinstance(input_source, io.BytesIO):
        curr_pos = input_source.tell()
        input_source.seek(0)
        data = input_source.read()
        input_source.seek(curr_pos)
        hasher.update(data)
        return hasher.hexdigest()

    elif isinstance(input_source, bytes):
        hasher.update(input_source)
        return hasher.hexdigest()

    elif isinstance(input_source, list) and len(input_source) > 0 and isinstance(input_source[0], Image.Image):
        # Hash frame dimensions, frame count, and sampled pixels
        hasher.update(f"frames_count:{len(input_source)}_size:{input_source[0].size}".encode("utf-8"))
        for idx, f in enumerate(input_source):
            # Hash downsampled frame or full byte buffer
            buf = io.BytesIO()
            f.save(buf, format="PNG", compress_level=1)
            hasher.update(buf.getvalue())
        return hasher.hexdigest()

    hasher.update(str(input_source).encode("utf-8"))
    return hasher.hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# 3. HIGH-QUALITY FFMPEG & FALLBACK MP4 CONVERSION
# ─────────────────────────────────────────────────────────────────────────────

def convert_to_high_quality_mp4(
    input_source: Union[str, bytes, io.BytesIO, List[Image.Image]],
    output_mp4_path: str | None = None,
    max_size_bytes: int = 20 * 1024 * 1024,
    initial_crf: int = 16,
    preset: str = "slow",
    fps: float | None = None,
) -> tuple[str, MediaMetadata, bool]:
    """
    Converts GIF, video, or frame sequence into high-quality H.264 MP4.
    
    Adheres strictly to the quality ladder:
      CRF 16 (Original Resolution)
        ↓ if file size exceeds max_size_bytes
      CRF 18 (Original Resolution)
        ↓
      CRF 20 (Original Resolution)
        ↓
      CRF 20 (Scale 0.85)
        ↓
      CRF 21 (Scale 0.75)

    Returns:
      (output_mp4_path, metadata, is_temp_file_bool)
    """
    is_temp = False
    if output_mp4_path is None:
        temp_out = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        output_mp4_path = temp_out.name
        temp_out.close()
        is_temp = True

    # 1. Probe input metadata
    meta = probe_media(input_source)
    target_fps = fps or meta.fps or 24.0
    if target_fps <= 0:
        target_fps = 24.0

    # 2. Quality Levels Ladder
    quality_levels = [
        {"crf": initial_crf, "scale": None, "fps_cap": None},
        {"crf": 18, "scale": None, "fps_cap": None},
        {"crf": 20, "scale": None, "fps_cap": None},
        {"crf": 20, "scale": 0.85, "fps_cap": None},
        {"crf": 21, "scale": 0.75, "fps_cap": 30.0 if target_fps > 30 else None},
    ]

    temp_files_to_clean = []
    temp_dir_to_clean = None

    try:
        # A. FFmpeg Available in System PATH
        if is_ffmpeg_available():
            input_args = []
            if isinstance(input_source, list) and len(input_source) > 0 and isinstance(input_source[0], Image.Image):
                # Save frames to temp directory for FFmpeg ingestion
                temp_dir_to_clean = tempfile.mkdtemp(prefix="frames_")
                for i, frame in enumerate(input_source):
                    f_path = os.path.join(temp_dir_to_clean, f"frame_{i:05d}.png")
                    frame.save(f_path, format="PNG")
                input_args = [
                    "-framerate", str(target_fps),
                    "-i", os.path.join(temp_dir_to_clean, "frame_%05d.png")
                ]
            elif isinstance(input_source, (bytes, io.BytesIO)):
                raw_bytes = input_source.getvalue() if isinstance(input_source, io.BytesIO) else input_source
                t_in = tempfile.NamedTemporaryFile(suffix=".gif", delete=False)
                t_in.write(raw_bytes)
                t_in.close()
                temp_files_to_clean.append(t_in.name)
                input_args = ["-i", t_in.name]
            else:
                input_args = ["-i", str(input_source)]

            best_output_path = None
            for q in quality_levels:
                crf = q["crf"]
                scale = q["scale"]
                fps_cap = q["fps_cap"]

                # Build video filter chain ensuring even dimensions (divisible by 2 for yuv420p)
                filters = []
                if scale:
                    filters.append(f"scale=trunc(iw*{scale}/2)*2:trunc(ih*{scale}/2)*2")
                else:
                    filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")

                if fps_cap and target_fps > fps_cap:
                    filters.append(f"fps={fps_cap}")

                vf_str = ",".join(filters)

                cmd = [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel", "error",
                    *input_args,
                    "-vf", vf_str,
                    "-c:v", "libx264",
                    "-preset", preset,
                    "-crf", str(crf),
                    "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart",
                    "-an",
                    output_mp4_path
                ]

                try:
                    subprocess.run(cmd, capture_output=True, check=True)
                    if os.path.exists(output_mp4_path) and os.path.getsize(output_mp4_path) > 0:
                        sz = os.path.getsize(output_mp4_path)
                        best_output_path = output_mp4_path
                        if sz <= max_size_bytes:
                            logger.info(f"FFmpeg conversion succeeded (CRF={crf}, scale={scale}): {sz/1024:.1f} KB")
                            break
                except subprocess.CalledProcessError as err:
                    logger.warning(f"FFmpeg conversion attempt failed (CRF={crf}): {err.stderr.decode('utf-8', errors='ignore')}")

            if best_output_path and os.path.exists(best_output_path):
                final_meta = probe_media(best_output_path)
                return best_output_path, final_meta, is_temp

        # B. Fallback when FFmpeg is not available in PATH (OpenCV / PIL)
        logger.warning("FFmpeg not found in system PATH. Executing OpenCV / Pillow fallback for MP4 conversion.")
        
        frames_list = []
        if isinstance(input_source, list) and len(input_source) > 0 and isinstance(input_source[0], Image.Image):
            frames_list = input_source
        elif isinstance(input_source, (bytes, io.BytesIO)):
            raw_bytes = input_source.getvalue() if isinstance(input_source, io.BytesIO) else input_source
            with Image.open(io.BytesIO(raw_bytes)) as img:
                for f_i in range(getattr(img, "n_frames", 1)):
                    img.seek(f_i)
                    frames_list.append(img.copy().convert("RGB"))
        elif isinstance(input_source, str) and os.path.exists(input_source):
            with Image.open(input_source) as img:
                for f_i in range(getattr(img, "n_frames", 1)):
                    img.seek(f_i)
                    frames_list.append(img.copy().convert("RGB"))

        if frames_list and cv2 is not None and np is not None:
            w, h = frames_list[0].size
            # Ensure even dimensions
            w = (w // 2) * 2
            h = (h // 2) * 2
            
            for codec in ["mp4v", "avc1", "H264", "XVID"]:
                try:
                    fourcc = cv2.VideoWriter_fourcc(*codec)
                    writer = cv2.VideoWriter(output_mp4_path, fourcc, target_fps, (w, h))
                    if writer.isOpened():
                        for frame in frames_list:
                            if frame.size != (w, h):
                                frame = frame.resize((w, h), Image.Resampling.LANCZOS)
                            arr = np.array(frame.convert("RGB"))
                            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
                            writer.write(bgr)
                        writer.release()
                        if os.path.exists(output_mp4_path) and os.path.getsize(output_mp4_path) > 1000:
                            final_meta = MediaMetadata(width=w, height=h, fps=target_fps, duration=len(frames_list)/target_fps, size_bytes=os.path.getsize(output_mp4_path))
                            return output_mp4_path, final_meta, is_temp
                except Exception as e:
                    logger.warning(f"OpenCV codec {codec} failed: {e}")

        # If everything fails, write GIF to output path
        if frames_list:
            frames_list[0].save(
                output_mp4_path,
                format="GIF",
                save_all=True,
                append_images=frames_list[1:],
                duration=int(1000.0 / target_fps),
                loop=0,
                optimize=True
            )
            final_meta = MediaMetadata(width=frames_list[0].width, height=frames_list[0].height, fps=target_fps, duration=len(frames_list)/target_fps, size_bytes=os.path.getsize(output_mp4_path))
            return output_mp4_path, final_meta, is_temp

    finally:
        for f in temp_files_to_clean:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass
        if temp_dir_to_clean and os.path.exists(temp_dir_to_clean):
            try:
                shutil.rmtree(temp_dir_to_clean, ignore_errors=True)
            except Exception:
                pass

    return output_mp4_path, meta, is_temp


# ─────────────────────────────────────────────────────────────────────────────
# 4. TELEGRAM HIGH-QUALITY ANIMATION DISPATCHER & CACHING
# ─────────────────────────────────────────────────────────────────────────────

async def send_high_quality_animation(
    bot: Any,
    chat_id: Union[int, str],
    animation_input: Union[str, bytes, io.BytesIO, List[Image.Image]],
    caption: str | None = None,
    parse_mode: str | None = "HTML",
    reply_markup: Any = None,
    filename: str = "animation.mp4",
    max_size_bytes: int = 20 * 1024 * 1024,
    initial_crf: int = 16,
    **kwargs
) -> Any:
    """
    Sends an animation in highest possible visual fidelity to Telegram:
    1. Computes SHA-256 hash of source media.
    2. Checks SQLite database for cached Telegram file_id (Instant 0ms dispatch if cached).
    3. If not cached, encodes via FFmpeg libx264 (CRF 16) directly to MP4.
    4. Sends via bot.send_animation with width, height, and duration parameters.
    5. Caches the newly generated file_id in the database for instant future re-sends.
    6. Ensures 100% cleanup of temporary files in finally blocks.
    """
    # Check if animation_input is already a raw Telegram file_id string
    if isinstance(animation_input, str) and not os.path.exists(animation_input) and len(animation_input) > 20:
        return await bot.send_animation(
            chat_id=chat_id,
            animation=animation_input,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            **kwargs
        )

    # 1. Compute deterministic SHA-256 hash
    file_hash = compute_media_hash(animation_input)

    # 2. Check Database Cache
    cached_file_id = database.get_cached_telegram_media(file_hash, media_type="animation")
    if cached_file_id:
        logger.info(f"⚡ [MediaCache] Reusing cached Telegram file_id {cached_file_id[:16]}... for hash {file_hash[:8]}")
        try:
            return await bot.send_animation(
                chat_id=chat_id,
                animation=cached_file_id,
                caption=caption,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                **kwargs
            )
        except Exception as e:
            logger.warning(f"Failed to send with cached file_id {cached_file_id}: {e}. Regenerating and re-uploading media.")

    # 3. Convert input to pristine H.264 MP4
    mp4_path, meta, is_temp = convert_to_high_quality_mp4(
        input_source=animation_input,
        max_size_bytes=max_size_bytes,
        initial_crf=initial_crf
    )

    clean_filename = filename if filename.endswith(".mp4") else f"{filename}.mp4"

    try:
        with open(mp4_path, "rb") as f_anim:
            result = await bot.send_animation(
                chat_id=chat_id,
                animation=f_anim,
                filename=clean_filename,
                width=meta.width if meta.width > 0 else None,
                height=meta.height if meta.height > 0 else None,
                duration=int(meta.duration) if meta.duration > 0 else None,
                caption=caption,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                **kwargs
            )

        # 4. Save file_id to Cache
        file_id = None
        if hasattr(result, "animation") and result.animation:
            file_id = result.animation.file_id
        elif hasattr(result, "document") and result.document:
            file_id = result.document.file_id
        elif hasattr(result, "video") and result.video:
            file_id = result.video.file_id

        if file_id:
            database.save_cached_telegram_media(file_hash, file_id, media_type="animation")
            logger.info(f"💾 [MediaCache] Saved file_id {file_id[:16]}... for hash {file_hash[:8]}")

        return result

    finally:
        if is_temp and os.path.exists(mp4_path):
            try:
                os.remove(mp4_path)
            except Exception:
                pass
