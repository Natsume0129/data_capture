from __future__ import annotations

import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class SplitTask:
    clip_key: str
    raw_path: Path
    output_path: Path
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def ensure_ffmpeg(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_file():
        located = shutil.which(str(path))
        if not located:
            raise FileNotFoundError(f"FFmpeg not found: {path}")
        candidate = Path(located)
    result = subprocess.run(
        [str(candidate), "-version"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "FFmpeg check failed")
    return candidate.resolve()


def build_ffmpeg_command(ffmpeg: Path, task: SplitTask) -> list[str]:
    if task.duration_ms <= 0:
        raise ValueError("split duration must be positive")
    return [
        str(ffmpeg),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(task.raw_path),
        "-ss",
        f"{task.start_ms / 1000:.3f}",
        "-t",
        f"{task.duration_ms / 1000:.3f}",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(task.output_path),
    ]


def wait_for_stable_file(path: Path, timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    previous_size = -1
    stable_checks = 0
    while time.monotonic() < deadline:
        if path.is_file():
            size = path.stat().st_size
            if size > 0 and size == previous_size:
                stable_checks += 1
                if stable_checks >= 2:
                    return
            else:
                stable_checks = 0
            previous_size = size
        time.sleep(0.2)
    raise TimeoutError(f"raw video was not finalized: {path}")


def run_split(ffmpeg: Path, task: SplitTask) -> None:
    wait_for_stable_file(task.raw_path)
    task.output_path.parent.mkdir(parents=True, exist_ok=True)
    command = build_ffmpeg_command(ffmpeg, task)
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if result.returncode != 0:
        if task.output_path.exists():
            task.output_path.unlink()
        raise RuntimeError(result.stderr.strip() or "FFmpeg split failed")
    if not task.output_path.is_file() or task.output_path.stat().st_size == 0:
        raise RuntimeError("FFmpeg returned success without an output file")


class SplitManager:
    def __init__(
        self,
        ffmpeg: Path,
        on_result: Callable[[SplitTask, bool, str], None],
    ):
        self.ffmpeg = ffmpeg
        self.on_result = on_result
        self._queue: queue.Queue[SplitTask | None] = queue.Queue()
        self._pending = 0
        self._lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run, name="video-splitter", daemon=True
        )
        self._thread.start()

    @property
    def pending(self) -> int:
        with self._lock:
            return self._pending

    def enqueue(self, task: SplitTask) -> None:
        with self._lock:
            self._pending += 1
        self._queue.put(task)

    def wait(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.pending:
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
        return True

    def shutdown(self, wait: bool = True) -> None:
        self._queue.put(None)
        if wait:
            self._thread.join()

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            if task is None:
                self._queue.task_done()
                return
            success = True
            error = ""
            try:
                run_split(self.ffmpeg, task)
            except Exception as exc:
                success = False
                error = str(exc)
            finally:
                with self._lock:
                    self._pending -= 1
                self._queue.task_done()
            self.on_result(task, success, error)

