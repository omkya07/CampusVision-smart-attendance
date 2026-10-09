"""
Smart Attendance System - Web Backend
--------------------------------------------------------
Flask server that:
    1. Serves the frontend (index.html)
    2. Manages student records (PRN, Roll No, Name, Division, Branch)
    3. Handles multi-angle face enrollment (center, left, right)
    4. Handles attendance scanning: ML-based anti-spoofing check (DeepFace /
       MiniFASNet, pretrained) followed by face recognition (recognize + mark)

Requirements:
    pip install flask face_recognition opencv-python numpy pillow deepface tf-keras

Run:
    python app.py
    Then open http://localhost:5000 in your browser

Note: On first run, DeepFace will auto-download the pretrained anti-spoofing
model weights (a few MB) — this happens once and is cached afterward.
"""

import os
import warnings

# Quiet TensorFlow / oneDNN / absl / protobuf noise before those libs load
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")
os.environ.setdefault("CUDA_MODULE_LOADING", "LAZY")
os.environ.setdefault("ORT_LOGGING_LEVEL", "3")  # 3 = ERROR only
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*tf.losses.sparse_softmax.*")

from flask import Flask, request, jsonify, send_from_directory, g
try:
    from deepface import DeepFace
    DEEPFACE_AVAILABLE = True
except Exception as _e:
    DeepFace = None
    DEEPFACE_AVAILABLE = False
    print(f"[WARN] DeepFace not available ({_e}). Anti-spoof disabled; CCTV/ArcFace still work.")
import numpy as np
import cv2
from PIL import Image
import io
import base64
import pickle
from datetime import datetime
from face_engine import FaceEngine
from session_manager import init_session_manager
from db import get_db, check_connection
from auth import require_student_auth, get_firebase_config

app = Flask(__name__, static_folder="../frontend", static_url_path="")

# Legacy local file paths - no longer used for live reads/writes now that
# MongoDB is the source of truth, but kept here since migrate_to_mongo.py
# reads these same paths for the one-time import of existing local data.
STUDENTS_DB_PATH = "students_database.pkl"
ATTENDANCE_LOG = "attendance_log.csv"
CAMERAS_DB_PATH = "cameras.json"

# Lazy singleton — first request loads InsightFace (or dlib fallback)
def engine():
    return FaceEngine.get()


ANGLES = ["center", "left", "right"]
ANGLE_LABELS = {
    "center": "Look straight at the camera",
    "left": "Move your head slightly to the left",
    "right": "Move your head slightly to the right",
}


# ---------------------------------------------------------
# Database helpers (MongoDB-backed)
# ---------------------------------------------------------
def load_students_db():
    """Returns {prn: student_dict} loaded live from MongoDB Atlas, including
    face biometrics and face photo thumbnails."""
    db = get_db()
    result = {}
    for doc in db.students.find({}):
        prn = str(doc.get("prn", "")).strip()
        if not prn:
            continue
        result[prn] = {
            "prn": prn,
            "roll_no": doc.get("roll_no", ""),
            "name": doc.get("name", ""),
            "email": doc.get("email", ""),
            "phone": doc.get("phone", ""),
            "academic_year": doc.get("academic_year", ""),
            "semester": doc.get("semester", ""),
            "division": doc.get("division", ""),
            "branch": doc.get("branch", ""),
            "photo": doc.get("photo", ""),
            "photos": doc.get("photos", {}),
            "encodings": doc.get("encodings", {}),
            "embedding_model": doc.get("embedding_model"),
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
        }
    return result


def save_students_db(students_dict):
    """Safe upsert sync of the students collection in MongoDB Atlas.
    Uses replace_one upsert to prevent data loss or wiping."""
    db = get_db()
    if not students_dict:
        db.students.delete_many({})
        return
    existing_prns = set(str(d.get("prn")) for d in db.students.find({}, {"prn": 1}))
    new_prns = set(str(k) for k in students_dict.keys())
    to_delete = existing_prns - new_prns
    if to_delete:
        db.students.delete_many({"prn": {"$in": list(to_delete)}})
    for prn, s in students_dict.items():
        doc = dict(s)
        doc["_id"] = str(prn)
        doc["prn"] = str(prn)
        doc["updated_at"] = datetime.now().isoformat()
        db.students.replace_one({"_id": str(prn)}, doc, upsert=True)


def get_year_code(semester):
    sem = (semester or "").strip().upper()
    if sem in ["SEM-I", "SEM-II", "SEM-1", "SEM-2", "1", "2"] or "FY" in sem:
        return "FY"
    if sem in ["SEM-III", "SEM-IV", "SEM-3", "SEM-4", "3", "4"] or "SY" in sem:
        return "SY"
    if sem in ["SEM-V", "SEM-VI", "SEM-5", "SEM-6", "5", "6"] or "TY" in sem:
        return "TY"
    if sem in ["SEM-VII", "SEM-VIII", "SEM-7", "SEM-8", "7", "8"] or "LY" in sem:
        return "LY"
    return "UNKNOWN"


def get_semesters_for_year(year_code):
    y = (year_code or "").upper()
    if "FY" in y or "1" in y:
        return ["SEM-I", "SEM-II", "SEM-1", "SEM-2", "1", "2"]
    if "SY" in y or "2" in y:
        return ["SEM-III", "SEM-IV", "SEM-3", "SEM-4", "3", "4"]
    if "TY" in y or "3" in y:
        return ["SEM-V", "SEM-VI", "SEM-5", "SEM-6", "5", "6"]
    if "LY" in y or "4" in y:
        return ["SEM-VII", "SEM-VIII", "SEM-7", "SEM-8", "7", "8"]
    return []


def build_student_selection_query(classes=None, branches=None, divisions=None, semesters=None):
    """
    Builds MongoDB query. If 'classes' is provided, each element represents an exact
    class selection (branch, year/semester, division) matched via $or.
    Otherwise falls back to Cartesian product of branches, divisions, semesters.
    """
    if classes:
        clauses = []
        for c in classes:
            clause = {}
            if isinstance(c, dict):
                br = (c.get("branch") or "").strip()
                div = (c.get("division") or "").strip()
                sems = c.get("semesters", [])
                yr = (c.get("year") or "").strip()
                if br:
                    clause["branch"] = br
                if div:
                    clause["division"] = div
                if sems:
                    clause["semester"] = {"$in": sems}
                elif yr:
                    mapped_sems = get_semesters_for_year(yr)
                    if mapped_sems:
                        clause["semester"] = {"$in": mapped_sems}
            elif isinstance(c, str):
                parts = c.split("_")
                if len(parts) >= 3:
                    br_code, yr_code, div_code = parts[0], parts[1], parts[2]
                    clause["division"] = div_code
                    mapped_sems = get_semesters_for_year(yr_code)
                    if mapped_sems:
                        clause["semester"] = {"$in": mapped_sems}
                    clause["branch"] = {"$regex": f"^{br_code}", "$options": "i"}
            if clause:
                clauses.append(clause)
        if clauses:
            return {"$or": clauses} if len(clauses) > 1 else clauses[0]
        return {}

    query = {}
    if branches:
        query["branch"] = {"$in": branches}
    if divisions:
        query["division"] = {"$in": divisions}
    if semesters:
        query["semester"] = {"$in": semesters}
    return query


