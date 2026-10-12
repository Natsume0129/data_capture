"""Local-only browser video annotation. No capture-app or Qt dependency."""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

LABELS = ("neutral", "polite smile", "bitter smile")
EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".mpeg", ".mpg", ".wmv"}
STATE = ".video_annotations.json"
ROOT = Path(__file__).resolve().parent


def natural_key(name):
    return [int(x) if x.isdigit() else x.casefold() for x in re.split(r"(\d+)", name)]


def atomic_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)


class Annotator:
    def __init__(self, ffmpeg):
        self.ffmpeg = ffmpeg
        self.lock = threading.RLock()
        self.preview_lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.cache = tempfile.TemporaryDirectory(prefix="video_annotation_")
        self.folder = None
        self.records = {}
        self.files = []

    def open_folder(self, value):
        folder = Path(value).expanduser().resolve(strict=True)
        if not folder.is_dir():
            raise ValueError("请选择文件夹")
        with self.lock:
            if any(r.get("status") == "exporting" for r in self.records.values()):
                raise ValueError("后台仍在导出，请完成后再切换文件夹")
            state_path = folder / STATE
            if state_path.exists():
                data = json.loads(state_path.read_text(encoding="utf-8"))
                if data.get("app") != "video-annotator-v1":
                    raise ValueError("标注进度文件格式不受支持")
                records = data["records"]
            else:
                if (folder / "annotations.csv").exists():
                    raise ValueError("已有不属于本工具的 annotations.csv，请先改名，避免覆盖")
                records = {}
            files = sorted((p.name for p in folder.iterdir() if p.is_file()
                            and p.suffix.lower() in EXTENSIONS
                            and not p.name.lower().endswith("_trimmed.mp4")), key=natural_key)
            for name in files:
                r = records.setdefault(name, {"status": "pending"})
                if r["status"] == "exporting" or (r["status"] == "done" and not (folder / (name + "_trimmed.mp4")).exists()):
                    r.update(status="error", error="导出中断或裁剪文件缺失，请重新保存")
            atomic_json(state_path, {"app": "video-annotator-v1", "records": records})
            self.folder, self.files, self.records = folder, files, records
            self.persist()
            return self.snapshot()

    def persist(self):
        atomic_json(self.folder / STATE, {"app": "video-annotator-v1", "records": self.records})
        path = self.folder / "annotations.csv"
        temporary = path.with_suffix(".csv.tmp")
        with temporary.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["文件名", "文件目录", "类别"])
            for name in self.files:
                r = self.records[name]
                if r["status"] == "done":
                    writer.writerow([name + "_trimmed.mp4", str(self.folder), r["label"]])
        os.replace(temporary, path)

    def snapshot(self):
        with self.lock:
            return {"folder": str(self.folder or ""), "files": [dict(name=n, **self.records[n]) for n in self.files]}

    def source(self, name):
        if name not in self.files or not self.folder:
            raise ValueError("视频不在当前列表中")
        return self.folder / name

    def run_ffmpeg(self, args):
        result = subprocess.run([self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", *args],
                                capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise ValueError(result.stderr.decode("utf-8", errors="replace")[-2000:] or "视频处理失败")

    def preview(self, name):
        # A browser-compatible proxy also supports AVI/MKV and unusual camera codecs.
        with self.lock:
            source = self.source(name)
            signature = f"{source}:{source.stat().st_size}:{source.stat().st_mtime_ns}"
        import hashlib
        target = Path(self.cache.name) / (hashlib.sha256(signature.encode()).hexdigest() + ".mp4")
        with self.preview_lock:
            if not target.exists():
                partial = target.with_suffix(".part.mp4")
                try:
                    self.run_ffmpeg(["-y", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
                                     "-vf", r"scale=trunc(min(1280\,iw)/2)*2:-2", "-c:v", "libx264",
                                     "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p",
                                     "-fps_mode", "vfr", "-c:a", "aac", "-movflags", "+faststart", str(partial)])
                    os.replace(partial, target)
                finally:
                    partial.unlink(missing_ok=True)
        return target.name

    def save(self, name, start=0, end=0, label=None, discard=False):
        with self.lock:
            source = self.source(name)
            old = self.records[name]
            if old["status"] == "exporting":
                raise ValueError("这个视频正在导出，请稍后修改")
            target = self.folder / (name + "_trimmed.mp4")
            if discard:
                # Only remove an output known to have been created by this tool.
                if old.get("owned_output"):
                    target.unlink(missing_ok=True)
                self.records[name] = dict(old, status="discarded", error="")
                self.persist()
                return
            start, end = float(start), float(end)
            if label not in LABELS or not all(map(math.isfinite, (start, end))) or not 0 <= start < end:
                raise ValueError("请选择类别和有效的起止时间")
            import av
            with av.open(str(source)) as video:
                stream = video.streams.video[0]
                duration = float(stream.duration * stream.time_base) if stream.duration is not None else (video.duration or 0) / av.time_base
            if duration <= 0 or end > duration + 0.1:
                raise ValueError("裁剪终点超出视频时长，或视频没有有效时长")
            if target.exists() and not old.get("owned_output"):
                raise ValueError("同名裁剪文件已存在且不属于当前标注记录，不能覆盖")
            self.records[name] = dict(old, start=start, end=end, label=label, status="exporting", error="")
            self.persist()
            self.pool.submit(self.export, name, source, target, start, end)

    def export(self, name, source, target, start, end):
        partial = target.with_name(target.name + ".part")
        try:
            self.run_ffmpeg(["-y", "-i", str(source), "-ss", str(start), "-t", str(end - start),
                             "-map", "0:v:0", "-map", "0:a:0?", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                             "-fps_mode", "vfr", "-c:a", "aac", "-movflags", "+faststart", "-f", "mp4", str(partial)])
            import av
            with av.open(str(partial)) as container:
                if next(container.decode(video=0), None) is None:
                    raise ValueError("裁剪结果没有有效视频帧")
            with self.lock:
                os.replace(partial, target)
                self.records[name].update(status="done", owned_output=True, error="")
                self.persist()
        except Exception as exc:
            with self.lock:
                self.records[name].update(status="error", error=str(exc))
                try:
                    self.persist()
                except OSError as save_error:
                    self.records[name]["error"] += f"；保存记录失败：{save_error}（请关闭 Excel 中的 CSV）"
        finally:
            partial.unlink(missing_ok=True)


def choose_folder():
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    selected = filedialog.askdirectory(title="选择视频文件夹", parent=root)
    root.destroy()
    return selected


def handler_for(app, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_json(self, value, status=200):
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authorized(self, query):
            return secrets.compare_digest(query.get("token", [""])[0], token)

        def do_GET(self):
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            if not self.authorized(query):
                self.send_error(403)
                return
            try:
                if url.path == "/api/state":
                    self.send_json(app.snapshot())
                    return
                if url.path == "/":
                    path = ROOT / "annotation_web" / "index.html"
                    mime = "text/html; charset=utf-8"
                elif url.path.startswith("/media/"):
                    name = url.path.removeprefix("/media/")
                    if not re.fullmatch(r"[a-f0-9]{64}\.mp4", name):
                        raise ValueError("无效媒体路径")
                    path = Path(app.cache.name) / name
                    mime = "video/mp4"
                else:
                    self.send_error(404)
                    return
                size = path.stat().st_size
                start, end = 0, size - 1
                range_header = self.headers.get("Range")
                if range_header:
                    match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_header)
                    if not match or int(match[1]) >= size:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.end_headers()
                        return
                    start = int(match[1])
                    end = min(int(match[2]) if match[2] else end, end)
                    if end < start:
                        self.send_error(416)
                        return
                self.send_response(206 if range_header else 200)
                self.send_header("Content-Type", mime)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(end - start + 1))
                if range_header:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                with path.open("rb") as f:
                    f.seek(start)
                    remaining = end - start + 1
                    while remaining > 0:
                        chunk = f.read(min(256 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)

        def do_POST(self):
            url = urlsplit(self.path)
            if not self.authorized(parse_qs(url.query)):
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 <= length <= 16384:
                    raise ValueError("请求过大")
                data = json.loads(self.rfile.read(length) or b"{}")
                if url.path == "/api/pick":
                    result = subprocess.run([sys.executable, str(ROOT / "annotation_app.py"), "--pick-folder"], capture_output=True)
                    if result.returncode:
                        raise ValueError("无法打开文件夹选择窗口，请直接输入目录路径")
                    self.send_json({"path": result.stdout.decode("utf-8").strip()})
                elif url.path == "/api/open":
                    self.send_json(app.open_folder(data["path"]))
                elif url.path == "/api/preview":
                    self.send_json({"media": app.preview(data["name"])})
                elif url.path == "/api/save":
                    app.save(**data)
                    self.send_json(app.snapshot())
                else:
                    self.send_error(404)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pick-folder", action="store_true")
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg") or str(ROOT / "ffmpeg.exe" if (ROOT / "ffmpeg.exe").exists() else Path("C:/ffmpeg/bin/ffmpeg.exe")))
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    if args.pick_folder:
        sys.stdout.reconfigure(encoding="utf-8")
        print(choose_folder())
        return
    from capture_app.splitter import ensure_ffmpeg
    app = Annotator(str(ensure_ffmpeg(args.ffmpeg)))
    token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(app, token))
    address = f"http://127.0.0.1:{server.server_port}/?token={token}"
    print(f"Video annotator: {address}\nKeep this terminal open. Ctrl+C to exit; queued exports will finish.", flush=True)
    webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.pool.shutdown(wait=True)
        app.cache.cleanup()


if __name__ == "__main__":
    main()
