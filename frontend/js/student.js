const API = ""; 
let idToken = null;
let currentStudent = null;
let currentCourseName = "";

// ---------- Date Scroller Logic ----------
let currentDate = new Date(); // Start with today

function generateDateScroller() {
  const container = document.getElementById("dateScroller");
  let html = "";
  // Generate a week around the current date
  for (let i = -3; i <= 3; i++) {
    const d = new Date(currentDate);
    d.setDate(d.getDate() + i);
    const dayStr = d.toLocaleDateString('en-US', { weekday: 'short' });
    const num = d.getDate();
    const isToday = i === 0 ? "active" : "";
    const isoDate = d.toISOString().split('T')[0];
    html += `
      <div class="v-date-item ${isToday}" onclick="selectDate('${isoDate}')">
        <div class="v-date-day">${dayStr}</div>
        <div class="v-date-num">${num}</div>
      </div>
    `;
  }
  container.innerHTML = html;
}

function selectDate(isoDate) {
  currentDate = new Date(isoDate);
  generateDateScroller();
  loadLectures(isoDate);
}

// ---------- Navigation Logic ----------
function switchView(mainView, subTab = null) {
  document.querySelectorAll(".view").forEach(v => v.classList.add("hidden"));
  document.getElementById(`view-${mainView}`).classList.remove("hidden");
  
  document.querySelectorAll("#globalNav .v-nav-item").forEach(b => b.classList.remove("active"));
  const navBtn = document.querySelector(`#globalNav .v-nav-item[data-main="${mainView}"]`);
  if (navBtn) navBtn.classList.add("active");
  
  const topbar = document.getElementById("topbar");
  const topTitle = document.getElementById("topTitle");
  const backBtn = document.getElementById("backBtn");
  
  if (mainView === "home") {
    topbar.classList.add("hidden");
    loadHome();
  } else {
    topbar.classList.remove("hidden");
    backBtn.style.opacity = "1"; 
    backBtn.style.pointerEvents = "auto";
    
    if (mainView === "planning") {
      topTitle.textContent = "Academic Planning";
      if (subTab) switchPlanningTab(subTab);
      else switchPlanningTab('lectures'); 
    } else if (mainView === "profile") {
      topTitle.textContent = "Profile";
      loadProfile();
    } else if (mainView === "notifications") {
      topTitle.textContent = "Notifications";
      loadNotifications();
    } else if (mainView === "about") {
      topTitle.textContent = "About Institute";
    }
  }
}

function goBack() {
  // If we are in the Session List view, go back to Courses
  if (!document.getElementById("planning-sessions").classList.contains("hidden")) {
      switchPlanningTab('courses');
      return;
  }
  switchView('home');
}

function switchPlanningTab(tab) {
  document.getElementById("planning-lectures").classList.add("hidden");
  document.getElementById("planning-courses").classList.add("hidden");
  document.getElementById("planning-request").classList.add("hidden");
  document.getElementById("planning-sessions").classList.add("hidden");
  
  document.getElementById(`planning-${tab}`).classList.remove("hidden");
  
  const backBtn = document.getElementById("backBtn");
  const topTitle = document.getElementById("topTitle");
  
  if (tab === "sessions") {
      backBtn.style.opacity = "1";
      backBtn.style.pointerEvents = "auto";
      topTitle.textContent = "Session List";
      document.getElementById("planningTopTabs").style.display = "none";
  } else {
      backBtn.style.opacity = "1";
      backBtn.style.pointerEvents = "auto";
      topTitle.textContent = "Academic Planning";
      document.getElementById("planningTopTabs").style.display = "flex";
      document.querySelectorAll(".v-ct-tab[data-tab]").forEach(b => b.classList.remove("active"));
      const tabBtn = document.querySelector(`.v-ct-tab[data-tab="${tab}"]`);
      if (tabBtn) tabBtn.classList.add("active");
  }
  
  if (tab === "lectures") {
    generateDateScroller();
    loadLectures(currentDate.toISOString().split('T')[0]);
  }
  if (tab === "courses") loadCourses();
  if (tab === "request") loadRequests();
}

function openSessionList(courseName) {
    currentCourseName = courseName;
    document.getElementById("sessionCourseName").textContent = courseName;
    switchPlanningTab('sessions');
    loadSessions(courseName);
}

// ---------- Initialization & Firebase Auth ----------
async function init() {
  try {
    const res = await fetch(`${API}/api/firebase/config`, {
      headers: { "Bypass-Tunnel-Reminder": "true" }
    });
    const config = await res.json();
    if (!config.apiKey) return;
    
    firebase.initializeApp(config);
    const uiConfig = {
      callbacks: {
        signInSuccessWithAuthResult: () => false,
        uiShown: () => { document.getElementById('authErrorMsg').style.display = 'none'; }
      },
      signInFlow: 'popup',
      signInOptions: [firebase.auth.EmailAuthProvider.PROVIDER_ID, firebase.auth.GoogleAuthProvider.PROVIDER_ID]
    };
    const ui = new firebaseui.auth.AuthUI(firebase.auth());
    ui.start('#firebaseui-auth-container', uiConfig);
    
    firebase.auth().onAuthStateChanged(async (user) => {
      if (user) {
        idToken = await user.getIdToken();
        await linkStudentAccount();
      } else {
        idToken = null;
        document.getElementById("authScreen").style.display = "flex";
        document.getElementById("topbar").classList.add("hidden");
        document.getElementById("mainContent").classList.add("hidden");
        document.getElementById("globalNav").classList.add("hidden");
      }
    });
  } catch (e) {
    console.error(e);
  }
}

async function linkStudentAccount() {
  try {
    const res = await fetch(`${API}/api/student/link`, {
      method: "POST", headers: { "Authorization": `Bearer ${idToken}`, "Bypass-Tunnel-Reminder": "true" }
    });
    const data = await res.json();
    if (res.ok && data.success) {
      document.getElementById("authScreen").style.display = "none";
      document.getElementById("mainContent").classList.remove("hidden");
      document.getElementById("globalNav").classList.remove("hidden");
      
      switchView('home');
    } else {
      document.getElementById("authErrorMsg").textContent = data.message;
      document.getElementById("authErrorMsg").style.display = "block";
      firebase.auth().signOut();
    }
  } catch (e) {
    firebase.auth().signOut();
  }
}

function signOut() {
  firebase.auth().signOut();
}

async function fetchWithAuth(url, options = {}) {
  const headers = { ...options.headers, "Authorization": `Bearer ${idToken}`, "Bypass-Tunnel-Reminder": "true" };
  const res = await fetch(url, { ...options, headers });
  if (res.status === 401 || res.status === 403) signOut();
  return res;
}

// ---------- Data Loaders ----------

async function loadHome() {
  const res = await fetchWithAuth(`${API}/api/student/profile`);
  const p = await res.json();
  
  // Greeting with first name
  const firstName = (p.name || "STUDENT").trim().split(/\s+/)[0].toUpperCase();
  const hiEl = document.getElementById("homeHiName");
  if (hiEl) hiEl.textContent = `HI ${firstName} !`;

  document.getElementById("homeName").textContent = p.name;
  document.getElementById("homePRN").textContent = p.prn;

  // Department, Semester, Division
  const branchEl = document.getElementById("homeBranch");
  if (branchEl) branchEl.textContent = p.branch || "N/A";

  const semEl = document.getElementById("homeSem");
  if (semEl) semEl.textContent = p.semester || "N/A";

  const divEl = document.getElementById("homeDiv");
  if (divEl) divEl.textContent = p.division ? `Div ${p.division}` : "N/A";

  const acadEl = document.getElementById("homeAcadYear");
  if (acadEl && p.academic_year) {
    acadEl.textContent = p.academic_year.includes("202") ? p.academic_year : `${p.academic_year} · 2026-27`;
  }
  
  if (p.photo) document.getElementById("homeAvatar").src = p.photo;
}