def student_matches_selection(student, classes=None, branches=None, divisions=None, semesters=None):
    """
    In-memory test for face gallery filtering.
    """
    s_branch = (student.get("branch") or "").strip()
    s_div = (student.get("division") or "").strip()
    s_sem = (student.get("semester") or "").strip()
    s_yr = get_year_code(s_sem)

    if classes:
        for c in classes:
            if isinstance(c, dict):
                req_br = (c.get("branch") or "").strip()
                req_div = (c.get("division") or "").strip()
                req_sems = c.get("semesters", [])
                req_yr = (c.get("year") or "").strip().upper()

                if req_br and req_br.lower() != s_branch.lower():
                    continue
                if req_div and req_div.upper() != s_div.upper():
                    continue
                if req_sems:
                    if s_sem not in req_sems and s_yr != req_yr:
                        continue
                elif req_yr:
                    if s_yr != req_yr:
                        continue
                return True
            elif isinstance(c, str):
                parts = c.split("_")
                if len(parts) >= 3:
                    b_c, y_c, d_c = parts[0].upper(), parts[1].upper(), parts[2].upper()
                    if d_c != s_div.upper():
                        continue
                    if y_c != s_yr:
                        continue
                    if b_c not in s_branch.upper():
                        continue
                    return True
        return False

    if branches and s_branch not in branches:
        return False
    if divisions and s_div not in divisions:
        return False
    if semesters and s_sem not in semesters:
        return False
    return True


def decode_base64_image(data_url):
    """Convert a base64 data URL (from browser canvas or file) into an RGB numpy array."""
    header, encoded = data_url.split(",", 1) if "," in data_url else ("", data_url)
    img_bytes = base64.b64decode(encoded)
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    return np.array(img)


def mark_attendance(student, session_name="Manual Scan", snapshot_b64=None, date_str=None):
    """
    Mark student present for a specific lecture/lab session in MongoDB Atlas.
    Same student can be marked once per session per day (multiple sessions OK).
    Stores face verification snapshot for attendance proof.
    Returns True if newly written, False if already present for this session today.
    """
    db = get_db()
    session_name = (session_name or "Manual Scan").strip() or "Manual Scan"
    today = date_str or datetime.now().strftime("%Y-%m-%d")
    now_time = datetime.now().strftime("%H:%M:%S")

    # Duplicate = same PRN + same session + same date
    existing = db.attendance.find_one({
        "prn": str(student["prn"]),
        "session": session_name,
        "date": today,
    })
    
    snapshot = snapshot_b64 or student.get("photo", "")

    if existing:
        if existing.get("status") == "absent":
            db.attendance.update_one(
                {"_id": existing["_id"]},
                {"$set": {
                    "status": "present",
                    "time": now_time,
                    "snapshot": snapshot
                }}
            )
            return True
        return False

    db.attendance.insert_one({
        "session": session_name,
        "prn": str(student["prn"]),
        "roll_no": student.get("roll_no", ""),
        "name": student.get("name", ""),
        "division": student.get("division", ""),
        "branch": student.get("branch", ""),
        "date": today,
        "time": now_time,
        "snapshot": snapshot,
        "status": "present"
    })
    return True



# Multi-session manager (many classrooms / teachers at once)
sessions = init_session_manager(load_students_db, mark_attendance)


# ---------------------------------------------------------
# Frontend
# ---------------------------------------------------------
@app.route("/")
def serve_index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/student")
def serve_student_index():
    return send_from_directory(app.static_folder, "student.html")


# ---------------------------------------------------------
# Student management APIs
# ---------------------------------------------------------
@app.route("/api/students", methods=["GET"])
def get_students():
    db = load_students_db()
    result = []
    for prn, s in db.items():
        angles_done = list(s.get("encodings", {}).keys())
        result.append({
            "prn": s["prn"],
            "roll_no": s["roll_no"],
            "name": s["name"],
            "email": s.get("email", ""),
            "phone": s.get("phone", ""),
            "academic_year": s.get("academic_year", ""),
            "semester": s.get("semester", ""),
            "division": s["division"],
            "branch": s["branch"],
            "photo": s.get("photo", "") or s.get("photos", {}).get("center", ""),
            "photos": s.get("photos", {}),
            "angles_done": angles_done,
            "face_setup_complete": len(angles_done) == len(ANGLES) or len(angles_done) >= 1,
        })
    return jsonify(result)


@app.route("/api/students", methods=["POST"])
def add_student():
    data = request.json
    prn = data.get("prn", "").strip()
    if not prn:
        return jsonify({"success": False, "message": "PRN is required"}), 400

    db = load_students_db()
    if prn in db:
        return jsonify({"success": False, "message": "PRN already registered"}), 400

    db[prn] = {
        "prn": prn,
        "roll_no": data.get("roll_no", "").strip(),
        "name": data.get("name", "").strip(),
        "email": data.get("email", "").strip().lower(),
        "phone": data.get("phone", "").strip(),
        "academic_year": data.get("academic_year", "").strip(),
        "semester": data.get("semester", "").strip(),
        "division": data.get("division", "").strip(),
        "branch": data.get("branch", "").strip(),
        "encodings": {},   # angle -> encoding
    }
    save_students_db(db)
    return jsonify({"success": True, "message": "Student registered"})

