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
import base64
from datetime import datetime

try:
    from dotenv import load_dotenv
    _root_env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(_root_env):
        load_dotenv(_root_env)
    _backend_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(_backend_env):
        load_dotenv(_backend_env)
except Exception:
    pass

from face_engine import FaceEngine
from latest_frame_reader import LatestFrameReader
from face_index import FaceIndex

# For a wide classroom shot (many students, back row far from camera),
# resizing down too much before detection throws away the detail that
# distant/small faces need.
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
# Confirmation count: require 3 sightings across distinct frames before marking attendance
CONFIRM_COUNT = int(os.environ.get("CONFIRM_COUNT", "3"))
FRAME_RESIZE_WIDTH = int(os.environ.get(
    "FRAME_RESIZE_WIDTH",
    "1920" if _HAS_CUDA else "1280",
))
RECONNECT_DELAY_SEC = 3
MAX_RECONNECT_ATTEMPTS = 15
# Calibrated for classroom CCTV: 0.30 prevents false positive identity swapping
CCTV_MATCH_THRESHOLD = float(os.environ.get("CCTV_MATCH_THRESHOLD", "0.30"))


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
        self._detections = []
        self._detections_time = 0.0
        self._detections_lock = threading.Lock()
        self._rec_thread = None
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
            with self._detections_lock:
                self._detections = []
                self._detections_time = 0.0
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

    def _create_status_frame(self, title: str, subtitle: str, clock_str: str):
        img = np.zeros((360, 640, 3), dtype=np.uint8)
        img[:] = (18, 22, 28)
        cv2.putText(img, title, (30, 140), cv2.FONT_HERSHEY_SIMPLEX, 0.70, (0, 200, 255), 2, cv2.LINE_AA)
        cv2.putText(img, subtitle, (30, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (160, 160, 160), 1, cv2.LINE_AA)
        cv2.putText(img, f"Live Clock: {clock_str}", (30, 225), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (40, 220, 160), 1, cv2.LINE_AA)
        return img

    def _run(self):
        # 1. Start low-latency RTSP frame reader
        reader = LatestFrameReader(
            self.rtsp_url,
            reconnect_delay_sec=RECONNECT_DELAY_SEC,
            max_reconnect_attempts=MAX_RECONNECT_ATTEMPTS,
        ).start()

        # Give it a moment to connect
        wait_start = time.time()
        while reader.read() is None and reader._running and time.time() - wait_start < 6:
            time.sleep(0.05)

        if reader.read() is None and not reader._running:
            self.last_error = reader.last_error or "Could not open CCTV stream."
            reader.stop()
            with self._lock:
                self._running = False
            return

        # 2. Start asynchronous AI recognition worker in a separate background thread
        # This prevents the heavy ArcFace / SCRFD CNN pipeline from blocking live preview frames!
        self._rec_thread = threading.Thread(target=self._recognition_worker, args=(reader,), daemon=True)
        self._rec_thread.start()

        # 3. High-rate Preview Loop (~4-5 FPS)
        # Keeps camera video moving continuously and updates the clock second-by-second with zero lag!
        while True:
            with self._lock:
                if self._stop_flag:
                    break
                if time.time() - self.start_time >= self.duration_sec:
                    break

            frame, frame_id, age, connected = reader.read_with_meta()
            now = time.time()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if frame is None or not connected or age > 6.0:
                reconn_msg = f"Connecting / Reconnecting ({self.rtsp_url[:40]})..." if not connected else "Buffering stream frames..."
                status_img = self._create_status_frame("CCTV Camera Connecting / Buffering", reconn_msg, now_str)
                self._set_preview(status_img)
                time.sleep(0.3)
                continue

            h, w = frame.shape[:2]
            scale = 1.0
            if w > FRAME_RESIZE_WIDTH:
                scale = FRAME_RESIZE_WIDTH / float(w)
                frame = cv2.resize(frame, (FRAME_RESIZE_WIDTH, int(h * scale)))

            if len(frame.shape) == 2:
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            elif frame.shape[2] == 1:
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

            annotated = frame.copy()

            # Overlay face bounding boxes from latest AI pass (if < 3.5 seconds old)
            with self._detections_lock:
                dets = list(self._detections)
                det_age = now - self._detections_time

            if det_age < 3.5:
                for d in dets:
                    bx1, by1, bx2, by2 = d["bbox"]
                    x1 = max(0, int(bx1 * scale))
                    y1 = max(0, int(by1 * scale))
                    x2 = min(annotated.shape[1] - 1, int(bx2 * scale))
                    y2 = min(annotated.shape[0] - 1, int(by2 * scale))
                    lbl = d["label"]
                    col = d["color"]
                    cv2.rectangle(annotated, (x1, y1), (x2, y2), col, 2)
                    tw = max(90, len(lbl) * 9)
                    cv2.rectangle(annotated, (x1, max(0, y1 - 22)), (min(annotated.shape[1] - 1, x1 + tw), y1), col, -1)
                    cv2.putText(
                        annotated, lbl, (x1 + 4, max(14, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.50, (0, 0, 0), 1, cv2.LINE_AA,
                    )

            # Top live HUD bar with real-time second-by-second clock and active live badge
            cv2.rectangle(annotated, (0, 0), (annotated.shape[1], 34), (18, 22, 28), -1)
            cv2.circle(annotated, (14, 17), 5, (40, 220, 120), -1)
            hud_left = f"LIVE  |  {now_str}  |  Faces: {self.faces_last_frame}  |  Marked: {len(self.marked_this_session)}"
            cv2.putText(annotated, hud_left, (26, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (40, 220, 160), 1, cv2.LINE_AA)

            hud_right = f"{self.session_name} ({self.detector_mode})"
            right_x = max(200, annotated.shape[1] - len(hud_right) * 9 - 15)
            cv2.putText(annotated, hud_right, (right_x, 23),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.46, (200, 200, 200), 1, cv2.LINE_AA)

            self._set_preview(annotated)
            time.sleep(0.20)  # ~5 FPS fast preview update: smooth movement, real-time seconds!

        reader.stop()
        with self._lock:
            self._running = False
            self._finished_at = time.time()

    def _recognition_worker(self, reader):
        """Asynchronous background worker that runs AI face detection and recognition."""
        last_sample_time = 0.0
        while True:
            with self._lock:
                if self._stop_flag or not self._running:
                    break
                if time.time() - self.start_time >= self.duration_sec:
                    break

            now = time.time()
            if (now - last_sample_time) < SAMPLE_INTERVAL_SEC:
                time.sleep(0.05)
                continue

            frame = reader.read()
            if frame is None:
                time.sleep(0.08)
                continue

            try:
                dets, n_faces = self._detect_and_match(frame)
                with self._detections_lock:
                    self._detections = dets
                    self._detections_time = time.time()
                self.faces_last_frame = n_faces
                self.frames_processed += 1
                last_sample_time = time.time()
            except Exception as e:
                self.last_error = f"Recognition worker error: {str(e)}"
                print(f"[CCTV] {self.last_error}")
                time.sleep(0.3)

    def _detect_and_match(self, frame):
        """Heavy step: FaceEngine detection + embedding + gallery match."""
        h, w = frame.shape[:2]
        if w > FRAME_RESIZE_WIDTH:
            scale = FRAME_RESIZE_WIDTH / float(w)
            frame = cv2.resize(frame, (FRAME_RESIZE_WIDTH, int(h * scale)))

        if len(frame.shape) == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        elif frame.shape[2] == 1:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

        eng = FaceEngine.get()
        faces = eng.detect_and_embed(frame)

        db = self._load_students_db()
        classes = getattr(self, "classes", [])
        branches = getattr(self, "branches", [])
        divisions = getattr(self, "divisions", [])
        semesters = getattr(self, "semesters", [])

        known, meta = [], []
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
        if known:
            try:
                dim = eng.embedding_dim()
            except Exception:
                dim = 512
            gallery = FaceIndex(dim=dim, threshold=CCTV_MATCH_THRESHOLD)
            gallery.build(known, meta)

        detections = []
        for face in faces:
            x1, y1, x2, y2 = [int(v) for v in face.bbox]
            label = "Unknown"
            color = (0, 200, 255)

            if gallery is not None and gallery.size > 0:
                hit, score = gallery.search(face.embedding, threshold=CCTV_MATCH_THRESHOLD)
                if hit is not None:
                    prn, student = hit
                    label = f"{student['name']} ({student['roll_no']}) {score:.2f}"
                    color = (0, 220, 120)

                    # Extract actual live CCTV face crop for attendance snapshot proof
                    snap_b64 = None
                    try:
                        hf, wf = frame.shape[:2]
                        pad_x = int((x2 - x1) * 0.35)
                        pad_y = int((y2 - y1) * 0.40)
                        cx1 = max(0, int(x1 - pad_x))
                        cy1 = max(0, int(y1 - pad_y))
                        cx2 = min(wf, int(x2 + pad_x))
                        cy2 = min(hf, int(y2 + pad_y))
                        if cx2 > cx1 and cy2 > cy1:
                            crop = frame[cy1:cy2, cx1:cx2].copy()
                            ch, cw = crop.shape[:2]
                            if max(ch, cw) > 240:
                                sc = 240.0 / float(max(ch, cw))
                                crop = cv2.resize(crop, (int(cw * sc), int(ch * sc)))
                            ok, buf = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                            if ok:
                                snap_b64 = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("utf-8")
                    except Exception as e:
                        print(f"[CCTV] Error generating live snapshot crop: {e}")

                    self._register_sighting(prn, student, snapshot_b64=snap_b64)
                elif score > 0:
                    label = f"Unknown ({score:.2f})"

            detections.append({
                "bbox": (x1, y1, x2, y2),
                "label": label,
                "color": color
            })

        return detections, len(faces)

    def _register_sighting(self, prn, student, snapshot_b64=None):
        with self._lock:
            if prn not in self.tracked:
                self.tracked[prn] = {
                    "seen_count": 0,
                    "name": student["name"],
                    "roll_no": student["roll_no"],
                    "marked": False,
                    "snapshot": snapshot_b64,
                }
            elif snapshot_b64 and not self.tracked[prn].get("snapshot"):
                self.tracked[prn]["snapshot"] = snapshot_b64
                if self.tracked[prn].get("marked"):
                    try:
                        self._mark_attendance(
                            student,
                            session_name=self.session_name or "Lecture",
                            snapshot_b64=snapshot_b64,
                        )
                    except Exception:
                        pass

            self.tracked[prn]["seen_count"] += 1
            if (self.tracked[prn]["seen_count"] >= CONFIRM_COUNT
                    and not self.tracked[prn]["marked"]):
                snap_to_use = snapshot_b64 or self.tracked[prn].get("snapshot")
                try:
                    newly_marked = self._mark_attendance(
                        student,
                        session_name=self.session_name or "Lecture",
                        snapshot_b64=snap_to_use,
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
