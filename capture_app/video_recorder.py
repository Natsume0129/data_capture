from __future__ import annotations

import math
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from numpy import ndarray


class TimestampedVideoWriter:
    """Encode only acquired frames, on the recording's monotonic timeline."""

    _TIME_BASE = Fraction(1, 1000)
    _MAX_SOURCE_AGE = 2.0

    def __init__(
        self,
        path: Path,
        width: int,
        height: int,
        fps: float,
        started_at: float,
    ) -> None:
        # Keep importing the application possible without the optional encoder.
        try:
            import av
        except ImportError as exc:
            raise RuntimeError(
                "Video recording requires PyAV. Install the application "
                "dependencies with: python -m pip install -r requirements.txt"
            ) from exc

        if width <= 0 or height <= 0 or not math.isfinite(fps) or fps <= 0:
            raise ValueError("video dimensions and nominal frame rate must be positive")
        if not math.isfinite(started_at):
            raise ValueError("recording start time must be finite")

        self.started_at = started_at
        self._av = av
        self._width = width
        self._height = height
        self._nominal_duration = max(1, round(1000 / fps))
        self._closed = False
        self._flushed = False
        self._frame_count = 0
        self._last_pts: int | None = None
        self._last_captured_at: float | None = None
        self._pending_packet = None
        self._last_packet_duration: int | None = None
        self._container = av.open(
            str(path),
            mode="w",
            format="mp4",
            options={"video_track_timescale": "1000"},
        )
        try:
            self._stream = self._container.add_stream(
                "libx264", rate=Fraction(str(fps)).limit_denominator(10_000)
            )
            self._stream.width = width
            self._stream.height = height
            self._stream.pix_fmt = "yuv420p"
            self._stream.time_base = self._TIME_BASE
            self._stream.codec_context.time_base = self._TIME_BASE
            self._stream.codec_context.max_b_frames = 0
            self._stream.options = {"preset": "ultrafast", "tune": "zerolatency"}
        except Exception:
            self._close()
            raise

    @property
    def frame_count(self) -> int:
        return self._frame_count

    def timestamp_ms(self, now: float) -> int:
        if not math.isfinite(now):
            raise ValueError("recording timestamp must be finite")
        return max(0, round((now - self.started_at) * 1000))

    def write_frame(
        self, frame: ndarray, captured_at: float, *, initial: bool = False
    ) -> None:
        if self._closed:
            raise RuntimeError("recording is already closed")
        try:
            if not math.isfinite(captured_at):
                raise ValueError("frame acquisition time must be finite")
            if not initial and captured_at < self.started_at:
                return
            pts = 0 if initial else self.timestamp_ms(captured_at)
            if self._last_pts is not None and pts <= self._last_pts:
                return
            if frame.shape != (self._height, self._width, 3):
                raise ValueError("BGR frame dimensions do not match the recording")
            video_frame = self._av.VideoFrame.from_ndarray(frame, format="bgr24")
            video_frame.pts = pts
            video_frame.time_base = self._TIME_BASE
            for packet in self._stream.encode(video_frame):
                self._accept_packet(packet)
            self._last_pts = pts
            self._last_captured_at = captured_at
            self._frame_count += 1
        except Exception:
            self._abort_after_error()
            raise

    def _accept_packet(self, packet) -> None:
        if packet.pts is None:
            raise RuntimeError("video encoder returned a packet without a timestamp")
        if packet.time_base is None:
            packet.time_base = self._TIME_BASE
        if self._pending_packet is not None:
            previous = self._pending_packet
            duration = round(
                (packet.pts * packet.time_base - previous.pts * previous.time_base)
                / previous.time_base
            )
            if duration <= 0:
                raise RuntimeError("video encoder returned non-increasing timestamps")
            previous.duration = duration
            self._container.mux(previous)
            self._last_packet_duration = duration
        # Keep one real frame's packet until its actual display duration is known.
        self._pending_packet = packet

    def _flush_encoder(self) -> None:
        if not self._flushed:
            self._flushed = True
            for packet in self._stream.encode(None):
                self._accept_packet(packet)

    def _mux_last_packet(self, duration_ms: int | None = None) -> None:
        packet = self._pending_packet
        if packet is None:
            return
        if duration_ms is None:
            # An interrupted recording has no declared stop boundary. Retain the
            # acquired packet, without extending it to an unknown wall-clock end.
            packet.duration = self._last_packet_duration or self._nominal_duration
        else:
            packet.duration = round(
                (duration_ms * self._TIME_BASE - packet.pts * packet.time_base)
                / packet.time_base
            )
            if packet.duration < 0:
                raise RuntimeError("recording stopped before its last acquired frame")
        self._container.mux(packet)
        self._pending_packet = None

    def finish(self, stopped_at: float) -> int:
        if self._closed:
            raise RuntimeError("recording is already closed")
        try:
            duration_ms = self.timestamp_ms(stopped_at)
            if not self._frame_count:
                raise RuntimeError("recording contains no camera frames")
            if stopped_at - self._last_captured_at > self._MAX_SOURCE_AGE:
                raise RuntimeError("camera frames stopped arriving during recording")
            if stopped_at < self.started_at or stopped_at < self._last_captured_at:
                raise RuntimeError("recording stopped before its last acquired frame")
            self._flush_encoder()
            self._mux_last_packet(duration_ms)
        except Exception:
            self._abort_after_error()
            raise
        self._close()
        return duration_ms

    def abort(self) -> None:
        """Close an interrupted recording, retaining all already acquired frames."""
        if self._closed:
            return
        try:
            self._flush_encoder()
            self._mux_last_packet()
        finally:
            self._close()

    def _abort_after_error(self) -> None:
        try:
            self.abort()
        except Exception:
            # Preserve the original capture/encoding error after best-effort close.
            pass

    def _close(self) -> None:
        self._closed = True
        self._container.close()