@app.route("/api/students_upload", methods=["POST"])
def upload_students_csv():
    if "csv_file" not in request.files:
        return jsonify({"success": False, "message": "No file uploaded"}), 400
    
    file = request.files["csv_file"]
    if not file.filename.endswith(".csv"):
        return jsonify({"success": False, "message": "Only CSV files are supported"}), 400
        
    import csv, io
    try:
        stream = io.StringIO(file.stream.read().decode("utf-8-sig"), newline=None)
        reader = csv.DictReader(stream)
        db = load_students_db()
        count = 0
        
        for row in reader:
            normalized_row = {k.strip().lower(): v.strip() for k, v in row.items() if k}
            prn = normalized_row.get("prn", "")
            if not prn: continue
            
            if prn not in db:
                db[prn] = {"encodings": {}}
                
            db[prn]["prn"] = prn
            db[prn]["roll_no"] = normalized_row.get("roll no", normalized_row.get("roll_no", db[prn].get("roll_no", "")))
            db[prn]["name"] = normalized_row.get("name", normalized_row.get("full name", db[prn].get("name", "")))
            db[prn]["email"] = normalized_row.get("email", db[prn].get("email", ""))
            db[prn]["phone"] = normalized_row.get("phone", normalized_row.get("phone number", db[prn].get("phone", "")))
            db[prn]["academic_year"] = normalized_row.get("academic year", normalized_row.get("academic_year", db[prn].get("academic_year", "")))
            db[prn]["semester"] = normalized_row.get("semester", normalized_row.get("sem", db[prn].get("semester", "")))
            db[prn]["division"] = normalized_row.get("division", normalized_row.get("div", db[prn].get("division", "")))
            db[prn]["branch"] = normalized_row.get("branch", normalized_row.get("dept", db[prn].get("branch", "")))
            count += 1
            
        save_students_db(db)
        return jsonify({"success": True, "message": f"Imported/Updated {count} students."})
    except Exception as e:
        return jsonify({"success": False, "message": f"Error parsing CSV: {str(e)}"}), 400

@app.route("/api/students/csv_template", methods=["GET"])
def download_csv_template():
    import io
    from flask import Response
    output = io.StringIO()
    output.write("PRN,Roll No,Name,Email,Phone,Academic Year,Semester,Division,Branch\n")
    output.write("2425000386,386,Omkar Subhash Bhogulkar,omkar@example.com,9876543210,2026-27,SEM-I,A,Computer Science\n")
    output.write("25260001125,1125,Prajwal Vinayak Jadhav,prajwal@example.com,9988776655,2026-27,SEM-I,B,Information Technology\n")
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=students_template.csv"}
    )

@app.route("/api/students/<prn>", methods=["DELETE"])
def delete_student(prn):
    db = load_students_db()
    if prn in db:
        del db[prn]
        save_students_db(db)
        return jsonify({"success": True})
    return jsonify({"success": False, "message": "Student not found"}), 404


# ---------------------------------------------------------
# Face enrollment API (multi-angle)
# ---------------------------------------------------------
@app.route("/api/students/<prn>/reset_enrollment", methods=["POST"])
def reset_student_enrollment(prn):
    db = load_students_db()
    if prn not in db:
        return jsonify({"success": False, "message": "Student not found"}), 404
    
    db[prn]["encodings"] = {}
    db[prn]["photos"] = {}
    db[prn]["photo"] = ""
    db[prn]["face_setup_complete"] = False
    save_students_db(db)
    
    return jsonify({"success": True})

