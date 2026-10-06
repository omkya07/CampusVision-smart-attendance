const API = "";  // same origin
let ANGLES = [];
let currentAngleIndex = 0;
let currentPrn = null;
let enrollStream = null;
let attendStream = null;
let scanAttempts = 0;
const MAX_ATTEMPTS = 3;

// ---------- Navigation ----------
document.querySelectorAll(".nav-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(btn.dataset.view).classList.add("active");

    if (btn.dataset.view === "students") loadStudents();
    if (btn.dataset.view === "enroll") loadStudentSelect();
    if (btn.dataset.view === "attendance") { loadToday(); startAttendCamera(); }
    if (btn.dataset.view === "cameras") loadCameras();
    if (btn.dataset.view === "cctv") { fillCctvCameraSelect(); }
    if (btn.dataset.view === "sessions") loadSessionsPage();
  });
});

// ---------- Students ----------
async function registerStudent() {
  const payload = {
    prn: document.getElementById("prn").value,
    roll_no: document.getElementById("roll_no").value,
    name: document.getElementById("name").value,
    division: document.getElementById("division").value,
    branch: document.getElementById("branch").value,
  };
  const res = await fetch(`${API}/api/students`, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload)
  });
  const data = await res.json();
  const msg = document.getElementById("registerMsg");
  msg.textContent = data.message;
  msg.className = "status-msg " + (data.success ? "success" : "error");
  if (data.success) {
    ["prn","roll_no","name","division","branch"].forEach(id => document.getElementById(id).value = "");
    loadStudents();
  }
}

function getInitials(name) {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

async function loadStudents() {
  const res = await fetch(`${API}/api/students`);
  const students = await res.json();
  const body = document.getElementById("studentTableBody");
  if (!students.length) {
    body.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:24px; color:var(--muted);">No students registered yet. Add one above.</td></tr>`;
    return;
  }
  body.innerHTML = students.map(s => {
    const avatar = s.photo
      ? `<img src="${s.photo}" class="student-avatar" alt="${s.name}" title="${s.name}">`
      : `<div class="avatar-placeholder" title="No photo">${getInitials(s.name)}</div>`;
    return `
      <tr>
        <td style="width:48px;">${avatar}</td>
        <td>${s.prn}</td><td>${s.roll_no}</td><td><strong>${s.name}</strong></td>
        <td>${s.division}</td><td>${s.branch}</td>
        <td><span class="badge ${s.face_setup_complete ? 'done' : 'pending'}">
          ${s.face_setup_complete ? 'Complete' : s.angles_done.length + '/3 angles'}
        </span></td>
        <td><button class="btn secondary btn-sm" onclick="deleteStudent('${s.prn}')">Remove</button></td>
      </tr>`;
  }).join("");
}

async function deleteStudent(prn) {
  await fetch(`${API}/api/students/${prn}`, { method: "DELETE" });
  loadStudents();
}

// ---------- Enrollment ----------
async function loadStudentSelect() {
  const res = await fetch(`${API}/api/students`);
  const students = await res.json();
  const select = document.getElementById("studentSelect");
  select.innerHTML = students.map(s =>
    `<option value="${s.prn}">${s.name} (Roll No ${s.roll_no}) ${s.face_setup_complete ? '✓' : ''}</option>`
  ).join("");
}

async function handlePhotoUpload(event) {
  const file = event.target.files && event.target.files[0];
  if (!file) return;

  const prn = document.getElementById("studentSelect").value;
  const msg = document.getElementById("photoUploadMsg");
  if (!prn) {
    msg.textContent = "Please select a student from the dropdown first.";
    msg.className = "status-msg error";
    event.target.value = "";
    return;
  }

  msg.textContent = "Detecting face and computing biometric embeddings...";
  msg.className = "status-msg info";

  const reader = new FileReader();
  reader.onload = async (e) => {
    try {
      const res = await fetch(`${API}/api/students/${prn}/upload_photo`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image: e.target.result }),
      });
      const data = await res.json();
      msg.textContent = data.message || (data.success ? "Photo and biometrics saved to MongoDB!" : "Failed to enroll photo.");
      msg.className = "status-msg " + (data.success ? "success" : "error");
      if (data.success) {
        loadStudentSelect();
        loadStudents();
      }
    } catch (err) {
      msg.textContent = "Error uploading photo: " + err.message;
      msg.className = "status-msg error";
    } finally {
      event.target.value = "";
    }
  };
  reader.readAsDataURL(file);
}

