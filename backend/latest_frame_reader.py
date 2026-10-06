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
    def __init__(self, source, reconnect_delay_sec=5, max_reconnect_attempts=10):
        self.source = source
        self.reconnect_delay_sec = reconnect_delay_sec
        self.max_reconnect_attempts = max_reconnect_attempts

        self._cap = None
        self._latest_frame = None
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
            self._cap.release()

    def read(self):
        """Returns the most recent frame available, or None if not ready yet."""
        with self._lock:
            if self._latest_frame is None:
                return None
            return self._latest_frame.copy()

    def _open_capture(self):
        # Force FFmpeg backend with low-latency options - reduces RTSP's own
        # internal buffering (separate from OpenCV's buffering above).
        import os
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
            "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay"
        )
        cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
        # Ask OpenCV to keep only 1 frame in its own buffer too, where supported
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _reader_loop(self):
        self._cap = self._open_capture()
        reconnect_attempts = 0

        if not self._cap.isOpened():
            self.last_error = "Could not open stream on startup."
            self._running = False
            return

        self.connected = True

        while self._running:
            ret, frame = self._cap.read()
            if not ret:
                self.connected = False
                reconnect_attempts += 1
                if reconnect_attempts > self.max_reconnect_attempts:
                    self.last_error = "Lost connection and exceeded max reconnect attempts."
                    break
                self._cap.release()
                time.sleep(self.reconnect_delay_sec)
                self._cap = self._open_capture()
                continue

            reconnect_attempts = 0
            self.connected = True
            with self._lock:
                self._latest_frame = frame
            # Deliberately no sleep here - we WANT to drain frames as fast as
            # the camera sends them, so the buffer never has a chance to grow.

        if self._cap:
            self._cap.release()
        self._running = False
