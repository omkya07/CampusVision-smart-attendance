"""
CCTV Session Worker
--------------------------------------------------------
Uses FaceEngine (InsightFace ArcFace primary, dlib fallback) for multi-face
detection + recognition on an RTSP stream, with live annotated preview.
"""

import cv2
import numpy as np
import threading
import time
import os
from datetime import datetime

from face_engine import FaceEngine
from latest_frame_reader import LatestFrameReader
from face_index import FaceIndex

# For a wide classroom shot (many students, back row far from camera),
# resizing down too much before detection throws away the detail that
# distant/small faces need. Default raised for that use case - override if
# your camera framing is tighter (e.g. small room, few students):
#   set FRAME_RESIZE_WIDTH=960     (Windows)   /   export FRAME_RESIZE_WIDTH=960   (Mac/Linux)
def _cuda_available():
    try:
        import onnxruntime as ort
        return "CUDAExecutionProvider" in ort.get_available_providers()
    except Exception:
        return False

_HAS_CUDA = _cuda_available()
# CPU: less frequent recognition; GPU: snappier
SAMPLE_INTERVAL_SEC = float(os.environ.get(
    "SAMPLE_INTERVAL_SEC",
    "1.3" if _HAS_CUDA else "2.5",
))
PREVIEW_INTERVAL_SEC = 0.5
CONFIRM_COUNT = 1          # 1 solid match is enough
FRAME_RESIZE_WIDTH = int(os.environ.get(
    "FRAME_RESIZE_WIDTH",
    "1920" if _HAS_CUDA else "960",
))
RECONNECT_DELAY_SEC = 3
MAX_RECONNECT_ATTEMPTS = 15
# Looser than webcam scan — ceiling CCTV angle differs a lot from enrollment
CCTV_MATCH_THRESHOLD = 0.28


