"""ffprobe wrapper: reads stream info and decides whether a file is HDR."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".hevc", ".h265", ".webm", ".avi", ".ts", ".mts", ".m2ts"}

PQ_TRC = {"smpte2084"}
HLG_TRC = {"arib-std-b67"}


@dataclass
class MediaInfo:
    path: str
    width: int = 0
    height: int = 0
    fps: float = 0.0
    duration: float = 0.0
    bitrate: int = 0          # bits/s, whole file
    vbitrate: int = 0         # bits/s, video stream if known
    vcodec: str = ""
    pix_fmt: str = ""
    color_trc: str = ""
    color_primaries: str = ""
    color_space: str = ""
    has_audio: bool = False
    acodec: str = ""
    is_hdr: bool = False
    hdr_kind: str = ""        # "PQ" (HDR10/HDR10+), "HLG" or ""
    has_hdr10plus: bool = False
    error: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def size_mb(self) -> float:
        try:
            return os.path.getsize(self.path) / 1e6
        except OSError:
            return 0.0

    @property
    def bit_depth(self) -> int:
        return 10 if "10" in self.pix_fmt else (12 if "12" in self.pix_fmt else 8)


def _parse_fps(rate: str) -> float:
    try:
        if "/" in rate:
            a, b = rate.split("/")
            return float(a) / float(b) if float(b) else 0.0
        return float(rate)
    except (ValueError, ZeroDivisionError):
        return 0.0


def is_video_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO_EXTS


def _ffprobe_json(ffprobe: str, args: list[str]) -> tuple[dict, str]:
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-print_format", "json", *args],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {}, str(e)
    if out.returncode != 0:
        return {}, out.stderr.strip() or f"ffprobe exit {out.returncode}"
    try:
        return json.loads(out.stdout or "{}"), ""
    except json.JSONDecodeError:
        return {}, "ffprobe: bad JSON"


def probe(path: str, ffprobe: str = "ffprobe") -> MediaInfo:
    info = MediaInfo(path=path)
    data, err = _ffprobe_json(ffprobe, ["-show_format", "-show_streams", path])
    if err:
        info.error = err
        return info
    info.raw = data
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    if v is None:
        info.error = "no video stream"
        return info

    info.width = int(v.get("width", 0) or 0)
    info.height = int(v.get("height", 0) or 0)
    info.fps = _parse_fps(v.get("avg_frame_rate") or v.get("r_frame_rate") or "0")
    if info.fps <= 0:
        info.fps = _parse_fps(v.get("r_frame_rate") or "0")
    info.vcodec = v.get("codec_name", "")
    info.pix_fmt = v.get("pix_fmt", "")
    info.color_trc = v.get("color_transfer", "") or ""
    info.color_primaries = v.get("color_primaries", "") or ""
    info.color_space = v.get("color_space", "") or ""
    try:
        info.vbitrate = int(v.get("bit_rate", 0) or 0)
    except ValueError:
        pass
    try:
        info.duration = float(fmt.get("duration", 0) or v.get("duration", 0) or 0)
    except ValueError:
        pass
    try:
        info.bitrate = int(fmt.get("bit_rate", 0) or 0)
    except ValueError:
        pass
    if a is not None:
        info.has_audio = True
        info.acodec = a.get("codec_name", "")

    # Stream-level side data (mastering display / HDR10+ in some builds)
    side = list(v.get("side_data_list", []))
    # First video frame: HDR10+ dynamic metadata lives in frame side data
    frames, _ = _ffprobe_json(ffprobe, ["-select_streams", "v:0", "-show_frames",
                                        "-read_intervals", "%+#1", path])
    for fr in frames.get("frames", []):
        side += fr.get("side_data_list", [])
    for sd in side:
        t = (sd.get("side_data_type") or "").lower()
        if "hdr dynamic" in t or "hdr10+" in t or "2094" in t:
            info.has_hdr10plus = True
        if "mastering display" in t or "content light" in t:
            info.is_hdr = True

    trc = info.color_trc.lower()
    if trc in PQ_TRC:
        info.is_hdr, info.hdr_kind = True, "PQ"
    elif trc in HLG_TRC:
        info.is_hdr, info.hdr_kind = True, "HLG"
    elif info.color_primaries.lower() == "bt2020" and (not trc or trc == "unknown"):
        # Samsung sometimes leaves transfer unset but the content is PQ
        info.is_hdr, info.hdr_kind = True, "PQ"
    elif info.is_hdr and not info.hdr_kind:
        info.hdr_kind = "PQ"
    return info


def collect_files(paths: list[str], recursive: bool = False) -> list[str]:
    """Expand a mix of files and directories into a sorted list of video files."""
    result: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            if recursive:
                for root, _dirs, files in os.walk(p):
                    for f in files:
                        fp = os.path.join(root, f)
                        if is_video_file(fp):
                            result.append(fp)
            else:
                for f in os.listdir(p):
                    fp = os.path.join(p, f)
                    if os.path.isfile(fp) and is_video_file(fp):
                        result.append(fp)
        elif os.path.isfile(p) and is_video_file(p):
            result.append(p)
    seen = set()
    uniq = []
    for f in sorted(result):
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq
