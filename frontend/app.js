// --- VIEW MANAGEMENT ---
function showView(viewName) {
    document.querySelectorAll('.auth-view, .app-layout').forEach(v => v.style.display = 'none');
    const el = document.getElementById(`${viewName}-view`);
    el.style.display = 'flex';
    if (viewName === 'dashboard') el.style.display = 'block'; 

}function showPromptView() {
    document.getElementById('prompt-view').style.display = 'block';
    document.getElementById('generating-view').style.display = 'none';
    document.getElementById('report-viewer').style.display = 'none';
    document.getElementById('admin-table-viewer').style.display = 'none';
    document.getElementById('admin-users-viewer').style.display = 'none';

    document.getElementById('nav-generator').classList.add('active');
    document.getElementById('nav-admin-reports').classList.remove('active');
    document.getElementById('nav-admin-users').classList.remove('active');
}

function togglePassword(inputId, iconEl) {
    const input = document.getElementById(inputId);
    if (input.type === 'password') {
        input.type = 'text';
        iconEl.textContent = '🙈';
    } else {
        input.type = 'password';
        iconEl.textContent = '👁️';
    }
}

// --- API HELPER ---
async function api(endpoint, method = 'GET', body = null) {
    const token = localStorage.getItem('token');
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const res = await fetch(endpoint, { method, headers, body: body ? JSON.stringify(body) : null });
    const text = await res.text();
    if (!res.ok) {
        let errorMsg = `Error: ${res.status}`;
        try { 
            const errData = JSON.parse(text); 
            errorMsg = errData.detail || JSON.stringify(errData); 
        } catch (e) { 
            errorMsg = text.includes("Internal Server Error") ? "Backend crashed." : text.substring(0, 100); 
        }
        throw new Error(errorMsg);
    }
    try { return JSON.parse(text); } catch (e) { throw new Error("Invalid JSON response."); }
}

function formatDateAsIST(utcTimestamp) {
    // Ensure the timestamp is treated as UTC even if 'Z' suffix is missing
    var isoString = utcTimestamp;
    if (!isoString.endsWith('Z') && !isoString.includes('+')) {
        isoString = isoString + 'Z';
    }
    var date = new Date(isoString);
    return date.toLocaleString('en-IN', {
        timeZone: 'Asia/Kolkata',
        year: 'numeric',
        month: 'numeric',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: true
    });
}

// --- SECURE FILE DOWNLOAD ---
async function downloadFile(url, filename) {
    try {
        const token = localStorage.getItem('token');
        const res = await fetch(url, { headers: { 'Authorization': `Bearer ${token}` } });
        if (!res.ok) throw new Error('Download failed: Unauthorized or file missing.');
        const blob = await res.blob();
        const blobUrl = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = blobUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(blobUrl);
    } catch (err) {
        alert("Error downloading file: " + err.message);
    }
}

// --- MARKDOWN LINK FIXER ---
function linkifyHtml(html) {
    // Regex to find http/https links that are NOT already inside an <a> tag
    const urlRegex = /(https?:\/\/[^\s<]+)(?![^<]*>|[^<]*<\/a>)/g;
    return html.replace(urlRegex, '<a href="$1" target="_blank" rel="noopener noreferrer" class="auto-link">$1</a>');
}

// --- AUTH LOGIC ---
document.getElementById('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
        document.getElementById('login-error').textContent = '';
        const data = await api('/auth/login', 'POST', { email: document.getElementById('login-email').value, password: document.getElementById('login-password').value });
        localStorage.setItem('token', data.access_token);
        initDashboard();
    } catch (err) { document.getElementById('login-error').textContent = err.message; }
});

document.getElementById('register-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
        document.getElementById('reg-error').textContent = '';
        const data = await api('/auth/register', 'POST', { username: document.getElementById('reg-username').value, email: document.getElementById('reg-email').value, password: document.getElementById('reg-password').value });
        localStorage.setItem('token', data.access_token);
        initDashboard();
    } catch (err) { document.getElementById('reg-error').textContent = err.message; }
});

