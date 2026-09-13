"""Runs ffmpeg jobs in background threads and reports progress via callbacks."""
from __future__ import annotations

import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

from .pipeline import Plan
from .probe import MediaInfo


@dataclass
class Job:
    index: int
    info: MediaInfo
    plan: Optional[Plan]
    status: str = "pending"     # pending | running | done | error | cancelled | skipped
    progress: float = 0.0       # 0..100
    speed: str = ""
    eta: str = ""
    message: str = ""
    log: list = field(default_factory=list)
    started: float = 0.0
    finished: float = 0.0


ProgressCb = Callable[[Job], None]


def _fmt_eta(seconds: float) -> str:
    if seconds < 0 or seconds != seconds:
        return ""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class Runner:
    def __init__(self, on_progress: ProgressCb, on_job_done: ProgressCb, on_all_done: Callable[[], None]):
        self.on_progress = on_progress
        self.on_job_done = on_job_done
        self.on_all_done = on_all_done
        self._cancel = threading.Event()
        self._procs: dict[int, subprocess.Popen] = {}
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, jobs: list[Job], parallel: int = 1) -> None:
        self._cancel.clear()
        self._thread = threading.Thread(target=self._run_all, args=(jobs, max(1, parallel)), daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()
        with self._lock:
            for p in list(self._procs.values()):
                try:
                    p.terminate()
                except OSError:
                    pass

    def _run_all(self, jobs: list[Job], parallel: int) -> None:
        with ThreadPoolExecutor(max_workers=parallel) as ex:
            list(ex.map(self._run_job, jobs))
        self.on_all_done()

    def _run_job(self, job: Job) -> None:
        if self._cancel.is_set():
            job.status = "cancelled"
            self.on_job_done(job)
            return
        if job.plan is None:
            job.status = "skipped"
            self.on_job_done(job)
            return
        job.status = "running"
        job.started = time.time()
        self.on_progress(job)
        out_dir = os.path.dirname(job.plan.output)
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            job.status, job.message = "error", str(e)
            self.on_job_done(job)
            return
        tmp_out = job.plan.output + ".part" + os.path.splitext(job.plan.output)[1]
        cmd = list(job.plan.cmd)
        cmd[-1] = tmp_out
        job.log.append("$ " + " ".join(cmd))
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        except OSError as e:
            job.status, job.message = "error", str(e)
            self.on_job_done(job)
            return
        with self._lock:
            self._procs[job.index] = p

        err_lines: list[str] = []

        def read_err():
            assert p.stderr is not None
            for line in p.stderr:
                line = line.rstrip()
                if line:
                    err_lines.append(line)
                    job.log.append(line)

        t = threading.Thread(target=read_err, daemon=True)
        t.start()

        dur = job.info.duration or 0.0
        last_emit = 0.0
        assert p.stdout is not None
        for line in p.stdout:
            line = line.strip()
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key == "out_time_us" or key == "out_time_ms":
                try:
                    secs = int(val) / 1_000_000.0
                except ValueError:
                    continue
                if dur > 0:
                    job.progress = max(0.0, min(99.9, secs / dur * 100.0))
                    elapsed = time.time() - job.started
                    if job.progress > 1:
                        remaining = elapsed * (100 - job.progress) / job.progress
                        job.eta = _fmt_eta(remaining)
            elif key == "speed":
                job.speed = val.strip()
            elif key == "progress":
                now = time.time()
                if now - last_emit > 0.3 or val == "end":
                    last_emit = now
                    self.on_progress(job)
        p.wait()
        t.join(timeout=2)
        with self._lock:
            self._procs.pop(job.index, None)
        job.finished = time.time()

        if self._cancel.is_set():
            job.status = "cancelled"
            try:
                os.remove(tmp_out)
            except OSError:
                pass
        elif p.returncode == 0:
            try:
                os.replace(tmp_out, job.plan.output)
                job.status, job.progress = "done", 100.0
                job.message = _fmt_eta(job.finished - job.started)
            except OSError as e:
                job.status, job.message = "error", str(e)
        else:
            job.status = "error"
            job.message = (err_lines[-1] if err_lines else f"ffmpeg exit {p.returncode}")[:300]
            try:
                os.remove(tmp_out)
            except OSError:
                pass
        self.on_job_done(job)
