const API_URL = ""; // Use relative paths

let jobs = [];
let candidates = [];
let evaluations = [];
let currentEvaluationId = null;
let currentCandidateId = null;
let editingJobId = null;

document.addEventListener("DOMContentLoaded", () => {
    console.log("Hunters AI Initializing...");
    
    // Deep Reset: Clear any old "junk" tokens from previous versions
    const token = localStorage.getItem("token");
    if (token && (token.includes("{") || token === "undefined" || token === "null")) {
        console.log("Cleaning up invalid session...");
        localStorage.removeItem("token");
    }

    checkAuth();
    
    // Navigation
    document.querySelectorAll(".nav-links li").forEach(link => {
        link.addEventListener("click", (e) => {
            document.querySelectorAll(".nav-links li").forEach(l => l.classList.remove("active"));
            e.currentTarget.classList.add("active");
            
            const viewId = e.currentTarget.getAttribute("data-view");
            document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
            document.getElementById(viewId).classList.add("active");
        });
    });
});

function checkAuth() {
    try {
        const token = localStorage.getItem("token");
        const savedEmail = localStorage.getItem("rememberedEmail");
        
        const loginOverlay = document.getElementById("login-overlay");
        const loginEmailInput = document.getElementById("login-email");
        const loginRememberCheckbox = document.getElementById("login-remember");

        if (savedEmail && loginEmailInput) {
            loginEmailInput.value = savedEmail;
            if (loginRememberCheckbox) loginRememberCheckbox.checked = true;
        }

        if (!token || token === "undefined") {
            loginOverlay.classList.add("active");
        } else {
            loginOverlay.classList.remove("active");
            fetchData();
            fetchUserInfo();
        }
    } catch (err) {
        console.error("Auth check failed", err);
        document.getElementById("login-overlay").classList.add("active");
    }
}

async function authFetch(url, options = {}) {
    const token = localStorage.getItem("token");
    if (!token) {
        handleLogout();
        throw new Error("No token found");
    }
    
    const headers = {
        ...options.headers,
        "Authorization": `Bearer ${token}`
    };
    
    try {
        const response = await fetch(url, { ...options, headers });
        if (response.status === 401) {
            console.warn("Unauthorized! Clearing session...");
            handleLogout();
        }
        return response;
    } catch (err) {
        console.error("Fetch error:", err);
        throw err;
    }
}

async function fetchData() {
    try {
        const [jobsRes, candsRes, evalsRes] = await Promise.all([
            authFetch(`${API_URL}/jobs`),
            authFetch(`${API_URL}/candidates`),
            authFetch(`${API_URL}/results`)
        ]);

        jobs = await jobsRes.json();
        candidates = await candsRes.json();
        evaluations = await evalsRes.json();

        // Ensure they are arrays
        if (!Array.isArray(jobs)) jobs = [];
        if (!Array.isArray(candidates)) candidates = [];
        if (!Array.isArray(evaluations)) evaluations = [];

        updateDashboard();
        renderJobs();
        renderCandidates();
    } catch (err) {
        console.error("Failed to fetch data", err);
    }
}

function updateDashboard() {
    document.getElementById("total-jobs").innerText = jobs.length;
    document.getElementById("total-candidates").innerText = candidates.length;
    
    const accepted = evaluations.filter(e => e.decision.toLowerCase() === "shortlist").length;
    document.getElementById("total-accepted").innerText = accepted;

    const tbody = document.querySelector("#recent-candidates-table tbody");
    tbody.innerHTML = "";
    
    candidates.slice(-5).reverse().forEach(c => {
        const job = jobs.find(j => j.id === c.job_applied);
        const eval = evaluations.find(e => e.candidate_id === c.id);
        
        const score = eval ? eval.score : "-";
        const decision = eval ? eval.decision : "Pending";
        const badgeClass = eval ? decision.toLowerCase() : "pending";

        tbody.innerHTML += `
            <tr>
                <td><strong>${c.name}</strong><br><small style="color:var(--text-muted)">${c.email}</small></td>
                <td>${job ? job.job_title : "Unknown"}</td>
                <td>${score}/10</td>
                <td><span class="badge ${badgeClass}">${decision}</span></td>
                <td><button class="btn-action" onclick="viewCandidate(${c.id})">View AI Report</button></td>
            </tr>
        `;
    });
}