let poseModelsLoaded = false;
let enrollLoopHandle = null;
let enrollLoopActive = false;
let poseHoldCounter = 0;
let capturingInProgress = false;

const POSE_OFFSET_X = 0.10;    // gentle head turn threshold
const CENTER_TOLERANCE = 0.12; // generous center tolerance
const POSE_HOLD_FRAMES = 3;    // 3 consecutive good frames for quick, smooth auto-capture

async function loadPoseModels() {
  if (poseModelsLoaded) return;
  const MODEL_URL = "https://cdn.jsdelivr.net/gh/justadudewhohacks/face-api.js@master/weights";
  try {
    await faceapi.nets.tinyFaceDetector.loadFromUri(MODEL_URL);
    await faceapi.nets.faceLandmark68TinyNet.loadFromUri(MODEL_URL);
    poseModelsLoaded = true;
  } catch (err) {
    console.warn("Could not load pose detection models:", err);
  }
}

async function startEnrollment() {
  currentPrn = document.getElementById("studentSelect").value;
  if (!currentPrn) return;

  const res = await fetch(`${API}/api/angles`);
  ANGLES = await res.json();
  currentAngleIndex = 0;
  poseHoldCounter = 0;
  capturingInProgress = false;

  const card = document.getElementById("enrollCameraCard");
  card.style.display = "block";
  renderAngleDots();
  if (ANGLES.length > 0) {
    updatePoseArrow(ANGLES[0].angle);
    updateManualCaptureButton();
  }
  document.getElementById("enrollInstruction").textContent = ANGLES[0].instruction;
  document.getElementById("enrollMsg").textContent = "Hold your face steady in the guided direction (auto-captures) or click the button below";
  document.getElementById("enrollMsg").className = "status-msg info";

  if (!enrollStream) {
    try {
      enrollStream = await navigator.mediaDevices.getUserMedia({ video: true });
      document.getElementById("enrollVideo").srcObject = enrollStream;
    } catch (e) {
      document.getElementById("enrollMsg").textContent = "Camera access error. Please ensure permissions are granted.";
      document.getElementById("enrollMsg").className = "status-msg error";
      return;
    }
  }

  await loadPoseModels();
  enrollLoopActive = true;
  if (!enrollLoopHandle) {
    enrollLoopHandle = requestAnimationFrame(enrollPoseLoop);
  }
}

function renderAngleDots() {
  const dots = document.getElementById("angleDots");
  dots.innerHTML = ANGLES.map((a, i) => {
    let cls = "dot";
    if (i < currentAngleIndex) cls += " done";
    else if (i === currentAngleIndex) cls += " current";
    return `<div class="${cls}"></div>`;
  }).join("");
}

function updatePoseArrow(angleType) {
  const arrow = document.getElementById("poseArrow");
  if (!arrow) return;
  arrow.className = "pose-arrow";
  if (angleType === "left") {
    arrow.classList.add("show-left");
    arrow.innerHTML = `<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"></line><polyline points="12 19 5 12 12 5"></polyline></svg>`;
  } else if (angleType === "right") {
    arrow.classList.add("show-right");
    arrow.innerHTML = `<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"></line><polyline points="12 5 19 12 12 19"></polyline></svg>`;
  } else if (angleType === "center") {
    arrow.classList.add("show-center");
    arrow.innerHTML = `<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle><circle cx="12" cy="12" r="3.5" fill="currentColor"></circle></svg>`;
  }
}

function updateManualCaptureButton() {
  const btn = document.getElementById("manualCaptureBtn");
  const btnText = document.getElementById("manualCaptureText");
  if (!btn || !btnText) return;
  if (currentAngleIndex < ANGLES.length) {
    btn.style.display = "inline-flex";
    const angle = ANGLES[currentAngleIndex].angle;
    const label = angle.charAt(0).toUpperCase() + angle.slice(1);
    btnText.textContent = `Capture ${label}`;
  } else {
    btn.style.display = "none";
  }
}