@app.route("/api/students/<prn>/capture", methods=["POST"])
def capture_face_angle(prn):
    db = load_students_db()
    if prn not in db:
        return jsonify({"success": False, "message": "Student not found"}), 404

    data = request.json
    angle = data.get("angle")
    image_data = data.get("image")

    if angle not in ANGLES:
        return jsonify({"success": False, "message": "Invalid angle"}), 400

    try:
        rgb_frame = decode_base64_image(image_data)
    except Exception:
        return jsonify({"success": False, "message": "Could not decode image"}), 400

    # Skip DeepFace liveness on enrollment — it is too slow (mtcnn/retinaface)
    # and webcam enrollment is already a live capture. Scan still uses liveness.

    bgr = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR)
    detect_fn = getattr(engine(), "detect_and_embed_closeup", engine().detect_and_embed)
    faces = detect_fn(bgr)
    if len(faces) != 1:
        return jsonify({
            "success": False,
            "message": "Make sure exactly one face is clearly visible, then try again."
        })

    current_encoding = faces[0].embedding.tolist()

    # Check this face isn't already enrolled under a DIFFERENT student
    known = []
    known_meta = []
    for other_prn, other_student in db.items():
        if other_prn == prn:
            continue
        for other_angle, other_enc in other_student.get("encodings", {}).items():
            known.append(np.array(other_enc, dtype=np.float32))
            known_meta.append(other_student)

    if known:
        idx, score = engine().best_match(faces[0].embedding, known)
        if idx is not None:
            other = known_meta[idx]
            return jsonify({
                "success": False,
                "duplicate_face": True,
                "message": (f"This face is already enrolled as {other['name']} "
                            f"(Roll No {other['roll_no']}).")
            })

    # Crop and optimize face photo for MongoDB cloud storage
    h, w, _ = bgr.shape
    x1, y1, x2, y2 = [int(v) for v in faces[0].bbox]
    pad_x = int((x2 - x1) * 0.25)
    pad_y = int((y2 - y1) * 0.25)
    cx1 = max(0, x1 - pad_x)
    cy1 = max(0, y1 - pad_y)
    cx2 = min(w, x2 + pad_x)
    cy2 = min(h, y2 + pad_y)
    face_crop = bgr[cy1:cy2, cx1:cx2]
    thumb_b64 = ""
    if face_crop.size > 0:
        face_thumb = cv2.resize(face_crop, (160, 160), interpolation=cv2.INTER_AREA)
        _, buf = cv2.imencode(".jpg", face_thumb, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        thumb_b64 = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("utf-8")

    if "photos" not in db[prn] or not isinstance(db[prn]["photos"], dict):
        db[prn]["photos"] = {}
    if thumb_b64:
        db[prn]["photos"][angle] = thumb_b64
        if angle == "center" or not db[prn].get("photo"):
            db[prn]["photo"] = thumb_b64

    db[prn]["encodings"][angle] = current_encoding
    db[prn]["embedding_model"] = engine().model_name
    save_students_db(db)

    angles_done = list(db[prn]["encodings"].keys())
    remaining = [a for a in ANGLES if a not in angles_done]

    return jsonify({
        "success": True,
        "message": f"Captured '{angle}' angle successfully.",
        "angles_done": angles_done,
        "photo": db[prn].get("photo", ""),
        "all_done": len(remaining) == 0,
        "next_angle": remaining[0] if remaining else None,
        "next_instruction": ANGLE_LABELS.get(remaining[0]) if remaining else None,
    })


@app.route("/api/students/<prn>/upload_photo", methods=["POST"])
def upload_student_photo(prn):
    """Directly enroll face from an uploaded photo file (JPG/PNG) into MongoDB."""
    db = load_students_db()
    if prn not in db:
        return jsonify({"success": False, "message": "Student not found"}), 404

    data = request.json or {}
    image_data = data.get("image")
    if not image_data:
        return jsonify({"success": False, "message": "No photo data provided"}), 400

    try:
        rgb_frame = decode_base64_image(image_data)
    except Exception:
        return jsonify({"success": False, "message": "Could not decode image"}), 400

    bgr = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR)
    detect_fn = getattr(engine(), "detect_and_embed_closeup", engine().detect_and_embed)
    faces = detect_fn(bgr)
    if len(faces) != 1:
        return jsonify({
            "success": False,
            "message": f"Found {len(faces)} face(s). Please upload a clear photo with exactly 1 face."
        }), 400

    current_encoding = faces[0].embedding.tolist()

    # Check duplicate face
    known, known_meta = [], []
    for other_prn, other_student in db.items():
        if other_prn == prn:
            continue
        for other_angle, other_enc in other_student.get("encodings", {}).items():
            known.append(np.array(other_enc, dtype=np.float32))
            known_meta.append(other_student)

    if known:
        idx, score = engine().best_match(faces[0].embedding, known)
        if idx is not None:
            other = known_meta[idx]
            return jsonify({
                "success": False,
                "message": f"This face is already enrolled as {other['name']} (Roll No {other['roll_no']})."
            }), 400

    # Crop and optimize face photo for MongoDB storage
    h, w, _ = bgr.shape
    x1, y1, x2, y2 = [int(v) for v in faces[0].bbox]
    pad_x = int((x2 - x1) * 0.25)
    pad_y = int((y2 - y1) * 0.25)
    cx1 = max(0, x1 - pad_x)
    cy1 = max(0, y1 - pad_y)
    cx2 = min(w, x2 + pad_x)
    cy2 = min(h, y2 + pad_y)
    face_crop = bgr[cy1:cy2, cx1:cx2]
    if face_crop.size > 0:
        face_thumb = cv2.resize(face_crop, (160, 160), interpolation=cv2.INTER_AREA)
        _, buf = cv2.imencode(".jpg", face_thumb, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        thumb_b64 = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("utf-8")
    else:
        thumb_b64 = ""

    if "photos" not in db[prn] or not isinstance(db[prn]["photos"], dict):
        db[prn]["photos"] = {}
    if thumb_b64:
        db[prn]["photos"]["center"] = thumb_b64
        db[prn]["photo"] = thumb_b64

    db[prn]["encodings"]["center"] = current_encoding
    db[prn]["embedding_model"] = engine().model_name
    save_students_db(db)

    return jsonify({
        "success": True,
        "message": f"Face photo and biometrics saved to MongoDB for {db[prn]['name']}.",
        "photo": thumb_b64,
        "face_setup_complete": True,
    })


@app.route("/api/angles", methods=["GET"])
def get_angles():
    """Returns the ordered list of angles + instructions, for the frontend to drive the flow."""
    return jsonify([{"angle": a, "instruction": ANGLE_LABELS[a]} for a in ANGLES])


# ---------------------------------------------------------
# ML-based anti-spoofing check (pretrained MiniFASNet via DeepFace)
# ---------------------------------------------------------
ANTISPOOF_THRESHOLD = 0.5   # DeepFace's internal default cutoff for is_real


def check_liveness(rgb_frame):
    """
    Runs the frame through DeepFace's pretrained anti-spoofing model.
    Returns (is_real: bool, score: float or None, message: str or None on failure).
    """
    if not DEEPFACE_AVAILABLE:
        return True, 1.0, None  # skip anti-spoof if DeepFace missing

    bgr_frame = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR)

    # Try a few detector backends in order — different environments can have
    # different quirks (e.g. some opencv-python versions ship a broken/missing
    # Haar cascade path). Falling through a short list makes this robust
    # across machines instead of depending on exactly one backend working.
    backends_to_try = ["mtcnn", "retinaface", "opencv"]
    last_error = None

    for backend in backends_to_try:
        try:
            faces = DeepFace.extract_faces(
                img_path=bgr_frame,
                detector_backend=backend,
                anti_spoofing=True,
                enforce_detection=False,
            )
            if faces:
                face_info = faces[0]
                is_real = face_info.get("is_real", None)
                score = face_info.get("antispoof_score", None)
                return is_real, score, None
            else:
                last_error = "No face detected for liveness check."
        except Exception as e:
            last_error = f"Liveness check error ({backend}): {str(e)}"
            continue

    return None, None, last_error


