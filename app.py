from __future__ import annotations

import sys
import json
import os
import shutil
import traceback
from pathlib import Path

from capture_app.ui import create_application


def run_self_check(project_root: Path, output_path: Path) -> int:
    def write_report(payload: dict[str, object]) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    report: dict[str, object] = {
        "status": "running",
        "stage": "starting",
        "project_root": str(project_root),
    }
    write_report(report)
    from google.cloud import texttospeech

    from capture_app.camera import CameraController
    from capture_app.question_sets import load_question_set
    from capture_app.splitter import ensure_ffmpeg

    report["stage"] = "creating_ui"
    write_report(report)
    app, window = create_application(project_root)
    report["stage"] = "loading_question_sets"
    write_report(report)
    question_files = sorted((project_root / "question_set").glob("*.csv"))
    question_sets = [load_question_set(path) for path in question_files]
    report["stage"] = "checking_ffmpeg"
    write_report(report)
    bundled_ffmpeg = project_root / "ffmpeg.exe"
    ffmpeg = ensure_ffmpeg(
        bundled_ffmpeg
        if bundled_ffmpeg.is_file()
        else (shutil.which("ffmpeg") or "ffmpeg")
    )
    report = {
        "python": sys.version,
        "project_root": str(project_root),
        "question_set_files": len(question_sets),
        "scenes": sum(len(item.scenes) for item in question_sets),
        "segments": sum(item.segment_count for item in question_sets),
        "camera_devices": len(CameraController.devices()),
        "ffmpeg": str(ffmpeg),
        "google_tts_client": texttospeech.TextToSpeechClient.__name__,
        "ui_languages": ["zh_CN", "en", "ja"],
        "status": "ok",
    }
    write_report(report)
    window.close()
    app.processEvents()
    return 0


def main() -> int:
    project_root = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent
    )
    self_check_target = os.environ.get("SCENARIO_CAPTURE_SELF_CHECK")
    if self_check_target:
        return run_self_check(
            project_root, Path(self_check_target).resolve()
        )
    if "--self-check" in sys.argv:
        flag_index = sys.argv.index("--self-check")
        if flag_index + 1 >= len(sys.argv):
            return 2
        return run_self_check(
            project_root, Path(sys.argv[flag_index + 1]).resolve()
        )
    app, window = create_application(project_root)
    window.show()
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        target = os.environ.get("SCENARIO_CAPTURE_SELF_CHECK")
        if target:
            Path(target).write_text(
                json.dumps(
                    {
                        "status": "error",
                        "traceback": traceback.format_exc(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        raise