async function triggerManualCapture() {
  if (capturingInProgress || currentAngleIndex >= ANGLES.length) return;
  const targetAngle = ANGLES[currentAngleIndex].angle;
  capturingInProgress = true;
  const arrow = document.getElementById("poseArrow");
  if (arrow) {
    arrow.classList.remove("aligned");
    arrow.classList.add("captured");
  }
  await autoCaptureAngle(targetAngle);
  capturingInProgress = false;
}

function grabFrame(videoEl) {
  const canvas = document.createElement("canvas");
  canvas.width = videoEl.videoWidth;
  canvas.height = videoEl.videoHeight;
  canvas.getContext("2d").drawImage(videoEl, 0, 0);
  return canvas.toDataURL("image/jpeg", 0.9);
}

function poseMatchesTarget(targetAngle, offsetX, offsetY) {
  if (targetAngle === "center") {
    // Only check horizontal alignment; vertical offset naturally varies with posture
    return Math.abs(offsetX) < CENTER_TOLERANCE;
  }
  // Looking to the left: in raw camera coordinates, user's nose moves to camera right (offsetX > 0)
  if (targetAngle === "left") {
    return offsetX > POSE_OFFSET_X;
  }
  // Looking to the right: in raw camera coordinates, user's nose moves to camera left (offsetX < 0)
  if (targetAngle === "right") {
    return offsetX < -POSE_OFFSET_X;
  }
  return false;
}

async function enrollPoseLoop() {
  const video = document.getElementById("enrollVideo");
  const arrow = document.getElementById("poseArrow");
  const instruction = document.getElementById("enrollInstruction");

  if (enrollLoopActive && video.readyState === 4 && !capturingInProgress && currentAngleIndex < ANGLES.length) {
    const targetAngle = ANGLES[currentAngleIndex].angle;

    let result = null;
    try {
      const options = new faceapi.TinyFaceDetectorOptions({ inputSize: 224 });
      result = await faceapi.detectSingleFace(video, options).withFaceLandmarks(true);
    } catch (e) {
      // If faceapi is still initializing or encountered an issue, continue loop
    }

    if (result) {
      const box = result.detection.box;
      const nosePoints = result.landmarks.getNose();
      const noseTip = nosePoints[nosePoints.length - 1];
      const offsetX = (noseTip.x - (box.x + box.width / 2)) / box.width;
      const offsetY = (noseTip.y - (box.y + box.height / 2)) / box.height;

      if (poseMatchesTarget(targetAngle, offsetX, offsetY)) {
        poseHoldCounter++;
        if (arrow) arrow.classList.add("aligned");
        if (instruction) {
          instruction.textContent = `Hold steady (${poseHoldCounter}/${POSE_HOLD_FRAMES})...`;
        }
      } else {
        poseHoldCounter = 0;
        if (arrow) arrow.classList.remove("aligned");
        if (instruction && ANGLES[currentAngleIndex]) {
          instruction.textContent = ANGLES[currentAngleIndex].instruction;
        }
      }

      if (poseHoldCounter >= POSE_HOLD_FRAMES) {
        poseHoldCounter = 0;
        capturingInProgress = true;
        if (arrow) {
          arrow.classList.remove("aligned");
          arrow.classList.add("captured");
        }
        await autoCaptureAngle(targetAngle);
        capturingInProgress = false;
      }
    } else {
      poseHoldCounter = 0;
      if (arrow) arrow.classList.remove("aligned");
      if (instruction && ANGLES[currentAngleIndex]) {
        instruction.textContent = ANGLES[currentAngleIndex].instruction;
      }
    }
  }

  enrollLoopHandle = requestAnimationFrame(enrollPoseLoop);
}

