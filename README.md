# CampusVision — Smart Attendance System

> AI-powered face recognition attendance for classrooms — CCTV + webcam, fully cloud-synced via MongoDB Atlas.
> 
> **Team CampusVision · KIT's College of Engineering, Kolhapur**

---

## Features

| Feature | Details |
|---|---|
| 🎯 **Face Recognition** | InsightFace ArcFace (512-d) + SCRFD detector |
| 📷 **CCTV Mode** | Multi-session RTSP streams (multiple classrooms simultaneously) |
| 🔍 **Distant Faces** | Two-stage: YOLOv8 person → head crop → upscale → ArcFace |
| ⚡ **Fast Search** | FAISS IndexFlatIP gallery matching (cosine similarity) |
| 🛡️ **Anti-Spoofing** | DeepFace MiniFASNet liveness check on webcam scan |
| 🌐 **Cloud Database** | MongoDB Atlas — all data accessible from any device |
| 📸 **Face Photos in DB** | Enrolled face photos & attendance snapshots stored in MongoDB |
| 🔄 **Multi-Angle Enroll** | Center / Left / Right pose guidance with auto-capture |
| 📁 **Bulk Import** | Import entire folders of student photos via `import_faces_to_mongo.py` |
| 🖥️ **Dark UI** | Kinetic Intelligence theme — sidebar nav, live session cards |

---

## MongoDB Atlas Collections

All essential data is stored in **MongoDB Atlas (cloud)** — open the project on any device and all data is immediately available.

| Collection | Contents |
|---|---|
| `students` | PRN, name, roll no, division, branch, face biometric embeddings (512-d), enrolled face photos (base64 thumbnails per angle) |
| `attendance` | Session, PRN, name, date/time, face verification snapshot photo |
| `classrooms` | Classroom number, location, RTSP URL, session state |

---

## Setup (Windows)

