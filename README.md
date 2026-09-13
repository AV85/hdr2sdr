# hdr2sdr

**Convert HDR10 / HDR10+ / HLG phone footage into vivid, correctly colored SDR video that looks right in any video editor.**

Built for Ubuntu on top of ffmpeg. Graphical interface (Qt) plus a command-line tool. UI in English, Русский, Deutsch, Español, Français.

---

## The problem it solves

Modern phones (Samsung Galaxy S23/S24, Pixel, iPhone, …) record video in HDR: 10-bit HEVC, BT.2020 color primaries and a PQ (SMPTE 2084) or HLG transfer curve. When such a file is opened in a video editor without color management — or the timeline is set to plain Rec.709 — the editor interprets HDR values as ordinary SDR. The result is the familiar washed-out, gray, low-contrast picture.

The colors are not lost; they are simply being read wrong. **hdr2sdr** performs proper *tone mapping* from HDR to SDR (BT.709), so the converted file looks the way the video looked on the phone screen and opens correctly in DaVinci Resolve, Kdenlive, Shotcut, OpenShot, Premiere, or anything else.

## Features

- **Correct HDR → SDR tone mapping** using ffmpeg:
  - `libplacebo` on the GPU via Vulkan (ITU-R BT.2390 by default, HDR10+ dynamic metadata supported) when available;
  - `zscale` + `tonemap` on the CPU (Hable) as a fallback — same result, slower.
- **Automatic HDR detection** per file (PQ/HLG transfer, BT.2020 primaries, HDR10+ metadata). Non-HDR files are skipped by default, or can be re-encoded too.
- **Quality**: *same as original* by default (visually lossless CRF), plus *lower* / *higher* presets and manual CRF or bitrate.
- **Codecs**: H.264 8-bit (maximum compatibility, default), H.265 8-bit, H.265 10-bit, Apple ProRes (for editing).
- **Hardware encoding** with VAAPI on AMD / Intel GPUs (optional; software x264/x265 is the default because it yields better quality per bit).
- **Color fine-tuning**: presets (Natural / Vivid / Like the phone screen / custom, savable), saturation, contrast, brightness, gamma, tone-mapping algorithm and engine, source peak brightness.
- **Before / after preview** of any frame before you convert a whole batch.
- **Batch processing**: single files or whole folders (optionally recursive), drag & drop, output folder or next to the source, file-name suffix, MP4 / MOV / MKV containers, collision policy (number / skip / overwrite), parallel jobs, per-file and total progress with ETA, cancel.
- Audio is copied untouched (or re-encoded to AAC); recording date, GPS and other metadata are preserved.
- Settings persist between runs. "Show ffmpeg command" reveals exactly what will be executed.

## Requirements