async function autoCaptureAngle(angle) {
  const video = document.getElementById("enrollVideo");
  const image = grabFrame(video);
  const msg = document.getElementById("enrollMsg");
  const arrow = document.getElementById("poseArrow");

  msg.textContent = `Captured '${angle}' — verifying...`;
  msg.className = "status-msg info";

  const res = await fetch(`${API}/api/students/${currentPrn}/capture`, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ angle, image })
  });
  const data = await res.json();

  if (!data.success) {
    // Rejected (duplicate face / no clear face) - stay on same angle and retry
    msg.textContent = data.message;
    msg.className = "status-msg error";
    if (arrow) {
      arrow.classList.remove("captured");
      updatePoseArrow(angle);
    }
    return;
  }

  currentAngleIndex++;
  renderAngleDots();
  updateManualCaptureButton();

  if (data.all_done) {
    msg.textContent = "Face setup complete!";
    msg.className = "status-msg success";
    document.getElementById("enrollInstruction").textContent = "Done ✓";
    if (arrow) {
      arrow.className = "pose-arrow done";
      arrow.innerHTML = `<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>`;
    }
    const capBtn = document.getElementById("manualCaptureBtn");
    if (capBtn) capBtn.style.display = "none";
    enrollLoopActive = false;
    loadStudentSelect();
    loadStudents();
  } else {
    msg.textContent = data.message;
    msg.className = "status-msg success";
    document.getElementById("enrollInstruction").textContent = data.next_instruction;
    if (currentAngleIndex < ANGLES.length) {
      updatePoseArrow(ANGLES[currentAngleIndex].angle);
    }
  }
}

// ---------- Attendance ----------
// Liveness / anti-spoofing now runs server-side using a pretrained ML model
// (DeepFace + MiniFASNet). The browser just captures a frame and sends it;
// the backend rejects photos/videos before attempting recognition.
async function startAttendCamera() {
  if (!attendStream) {
    attendStream = await navigator.mediaDevices.getUserMedia({ video: true });
    document.getElementById("attendVideo").srcObject = attendStream;
  }
}

async function loadToday() {
  const res = await fetch(`${API}/api/attendance/today`);
  const records = await res.json();
  document.getElementById("todayTableBody").innerHTML = records.map(r => `
    <tr>
      <td>${r.session || "—"}</td>
      <td>${r.roll_no}</td>
      <td>${r.name}</td>
      <td>${r.division}</td>
      <td>${r.time}</td>
    </tr>
  `).join("");
}

async function scanFace() {
  const video = document.getElementById("attendVideo");
  const image = grabFrame(video);
  const resultDiv = document.getElementById("attendResult");
  const tracker = document.getElementById("attemptTracker");
  const scanBtn = document.getElementById("scanBtn");

  scanBtn.disabled = true;
  resultDiv.innerHTML = `<div class="status-msg info">Checking liveness and scanning...</div>`;

  const sessionName = (document.getElementById("manualSessionName")?.value || "").trim();
  if (!sessionName) {
    resultDiv.innerHTML = `<div class="status-msg error">Enter a session name first (e.g. DBMS Lecture).</div>`;
    scanBtn.disabled = false;
    return;
  }

  const res = await fetch(`${API}/api/attendance/scan`, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ image, session_name: sessionName })
  });
  const data = await res.json();

  if (data.recognized) {
    scanAttempts = 0;
    tracker.textContent = "";
    resultDiv.innerHTML = `
      <div class="result-popup ok">
        <div class="name">${data.name}</div>
        <div class="sub">Roll No ${data.roll_no} — ${data.message}</div>
      </div>`;
    loadToday();
  } else if (data.spoof_detected) {
    resultDiv.innerHTML = `
      <div class="result-popup fail">
        <div class="name">Spoof Detected</div>
        <div class="sub">${data.message}</div>
      </div>`;
  } else {
    scanAttempts++;
    if (scanAttempts >= MAX_ATTEMPTS) {
      resultDiv.innerHTML = `
        <div class="result-popup fail">
          <div class="name">Not Recognized</div>
          <div class="sub">Sent for manual review after ${MAX_ATTEMPTS} attempts. Teacher will verify at end of session.</div>
        </div>`;
      scanAttempts = 0;
    } else {
      resultDiv.innerHTML = `<div class="status-msg error">${data.message}</div>`;
      tracker.textContent = `Attempt ${scanAttempts}/${MAX_ATTEMPTS}`;
    }
  }

  scanBtn.disabled = false;
}

// ---------- CCTV Session ----------
let cctvPollHandle = null;
let cctvPreviewHandle = null;
let currentSessionId = null;

