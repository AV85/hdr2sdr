"""Builds ffmpeg commands: HDR -> SDR tone mapping, color tweaks, encoding."""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass

from .caps import Caps
from .probe import MediaInfo
from .settings import Settings

# tonemap filter (zscale path) supports fewer algorithms than libplacebo
ZSCALE_ALGOS = {"hable", "mobius", "reinhard", "gamma", "linear", "clip"}
ZSCALE_FALLBACK = {"bt.2390": "hable", "bt.2446a": "hable", "spline": "mobius"}

# CRF tables: (original, lower, higher)
CRF_TABLE = {
    "h264": (17, 23, 14),
    "hevc8": (20, 26, 16),
    "hevc10": (20, 26, 16),
}
# VAAPI constant-QP roughly comparable to CRF
QP_TABLE = {
    "h264": (19, 25, 15),
    "hevc8": (22, 28, 18),
    "hevc10": (22, 28, 18),
}
PRORES_PROFILE = {"original": 3, "lower": 1, "higher": 3, "crf": 3, "bitrate": 3}

PIX_FMT = {"h264": "yuv420p", "hevc8": "yuv420p", "hevc10": "yuv420p10le", "prores": "yuv422p10le"}
VAAPI_FMT = {"h264": "nv12", "hevc8": "nv12", "hevc10": "p010"}


@dataclass
class Plan:
    """Result of planning one conversion: the command and human-readable decisions."""
    cmd: list[str]
    engine: str          # "libplacebo" | "zscale" | "none"
    tonemap: str
    encoder: str
    pix_fmt: str
    hw_encode: bool
    quality_desc: str
    output: str

    def as_shell(self) -> str:
        return " ".join(shlex.quote(a) for a in self.cmd)


def choose_engine(s: Settings, caps: Caps) -> str:
    e = s.color.engine
    if e == "libplacebo" and caps.has_libplacebo and caps.vulkan_ok:
        return "libplacebo"
    if e == "zscale" and caps.has_zscale and caps.has_tonemap:
        return "zscale"
    # auto
    if caps.has_libplacebo and caps.vulkan_ok:
        return "libplacebo"
    if caps.has_zscale and caps.has_tonemap:
        return "zscale"
    return "none"


def choose_tonemap(s: Settings, engine: str) -> str:
    algo = s.color.tonemap
    if algo == "auto":
        return "bt.2390" if engine == "libplacebo" else "hable"
    if engine == "zscale" and algo not in ZSCALE_ALGOS:
        return ZSCALE_FALLBACK.get(algo, "hable")
    return algo


def effective_codec(s: Settings, caps: Caps) -> tuple[str, bool]:
    """Return (codec_key, use_vaapi)."""
    codec = s.codec
    use_vaapi = False
    if codec in ("h264", "hevc8", "hevc10") and s.hwaccel in ("auto", "vaapi"):
        want = s.hwaccel == "vaapi"
        ok = caps.vaapi_ok and (caps.vaapi_h264 if codec == "h264" else caps.vaapi_hevc)
        # "auto" prefers software x264/x265 for quality; VAAPI only when forced
        use_vaapi = ok and want
    if codec == "prores" and not caps.has_prores:
        codec = "h264"
    if codec == "h264" and not caps.has_x264 and not use_vaapi:
        codec = "hevc8" if caps.has_x265 else codec
    return codec, use_vaapi


def output_path(info: MediaInfo, s: Settings, codec: str) -> str:
    base = os.path.splitext(os.path.basename(info.path))[0]
    container = s.container
    if codec == "prores" and container == "mp4":
        container = "mov"
    if codec in ("hevc8", "hevc10") and container == "mp4":
        pass  # hvc1 tag makes it fine
    out_dir = s.output_dir or os.path.dirname(info.path)
    return os.path.join(out_dir, f"{base}{s.suffix}.{container}")


