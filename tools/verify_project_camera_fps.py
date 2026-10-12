"""Measure the project's C920 pipeline and retain a video/JSON report in tmp.

Run with the project's Python environment. Close other camera users first.
The parent process bounds camera initialization and shutdown as well as capture.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time


def measure(seconds: float) -> None:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import av
    from PySide6.QtCore import QEventLoop, QTimer, Qt
    from PySide6.QtWidgets import QApplication, QLabel
    from capture_app import camera

    observations = []

    class ObservedCapture(camera.CaptureThread):
        def _process_frame(self, frame, captured_at=None):
            super()._process_frame(frame, captured_at)
            signature = hashlib.sha256(frame[::16, ::16].tobytes()).hexdigest()
            observations.append((captured_at, signature))

    camera.CaptureThread = ObservedCapture
    app = QApplication(["camera-fps-verification"])
    preview = QLabel()
    preview.resize(480, 270)
    controller = camera.CameraController()
    errors = []
    controller.error.connect(errors.append)
    devices = [d for d in controller.devices() if "c920" in d.name.casefold()]
    if len(devices) != 1:
        raise RuntimeError(f"Expected one C920, found {len(devices)}")
    directory = Path(__file__).resolve().parents[1] / "tmp"
    directory.mkdir(exist_ok=True)
    stem = directory / (time.strftime("c920_verified_%Y%m%d_%H%M%S") + f"_{os.getpid()}")
    video_path = stem.with_suffix(".mp4")

    def wait(duration):
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.setTimerType(Qt.TimerType.PreciseTimer)
        timer.timeout.connect(loop.quit)
        timer.start(round(duration * 1000))
        loop.exec()

    def phase(name, duration):
        start = time.monotonic()
        wait(duration)
        end = time.monotonic()
        samples = [s for s in observations if start <= s[0] <= end]
        gaps = [b[0] - a[0] for a, b in zip(samples, samples[1:])]
        result = {
            "phase": name, "seconds": end - start, "frames": len(samples),
            "fps": len(samples) / (end - start),
            "mean_interval_ms": statistics.mean(gaps) * 1000 if gaps else None,
            "max_interval_ms": max(gaps) * 1000 if gaps else None,
            "intervals_over_66_7_ms": sum(g > 2 / 30 for g in gaps),
            "identical_adjacent_sampled_images": sum(a[1] == b[1] for a, b in zip(samples, samples[1:])),
            "per_second_frames": [sum(start + i <= s[0] < start + i + 1 for s in samples) for i in range(int(duration))],
            "capture_info": controller.capture_info,
        }
        print(json.dumps(result), flush=True)
        if errors:
            raise RuntimeError(errors)
        return result

    report = {"device": devices[0].name, "video": str(video_path), "results": [],
              "notes": ["Preview uses offscreen Qt at 480x270; recording uses the actual project writer and disk.",
                        "Sampled image hashes detect identical sampled pixels, not unique sensor exposures."]}
    try:
        controller.open(devices[0], preview)
        print("Warming up C920 for 5 seconds", flush=True)
        wait(5)
        report["results"].append(phase("preview", 15))
        controller.set_preview(None)
        controller.start_recording(video_path)
        print(f"Recording to disk for {seconds:g} seconds", flush=True)
        report["results"].append(phase("recording", seconds))
        _, duration_ms = controller.stop_recording()
        report["recording_duration_ms"] = duration_ms
    finally:
        controller.close()
    with av.open(str(video_path)) as container:
        pts = [float(f.pts * f.time_base) for f in container.decode(video=0)]
        report["decoded_frames"] = len(pts)
        report["video_duration_seconds"] = container.duration / av.time_base
        report["strictly_increasing_timestamps"] = all(b > a for a, b in zip(pts, pts[1:]))
    report["errors"] = errors
    report["minimum_required_fps"] = camera.MIN_CAPTURE_FPS
    report["meets_25_fps_requirement"] = (
        report["strictly_increasing_timestamps"]
        and all(
            result["fps"] >= camera.MIN_CAPTURE_FPS
            and min(result["per_second_frames"]) >= camera.MIN_CAPTURE_FPS
            and result["capture_info"]["actual_firstframe_dims"] == {"width": 1920, "height": 1080}
            for result in report["results"]
        )
    )
    stem.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Report:", stem.with_suffix(".json"), flush=True)
    print("Decoded frames:", report["decoded_frames"], "duration:", report["video_duration_seconds"], flush=True)
    print("Meets 1080p / 25+ FPS requirement:", report["meets_25_fps_requirement"], flush=True)
    if not report["meets_25_fps_requirement"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 600:
        parser.error("--seconds must be between 1 and 600")
    if args.worker:
        measure(args.seconds)
    else:
        subprocess.run([sys.executable, "-u", __file__, "--worker", "--seconds", str(args.seconds)],
                       check=True, timeout=args.seconds + 60)