### 1. MongoDB Atlas (one-time cloud setup)
1. Create a free cluster → [mongodb.com/cloud/atlas/register](https://www.mongodb.com/cloud/atlas/register)
2. **Database Access** → Create a user with username + password
3. **Network Access** → Add IP → Allow from Anywhere (`0.0.0.0/0`) *(fine for college project)*
4. **Connect** → **Drivers** → Copy the connection string
5. Copy `.env.example` to `.env` and paste your connection string:
   ```
   MONGODB_URI=mongodb+srv://user:password@cluster.mongodb.net/?retryWrites=true&w=majority
   MONGODB_DB_NAME=campusvision
   ```

### 2. Python Environment
```powershell
cd CampusVision-smart-attendance
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3. (Optional) Migrate existing local data
*Only needed if upgrading from an old file-based version of this project:*
```powershell
cd backend
python migrate_to_mongo.py
```
Imports `students_database.pkl`, `attendance_log.csv`, and `classrooms.json` into MongoDB. Safe to re-run — no duplicates created.

### 4. Run
```powershell
cd backend
python app.py
```
Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser.

Startup logs show a **DATABASE (MongoDB)** section confirming the connection, with live student / attendance / classroom counts.

### CPU-only (no NVIDIA GPU)
```powershell
python -m pip install -r requirements-cpu.txt
```

### First run note
- Downloads InsightFace `buffalo_l` models automatically (~150 MB, once)
- Downloads `yolov8n.pt` person detector automatically (~6 MB, once)

---

## Enrolling Students

### Option A — Webcam (multi-angle)
1. Go to **Students** tab → Register a student (PRN, name, etc.)
2. Go to **Enroll Face** tab → Select student → Click **Webcam Setup**
3. Follow the animated pose guide: **Center → Left → Right**
4. Each angle auto-captures when your face aligns — or click **Capture** manually
5. Face photo thumbnail + ArcFace biometric (512-d) saved to MongoDB Atlas

### Option B — Upload a photo (from any device)
1. Go to **Enroll Face** tab → Select student → Click **Upload Face Photo**
2. Select any `.jpg` / `.png` from your device (phone, laptop, etc.)
3. Backend detects the face, computes embedding, crops avatar → saved to MongoDB Atlas instantly

### Option C — Bulk import from folder (admin script)
```powershell
cd backend
python import_faces_to_mongo.py "C:\path\to\student_photos"
```
**Filename format:** `PRN_FullName.jpg` or just `PRN.jpg`

Example:
```
22070121_Rahul Sharma.jpg
22070122_Priya Patel.jpg
```
All faces are detected, embeddings computed, and synced to MongoDB Atlas in one shot.

---

## Using the App on Any Device

Because everything is stored in MongoDB Atlas:

1. **Any PC / Server**: Clone repo → add `.env` → `python app.py`
2. **From a phone/tablet on the same network**: Open `http://<your-pc-ip>:5000`
   - Register students, upload face photos from your phone gallery
   - Start CCTV sessions, view live attendance
3. **Switch computers**: All students, biometrics, photos, and attendance history are in the cloud — nothing is tied to a local machine

---

## Project Layout

```
backend/
├── app.py                    Flask API + student, attendance, classroom routes
├── face_engine.py            InsightFace ArcFace + YOLOv8 detector
├── face_index.py             FAISS / NumPy gallery search
├── cctv_session.py           RTSP session worker (one thread per classroom)
├── session_manager.py        Multi-session orchestrator
├── latest_frame_reader.py    Low-latency RTSP frame reader
├── db.py                     MongoDB Atlas connection (thread-safe singleton)
├── migrate_to_mongo.py       One-time import of old local data
├── import_faces_to_mongo.py  Bulk face photo importer (NEW)
└── yolov8n.pt                YOLOv8 nano (auto-downloaded)

frontend/
├── index.html                Single-page app
├── css/style.css             Kinetic Intelligence dark theme
└── js/app.js                 API calls, enrollment flow, live UI updates

.env.example    → copy to .env, fill in MONGODB_URI
requirements.txt
requirements-cpu.txt
Dockerfile
docker-compose.yml
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/students` | List all students (with photo thumbnails) |
| `POST` | `/api/students` | Register new student |
| `DELETE` | `/api/students/<prn>` | Remove student + biometrics |
| `POST` | `/api/students/<prn>/capture` | Enroll face angle (center/left/right) from webcam |
| `POST` | `/api/students/<prn>/upload_photo` | Enroll face from uploaded image file |
| `GET` | `/api/angles` | Get ordered enrollment angles + instructions |
| `POST` | `/api/attendance/scan` | Webcam face scan + anti-spoof + mark attendance |
| `GET` | `/api/attendance/today` | Attendance records (with snapshots) |
| `GET` | `/api/attendance/sessions` | Session list with present counts |
| `GET` | `/api/classrooms` | List classrooms + live session status |
| `POST` | `/api/classrooms` | Add/register a classroom CCTV |
| `DELETE` | `/api/classrooms/<id>` | Remove classroom |
| `POST` | `/api/cctv/start` | Start CCTV attendance session |
| `POST` | `/api/cctv/stop` | Stop CCTV session |
| `GET` | `/api/cctv/status` | Live session stats (frames, faces, marked count) |
| `GET` | `/api/cctv/preview` | Latest annotated JPEG preview frame |
| `GET` | `/api/cctv/sessions` | All running CCTV sessions |

---

## Docker (Optional)

```powershell
docker-compose up --build
```

Opens at [http://localhost:5000](http://localhost:5000). MongoDB Atlas URI must be set in `.env` before building.

---

## Tech Stack

- **Backend**: Python 3.11+, Flask, PyMongo
- **Face AI**: InsightFace (ArcFace buffalo_l), SCRFD, YOLOv8n, DeepFace (anti-spoof)
- **Search**: FAISS (GPU/CPU), NumPy fallback
- **Database**: MongoDB Atlas (cloud, free tier)
- **Frontend**: Vanilla HTML/CSS/JS (no framework), face-api.js (pose detection)

---

## Repo

[github.com/omkya07/CampusVision-smart-attendance](https://github.com/omkya07/CampusVision-smart-attendance)