# ---------------------------------------------------------
# Attendance scanning API
# ---------------------------------------------------------
@app.route("/api/attendance/scan", methods=["POST"])
def scan_attendance():
    data = request.json or {}
    image_data = data.get("image")
    session_name = (data.get("session_name") or "").strip()
    if not session_name:
        return jsonify({
            "recognized": False,
            "message": "Enter a session name (subject) before scanning — e.g. DBMS Lecture."
        }), 400

    try:
        rgb_frame = decode_base64_image(image_data)
    except Exception:
        return jsonify({"recognized": False, "message": "Could not decode image"}), 400

    # Step 1: ML-based liveness / anti-spoofing check
    is_real, spoof_score, error_msg = check_liveness(rgb_frame)

    if error_msg:
        return jsonify({"recognized": False, "message": error_msg})

    if is_real is False:
        return jsonify({
            "recognized": False,
            "spoof_detected": True,
            "spoof_score": spoof_score,
            "message": "Spoof detected — please use your live face, not a photo or video."
        })

    # Step 2: Passed liveness -> ArcFace / dlib identity matching
    bgr = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR)
    faces = engine().detect_and_embed(bgr)
    if not faces:
        return jsonify({"recognized": False, "message": "No face detected. Please face the camera."})

    # Use the largest face if multiple appear
    faces.sort(key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]), reverse=True)
    query = faces[0].embedding
    branches = data.get("branches", [])
    divisions = data.get("divisions", [])
    semesters = data.get("semesters", [])
    classes = data.get("classes", [])

    db = load_students_db()
    known, meta = [], []
    for prn, s in db.items():
        if not student_matches_selection(s, classes=classes, branches=branches, divisions=divisions, semesters=semesters):
            continue

        for _angle, enc in s.get("encodings", {}).items():
            known.append(np.array(enc, dtype=np.float32))
            meta.append(s)

    if not known:
        return jsonify({"recognized": False, "message": "No students found in the selected classes."})

    idx, score = engine().best_match(query, known)
    if idx is not None:
        student = meta[idx]

        # Crop face thumbnail for attendance proof in MongoDB
        h, w, _ = bgr.shape
        x1, y1, x2, y2 = [int(v) for v in faces[0].bbox]
        cx1 = max(0, x1 - 15)
        cy1 = max(0, y1 - 15)
        cx2 = min(w, x2 + 15)
        cy2 = min(h, y2 + 15)
        snap_crop = bgr[cy1:cy2, cx1:cx2]
        snap_b64 = ""
        if snap_crop.size > 0:
            snap_thumb = cv2.resize(snap_crop, (130, 130), interpolation=cv2.INTER_AREA)
            _, buf = cv2.imencode(".jpg", snap_thumb, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            snap_b64 = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("utf-8")

        marked = mark_attendance(student, session_name=session_name, snapshot_b64=snap_b64)
        return jsonify({
            "recognized": True,
            "name": student["name"],
            "roll_no": student["roll_no"],
            "prn": student["prn"],
            "photo": student.get("photo", "") or snap_b64,
            "session": session_name,
            "score": round(float(score), 3),
            "already_marked": not marked,
            "message": (
                f"Marked present in \"{session_name}\": {student['name']} (Roll No {student['roll_no']})"
                if marked else
                f"{student['name']} already marked in \"{session_name}\" today"
            )
        })
    else:
        return jsonify({"recognized": False, "message": "Face not recognized."})


@app.route("/api/attendance/today", methods=["GET"])
def attendance_today():
    """Attendance records. Optional query: session, date (default today). Use date=all for every day."""
    db = get_db()
    today = datetime.now().strftime("%Y-%m-%d")
    session_filter = request.args.get("session")
    date_filter = request.args.get("date", today)

    query = {}
    if date_filter != "all":
        query["date"] = date_filter
    if session_filter:
        query["session"] = session_filter

    records = []
    for doc in db.attendance.find(query).sort([("date", -1), ("time", -1)]):
        records.append({
            "session": doc.get("session"),
            "prn": doc.get("prn"),
            "roll_no": doc.get("roll_no"),
            "name": doc.get("name"),
            "division": doc.get("division"),
            "branch": doc.get("branch"),
            "date": doc.get("date"),
            "time": doc.get("time"),
            "snapshot": doc.get("snapshot", "") or "",
            "status": doc.get("status", "present"),
        })
    return jsonify(records)


@app.route("/api/attendance/sessions", methods=["GET"])
def attendance_sessions():
    """List sessions with present counts (today by default, ?all=1 for all)."""
    db = get_db()
    today = datetime.now().strftime("%Y-%m-%d")
    all_days = request.args.get("all") == "1"

    query = {} if all_days else {"date": today}
    buckets = {}  # (session, date) -> {count, times}
    for doc in db.attendance.find(query):
        sess, date, time_s = doc.get("session"), doc.get("date"), doc.get("time")
        key = (sess, date)
        if key not in buckets:
            buckets[key] = {"session": sess, "date": date, "count": 0, "first_time": time_s, "last_time": time_s}
        
        # Only increment count if present
        if doc.get("status", "present") == "present":
            buckets[key]["count"] += 1
            
        if time_s > buckets[key]["last_time"]:
            buckets[key]["last_time"] = time_s
        if time_s < buckets[key]["first_time"]:
            buckets[key]["first_time"] = time_s
    session_list = list(buckets.values())
    session_list.sort(key=lambda x: (x["date"], x["last_time"]), reverse=True)
    return jsonify(session_list)



# ---------------------------------------------------------
# Classroom registry (classroom number → CCTV RTSP URL)
# ---------------------------------------------------------
import uuid


def load_classrooms():
    db = get_db()
    rows = []
    for doc in db.classrooms.find({}):
        cname = doc.get("classroom") or doc.get("name") or ""
        rows.append({
            "id": doc.get("id"),
            "classroom": cname,
            "name": cname,
            "location": doc.get("location") or "",
            "rtsp_url": doc.get("rtsp_url") or "",
        })
    return rows


def save_classrooms(rows):
    """Full-replace sync, same pattern as save_students_db - simple and safe
    at the scale of a handful of classrooms."""
    db = get_db()
    db.classrooms.delete_many({})
    if rows:
        docs = []
        for r in rows:
            doc = dict(r)
            doc["_id"] = doc.get("id") or str(uuid.uuid4())[:8]
            docs.append(doc)
        db.classrooms.insert_many(docs)


def find_classroom(classroom_number: str):
    key = (classroom_number or "").strip().lower()
    for c in load_classrooms():
        if (c.get("classroom") or "").strip().lower() == key:
            return c
        if (c.get("id") or "") == classroom_number:
            return c
    return None


@app.route("/api/classrooms", methods=["GET"])
def get_classrooms():
    rows = load_classrooms()
    live = sessions.status()
    active_by_url = {}
    for s in live.get("sessions", []):
        if s.get("running"):
            # match later by classroom field
            active_by_url[s.get("classroom") or ""] = s
    for c in rows:
        st = active_by_url.get(c.get("classroom") or "")
        c["active"] = bool(st)
        c["active_session"] = st.get("session_name") if st else None
        c["session_id"] = st.get("session_id") if st else None
    return jsonify(rows)


@app.route("/api/classrooms", methods=["POST"])
def add_classroom():
    data = request.json or {}
    classroom = (data.get("classroom") or data.get("name") or "").strip()
    rtsp_url = (data.get("rtsp_url") or "").strip()
    location = (data.get("location") or "").strip()
    if not classroom or not rtsp_url:
        return jsonify({"success": False, "message": "Classroom number/name and RTSP URL are required"}), 400
    rows = load_classrooms()
    for c in rows:
        if (c.get("classroom") or "").strip().lower() == classroom.lower():
            return jsonify({"success": False, "message": f"Classroom \"{classroom}\" already exists"}), 400
    row = {
        "id": str(uuid.uuid4())[:8],
        "classroom": classroom,
        "name": classroom,
        "location": location or classroom,
        "rtsp_url": rtsp_url,
    }
    rows.append(row)
    save_classrooms(rows)
    return jsonify({"success": True, "classroom": row, "message": f"Classroom \"{classroom}\" saved."})


@app.route("/api/classrooms/<cid>", methods=["DELETE"])
def delete_classroom(cid):
    rows = load_classrooms()
    new_rows = [c for c in rows if c.get("id") != cid and c.get("classroom") != cid]
    if len(new_rows) == len(rows):
        return jsonify({"success": False, "message": "Classroom not found"}), 404
    save_classrooms(new_rows)
    return jsonify({"success": True, "message": "Classroom removed."})


# Back-compat aliases for older frontend "cameras" API
@app.route("/api/cameras", methods=["GET"])
def get_cameras_alias():
    return get_classrooms()


@app.route("/api/cameras/<cid>", methods=["DELETE"])
def delete_cameras_alias(cid):
    return delete_classroom(cid)


@app.route("/api/cameras", methods=["POST"])
def add_camera_alias():
    data = request.json or {}
    if "name" in data and "classroom" not in data:
        data = dict(data)
        data["classroom"] = data.get("name")
    request.json = data  # may not work — handle inline
    classroom = (data.get("classroom") or data.get("name") or "").strip()
    rtsp_url = (data.get("rtsp_url") or "").strip()
    location = (data.get("location") or "").strip()
    if not classroom or not rtsp_url:
        return jsonify({"success": False, "message": "Classroom number/name and RTSP URL are required"}), 400
    rows = load_classrooms()
    for c in rows:
        if (c.get("classroom") or "").strip().lower() == classroom.lower():
            return jsonify({"success": False, "message": f"Classroom \"{classroom}\" already exists"}), 400
    row = {
        "id": str(uuid.uuid4())[:8],
        "classroom": classroom,
        "location": location or classroom,
        "rtsp_url": rtsp_url,
    }
    rows.append(row)
    save_classrooms(rows)
    return jsonify({"success": True, "camera": row, "classroom": row, "message": f"Classroom \"{classroom}\" saved."})


@app.route("/api/cameras/<cam_id>", methods=["DELETE"])
def delete_camera_alias(cam_id):
    return delete_classroom(cam_id)


# ---------------------------------------------------------
# Multi-session CCTV (concurrent classrooms / teachers)
# ---------------------------------------------------------
@app.route("/api/cctv/start", methods=["POST"])
def cctv_start():
    data = request.json or {}
    duration = data.get("duration_minutes", 40)
    session_name = (data.get("session_name") or "").strip()
    teacher = (data.get("teacher") or "").strip()
    classroom = (data.get("classroom") or data.get("camera_name") or "").strip()
    rtsp_url = (data.get("rtsp_url") or "").strip()
    camera_name = (data.get("camera_name") or "").strip()
    division = (data.get("division") or "").strip()

    # Resolve classroom → RTSP automatically
    if classroom and not rtsp_url:
        found = find_classroom(classroom)
        if not found:
            return jsonify({
                "success": False,
                "message": f"Classroom \"{classroom}\" not found. Add it under Cameras/Classrooms first."
            }), 400
        rtsp_url = found["rtsp_url"]
        classroom = found["classroom"]
        if not camera_name:
            camera_name = found["classroom"]

    cam_id = data.get("camera_id")
    if cam_id and not rtsp_url:
        for c in load_classrooms():
            if c.get("id") == cam_id:
                rtsp_url = c.get("rtsp_url")
                classroom = classroom or c.get("classroom")
                camera_name = camera_name or c.get("classroom")
                break

    if not rtsp_url:
        return jsonify({"success": False, "message": "Select a classroom or provide RTSP URL"}), 400
    if not session_name:
        return jsonify({
            "success": False,
            "message": "Session name (subject) is required — e.g. DBMS Lecture, Python Lab."
        }), 400

    branches = data.get("branches", [])
    divisions = data.get("divisions", [])
    semesters = data.get("semesters", [])
    classes = data.get("classes", [])

    sid, message = sessions.start(
        rtsp_url,
        duration,
        session_name=session_name,
        camera_name=camera_name or classroom,
        classroom=classroom,
        teacher=teacher,
        branches=branches,
        divisions=divisions,
        semesters=semesters,
        classes=classes
    )
    if not sid:
        return jsonify({"success": False, "message": message})
        
    # Mark default absent if criteria provided
    if classes or branches or divisions or semesters:
        db = get_db()
        today = datetime.now().strftime("%Y-%m-%d")
        
        query = build_student_selection_query(classes=classes, branches=branches, divisions=divisions, semesters=semesters)
        students = list(db.students.find(query))
        inserted = 0
        for s in students:
            existing = db.attendance.find_one({"prn": str(s["prn"]), "session": session_name, "date": today})
            if not existing:
                db.attendance.insert_one({
                    "session": session_name,
                    "prn": str(s["prn"]),
                    "roll_no": s.get("roll_no", ""),
                    "name": s.get("name", ""),
                    "division": s.get("division", ""),
                    "branch": s.get("branch", ""),
                    "date": today,
                    "time": "--:--",
                    "snapshot": "",
                    "status": "absent"
                })
                inserted += 1
        message += f" Marked {inserted} students absent initially."

    return jsonify({
        "success": True,
        "message": message,
        "session_id": sid,
        "classroom": classroom,
        "session_name": session_name,
    })

@app.route("/api/cctv/stop", methods=["POST"])
def cctv_stop():
    data = request.json or {}
    session_id = data.get("session_id")
    if not session_id:
        # stop nothing without id — list running
        return jsonify({
            "success": False,
            "message": "session_id is required to stop. Use GET /api/cctv/sessions to list active sessions."
        }), 400
    success, message = sessions.stop(session_id)
    return jsonify({"success": success, "message": message})


@app.route("/api/cctv/sessions", methods=["GET"])
def cctv_sessions_list():
    return jsonify(sessions.status())


@app.route("/api/cctv/status", methods=["GET"])
def cctv_status():
    session_id = request.args.get("session_id")
    if session_id:
        return jsonify(sessions.status(session_id))
    # summary of all
    return jsonify(sessions.status())


@app.route("/api/cctv/preview")
def cctv_preview():
    """Latest annotated frame for a session (?session_id=...)."""
    from flask import Response
    session_id = request.args.get("session_id")
    jpeg = sessions.get_preview_jpeg(session_id) if session_id else None
    if not jpeg:
        import numpy as np
        blank = np.zeros((240, 320, 3), dtype=np.uint8)
        cv2.putText(blank, "Select / start a session", (20, 120),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)
        ok, buf = cv2.imencode(".jpg", blank)
        jpeg = buf.tobytes() if ok else b""
    return Response(jpeg, mimetype="image/jpeg",
                    headers={"Cache-Control": "no-store, no-cache, must-revalidate"})




# ---------------------------------------------------------
# Admin: Attendance Requests API
# ---------------------------------------------------------
@app.route("/api/attendance/requests", methods=["GET"])
def get_attendance_requests():
    db = get_db()
    reqs = list(db.attendance_requests.find({}).sort("created_at", -1))
    for r in reqs:
        r["_id"] = str(r["_id"])
    return jsonify(reqs)


@app.route("/api/attendance/requests/<req_id>/review", methods=["POST"])
def review_attendance_request(req_id):
    from bson.objectid import ObjectId
    db = get_db()
    data = request.json
    status = data.get("status")
    if status not in ["approved", "rejected"]:
        return jsonify({"success": False, "message": "Invalid status"}), 400

    req = db.attendance_requests.find_one({"_id": ObjectId(req_id)})
    if not req:
        return jsonify({"success": False, "message": "Request not found"}), 404

    db.attendance_requests.update_one(
        {"_id": ObjectId(req_id)},
        {"$set": {
            "status": status,
            "reviewed_at": datetime.now().isoformat(),
            "reviewed_by": "admin"
        }}
    )

    # Auto mark attendance if approved
    if status == "approved":
        student = db.students.find_one({"prn": req["prn"]})
        if student:
            mark_attendance(
                student,
                session_name=req["session"],
                snapshot_b64=""
            )

    return jsonify({"success": True, "message": f"Request {status}"})

@app.route("/api/attendance/requests/<req_id>/verify", methods=["POST"])
def verify_attendance_request(req_id):
    from bson.objectid import ObjectId
    db = get_db()
    data = request.json or {}
    b64_image = data.get("image", "")

    req = db.attendance_requests.find_one({"_id": ObjectId(req_id)})
    if not req:
        return jsonify({"success": False, "message": "Request not found"}), 404

    student = db.students.find_one({"prn": str(req["prn"])})
    if not student:
        return jsonify({"success": False, "message": "Student record not found"}), 404

    # If image is provided, verify against student's enrolled face
    if b64_image:
        try:
            rgb_frame = decode_base64_image(b64_image if b64_image.startswith("data:") else f"data:image/jpeg;base64,{b64_image}")
            bgr = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR)
            faces = engine().detect_and_embed(bgr)
            if not faces:
                return jsonify({"success": False, "message": "No face detected in camera frame. Please look directly at the camera."}), 400

            faces.sort(key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]), reverse=True)
            query = faces[0].embedding

            known = []
            for angle, enc in student.get("encodings", {}).items():
                known.append(np.array(enc, dtype=np.float32))

            if not known:
                return jsonify({"success": False, "message": "Student has no registered face encodings to match against."}), 400

            idx, score = engine().best_match(query, known)
            if idx is None:
                return jsonify({"success": False, "message": f"Face does not match {student.get('name')}. Verification failed."}), 400

        except Exception as e:
            return jsonify({"success": False, "message": f"Face processing error: {str(e)}"}), 400

    # Approved (either verified by face or approved manually by admin)
    db.attendance_requests.update_one(
        {"_id": ObjectId(req_id)},
        {"$set": {
            "status": "approved",
            "reviewed_at": datetime.now().isoformat(),
            "reviewed_by": "admin_face_verify" if b64_image else "admin_manual"
        }}
    )

    mark_attendance(
        student,
        session_name=req.get("session", "Lecture"),
        snapshot_b64=b64_image if b64_image else student.get("photo", ""),
        date_str=req.get("date")
    )

    return jsonify({"success": True, "message": f"Request approved and attendance marked present for {student.get('name')}!"})