function formatSeconds(s) {
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

function startCctvPreview() {
  const img = document.getElementById("cctvPreview");
  if (!img) return;
  if (cctvPreviewHandle) clearInterval(cctvPreviewHandle);
  const refresh = () => {
    img.src = `${API}/api/cctv/preview?session_id=${currentSessionId || ''}&t=${Date.now()}`;
  };
  refresh();
  cctvPreviewHandle = setInterval(refresh, 500);
}

function stopCctvPreview() {
  if (cctvPreviewHandle) {
    clearInterval(cctvPreviewHandle);
    cctvPreviewHandle = null;
  }
}

async function startCctv() {
  const rtspUrl = document.getElementById("rtspUrl").value.trim();
  const sessionName = document.getElementById("cctvSessionName").value.trim();
  const duration = parseInt(document.getElementById("cctvDuration").value) || 40;
  const msg = document.getElementById("cctvMsg");

  if (!rtspUrl) {
    msg.textContent = "Please enter the RTSP URL for the CCTV camera.";
    msg.className = "status-msg error";
    return;
  }
  if (!sessionName) {
    msg.textContent = "Please enter a session name (subject), e.g. DBMS Lecture or Python Lab.";
    msg.className = "status-msg error";
    return;
  }

  msg.textContent = "Connecting to CCTV stream...";
  msg.className = "status-msg info";

  const camSel = document.getElementById("cctvCameraSelect");
  let camera_name = "";
  let camera_id = "";
  if (camSel && camSel.value) {
    camera_id = camSel.value;
    const opt = camSel.options[camSel.selectedIndex];
    camera_name = decodeURIComponent(opt.dataset.name || "");
  }

  const res = await fetch(`${API}/api/cctv/start`, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      rtsp_url: rtspUrl,
      duration_minutes: duration,
      session_name: sessionName,
      camera_name,
      camera_id,
    })
  });
  const data = await res.json();

  msg.textContent = data.message;
  msg.className = data.success ? "status-msg success" : "status-msg error";

  if (data.success) {
    currentSessionId = data.session_id || null;
    document.getElementById("cctvStartBtn").disabled = true;
    document.getElementById("cctvStopBtn").disabled = false;
    document.getElementById("cctvStatusCard").style.display = "block";
    startCctvPreview();
    if (!cctvPollHandle) {
      cctvPollHandle = setInterval(pollCctvStatus, 1500);
    }
    loadActiveSessions();
  }
}

async function stopCctv() {
  if (!currentSessionId) {
    const msg = document.getElementById("cctvMsg");
    msg.textContent = "No active session selected to stop.";
    msg.className = "status-msg error";
    return;
  }
  const res = await fetch(`${API}/api/cctv/stop`, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ session_id: currentSessionId })
  });
  const data = await res.json();
  const msg = document.getElementById("cctvMsg");
  msg.textContent = data.message;
  msg.className = "status-msg info";
  loadActiveSessions();
}

async function pollCctvStatus() {
  const res = await fetch(`${API}/api/cctv/status?session_id=${currentSessionId || ""}`);
  const data = await res.json();

  if (data.session_name) {
    document.getElementById("cctvSessionLabel").textContent = data.session_name;
  }
  const camLbl = document.getElementById("cctvCameraLabel");
  if (camLbl) camLbl.textContent = data.camera_name || "—";
  if (data.session_started_at) {
    document.getElementById("cctvStartedAt").textContent = data.session_started_at;
  }
  document.getElementById("cctvElapsed").textContent = formatSeconds(data.elapsed_sec);
  document.getElementById("cctvRemaining").textContent = formatSeconds(data.remaining_sec);
  document.getElementById("cctvMarkedCount").textContent = data.marked_count;
  document.getElementById("cctvFrames").textContent = data.frames_processed;
  if (data.faces_last_frame !== undefined) {
    document.getElementById("cctvFaces").textContent = data.faces_last_frame;
  }
  if (data.enrolled_faces !== undefined) {
    document.getElementById("cctvEnrolled").textContent = data.enrolled_faces;
  }
  if (data.detector_mode) {
    const labels = {
      arcface_buffalo_l: "InsightFace ArcFace",
      dlib_128: "dlib (fallback)",
      face_yolo: "YOLOv8 Face",
      person_yolo: "YOLOv8 Person + face",
      hog: "HOG (fallback)",
    };
    document.getElementById("cctvDetector").textContent = labels[data.detector_mode] || data.detector_mode;
  }

  document.getElementById("cctvMarkedBody").innerHTML = data.marked_students.map(s => `
    <tr><td>${s.roll_no}</td><td>${s.name}</td><td>${s.time}</td></tr>
  `).join("");

  if (data.last_error) {
    const msg = document.getElementById("cctvMsg");
    msg.textContent = data.last_error;
    msg.className = "status-msg error";
  }

  if (!data.running) {
    clearInterval(cctvPollHandle);
    cctvPollHandle = null;
    stopCctvPreview();
    document.getElementById("cctvStartBtn").disabled = false;
    document.getElementById("cctvStopBtn").disabled = true;
    const msg = document.getElementById("cctvMsg");
    if (!data.last_error) {
      msg.textContent = "Session ended.";
      msg.className = "status-msg success";
    }
  }
}


