"""Media metadata extractors — images, audio, video.

Extracts metadata (EXIF, ID3 tags, etc.) and formats it as searchable text.
All dependencies are optional.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("yaaos-sfs")


def extract_image_metadata(path: Path) -> str | None:
    """Extract EXIF and basic metadata from images using Pillow."""
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS, GPSTAGS

        img = Image.open(str(path))
        parts = [f"Image: {path.name}"]
        parts.append(f"Format: {img.format}")
        parts.append(f"Size: {img.size[0]}x{img.size[1]}")
        parts.append(f"Mode: {img.mode}")

        # Extract EXIF
        exif_data = img.getexif()
        if exif_data:
            for tag_id, value in exif_data.items():
                tag_name = TAGS.get(tag_id, str(tag_id))
                if tag_name in (
                    "Make",
                    "Model",
                    "DateTime",
                    "DateTimeOriginal",
                    "Software",
                    "Artist",
                    "Copyright",
                    "ImageDescription",
                    "UserComment",
                    "XPTitle",
                    "XPComment",
                    "XPSubject",
                    "XPKeywords",
                ):
                    # Clean up byte values
                    if isinstance(value, bytes):
                        try:
                            value = value.decode("utf-8", errors="replace").strip("\x00")
                        except Exception:
                            continue
                    if value:
                        parts.append(f"{tag_name}: {value}")

            # GPS data
            gps_info = exif_data.get_ifd(0x8825)
            if gps_info:
                gps_parts = []
                for tag_id, value in gps_info.items():
                    tag_name = GPSTAGS.get(tag_id, str(tag_id))
                    gps_parts.append(f"{tag_name}: {value}")
                if gps_parts:
                    parts.append("GPS: " + ", ".join(gps_parts))

        img.close()
        return "\n".join(parts) if len(parts) > 3 else "\n".join(parts)
    except Exception as e:
        log.debug(f"Image metadata extraction failed for {path.name}: {e}")
        return None


def extract_audio_metadata(path: Path) -> str | None:
    """Extract ID3/audio tags using mutagen."""
    try:
        import mutagen

        audio = mutagen.File(str(path), easy=True)
        if audio is None:
            return None

        parts = [f"Audio: {path.name}"]

        # Duration
        if audio.info and hasattr(audio.info, "length"):
            minutes = int(audio.info.length // 60)
            seconds = int(audio.info.length % 60)
            parts.append(f"Duration: {minutes}:{seconds:02d}")

        if audio.info and hasattr(audio.info, "bitrate"):
            parts.append(f"Bitrate: {audio.info.bitrate // 1000}kbps")

        if audio.info and hasattr(audio.info, "sample_rate"):
            parts.append(f"Sample rate: {audio.info.sample_rate}Hz")

        # Tags
        tag_fields = [
            "title",
            "artist",
            "album",
            "albumartist",
            "genre",
            "date",
            "tracknumber",
            "composer",
            "comment",
        ]
        if audio.tags:
            for field in tag_fields:
                values = audio.get(field)
                if values:
                    val = ", ".join(str(v) for v in values)
                    parts.append(f"{field.capitalize()}: {val}")

        return "\n".join(parts) if len(parts) > 1 else None
    except Exception as e:
        log.debug(f"Audio metadata extraction failed for {path.name}: {e}")
        return None


def extract_video_metadata(path: Path) -> str | None:
    """Extract video metadata using mutagen (for container formats that support it)."""
    try:
        import mutagen

        video = mutagen.File(str(path))
        if video is None:
            return None

        parts = [f"Video: {path.name}"]

        if video.info and hasattr(video.info, "length"):
            minutes = int(video.info.length // 60)
            seconds = int(video.info.length % 60)
            parts.append(f"Duration: {minutes}:{seconds:02d}")

        if video.info and hasattr(video.info, "bitrate"):
            parts.append(f"Bitrate: {video.info.bitrate // 1000}kbps")

        # For MP4/M4A, try to get tags
        if video.tags:
            tag_map = {
                "\xa9nam": "Title",
                "\xa9ART": "Artist",
                "\xa9alb": "Album",
                "\xa9day": "Year",
                "\xa9gen": "Genre",
                "\xa9cmt": "Comment",
                "desc": "Description",
            }
            for key, label in tag_map.items():
                val = video.tags.get(key)
                if val:
                    text = ", ".join(str(v) for v in val) if isinstance(val, list) else str(val)
                    parts.append(f"{label}: {text}")

        return "\n".join(parts) if len(parts) > 1 else None
    except Exception as e:
        log.debug(f"Video metadata extraction failed for {path.name}: {e}")
        return None


def register_extractors() -> None:
    """Register media extractors for available libraries."""
    from . import register

    _optional_register(
        register,
        [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp"],
        extract_image_metadata,
        "Pillow",
        "PIL",
    )
    _optional_register(
        register,
        [".mp3", ".wav", ".flac", ".m4a", ".ogg", ".wma", ".aac", ".opus"],
        extract_audio_metadata,
        "mutagen",
        "mutagen",
    )
    _optional_register(
        register,
        [".mp4", ".mkv", ".avi", ".webm", ".mov", ".wmv"],
        extract_video_metadata,
        "mutagen (video)",
        "mutagen",
    )


def _optional_register(register_fn, extensions, extractor, pkg_name, import_name):
    """Register an extractor only if its dependency can be imported."""
    try:
        __import__(import_name)
        register_fn(extensions, extractor)
    except ImportError:
        log.debug(f"{pkg_name} not installed — skipping {extensions} support")