class CCTVSession:
    def __init__(self, load_students_db_fn, mark_attendance_fn):
        self._load_students_db = load_students_db_fn
        self._mark_attendance = mark_attendance_fn
        self._lock = threading.Lock()
        self._thread = None
        self._running = False
        self._stop_flag = False
        self.rtsp_url = None
        self.duration_sec = None
        self.start_time = None
        self.session_name = None
        self.camera_name = None
        self.session_started_at = None
        self.tracked = {}
        self.marked_this_session = []
        self.last_error = None
        self.frames_processed = 0
        self.faces_last_frame = 0
        self.enrolled_faces = 0
        self.detector_mode = "init"
        self._preview_jpeg = None
        self._preview_lock = threading.Lock()
        try:
            eng = FaceEngine.get()
            self.detector_mode = eng.model_name
            print(f"[CCTV] Face engine ready: {eng.model_name}")
        except Exception as e:
            self.detector_mode = "error"
            self.last_error = str(e)
            print(f"[CCTV] Face engine failed: {e}")

    def start(self, rtsp_url, duration_minutes=40, session_name="Lecture", camera_name=""):
        with self._lock:
            if self._running:
                return False, "A CCTV session is already running. Stop it before starting another camera."
            self.rtsp_url = rtsp_url
            self.duration_sec = max(1, int(duration_minutes)) * 60
            self.start_time = time.time()
            self.session_name = (session_name or "Lecture").strip() or "Lecture"
            self.camera_name = (camera_name or "").strip()
            self.session_started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.tracked = {}
            self.marked_this_session = []
            self.last_error = None
            self.frames_processed = 0
            self.faces_last_frame = 0
            self._stop_flag = False
            self._running = True
            with self._preview_lock:
                self._preview_jpeg = None
            db = self._load_students_db()
            self.enrolled_faces = sum(len(s.get("encodings", {})) for s in db.values())
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            cam_bit = f" on camera \"{self.camera_name}\"" if self.camera_name else ""
            msg = (
                f"Session \"{self.session_name}\"{cam_bit} started at {self.session_started_at} "
                f"({duration_minutes} min, engine: {self.detector_mode}, "
                f"enrolled encodings: {self.enrolled_faces})."
            )
            if self.enrolled_faces == 0:
                msg += " WARNING: no faces enrolled — complete Face Setup first."
            return True, msg

    def stop(self):
        with self._lock:
            if not self._running:
                return False, "No CCTV session is running."
            self._stop_flag = True
            return True, "Stopping CCTV session..."

    def status(self):
        with self._lock:
            elapsed = time.time() - self.start_time if self.start_time else 0
            remaining = max(0, self.duration_sec - elapsed) if self.duration_sec else 0
            return {
                "running": self._running,
                "session_name": self.session_name,
                "camera_name": getattr(self, "camera_name", None),
                "session_started_at": self.session_started_at,
                "elapsed_sec": int(elapsed),
                "remaining_sec": int(remaining),
                "marked_count": len(self.marked_this_session),
                "marked_students": list(self.marked_this_session),
                "tracked_count": len(self.tracked),
                "frames_processed": self.frames_processed,
                "faces_last_frame": self.faces_last_frame,
                "enrolled_faces": self.enrolled_faces,
                "last_error": self.last_error,
                "detector_mode": self.detector_mode,
            }

    def get_preview_jpeg(self):
        with self._preview_lock:
            return self._preview_jpeg

    def _set_preview(self, frame_bgr):
        ok, buf = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
        if ok:
            with self._preview_lock:
                self._preview_jpeg = buf.tobytes()

    def _run(self):
        # LatestFrameReader runs its own background thread that continuously
        # drains the RTSP stream and always exposes the single newest frame.
        # This decouples "how fast the camera sends frames" from "how long our
        # detection takes" - no more backlog, no more catch-up lag.
        reader = LatestFrameReader(
            self.rtsp_url,
            reconnect_delay_sec=RECONNECT_DELAY_SEC,
            max_reconnect_attempts=MAX_RECONNECT_ATTEMPTS,
        ).start()

        # Give it a moment to establish the connection before giving up
        wait_start = time.time()
        while reader.read() is None and reader._running and time.time() - wait_start < 8:
            time.sleep(0.05)

        if reader.read() is None and not reader._running:
            self.last_error = reader.last_error or "Could not open CCTV stream."
            reader.stop()
            with self._lock:
                self._running = False
            return

        last_sample_time = 0
        last_preview_time = 0

        while True:
            with self._lock:
                if self._stop_flag:
                    break
                if time.time() - self.start_time >= self.duration_sec:
                    break

            frame = reader.read()
            if frame is None:
                if not reader._running:
                    self.last_error = reader.last_error or "CCTV stream disconnected."
                    break
                time.sleep(0.02)
                continue

            now = time.time()
            do_recognize = (now - last_sample_time) >= SAMPLE_INTERVAL_SEC
            do_preview = (now - last_preview_time) >= PREVIEW_INTERVAL_SEC

            try:
                if do_recognize:
                    # Full pipeline: detection + embedding + matching.
                    # This is the expensive step - only run it on its own interval.
                    annotated, n_faces = self._process_frame(frame, recognize=True)
                    self._set_preview(annotated)
                    last_preview_time = now
                    last_sample_time = now
                    self.frames_processed += 1
                    self.faces_last_frame = n_faces
                elif do_preview:
                    # Cheap refresh: just re-stream the current frame with the
                    # last-known HUD text, WITHOUT re-running detection. Keeps
                    # the preview looking live between recognition ticks at
                    # near-zero extra compute cost.
                    self._set_preview(self._cheap_hud_frame(frame))
                    last_preview_time = now
            except Exception as e:
                self.last_error = f"Frame processing error: {str(e)}"
                print(f"[CCTV] {self.last_error}")

        reader.stop()
        with self._lock:
            self._running = False
            self._finished_at = time.time()

    def _cheap_hud_frame(self, frame):
        """Resize + HUD text only - no detection. Used for preview-only ticks."""
        h, w = frame.shape[:2]
        if w > FRAME_RESIZE_WIDTH:
            scale = FRAME_RESIZE_WIDTH / w
            frame = cv2.resize(frame, (FRAME_RESIZE_WIDTH, int(h * scale)))
        hud = f"Faces: {self.faces_last_frame}  |  Marked: {len(self.marked_this_session)}  |  {self.detector_mode}"
        cv2.putText(frame, hud, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (40, 220, 160), 2, cv2.LINE_AA)
        return frame

    def _process_frame(self, frame, recognize=True):
        h, w = frame.shape[:2]
        if w > FRAME_RESIZE_WIDTH:
            scale = FRAME_RESIZE_WIDTH / w
            frame = cv2.resize(frame, (FRAME_RESIZE_WIDTH, int(h * scale)))

        if len(frame.shape) == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        elif frame.shape[2] == 1:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

        eng = FaceEngine.get()
        faces = eng.detect_and_embed(frame)
        annotated = frame.copy()

        known, meta = [], []
        if recognize:
            db = self._load_students_db()
            classes = getattr(self, "classes", [])
            branches = getattr(self, "branches", [])
            divisions = getattr(self, "divisions", [])
            semesters = getattr(self, "semesters", [])
            for prn, s in db.items():
                if classes:
                    matched = False
                    s_branch = (s.get("branch") or "").strip().lower()
                    s_div = (s.get("division") or "").strip().upper()
                    s_sem = (s.get("semester") or "").strip().upper()
                    for c in classes:
                        if isinstance(c, dict):
                            req_br = (c.get("branch") or "").strip().lower()
                            req_div = (c.get("division") or "").strip().upper()
                            req_sems = [str(x).upper() for x in c.get("semesters", [])]
                            if req_br and req_br != s_branch:
                                continue
                            if req_div and req_div != s_div:
                                continue
                            if req_sems and s_sem not in req_sems:
                                continue
                            matched = True
                            break
                        elif isinstance(c, str):
                            parts = c.split("_")
                            if len(parts) >= 3 and parts[2].upper() == s_div and parts[0].lower() in s_branch:
                                matched = True
                                break
                    if not matched:
                        continue
                else:
                    if branches and s.get("branch") not in branches:
                        continue
                    if divisions and s.get("division") not in divisions:
                        continue
                    if semesters and s.get("semester") not in semesters:
                        continue
                for _a, enc in s.get("encodings", {}).items():
                    known.append(np.array(enc, dtype=np.float32))
                    meta.append((prn, s))

        # Fast gallery match (FAISS if installed, else NumPy)
        gallery = None
        if recognize and known:
            try:
                dim = eng.embedding_dim()
            except Exception:
                dim = 512
            gallery = FaceIndex(dim=dim, threshold=CCTV_MATCH_THRESHOLD)
            gallery.build(known, meta)

        for face in faces:
            x1, y1, x2, y2 = face.bbox
            label = "Unknown"
            color = (0, 200, 255)

            if gallery is not None and gallery.size > 0:
                hit, score = gallery.search(face.embedding, threshold=CCTV_MATCH_THRESHOLD)
                if hit is not None:
                    prn, student = hit
                    label = f"{student['name']} ({student['roll_no']}) {score:.2f}"
                    color = (0, 220, 120)
                    self._register_sighting(prn, student)
                elif score > 0:
                    label = f"Unknown ({score:.2f})"

            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            tw = max(90, len(label) * 9)
            cv2.rectangle(annotated, (x1, max(0, y1 - 22)), (x1 + tw, y1), color, -1)
            cv2.putText(
                annotated, label, (x1 + 4, max(14, y1 - 6)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA,
            )

        hud = f"Faces: {len(faces)}  |  Marked: {len(self.marked_this_session)}  |  {self.detector_mode}"
        cv2.putText(annotated, hud, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (40, 220, 160), 2, cv2.LINE_AA)
        return annotated, len(faces)

    def _register_sighting(self, prn, student):
        with self._lock:
            if prn not in self.tracked:
                self.tracked[prn] = {
                    "seen_count": 0,
                    "name": student["name"],
                    "roll_no": student["roll_no"],
                    "marked": False,
                }
            self.tracked[prn]["seen_count"] += 1
            if (self.tracked[prn]["seen_count"] >= CONFIRM_COUNT
                    and not self.tracked[prn]["marked"]):
                try:
                    newly_marked = self._mark_attendance(
                        student, session_name=self.session_name or "Lecture"
                    )
                except Exception as e:
                    print(f"[CCTV] mark_attendance error: {e}")
                    newly_marked = False
                self.tracked[prn]["marked"] = True
                # Always show in session table so UI is not empty
                self.marked_this_session.append({
                    "name": student["name"],
                    "roll_no": student["roll_no"],
                    "time": datetime.now().strftime("%H:%M:%S"),
                })
                if newly_marked:
                    print(f"[CCTV] Marked present: {student['name']} ({student['roll_no']})")
                else:
                    print(f"[CCTV] Already marked today: {student['name']} ({student['roll_no']})")


cctv_session = None


def init_cctv_session(load_students_db_fn, mark_attendance_fn):
    global cctv_session
    cctv_session = CCTVSession(load_students_db_fn, mark_attendance_fn)
    return cctv_session