// ---------- Sessions page ----------
let sessionFilterMode = "today";

function setSessionFilter(mode) {
  sessionFilterMode = mode;
  document.getElementById("sessFilterToday").classList.toggle("active", mode === "today");
  document.getElementById("sessFilterAll").classList.toggle("active", mode === "all");
  loadSessionsPage();
}

async function loadSessionsPage() {
  const url = sessionFilterMode === "all"
    ? `${API}/api/attendance/sessions?all=1`
    : `${API}/api/attendance/sessions`;
  const res = await fetch(url);
  const sessions = await res.json();
  const grid = document.getElementById("sessionCards");
  if (!sessions.length) {
    grid.innerHTML = `<div class="empty-state">No sessions yet. Start a CCTV or manual scan session.</div>`;
    return;
  }
  grid.innerHTML = sessions.map(s => `
    <div class="session-card" onclick="openSessionDetail('${encodeURIComponent(s.session)}', '${s.date}')">
      <div class="sc-title">${s.session}</div>
      <div class="sc-meta">${s.date} · ${s.first_time || ""} – ${s.last_time || ""}</div>
      <span class="sc-count">${s.count} present</span>
    </div>
  `).join("");
}

async function openSessionDetail(sessionEnc, date) {
  const session = decodeURIComponent(sessionEnc);
  // highlight selected card
  document.querySelectorAll(".session-card").forEach(c => c.classList.remove("selected"));
  // load students for this session+date
  const res = await fetch(`${API}/api/attendance/today?session=${encodeURIComponent(session)}&date=${encodeURIComponent(date)}`);
  const records = await res.json();

  document.getElementById("sessionDetailCard").style.display = "block";
  document.getElementById("sessionDetailTitle").textContent = session;
  document.getElementById("sessionDetailMeta").textContent = `Date: ${date}`;
  document.getElementById("sessionDetailCount").textContent = `${records.length} student(s) present`;
  document.getElementById("sessionDetailBody").innerHTML = records.map((r, i) => {
    const snap = r.snapshot
      ? `<img src="${r.snapshot}" class="snap-thumb" alt="${r.name}" title="Verification snapshot for ${r.name}">`
      : `<div class="avatar-placeholder" style="width:34px; height:34px;">${getInitials(r.name)}</div>`;
    return `
      <tr>
        <td>${i + 1}</td>
        <td style="width:44px;">${snap}</td>
        <td>${r.roll_no}</td>
        <td><strong>${r.name}</strong></td>
        <td>${r.division}</td>
        <td>${r.branch || "—"}</td>
        <td>${r.time}</td>
      </tr>`;
  }).join("") || `<tr><td colspan="7" style="text-align:center; padding:20px; color:var(--muted);">No students in this session.</td></tr>`;

  document.getElementById("sessionDetailCard").scrollIntoView({ behavior: "smooth", block: "start" });
}

function closeSessionDetail() {
  document.getElementById("sessionDetailCard").style.display = "none";
}



// ---------- Multi-camera dashboard ----------
let pendingCamera = null; // {id, name, rtsp_url}

