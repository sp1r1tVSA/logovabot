"""
services module package.
"""

try:
    from services.animation_sender import (
        MediaMetadata,
        is_ffmpeg_available,
        is_ffprobe_available,
        probe_media,
        compute_media_hash,
        convert_to_high_quality_mp4,
        send_high_quality_animation
    )
except ImportError:
    pass

__all__ = [
    "MediaMetadata",
    "is_ffmpeg_available",
    "is_ffprobe_available",
    "probe_media",
    "compute_media_hash",
    "convert_to_high_quality_mp4",
    "send_high_quality_animation",
]