function renderJobs() {
    const grid = document.getElementById("jobs-grid");
    if (!grid) return;
    grid.innerHTML = "";
    if (jobs.length === 0) {
        grid.innerHTML = "<p style='grid-column: 1/-1; text-align: center; color: var(--text-muted);'>No jobs found. Create one to get started!</p>";
        return;
    }
    jobs.forEach(j => {
        grid.innerHTML += `
            <div class="job-card">
                <div class="job-card-header">
                    <h3>${j.job_title}</h3>
                    <div style="display:flex; gap:10px;">
                        <button class="btn-share edit-btn" onclick="editJob(${j.id})" title="Edit Job">
                            <i class='bx bx-pencil'></i> Edit
                        </button>
                        <button class="btn-share" onclick="copyPublicLink(${j.id})" title="Share Link">
                            <i class='bx bx-share-alt'></i>
                        </button>
                        <button class="btn-share" style="color:var(--red); border-color:var(--red);" onclick="deleteJob(${j.id})" title="Delete Job">
                            <i class='bx bx-trash'></i>
                        </button>
                    </div>
                </div>
                <div class="job-meta">
                    <p><i class='bx bx-money'></i> ${j.salary_range || 'Not specified'}</p>
                    <p><i class='bx bx-book'></i> ${j.education_level}</p>
                    <p><i class='bx bx-time'></i> ${j.min_experience} yrs min</p>
                </div>
                <div class="job-details-tags" style="margin-top: 12px; font-size: 11px; line-height: 1.4;">
                    <div style="margin-bottom: 5px;"><strong><i class='bx bx-bolt-circle'></i> Skills:</strong> ${j.required_skills}</div>
                    <div style="margin-bottom: 5px;"><strong><i class='bx bx-smile'></i> Behavioral:</strong> ${j.behavioral_skills || 'None'}</div>
                    <div style="margin-bottom: 5px;"><strong><i class='bx bx-buildings'></i> Industry:</strong> ${j.industry_experience || 'Any'}</div>
                </div>
                <div class="job-weights-badge" style="margin-top: 10px; padding: 4px 8px; background: #fff8e1; color: #b58105; border-radius: 4px; font-size: 10px; font-weight: bold; display: inline-block;">
                    AI BALANCE: ${Math.round((j.weight_experience || 0.3)*100)}% EXP / ${Math.round((j.weight_skills || 0.4)*100)}% SKILLS / ${Math.round((j.weight_behavioral || 0.2)*100)}% BEH
                </div>
                <div style="margin-top: 16px; display: flex; justify-content: flex-end;">
                    <a href="/apply.html?job_id=${j.id}" target="_blank" class="btn-apply-link">Apply Now <i class='bx bx-right-arrow-alt'></i></a>
                </div>
            </div>
        `;
    });
}

function copyPublicLink(id) {
    const link = `${window.location.origin}/apply.html?job_id=${id}`;
    navigator.clipboard.writeText(link).then(() => {
        alert("Public Application Link copied to clipboard!");
    });
}

function renderCandidates() {
    const tbody = document.querySelector("#all-candidates-table tbody");
    tbody.innerHTML = "";
    
    candidates.forEach(c => {
        const eval = evaluations.find(e => e.candidate_id === c.id);
        const score = eval ? eval.score : "-";
        const decision = eval ? eval.decision : "Pending";
        const badgeClass = eval ? decision.toLowerCase() : "pending";

        tbody.innerHTML += `
            <tr>
                <td><strong>${c.name}</strong></td>
                <td>${c.experience_years} yrs</td>
                <td>${c.expected_salary || '-'}</td>
                <td>${score}</td>
                <td><span class="badge ${badgeClass}">${decision}</span></td>
                <td>
                    <div style="display:flex; gap:8px;">
                        <button class="btn-action" onclick="viewCandidate(${c.id})">Details</button>
                        <button class="btn-action" style="color:var(--red); border-color:var(--red);" onclick="deleteCandidate(${c.id})"><i class='bx bx-trash'></i></button>
                    </div>
                </td>
            </tr>
        `;
    });
}