# ---------------------------------------------------------
# Student Portal APIs (Require Student Auth)
# ---------------------------------------------------------
@app.route("/api/student/link", methods=["POST"])
@require_student_auth
def student_link():
    # Middleware already links and populates g.student
    return jsonify({
        "success": True,
        "message": "Account linked successfully",
        "student": {
            "prn": g.student["prn"],
            "name": g.student["name"],
        }
    })


@app.route("/api/student/profile", methods=["GET"])
@require_student_auth
def get_student_profile():
    s = g.student
    return jsonify({
        "prn": s["prn"],
        "roll_no": s["roll_no"],
        "name": s["name"],
        "email": s.get("email", ""),
        "phone": s.get("phone", ""),
        "division": s.get("division", ""),
        "branch": s.get("branch", ""),
        "semester": s.get("semester", ""),
        "academic_year": s.get("academic_year", ""),
        "photos": s.get("photos", {}),
        "photo": s.get("photo", "") or s.get("photos", {}).get("center", ""),
    })


@app.route("/api/student/attendance", methods=["GET"])
@require_student_auth
def get_student_attendance():
    db = get_db()
    prn = g.student["prn"]
    query = {"prn": prn}
    
    date_filter = request.args.get("date")
    if date_filter:
        query["date"] = date_filter
        
    records = list(db.attendance.find(query).sort([("date", -1), ("time", -1)]))
    for r in records:
        r["_id"] = str(r["_id"])
    return jsonify(records)