document.getElementById('reset-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
        document.getElementById('reset-error').textContent = '';
        await api('/auth/reset-password', 'POST', { email: document.getElementById('reset-email').value, new_password: document.getElementById('reset-new-pass').value });
        alert('Password reset! Please login.'); showView('login');
    } catch (err) { document.getElementById('reset-error').textContent = err.message; }
});

function logout() { localStorage.removeItem('token'); showView('login'); }

// --- FORMAT TOGGLE LOGIC (SINGLE SELECT) ---
function toggleFormat(btn) {
    btn.closest('.format-stack').querySelectorAll('.pill-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
}

// --- PROGRESS SIMULATION ---
const workflowSteps = [
    { name: "Analyzing Request", pct: 10 },
    { name: "Planning Report", pct: 20 },
    { name: "Researching Topic", pct: 40 },
    { name: "Analyzing Findings", pct: 60 },
    { name: "Validating Content", pct: 75 },
    { name: "Writing Report", pct: 90 }
];
let currentStep = 0;
let progressInterval = null;

function updateProgressUI(stepName, pct) {
    document.getElementById('progress-step').textContent = stepName;
    document.getElementById('progress-pct').textContent = `${pct}%`;
    document.getElementById('progress-bar-fill').style.width = `${pct}%`;
}

function startProgressSimulation() {
    currentStep = 0;
    document.getElementById('prompt-view').style.display = 'none';
    document.getElementById('generating-view').style.display = 'flex';
    
    updateProgressUI(workflowSteps[0].name, workflowSteps[0].pct);

    progressInterval = setInterval(() => {
        currentStep++;
        if (currentStep < workflowSteps.length) {
            const step = workflowSteps[currentStep];
            updateProgressUI(step.name, step.pct);
        } else {
            updateProgressUI("Finalizing Document...", 95);
            clearInterval(progressInterval);
        }
    }, 6000); 
}

function stopProgressSimulation() {
    clearInterval(progressInterval);
    document.getElementById('generating-view').style.display = 'none';
}

// --- ADMIN TABLE LOGIC ---
function viewAdminTable() {
    document.getElementById('prompt-view').style.display = 'none';
    document.getElementById('generating-view').style.display = 'none';
    document.getElementById('report-viewer').style.display = 'none';
    document.getElementById('admin-users-viewer').style.display = 'none';

    document.getElementById('admin-table-viewer').style.display = 'block';

    document.getElementById('nav-generator').classList.remove('active');
    document.getElementById('nav-admin-reports').classList.add('active');
    document.getElementById('nav-admin-users').classList.remove('active');

    var tbody = document.getElementById('admin-reports-tbody');
    tbody.innerHTML = '<tr><td colspan="3">Loading...</td></tr>';

    api('/admin/reports').then(function (reports) {
        if (reports.length === 0) {
            tbody.innerHTML = '<tr><td colspan="3">No reports generated yet.</td></tr>';
            return;
        }

        var html = '';
        for (var i = 0; i < reports.length; i++) {
            var r = reports[i];
            html += '<tr>';
            html += '<td><span class="report-link" onclick="loadReportById(' + r.id + ')">' + r.title + '</span></td>';
            html += '<td>' + r.username + '</td>';
            html += '<td>' + formatDateAsIST(r.created_at) + '</td>';
            html += '</tr>';
        }
        tbody.innerHTML = html;
    }).catch(function (err) {
        tbody.innerHTML = '<tr><td colspan="3">Error: ' + err.message + '</td></tr>';
    });
}
async function loadReportById(id) {
    document.getElementById('admin-table-viewer').style.display = 'none';
    document.getElementById('admin-users-viewer').style.display = 'none';
    document.getElementById('generating-view').style.display = 'none';
    document.getElementById('prompt-view').style.display = 'none';
    document.getElementById('report-viewer').style.display = 'block';
    document.getElementById('viewer-title').style.display = 'none';
    document.getElementById('download-buttons').innerHTML = '';

    document.getElementById('viewer-content').innerHTML = '<p style="color:#909296;">Loading report...</p>';

    try {
        const data = await api(`/reports/${id}`);
        document.getElementById('viewer-content').innerHTML = linkifyHtml(marked.parse(data.markdown));

        const btnContainer = document.getElementById('download-buttons');
        btnContainer.innerHTML = '';

        if (data.files) {
            let downloadUrl = null;
            let downloadName = null;
            if (data.files.pdf) { downloadUrl = `/download/${data.files.pdf}`; downloadName = data.files.pdf; }
            else if (data.files.docx) { downloadUrl = `/download/${data.files.docx}`; downloadName = data.files.docx; }

            if (downloadUrl) {
                btnContainer.innerHTML = `<button onclick="downloadFile('${downloadUrl}', '${downloadName}')" class="btn-dl" style="background: var(--accent-green); padding: 8px 20px; font-size: 14px; cursor: pointer; border: none; color: #fff; border-radius: 6px; font-weight: 600;">⬇ Download</button>`;
            }
        }
    } catch (err) {
        document.getElementById('viewer-content').innerHTML = '<p style="color:var(--accent-red);">Failed to load report: ' + err.message + '</p>';
    }
}
// --- DASHBOARD LOGIC ---
async function initDashboard() {
    showView('dashboard');
    try {
        currentUserData = await api('/auth/me');
        if (currentUserData.username === 'admin') {
            document.getElementById('nav-admin-reports').style.display = 'block';
            document.getElementById('nav-admin-users').style.display = 'block'; // Show Users Nav
        } else {
            document.getElementById('nav-admin-reports').style.display = 'none';
            document.getElementById('nav-admin-users').style.display = 'none';
        }
        showPromptView();
    } catch (err) { console.error(err); }
}
// Generate Report Button Click
document.getElementById('gen-btn').addEventListener('click', async () => {
    const query = document.getElementById('query-input').value;
    const btn = document.getElementById('gen-btn');
    
    const formats = [];
    document.querySelectorAll('.pill-btn.active').forEach(b => formats.push(b.getAttribute('data-format')));

    if (formats.length === 0) { alert('Please select an export format.'); return; }
    if (!query.trim()) { alert('Please enter a report topic.'); return; }

    btn.disabled = true;
    document.getElementById('download-buttons').innerHTML = '';
    
    startProgressSimulation();

    try {
        const data = await api('/generate-report', 'POST', { query, output_formats: formats });
        
        stopProgressSimulation();
        
        document.getElementById('viewer-title').style.display = 'none'; 
        // Use linkifyHtml to make bare URLs clickable
        document.getElementById('viewer-content').innerHTML = linkifyHtml(marked.parse(data.markdown));
        document.getElementById('report-viewer').style.display = 'block';
        document.getElementById('query-input').value = '';
        
        const btnContainer = document.getElementById('download-buttons');
        btnContainer.innerHTML = '';
        
        if (data.files) {
            let downloadUrl = null;
            let downloadName = null;
            if (data.files.pdf) { downloadUrl = `/download/${data.files.pdf}`; downloadName = data.files.pdf; }
            else if (data.files.md) { downloadUrl = `/download/${data.files.md}`; downloadName = data.files.md; }
            else if (data.files.docx) { downloadUrl = `/download/${data.files.docx}`; downloadName = data.files.docx; }

            if (downloadUrl) {
                btnContainer.innerHTML = `<button onclick="downloadFile('${downloadUrl}', '${downloadName}')" class="btn-dl" style="background: var(--accent-green); padding: 8px 20px; font-size: 14px; cursor: pointer; border: none; color: #fff; border-radius: 6px; font-weight: 600;">⬇ Download</button>`;
            }
        }

    } catch (err) { 
        stopProgressSimulation();
        showPromptView();
        alert("Error: " + err.message); 
    } 
    finally { btn.disabled = false; }
});

// --- AVATAR LOGIC ---
function getInitials(name) {
    if (!name) return "?";
    const parts = name.trim().split(/\s+/);
    if (parts.length >= 2) {
        return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    }
    return name.substring(0, 2).toUpperCase();
}

function renderAvatar(elementId, name, picBase64) {
    const el = document.getElementById(elementId);
    if (!el) return;
    if (picBase64) {
        el.style.backgroundImage = `url(${picBase64})`;
        el.innerText = "";
    } else {
        el.style.backgroundImage = 'none';
        el.innerText = getInitials(name);
    }
}

// --- PROFILE LOGIC ---
let currentUserData = null;

async function showProfileModal() {
    document.getElementById('profile-modal').style.display = 'flex';
    try {
        // Fetch the latest user data from /auth/me
        currentUserData = await api('/auth/me');
        
        // Populate the text
        document.getElementById('profile-name').textContent = currentUserData.username;
        document.getElementById('profile-email').textContent = currentUserData.email;
        
        // Render the Avatar (Image or Initials)
        renderAvatar('profile-avatar-lg', currentUserData.username, currentUserData.profile_picture_base64);
    } catch (err) { 
        console.error("Failed to load profile:", err); 
    }
}

function closeProfileModal() {
    document.getElementById('profile-modal').style.display = 'none';
}

async function handleProfilePicUpload(event) {
    const file = event.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = async (e) => {
        const base64 = e.target.result;
        try {
            await api('/auth/profile', 'PUT', { profile_picture_base64: base64 });
            renderAvatar('profile-avatar-lg', currentUserData.username, base64);
            alert("Profile picture updated!");
        } catch (err) { alert("Error: " + err.message); }
    };
    reader.readAsDataURL(file);
}

async function removeProfilePic() {
    try {
        // Send empty string to remove the picture
        await api('/auth/profile', 'PUT', { profile_picture_base64: "" });
        renderAvatar('profile-avatar-lg', currentUserData.username, null);
        alert("Profile picture removed.");
    } catch (err) { alert("Error: " + err.message); }
}

function viewAdminUsers() {
    document.getElementById('prompt-view').style.display = 'none';
    document.getElementById('generating-view').style.display = 'none';
    document.getElementById('report-viewer').style.display = 'none';
    document.getElementById('admin-table-viewer').style.display = 'none';

    document.getElementById('admin-users-viewer').style.display = 'block';

    document.getElementById('nav-generator').classList.remove('active');
    document.getElementById('nav-admin-reports').classList.remove('active');
    document.getElementById('nav-admin-users').classList.add('active');

    var tbody = document.getElementById('admin-users-tbody');
    tbody.innerHTML = '<tr><td colspan="4">Loading...</td></tr>';

    api('/admin/users').then(function (users) {
        if (users.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4">No users found.</td></tr>';
            return;
        }

        var html = '';
        for (var i = 0; i < users.length; i++) {
            var u = users[i];
            html += '<tr>';
            html += '<td>' + u.username + '</td>';
            html += '<td>' + u.email + '</td>';
            html += '<td>' + (u.is_admin ? 'Admin' : 'User') + '</td>';
            html += '<td>' + new Date(u.created_at).toLocaleDateString() + '</td>';
            html += '</tr>';
        }
        tbody.innerHTML = html;
    }).catch(function (err) {
        tbody.innerHTML = '<tr><td colspan="4">Error: ' + err.message + '</td></tr>';
    });
}

// --- ON LOAD CHECK AUTH ---
window.onload = () => {
    const token = localStorage.getItem('token');
    if (token && token !== 'undefined' && token !== 'null') { initDashboard(); } 
    else { localStorage.removeItem('token'); showView('login'); }
};