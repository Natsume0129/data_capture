r"""Compare DirectShow capture and in-memory encoding on the laptop.

Run from the project root with:
    .venv313\Scripts\python.exe tools\diagnose_camera_fps.py

Close the capture application, Zoom, and other camera users first. This script
does not change application settings or write image/video files. It writes one
JSON report under the project's tmp directory. Each profile has a 20-second
process timeout, including imports, camera opening, and cleanup.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from fractions import Fraction
import io
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import time


REQUESTED_FPS = 30
MEASUREMENT_SECONDS = 3.0
WARMUP_FRAMES = 3
WARMUP_TIMEOUT_SECONDS = 5.0
PROFILE_TIMEOUT_SECONDS = 20.0
PROFILES = (
    {"id": "1080_default_capture", "width": 1920, "height": 1080,
     "fourcc": None, "encode": False},
    {"id": "1080_default_encode", "width": 1920, "height": 1080,
     "fourcc": None, "encode": True},
    {"id": "1080_mjpg_capture", "width": 1920, "height": 1080,
     "fourcc": "MJPG", "encode": False},
    {"id": "1080_mjpg_encode", "width": 1920, "height": 1080,
     "fourcc": "MJPG", "encode": True},
    {"id": "720_mjpg_capture", "width": 1280, "height": 720,
     "fourcc": "MJPG", "encode": False},
)


def finite_number(value):
    """Make driver properties safe for strict JSON, including unknown values."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def fourcc_description(value):
    number = finite_number(value)
    if number is None:
        return {"value": None, "decoded": None}
    integer = int(number) & 0xFFFFFFFF
    decoded = integer.to_bytes(4, "little").decode("ascii", errors="replace")
    return {"value": integer, "decoded": decoded.rstrip("\x00")}


def profile_result(profile, camera_index):
    return {
        "profile": profile["id"],
        "camera_index": camera_index,
        "requested": {
            "backend": "CAP_DSHOW",
            "width": profile["width"],
            "height": profile["height"],
            "fps": REQUESTED_FPS,
            "fourcc": profile["fourcc"],
            "encode_to_ram": profile["encode"],
        },
        "status": "error",
    }