async function loadCameras() {
  const res = await fetch(`${API}/api/classrooms`);
  const cams = await res.json();
  const grid = document.getElementById("cameraCards");
  if (!grid) return;
  if (!cams.length) {
    grid.innerHTML = `<div class="empty-state">No cameras saved yet. Add one above.</div>`;
    fillCctvCameraSelect(cams);
    return;
  }
  grid.innerHTML = cams.map(c => {
    const title = c.classroom || c.name || "Classroom";
    const badge = c.active
      ? `<span class="sc-badge live">LIVE · ${c.session_name || "session"}</span>`
      : `<span class="sc-badge idle">Idle</span>`;
    const fullUrl = c.rtsp_url || "";
    const shortUrl = fullUrl.length > 38 ? fullUrl.slice(0, 36) + "…" : fullUrl;
    return `
      <div class="session-card ${c.active ? "selected" : ""}">
        <div class="sc-header">
          <div class="sc-title" title="${title}">${title}</div>
          ${badge}
        </div>
        <div class="sc-meta">${c.location || "No location set"}</div>
        <div class="sc-url" title="${fullUrl}">${shortUrl}</div>
        <div class="sc-actions">
          <button class="btn btn-sm" onclick='prepareCamStart(${JSON.stringify({id: c.id, classroom: title, name: title, rtsp_url: c.rtsp_url, location: c.location})})'>Start session</button>
          <button class="btn secondary btn-sm" onclick="deleteCamera('${c.id}')">Remove</button>
        </div>
      </div>`;
  }).join("");
  fillCctvCameraSelect(cams);
}

function fillCctvCameraSelect(cams) {
  const sel = document.getElementById("cctvCameraSelect");
  if (!sel) return;
  const run = (list) => {
    const cur = sel.value;
    sel.innerHTML = `<option value="">— Select saved camera (optional) —</option>` +
      list.map(c => {
        const name = c.classroom || c.name || "Camera";
        const loc = c.location ? ` · ${c.location}` : "";
        return `<option value="${c.id}" data-url="${encodeURIComponent(c.rtsp_url || '')}" data-name="${encodeURIComponent(name)}">${name}${loc}</option>`;
      }).join("");
    if (cur) sel.value = cur;
  };
  if (cams && Array.isArray(cams)) run(cams);
  else fetch(`${API}/api/classrooms`).then(r => r.json()).then(run).catch(() => {});
}

function onCctvCameraPick() {
  const sel = document.getElementById("cctvCameraSelect");
  const opt = sel.options[sel.selectedIndex];
  if (!opt || !opt.value) return;
  const url = decodeURIComponent(opt.dataset.url || "");
  const name = decodeURIComponent(opt.dataset.name || "");
  document.getElementById("rtspUrl").value = url;
  const sessInput = document.getElementById("cctvSessionName");
  if (sessInput && (!sessInput.value || sessInput.value.includes("Lecture"))) {
    sessInput.value = `${name} Lecture`;
  }
}

async function addCamera() {
  const name = document.getElementById("camName").value.trim();
  const location = document.getElementById("camLocation").value.trim();
  const rtsp_url = document.getElementById("camRtsp").value.trim();
  const msg = document.getElementById("camMsg");
  const res = await fetch(`${API}/api/classrooms`, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ classroom: name, name, location, rtsp_url })
  });
  const data = await res.json();
  msg.textContent = data.message || (data.success ? "Saved" : "Failed");
  msg.className = "status-msg " + (data.success ? "success" : "error");
  if (data.success) {
    document.getElementById("camName").value = "";
    document.getElementById("camLocation").value = "";
    document.getElementById("camRtsp").value = "";
    loadCameras();
  }
}

async function deleteCamera(id) {
  if (!confirm("Remove this camera from the dashboard?")) return;
  await fetch(`${API}/api/classrooms/${id}`, { method: "DELETE" });
  loadCameras();
}

function prepareCamStart(cam) {
  pendingCamera = cam;
  const name = cam.classroom || cam.name || "Classroom";
  document.getElementById("camStartPanel").style.display = "block";
  document.getElementById("camStartName").textContent = name;
  document.getElementById("camStartSession").value = `${name} Lecture`;
  document.getElementById("camStartMsg").textContent = "";
  document.getElementById("camStartPanel").scrollIntoView({ behavior: "smooth" });
}

