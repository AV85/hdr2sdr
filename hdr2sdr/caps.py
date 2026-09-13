"""Detect what the local ffmpeg build and GPU can do."""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
from dataclasses import dataclass, field


@dataclass
class Caps:
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    found: bool = False
    version: str = ""
    filters: set = field(default_factory=set)
    encoders: set = field(default_factory=set)
    has_zscale: bool = False
    has_tonemap: bool = False
    has_libplacebo: bool = False
    vulkan_ok: bool = False
    vaapi_device: str = ""
    vaapi_h264: bool = False
    vaapi_hevc: bool = False
    has_x264: bool = False
    has_x265: bool = False
    has_prores: bool = False
    notes: list = field(default_factory=list)

    @property
    def can_tonemap(self) -> bool:
        return (self.has_zscale and self.has_tonemap) or (self.has_libplacebo and self.vulkan_ok)

    @property
    def vaapi_ok(self) -> bool:
        return bool(self.vaapi_device) and (self.vaapi_h264 or self.vaapi_hevc)

    def summary(self) -> str:
        parts = [f"ffmpeg: {self.version or 'not found'}"]
        parts.append("libplacebo/Vulkan: " + ("OK" if (self.has_libplacebo and self.vulkan_ok) else "no"))
        parts.append("zscale+tonemap: " + ("OK" if (self.has_zscale and self.has_tonemap) else "no"))
        parts.append("VAAPI: " + (self.vaapi_device if self.vaapi_ok else "no"))
        parts.append("x264: " + ("OK" if self.has_x264 else "no"))
        parts.append("x265: " + ("OK" if self.has_x265 else "no"))
        return " | ".join(parts)


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, "", str(e)


def _resolve(name: str, override: str = "") -> str:
    if override:
        if os.path.isdir(override):
            cand = os.path.join(override, name)
            if os.access(cand, os.X_OK):
                return cand
        elif os.access(override, os.X_OK):
            # override is a path to ffmpeg; derive ffprobe next to it
            d = os.path.dirname(override)
            cand = os.path.join(d, name)
            return cand if os.access(cand, os.X_OK) else override
    # bundled static build (installed by install.sh --static-ffmpeg)
    home = os.path.expanduser("~/.local/share/hdr2sdr/ffmpeg/bin")
    cand = os.path.join(home, name)
    if os.access(cand, os.X_OK):
        return cand
    return shutil.which(name) or name


def detect(ffmpeg_override: str = "", quick: bool = False) -> Caps:
    c = Caps()
    c.ffmpeg = _resolve("ffmpeg", ffmpeg_override)
    c.ffprobe = _resolve("ffprobe", ffmpeg_override)

    rc, out, _ = _run([c.ffmpeg, "-version"])
    if rc != 0:
        c.notes.append("ffmpeg not found")
        return c
    c.found = True
    c.version = out.splitlines()[0].replace("ffmpeg version ", "").split(" ")[0] if out else "?"

    rc, out, _ = _run([c.ffmpeg, "-hide_banner", "-filters"])
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] and parts[0][0] in ".T":
            c.filters.add(parts[1])
    c.has_zscale = "zscale" in c.filters
    c.has_tonemap = "tonemap" in c.filters
    c.has_libplacebo = "libplacebo" in c.filters

    rc, out, _ = _run([c.ffmpeg, "-hide_banner", "-encoders"])
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] and parts[0][0] in "VAS.":
            c.encoders.add(parts[1])
    c.has_x264 = "libx264" in c.encoders
    c.has_x265 = "libx265" in c.encoders
    c.has_prores = "prores_ks" in c.encoders
    c.vaapi_h264 = "h264_vaapi" in c.encoders
    c.vaapi_hevc = "hevc_vaapi" in c.encoders

    if quick:
        return c

    # Vulkan / libplacebo functional test
    if c.has_libplacebo:
        rc, _, err = _run([c.ffmpeg, "-v", "error", "-init_hw_device", "vulkan",
                           "-f", "lavfi", "-i", "color=c=gray:s=64x64:d=0.1",
                           "-vf", "libplacebo=format=yuv420p", "-frames:v", "1",
                           "-f", "null", "-"], timeout=40)
        c.vulkan_ok = rc == 0
        if not c.vulkan_ok:
            c.notes.append("libplacebo/Vulkan test failed: " + err.strip()[:200])

    # VAAPI functional test (AMD/Intel)
    devices = sorted(glob.glob("/dev/dri/renderD*"))
    env_dev = os.environ.get("HDR2SDR_VAAPI_DEVICE")
    if env_dev:
        devices = [env_dev] + devices
    for dev in devices:
        if not (c.vaapi_h264 or c.vaapi_hevc):
            break
        enc = "h264_vaapi" if c.vaapi_h264 else "hevc_vaapi"
        # Real encoders reject tiny frames (e.g. AMD: width >= 96), so probe
        # with a small but realistic size.
        rc, _, err = _run([c.ffmpeg, "-v", "error", "-vaapi_device", dev,
                           "-f", "lavfi", "-i", "color=c=gray:s=256x144:d=0.1",
                           "-vf", "format=nv12,hwupload", "-c:v", enc,
                           "-frames:v", "1", "-f", "null", "-"], timeout=40)
        if rc == 0:
            c.vaapi_device = dev
            break
        c.notes.append(f"VAAPI test on {dev} failed: {err.strip()[:200]}")
    if not c.vaapi_device:
        c.vaapi_h264 = c.vaapi_hevc = False
    return c