- Ubuntu 22.04 / 24.04 (or another Debian-based distribution). Other Linux distributions work if `ffmpeg` and Python ≥ 3.10 are present.
- `ffmpeg` with `zscale` and `tonemap` filters (the Ubuntu package has them; Ubuntu 24.04's build also includes `libplacebo`).
- For GPU tone mapping: a Vulkan-capable GPU with Mesa drivers (AMD / Intel). For hardware encoding: VAAPI (`mesa-va-drivers`).

## Installation

```bash
git clone https://github.com/AV85/hdr2sdr.git
cd hdr2sdr
./install.sh
```

`install.sh` does everything for the current user:

1. installs system packages with `sudo apt` — `ffmpeg`, Python, and the AMD/Intel GPU stack (`mesa-va-drivers`, `mesa-vulkan-drivers`, `vainfo`, `vulkan-tools`);
2. creates a virtual environment in `~/.local/share/hdr2sdr/venv` and installs the app with PySide6;
3. adds the `hdr2sdr` and `hdr2sdr-cli` commands to `~/.local/bin` and an **HDR2SDR** entry to the applications menu;
4. prints what your ffmpeg can do (`hdr2sdr-cli --caps`).

Optional: if your system ffmpeg lacks `libplacebo` (see the `--caps` output), install a static ffmpeg build that has it:

```bash
./install.sh --static-ffmpeg
```

The app finds this build automatically. Without it everything still works on the CPU path.

**Update** to the latest version:

```bash
cd hdr2sdr && git pull && ./install.sh
```

**Uninstall** (keeps your settings in `~/.config/hdr2sdr`):

```bash
./install.sh --uninstall
```

If the shell says `hdr2sdr: command not found`, `~/.local/bin` is not in your `PATH` yet — log out and back in, or run `export PATH="$HOME/.local/bin:$PATH"`.

## Usage

### Graphical interface

Run `hdr2sdr` or open **HDR2SDR** from the applications menu.

1. Add files or a folder (or drag & drop them into the window). Each file shows whether it is HDR.
2. Optionally choose an output folder; leave it empty to save next to the sources with the `_SDR` suffix.
3. Optionally press **Preview before/after** to check the colors on one frame.
4. Press **Start**.

Language: menu **Language** → English / Русский / Deutsch / Español / Français. The choice is remembered.

### Command line

```bash
hdr2sdr-cli INPUT [INPUT ...] -o OUTPUT_DIR [options]
```

Examples:

```bash
hdr2sdr-cli ~/Videos/2026-09-13 -o ~/Videos/SDR              # a whole folder with default settings
hdr2sdr-cli clip.mp4 --preset vivid --codec hevc10 -q higher  # vivid colors, H.265 10-bit, higher quality
hdr2sdr-cli clip.mp4 --hw vaapi --speed fast                  # encode on an AMD/Intel GPU
hdr2sdr-cli clip.mp4 --tonemap hable --saturation 1.2         # pick the algorithm, boost saturation
hdr2sdr-cli clip.mp4 --dry-run                                # print the ffmpeg command only
hdr2sdr-cli --caps                                            # show ffmpeg / GPU capabilities
hdr2sdr-cli --help                                            # all options
```

## How it works

1. `ffprobe` reads the stream: transfer characteristic (PQ / HLG), primaries (BT.2020), mastering-display and HDR10+ side data, resolution, frame rate, duration, audio.
2. A filter chain is built:
   - GPU: `libplacebo=tonemapping=bt.2390:colorspace=bt709:color_primaries=bt709:color_trc=bt709`
   - CPU: `zscale=t=linear:npl=100 → format=gbrpf32le → zscale=p=bt709 → tonemap=hable → zscale=t=bt709:m=bt709:r=tv`
   - then optional `eq` (saturation / contrast / brightness / gamma), scaling and frame-rate change.
3. HDR side data is stripped and the output is tagged BT.709 so players and editors treat it as SDR.
4. Video is encoded with x264 / x265 / ProRes or VAAPI; audio is copied; metadata is mapped with `-map_metadata 0`; MP4/MOV get `+faststart`.

Quality modes map to CRF: H.264 — 17 / 23 / 14, H.265 — 20 / 26 / 16 for *original / lower / higher*. Note that re-encoding cannot add information: *higher* means extra headroom for further editing, not an improvement of the source.

## Options reference

| Group | Options |
|---|---|
| Input / output | files or folders, drag & drop, include subfolders, output folder (empty = next to source), file suffix, container MP4 / MOV / MKV, non-HDR files (skip / convert), if output exists (add number / skip / overwrite) |
| Color | presets Natural / Vivid / Like the phone screen / custom (save & delete your own), tone-mapping algorithm (auto, bt.2390, bt.2446a, spline, hable, mobius, reinhard, gamma, linear, clip), engine (auto / libplacebo GPU / zscale CPU), source peak brightness (nits), saturation, contrast, brightness, gamma, before/after preview |
| Quality | same as original / lower / higher / manual CRF / manual bitrate; codec H.264, H.265 8-bit, H.265 10-bit, ProRes; encoding speed (x264/x265 presets); hardware acceleration auto / VAAPI / off; resolution keep / 2160 / 1440 / 1080 / 720; frame rate; audio copy / AAC (bitrate) |
| Advanced | parallel files, custom ffmpeg path, re-detect capabilities, keep metadata, show ffmpeg log, show ffmpeg command, preview frame time |
| Interface | 5 languages, queue with per-file status, progress, speed and ETA, cancel, persistent settings, reset to defaults |

## Troubleshooting

- `hdr2sdr-cli --caps` shows what the app sees: ffmpeg version, libplacebo/Vulkan, VAAPI device, available encoders.
- **VAAPI on AMD**: `vainfo` should list `VAEntrypointEncSlice` for H264/HEVC. Your user must be in the `video` and `render` groups: `sudo usermod -aG video,render $USER`, then log out and in.
- **Vulkan error**: the app falls back to the CPU engine automatically. The output is identical, only slower. `vulkaninfo --summary` helps diagnose driver issues.
- **Colors still look off in the editor**: make sure the editor timeline is Rec.709 and no "auto HDR" setting is applied to the converted file; compare with the built-in preview.
- Settings live in `~/.config/hdr2sdr/settings.json`, user presets in `presets.json` next to it.

## Project layout

```
hdr2sdr/            Python package
  gui.py            PySide6 interface
  cli.py            command-line interface
  pipeline.py       ffmpeg command builder (tone mapping, encoding)
  probe.py          ffprobe wrapper, HDR detection
  caps.py           ffmpeg / GPU capability detection
  worker.py         job queue, progress parsing
  settings.py       settings, presets, persistence
  i18n.py           translations (locales/*.json)
install.sh          installer / updater / uninstaller
hdr2sdr.desktop     application-menu entry
```

## License

MIT — see [LICENSE](LICENSE).

---

### Кратко по-русски

Установка: `git clone https://github.com/AV85/hdr2sdr.git && cd hdr2sdr && ./install.sh`. Запуск: `hdr2sdr` (или «HDR2SDR» в меню приложений). Язык переключается в меню **Language → Русский**. Обновление: `git pull && ./install.sh`.
