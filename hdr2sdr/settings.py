"""User settings, presets and persistence (JSON in ~/.config/hdr2sdr)."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "hdr2sdr")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")
PRESETS_FILE = os.path.join(CONFIG_DIR, "presets.json")

TONEMAP_ALGOS = ["auto", "bt.2390", "bt.2446a", "spline", "hable", "mobius", "reinhard", "gamma", "linear", "clip"]
ENGINES = ["auto", "libplacebo", "zscale"]
CODECS = ["h264", "hevc8", "hevc10", "prores"]
CONTAINERS = ["mp4", "mov", "mkv"]
QUALITY_MODES = ["original", "lower", "higher", "crf", "bitrate"]
ENC_PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
HWACCEL = ["auto", "vaapi", "off"]
RESOLUTIONS = ["keep", "2160", "1440", "1080", "720"]
AUDIO_MODES = ["copy", "aac"]
NON_HDR = ["skip", "convert"]
EXISTS = ["rename", "skip", "overwrite"]


@dataclass
class ColorSettings:
    saturation: float = 1.0   # 0.0 .. 3.0 (eq filter)
    contrast: float = 1.0     # -2 .. 2, 1.0 = neutral
    brightness: float = 0.0   # -1 .. 1, 0 = neutral
    gamma: float = 1.0        # 0.1 .. 10, 1 = neutral
    tonemap: str = "auto"
    engine: str = "auto"
    source_peak: float = 0.0  # nits; 0 = auto / metadata
    desat: float = 0.0        # tonemap filter desaturation strength (0 keeps colors)

    def is_neutral_eq(self) -> bool:
        return (abs(self.saturation - 1) < 1e-6 and abs(self.contrast - 1) < 1e-6
                and abs(self.brightness) < 1e-6 and abs(self.gamma - 1) < 1e-6)


COLOR_PRESETS: dict[str, ColorSettings] = {
    "natural": ColorSettings(),
    "vivid": ColorSettings(saturation=1.25, contrast=1.06),
    "phone": ColorSettings(saturation=1.15, contrast=1.04, gamma=0.97),
}


@dataclass
class Settings:
    language: str = "en"
    ffmpeg_path: str = ""          # empty = auto
    # input/output
    output_dir: str = ""           # empty = next to the source file
    suffix: str = "_SDR"
    container: str = "mp4"
    recursive: bool = False
    non_hdr: str = "skip"          # skip | convert
    exists: str = "rename"         # rename | skip | overwrite
    last_input_dir: str = ""
    # color
    color: ColorSettings = field(default_factory=ColorSettings)
    color_preset: str = "natural"
    # quality
    quality_mode: str = "original" # original | lower | higher | crf | bitrate
    crf: int = 18
    bitrate_kbps: int = 40000
    codec: str = "h264"            # h264 | hevc8 | hevc10 | prores
    enc_preset: str = "medium"
    hwaccel: str = "auto"          # auto | vaapi | off
    resolution: str = "keep"
    fps: float = 0.0               # 0 = keep
    audio: str = "copy"            # copy | aac
    audio_kbps: int = 256
    # misc
    parallel: int = 1
    preview_time: float = 2.0
    keep_metadata: bool = True
    show_log: bool = False

    def copy(self) -> "Settings":
        return Settings.from_dict(self.to_dict())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        s = cls()
        for f in fields(cls):
            if f.name not in d:
                continue
            val = d[f.name]
            if f.name == "color" and isinstance(val, dict):
                cs = ColorSettings()
                for cf in fields(ColorSettings):
                    if cf.name in val:
                        setattr(cs, cf.name, val[cf.name])
                val = cs
            setattr(s, f.name, val)
        return s


def load() -> Settings:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            return Settings.from_dict(json.load(fh))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return Settings()


def save(s: Settings) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(s.to_dict(), fh, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_FILE)


def load_user_presets() -> dict[str, ColorSettings]:
    try:
        with open(PRESETS_FILE, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for name, d in raw.items():
        cs = ColorSettings()
        for cf in fields(ColorSettings):
            if cf.name in d:
                setattr(cs, cf.name, d[cf.name])
        out[name] = cs
    return out


def save_user_presets(presets: dict[str, ColorSettings]) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(PRESETS_FILE, "w", encoding="utf-8") as fh:
        json.dump({k: asdict(v) for k, v in presets.items()}, fh, indent=2, ensure_ascii=False)