async function loadLectures(dateIso) {
  const res = await fetchWithAuth(`${API}/api/student/attendance?date=${dateIso}`);
  const history = await res.json();
  
  const reqRes = await fetchWithAuth(`${API}/api/student/attendance/requests`);
  const requests = await reqRes.json();
  const dateReqs = requests.filter(r => r.date === dateIso);
  
  const container = document.getElementById("lecturesList");
  
  if (!history.length && !dateReqs.length) {
    container.innerHTML = `
      <div class="v-lecture-card absent">
        <div class="v-lec-header">
          <span class="v-lec-time">Whole Day</span>
          <span class="v-lec-status">No Records</span>
        </div>
        <h4 class="v-lec-subject">No lectures recorded</h4>
        <div class="v-lec-prof">No attendance marked for today.</div>
      </div>
    `;
    return;
  }
  
  let html = "";
  history.forEach(r => {
    const isPresent = (r.status === "present" || !r.status);
    const statusClass = isPresent ? "present" : "absent";
    const statusText = isPresent ? "Present" : "Absent";
    const statusIcon = isPresent 
      ? `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>`
      : `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>`;

    const actionBtn = !isPresent 
      ? `<button class="v-btn" style="margin-top:12px; padding:6px 12px; font-size:12px; width:auto;" onclick="quickRequest(this, '${r.date}', '${r.session}')">Request Manual Scan</button>`
      : '';

    html += `
      <div class="v-lecture-card ${statusClass}">
        <div class="v-lec-header">
          <span class="v-lec-time">${r.time}</span>
          <span class="v-lec-status">
            ${statusIcon}
            ${statusText}
          </span>
        </div>
        <h4 class="v-lec-subject">${r.session}</h4>
        <div class="v-lec-code">${isPresent ? 'Auto-verified by CampusVision' : 'Missed class / Not scanned'}</div>
        <div class="v-lec-prof">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>
          Smart Attendance
        </div>
        <div class="v-lec-type">Regular | Theory | Session</div>
        ${actionBtn}
      </div>
    `;
  });
  
  dateReqs.forEach(r => {
    if (r.status === 'pending') {
      html += `
        <div class="v-lecture-card pending">
          <div class="v-lec-header">
            <span class="v-lec-time">${r.date}</span>
            <span class="v-lec-status">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
              Not Yet Updated
            </span>
          </div>
          <h4 class="v-lec-subject">${r.session}</h4>
          <div class="v-lec-code">Manual Request Raised</div>
          <div class="v-lec-prof">Reason: ${r.reason}</div>
          <div class="v-lec-type">Pending Admin Approval</div>
        </div>
      `;
    }
  });
  
  container.innerHTML = html;
}

async function loadCourses() {
  const res = await fetchWithAuth(`${API}/api/student/attendance/summary`);
  const data = await res.json();
  
  const present = data.total_attended || 0;
  const absent = data.total_absent || 0;
  const assumedTotal = present + absent === 0 ? 1 : present + absent;
  const pct = Math.round((present / assumedTotal) * 100);
  
  document.getElementById("attendanceChart").style.setProperty("--v-chart-pct", `${pct}%`);
  document.getElementById("statPresent").textContent = `${present} / ${assumedTotal} | ${pct}%`;
  document.getElementById("statAbsent").textContent = `${absent} / ${assumedTotal} | ${100-pct}%`;
  document.getElementById("avgAttText").textContent = `${pct}%`;
  
  const container = document.getElementById("coursesList");
  let html = "";
  
  const subjects = Object.entries(data.subject_breakdown || {});
  subjects.forEach(([name, stats]) => {
    // stats is an object: {present: 3, total: 4}
    const subjPresent = stats.present || 0;
    const subjTotal = stats.total || 0;
    const coursePct = subjTotal === 0 ? 0 : Math.round((subjPresent / subjTotal) * 100);

    html += `
      <div class="v-course-card" onclick="openSessionList('${name}')">
        <h4>${name}</h4>
        <div class="v-course-meta">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path></svg>
          Theory | Regular
        </div>
        <div class="v-course-att">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>
          Attendance: ${subjPresent} / ${subjTotal} | ${coursePct}%
        </div>
      </div>
    `;
  });
  
  if (!subjects.length) {
    html = `<p style="text-align:center; color:var(--v-muted); margin-top:20px;">No courses found. Go attend some classes!</p>`;
  }
  
  container.innerHTML = html;
}