function viewCandidate(id) {
    const candidate = candidates.find(c => c.id === id);
    const eval = evaluations.find(e => e.candidate_id === id);
    currentCandidateId = id;
    
    document.getElementById("modal-candidate-name").innerText = `${candidate.name}'s AI Report`;
    document.getElementById("modal-candidate-phone").innerText = candidate.phone || '-';
    document.getElementById("modal-candidate-expected-salary").innerText = candidate.expected_salary || '-';
    
    if (eval) {
        currentEvaluationId = eval.id;
        document.getElementById("modal-candidate-score").innerText = eval.score;
        // set conic gradient
        document.getElementById("modal-candidate-score").style.background = `conic-gradient(var(--primary) ${eval.score * 10}%, var(--bg-dark) 0)`;
        
        document.getElementById("modal-candidate-decision").innerText = eval.decision;
        document.getElementById("modal-candidate-decision").className = `decision-badge badge ${eval.decision.toLowerCase()}`;
        
        document.getElementById("modal-candidate-reason").innerText = eval.reason;
        document.getElementById("modal-candidate-strengths").innerText = eval.strengths || "None noted.";
        document.getElementById("modal-candidate-weaknesses").innerText = eval.weaknesses || "None noted.";
        
        const qList = document.getElementById("modal-candidate-questions");
        qList.innerHTML = "";
        if (eval.suggested_interview_questions && eval.suggested_interview_questions.length > 0) {
            eval.suggested_interview_questions.forEach(q => {
                qList.innerHTML += `<li>${q}</li>`;
            });
        } else {
            qList.innerHTML = "<li>No specific questions generated.</li>";
        }
    } else {
        document.getElementById("modal-candidate-score").innerText = "0";
        document.getElementById("modal-candidate-decision").innerText = "Pending";
        document.getElementById("modal-candidate-reason").innerText = "Evaluation is currently running or failed.";
        document.getElementById("modal-candidate-strengths").innerText = "-";
        document.getElementById("modal-candidate-weaknesses").innerText = "-";
        document.getElementById("modal-candidate-questions").innerHTML = "";
    }
    
    document.getElementById("candidate-detail-modal").classList.add("active");
}

function closeModals() {
    document.querySelectorAll(".modal").forEach(m => m.classList.remove("active"));
}

function switchJobTab(event, tabId) {
    // Remove active class from all tabs
    const container = event.target.closest('.modal-content');
    container.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    container.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));

    // Add active class to clicked tab and target content
    event.target.classList.add('active');
    document.getElementById(tabId).classList.add('active');
}

function openJobModal() {
    // Only reset form, don't reset editingJobId here
    document.getElementById("job-manual-form").reset();
    const submitBtn = document.querySelector("#job-manual-form button[type='submit']");
    if (submitBtn) {
        submitBtn.innerHTML = "<i class='bx bx-save'></i> Save Job";
        submitBtn.disabled = false;
    }
    document.getElementById("job-add-modal").classList.add("active");
}

function openNewJobModal() {
    editingJobId = null; // Explicitly reset for NEW job
    openJobModal();
}

function openCandidateModal() {
    const select = document.getElementById("candidate-job-id");
    select.innerHTML = "";
    jobs.forEach(j => {
        select.innerHTML += `<option value="${j.id}">${j.job_title}</option>`;
    });
    document.getElementById("candidate-add-modal").classList.add("active");
}