@app.route("/api/student/attendance/summary", methods=["GET"])
@require_student_auth
def get_student_attendance_summary():
    db = get_db()
    prn = g.student["prn"]
    
    records = list(db.attendance.find({"prn": prn}))
    
    total_attended = sum(1 for r in records if r.get("status", "present") == "present")
    total_absent = sum(1 for r in records if r.get("status") == "absent")
    
    subjects = {}
    for r in records:
        sess = r.get("session")
        if sess not in subjects:
            subjects[sess] = {"present": 0, "total": 0}
        subjects[sess]["total"] += 1
        if r.get("status", "present") == "present":
            subjects[sess]["present"] += 1
        
    return jsonify({
        "total_attended": total_attended,
        "total_absent": total_absent,
        "subject_breakdown": subjects
    })


@app.route("/api/admin/session/start", methods=["POST"])
def admin_start_session():
    """
    Called when admin starts a session for a specific division.
    Marks all students in that division as 'absent' for this session/date.
    """
    db = get_db()
    data = request.json
    session_name = (data.get("session") or "").strip()
    branches = data.get("branches", [])
    divisions = data.get("divisions", [])
    semesters = data.get("semesters", [])
    classes = data.get("classes", [])
    
    if not session_name:
        return jsonify({"success": False, "message": "Session name is required"}), 400
        
    today = datetime.now().strftime("%Y-%m-%d")
    
    # 1. Get all students matching criteria
    query = build_student_selection_query(classes=classes, branches=branches, divisions=divisions, semesters=semesters)
    students = list(db.students.find(query))
    if not students:
        return jsonify({"success": False, "message": "No students found matching those criteria"}), 404
        
    # 2. Mark them absent if no record exists for today/session
    inserted = 0
    for s in students:
        existing = db.attendance.find_one({
            "prn": str(s["prn"]),
            "session": session_name,
            "date": today
        })
        if not existing:
            db.attendance.insert_one({
                "session": session_name,
                "prn": str(s["prn"]),
                "roll_no": s.get("roll_no", ""),
                "name": s.get("name", ""),
                "division": s.get("division", ""),
                "branch": s.get("branch", ""),
                "date": today,
                "time": "--:--",
                "snapshot": "",
                "status": "absent"
            })
            inserted += 1
            
    return jsonify({
        "success": True, 
        "message": f"Session started. Marked {inserted} students absent initially."
    })