def run_worker(profile, camera_index):
    """One camera lifetime per process; never create a video file on disk."""
    result = profile_result(profile, camera_index)
    cap = None
    container = None
    stream = None
    ram_buffer = None
    started = None
    read_total = 0.0
    successful_read_total = 0.0
    conversion_total = 0.0
    encode_mux_total = 0.0
    successful_reads = 0
    failed_reads = 0
    encoded_frames = 0
    converted_frames = 0
    measured_elapsed = 0.0

    try:
        import cv2

        if profile["encode"]:
            try:
                import av
            except ImportError as error:
                result["status"] = "unavailable"
                result["error"] = (
                    "PyAV (av) is unavailable in this Python environment: "
                    f"{error}. Capture-only profiles can still run."
                )
                return result

        cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            result["error"] = "DirectShow could not open the selected camera."
            return result

        accepted = {}
        if profile["fourcc"] is not None:
            accepted["fourcc"] = bool(cap.set(
                cv2.CAP_PROP_FOURCC,
                cv2.VideoWriter_fourcc(*profile["fourcc"]),
            ))
        else:
            accepted["fourcc"] = None  # Default format: no FOURCC set call.
        accepted["width"] = bool(cap.set(cv2.CAP_PROP_FRAME_WIDTH, profile["width"]))
        accepted["height"] = bool(cap.set(cv2.CAP_PROP_FRAME_HEIGHT, profile["height"]))
        accepted["fps"] = bool(cap.set(cv2.CAP_PROP_FPS, REQUESTED_FPS))
        result["property_set_return_values"] = accepted

        warmup_start = time.perf_counter()
        warmup_successful = 0
        warmup_failed = 0
        first_dimensions = None
        while (
            warmup_successful < WARMUP_FRAMES
            and time.perf_counter() - warmup_start < WARMUP_TIMEOUT_SECONDS
        ):
            ok, frame = cap.read()
            if not ok or frame is None or frame.size == 0:
                warmup_failed += 1
                continue
            warmup_successful += 1
            if first_dimensions is None:
                first_dimensions = {"width": int(frame.shape[1]),
                                    "height": int(frame.shape[0])}

        result["warmup"] = {
            "successful_reads": warmup_successful,
            "failed_reads": warmup_failed,
            "elapsed_seconds": time.perf_counter() - warmup_start,
        }
        result["first_frame_dimensions"] = first_dimensions
        result["driver_properties"] = {
            "width": finite_number(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": finite_number(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps": finite_number(cap.get(cv2.CAP_PROP_FPS)),
            "fourcc": fourcc_description(cap.get(cv2.CAP_PROP_FOURCC)),
            # These are read-only observations, not exposure setting changes.
            "exposure": finite_number(cap.get(cv2.CAP_PROP_EXPOSURE)),
            "auto_exposure": finite_number(cap.get(cv2.CAP_PROP_AUTO_EXPOSURE)),
        }
        if warmup_successful < WARMUP_FRAMES:
            result["error"] = "Could not obtain three successful warmup frames."
            return result

        if profile["encode"]:
            ram_buffer = io.BytesIO()
            container = av.open(ram_buffer, mode="w", format="mp4")
            stream = container.add_stream(
                "libx264", rate=REQUESTED_FPS,
                options={"preset": "ultrafast", "tune": "zerolatency"},
            )
            stream.width = first_dimensions["width"]
            stream.height = first_dimensions["height"]
            stream.pix_fmt = "yuv420p"
            stream.time_base = Fraction(1, 1000)
            stream.codec_context.time_base = Fraction(1, 1000)
            stream.codec_context.max_b_frames = 0
            result["encoder"] = {
                "codec": "libx264", "preset": "ultrafast",
                "tune": "zerolatency", "max_b_frames": 0,
                "nominal_rate": REQUESTED_FPS,
                "input_time_base": "1/1000",
                "pts_source": "elapsed milliseconds at successful read completion",
                "destination": "in-memory BytesIO MP4",
            }

        started = time.perf_counter()
        while time.perf_counter() - started < MEASUREMENT_SECONDS:
            read_start = time.perf_counter()
            ok, frame = cap.read()
            read_end = time.perf_counter()
            read_seconds = read_end - read_start
            read_total += read_seconds
            if not ok or frame is None or frame.size == 0:
                failed_reads += 1
                continue
            successful_reads += 1
            successful_read_total += read_seconds

            if profile["encode"]:
                conversion_start = time.perf_counter()
                video_frame = av.VideoFrame.from_ndarray(frame, format="bgr24")
                video_frame.pts = round((read_end - started) * 1000)
                video_frame.time_base = Fraction(1, 1000)
                conversion_total += time.perf_counter() - conversion_start
                converted_frames += 1

                encode_start = time.perf_counter()
                for packet in stream.encode(video_frame):
                    container.mux(packet)
                encode_mux_total += time.perf_counter() - encode_start
                encoded_frames += 1

        measured_elapsed = time.perf_counter() - started
        result["status"] = "ok" if successful_reads else "error"
        if not successful_reads:
            result["error"] = "No successful reads during the measurement interval."
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
    finally:
        if started is not None:
            if not measured_elapsed:
                measured_elapsed = time.perf_counter() - started
            attempts = successful_reads + failed_reads
            result["measurement"] = {
                "target_seconds": MEASUREMENT_SECONDS,
                "actual_elapsed_seconds": measured_elapsed,
                "successful_reads": successful_reads,
                "failed_reads": failed_reads,
                "effective_fps": successful_reads / measured_elapsed if measured_elapsed else None,
                "mean_read_ms": successful_read_total * 1000 / successful_reads if successful_reads else None,
                "mean_all_read_attempts_ms": read_total * 1000 / attempts if attempts else None,
                "converted_frames": converted_frames,
                "mean_conversion_ms": conversion_total * 1000 / converted_frames if converted_frames else None,
                "encoded_frames": encoded_frames,
                "mean_encode_and_ram_mux_ms": encode_mux_total * 1000 / encoded_frames if encoded_frames else None,
            }

        if container is not None:
            finalize_start = time.perf_counter()
            try:
                if stream is not None:
                    for packet in stream.encode(None):
                        container.mux(packet)
            except Exception as error:
                result["status"] = "error"
                result["finalize_error"] = f"{type(error).__name__}: {error}"
            finally:
                try:
                    container.close()
                except Exception as error:
                    result["status"] = "error"
                    result["close_error"] = f"{type(error).__name__}: {error}"
                result["encoder_finalize_ms"] = (time.perf_counter() - finalize_start) * 1000
        if ram_buffer is not None:
            try:
                result["ram_mp4_bytes"] = ram_buffer.getbuffer().nbytes
            except ValueError:
                result["ram_mp4_bytes"] = None
            finally:
                ram_buffer.close()
        if cap is not None:
            cap.release()
    return result


def run_profile(profile, camera_index, camera_name):
    result = profile_result(profile, camera_index)
    wall_start = time.perf_counter()
    try:
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--worker",
             "--case", profile["id"], "--index", str(camera_index)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=PROFILE_TIMEOUT_SECONDS,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if completed.returncode != 0:
            result["error"] = f"Worker exited with code {completed.returncode}."
        else:
            try:
                result = json.loads(completed.stdout)
            except json.JSONDecodeError:
                result["error"] = "Worker did not return a valid JSON diagnostic result."
        if completed.stderr.strip():
            result["worker_stderr"] = completed.stderr.strip()[-2000:]
    except subprocess.TimeoutExpired:
        # subprocess.run kills and waits for the worker before raising.
        result["status"] = "timeout"
        result["error"] = (
            f"Worker exceeded {PROFILE_TIMEOUT_SECONDS:g} seconds and was terminated. "
            "This profile did not return complete measurements."
        )
    except OSError as error:
        result["error"] = f"Could not start worker: {error}"
    result["camera_name"] = camera_name
    result["worker_wall_seconds"] = time.perf_counter() - wall_start
    return result


def number_text(value, suffix=""):
    return "unknown" if value is None else f"{value:.2f}{suffix}"


def print_profile(result):
    print(f"\n{result['profile']}: {result['status']}")
    if "property_set_return_values" in result:
        print(f"  Property set return values: {result['property_set_return_values']}")
    dimensions = result.get("first_frame_dimensions")
    props = result.get("driver_properties", {})
    if dimensions:
        print(f"  First frame: {dimensions['width']}x{dimensions['height']}; "
              f"driver FPS: {number_text(props.get('fps'))}; "
              f"FOURCC: {props.get('fourcc', {}).get('decoded')!r}")
        print(f"  Exposure: {props.get('exposure')}; "
              f"auto exposure: {props.get('auto_exposure')} (read-only)")
    measurement = result.get("measurement")
    if measurement:
        print(f"  Successful reads: {measurement['successful_reads']}; "
              f"failed: {measurement['failed_reads']}; "
              f"elapsed: {measurement['actual_elapsed_seconds']:.3f}s; "
              f"effective FPS: {number_text(measurement['effective_fps'])}")
        print(f"  Mean read: {number_text(measurement['mean_read_ms'], ' ms')}; "
              f"conversion: {number_text(measurement['mean_conversion_ms'], ' ms')}; "
              f"encode + RAM mux: {number_text(measurement['mean_encode_and_ram_mux_ms'], ' ms')}")
    for key in ("error", "finalize_error", "close_error"):
        if key in result:
            print(f"  {key}: {result[key]}")


def interactive_main():
    if os.name != "nt":
        print("This diagnostic uses Windows DirectShow and must run on the laptop with Windows.")
        return 2
    try:
        import cv2
        from cv2_enumerate_cameras import enumerate_cameras
    except ImportError as error:
        print(f"Required camera dependency is unavailable: {error}")
        print("Run this script with the project's .venv313 Python environment.")
        return 2
    try:
        cameras = list(enumerate_cameras(cv2.CAP_DSHOW))
    except Exception as error:
        print(f"Could not enumerate DirectShow cameras: {type(error).__name__}: {error}")
        return 2
    if not cameras:
        print("No DirectShow cameras were found.")
        return 2

    print("DirectShow cameras:")
    choices = {}
    for camera in cameras:
        choices[int(camera.index)] = str(camera.name)
        print(f"  Index {camera.index}: {camera.name}")
    while True:
        selected = input("Which camera index should be tested? ").strip()
        try:
            camera_index = int(selected)
        except ValueError:
            print("Enter an index from the list above.")
            continue
        if camera_index in choices:
            break
        print("That index was not in the DirectShow list.")

    camera_name = choices[camera_index]
    print(f"\nSelected: {camera_name} (index {camera_index})")
    print("Close the capture application, Zoom, and every other application using this camera.")
    while input("Press Enter to confirm they are closed and begin the five profiles: ") != "":
        print("Press Enter without typing after closing the camera applications.")
    print("Each profile warms up with 3 successful frames, then measures about 3 seconds.")
    print("Frames/video remain in RAM. Only a JSON report will be saved.")

    results = []
    for position, profile in enumerate(PROFILES, start=1):
        print(f"\nRunning {position}/{len(PROFILES)}: {profile['id']} ...", flush=True)
        result = run_profile(profile, camera_index, camera_name)
        results.append(result)
        print_profile(result)

    report = {
        "created_at": datetime.now().astimezone().isoformat(),
        "environment": {
            "python_version": sys.version,
            "python_executable": sys.executable,
            "platform": platform.platform(),
            "processor": platform.processor(),
            "logical_cpu_cores": os.cpu_count(),
            "opencv_version": cv2.__version__,
        },
        "camera": {"index": camera_index, "name": camera_name},
        "notes": [
            "Default profiles do not request a FOURCC; all profiles request 30 FPS.",
            "Driver FPS/property set return values are not measured FPS or proof of negotiation.",
            "Effective FPS counts only successful reads with nonempty frames after warmup.",
            "No dummy frames are generated and no images or videos are saved.",
            "Pixel uniqueness is not tested; successful reads alone cannot prove unique exposures.",
            "Read mean includes successful reads; all-attempts mean is reported separately.",
            "Conversion mean covers BGR24 VideoFrame creation and timestamp assignment.",
            "Encoding mean includes encoder color conversion, encoding, and MP4 muxing into RAM.",
            "Elapsed measurement includes the final read/encode even if it crosses 3 seconds.",
            "Encoder finalization is reported separately and excluded from effective FPS.",
            "Exposure and auto-exposure values are driver-reported and were not changed.",
            "Profiles run sequentially in separate workers; no cause is inferred automatically.",
        ],
        "profiles": results,
    }
    report_dir = Path(__file__).resolve().parent.parent / "tmp"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"camera_fps_diagnostic_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
    with report_path.open("x", encoding="utf-8") as report_file:
        json.dump(report, report_file, ensure_ascii=False, indent=2, allow_nan=False)
        report_file.write("\n")
    print(f"\nJSON report: {report_path.resolve()}")
    print("Driver-reported FPS and measured FPS are separate observations; no cause is inferred.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--case", choices=[profile["id"] for profile in PROFILES],
                        help=argparse.SUPPRESS)
    parser.add_argument("--index", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if args.case is None or args.index is None:
            parser.error("Worker mode requires --case and --index.")
        profile = next(profile for profile in PROFILES if profile["id"] == args.case)
        print(json.dumps(run_worker(profile, args.index), ensure_ascii=True, allow_nan=False))
        return 0
    if args.case is not None or args.index is not None:
        parser.error("Camera selection and confirmation are interactive; run without worker options.")
    try:
        return interactive_main()
    except (KeyboardInterrupt, EOFError):
        print("\nDiagnostic cancelled.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