async function handleJobUpload(event) {
    event.preventDefault();
    const fileInput = document.getElementById("job-file");
    const formData = new FormData();
    formData.append("file", fileInput.files[0]);

    const btn = event.submitter;
    const originalText = btn.innerHTML;
    btn.innerHTML = "<i class='bx bx-loader-alt bx-spin'></i> Processing...";
    btn.disabled = true;

    try {
        const response = await authFetch(`${API_URL}/jobs/upload`, {
            method: "POST",
            body: formData
        });
        const result = await response.json();
        alert(result.message);
        closeModals();
        fetchData();
    } catch (err) {
        alert("Failed to upload job.");
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}

async function handleJobManualCreate(event) {
    event.preventDefault();
    const payload = {
        job_title: document.getElementById("manual-job-title").value,
        job_location: document.getElementById("manual-job-location").value,
        min_experience: parseInt(document.getElementById("manual-job-exp").value),
        required_skills: document.getElementById("manual-job-skills").value,
        nice_to_have_skills: document.getElementById("manual-job-nice").value,
        education_level: document.getElementById("manual-job-edu").value,
        salary_range: document.getElementById("manual-job-salary").value,
        weight_experience: parseFloat(document.getElementById("manual-job-w-exp").value),
        weight_skills: parseFloat(document.getElementById("manual-job-w-skills").value),
        weight_education: parseFloat(document.getElementById("manual-job-w-edu").value)
    };

    const btn = event.submitter;
    const originalText = btn.innerHTML;
    btn.innerHTML = "<i class='bx bx-loader-alt bx-spin'></i> Saving...";
    btn.disabled = true;

    try {
        const response = await authFetch(`${API_URL}/jobs/`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const result = await response.json();
        alert("Job created successfully!");
        closeModals();
        fetchData();
    } catch (err) {
        alert("Failed to create job.");
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}

async function deleteCandidate(id) {
    if (!confirm("Are you sure you want to delete this candidate?")) return;
    
    try {
        const response = await authFetch(`${API_URL}/candidates/${id}`, {
            method: "DELETE"
        });
        const result = await response.json();
        fetchData(); // Refresh list
    } catch (err) {
        alert("Failed to delete candidate.");
    }
}

async function deleteAllCandidates() {
    if (!confirm("CRITICAL: Are you sure you want to delete ALL candidates and evaluations? This cannot be undone.")) return;
    
    try {
        const response = await authFetch(`${API_URL}/candidates/bulk/all`, {
            method: "DELETE"
        });
        const result = await response.json();
        alert(result.message);
        fetchData(); // Refresh list
    } catch (err) {
        alert("Failed to delete all candidates.");
    }
}

async function handleCandidateUpload(event) {
    event.preventDefault();
    const jobId = document.getElementById("candidate-job-id").value;
    const fileInput = document.getElementById("candidate-file");
    const formData = new FormData();
    formData.append("file", fileInput.files[0]);

    const btn = event.submitter;
    const originalText = btn.innerHTML;
    btn.innerHTML = "<i class='bx bx-loader-alt bx-spin'></i> Screening...";
    btn.disabled = true;

    try {
        const response = await authFetch(`${API_URL}/candidates/upload?job_id=${jobId}`, {
            method: "POST",
            body: formData
        });
        const result = await response.json();
        alert(result.message);
        closeModals();
        fetchData();
    } catch (err) {
        alert("Failed to upload candidates.");
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}

async function importFromSheets() {
    const btn = document.querySelector('button[onclick="importFromSheets()"]');
    const originalText = btn.innerHTML;
    btn.innerHTML = "<i class='bx bx-loader-alt bx-spin'></i> Importing...";
    btn.disabled = true;
    
    try {
        const response = await authFetch(`${API_URL}/sheets/import`, { method: "POST" });
        const result = await response.json();
        alert(result.message || "Import completed successfully!");
        fetchData(); // Refresh UI
    } catch (err) {
        alert("Import failed. Ensure GOOGLE_SHEET_URL is configured properly.");
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}

async function exportToCSV() {
    const btn = document.querySelector('button[onclick="exportToCSV()"]');
    const originalText = btn.innerHTML;
    btn.innerHTML = "<i class='bx bx-loader-alt bx-spin'></i> Exporting...";
    btn.disabled = true;
    
    try {
        const response = await authFetch(`${API_URL}/candidates/export/csv`);
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "candidate_evaluations.csv";
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
    } catch (err) {
        alert("Export failed.");
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}

async function handleLogin(event) {
    event.preventDefault();
    const email = document.getElementById("login-email").value;
    const password = document.getElementById("login-password").value;
    const btn = event.submitter;

    btn.disabled = true;
    btn.innerText = "Authenticating...";

    try {
        const formData = new FormData();
        formData.append("username", email);
        formData.append("password", password);

        const response = await fetch(`${API_URL}/auth/login`, {
            method: "POST",
            body: formData
        });

        if (!response.ok) {
            let errorMessage = "Invalid email or password";
            try {
                const errorData = await response.json();
                if (response.status === 403 && errorData.detail && errorData.detail.includes("pending approval")) {
                    throw new Error("⏳ Waiting for Admin's Approval\n\nYour company registration is pending administrator approval. We'll notify you as soon as you're approved!");
                }
                errorMessage = errorData.detail || "Login failed";
            } catch (e) {
                if (e.message.includes("pending approval")) throw e;
            }
            throw new Error(errorMessage);
        }

        const data = await response.json();
        
        // Remember me logic
        if (document.getElementById("login-remember").checked) {
            localStorage.setItem("rememberedEmail", email);
        } else {
            localStorage.removeItem("rememberedEmail");
        }

        localStorage.setItem("token", data.access_token);
        
        // Check if user is company or admin and route accordingly
        try {
            const userResponse = await fetch(`${API_URL}/auth/me`, {
                headers: { 'Authorization': `Bearer ${data.access_token}` }
            });
            
            if (userResponse.ok) {
                const user = await userResponse.json();
                
                if (user.is_admin) {
                    // Admin user - go to admin dashboard
                    checkAuth();
                } else if (user.company_id) {
                    // Company user - go to company dashboard
                    window.location.href = 'company-dashboard.html';
                } else {
                    // Regular user (shouldn't happen in current setup)
                    checkAuth();
                }
            } else {
                // Fallback to admin dashboard
                checkAuth();
            }
        } catch (error) {
            console.error('Error checking user type:', error);
            checkAuth();
        }
    } catch (err) {
        alert("Login failed: " + err.message);
    } finally {
        btn.disabled = false;
        btn.innerText = "Login to Dashboard";
    }
}

function handleLogout() {
    localStorage.removeItem("token");
    location.reload();
}

function editJob(id) {
    const job = jobs.find(j => j.id === id);
    if (!job) return;
    
    openJobModal();
    editingJobId = id; // Set AFTER opening to ensure it's not reset
    
    // Switch to manual tab for editing
    const uploadTab = document.getElementById('upload-tab');
    const manualTab = document.getElementById('manual-tab');
    const tabBtns = document.querySelectorAll('.tab-btn');
    
    tabBtns.forEach(b => b.classList.remove('active'));
    tabBtns[1].classList.add('active');
    uploadTab.classList.remove('active');
    manualTab.classList.add('active');

    // Fill form
    document.getElementById("manual-job-title").value = job.job_title;
    document.getElementById("manual-job-location").value = job.job_location || '';
    document.getElementById("manual-job-exp").value = job.min_experience;
    document.getElementById("manual-job-desc").value = job.job_description || '';
    document.getElementById("manual-job-skills").value = job.required_skills;
    document.getElementById("manual-job-nice").value = job.nice_to_have_skills || '';
    document.getElementById("manual-job-edu").value = job.education_level;
    document.getElementById("manual-job-salary").value = job.salary_range || '';
    document.getElementById("manual-job-behavioral").value = job.behavioral_skills || '';
    document.getElementById("manual-job-industry").value = job.industry_experience || '';
    document.getElementById("manual-job-w-exp").value = job.weight_experience;
    document.getElementById("manual-job-w-skills").value = job.weight_skills;
    document.getElementById("manual-job-w-edu").value = job.weight_education;
    document.getElementById("manual-job-w-behavioral").value = job.weight_behavioral || 0.2;

    // Change button text
    const submitBtn = document.querySelector("#job-manual-form button[type='submit']");
    submitBtn.innerHTML = "<i class='bx bx-save'></i> Update Job Description";
}

async function handleJobManualCreate(event) {
    if (event) event.preventDefault();
    console.log("Diagnostic: Starting Job Save...");

    const safeGet = (id) => {
        const el = document.getElementById(id);
        if (!el) {
            console.error(`Diagnostic: Missing element ID: ${id}`);
            return "";
        }
        return el.value;
    };

    try {
        const payload = {
            job_title: safeGet("manual-job-title"),
            job_location: safeGet("manual-job-location"),
            job_description: safeGet("manual-job-desc"),
            min_experience: parseInt(safeGet("manual-job-exp")) || 0,
            required_skills: safeGet("manual-job-skills"),
            nice_to_have_skills: safeGet("manual-job-nice"),
            education_level: safeGet("manual-job-edu"),
            salary_range: safeGet("manual-job-salary"),
            behavioral_skills: safeGet("manual-job-behavioral"),
            industry_experience: safeGet("manual-job-industry"),
            weight_experience: parseFloat(safeGet("manual-job-w-exp")) || 0.3,
            weight_skills: parseFloat(safeGet("manual-job-w-skills")) || 0.4,
            weight_education: parseFloat(safeGet("manual-job-w-edu")) || 0.1,
            weight_behavioral: parseFloat(safeGet("manual-job-w-behavioral")) || 0.2
        };

        if (!payload.job_title) {
            alert("Error: Job Title is required!");
            return;
        }

        const url = editingJobId ? `/jobs/${editingJobId}` : `/jobs`;
        const method = editingJobId ? "PUT" : "POST";

        const submitBtn = document.querySelector("#job-manual-form button[type='submit']");
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.innerHTML = "<i class='bx bx-loader-alt bx-spin'></i> Saving...";
        }

        console.log("Diagnostic: Sending Payload", method, url, payload);

        const response = await authFetch(url, {
            method: method,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        if (response.ok) {
            alert(editingJobId ? "Job Updated Successfully!" : "Job Created Successfully!");
            location.reload();
        } else {
            const errorData = await response.json().catch(() => ({}));
            alert(`Server Rejected Save: ${JSON.stringify(errorData.detail || "Check all fields")}`);
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.innerHTML = "<i class='bx bx-save'></i> Retry Save";
            }
        }
    } catch (err) {
        console.error("Diagnostic: Crash in handleJobManualCreate", err);
        alert(`Script Crash: ${err.message}`);
    }
}

function exportScreeningCard(id) {
    const candidate = candidates.find(c => c.id === id);
    const eval = evaluations.find(e => e.candidate_id === id);
    const job = jobs.find(j => j.id === candidate.job_applied);
    
    if (!eval) {
        alert("No evaluation found for this candidate.");
        return;
    }

    const printWindow = window.open('', '_blank');
    const html = `
        <html>
        <head>
            <title>Screening Card - ${candidate.name}</title>
            <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&display=swap" rel="stylesheet">
            <style>
                body { font-family: 'Outfit', sans-serif; padding: 40px; color: #1e293b; background: white; }
                .card { max-width: 800px; margin: auto; border: 2px solid #10367a; }
                .header { background: #10367a; color: white; padding: 20px; text-align: center; }
                .header h1 { margin: 0; font-size: 24px; text-transform: uppercase; letter-spacing: 2px; }
                .section-title { background: #10367a; color: white; padding: 8px 15px; font-weight: 600; display: flex; align-items: center; gap: 10px; margin-top: 20px; }
                .grid { display: grid; grid-template-columns: 200px 1fr; border-bottom: 1px solid #e2e8f0; }
                .grid div { padding: 10px 15px; border-right: 1px solid #e2e8f0; }
                .grid div:last-child { border-right: none; }
                .label { background: #f8fafc; font-weight: 600; color: #10367a; }
                .score-summary { background: #c5923b; color: white; padding: 10px; text-align: center; font-weight: bold; margin-top: 20px; }
                .decision-box { display: grid; grid-template-columns: 1fr 1fr; border: 2px solid #10367a; margin-top: 10px; }
                .decision-box div { padding: 20px; text-align: center; font-weight: bold; font-size: 20px; }
                .decision-box .label { background: white; color: #10367a; border-right: 2px solid #10367a; }
                .decision-box .value { background: #f0fff4; color: #10b981; }
                .rejection-reason { background: #df2029; color: white; padding: 10px; font-weight: bold; margin-top: 20px; text-align: center; }
                .reason-list { padding: 15px; background: #fff5f5; border: 1px solid #feb2b2; }
                .notes-section { border: 1px solid #e2e8f0; padding: 20px; min-height: 100px; margin-top: 20px; }
                @media print { .no-print { display: none; } }
            </style>
        </head>
        <body>
            <div class="no-print" style="margin-bottom: 20px; text-align: center;">
                <button onclick="window.print()" style="padding: 10px 20px; background: #10367a; color: white; border: none; border-radius: 8px; cursor: pointer;">Download / Print PDF</button>
            </div>
            <div class="card">
                <div class="header">
                    <h1>🎯 HUNTERS — CANDIDATE: "${candidate.name}" LIVE SCREENING CARD</h1>
                </div>
                
                <div class="section-title">📋 CANDIDATE INFORMATION</div>
                <div class="grid"><div class="label">Candidate Name</div><div>${candidate.name}</div></div>
                <div class="grid"><div class="label">Role Applied For</div><div>${job.job_title}</div></div>
                <div class="grid"><div class="label">Phone Number</div><div>${candidate.phone}</div></div>
                <div class="grid"><div class="label">Screening Date</div><div>${new Date().toLocaleDateString()}</div></div>
                <div class="grid"><div class="label">Years of Experience</div><div>${candidate.experience_years}</div></div>

                <div class="section-title">⚖️ COMPETENCY SCORING</div>
                <div class="grid" style="grid-template-columns: 1fr 1fr 100px;">
                    <div class="label">Metric</div><div class="label">Notes / Details</div><div class="label">Score</div>
                </div>
                <div class="grid" style="grid-template-columns: 1fr 1fr 100px;">
                    <div class="label">Experience Weight: ${job.weight_experience}</div><div>Verified against JD requirements</div><div>-</div>
                </div>
                <div class="grid" style="grid-template-columns: 1fr 1fr 100px;">
                    <div class="label">Skills Weight: ${job.weight_skills}</div><div>AI analysis of core technologies</div><div>-</div>
                </div>
                <div class="grid" style="grid-template-columns: 1fr 1fr 100px;">
                    <div class="label">Education Weight: ${job.weight_education}</div><div>Academic background alignment</div><div>-</div>
                </div>

                <div class="score-summary">🔢 SCORE SUMMARY</div>
                <div class="grid"><div class="label">Weighted AI Score</div><div style="font-size: 24px; font-weight: bold; color: #10367a;">${eval.score} / 10</div></div>

                <div class="section-title">⚙️ AUTO DECISION ENGINE</div>
                <div class="decision-box">
                    <div class="label">SCREENING DECISION</div>
                    <div class="value" style="color: ${eval.decision.toLowerCase() === 'reject' ? '#df2029' : '#10b981'}">${eval.decision.toUpperCase()}</div>
                </div>

                <div class="rejection-reason">🚩 ANALYSIS & REASONING</div>
                <div class="reason-list">
                    ${eval.reason.split('\n').map(r => `<p>🚩 ${r}</p>`).join('')}
                </div>

                <div class="section-title">📝 SCREENER NOTES & RECOMMENDATION</div>
                <div class="notes-section">
                    <strong>Strengths:</strong><br>${eval.strengths}<br><br>
                    <strong>Weaknesses:</strong><br>${eval.weaknesses}
                </div>
            </div>
        </body>
        </html>
    `;
    printWindow.document.write(html);
    printWindow.document.close();
}

async function fetchUserInfo() {
    try {
        const res = await authFetch(`${API_URL}/auth/me`);
        if (!res.ok) return;
        const user = await res.json();
        const displayEl = document.getElementById("user-display-name");
        if (displayEl) {
            displayEl.innerText = user.full_name || user.email;
        }
    } catch (err) {
        console.error("Failed to fetch user info", err);
    }
}

let isEditMode = false;
function toggleEditEvaluation() {
    isEditMode = !isEditMode;
    const scoreEl = document.getElementById("modal-candidate-score");
    const decisionEl = document.getElementById("modal-candidate-decision");
    const reasonEl = document.getElementById("modal-candidate-reason");
    const strengthsEl = document.getElementById("modal-candidate-strengths");
    const weaknessesEl = document.getElementById("modal-candidate-weaknesses");
    
    const editBtn = document.getElementById("edit-eval-btn");
    const saveBtn = document.getElementById("save-eval-btn");

    if (isEditMode) {
        scoreEl.innerHTML = `<input type="number" step="0.1" id="edit-score" value="${scoreEl.innerText}" style="width: 60px; font-size: 20px; text-align: center; border-radius: 50%;">`;
        decisionEl.innerHTML = `
            <select id="edit-decision" style="padding: 4px; border-radius: 8px;">
                <option value="Shortlist" ${decisionEl.innerText === 'Shortlist' ? 'selected' : ''}>Shortlist</option>
                <option value="Maybe" ${decisionEl.innerText === 'Maybe' ? 'selected' : ''}>Maybe</option>
                <option value="Reject" ${decisionEl.innerText === 'Reject' ? 'selected' : ''}>Reject</option>
            </select>
        `;
        reasonEl.innerHTML = `<textarea id="edit-reason" style="width: 100%; height: 100px; padding: 10px; border-radius: 10px; border: 1px solid var(--border);">${reasonEl.innerText}</textarea>`;
        strengthsEl.innerHTML = `<textarea id="edit-strengths" style="width: 100%; height: 80px; padding: 10px; border-radius: 10px; border: 1px solid var(--border);">${strengthsEl.innerText}</textarea>`;
        weaknessesEl.innerHTML = `<textarea id="edit-weaknesses" style="width: 100%; height: 80px; padding: 10px; border-radius: 10px; border: 1px solid var(--border);">${weaknessesEl.innerText}</textarea>`;
        
        editBtn.innerHTML = "<i class='bx bx-x'></i> Cancel";
        saveBtn.style.display = "block";
    } else {
        location.reload(); // Simplest way to reset the UI state
    }
}

async function saveManualEvaluation() {
    if (!currentEvaluationId) return;
    
    const payload = {
        score: parseFloat(document.getElementById("edit-score").value),
        decision: document.getElementById("edit-decision").value,
        reason: document.getElementById("edit-reason").value,
        strengths: document.getElementById("edit-strengths").value,
        weaknesses: document.getElementById("edit-weaknesses").value,
        suggested_interview_questions: evaluations.find(e => e.id === currentEvaluationId).suggested_interview_questions
    };

    try {
        const response = await authFetch(`${API_URL}/evaluations/update/${currentEvaluationId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        if (response.ok) {
            alert("Report updated successfully!");
            location.reload();
        } else {
            alert("Failed to update report.");
        }
    } catch (err) {
        alert("Error saving changes.");
    }
}

async function deleteJob(id) {
    if (!confirm("Are you sure you want to delete this job? This will also delete all associated candidates and evaluations.")) return;

    try {
        const response = await authFetch(`/jobs/${id}`, {
            method: "DELETE"
        });

        if (response.ok) {
            alert("Job deleted successfully!");
            location.reload();
        } else {
            alert("Failed to delete job.");
        }
    } catch (err) {
        console.error("Delete job error:", err);
        alert("Error deleting job.");
    }
}
