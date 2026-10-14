"""
Low-Latency RTSP Frame Reader
--------------------------------------------------------
Drop-in replacement for cv2.VideoCapture when reading from an RTSP CCTV
stream. Prevents the classic "lag builds up over time" problem.

WHY THIS IS NEEDED:
OpenCV's VideoCapture buffers incoming frames internally. If your
processing (detection + recognition) takes longer than the camera's
frame interval, frames queue up. Every .read() call then returns the
OLDEST buffered frame, not the current one - so your preview/detection
falls further behind the longer the session runs.

HOW THIS FIXES IT:
A background thread continuously reads frames as fast as the camera
sends them and only keeps the SINGLE most recent one, immediately
overwriting anything older. Your main loop always grabs the latest
frame, at whatever pace it can actually process - no backlog, no
growing lag, at the cost of only skipping (not queuing) frames your
pipeline was too slow to look at. That trade-off is exactly right for
live attendance: you don't need every frame, you need the CURRENT one.

Usage:
    reader = LatestFrameReader(rtsp_url)
    reader.start()
    while True:
        frame = reader.read()   # always the newest available frame
        if frame is None:
            continue
        # ... run detection/recognition on `frame` ...
    reader.stop()
"""

import cv2
import threading
import time


class LatestFrameReader:
    def __init__(self, source, reconnect_delay_sec=3, max_reconnect_attempts=20):
        self.source = source
        self.reconnect_delay_sec = reconnect_delay_sec
        self.max_reconnect_attempts = max_reconnect_attempts

        self._cap = None
        self._latest_frame = None
        self._frame_id = 0
        self._last_frame_time = 0.0
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self.last_error = None
        self.connected = False

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass

    def read(self):
        """Returns the most recent frame available, or None if not ready yet."""
        with self._lock:
            if self._latest_frame is None:
                return None
            return self._latest_frame.copy()

    def read_with_meta(self):
        """Returns (frame, frame_id, age_seconds, connected)."""
        with self._lock:
            if self._latest_frame is None:
                return None, 0, 999.0, self.connected
            age = time.time() - self._last_frame_time if self._last_frame_time else 999.0
            return self._latest_frame.copy(), self._frame_id, age, self.connected

    def _open_capture(self):
        import os
        src = self.source
        if isinstance(src, str) and src.strip().isdigit():
            src = int(src.strip())

        if isinstance(src, int):
            cap = cv2.VideoCapture(src, cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY)
        else:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|timeout;5000000"
            )
            cap = cv2.VideoCapture(src, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                cap = cv2.VideoCapture(src)

        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        return cap

    def _reader_loop(self):
        self._cap = self._open_capture()
        reconnect_attempts = 0

        if not self._cap or not self._cap.isOpened():
            self.last_error = f"Could not connect to camera source ({self.source})."
            self.connected = False
        else:
            self.connected = True

        consecutive_failures = 0
        MAX_CONSECUTIVE_FAILURES = 15  # tolerate transient packet hiccups (~0.5s) without reconnect freeze

        while self._running:
            if not self._cap or not self._cap.isOpened():
                reconnect_attempts += 1
                if reconnect_attempts > self.max_reconnect_attempts:
                    self.last_error = f"Camera disconnected; exceeded {self.max_reconnect_attempts} reconnect attempts."
                    self.connected = False
                    break
                time.sleep(self.reconnect_delay_sec)
                self._cap = self._open_capture()
                continue

            ret, frame = self._cap.read()
            if not ret or frame is None or frame.size == 0:
                consecutive_failures += 1
                if consecutive_failures < MAX_CONSECUTIVE_FAILURES:
                    # Brief network stutter / dropped packet: retry reading next frame without tearing down connection
                    time.sleep(0.015)
                    continue

                # Stream genuinely lost after multiple consecutive failures
                consecutive_failures = 0
                self.connected = False
                reconnect_attempts += 1
                if reconnect_attempts > self.max_reconnect_attempts:
                    self.last_error = "Lost connection to CCTV stream."
                    break
                try:
                    self._cap.release()
                except Exception:
                    pass
                time.sleep(self.reconnect_delay_sec)
                self._cap = self._open_capture()
                continue

            consecutive_failures = 0
            reconnect_attempts = 0
            self.connected = True
            with self._lock:
                self._latest_frame = frame
                self._frame_id += 1
                self._last_frame_time = time.time()

            # Yield GIL briefly so preview and Flask worker threads run smoothly
            time.sleep(0.005)

        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass
        self._running = False

