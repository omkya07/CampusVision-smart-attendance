# CampusVision Smart Attendance

An AI-powered, multi-platform smart attendance management system that uses YOLOv8, InsightFace, and DeepFace for flawless classroom facial recognition.

## 🚀 Quick Start Guide

### 1. Prerequisites
- **Python 3.8+** installed on your machine.
- **Node.js** (optional, strictly for running `npx localtunnel`).
- **MongoDB** (Atlas cloud URI or a local MongoDB instance).

### 2. Backend Installation
First, open your terminal and navigate to the project directory to set up the backend:
```bash
# Navigate to the backend folder
cd backend

# Create a virtual environment (Recommended)
python -m venv .venv

# Activate the virtual environment
# On Windows:
.venv\Scripts\activate
# On Mac/Linux:
source .venv/bin/activate

# Install all the required AI models and Python packages
pip install -r requirements.txt
```

### 3. Environment Variables
Create a `.env` file inside the `backend/` directory and add your MongoDB connection string so the database connects successfully:
```ini
# backend/.env
MONGO_URI="mongodb+srv://<username>:<password>@cluster.mongodb.net/attendance_db"
```

### 4. Running the Application
You need to run the backend server to process the AI calculations, and open the frontend files to view the UI.

**Start the Backend Server:**
```bash
cd backend
# Make sure your virtual environment is still activated
python app.py
```
*(The server will start on `http://127.0.0.1:5000`)*

**Launch the Frontend:**
Because the frontend is built in lightning-fast Vanilla JS, there is no build step. You can simply double-click the files to open them in your browser, or use an extension like VS Code Live Server:
- 👨‍🏫 **Admin Portal:** Open `frontend/index.html`
- 🎓 **Student Portal:** Open `frontend/student.html`

### 5. Mobile Testing (Localtunnel)
To test the Student Portal on an actual mobile phone (to scan QR codes and use the mobile camera for Face Enrollment), you need to expose your local backend to the internet.

Open a **new terminal window** (keep the Python server running in the first one) and run:
```bash
npx localtunnel --port 5000 --local-host 127.0.0.1
```
*Copy the generated URL (e.g., `https://some-name.loca.lt`) and ensure your Firebase Auth Authorized Domains include this new URL before testing!*

---

## 📚 Complete Documentation
For a deep-dive into the system's architecture, ML workflows, and why certain technologies (like InsightFace vs DeepFace) were chosen, please read the detailed [PROJECT_DOCUMENTATION.md](./PROJECT_DOCUMENTATION.md) file.
