"""Command-line interface: hdr2sdr-cli INPUT... [-o DIR] [options]."""
from __future__ import annotations

import argparse
import sys
import time

from . import __version__, caps as capsmod, pipeline, settings as cfg
from .probe import collect_files, probe
from .settings import (CODECS, COLOR_PRESETS, CONTAINERS, ENC_PRESETS, ENGINES, HWACCEL, QUALITY_MODES,
                       RESOLUTIONS, TONEMAP_ALGOS)
from .worker import Job, Runner


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hdr2sdr-cli",
                                description="Convert HDR10+/HLG videos to vivid SDR (ffmpeg based).")
    p.add_argument("inputs", nargs="*", help="video files and/or folders")
    p.add_argument("-o", "--output-dir", default=None, help="output folder (default: next to source)")
    p.add_argument("-r", "--recursive", action="store_true")
    p.add_argument("--suffix", default=None)
    p.add_argument("--container", choices=CONTAINERS)
    p.add_argument("--preset", choices=list(COLOR_PRESETS), help="color preset")
    p.add_argument("--saturation", type=float)
    p.add_argument("--contrast", type=float)
    p.add_argument("--brightness", type=float)
    p.add_argument("--gamma", type=float)
    p.add_argument("--tonemap", choices=TONEMAP_ALGOS)
    p.add_argument("--engine", choices=ENGINES)
    p.add_argument("--peak", type=float, help="source peak nits (0 = auto)")
    p.add_argument("-q", "--quality", choices=QUALITY_MODES)
    p.add_argument("--crf", type=int)
    p.add_argument("--bitrate", type=int, help="kbps")
    p.add_argument("--codec", choices=CODECS)
    p.add_argument("--speed", choices=ENC_PRESETS, help="encoder preset")
    p.add_argument("--hw", choices=HWACCEL)
    p.add_argument("--resolution", choices=RESOLUTIONS)
    p.add_argument("--fps", type=float)
    p.add_argument("--aac", action="store_true", help="re-encode audio to AAC")
    p.add_argument("--convert-sdr", action="store_true", help="also re-encode non-HDR files")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("-j", "--parallel", type=int)
    p.add_argument("--ffmpeg", default=None, help="path to ffmpeg binary or its directory")
    p.add_argument("--dry-run", action="store_true", help="print commands and exit")
    p.add_argument("--caps", action="store_true", help="show ffmpeg capabilities and exit")
    p.add_argument("--version", action="version", version=f"hdr2sdr {__version__}")
    return p


def main() -> int:
    args = build_parser().parse_args()
    s = cfg.load()
    if args.ffmpeg:
        s.ffmpeg_path = args.ffmpeg
    caps = capsmod.detect(s.ffmpeg_path)
    if args.caps:
        print(caps.summary().replace(" | ", "\n"))
        for n in caps.notes:
            print("  ", n)
        return 0
    if not caps.found:
        print("ffmpeg not found (sudo apt install ffmpeg)", file=sys.stderr)
        return 2

    if args.preset:
        s.color = cfg.ColorSettings(**vars(COLOR_PRESETS[args.preset]))
    for name in ("saturation", "contrast", "brightness", "gamma", "tonemap", "engine"):
        v = getattr(args, name)
        if v is not None:
            setattr(s.color, name, v)
    if args.peak is not None:
        s.color.source_peak = args.peak
    if args.output_dir is not None:
        s.output_dir = args.output_dir
    if args.suffix is not None:
        s.suffix = args.suffix
    if args.container:
        s.container = args.container
    if args.quality:
        s.quality_mode = args.quality
    if args.crf is not None:
        s.crf, s.quality_mode = args.crf, "crf"
    if args.bitrate is not None:
        s.bitrate_kbps, s.quality_mode = args.bitrate, "bitrate"
    if args.codec:
        s.codec = args.codec
    if args.speed:
        s.enc_preset = args.speed
    if args.hw:
        s.hwaccel = args.hw
    if args.resolution:
        s.resolution = args.resolution
    if args.fps is not None:
        s.fps = args.fps
    if args.aac:
        s.audio = "aac"
    if args.convert_sdr:
        s.non_hdr = "convert"
    if args.overwrite:
        s.exists = "overwrite"
    if args.parallel:
        s.parallel = args.parallel
    s.recursive = args.recursive

    if not args.inputs:
        print("no inputs given (see --help)", file=sys.stderr)
        return 1
    files = collect_files(args.inputs, s.recursive)
    if not files:
        print("no video files found", file=sys.stderr)
        return 1

    jobs: list[Job] = []
    for i, f in enumerate(files):
        info = probe(f, caps.ffprobe)
        job = Job(index=i, info=info, plan=None)
        if info.error and not info.width:
            job.status, job.message = "error", info.error
        elif not info.is_hdr and s.non_hdr == "skip":
            job.status, job.message = "skipped", "not HDR"
        elif info.is_hdr and not caps.can_tonemap:
            job.status, job.message = "error", "ffmpeg lacks tone-mapping filters"
        else:
            codec, _ = pipeline.effective_codec(s, caps)
            out = pipeline.resolve_collision(pipeline.output_path(info, s, codec), s.exists)
            if out is None:
                job.status, job.message = "skipped", "output exists"
            else:
                job.plan = pipeline.plan(info, s, caps, out)
        jobs.append(job)
        tag = "HDR" if info.is_hdr else "SDR"
        print(f"[{i+1}/{len(files)}] {f}  ({tag}, {info.width}x{info.height})  -> "
              f"{job.plan.output if job.plan else job.status + ': ' + job.message}")
        if job.plan and args.dry_run:
            print("   ", job.plan.as_shell())

    if args.dry_run:
        return 0
    runnable = [j for j in jobs if j.plan]
    if not runnable:
        return 0

    done = {"flag": False}
    last = {}

    def on_progress(j: Job):
        key = j.index
        line = f"  [{key+1}] {j.progress:5.1f}%  {j.speed} ETA {j.eta}"
        if last.get(key) != line:
            last[key] = line
            print(line, end="\r" if s.parallel == 1 else "\n", flush=True)

    def on_done(j: Job):
        print(f"\n[{j.index+1}] {j.status}: {j.message}", flush=True)

    def on_all():
        done["flag"] = True

    runner = Runner(on_progress, on_done, on_all)
    runner.start(runnable, s.parallel)
    try:
        while not done["flag"]:
            time.sleep(0.2)
    except KeyboardInterrupt:
        runner.cancel()
        while not done["flag"]:
            time.sleep(0.2)
        print("\ncancelled")
        return 130
    errors = sum(1 for j in jobs if j.status == "error")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