// Fetch all attendance (no date filter) and show for specific course
async function loadSessions(courseName) {
  const res = await fetchWithAuth(`${API}/api/student/attendance`);
  const allHistory = await res.json();
  
  const courseHistory = allHistory.filter(r => r.session === courseName);
  const container = document.getElementById("sessionsList");
  
  if (!courseHistory.length) {
      container.innerHTML = `<p style="color:var(--v-muted); font-size:13px;">No recorded attendance for this course yet.</p>`;
      return;
  }
  
  let html = "";
  courseHistory.forEach(r => {
      // Date parsing for visual style "15th, Jul, 2026"
      const d = new Date(r.date);
      const day = d.getDate();
      const suffix = (day % 10 === 1 && day !== 11) ? 'st' : (day % 10 === 2 && day !== 12) ? 'nd' : (day % 10 === 3 && day !== 13) ? 'rd' : 'th';
      const formattedDate = `${day}${suffix}, ${d.toLocaleDateString('en-US', {month: 'short'})}, ${d.getFullYear()}`;
      
      const isPresent = (r.status === "present" || !r.status);
      const statusClass = isPresent ? "present" : "absent";
      const statusText = isPresent ? "Present" : "Absent";
      const statusIcon = isPresent 
        ? `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>`
        : `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>`;

      const actionBtn = !isPresent 
        ? `<button class="v-btn" style="margin-top:12px; padding:6px 12px; font-size:12px; width:auto;" onclick="quickRequest(this, '${r.date}', '${r.session}')">Request Manual Scan</button>`
        : '';

      html += `
      <div class="v-lecture-card ${statusClass}">
        <div class="v-lec-header">
          <span class="v-lec-time">${r.time} to --:--</span>
          <span class="v-lec-status">
            ${statusIcon}
            ${statusText}
          </span>
        </div>
        <h4 class="v-lec-subject">${formattedDate}</h4>
        <div class="v-lec-code">${isPresent ? 'Auto-verified by CampusVision' : 'Missed class / Not scanned'}</div>
        <div class="v-lec-prof">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>
          Smart Attendance
        </div>
        <div class="v-lec-type">Regular | Theory | Session</div>
        ${actionBtn}
      </div>
      `;
  });
  container.innerHTML = html;
}

async function loadRequests() {
  const res = await fetchWithAuth(`${API}/api/student/attendance/requests`);
  const reqs = await res.json();
  const container = document.getElementById("myRequestsList");
  
  if (!reqs.length) {
    container.innerHTML = `<p style="color:var(--v-muted); font-size:13px;">No requests raised yet.</p>`;
    return;
  }
  
  container.innerHTML = reqs.map(r => {
    let color = 'var(--v-orange)';
    if (r.status === 'approved') color = 'var(--v-green)';
    if (r.status === 'rejected') color = '#e03131';
    
    return `
      <div class="v-course-card" style="margin-bottom:12px; cursor:default;">
        <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
          <strong style="font-size:14px;">${r.session}</strong>
          <span style="font-size:12px; font-weight:700; color:${color};">${r.status.toUpperCase()}</span>
        </div>
        <div style="font-size:12px; color:var(--v-muted); margin-bottom:8px;">Date: ${r.date}</div>
        <div style="font-size:13px;">Reason: ${r.reason}</div>
      </div>
    `;
  }).join("");
}

async function loadNotifications() {
  const res = await fetchWithAuth(`${API}/api/student/attendance/requests`);
  const reqs = await res.json();
  const container = document.getElementById("notificationsList");
  
  if (!reqs.length) {
    container.innerHTML = `<p style="color:var(--v-muted); font-size:13px; text-align:center; padding: 20px;">You have no new notifications.</p>`;
    return;
  }
  
  container.innerHTML = reqs.map(r => {
    let color = 'var(--v-orange)';
    if (r.status === 'approved') color = 'var(--v-green)';
    if (r.status === 'rejected') color = '#e03131';
    
    let msg = `Your manual request for ${r.session} on ${r.date} is pending approval.`;
    if (r.status === 'approved') {
        msg = `Your attendance for ${r.session} on ${r.date} has been Approved.`;
    } else if (r.status === 'rejected') {
        msg = `Your attendance for ${r.session} on ${r.date} was Rejected.`;
    }

    return `
      <div class="v-course-card" style="margin-bottom:12px; cursor:default; border-left: 4px solid ${color};">
        <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
          <strong style="font-size:14px; color:${color};">${r.status.toUpperCase()}</strong>
          <span style="font-size:12px; font-weight:700; color:var(--v-muted);">${r.date}</span>
        </div>
        <div style="font-size:13px; color:#1a1a1a; margin-top:8px;">${msg}</div>
      </div>
    `;
  }).join("");
}