function cancelCamStart() {
  pendingCamera = null;
  document.getElementById("camStartPanel").style.display = "none";
}

async function startFromCamera() {
  if (!pendingCamera) return;
  const sessionName = document.getElementById("camStartSession").value.trim();
  const duration = parseInt(document.getElementById("camStartDuration").value) || 40;
  const msg = document.getElementById("camStartMsg");
  if (!sessionName) {
    msg.textContent = "Enter a session name (subject).";
    msg.className = "status-msg error";
    return;
  }
  msg.textContent = "Starting...";
  msg.className = "status-msg info";
  const res = await fetch(`${API}/api/cctv/start`, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      camera_id: pendingCamera.id,
      rtsp_url: pendingCamera.rtsp_url,
      classroom: pendingCamera.classroom || pendingCamera.name,
      camera_name: pendingCamera.classroom || pendingCamera.name,
      session_name: sessionName,
      duration_minutes: duration,
    })
  });
  const data = await res.json();
  msg.textContent = data.message;
  msg.className = "status-msg " + (data.success ? "success" : "error");
  if (data.success) {
    currentSessionId = data.session_id || null;
    document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
    const cctvBtn = document.querySelector('.nav-btn[data-view="cctv"]');
    if (cctvBtn) cctvBtn.classList.add("active");
    document.getElementById("cctv").classList.add("active");
    document.getElementById("rtspUrl").value = pendingCamera.rtsp_url;
    document.getElementById("cctvSessionName").value = sessionName;
    document.getElementById("cctvDuration").value = duration;
    document.getElementById("cctvStartBtn").disabled = true;
    document.getElementById("cctvStopBtn").disabled = false;
    document.getElementById("cctvStatusCard").style.display = "block";
    document.getElementById("cctvMsg").textContent = data.message;
    document.getElementById("cctvMsg").className = "status-msg success";
    startCctvPreview();
    if (!cctvPollHandle) cctvPollHandle = setInterval(pollCctvStatus, 1500);
    loadCameras();
    loadActiveSessions();
    cancelCamStart();
  }
}



async function loadActiveSessions() {
  const el = document.getElementById("activeSessionsList");
  if (!el) return;
  const res = await fetch(`${API}/api/cctv/sessions`);
  const data = await res.json();
  const list = (data.sessions || []).filter(s => s.running);
  if (!list.length) {
    el.innerHTML = `<div class="empty-state">No active sessions. Running: 0 / ${data.max_concurrent || 80}</div>`;
    return;
  }
  el.innerHTML = list.map(s => `
    <div class="session-card ${s.session_id === currentSessionId ? "selected" : ""}">
      <div class="sc-header">
        <div class="sc-title" title="${s.session_name || "Session"}">${s.session_name || "Session"}</div>
        <span class="sc-badge live">LIVE</span>
      </div>
      <div class="sc-meta">Classroom: ${s.classroom || "—"} · ${s.session_started_at || ""}</div>
      <div class="sc-meta">Marked: ${s.marked_count || 0} · Faces: ${s.faces_last_frame || 0}</div>
      <div class="sc-actions">
        <button class="btn btn-sm" onclick="watchSession('${s.session_id}')">Watch</button>
        <button class="btn secondary btn-sm" onclick="stopSessionById('${s.session_id}')">Stop</button>
      </div>
    </div>
  `).join("") + `<p class="hint">Active ${list.length} / max ${data.max_concurrent || 80}</p>`;
}

function watchSession(sid) {
  currentSessionId = sid;
  document.getElementById("cctvStatusCard").style.display = "block";
  document.getElementById("cctvStopBtn").disabled = false;
  startCctvPreview();
  if (!cctvPollHandle) cctvPollHandle = setInterval(pollCctvStatus, 1500);
  pollCctvStatus();
  loadActiveSessions();
}

async function stopSessionById(sid) {
  await fetch(`${API}/api/cctv/stop`, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ session_id: sid })
  });
  if (currentSessionId === sid) currentSessionId = null;
  loadActiveSessions();
}

// Initial load
loadStudents();