@app.route("/api/student/attendance/request", methods=["POST"])
@require_student_auth
def create_attendance_request():
    db = get_db()
    prn = g.student["prn"]
    data = request.json
    
    session_name = (data.get("session") or "").strip()
    date = (data.get("date") or "").strip()
    reason = (data.get("reason") or "").strip()
    
    if not session_name or not date or not reason:
        return jsonify({"success": False, "message": "Missing fields"}), 400
        
    # Check if already requested or present
    existing_req = db.attendance_requests.find_one({"prn": prn, "session": session_name, "date": date})
    if existing_req:
        return jsonify({"success": False, "message": "Request already submitted for this session/date"}), 400
        
    existing_att = db.attendance.find_one({"prn": prn, "session": session_name, "date": date})
    if existing_att and existing_att.get("status", "present") == "present":
        return jsonify({"success": False, "message": "You are already marked present for this session/date"}), 400
        
    db.attendance_requests.insert_one({
        "prn": prn,
        "student_name": g.student["name"],
        "session": session_name,
        "date": date,
        "reason": reason,
        "status": "pending",
        "created_at": datetime.now().isoformat()
    })
    
    return jsonify({"success": True, "message": "Attendance request submitted"})


@app.route("/api/student/attendance/requests", methods=["GET"])
@require_student_auth
def get_my_attendance_requests():
    db = get_db()
    prn = g.student["prn"]
    reqs = list(db.attendance_requests.find({"prn": prn}).sort("created_at", -1))
    for r in reqs:
        r["_id"] = str(r["_id"])
    return jsonify(reqs)


@app.route("/api/firebase/config", methods=["GET"])
def firebase_config():
    return jsonify(get_firebase_config())




def print_startup_diagnostics():
    """Compact essential startup log: packages + GPU status."""
    import sys
    import platform

    def ver(name):
        try:
            m = __import__(name)
            v = getattr(m, "__version__", None)
            if v is None and name == "cv2":
                v = getattr(getattr(m, "version", None), "VERSION", "?")
            return str(v) if v else "ok"
        except Exception:
            return "MISSING"

    print("\n" + "=" * 60)
    print("  CAMPUSVISION — SMART ATTENDANCE")
    print("=" * 60)
    print(f"  Python     : {sys.version.split()[0]}")
    print(f"  Executable : {sys.executable}")
    print(f"  Platform   : {platform.system()} {platform.release()}")
    print("-" * 60)
    print("  DATABASE (MongoDB)")
    try:
        check_connection()
        print("  >>> MongoDB: CONNECTED <<<")
    except Exception as e:
        print("  >>> MongoDB: CONNECTION FAILED <<<")
        print(f"  Error      : {e}")
        print("  Fix        : copy .env.example to .env and set MONGODB_URI,")
        print("               then run migrate_to_mongo.py if you have existing local data.")
    print("-" * 60)
    print("  PACKAGES")
    print(f"  Flask          {ver('flask')}")
    print(f"  OpenCV         {ver('cv2')}")
    print(f"  NumPy          {ver('numpy')}")
    print(f"  InsightFace    {ver('insightface')}")
    print(f"  ONNX Runtime   {ver('onnxruntime')}")
    print(f"  DeepFace       {ver('deepface')}")
    try:
        import faiss
        print(f"  FAISS          {getattr(faiss, '__version__', 'ok')} (cpu)")
    except Exception:
        print("  FAISS          MISSING (NumPy match fallback)")
    print("-" * 60)

    # GPU
    gpu_ok = False
    gpu_name = ""
    providers = []
    try:
        import onnxruntime as ort
        providers = ort.get_available_providers()
        gpu_ok = "CUDAExecutionProvider" in providers
    except Exception as e:
        print(f"  ONNX providers error: {e}")

    try:
        import subprocess
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            gpu_name = r.stdout.strip()
    except Exception:
        pass

    print("  GPU / COMPUTE")
    if gpu_ok:
        print("  >>> GPU: CONNECTED (CUDA) <<<")
        if gpu_name:
            print(f"  Device     : {gpu_name}")
        print(f"  Providers  : {providers}")
    else:
        print("  >>> GPU: NOT ACTIVE — using CPU <<<")
        print(f"  Providers  : {providers or ['CPU only']}")
        if gpu_name:
            print(f"  nvidia-smi : {gpu_name} (driver OK, ORT not using CUDA)")
        print("  Tip        : pip install onnxruntime-gpu + CUDA toolkit")

    print("-" * 60)
    # Face engine (loads models once)
    try:
        eng = FaceEngine.get()
        print("  FACE ENGINE")
        print(f"  Backend    : {eng.backend} / {eng.model_name}")
        print(f"  Embed dim  : {eng.embedding_dim()}")
        try:
            from face_engine import DET_SIZE, DET_THRESH
            print(f"  Det size   : {DET_SIZE}  thresh={DET_THRESH}")
        except Exception:
            pass
        try:
            from face_index import FaceIndex
            fi = FaceIndex(dim=eng.embedding_dim())
            print(f"  Match idx  : {'faiss' if fi.info()['faiss_available'] else 'numpy'}")
        except Exception:
            pass
    except Exception as e:
        print(f"  FACE ENGINE ERROR: {e}")

    print("-" * 60)
    print("  DATA")
    try:
        sdb = load_students_db()
        n = len(sdb)
        enc = sum(len(s.get("encodings", {})) for s in sdb.values())
        print(f"  Students   : {n}  encodings={enc}")
    except Exception:
        print("  Students   : (could not query MongoDB)")
    try:
        att_count = get_db().attendance.count_documents({})
        print(f"  Attendance : {att_count} record(s)")
    except Exception:
        print("  Attendance : (could not query MongoDB)")
    try:
        room_count = get_db().classrooms.count_documents({})
        print(f"  Classrooms : {room_count}")
    except Exception:
        print("  Classrooms : (could not query MongoDB)")
    print("=" * 60)
    print("  UI  http://127.0.0.1:5000")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    print_startup_diagnostics()
    app.run(debug=True, use_reloader=False, host="0.0.0.0", port=5000)
