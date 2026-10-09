"""
Multi-session CCTV manager
--------------------------------------------------------
Supports many concurrent lecture sessions (different classrooms / teachers).
Each session has its own RTSP worker thread and attendance scope.

Practical limits:
- CPU-only: ~4–8 concurrent streams is realistic
- With GPU: more is possible
- 60–80 concurrent full ArcFace pipelines need multiple servers or edge devices

The manager still accepts many session start requests; if over soft limit it
warns but can be configured via MAX_CONCURRENT_SESSIONS.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime
from typing import Dict, Optional

from cctv_session import CCTVSession

# Soft cap — raise if you have stronger hardware / multiple workers
MAX_CONCURRENT_SESSIONS = 80


class SessionManager:
    def __init__(self, load_students_db_fn, mark_attendance_fn):
        self._load_students_db = load_students_db_fn
        self._mark_attendance = mark_attendance_fn
        self._lock = threading.Lock()
        self._sessions: Dict[str, CCTVSession] = {}

    def _prune_finished(self):
        dead = [sid for sid, s in self._sessions.items() if not s.status().get("running")]
        for sid in dead:
            # keep finished sessions briefly for status polling, drop after 10 min idle
            s = self._sessions[sid]
            st = s.status()
            if not st.get("running"):
                ended = st.get("elapsed_sec", 0)
                # remove if stopped and older than duration + 600s wall time
                if getattr(s, "_finished_at", None) and time.time() - s._finished_at > 600:
                    del self._sessions[sid]

    def start(
        self,
        rtsp_url: str,
        duration_minutes: int = 40,
        session_name: str = "Lecture",
        camera_name: str = "",
        classroom: str = "",
        teacher: str = "",
        branches: list = None,
        divisions: list = None,
        semesters: list = None,
        classes: list = None,
    ):
        with self._lock:
            self._prune_finished()
            running = sum(1 for s in self._sessions.values() if s.status().get("running"))
            if running >= MAX_CONCURRENT_SESSIONS:
                return None, (
                    f"Maximum concurrent sessions ({MAX_CONCURRENT_SESSIONS}) reached. "
                    "Stop an existing session or add another server."
                )

            sid = str(uuid.uuid4())[:10]
            worker = CCTVSession(self._load_students_db, self._mark_attendance)
            # Bind identity onto the worker for status
            worker.session_id = sid
            worker.classroom = classroom
            worker.teacher = teacher
            worker.branches = branches or []
            worker.divisions = divisions or []
            worker.semesters = semesters or []
            worker.classes = classes or []

            ok, msg = worker.start(
                rtsp_url,
                duration_minutes,
                session_name=session_name,
                camera_name=camera_name or classroom,
            )
            if not ok:
                return None, msg

            self._sessions[sid] = worker
            return sid, msg

    def stop(self, session_id: str):
        with self._lock:
            s = self._sessions.get(session_id)
            if not s:
                return False, "Session not found."
            ok, msg = s.stop()
            s._finished_at = time.time()
            return ok, msg

    def stop_all(self):
        with self._lock:
            for s in self._sessions.values():
                if s.status().get("running"):
                    s.stop()
                    s._finished_at = time.time()
            return True, "Stop signal sent to all sessions."

    def status(self, session_id: Optional[str] = None):
        with self._lock:
            if session_id:
                s = self._sessions.get(session_id)
                if not s:
                    return {"running": False, "error": "Session not found"}
                st = s.status()
                st["session_id"] = session_id
                st["classroom"] = getattr(s, "classroom", None)
                st["teacher"] = getattr(s, "teacher", None)
                return st

            items = []
            for sid, s in self._sessions.items():
                st = s.status()
                st["session_id"] = sid
                st["classroom"] = getattr(s, "classroom", None)
                st["teacher"] = getattr(s, "teacher", None)
                items.append(st)
            items.sort(key=lambda x: x.get("session_started_at") or "", reverse=True)
            return {
                "sessions": items,
                "running_count": sum(1 for x in items if x.get("running")),
                "max_concurrent": MAX_CONCURRENT_SESSIONS,
            }

    def get_preview_jpeg(self, session_id: str):
        with self._lock:
            s = self._sessions.get(session_id)
            if not s:
                return None
            return s.get_preview_jpeg()

    def get_worker(self, session_id: str) -> Optional[CCTVSession]:
        return self._sessions.get(session_id)


session_manager = None


def init_session_manager(load_students_db_fn, mark_attendance_fn):
    global session_manager
    session_manager = SessionManager(load_students_db_fn, mark_attendance_fn)
    return session_manager