async function submitRequest() {
  const payload = {
    date: document.getElementById("reqDate").value,
    session: document.getElementById("reqSession").value,
    reason: document.getElementById("reqReason").value
  };
  const msg = document.getElementById("reqMsg");
  
  if (!payload.date || !payload.session || !payload.reason) {
    msg.textContent = "Please fill all fields.";
    msg.style.color = "#e03131";
    return;
  }
  
  msg.textContent = "Submitting...";
  msg.style.color = "var(--v-blue)";
  
  try {
    const res = await fetchWithAuth(`${API}/api/student/attendance/request`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    msg.textContent = data.message;
    msg.style.color = data.success ? "var(--v-green)" : "#e03131";
    
    if (data.success) {
      document.getElementById("reqDate").value = "";
      document.getElementById("reqSession").value = "";
      document.getElementById("reqReason").value = "";
      loadRequests();
    }
  } catch (e) {
    msg.textContent = "Network error.";
    msg.style.color = "#e03131";
  }
}

async function quickRequest(btn, date, session) {
  btn.disabled = true;
  btn.textContent = "Submitting...";
  
  try {
    const res = await fetchWithAuth(`${API}/api/student/attendance/request`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        date: date,
        session: session,
        reason: "Missed class / Not scanned properly"
      })
    });
    const data = await res.json();
    if (data.success) {
      btn.textContent = "Requested";
      btn.style.backgroundColor = "var(--v-green)";
      btn.style.color = "white";
      // Refresh requests list in the background so it shows up in Leave Request tab
      loadRequests();
    } else {
      btn.textContent = "Failed";
      btn.style.backgroundColor = "#e03131";
    }
  } catch (e) {
    btn.textContent = "Error";
    btn.style.backgroundColor = "#e03131";
  }
}

async function loadProfile() {
  const res = await fetchWithAuth(`${API}/api/student/profile`);
  const p = await res.json();
  document.getElementById("profName").textContent = p.name;
  document.getElementById("profEmail").textContent = p.email || 'Not assigned';
  document.getElementById("profPRN").textContent = p.prn || 'N/A';
  document.getElementById("profRollNo").textContent = p.roll_no || 'N/A';
  document.getElementById("profDiv").textContent = p.division || 'N/A';
  document.getElementById("profSem").textContent = p.semester || 'N/A';
  document.getElementById("profAcadYear").textContent = p.academic_year || '2026-27';
  document.getElementById("profPhone").textContent = p.phone || 'N/A';
  document.getElementById("profBranch").textContent = p.branch || 'N/A';
  
  const photosContainer = document.getElementById("profPhotos");
  photosContainer.innerHTML = ''; // clear
  if (p.photos) {
    Object.entries(p.photos).forEach(([angle, b64]) => {
      const img = document.createElement("img");
      img.src = b64;
      img.alt = angle;
      img.style.width = "80px";
      img.style.height = "80px";
      img.style.objectFit = "cover";
      img.style.borderRadius = "8px";
      img.style.border = "1px solid var(--v-border)";
      
      const wrap = document.createElement("div");
      wrap.style.display = "flex";
      wrap.style.flexDirection = "column";
      wrap.style.alignItems = "center";
      wrap.style.gap = "4px";
      
      const label = document.createElement("span");
      label.textContent = angle.toUpperCase();
      label.style.fontSize = "10px";
      label.style.color = "var(--v-muted)";
      label.style.fontWeight = "700";
      
      wrap.appendChild(img);
      wrap.appendChild(label);
      photosContainer.appendChild(wrap);
    });
  }
}

init();