def resolve_collision(path: str, mode: str) -> str | None:
    """Return final path, or None when the job must be skipped."""
    if not os.path.exists(path):
        return path
    if mode == "overwrite":
        return path
    if mode == "skip":
        return None
    root, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(f"{root}_{n}{ext}"):
        n += 1
    return f"{root}_{n}{ext}"


def _eq_filter(s: Settings) -> str:
    c = s.color
    if c.is_neutral_eq():
        return ""
    return (f"eq=saturation={c.saturation:.3f}:contrast={c.contrast:.3f}"
            f":brightness={c.brightness:.3f}:gamma={c.gamma:.3f}")


def build_filters(info: MediaInfo, s: Settings, caps: Caps, engine: str, tonemap: str,
                  codec: str, use_vaapi: bool, preview: bool = False,
                  preview_raw: bool = False, preview_width: int = 0) -> tuple[str, str]:
    """Return (filter_chain, final_pix_fmt)."""
    chain: list[str] = []
    do_tonemap = info.is_hdr and engine != "none" and not preview_raw
    pix = "rgb24" if preview else PIX_FMT.get(codec, "yuv420p")

    if do_tonemap:
        if engine == "libplacebo":
            f = (f"libplacebo=tonemapping={tonemap}:colorspace=bt709:color_primaries=bt709"
                 f":color_trc=bt709:range=tv:format={pix}")
            if s.color.source_peak > 0:
                f += ":peak_detect=false"
            else:
                f += ":peak_detect=true"
            chain.append(f)
        else:
            chain.append("zscale=t=linear:npl=100")
            chain.append("format=gbrpf32le")
            chain.append("zscale=p=bt709")
            tm = f"tonemap=tonemap={tonemap}:desat={s.color.desat:.2f}"
            if s.color.source_peak > 0:
                tm += f":peak={s.color.source_peak:.0f}"
            chain.append(tm)
            chain.append("zscale=t=bt709:m=bt709:r=tv")
            chain.append("sidedata=mode=delete")
            chain.append(f"format={pix}")
    elif preview_raw:
        # what a non-HDR-aware editor shows: raw values interpreted as SDR
        chain.append("format=rgb24")
    else:
        chain.append(f"format={pix}")

    eq = _eq_filter(s)
    if eq and not preview_raw:
        chain.append(eq)

    if preview:
        if preview_width:
            chain.append(f"scale={preview_width}:-2:flags=bicubic")
        return ",".join(chain), "rgb24"

    if s.resolution != "keep":
        try:
            h = int(s.resolution)
            if info.height and h < info.height:
                chain.append(f"scale=-2:{h}:flags=lanczos")
                chain.append(f"format={pix}")
        except ValueError:
            pass
    if s.fps and s.fps > 0:
        chain.append(f"fps={s.fps:g}")

    if use_vaapi:
        chain.append(f"format={VAAPI_FMT.get(codec, 'nv12')}")
        chain.append("hwupload")
    return ",".join(chain), pix


def _video_encoder_args(s: Settings, codec: str, use_vaapi: bool, pix: str) -> tuple[list[str], str, str]:
    """Return (args, encoder_name, quality_desc)."""
    mode = s.quality_mode
    idx = {"original": 0, "lower": 1, "higher": 2}.get(mode, 0)
    args: list[str] = []

    if codec == "prores":
        prof = PRORES_PROFILE.get(mode, 3)
        args += ["-c:v", "prores_ks", "-profile:v", str(prof), "-vendor", "apl0", "-pix_fmt", pix]
        return args, "prores_ks", f"ProRes profile {prof}"

    if use_vaapi:
        enc = "h264_vaapi" if codec == "h264" else "hevc_vaapi"
        args += ["-c:v", enc]
        if mode == "bitrate":
            kb = max(500, int(s.bitrate_kbps))
            args += ["-b:v", f"{kb}k", "-maxrate", f"{int(kb*1.3)}k", "-bufsize", f"{kb*2}k"]
            desc = f"{kb} kbps (VAAPI)"
        else:
            qp = s.crf if mode == "crf" else QP_TABLE[codec][idx]
            args += ["-rc_mode", "CQP", "-qp", str(qp)]
            desc = f"QP {qp} (VAAPI)"
        if codec == "hevc10":
            args += ["-profile:v", "main10"]
        if codec in ("hevc8", "hevc10"):
            args += ["-tag:v", "hvc1"]
        return args, enc, desc

    if codec == "h264":
        args += ["-c:v", "libx264", "-preset", s.enc_preset, "-profile:v", "high", "-pix_fmt", pix]
        enc = "libx264"
    else:
        args += ["-c:v", "libx265", "-preset", s.enc_preset, "-pix_fmt", pix,
                 "-tag:v", "hvc1", "-x265-params", "log-level=error"]
        enc = "libx265"
    if mode == "bitrate":
        kb = max(500, int(s.bitrate_kbps))
        args += ["-b:v", f"{kb}k", "-maxrate", f"{int(kb*1.3)}k", "-bufsize", f"{kb*2}k"]
        desc = f"{kb} kbps"
    else:
        crf = s.crf if mode == "crf" else CRF_TABLE[codec][idx]
        args += ["-crf", str(crf)]
        desc = f"CRF {crf}"
    return args, enc, desc


