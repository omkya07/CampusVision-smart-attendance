# CampusVision Smart Attendance - Project Documentation

## Project Overview
CampusVision is an AI-powered, multi-platform smart attendance management system designed for educational institutions. It replaces manual roll-calls with seamless, hardware-agnostic facial recognition and provides both administrators and students with robust dashboards to manage, review, and track attendance.

## Architecture
The system follows a classic decoupled client-server architecture:
- **Frontend (Client):** A completely responsive Web App that serves two distinct, specialized interfaces based on the route (Admin Dashboard vs Mobile-first Student Portal).
- **Backend (Server):** A Python Flask REST API that handles business logic, database transactions, and heavy machine learning inference (Face Recognition & Biometrics).
- **Database:** MongoDB for fast, scalable, document-oriented storage.
- **Auth Provider:** Firebase Authentication for secure, production-grade identity management.

## Technologies Used (Which, Where, Why)

### Backend (Python / Flask)
- **Flask:** Used as the core web framework. 
  - *Why:* It's lightweight, highly customizable, and integrates flawlessly with Python-based Machine Learning libraries without the bloat of larger frameworks like Django.
- **InsightFace (ArcFace):** The core facial recognition engine.
  - *Why:* Provides state-of-the-art accuracy for generating mathematical facial embeddings (512-d) and scales incredibly well to massive crowds in CCTV images.
- **DeepFace:** Used exclusively for Anti-spoofing (Liveness Checks). 
  - *Why:* DeepFace has a built-in pre-trained `MiniFASNet` model that instantly detects if a face shown to a webcam is a real human or a printed photo/screen.
- **Ultralytics (YOLOv8):** Used as the primary person-detector before face-cropping.
  - *Why:* YOLOv8 can detect bodies/people in a large classroom photo instantly, allowing the system to zoom in on heads before passing them to InsightFace for recognition.
- **OpenCV (cv2):** Used for image processing, cropping, and base64 parsing. 
  - *Why:* It is the absolute industry standard for handling, resizing, and modifying image arrays quickly before passing them to ML models.
- **PyMongo:** Used to interface with MongoDB. 
  - *Why:* Provides a seamless, native translation between Python dictionaries and MongoDB JSON documents.

### Frontend (HTML / CSS / Vanilla JavaScript)
- **Vanilla JS & CSS3:** Used to build the entire UI. 
  - *Why:* Avoids the overhead of a heavy framework (like React/Angular) for a project that needs to remain highly performant, easy to deploy locally without build steps, and easy to modify directly.
- **Firebase Auth & FirebaseUI:** Used on both Admin and Student portals. 
  - *Why:* Provides instant Google Sign-In and secure Email/Password authentication out-of-the-box, completely removing the security risks of building password-hashing and session management from scratch.
- **Face-api.js:** Used on the frontend during the student face enrollment phase. 
  - *Why:* Runs lightweight pose-detection (left, right, center) directly in the user's browser, providing instant UI feedback without spamming the backend with constant video frames, saving massive bandwidth.

### Database (MongoDB)
- **MongoDB Atlas / Local MongoDB:** 
  - *Why:* The unstructured nature of student profiles (which can have 1 to N face encodings, base64 thumbnail strings, and dynamic arrays of attendance records) maps perfectly to NoSQL document structures. Relational databases would require excessive and complex table joins for this use case.

## Core Workflows

1. **Student Registration & Face Enrollment:**
   - Admin registers a student manually or bulk-imports via CSV.
   - Student logs into the Mobile portal via Google/Firebase.
   - Student enters the Enrollment flow. The browser uses `face-api.js` to intelligently detect their head pose.
   - Once they align perfectly to the center, left, and right angles, the browser automatically captures the frame and POSTs it to the Flask Backend.
   - The Backend uses `InsightFace` to extract a mathematical facial embedding (a matrix of numbers representing the face) and stores it in MongoDB.

2. **Session Creation & Smart Scanning (Admin):**
   - Admin creates a new "Class Session" for a specific branch, subject, and time.
   - Admin launches the "Camera Scanner" (or uploads a classroom CCTV photo).
   - The Backend processes the image, detects all faces, extracts their embeddings, and compares them against the enrolled database using Cosine Similarity.
   - Confident matches are securely recorded as `Present`.

3. **Attendance Verification & Leave Requests (Student):**
   - Students can view their real-time attendance statistics on their mobile-friendly dashboard.
   - If a student was mistakenly marked absent due to occlusion or poor lighting, they can raise an "Attendance Request".
   - Admins immediately see this request in their dashboard, can review the original captured classroom photo to manually verify if the student is visible, and Approve/Reject the request with one click.
