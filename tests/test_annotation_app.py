import csv
import json
import shutil
import subprocess
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

import av
import pytest

from annotation_app import Annotator, STATE, handler_for


def test_annotation_export_resume_discard_and_media(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg unavailable")
    source = tmp_path / "video2.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10",
                    "-t", "2", "-c:v", "libx264", str(source)], check=True)
    shutil.copyfile(source, tmp_path / "video10.mp4")
    (tmp_path / "nested").mkdir()
    shutil.copyfile(source, tmp_path / "nested" / "hidden.mp4")
    original = source.read_bytes()
    app = Annotator(ffmpeg)
    server = None
    try:
        assert [f["name"] for f in app.open_folder(tmp_path)["files"]] == ["video2.mp4", "video10.mp4"]
        with pytest.raises(ValueError):
            app.save(source.name, 1, .5, "neutral")
        with pytest.raises(ValueError):
            app.save(source.name, 0, 20, "neutral")
        media = app.preview(source.name)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(app, "test-token"))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        request = Request(f"http://127.0.0.1:{server.server_port}/media/{media}?token=test-token", headers={"Range": "bytes=0-31"})
        with urlopen(request) as response:
            assert response.status == 206
            assert len(response.read()) == 32
        app.save(source.name, .3, 1.3, "polite smile")
        app.pool.submit(lambda: None).result(timeout=30)
        assert app.records[source.name]["status"] == "done", app.records[source.name]
        output = tmp_path / (source.name + "_trimmed.mp4")
        with av.open(str(output)) as video:
            assert len(list(video.decode(video=0))) == 10
            assert abs(video.duration / av.time_base - 1) < .15
        with (tmp_path / "annotations.csv").open(encoding="utf-8-sig", newline="") as f:
            assert list(csv.reader(f)) == [["文件名", "文件目录", "类别"], [output.name, str(tmp_path), "polite smile"]]
        app.open_folder(tmp_path)
        assert len(app.files) == 2
        assert app.records[source.name]["status"] == "done"
        assert json.loads((tmp_path / STATE).read_text(encoding="utf-8"))["records"][source.name]["start"] == .3
        app.save(source.name, discard=True)
        assert not output.exists()
        assert source.read_bytes() == original
        assert app.records[source.name]["status"] == "discarded"
        with (tmp_path / "annotations.csv").open(encoding="utf-8-sig", newline="") as f:
            assert len(list(csv.reader(f))) == 1
    finally:
        if server:
            server.shutdown()
            server.server_close()
        app.pool.shutdown(wait=True)
        app.cache.cleanup()