def plan(info: MediaInfo, s: Settings, caps: Caps, out_path: str | None = None) -> Plan:
    engine = choose_engine(s, caps)
    tonemap = choose_tonemap(s, engine)
    codec, use_vaapi = effective_codec(s, caps)
    out = out_path or output_path(info, s, codec)

    filters, pix = build_filters(info, s, caps, engine, tonemap, codec, use_vaapi)
    vargs, enc, qdesc = _video_encoder_args(s, codec, use_vaapi, pix)

    cmd: list[str] = [caps.ffmpeg, "-hide_banner", "-nostdin", "-y",
                      "-loglevel", "warning", "-progress", "pipe:1", "-nostats"]
    if engine == "libplacebo" and info.is_hdr:
        cmd += ["-init_hw_device", "vulkan"]
    if use_vaapi:
        cmd += ["-vaapi_device", caps.vaapi_device]
    cmd += ["-i", info.path]
    cmd += ["-map", "0:v:0", "-map", "0:a?"]
    if filters:
        cmd += ["-vf", filters]
    cmd += vargs
    if info.is_hdr and engine != "none":
        cmd += ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]
    # audio
    if s.audio == "aac" or not info.has_audio:
        if info.has_audio:
            cmd += ["-c:a", "aac", "-b:a", f"{int(s.audio_kbps)}k"]
    else:
        cmd += ["-c:a", "copy"]
    # metadata & container flags
    ext = os.path.splitext(out)[1].lower()
    if s.keep_metadata:
        cmd += ["-map_metadata", "0"]
    if ext in (".mp4", ".mov", ".m4v"):
        cmd += ["-movflags", "+faststart+use_metadata_tags"]
    cmd += [out]

    return Plan(cmd=cmd, engine=engine if info.is_hdr else "none", tonemap=tonemap,
                encoder=enc, pix_fmt=pix, hw_encode=use_vaapi, quality_desc=qdesc, output=out)


def preview_command(info: MediaInfo, s: Settings, caps: Caps, t: float, raw: bool,
                    width: int = 640) -> list[str]:
    """Command that writes one PNG frame to stdout (raw = as a naive editor shows it)."""
    engine = choose_engine(s, caps)
    tonemap = choose_tonemap(s, engine)
    filters, _ = build_filters(info, s, caps, engine, tonemap, "h264", False,
                               preview=True, preview_raw=raw, preview_width=width)
    t = max(0.0, min(t, max(0.0, info.duration - 0.1)))
    cmd = [caps.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error"]
    if engine == "libplacebo" and info.is_hdr and not raw:
        cmd += ["-init_hw_device", "vulkan"]
    cmd += ["-ss", f"{t:.3f}", "-i", info.path, "-map", "0:v:0", "-frames:v", "1",
            "-vf", filters, "-f", "image2pipe", "-c:v", "png", "-"]
    return cmd
