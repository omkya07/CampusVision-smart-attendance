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
    if (btn.dataset.view === "attendance") { loadToday(); startAttendCamera(); populateManualSessionDropdowns(); }
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
    email: document.getElementById("email").value,
    phone: document.getElementById("phone").value,
    academic_year: document.getElementById("academic_year").value,
    semester: document.getElementById("semester").value,
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
    ["prn","roll_no","name","email","phone","academic_year","semester","division","branch"].forEach(id => document.getElementById(id).value = "");
    loadStudents();
  }
}

function getInitials(name) {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

async function handleCsvUpload(e) {
  const file = e.target.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append("csv_file", file);
  
  const msg = document.getElementById("registerMsg");
  const progContainer = document.getElementById("uploadProgressContainer");
  const progBar = document.getElementById("uploadProgressBar");
  
  msg.textContent = "Uploading CSV...";
  msg.className = "status-msg";
  progContainer.style.display = "block";
  progBar.style.width = "0%";
  
  const xhr = new XMLHttpRequest();
  xhr.open("POST", `${API}/api/students_upload`);
  
  xhr.upload.onprogress = (event) => {
    if (event.lengthComputable) {
      const percent = (event.loaded / event.total) * 100;
      progBar.style.width = percent + "%";
    }
  };
  
  xhr.onload = () => {
    let data;
    try { data = JSON.parse(xhr.responseText); } catch(err) { data = {success: false, message: "Server error"}; }
    
    progContainer.style.display = "none";
    msg.textContent = data.message;
    msg.className = "status-msg " + (data.success ? "success" : "error");
    if (data.success) {
      e.target.value = ''; // reset file input
      loadStudents();
    }
  };
  
  xhr.onerror = () => {
    progContainer.style.display = "none";
    msg.textContent = "Upload failed due to network error.";
    msg.className = "status-msg error";
  };
  
  xhr.send(formData);
}

async function loadStudents() {
  const res = await fetch(`${API}/api/students`);
  const students = await res.json();
  const body = document.getElementById("studentTableBody");
  
  // Populate filters
  const branchSelect = document.getElementById("filterBranch");
  const divSelect = document.getElementById("filterDivision");
  
  const currentBranch = branchSelect.value;
  const currentDiv = divSelect.value;
  
  const branches = [...new Set(students.map(s => (s.branch || "").trim()).filter(Boolean))].sort();
  const divisions = [...new Set(students.map(s => (s.division || "").trim()).filter(Boolean))].sort();
  
  branchSelect.innerHTML = `<option value="">All Branches</option>` + branches.map(b => `<option value="${b}">${b}</option>`).join("");
  divSelect.innerHTML = `<option value="">All Divisions</option>` + divisions.map(d => `<option value="${d}">${d}</option>`).join("");
  
  branchSelect.value = currentBranch;
  divSelect.value = currentDiv;
  
  const filteredStudents = students.filter(s => {
    const bMatch = !currentBranch || (s.branch || "").trim() === currentBranch;
    const dMatch = !currentDiv || (s.division || "").trim() === currentDiv;
    return bMatch && dMatch;
  });
  
  if (!filteredStudents.length) {
    body.innerHTML = `<tr><td colspan="9" style="text-align:center; padding:24px; color:var(--muted);">No students match criteria.</td></tr>`;
    return;
  }
  body.innerHTML = filteredStudents.map(s => {
    const avatar = s.photo
      ? `<img src="${s.photo}" class="student-avatar has-photo" alt="${s.name}" title="Click to view full photo" onclick="openImageModal('${s.photo}', '${s.name}')">`
      : `<div class="avatar-placeholder" title="No photo">${getInitials(s.name)}</div>`;
    return `
      <tr>
        <td style="width:48px;">${avatar}</td>
        <td>${s.prn}</td><td>${s.roll_no}</td><td><strong>${s.name}</strong></td>
        <td>${s.email || `<span class="muted-inline">Not set</span>`}</td>
        <td>${s.division}</td><td>${s.branch}</td>
        <td><span class="badge ${s.face_setup_complete ? 'done' : 'pending'}">
          ${s.face_setup_complete ? 'Complete' : (s.angles_done ? s.angles_done.length : 0) + '/3 angles'}
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
  
  const branchSelect = document.getElementById("enrollFilterBranch");
  const divSelect = document.getElementById("enrollFilterDivision");
  
  const currentBranch = branchSelect.value;
  const currentDiv = divSelect.value;
  
  const branches = [...new Set(students.map(s => (s.branch || "").trim()).filter(Boolean))].sort();
  const divisions = [...new Set(students.map(s => (s.division || "").trim()).filter(Boolean))].sort();
  
  branchSelect.innerHTML = `<option value="">All Branches</option>` + branches.map(b => `<option value="${b}">${b}</option>`).join("");
  divSelect.innerHTML = `<option value="">All Divisions</option>` + divisions.map(d => `<option value="${d}">${d}</option>`).join("");
  
  branchSelect.value = currentBranch;
  divSelect.value = currentDiv;
  
  const filteredStudents = students.filter(s => {
    const bMatch = !currentBranch || (s.branch || "").trim() === currentBranch;
    const dMatch = !currentDiv || (s.division || "").trim() === currentDiv;
    return bMatch && dMatch;
  });

  const select = document.getElementById("studentSelect");
  select.innerHTML = filteredStudents.map(s =>
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

  await fetch(`${API}/api/students/${currentPrn}/reset_enrollment`, { method: "POST" });

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
  document.getElementById("todayTableBody").innerHTML = records.map(r => {
    const isAbsent = r.status === "absent";
    const timeDisplay = isAbsent ? '<span class="muted">--:--</span>' : r.time;
    const nameDisplay = isAbsent ? `<strong>${r.name}</strong> <span class="badge error" style="font-size:0.7em;">Absent</span>` : `<strong>${r.name}</strong>`;
    
    return `
      <tr ${isAbsent ? 'style="opacity: 0.7;"' : ''}>
        <td>${r.session || "—"}</td>
        <td>${r.roll_no}</td>
        <td>${nameDisplay}</td>
        <td>${r.division}</td>
        <td>${timeDisplay}</td>
      </tr>
    `;
  }).join("");
}

let allStudentsCache = [];

function renderCheckboxes(arr) {
  return arr.map(item => `<label class="checkbox-label"><input type="checkbox" value="${item}">${item}</label>`).join("");
}

function getBranchCode(branch) {
  if (!branch) return "GEN";
  const b = branch.toUpperCase().trim();
  if (b.includes("MECH")) return "MECH";
  if (b.includes("AIML") || (b.includes("AI") && b.includes("ML"))) return "CSE-AIML";
  if (b.includes("COMPUTER") || b.includes("CSE") || b.includes("CS")) return "CSE";
  if (b.includes("INFO") || b.includes("IT")) return "IT";
  if (b.includes("ELECTRO") || b.includes("ENTC") || b.includes("EXTC") || b.includes("ECE")) return "ENTC";
  if (b.includes("ELECTRI") || b.includes("EE")) return "ELECT";
  if (b.includes("CIVIL")) return "CIVIL";
  if (b.includes("AUTO")) return "AUTO";
  if (b.includes("DATA") || b.includes("DS")) return "DS";
  const words = b.split(/[\s_-]+/);
  if (words.length > 1) return words.map(w => w[0]).join("");
  return b.substring(0, 5);
}

function getYearCode(sem) {
  sem = (sem || "").toUpperCase().trim();
  if (["SEM-I", "SEM-II", "SEM-1", "SEM-2", "1", "2"].includes(sem) || sem.includes("FY") || sem.includes("FIRST")) return "FY";
  if (["SEM-III", "SEM-IV", "SEM-3", "SEM-4", "3", "4"].includes(sem) || sem.includes("SY") || sem.includes("SECOND")) return "SY";
  if (["SEM-V", "SEM-VI", "SEM-5", "SEM-6", "5", "6"].includes(sem) || sem.includes("TY") || sem.includes("THIRD")) return "TY";
  if (["SEM-VII", "SEM-VIII", "SEM-7", "SEM-8", "7", "8"].includes(sem) || sem.includes("LY") || sem.includes("FINAL") || sem.includes("FOURTH")) return "LY";
  return "GEN";
}

function getYearFullName(yearCode) {
  switch(yearCode) {
    case "FY": return "1st Year (FY)";
    case "SY": return "2nd Year (SY)";
    case "TY": return "3rd Year (TY)";
    case "LY": return "Final Year (LY)";
    default: return yearCode;
  }
}

function getSemestersForYear(yearCode) {
  switch(yearCode) {
    case "FY": return ["SEM-I", "SEM-II", "SEM-1", "SEM-2", "1", "2"];
    case "SY": return ["SEM-III", "SEM-IV", "SEM-3", "SEM-4", "3", "4"];
    case "TY": return ["SEM-V", "SEM-VI", "SEM-5", "SEM-6", "5", "6"];
    case "LY": return ["SEM-VII", "SEM-VIII", "SEM-7", "SEM-8", "7", "8"];
    default: return [];
  }
}

function getYearFromSemester(sem) {
  const y = getYearCode(sem);
  return getYearFullName(y);
}

const STANDARD_BRANCHES = [
  "CSE", "CSE-AIML", "AIDS", "IT", "MECH", "CIVIL", "ENTC", "ELECTRICAL", "CHEM", "BIOTECH", "AUTO", "ROBOTICS"
];

class DivisionPicker {
  constructor({ prefix, onSelectionChange }) {
    this.prefix = prefix;
    this.branchSelect = document.getElementById(`${prefix}PickerBranch`);
    this.yearSelect = document.getElementById(`${prefix}PickerYear`);
    this.divOptionsContainer = document.getElementById(`${prefix}PickerDivOptions`);
    this.chosenCountBadge = document.getElementById(`${prefix}ChosenCount`);
    this.chosenListContainer = document.getElementById(`${prefix}ChosenList`);
    this.onSelectionChange = onSelectionChange;
    this.chosenMap = new Map();
  }

  init(students) {
    this.populateBranches(students);
    this.renderDivOptions();
    this.renderChosen();
  }

  populateBranches(students) {
    if (!this.branchSelect) return;
    const currentVal = this.branchSelect.value;
    const foundBranches = (students || []).map(s => (s.branch || "").trim()).filter(Boolean);
    const set = new Set([...STANDARD_BRANCHES, ...foundBranches]);
    const branchList = Array.from(set).sort();

    this.branchSelect.innerHTML = `<option value="">— Select Branch —</option>` +
      branchList.map(b => `<option value="${b}">${b}</option>`).join("");

    if (currentVal && set.has(currentVal)) {
      this.branchSelect.value = currentVal;
    }
  }

  onFilterChange() {
    this.renderDivOptions();
  }

  clearFilters() {
    if (this.branchSelect) this.branchSelect.value = "";
    if (this.yearSelect) this.yearSelect.value = "";
    this.renderDivOptions();
  }

  renderDivOptions() {
    if (!this.divOptionsContainer) return;
    const branch = this.branchSelect?.value || "";
    const year = this.yearSelect?.value || "";

    if (!branch || !year) {
      this.divOptionsContainer.innerHTML = `
        <span class="muted-inline" style="font-size: 12px;">Choose Branch and Year above to display divisions.</span>
      `;
      return;
    }

    const bCode = getBranchCode(branch);
    const standardDivs = ["A", "B", "C"];
    const sems = getSemestersForYear(year);

    const matchingStudents = (allStudentsCache || []).filter(s => {
      const b = (s.branch || "").trim();
      const bMatch = b.toUpperCase() === branch.toUpperCase() || getBranchCode(b) === bCode;
      const sem = (s.semester || "").toUpperCase().trim();
      const yrMatch = getYearCode(sem || s.academic_year) === year || sems.includes(sem);
      return bMatch && yrMatch;
    });

    const extraDivs = matchingStudents.map(s => (s.division || "").trim().toUpperCase()).filter(Boolean);
    const allDivs = Array.from(new Set([...standardDivs, ...extraDivs])).sort();

    this.divOptionsContainer.innerHTML = allDivs.map(div => {
      const code = `${bCode}_${year}_${div}`;
      const isPicked = this.chosenMap.has(code);
      const count = matchingStudents.filter(s => (s.division || "").toUpperCase().trim() === div).length;
      const countLabel = count > 0 ? `${count} student${count > 1 ? 's' : ''}` : `0 enrolled`;

      return `
        <button type="button" 
                class="pick-div-btn ${isPicked ? 'picked' : ''}" 
                onclick="${this.prefix}Picker.toggleDivision('${code}', '${branch.replace(/'/g, "\\'")}', '${bCode}', '${year}', '${div}')">
          <span class="pick-check">${isPicked ? '✓' : '+'}</span>
          <span><strong>Div ${div}</strong> <span style="opacity:0.75; font-size:11px;">(${countLabel})</span></span>
        </button>
      `;
    }).join("");
  }

  toggleDivision(code, branch, bCode, year, div) {
    if (this.chosenMap.has(code)) {
      this.chosenMap.delete(code);
    } else {
      this.chosenMap.set(code, {
        code: code,
        branch: branch,
        branchCode: bCode,
        year: year,
        yearName: getYearFullName(year),
        division: div,
        semesters: getSemestersForYear(year)
      });
    }
    this.renderDivOptions();
    this.renderChosen();
    if (this.onSelectionChange) this.onSelectionChange(this.getChosenList());
  }

  removeChosen(code) {
    this.chosenMap.delete(code);
    this.renderDivOptions();
    this.renderChosen();
    if (this.onSelectionChange) this.onSelectionChange(this.getChosenList());
  }

  clearAllChosen() {
    this.chosenMap.clear();
    this.renderDivOptions();
    this.renderChosen();
    if (this.onSelectionChange) this.onSelectionChange(this.getChosenList());
  }

  renderChosen() {
    const list = Array.from(this.chosenMap.values());
    if (this.chosenCountBadge) {
      this.chosenCountBadge.textContent = `${list.length} chosen`;
    }

    if (!this.chosenListContainer) return;

    if (list.length === 0) {
      this.chosenListContainer.innerHTML = `
        <span class="muted-inline" style="font-size: 12px; padding: 4px 0;">
          No divisions chosen yet. Use the filters above to pick divisions (e.g. MECH_SY_A, CSE_TY_C).
        </span>
      `;
      return;
    }

    this.chosenListContainer.innerHTML = list.map(item => `
      <div class="chosen-chip">
        <div>
          <span>${item.code}</span>
          <span class="chosen-chip-sub">(${item.branch} &bull; ${item.year} &bull; Div ${item.division})</span>
        </div>
        <button type="button" class="chosen-chip-remove" title="Remove ${item.code}" onclick="${this.prefix}Picker.removeChosen('${item.code}')">&times;</button>
      </div>
    `).join("");
  }

  getChosenList() {
    return Array.from(this.chosenMap.values());
  }
}

// Global picker instances
let manualPicker = null;
let camStartPicker = null;
let cctvPicker = null;

function ensurePickersInitialized() {
  if (!manualPicker && document.getElementById("manualPickerBranch")) {
    manualPicker = new DivisionPicker({ prefix: "manual" });
    window.manualPicker = manualPicker;
  }
  if (!camStartPicker && document.getElementById("camStartPickerBranch")) {
    camStartPicker = new DivisionPicker({ prefix: "camStart" });
    window.camStartPicker = camStartPicker;
  }
  if (!cctvPicker && document.getElementById("cctvPickerBranch")) {
    cctvPicker = new DivisionPicker({ prefix: "cctv" });
    window.cctvPicker = cctvPicker;
  }
}

async function populateManualSessionDropdowns() {
  ensurePickersInitialized();
  const res = await fetch(`${API}/api/students`);
  allStudentsCache = await res.json();
  if (manualPicker) manualPicker.init(allStudentsCache);
  if (cctvPicker) cctvPicker.init(allStudentsCache);
}

async function initSession() {
  ensurePickersInitialized();
  const sessionName = (document.getElementById("manualSessionName")?.value || "").trim();
  const selectedClasses = manualPicker ? manualPicker.getChosenList() : [];
  const msg = document.getElementById("initSessionMsg");

  if (!sessionName) {
    msg.textContent = "Please enter session name.";
    msg.className = "status-msg error";
    return;
  }
  if (selectedClasses.length === 0) {
    msg.textContent = "Please filter and pick at least one division above (e.g. MECH_SY_A, MECH_TY_C).";
    msg.className = "status-msg error";
    return;
  }

  msg.textContent = "Initializing session and marking default absent...";
  msg.className = "status-msg info";

  try {
    const res = await fetch(`${API}/api/admin/session/start`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        session: sessionName,
        classes: selectedClasses
      })
    });
    const data = await res.json();
    msg.textContent = data.message;
    msg.className = "status-msg " + (data.success ? "success" : "error");
  } catch (e) {
    msg.textContent = "Network error initializing session.";
    msg.className = "status-msg error";
  }
}

async function scanFace() {
  ensurePickersInitialized();
  const video = document.getElementById("attendVideo");
  const image = grabFrame(video);
  const resultDiv = document.getElementById("attendResult");
  const tracker = document.getElementById("attemptTracker");
  const scanBtn = document.getElementById("scanBtn");

  scanBtn.disabled = true;
  resultDiv.innerHTML = `<div class="status-msg info">Checking liveness and scanning...</div>`;

  const sessionName = (document.getElementById("manualSessionName")?.value || "").trim();
  const selectedClasses = manualPicker ? manualPicker.getChosenList() : [];

  if (!sessionName) {
    resultDiv.innerHTML = `<div class="status-msg error">Enter a session name first (e.g. DBMS Lecture).</div>`;
    scanBtn.disabled = false;
    return;
  }

  const res = await fetch(`${API}/api/attendance/scan`, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ 
      image, 
      session_name: sessionName, 
      classes: selectedClasses
    })
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
  ensurePickersInitialized();
  const rtspUrl = document.getElementById("rtspUrl").value.trim();
  const sessionName = document.getElementById("cctvSessionName").value.trim();
  const duration = parseInt(document.getElementById("cctvDuration").value) || 40;
  const selectedClasses = cctvPicker ? cctvPicker.getChosenList() : [];
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
  if (selectedClasses.length === 0) {
    msg.textContent = "Please filter and pick at least one division above (e.g. MECH_SY_A, MECH_TY_C).";
    msg.className = "status-msg error";
    return;
  }

  msg.textContent = "Connecting to CCTV stream and initializing attendance...";
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
      classes: selectedClasses
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
  
  const presentCount = records.filter(r => r.status === "present").length;

  document.getElementById("sessionDetailCard").style.display = "block";
  document.getElementById("sessionDetailTitle").textContent = session;
  document.getElementById("sessionDetailMeta").textContent = `Date: ${date}`;
  document.getElementById("sessionDetailCount").textContent = `${presentCount} student(s) present`;
  
  document.getElementById("sessionDetailBody").innerHTML = records.map((r, i) => {
    const isAbsent = r.status === "absent";
    
    let snap;
    if (isAbsent) {
      snap = `<span class="badge error" style="font-size:0.7em;">Absent</span>`;
    } else if (r.snapshot) {
      snap = `<img src="${r.snapshot}" class="snap-thumb" alt="${r.name}" title="Click to view snapshot" onclick="openImageModal('${r.snapshot}', '${r.name} - Snapshot')">`;
    } else {
      snap = `<div class="avatar-placeholder" style="width:34px; height:34px;">${getInitials(r.name)}</div>`;
    }
    
    const timeDisplay = isAbsent ? '<span class="muted">--:--</span>' : r.time;
    
    return `
      <tr ${isAbsent ? 'style="opacity: 0.7;"' : ''}>
        <td>${i + 1}</td>
        <td style="width:60px; text-align:center;">${snap}</td>
        <td>${r.roll_no}</td>
        <td><strong>${r.name}</strong></td>
        <td>${r.division}</td>
        <td>${r.branch || "—"}</td>
        <td>${timeDisplay}</td>
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

async function prepareCamStart(cam) {
  pendingCamera = cam;
  const name = cam.classroom || cam.name || "Classroom";
  document.getElementById("camStartPanel").style.display = "block";
  document.getElementById("camStartName").textContent = name;
  document.getElementById("camStartSession").value = `${name} Lecture`;
  document.getElementById("camStartMsg").textContent = "";
  
  ensurePickersInitialized();
  const res = await fetch(`${API}/api/students`);
  allStudentsCache = await res.json();
  if (camStartPicker) camStartPicker.init(allStudentsCache);

  document.getElementById("camStartPanel").scrollIntoView({ behavior: "smooth" });
}

function cancelCamStart() {
  pendingCamera = null;
  document.getElementById("camStartPanel").style.display = "none";
}

async function startFromCamera() {
  if (!pendingCamera) return;
  ensurePickersInitialized();
  const sessionName = document.getElementById("camStartSession").value.trim();
  const duration = parseInt(document.getElementById("camStartDuration").value) || 40;
  const selectedClasses = camStartPicker ? camStartPicker.getChosenList() : [];

  const msg = document.getElementById("camStartMsg");
  if (!sessionName) {
    msg.textContent = "Enter a session name (subject).";
    msg.className = "status-msg error";
    return;
  }
  if (selectedClasses.length === 0) {
    msg.textContent = "Please filter and pick at least one division above (e.g. MECH_SY_A, MECH_TY_C).";
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
      classes: selectedClasses
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

// ---------- Image Modal ----------
function openImageModal(src, captionText) {
  const modal = document.getElementById("imageModal");
  const img = document.getElementById("imageModalImg");
  const caption = document.getElementById("imageModalCaption");
  
  if (!modal || !img) return;
  
  img.src = src;
  caption.textContent = captionText || "";
  
  // Force a tiny delay so the transition triggers
  setTimeout(() => {
    modal.classList.add("show");
  }, 10);
  
  // Close on Escape key
  document.addEventListener("keydown", handleModalEsc);
}

function closeImageModal() {
  const modal = document.getElementById("imageModal");
  if (modal) {
    modal.classList.remove("show");
    // Clear image source after animation ends to free memory
    setTimeout(() => {
      document.getElementById("imageModalImg").src = "";
    }, 300);
  }
  document.removeEventListener("keydown", handleModalEsc);
}

function handleModalEsc(e) {
  if (e.key === "Escape") closeImageModal();
}

// ---------- Attendance Requests (Admin) ----------
let cachedRequests = [];

async function loadRequests() {
  try {
    const res = await fetch(`${API}/api/attendance/requests`);
    const reqs = await res.json();
    cachedRequests = reqs;
    const body = document.getElementById("requestsTableBody");
    
    if (!reqs.length) {
      body.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:24px; color:var(--muted);">No pending requests.</td></tr>`;
      return;
    }
    
    body.innerHTML = reqs.map(r => {
      let statusBadge = `<span class="badge pending">Pending</span>`;
      if (r.status === 'approved') statusBadge = `<span class="badge done">Approved</span>`;
      if (r.status === 'rejected') statusBadge = `<span class="badge error">Rejected</span>`;
      
      let actions = `<span class="muted-inline">Reviewed</span>`;
      if (r.status === 'pending') {
        actions = `
          <button class="btn btn-sm" style="padding:4px 10px; margin-right:4px;" onclick="openVerifyModal('${r._id}')">Approve</button>
          <button class="btn secondary btn-sm" style="padding:4px 10px;" onclick="reviewRequest('${r._id}', 'rejected')">Reject</button>
        `;
      }
      
      return `
        <tr>
          <td>${r.date}</td>
          <td>${r.session}</td>
          <td>${r.prn}</td>
          <td><strong>${r.student_name}</strong></td>
          <td>${r.reason}</td>
          <td>${statusBadge}</td>
          <td>${actions}</td>
        </tr>
      `;
    }).join("");
  } catch (e) {
    console.error("Error loading requests:", e);
  }
}

async function reviewRequest(id, status) {
  if (!confirm(`Are you sure you want to ${status.slice(0, -1)} this request?`)) return;
  
  try {
    const res = await fetch(`${API}/api/attendance/requests/${id}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status })
    });
    const data = await res.json();
    if (data.success) {
      loadRequests();
      if (typeof loadToday === "function") loadToday();
    } else {
      alert("Failed to review request: " + data.message);
    }
  } catch (e) {
    alert("Network error reviewing request.");
  }
}

let currentVerifyRequestId = null;
let currentVerifyRequest = null;
let verifyStream = null;

async function openVerifyModal(id) {
  currentVerifyRequestId = id;
  const modal = document.getElementById("verifyModal");
  const video = document.getElementById("verifyVideo");
  const msg = document.getElementById("verifyMsg");
  const captureBtn = document.getElementById("verifyCaptureBtn");
  const manualBtn = document.getElementById("verifyManualBtn");
  const cameraDot = document.getElementById("verifyCameraDot");
  const cameraStatus = document.getElementById("verifyCameraStatus");

  // Lookup request
  let req = (cachedRequests || []).find(r => r._id === id);
  if (!req) {
    try {
      const res = await fetch(`${API}/api/attendance/requests`);
      cachedRequests = await res.json();
      req = cachedRequests.find(r => r._id === id);
    } catch (e) {}
  }
  currentVerifyRequest = req;

  // Lookup student in cache or fetch
  let student = (allStudentsCache || []).find(s => String(s.prn) === String(req?.prn));
  if (!student && req?.prn) {
    try {
      const sRes = await fetch(`${API}/api/students`);
      allStudentsCache = await sRes.json();
      student = (allStudentsCache || []).find(s => String(s.prn) === String(req.prn));
    } catch (e) {}
  }

  // Populate student details
  document.getElementById("verifyStudentName").textContent = req?.student_name || student?.name || "Student";
  document.getElementById("verifyStudentPrn").textContent = req?.prn || "—";
  document.getElementById("verifyStudentSession").textContent = req?.session || "—";
  document.getElementById("verifyStudentDate").textContent = req?.date || "—";
  document.getElementById("verifyStudentReason").textContent = req?.reason || "—";

  const classBadge = document.getElementById("verifyStudentClass");
  if (student) {
    classBadge.textContent = `${student.branch || 'Branch'} · Div ${student.division || 'A'}`;
    classBadge.style.display = "inline-block";
  } else {
    classBadge.style.display = "none";
  }

  // Student Photo or Initials
  const photoImg = document.getElementById("verifyStudentPhoto");
  const initialsSpan = document.getElementById("verifyStudentInitials");
  const studentPhotoUrl = student?.photo || student?.photos?.center || "";
  if (studentPhotoUrl) {
    photoImg.src = studentPhotoUrl;
    photoImg.style.display = "block";
    initialsSpan.style.display = "none";
  } else {
    photoImg.style.display = "none";
    initialsSpan.style.display = "block";
    initialsSpan.textContent = getInitials(req?.student_name || "S");
  }

  // Display modal
  modal.style.display = "flex";
  msg.textContent = "";
  msg.className = "status-msg";
  cameraDot.style.background = "#f59e0b";
  cameraStatus.textContent = "Connecting camera...";
  captureBtn.disabled = true;
  manualBtn.disabled = false;
  document.getElementById("verifyCaptureText").textContent = "Scan Face & Verify";

  // Request webcam stream
  try {
    verifyStream = await navigator.mediaDevices.getUserMedia({ 
      video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "user" } 
    });
    video.srcObject = verifyStream;
    await video.play();
    cameraDot.style.background = "#10b981";
    cameraStatus.textContent = "Webcam Live";
    msg.textContent = "Look into the camera and click 'Scan Face & Verify', or click 'Approve Manually'.";
    msg.className = "status-msg info";
    captureBtn.disabled = false;
  } catch (err) {
    console.warn("Camera access error:", err);
    cameraDot.style.background = "#ef4444";
    cameraStatus.textContent = "Camera unavailable";
    msg.textContent = "Camera access denied or device not found. You can still approve this request using 'Approve Manually'.";
    msg.className = "status-msg error";
    captureBtn.disabled = true;
  }
}

function closeVerifyModal() {
  const modal = document.getElementById("verifyModal");
  if (modal) modal.style.display = "none";
  if (verifyStream) {
    verifyStream.getTracks().forEach(t => t.stop());
    verifyStream = null;
  }
  const video = document.getElementById("verifyVideo");
  if (video) video.srcObject = null;
  currentVerifyRequestId = null;
  currentVerifyRequest = null;
}

async function captureVerify() {
  const video = document.getElementById("verifyVideo");
  const canvas = document.getElementById("verifyCanvas");
  const msg = document.getElementById("verifyMsg");
  const captureBtn = document.getElementById("verifyCaptureBtn");
  const manualBtn = document.getElementById("verifyManualBtn");

  if (!currentVerifyRequestId) return;

  captureBtn.disabled = true;
  manualBtn.disabled = true;
  document.getElementById("verifyCaptureText").textContent = "Analyzing Face...";
  msg.textContent = "Detecting face and matching biometric signature...";
  msg.className = "status-msg info";

  canvas.width = video.videoWidth || 640;
  canvas.height = video.videoHeight || 480;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  const b64 = canvas.toDataURL("image/jpeg", 0.9).split(',')[1];

  try {
    const res = await fetch(`${API}/api/attendance/requests/${currentVerifyRequestId}/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image: b64 })
    });
    const data = await res.json();

    if (data.success) {
      msg.textContent = "✓ Face verified successfully! Attendance marked present.";
      msg.className = "status-msg success";
      setTimeout(() => {
        closeVerifyModal();
        loadRequests();
        if (typeof loadToday === "function") loadToday();
      }, 1500);
    } else {
      msg.textContent = "Verification failed: " + (data.message || "Face not recognized.");
      msg.className = "status-msg error";
      captureBtn.disabled = false;
      manualBtn.disabled = false;
      document.getElementById("verifyCaptureText").textContent = "Scan Face & Verify";
    }
  } catch (err) {
    msg.textContent = "Network error during biometric verification.";
    msg.className = "status-msg error";
    captureBtn.disabled = false;
    manualBtn.disabled = false;
    document.getElementById("verifyCaptureText").textContent = "Scan Face & Verify";
  }
}

async function approveManually() {
  if (!currentVerifyRequestId) return;
  const reqName = currentVerifyRequest?.student_name || "this student";
  if (!confirm(`Are you sure you want to manually approve attendance for ${reqName}?`)) return;

  const msg = document.getElementById("verifyMsg");
  const captureBtn = document.getElementById("verifyCaptureBtn");
  const manualBtn = document.getElementById("verifyManualBtn");

  captureBtn.disabled = true;
  manualBtn.disabled = true;
  msg.textContent = "Approving attendance manually...";
  msg.className = "status-msg info";

  try {
    const res = await fetch(`${API}/api/attendance/requests/${currentVerifyRequestId}/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image: "" })
    });
    const data = await res.json();

    if (data.success) {
      msg.textContent = "✓ Request approved manually! Attendance marked present.";
      msg.className = "status-msg success";
      setTimeout(() => {
        closeVerifyModal();
        loadRequests();
        if (typeof loadToday === "function") loadToday();
      }, 1200);
    } else {
      msg.textContent = "Failed to approve: " + data.message;
      msg.className = "status-msg error";
      captureBtn.disabled = false;
      manualBtn.disabled = false;
    }
  } catch (err) {
    msg.textContent = "Network error while approving request.";
    msg.className = "status-msg error";
    captureBtn.disabled = false;
    manualBtn.disabled = false;
  }
}

async function rejectFromModal() {
  if (!currentVerifyRequestId) return;
  const id = currentVerifyRequestId;
  closeVerifyModal();
  await reviewRequest(id, 'rejected');
}
