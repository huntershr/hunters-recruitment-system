let huntersAllJobs = [];
let huntersCurrentView = localStorage.getItem('hunters_jobs_view') || 'card';
let huntersIsAdmin = false;
let huntersPendingDeleteJobId = null;

function huntersEsc(s) {
    if (s == null || s === undefined) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function huntersProfileJobsFiltersActive() {
    const p = document.getElementById('profile');
    const tab = document.getElementById('profile-tab-Jobs');
    if (!p || !p.classList.contains('active') || !tab) return false;
    return window.getComputedStyle(tab).display !== 'none';
}

function huntersFilterEls() {
    const pf = huntersProfileJobsFiltersActive();
    const p = (id) => document.getElementById((pf ? 'profile-' : '') + id);
    return {
        search: p('job-search-input'),
        status: p('job-status-filter'),
        type: p('job-type-filter'),
        loc: p('job-loc-filter'),
        company: document.getElementById('job-company-filter'),
    };
}

function huntersRelativePosted(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    const now = new Date();
    const diff = Math.floor((now - d) / 86400000);
    if (diff <= 0) return 'Posted today';
    if (diff === 1) return 'Posted 1 day ago';
    return `Posted ${diff} days ago`;
}

function huntersJobHideSalary(job) {
    return !!(job.hide_salary || job.hideSalary);
}

function huntersSalaryDisplay(job) {
    if (huntersJobHideSalary(job)) {
        return `<span style="font-size:11px;color:#9CA3AF;font-style:italic;">Salary hidden</span>`;
    }
    const cur = job.salary_currency || 'EGP';
    if (job.salary_min != null || job.salary_max != null) {
        const a = job.salary_min != null ? Number(job.salary_min).toLocaleString() : '—';
        const b = job.salary_max != null ? Number(job.salary_max).toLocaleString() : '—';
        return `<span style="font-size:12px;font-weight:500;color:#1B2A4A;">${cur} ${a} – ${b}</span>`;
    }
    if (job.salary_range) {
        return `<span style="font-size:12px;font-weight:500;color:#1B2A4A;">${huntersEsc(job.salary_range)}</span>`;
    }
    return `<span style="font-size:12px;color:#9CA3AF;">Negotiable</span>`;
}

function huntersStatusPill(status) {
    if (status === 'Approved') {
        return `<span style="background:#E1F5EE;color:#0F6E56;padding:4px 10px;border-radius:12px;font-size:11px;font-weight:500;display:inline-flex;align-items:center;gap:4px;"><span style="width:6px;height:6px;border-radius:50%;background:#0F6E56;"></span>Approved</span>`;
    }
    if (status === 'Rejected') {
        return `<span style="background:#FCEBEB;color:#A32D2D;padding:4px 10px;border-radius:12px;font-size:11px;font-weight:500;display:inline-flex;align-items:center;gap:4px;"><span style="width:6px;height:6px;border-radius:50%;background:#A32D2D;"></span>Rejected</span>`;
    }
    return `<span style="background:#FAEEDA;color:#854F0B;padding:4px 10px;border-radius:12px;font-size:11px;font-weight:500;display:inline-flex;align-items:center;gap:4px;"><span style="width:6px;height:6px;border-radius:50%;background:#C9A84C;"></span>Pending</span>`;
}

function huntersAiScoreCell(job) {
    const score = job.ai_score;
    if (score == null || score === '') return '—';
    const n = Number(score);
    if (Number.isNaN(n)) return '—';
    if (n >= 75) return `<span style="background:#E1F5EE;color:#0F6E56;padding:4px 8px;border-radius:12px;font-size:11px;font-weight:500;">${Math.round(n)}%</span>`;
    if (n >= 50) return `<span style="background:#FAEEDA;color:#854F0B;padding:4px 8px;border-radius:12px;font-size:11px;font-weight:500;">${Math.round(n)}%</span>`;
    return `<span style="background:#FCEBEB;color:#A32D2D;padding:4px 8px;border-radius:12px;font-size:11px;font-weight:500;">${Math.round(n)}%</span>`;
}

function huntersWeightPills(job) {
    const w = job.ai_weights || {};
    const e = w.experience ?? 0;
    const sk = w.skills ?? 0;
    const ed = w.education ?? 0;
    const be = w.behavioral ?? 0;
    return `<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;">
        <span style="background:#F0F2F8;color:#1B2A4A;border-radius:20px;padding:2px 8px;font-size:10px;font-weight:500;">Exp ${e}%</span>
        <span style="background:#F0F2F8;color:#1B2A4A;border-radius:20px;padding:2px 8px;font-size:10px;font-weight:500;">Skills ${sk}%</span>
        <span style="background:#F0F2F8;color:#1B2A4A;border-radius:20px;padding:2px 8px;font-size:10px;font-weight:500;">Edu ${ed}%</span>
        <span style="background:#F0F2F8;color:#1B2A4A;border-radius:20px;padding:2px 8px;font-size:10px;font-weight:500;">Behavioral ${be}%</span>
    </div>`;
}

function huntersCompanyLogoCircle(job, size) {
    const px = size || 40;
    const comp = job.company || window.__huntersCompanyContext;
    const name = (comp && comp.company_name) || job.company_name || 'Co';
    const initials = String(name).split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase() || '?';
    const url = (comp && comp.logo_url) || job.company_logo_url;
    if (url) {
        return `<img src="${huntersEsc(url)}" alt="" style="width:${px}px;height:${px}px;border-radius:50%;object-fit:cover;border:0.5px solid rgba(0,0,0,0.06);">`;
    }
    return `<div style="width:${px}px;height:${px}px;border-radius:50%;background:#1B2A4A;color:#C9A84C;display:flex;align-items:center;justify-content:center;font-size:${px > 32 ? 14 : 11}px;font-weight:500;">${huntersEsc(initials)}</div>`;
}

function huntersCompanyName(job) {
    const comp = job.company || window.__huntersCompanyContext;
    if (comp && comp.company_name) return comp.company_name;
    if (job.company_name) return job.company_name;
    if (job.company && job.company.company_name) return job.company.company_name;
    return '—';
}

function huntersCompanyIdForLink(job) {
    const comp = job.company || window.__huntersCompanyContext;
    if (comp && comp.id) return comp.id;
    if (job.company_id) return job.company_id;
    if (job.company && job.company.id) return job.company.id;
    return null;
}

function huntersOpenPublicCompany(job) {
    const id = huntersCompanyIdForLink(job);
    if (!id) {
        showToast('Company profile unavailable', 'info');
        return;
    }
    if (typeof navigateTo === 'function') {
        window.location.hash = `public-company?id=${encodeURIComponent(id)}`;
        navigateTo('public-profile');
    } else {
        window.open(`company-public.html?id=${id}`, '_blank');
    }
}

function huntersViewJob(jobId) {
    window.open(`/job-view.html?job_id=${jobId}`, '_blank');
}

async function huntersShareJob(jobId) {
    const url = window.location.origin + '/job-view.html?job_id=' + jobId;
    try {
        await navigator.clipboard.writeText(url);
    } catch (_) {
        const ta = document.createElement('textarea');
        ta.value = url;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
    }
    if (typeof showToast === 'function') showToast('Job link copied!', 'success');
}

function huntersInitJobs(isAdmin) {
    huntersIsAdmin = isAdmin;
    huntersToggleJobView(huntersCurrentView, true);
    if (!window.__huntersDelegBound) {
        window.__huntersDelegBound = true;
        document.addEventListener('input', function (e) {
            const id = e.target && e.target.id;
            if (!id) return;
            if (
                id === 'job-search-input' ||
                id === 'profile-job-search-input' ||
                id === 'job-loc-filter' ||
                id === 'profile-job-loc-filter'
            ) {
                renderHuntersJobs();
            }
        });
        document.addEventListener('change', function (e) {
            const id = e.target && e.target.id;
            if (!id) return;
            if (
                id === 'job-status-filter' ||
                id === 'profile-job-status-filter' ||
                id === 'job-type-filter' ||
                id === 'profile-job-type-filter' ||
                id === 'job-company-filter'
            ) {
                renderHuntersJobs();
            }
        });
    }
}

function huntersToggleJobView(view, skipFade) {
    huntersCurrentView = view;
    localStorage.setItem('hunters_jobs_view', view);
    const cardBtn = document.getElementById('card-view-btn') || document.getElementById('admin-card-view-btn');
    const listBtn = document.getElementById('list-view-btn') || document.getElementById('admin-list-view-btn');

    const applyBtnStyles = () => {
        if (view === 'card') {
            if (cardBtn) {
                cardBtn.style.background = '#1B2A4A';
                cardBtn.style.color = '#FFFFFF';
            }
            if (listBtn) {
                listBtn.style.background = '#FFFFFF';
                listBtn.style.color = '#6B7280';
            }
        } else {
            if (cardBtn) {
                cardBtn.style.background = '#FFFFFF';
                cardBtn.style.color = '#6B7280';
            }
            if (listBtn) {
                listBtn.style.background = '#1B2A4A';
                listBtn.style.color = '#FFFFFF';
            }
        }
    };

    const cv = document.getElementById('jobs-card-view');
    const lv = document.getElementById('jobs-list-view');
    const fade = (el, show) => {
        if (!el || skipFade) {
            if (el) el.style.opacity = '1';
            return;
        }
        el.style.transition = 'opacity 0.15s ease';
        el.style.opacity = '0';
        setTimeout(() => {
            if (show) el.style.display = '';
            el.style.opacity = '1';
        }, 150);
    };

    applyBtnStyles();

    if (view === 'card') {
        if (lv) {
            lv.style.display = 'none';
        }
        if (cv) {
            cv.style.display = 'grid';
            fade(cv, true);
        }
    } else {
        if (cv) {
            cv.style.display = 'none';
        }
        if (lv) {
            lv.style.display = 'block';
            fade(lv, true);
        }
    }
}

function huntersOpenPublicCompanyFromCard(jobId) {
    const job = huntersAllJobs.find((j) => j.id === jobId);
    if (job) huntersOpenPublicCompany(job);
}

function huntersJobCardInner(job, opts) {
    const o = opts || {};
    const loc = job.location || '';
    const title = job.title || '';
    const cname = huntersCompanyName(job);
    const cid = huntersCompanyIdForLink(job);
    const companyClick = cid
        ? `onclick="huntersOpenPublicCompanyFromCard(${job.id});event.stopPropagation();" onkeydown="if(event.key==='Enter')huntersOpenPublicCompanyFromCard(${job.id})" tabindex="0" role="link"`
        : '';
    const statusPill = huntersStatusPill(job.status);
    const apps = job.candidates ? job.candidates.length : 0;
    const expY = job.experience_years != null ? job.experience_years : job.min_experience;
    const pendingBtns =
        huntersIsAdmin && job.status === 'Pending'
            ? `<button type="button" onclick="event.stopPropagation();approveJob(${job.id})" style="background:#0F6E56;color:#fff;border:none;border-radius:7px;padding:5px 10px;font-size:11px;font-weight:500;cursor:pointer;">Approve</button>
               <button type="button" onclick="event.stopPropagation();showRejectJobForm(${job.id})" style="background:#fff;color:#CC2B2B;border:0.5px solid #CC2B2B;border-radius:7px;padding:5px 10px;font-size:11px;font-weight:500;cursor:pointer;">Reject</button>`
            : '';
    const footerExtra = o.hideEdit
        ? o.applyViaModal
            ? `<button type="button" onclick="event.stopPropagation();huntersOpenApplyModal(${job.id})" style="display:block;width:100%;text-align:center;background:#1B2A4A;color:#fff;border:none;border-radius:8px;padding:10px 14px;font-size:13px;font-weight:500;margin-top:4px;cursor:pointer;">Apply</button>`
            : `<a href="/apply.html?job_id=${job.id}" style="display:block;width:100%;text-align:center;background:#1B2A4A;color:#fff;text-decoration:none;border-radius:8px;padding:10px 14px;font-size:13px;font-weight:500;margin-top:4px;">Apply Now</a>`
        : `<div style="display:flex;gap:6px;align-items:center;">
                <button type="button" onclick="event.stopPropagation();openEditJobModal(${job.id})" style="background:#fff;color:#1B2A4A;border:0.5px solid #1B2A4A;border-radius:7px;padding:5px 12px;font-size:11px;font-weight:500;cursor:pointer;display:inline-flex;align-items:center;gap:4px;">
                    <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 013 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>Edit</button>
                <button type="button" onclick="event.stopPropagation();huntersViewJob(${job.id})" style="background:#1B2A4A;color:#fff;border:none;border-radius:7px;padding:5px 12px;font-size:11px;font-weight:500;cursor:pointer;display:inline-flex;align-items:center;gap:4px;">
                    <svg width="12" height="12" fill="none" stroke="#fff" stroke-width="2" viewBox="0 0 24 24"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>View</button>
                <button type="button" onclick="event.stopPropagation();huntersShareJob(${job.id})" title="Copy share link" style="background:#fff;color:#C9A84C;border:0.5px solid #C9A84C;border-radius:7px;padding:5px 8px;font-size:11px;cursor:pointer;display:inline-flex;align-items:center;">
                    <svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg></button>
                ${pendingBtns}
           </div>`;

    return `
        <div style="display:flex;justify-content:space-between;align-items:center;">
            <div style="flex-shrink:0;">${huntersCompanyLogoCircle(job, 40)}</div>
            <div style="flex-shrink:0;">${statusPill}</div>
        </div>
        <div style="font-size:15px;font-weight:500;color:#1B2A4A;margin-top:12px;line-height:1.35;max-height:2.7em;overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;">${huntersEsc(title)}</div>
        <div ${companyClick} style="font-size:12px;color:#C9A84C;margin-top:2px;cursor:${cid ? 'pointer' : 'default'};font-weight:500;${cid ? '' : 'opacity:0.7;'}" title="${huntersEsc(cname)}">${huntersEsc(cname)}</div>
        <div style="display:flex;flex-wrap:wrap;gap:12px;margin-top:10px;font-size:12px;color:#6B7280;">
            <span>📍 ${huntersEsc(loc) || '—'}</span>
            <span>💼 ${huntersEsc(job.employment_type || 'Full-time')}</span>
            <span>⏱ ${expY != null ? huntersEsc(String(expY)) + ' yrs' : '—'}</span>
        </div>
        <div style="margin-top:6px;">${huntersSalaryDisplay(job)}</div>
        ${huntersWeightPills(job)}
        <div style="display:flex;align-items:center;gap:6px;margin-top:8px;font-size:12px;color:#6B7280;">
            <svg width="12" height="12" fill="none" stroke="#C9A84C" stroke-width="2" viewBox="0 0 24 24"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg>
            <span>${apps} application${apps === 1 ? '' : 's'}</span>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:16px;padding-top:12px;border-top:0.5px solid #F3F4F6;">
            <span style="font-size:11px;color:#9CA3AF;">${huntersRelativePosted(job.created_at)}</span>
            ${footerExtra}
        </div>`;
}

function huntersListActionsHtml(job) {
    const del = huntersIsAdmin
        ? `<button type="button" onclick="event.stopPropagation();huntersToggleDeleteJob(${job.id})" style="height:28px;width:28px;padding:0;border:0.5px solid #CC2B2B;background:#fff;color:#CC2B2B;border-radius:7px;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;" title="Delete"><svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg></button>`
        : '';
    const confirm =
        huntersPendingDeleteJobId === job.id
            ? `<div style="display:flex;flex-direction:column;gap:6px;align-items:flex-end;">
                    <span style="font-size:11px;color:#A32D2D;">Delete this job?</span>
                    <div style="display:flex;gap:6px;">
                        <button type="button" onclick="event.stopPropagation();huntersToggleDeleteJob(null)" style="font-size:11px;border:0.5px solid #E5E7EB;background:#fff;border-radius:6px;padding:4px 8px;cursor:pointer;">Cancel</button>
                        <button type="button" onclick="event.stopPropagation();huntersExecuteDeleteJob(${job.id})" style="font-size:11px;border:none;background:#CC2B2B;color:#fff;border-radius:6px;padding:4px 10px;cursor:pointer;">Confirm</button>
                    </div>
               </div>`
            : `<div style="display:flex;gap:6px;align-items:center;">
                    <button type="button" onclick="event.stopPropagation();openEditJobModal(${job.id})" style="height:28px;width:28px;padding:0;border:0.5px solid #1B2A4A;background:#fff;color:#1B2A4A;border-radius:7px;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;" title="Edit"><svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 013 3L7 19l-4 1 1-4L16.5 3.5z"/></svg></button>
                    <button type="button" onclick="event.stopPropagation();huntersViewJob(${job.id})" style="height:28px;width:28px;padding:0;border:none;background:#1B2A4A;color:#fff;border-radius:7px;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;" title="View"><svg width="14" height="14" fill="none" stroke="#fff" stroke-width="2" viewBox="0 0 24 24"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button>
                    <button type="button" onclick="event.stopPropagation();huntersShareJob(${job.id})" style="height:28px;width:28px;padding:0;border:0.5px solid #C9A84C;background:#fff;color:#C9A84C;border-radius:7px;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;" title="Copy share link"><svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg></button>
                    ${del}
                    ${huntersIsAdmin && job.status === 'Pending' ? `<button type="button" onclick="event.stopPropagation();approveJob(${job.id})" style="height:28px;padding:0 8px;border:none;background:#0F6E56;color:#fff;border-radius:7px;font-size:11px;cursor:pointer;">✓</button><button type="button" onclick="event.stopPropagation();showRejectJobForm(${job.id})" style="height:28px;padding:0 8px;border:none;background:#CC2B2B;color:#fff;border-radius:7px;font-size:11px;cursor:pointer;">×</button>` : ''}
               </div>`;
    return confirm;
}

function huntersToggleDeleteJob(id) {
    huntersPendingDeleteJobId = id;
    renderHuntersJobs();
}

async function huntersExecuteDeleteJob(jobId) {
    const token = localStorage.getItem('token');
    huntersPendingDeleteJobId = null;
    try {
        const base = typeof API_URL !== 'undefined' ? API_URL : window.API_URL || window.location.origin;
        const res = await fetch(`${base}/jobs/${jobId}`, {
            method: 'DELETE',
            headers: { Authorization: 'Bearer ' + token },
        });
        if (res.ok) {
            showToast('Job deleted', 'success');
            if (typeof loadJobsList === 'function') loadJobsList();
            else location.reload();
        } else {
            const d = await res.json().catch(() => ({}));
            showToast(d.detail || 'Could not delete job', 'error');
            renderHuntersJobs();
        }
    } catch (e) {
        showToast('Delete failed', 'error');
        renderHuntersJobs();
    }
}

function renderHuntersJobs(jobsData) {
    if (jobsData && Array.isArray(jobsData)) {
        huntersAllJobs = jobsData;
    }

    const fe = huntersFilterEls();
    const searchVal = ((fe.search && fe.search.value) || '').toLowerCase();
    const statusVal = (fe.status && fe.status.value) || '';
    const typeVal = (fe.type && fe.type.value) || '';
    const locVal = ((fe.loc && fe.loc.value) || '').toLowerCase();
    const compVal = (fe.company && fe.company.value) || '';

    const filtered = huntersAllJobs.filter((job) => {
        const loc = (job.location || '').toLowerCase();
        const title = (job.title || '').toLowerCase();
        const titleMatch = title.includes(searchVal) || loc.includes(searchVal);
        const statusMatch = !statusVal || job.status === statusVal;
        const typeMatch = !typeVal || (job.employment_type || 'Full-time') === typeVal;
        const locMatch = !locVal || loc.includes(locVal);
        let compMatch = true;
        if (huntersIsAdmin && compVal) {
            const cid = job.company_id || (job.company && job.company.id);
            compMatch = String(cid || '') === String(compVal);
        }
        return titleMatch && statusMatch && typeMatch && locMatch && compMatch;
    });

    const cardContainer = document.getElementById('jobs-card-view');
    const listTbody = document.getElementById('jobs-list-tbody');
    const emptyState = document.getElementById('jobs-empty-state');
    const profileContainer = document.getElementById('profile-jobs-container');

    const mainJobs = document.getElementById('jobs') && document.getElementById('jobs').classList.contains('active');
    const profileJobs =
        document.getElementById('profile') &&
        document.getElementById('profile').classList.contains('active') &&
        huntersProfileJobsFiltersActive();

    if (mainJobs) {
        if (cardContainer) cardContainer.innerHTML = '';
        if (listTbody) listTbody.innerHTML = '';
    }
    if (profileJobs && profileContainer) profileContainer.innerHTML = '';

    const emptyProfileHtml = `
        <div style="text-align:center;padding:48px;background:#FFFFFF;border-radius:16px;border:0.5px solid rgba(0,0,0,0.06);box-shadow:0 2px 12px rgba(0,0,0,0.06);">
            <svg width="44" height="44" fill="none" stroke="#D1D5DB" stroke-width="1.5" viewBox="0 0 24 24" style="margin:0 auto;"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a2 2 0 00-2-2h-4a2 2 0 00-2 2v2"/></svg>
            <div style="font-size:14px;font-weight:500;color:#1B2A4A;margin-top:12px;">No jobs found</div>
            <div style="font-size:12px;color:#9CA3AF;margin-top:4px;">Try adjusting filters or post a new job</div>
        </div>`;

    if (filtered.length === 0) {
        if (mainJobs) {
            if (cardContainer) cardContainer.style.display = 'none';
            const lv = document.getElementById('jobs-list-view');
            if (lv) lv.style.display = 'none';
            if (emptyState) emptyState.style.display = 'block';
        }
        if (profileJobs && profileContainer) profileContainer.innerHTML = emptyProfileHtml;
        return;
    }

    if (mainJobs && emptyState) emptyState.style.display = 'none';
    if (mainJobs) {
        if (huntersCurrentView === 'card') {
            if (cardContainer) cardContainer.style.display = 'grid';
            const lv = document.getElementById('jobs-list-view');
            if (lv) lv.style.display = 'none';
        } else {
            if (cardContainer) cardContainer.style.display = 'none';
            const lv = document.getElementById('jobs-list-view');
            if (lv) lv.style.display = 'block';
        }
    }

    const cardStyle =
        'background:#FFFFFF;border-radius:16px;border:0.5px solid rgba(0,0,0,0.06);padding:20px;box-shadow:0 2px 12px rgba(0,0,0,0.06);transition:transform 0.2s ease,box-shadow 0.2s ease;display:flex;flex-direction:column;';

    filtered.forEach((job, idx) => {
        const inner = huntersJobCardInner(job, {});
        if (mainJobs && cardContainer) {
            const card = document.createElement('div');
            card.style.cssText = cardStyle;
            card.onmouseenter = () => {
                card.style.transform = 'translateY(-2px)';
                card.style.boxShadow = '0 6px 24px rgba(0,0,0,0.10)';
            };
            card.onmouseleave = () => {
                card.style.transform = 'none';
                card.style.boxShadow = '0 2px 12px rgba(0,0,0,0.06)';
            };
            card.innerHTML = inner;
            cardContainer.appendChild(card);
        }
        if (profileJobs && profileContainer) {
            const pCard = document.createElement('div');
            pCard.style.cssText = cardStyle;
            pCard.innerHTML = inner;
            profileContainer.appendChild(pCard);
        }

        if (mainJobs && listTbody) {
            const tr = document.createElement('tr');
            tr.style.cssText =
                'border-bottom:0.5px solid #F3F4F6;background:' + (idx % 2 === 0 ? '#FFFFFF' : '#FAFBFC') + ';';
            const salaryCell = huntersJobHideSalary(job)
                ? '<span style="font-size:12px;color:#9CA3AF;font-style:italic;">Hidden</span>'
                : huntersSalaryDisplay(job);
            const logo = huntersCompanyLogoCircle(job, 24);
            tr.innerHTML = `
                <td style="padding:10px 14px;font-size:12px;color:#1B2A4A;">
                    <div style="display:flex;align-items:center;gap:8px;">
                        <div style="flex-shrink:0;">${logo}</div>
                        <span style="font-weight:500;">${huntersEsc(job.title)}</span>
                    </div>
                </td>
                <td style="padding:10px 14px;">
                    <span onclick="huntersOpenPublicCompanyFromCard(${job.id})" style="color:#C9A84C;font-size:12px;font-weight:500;cursor:pointer;text-decoration:underline;">${huntersEsc(huntersCompanyName(job))}</span>
                </td>
                <td style="padding:10px 14px;font-size:12px;color:#6B7280;">${huntersEsc(job.location || '—')}</td>
                <td style="padding:10px 14px;font-size:12px;color:#6B7280;">${huntersEsc(job.employment_type || 'Full-time')}</td>
                <td style="padding:10px 14px;">${salaryCell}</td>
                <td style="padding:10px 14px;">${huntersStatusPill(job.status)}</td>
                <td style="padding:10px 14px;">${huntersAiScoreCell(job)}</td>
                <td style="padding:10px 14px;font-size:12px;color:#1B2A4A;font-weight:500;">${job.candidates ? job.candidates.length : 0}</td>
                <td style="padding:10px 14px;font-size:12px;color:#6B7280;">${(() => { const d = job.created_at ? new Date(job.created_at) : null; return d && !Number.isNaN(d.getTime()) ? d.toLocaleDateString() : '—'; })()}</td>
                <td style="padding:10px 14px;text-align:right;">${huntersListActionsHtml(job)}</td>`;
            listTbody.appendChild(tr);
        }
    });
}

function openNewJobModal() {
    document.getElementById('job-modal-title').innerText = "Post New Job";
    document.getElementById('job-modal-form').reset();
    document.getElementById('job-modal-id').value = '';
    
    if (huntersIsAdmin) {
        document.getElementById('admin-company-selector-wrapper').style.display = 'block';
        loadAdminCompaniesForDropdown();
    }
    
    gotoJobStep(1);
    document.getElementById('hunters-job-modal').style.display = 'flex';
}

function openEditJobModal(id) {
    const job = huntersAllJobs.find(j => j.id === id);
    if (!job) return;
    
    document.getElementById('job-modal-title').innerText = "Edit Job — " + job.title;
    document.getElementById('job-modal-id').value = job.id;
    
    document.getElementById('job-modal-title-input').value = job.title;
    document.getElementById('job-modal-location').value = job.location;
    document.getElementById('job-modal-type').value = job.employment_type || 'Full-time';
    document.getElementById('job-modal-experience').value = job.experience_years || 0;
    document.getElementById('job-modal-salary-min').value = job.salary_min || '';
    document.getElementById('job-modal-salary-max').value = job.salary_max || '';
    document.getElementById('job-modal-desc').value = job.description || '';
    document.getElementById('job-modal-skills').value = job.required_skills || '';
    const niceEl = document.getElementById('job-modal-nice');
    if (niceEl) niceEl.value = job.nice_to_have_skills || '';
    const behavEl = document.getElementById('job-modal-behavioral');
    if (behavEl) behavEl.value = job.behavioral_skills || '';
    
    if (huntersIsAdmin) {
        document.getElementById('admin-company-selector-wrapper').style.display = 'block';
        loadAdminCompaniesForDropdown().then(() => {
            document.getElementById('job-modal-company-id').value = job.company_id;
        });
    }

    if (job.ai_weights) {
        try {
            const w = typeof job.ai_weights === 'string' ? JSON.parse(job.ai_weights) : job.ai_weights;
            if(w.experience) document.getElementById('weight-exp').value = w.experience;
            if(w.skills) document.getElementById('weight-skills').value = w.skills;
            if(w.education) document.getElementById('weight-edu').value = w.education;
            if(w.behavioral) document.getElementById('weight-behav').value = w.behavioral;
            updateHuntersWeights();
        } catch(e) {}
    }

    const hs = document.getElementById('job-modal-hide-salary');
    if (hs) hs.checked = !!job.hide_salary;

    gotoJobStep(1);
    document.getElementById('hunters-job-modal').style.display = 'flex';
}

function closeHuntersJobModal() {
    document.getElementById('hunters-job-modal').style.display = 'none';
}

function gotoJobStep(step) {
    const aiPanel = document.getElementById('job-step-ai');
    const form    = document.getElementById('job-modal-form');
    if (aiPanel) aiPanel.style.display = 'none';
    if (form)    form.style.display    = '';
    const aiBtn = document.getElementById('btn-step-ai');
    if (aiBtn) { aiBtn.style.borderBottom = '2px solid transparent'; aiBtn.style.color = '#9CA3AF'; aiBtn.classList.remove('active'); }

    for (let i = 1; i <= 3; i++) {
        document.getElementById('job-step-' + i).style.display = (i === step) ? 'block' : 'none';
        const btn = document.getElementById('btn-step-' + i);
        if (i === step) {
            btn.style.borderBottom = '2px solid #C9A84C';
            btn.style.color = '#1B2A4A';
            btn.classList.add('active');
        } else {
            btn.style.borderBottom = '2px solid transparent';
            btn.style.color = '#9CA3AF';
            btn.classList.remove('active');
        }
    }
}

function updateHuntersWeights() {
    const exp = parseInt(document.getElementById('weight-exp').value) || 0;
    const skills = parseInt(document.getElementById('weight-skills').value) || 0;
    const edu = parseInt(document.getElementById('weight-edu').value) || 0;
    const behav = parseInt(document.getElementById('weight-behav').value) || 0;

    document.getElementById('val-weight-exp').innerText = exp + '%';
    document.getElementById('val-weight-skills').innerText = skills + '%';
    document.getElementById('val-weight-edu').innerText = edu + '%';
    document.getElementById('val-weight-behav').innerText = behav + '%';

    const total = exp + skills + edu + behav;
    const badge = document.getElementById('modal-weights-total');
    badge.innerText = "Total: " + total + "%";
    
    if (total === 100) {
        badge.style.background = '#E1F5EE';
        badge.style.color = '#0F6E56';
        badge.innerText += ' ✓';
    } else {
        badge.style.background = '#FCEBEB';
        badge.style.color = '#A32D2D';
        badge.innerText += ' ✗';
    }
}

async function loadAdminCompaniesForDropdown() {
    try {
        const res = await fetch(API_URL + '/companies/', {
            headers: {'Authorization': 'Bearer ' + localStorage.getItem('token')}
        });
        const data = await res.json();
        const sel = document.getElementById('job-modal-company-id');
        const filterSel = document.getElementById('job-company-filter');
        
        if (sel) {
            sel.innerHTML = data.map(c => `<option value="${c.id}">${c.company_name}</option>`).join('');
        }
        if (filterSel && filterSel.options.length <= 1) {
            filterSel.innerHTML = '<option value="">All Companies</option>' + data.map(c => `<option value="${c.id}">${c.company_name}</option>`).join('');
        }
    } catch(e) {}
}

async function saveHuntersJob(e) {
    e.preventDefault();
    
    const exp = parseInt(document.getElementById('weight-exp').value) || 0;
    const skills = parseInt(document.getElementById('weight-skills').value) || 0;
    const edu = parseInt(document.getElementById('weight-edu').value) || 0;
    const behav = parseInt(document.getElementById('weight-behav').value) || 0;
    
    if (exp + skills + edu + behav !== 100) {
        showToast("AI Weights must total 100%", "error");
        gotoJobStep(3);
        return;
    }

    const hideSal = document.getElementById('job-modal-hide-salary');
    const payload = {
        title: document.getElementById('job-modal-title-input').value,
        location: document.getElementById('job-modal-location').value,
        employment_type: document.getElementById('job-modal-type').value,
        experience_years: parseInt(document.getElementById('job-modal-experience').value),
        salary_min: parseInt(document.getElementById('job-modal-salary-min').value) || null,
        salary_max: parseInt(document.getElementById('job-modal-salary-max').value) || null,
        description: document.getElementById('job-modal-desc').value,
        required_skills: document.getElementById('job-modal-skills').value,
        nice_to_have_skills: (document.getElementById('job-modal-nice')?.value || '').trim() || null,
        behavioral_skills: (document.getElementById('job-modal-behavioral')?.value || '').trim() || null,
        ai_weights: { experience: exp, skills: skills, education: edu, behavioral: behav },
        hide_salary: hideSal ? !!hideSal.checked : false,
    };

    if (huntersIsAdmin) {
        payload.company_id = document.getElementById('job-modal-company-id').value;
    }

    const jobId = document.getElementById('job-modal-id').value;
    const method = jobId ? 'PUT' : 'POST';
    const base = typeof API_URL !== 'undefined' ? API_URL : window.API_URL || window.location.origin;
    const url = jobId ? `${base}/jobs/${jobId}` : `${base}/jobs`;

    try {
        const res = await fetch(url, {
            method: method,
            headers: {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + localStorage.getItem('token')
            },
            body: JSON.stringify(payload)
        });

        if (res.ok) {
            showToast("Job successfully saved!");
            closeHuntersJobModal();
            if (huntersIsAdmin && typeof loadJobsList === 'function') {
                loadJobsList();
            } else if (typeof fetchJobs === 'function') {
                fetchJobs();
            } else if (typeof loadJobsList === 'function') {
                loadJobsList();
            } else {
                location.reload();
            }
        } else {
            const data = await res.json();
            showToast("Error: " + (data.detail || 'Failed to save job'), "error");
        }
    } catch(err) {
        showToast("Error connecting to server", "error");
    }
}

function showToast(msg, type='success') {
    let t = document.getElementById('hunters-toast');
    if(!t) {
        t = document.createElement('div');
        t.id = 'hunters-toast';
        t.style.cssText = "position:fixed; bottom:24px; right:24px; background:#1B2A4A; color:white; padding:12px 24px; border-radius:8px; box-shadow:0 10px 25px rgba(0,0,0,0.2); z-index:99999; transform:translateY(100px); opacity:0; transition:all 0.3s cubic-bezier(0.68, -0.55, 0.27, 1.55); display:flex; align-items:center; gap:8px;";
        document.body.appendChild(t);
    }
    const color = type === 'error' ? '#CC2B2B' : '#C9A84C';
    t.innerHTML = `<svg width="18" height="18" fill="none" stroke="${color}" viewBox="0 0 24 24"><path stroke-width="2" d="M22 11.08V12a10 10 0 11-5.93-9.14"/><path stroke-width="2" d="M22 4L12 14.01l-3-3"/></svg> ${msg}`;
    
    setTimeout(() => {
        t.style.transform = 'translateY(0)';
        t.style.opacity = '1';
    }, 10);
    
    setTimeout(() => {
        t.style.transform = 'translateY(100px)';
        t.style.opacity = '0';
    }, 3000);
}

// B2 Tabs logic
function switchProfileTab(tabName) {
    const tabs = ['Jobs', 'Candidates', 'Analytics', 'Settings'];
    tabs.forEach(t => {
        const content = document.getElementById('profile-tab-' + t);
        if (!content) return;
        if (t === tabName) {
            content.style.display = 'block';
            content.style.opacity = '0';
            content.style.transition = 'opacity 0.15s ease';
            requestAnimationFrame(() => {
                content.style.opacity = '1';
            });
        } else {
            content.style.display = 'none';
            content.style.opacity = '1';
        }
    });

    const btns = document.querySelectorAll('.profile-tab-btn');
    btns.forEach(b => {
        if (b.innerText === tabName) {
            b.classList.add('active');
            b.style.borderBottom = '2px solid #C9A84C';
            b.style.color = '#1B2A4A';
            b.style.fontWeight = '500';
        } else {
            b.classList.remove('active');
            b.style.borderBottom = '2px solid transparent';
            b.style.color = '#9CA3AF';
            b.style.fontWeight = '400';
        }
    });

    if (tabName === 'Jobs') {
        renderHuntersJobs();
    } else if (tabName === 'Analytics') {
        renderHuntersAnalytics();
    } else if (tabName === 'Candidates') {
        renderHuntersProfileCandidates();
    }
}

function renderHuntersProfileCandidates() {
    const container = document.getElementById('profile-candidates-container');
    if (!container) return;

    let allCands = [];
    huntersAllJobs.forEach(j => {
        if (j.candidates && Array.isArray(j.candidates)) {
            j.candidates.forEach(c => {
                allCands.push({
                    ...c,
                    job_title: j.title
                });
            });
        }
    });

    if (allCands.length === 0) {
        container.innerHTML = `
            <div style="text-align:center; padding:40px;">
                <svg width="40" height="40" fill="none" stroke="#D1D5DB" stroke-width="1.5" viewBox="0 0 24 24" style="margin:0 auto;"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg>
                <div style="font-size:14px; font-weight:500; color:#1B2A4A; margin-top:12px;">No candidates yet</div>
                <div style="font-size:12px; color:#9CA3AF; margin-top:4px;">When candidates apply, they will appear here</div>
            </div>`;
        return;
    }

    let html = `
        <table style="width:100%; border-collapse:collapse; text-align:left;">
            <thead>
                <tr style="border-bottom:1px solid #E5E7EB;">
                    <th style="padding:12px 14px; font-size:11px; font-weight:500; color:#6B7280; text-transform:uppercase;">Name</th>
                    <th style="padding:12px 14px; font-size:11px; font-weight:500; color:#6B7280; text-transform:uppercase;">Job Applied</th>
                    <th style="padding:12px 14px; font-size:11px; font-weight:500; color:#6B7280; text-transform:uppercase;">Status</th>
                    <th style="padding:12px 14px; font-size:11px; font-weight:500; color:#6B7280; text-transform:uppercase;">Score</th>
                </tr>
            </thead>
            <tbody>
    `;

    allCands.forEach(c => {
        let statusBadge = `<span style="background:#FFF7E0; color:#9B6F00; padding:4px 8px; border-radius:6px; font-size:11px;">Pending</span>`;
        if (c.status === 'Shortlisted') statusBadge = `<span style="background:#E1F5EE; color:#0F6E56; padding:4px 8px; border-radius:6px; font-size:11px;">Shortlisted</span>`;
        else if (c.status === 'Rejected') statusBadge = `<span style="background:#FCEBEB; color:#A32D2D; padding:4px 8px; border-radius:6px; font-size:11px;">Rejected</span>`;
        
        let scoreHTML = '-';
        if (c.ai_score != null) {
            if (c.ai_score >= 75) scoreHTML = `<span style="color:#0F6E56; font-weight:500;">${c.ai_score}%</span>`;
            else if (c.ai_score >= 50) scoreHTML = `<span style="color:#854F0B; font-weight:500;">${c.ai_score}%</span>`;
            else scoreHTML = `<span style="color:#A32D2D; font-weight:500;">${c.ai_score}%</span>`;
        }

        html += `
            <tr style="border-bottom:1px solid #F3F4F6;">
                <td style="padding:12px 14px; font-size:13px; font-weight:500; color:#1B2A4A;">${c.full_name || c.name || 'Unknown'}</td>
                <td style="padding:12px 14px; font-size:12px; color:#4B5563;">${c.job_title}</td>
                <td style="padding:12px 14px;">${statusBadge}</td>
                <td style="padding:12px 14px; font-size:13px;">${scoreHTML}</td>
            </tr>
        `;
    });

    html += `</tbody></table>`;
    container.innerHTML = html;
}

function renderHuntersAnalytics() {
    const jobsCount = huntersAllJobs.length;
    let appsCount = 0;
    let shortlisted = 0;
    let scoreSum = 0;
    let scoreN = 0;

    huntersAllJobs.forEach((j) => {
        if (j.candidates) {
            appsCount += j.candidates.length;
            j.candidates.forEach((c) => {
                if (c.status === 'Shortlisted') shortlisted++;
                if (c.ai_score != null && !Number.isNaN(Number(c.ai_score))) {
                    scoreSum += Number(c.ai_score);
                    scoreN++;
                }
            });
        }
    });

    const avgScore = scoreN ? Math.round(scoreSum / scoreN) : 0;

    const sJobs = document.getElementById('stat-profile-jobs');
    const sApps = document.getElementById('stat-profile-apps');
    const sAvg = document.getElementById('stat-profile-avgscore');
    const sShort = document.getElementById('stat-profile-shortlisted');
    if (sJobs) sJobs.innerText = jobsCount;
    if (sApps) sApps.innerText = appsCount;
    if (sAvg) sAvg.innerText = scoreN ? avgScore + '%' : '—';
    if (sShort) sShort.innerText = shortlisted;

    const wrap = document.getElementById('profile-analytics-chart-wrap');
    if (!wrap) return;

    const maxC = Math.max(
        1,
        ...huntersAllJobs.map((j) => (j.candidates ? j.candidates.length : 0))
    );
    const bars = huntersAllJobs
        .map((j) => {
            const n = j.candidates ? j.candidates.length : 0;
            const h = Math.round((n / maxC) * 100);
            const t = huntersEsc((j.title || j.job_title || 'Job').slice(0, 18));
            return `<div style="flex:1;min-width:48px;max-width:120px;display:flex;flex-direction:column;align-items:center;gap:8px;">
                <div style="width:100%;height:120px;background:#F0F2F8;border-radius:8px;position:relative;display:flex;align-items:flex-end;overflow:hidden;padding:0 6px 0;">
                    <div style="width:100%;height:${h}%;background:linear-gradient(180deg,#C9A84C 0%,#1B2A4A 100%);border-radius:6px 6px 0 0;transition:height 0.3s ease;"></div>
                </div>
                <span style="font-size:10px;color:#6B7280;text-align:center;line-height:1.2;font-weight:500;">${t}</span>
                <span style="font-size:11px;color:#1B2A4A;font-weight:500;">${n}</span>
            </div>`;
        })
        .join('');

    wrap.innerHTML = `
        <div style="font-size:13px;font-weight:500;color:#1B2A4A;margin-bottom:12px;">Applications per job</div>
        <div style="display:flex;gap:12px;align-items:flex-end;overflow-x:auto;padding-bottom:8px;scroll-behavior:smooth;">
            ${bars || '<p style="font-size:12px;color:#9CA3AF;">No application data yet.</p>'}
        </div>`;
}

function huntersParseHashCompanyId() {
    const raw = (window.location.hash || '').replace(/^#/, '');
    if (!raw.includes('?')) return null;
    const q = raw.slice(raw.indexOf('?') + 1);
    const id = new URLSearchParams(q).get('id');
    if (id == null || id === '') return null;
    const n = parseInt(id, 10);
    return Number.isFinite(n) ? n : null;
}

function huntersOpenApplyModal(jobId) {
    const modal = document.getElementById('hunters-public-apply-modal');
    const titleEl = document.getElementById('public-apply-job-title');
    const idInput = document.getElementById('public-apply-job-id');
    const form = document.getElementById('hunters-public-apply-form');
    const job = huntersAllJobs.find((j) => j.id === jobId);
    if (!modal || !form || !idInput) {
        window.open(`/apply.html?job_id=${jobId}`, '_blank');
        return;
    }
    idInput.value = String(jobId);
    if (titleEl) titleEl.textContent = job ? job.title || 'Job' : 'Job';
    form.reset();
    idInput.value = String(jobId);
    if (titleEl) titleEl.textContent = job ? job.title || 'Job' : 'Job';
    modal.style.display = 'flex';
}

function huntersCloseApplyModal() {
    const modal = document.getElementById('hunters-public-apply-modal');
    if (modal) modal.style.display = 'none';
}

async function huntersSubmitPublicApply(ev) {
    ev.preventDefault();
    const idInput = document.getElementById('public-apply-job-id');
    const jobId = parseInt(idInput && idInput.value, 10);
    if (!Number.isFinite(jobId)) {
        showToast('Invalid job', 'error');
        return;
    }
    const name = document.getElementById('public-apply-name')?.value?.trim();
    const email = document.getElementById('public-apply-email')?.value?.trim();
    const phone = document.getElementById('public-apply-phone')?.value?.trim();
    const expectedSalary = document.getElementById('public-apply-salary')?.value?.trim();
    const fileInput = document.getElementById('public-apply-file');
    if (!name || !email || !phone || !expectedSalary) {
        showToast('Please fill in all fields', 'error');
        return;
    }
    if (!fileInput || !fileInput.files || !fileInput.files.length) {
        showToast('Please attach your CV', 'error');
        return;
    }
    const submitBtn = document.getElementById('public-apply-submit');
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.style.opacity = '0.6';
    }
    const base = typeof API_URL !== 'undefined' ? API_URL : window.API_URL || window.location.origin;
    const fd = new FormData();
    fd.append('name', name);
    fd.append('email', email);
    fd.append('phone', phone);
    fd.append('expected_salary', expectedSalary);
    fd.append('file', fileInput.files[0]);
    try {
        const res = await fetch(`${base}/public/apply/${jobId}`, { method: 'POST', body: fd });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
            showToast(data.message || 'Application submitted', 'success');
            huntersCloseApplyModal();
        } else {
            let errMsg = 'Application failed';
            const d = data.detail;
            if (typeof d === 'string') errMsg = d;
            else if (Array.isArray(d)) errMsg = d.map((x) => (x && x.msg) || JSON.stringify(x)).join(' ');
            else if (d != null) errMsg = String(d);
            showToast(errMsg, 'error');
        }
    } catch (e) {
        showToast('Could not reach server', 'error');
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.style.opacity = '1';
        }
    }
}

function renderHuntersPublicProfile(companyId) {
    const fromParam = companyId != null && companyId !== '' ? Number(companyId) : NaN;
    const fromHash = huntersParseHashCompanyId();
    let cid = Number.isFinite(fromParam) ? fromParam : fromHash;
    const comp = typeof window !== 'undefined' ? window.__huntersCurrentCompany : null;
    const ownId = comp && comp.id != null ? comp.id : null;

    if (cid != null && ownId != null && String(cid) !== String(ownId)) {
        if (typeof showToast === 'function') {
            showToast('This dashboard preview shows your own company only.', 'info');
        }
        cid = ownId;
    }
    if (cid == null && ownId != null) cid = ownId;

    const displayName =
        (comp && comp.company_name) ||
        document.getElementById('b1-company-name')?.textContent?.trim() ||
        'Company';
    const website = (comp && comp.company_website) || document.getElementById('b1-company-website')?.href || '#';
    const about =
        (comp && (comp.description || comp.bio || comp.about)) ||
        'We are building a strong team. Explore our open roles and apply with your CV.';

    const nameEl = document.getElementById('public-company-name');
    const siteEl = document.getElementById('public-company-website');
    const aboutEl = document.getElementById('public-about-text');
    const joinedEl = document.getElementById('public-joined-date');
    const countEl = document.getElementById('public-jobs-count');

    if (nameEl) nameEl.textContent = displayName;
    if (siteEl) {
        const w = website && website !== '#' ? website : '#';
        siteEl.href = w;
        siteEl.style.pointerEvents = w === '#' ? 'none' : '';
        siteEl.style.opacity = w === '#' ? '0.5' : '';
    }
    if (aboutEl) aboutEl.textContent = about;

    if (joinedEl) {
        const jc = comp && comp.created_at;
        if (jc) {
            const d = new Date(jc);
            joinedEl.textContent = !Number.isNaN(d.getTime()) ? d.toLocaleDateString(undefined, { year: 'numeric', month: 'short' }) : '—';
        } else {
            joinedEl.textContent = '—';
        }
    }

    const dataUrl = localStorage.getItem('companyLogoDataUrl');
    const initialsElem = document.getElementById('public-logo-initials');
    const imgElem = document.getElementById('public-logo-img');
    if (dataUrl) {
        if (initialsElem) initialsElem.style.display = 'none';
        if (imgElem) {
            imgElem.style.display = 'block';
            imgElem.src = dataUrl;
        }
    } else {
        const initials = displayName
            .split(/\s+/)
            .filter(Boolean)
            .slice(0, 2)
            .map((w) => w[0])
            .join('')
            .toUpperCase();
        if (initialsElem) {
            initialsElem.style.display = 'block';
            initialsElem.textContent = initials || '?';
        }
        if (imgElem) imgElem.style.display = 'none';
    }

    const openJobs = huntersAllJobs.filter((j) => j.status === 'Approved');
    if (countEl) countEl.textContent = String(openJobs.length);

    const container = document.getElementById('public-open-positions');
    if (!container) return;

    if (openJobs.length === 0) {
        container.innerHTML = `
            <div style="grid-column:1/-1;background:#F4F5FA;border-radius:12px;padding:32px;text-align:center;color:#6B7280;font-size:14px;font-weight:400;">
                No open positions at the moment. Check back later.
            </div>`;
        return;
    }

    const cardStyle =
        'background:#FFFFFF;border-radius:16px;border:0.5px solid rgba(0,0,0,0.06);padding:20px;box-shadow:0 2px 12px rgba(0,0,0,0.06);transition:transform 0.2s ease,box-shadow 0.2s ease;display:flex;flex-direction:column;';

    container.innerHTML = '';
    openJobs.forEach((job) => {
        const wrap = document.createElement('div');
        wrap.style.cssText = cardStyle;
        wrap.onmouseenter = () => {
            wrap.style.transform = 'translateY(-2px)';
            wrap.style.boxShadow = '0 6px 24px rgba(0,0,0,0.10)';
        };
        wrap.onmouseleave = () => {
            wrap.style.transform = 'none';
            wrap.style.boxShadow = '0 2px 12px rgba(0,0,0,0.06)';
        };
        wrap.innerHTML = huntersJobCardInner(job, { hideEdit: true, applyViaModal: true });
        container.appendChild(wrap);
    });
}

// ==========================================
// SECTION B: BULK UPLOAD MODAL LOGIC
// ==========================================

let bulkSelectedFiles = [];
let bulkProcessingResults = [];

function openBulkUploadModal() {
    const modal = document.getElementById('bulk-upload-modal');
    if (!modal) return;
    
    // Populate job select
    const jobSelect = document.getElementById('bulk-job-select');
    if (jobSelect) {
        jobSelect.innerHTML = '<option value="">— Screen without job match —</option>';
        const openJobs = huntersAllJobs.filter(j => j.status === 'Approved');
        openJobs.forEach(j => {
            jobSelect.innerHTML += `<option value="${j.id}">${huntersEsc(j.title)}</option>`;
        });
    }

    resetBulkUploadModal();
    modal.style.display = 'flex';
}

function closeBulkUploadModal() {
    const modal = document.getElementById('bulk-upload-modal');
    if (modal) modal.style.display = 'none';
}

function resetBulkUploadModal() {
    bulkSelectedFiles = [];
    bulkProcessingResults = [];
    document.getElementById('bulk-file-input').value = '';
    showBulkStep(1);
    renderBulkFileList();
}

function showBulkStep(step) {
    for (let i = 1; i <= 3; i++) {
        const content = document.getElementById('bulk-step-content-' + i);
        if (content) content.style.display = (i === step) ? 'block' : 'none';

        const circle = document.getElementById('bulk-step-circle-' + i);
        const label = document.getElementById('bulk-step-label-' + i);
        
        if (circle && label) {
            if (i < step) {
                // Completed
                circle.style.background = '#C9A84C';
                circle.style.color = '#FFFFFF';
                circle.innerHTML = '✓';
                label.style.color = '#1B2A4A';
                label.style.fontWeight = '500';
            } else if (i === step) {
                // Active
                circle.style.background = '#1B2A4A';
                circle.style.color = '#FFFFFF';
                circle.innerHTML = i;
                label.style.color = '#1B2A4A';
                label.style.fontWeight = '500';
            } else {
                // Idle
                circle.style.background = '#F0F2F8';
                circle.style.color = '#9CA3AF';
                circle.innerHTML = i;
                label.style.color = '#9CA3AF';
                label.style.fontWeight = '400';
            }
        }
    }
}

// Drag & Drop
function handleBulkDragOver(e) {
    e.preventDefault();
    const dropZone = document.getElementById('bulk-drop-zone');
    dropZone.style.borderColor = '#1B2A4A';
    dropZone.style.background = '#F0F4F8';
    dropZone.style.transform = 'scale(1.01)';
}

function handleBulkDragLeave(e) {
    e.preventDefault();
    const dropZone = document.getElementById('bulk-drop-zone');
    dropZone.style.borderColor = '#E5E7EB';
    dropZone.style.background = '#FAFBFC';
    dropZone.style.transform = 'scale(1)';
}

function handleBulkDrop(e) {
    e.preventDefault();
    handleBulkDragLeave(e);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        processSelectedFiles(e.dataTransfer.files);
    }
}

function handleBulkFileInput(e) {
    if (e.target.files && e.target.files.length > 0) {
        processSelectedFiles(e.target.files);
    }
}

function processSelectedFiles(files) {
    const validExtensions = ['.pdf', '.xlsx', '.xls'];
    const maxFiles = 15;
    const maxSize = 5 * 1024 * 1024; // 5MB

    for (let i = 0; i < files.length; i++) {
        const file = files[i];
        const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
        
        if (!validExtensions.includes(ext)) {
            showToast(`Skipped ${file.name}: Invalid file type`, 'error');
            continue;
        }
        if (file.size > maxSize) {
            showToast(`Skipped ${file.name}: Exceeds 5MB`, 'error');
            continue;
        }

        // Avoid duplicates by name
        if (!bulkSelectedFiles.some(f => f.name === file.name)) {
            bulkSelectedFiles.push(file);
        }
    }

    if (bulkSelectedFiles.length > maxFiles) {
        showToast(`Maximum ${maxFiles} files allowed. Trimming excess.`, 'error');
        bulkSelectedFiles = bulkSelectedFiles.slice(0, maxFiles);
    }

    renderBulkFileList();
}

function removeBulkFile(index) {
    bulkSelectedFiles.splice(index, 1);
    renderBulkFileList();
}

function renderBulkFileList() {
    const list = document.getElementById('bulk-file-list');
    const count = document.getElementById('bulk-file-count');
    const startBtn = document.getElementById('bulk-start-btn');

    list.innerHTML = '';
    count.innerText = bulkSelectedFiles.length;

    if (bulkSelectedFiles.length > 0) {
        startBtn.disabled = false;
        startBtn.style.opacity = '1';
    } else {
        startBtn.disabled = true;
        startBtn.style.opacity = '0.5';
    }

    bulkSelectedFiles.forEach((file, index) => {
        const isPdf = file.name.toLowerCase().endsWith('.pdf');
        const iconBg = isPdf ? '#FCEBEB' : '#E6F1FB';
        const iconColor = isPdf ? '#A32D2D' : '#185FA5';
        const sizeKB = Math.round(file.size / 1024);

        list.innerHTML += `
            <div style="display:flex; align-items:center; gap:10px; background:#F5F6F8; border-radius:8px; padding:8px 12px;">
                <div style="width:32px; height:32px; border-radius:7px; background:${iconBg}; display:flex; align-items:center; justify-content:center; flex-shrink:0;">
                    <svg width="16" height="16" fill="none" stroke="${iconColor}" stroke-width="1.5" viewBox="0 0 24 24">
                        <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
                        <polyline points="14 2 14 8 20 8"/>
                    </svg>
                </div>
                <div style="flex:1; min-width:0;">
                    <div style="font-size:12px; font-weight:500; color:#1B2A4A; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${file.name}</div>
                    <div style="font-size:11px; color:#9CA3AF;">${sizeKB} KB · ${isPdf ? 'PDF' : 'XLSX'}</div>
                </div>
                <button onclick="removeBulkFile(${index})" style="background:none; border:none; cursor:pointer; color:#D1D5DB; font-size:16px; line-height:1; padding:2px; transition:color 0.2s;" onmouseover="this.style.color='#A32D2D'" onmouseout="this.style.color='#D1D5DB'">✕</button>
            </div>
        `;
    });
}

// Processing Step
async function startBulkProcessing() {
    if (bulkSelectedFiles.length === 0) return;
    showBulkStep(2);

    const total = bulkSelectedFiles.length;
    document.getElementById('bulk-total-count').innerText = total;
    document.getElementById('bulk-processed-count').innerText = '0';
    document.getElementById('bulk-overall-progress').style.width = '0%';

    const list = document.getElementById('bulk-processing-list');
    list.innerHTML = '';
    bulkProcessingResults = [];

    // Create UI rows
    bulkSelectedFiles.forEach((file, index) => {
        list.innerHTML += `
            <div style="background:#F5F6F8; border-radius:10px; padding:12px 14px;">
                <div style="display:flex; align-items:center; gap:10px; margin-bottom:8px;">
                    <div style="flex:1;">
                        <div style="font-size:12px; font-weight:500; color:#1B2A4A;">${file.name}</div>
                        <div id="bulk-subtitle-${index}" style="font-size:11px; color:#9CA3AF;">Waiting…</div>
                    </div>
                    <div id="bulk-status-${index}">
                        <div style="width:10px; height:10px; border-radius:50%; background:#E5E7EB;"></div>
                    </div>
                </div>
                <div style="background:#E5E7EB; border-radius:20px; height:4px; overflow:hidden;">
                    <div id="bulk-progress-${index}" style="height:100%; background:linear-gradient(90deg, #C9A84C, #1B2A4A); border-radius:20px; width:0%; transition:width 0.3s ease;"></div>
                </div>
            </div>
        `;
    });

    const jobId = document.getElementById('bulk-job-select')?.value;
    let processed = 0;

    for (let i = 0; i < bulkSelectedFiles.length; i++) {
        const file = bulkSelectedFiles[i];
        updateFileStatus(i, 'processing', 'AI screening in progress…');
        animateProgress(i, 0, 85, 30000);

        try {
            let result;
            if (file.name.toLowerCase().endsWith('.pdf')) {
                result = await screenSingleCandidate(file, jobId);
                result.sourceFile = file.name;
                bulkProcessingResults.push(result);
            } else {
                const candidates = await parseExcelFile(file);
                for (let c of candidates) {
                    c.sourceFile = file.name;
                    c.score = null;
                    c.decision = 'Imported';
                    bulkProcessingResults.push(c);
                }
                result = { score: null, name: `${candidates.length} rows from ${file.name}` };
            }
            
            updateFileStatus(i, 'complete', `Complete`, result);
            animateProgress(i, 90, 100, 300);
        } catch (err) {
            updateFileStatus(i, 'error', `Failed — ${err.message}`);
            document.getElementById(`bulk-progress-${i}`).style.background = '#CC2B2B';
            animateProgress(i, 90, 100, 300);
            bulkProcessingResults.push({ error: true, name: file.name, sourceFile: file.name, message: err.message });
        }

        processed++;
        updateOverallProgress(processed, total);
    }

    setTimeout(() => {
        renderBulkResults();
        showBulkStep(3);
    }, 1000);
}

function updateFileStatus(index, state, subtitle, result) {
    const statusDiv = document.getElementById(`bulk-status-${index}`);
    const subDiv = document.getElementById(`bulk-subtitle-${index}`);
    
    if (subDiv) subDiv.innerText = subtitle;

    if (state === 'processing') {
        statusDiv.innerHTML = `<div style="width:14px; height:14px; border-radius:50%; border:2px solid #E5E7EB; border-top-color:#C9A84C; animation:spin 0.7s linear infinite;"></div>`;
    } else if (state === 'complete') {
        let scoreHTML = '';
        if (result && result.score != null) {
            let s = Math.round(result.score);
            if (s >= 75) scoreHTML = `<span style="background:#E1F5EE; color:#0F6E56; padding:2px 6px; border-radius:8px; font-size:10px; font-weight:500; margin-left:8px;">${s}%</span>`;
            else if (s >= 50) scoreHTML = `<span style="background:#FAEEDA; color:#854F0B; padding:2px 6px; border-radius:8px; font-size:10px; font-weight:500; margin-left:8px;">${s}%</span>`;
            else scoreHTML = `<span style="background:#FCEBEB; color:#A32D2D; padding:2px 6px; border-radius:8px; font-size:10px; font-weight:500; margin-left:8px;">${s}%</span>`;
        }
        statusDiv.innerHTML = `<div style="display:flex; align-items:center;"><svg width="16" height="16" fill="none" stroke="#0F6E56" stroke-width="2" viewBox="0 0 24 24"><path d="M22 11.08V12a10 10 0 11-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>${scoreHTML}</div>`;
    } else if (state === 'error') {
        statusDiv.innerHTML = `<svg width="16" height="16" fill="none" stroke="#A32D2D" stroke-width="2" viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
    }
}

function animateProgress(index, from, to, duration) {
    const bar = document.getElementById(`bulk-progress-${index}`);
    if (!bar) return;
    const start = performance.now();
    function step(now) {
        const p = Math.min((now - start) / duration, 1);
        bar.style.width = (from + (to - from) * p) + '%';
        if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
}

function updateOverallProgress(done, total) {
    const pct = Math.round((done / total) * 100);
    document.getElementById('bulk-overall-progress').style.width = pct + '%';
    document.getElementById('bulk-processed-count').innerText = done;
}

async function screenSingleCandidate(file, jobId) {
    const token = localStorage.getItem('token');
    const base = typeof API_URL !== 'undefined' ? API_URL : (window.API_URL || window.location.origin);
    const formData = new FormData();
    formData.append('file', file);
    if (jobId) formData.append('job_id', jobId);

    const response = await fetch(`${base}/candidates/screen-cv`, {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + token },
        body: formData,
    });

    if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || 'Screening failed');
    }
    return response.json();
}

// Excel Parsing using SheetJS dynamically
async function parseExcelFile(file) {
    if (!window.XLSX) {
        await new Promise((resolve, reject) => {
            const s = document.createElement('script');
            s.src = 'https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js';
            s.onload = resolve;
            s.onerror = reject;
            document.head.appendChild(s);
        });
    }

    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = e => {
            try {
                const wb = XLSX.read(e.target.result, { type: 'binary' });
                const ws = wb.Sheets[wb.SheetNames[0]];
                const rows = XLSX.utils.sheet_to_json(ws, { defval: '' });
                const candidates = rows.map(row => ({
                    name:         row['Name'] || row['name'] || '',
                    phone:        row['Phone'] || row['phone'] || '',
                    email:        row['Email'] || row['email'] || '',
                    location:     row['Location'] || row['location'] || '',
                    lastTitle:    row['Last Title'] || row['lastTitle'] || '',
                    lastEmployer: row['Last Employer'] || row['lastEmployer'] || '',
                    yearsExp:     row['Years of Exp.'] || row['yearsExp'] || ''
                }));
                resolve(candidates);
            } catch(err) { reject(err); }
        };
        reader.onerror = reject;
        reader.readAsBinaryString(file);
    });
}

// Step 3 UI
function renderBulkResults() {
    const tbody = document.getElementById('bulk-results-tbody');
    tbody.innerHTML = '';
    
    let shortlisted = 0, review = 0, rejected = 0, errors = 0;

    bulkProcessingResults.forEach(c => {
        if (c.error) {
            errors++;
            tbody.innerHTML += `
                <tr style="background:#F9FAFB; border-bottom:0.5px solid #F3F4F6;">
                    <td style="padding:10px 12px; font-size:12px; color:#6B7280;">${huntersEsc(c.name)}</td>
                    <td style="padding:10px 12px;">-</td>
                    <td style="padding:10px 12px;"><span style="display:flex; align-items:center; gap:4px; color:#A32D2D; font-size:11px;"><svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> Failed to process</span></td>
                    <td style="padding:10px 12px;"><button style="background:none; border:none; color:#C9A84C; font-size:11px; font-weight:500; cursor:pointer;">Retry</button></td>
                </tr>
            `;
            return;
        }

        if (c.decision === 'Shortlist') shortlisted++;
        else if (c.decision === 'Review' || c.decision === 'Maybe') review++;
        else if (c.decision === 'Imported') { /* Excel row, no score */ }
        else rejected++;

        let scoreHTML = `<span style="color:#9CA3AF; font-size:11px;">—</span>`;
        if (c.score != null) {
            const scoreVal = Math.round(c.score);
            if (scoreVal >= 75) scoreHTML = `<span style="background:#E1F5EE; color:#0F6E56; padding:4px 8px; border-radius:12px; font-size:11px; font-weight:500;">${scoreVal}%</span>`;
            else if (scoreVal >= 50) scoreHTML = `<span style="background:#FAEEDA; color:#854F0B; padding:4px 8px; border-radius:12px; font-size:11px; font-weight:500;">${scoreVal}%</span>`;
            else scoreHTML = `<span style="background:#FCEBEB; color:#A32D2D; padding:4px 8px; border-radius:12px; font-size:11px; font-weight:500;">${scoreVal}%</span>`;
        }

        const decMap = { Shortlist:'#0F6E56', Review:'#854F0B', Maybe:'#854F0B', Reject:'#A32D2D', Imported:'#185FA5' };
        const decColor = decMap[c.decision] || '#6B7280';
        let decisionBadge = `<span style="color:${decColor}; font-size:11px; font-weight:500;">${huntersEsc(c.decision || '—')}</span>`;

        tbody.innerHTML += `
            <tr style="border-bottom:0.5px solid #F3F4F6;">
                <td style="padding:10px 12px; font-size:12px; font-weight:500; color:#1B2A4A;">${huntersEsc(c.name)}</td>
                <td style="padding:10px 12px;">${scoreHTML}</td>
                <td style="padding:10px 12px;">${decisionBadge}</td>
                <td style="padding:10px 12px;"><button onclick="showToast('Added to Pipeline', 'success')" style="background:transparent; border:1px solid #1B2A4A; color:#1B2A4A; border-radius:6px; padding:4px 10px; font-size:11px; font-weight:500; cursor:pointer;">Add to Pipeline</button></td>
            </tr>
        `;
    });

    document.getElementById('count-shortlisted').innerText = shortlisted;
    document.getElementById('count-review').innerText = review;
    document.getElementById('count-rejected').innerText = rejected;
    document.getElementById('count-errors').innerText = errors;
}

function exportBulkResultsCSV() {
    const valid = bulkProcessingResults.filter(c => !c.error);
    if (valid.length === 0) {
        showToast('No valid results to export', 'info');
        return;
    }

    const headers = [
        "Applicant","Phone","Email","Location","Stage",
        "Last Title","Last Employer","Years of Exp.",
        "Resume File","Interviewer","Score","HR Notes","Decision"
    ];
    
    const rows = valid.map(c => [
        c.name||'', c.phone||'', c.email||'', c.location||'', c.stage||'Applied',
        c.lastTitle||'', c.lastEmployer||'', c.yearsExp||'',
        c.sourceFile||'', c.interviewer||'',
        Math.round(c.score) ?? '', c.notes||'', c.decision||''
    ]);
    
    const esc = v => `"${String(v).replace(/"/g,'""')}"`;
    const csv = '\uFEFF' + [headers,...rows].map(r=>r.map(esc).join(',')).join('\r\n');
    const uri = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
    const a = document.createElement('a');
    a.href = uri; 
    a.download = 'candidates_results.csv'; 
    a.click();
}

function saveBulkToPipeline() {
    const valid = bulkProcessingResults.filter(c => !c.error);
    showToast(`${valid.length} candidates added to pipeline`, 'success');
    closeBulkUploadModal();
    // Re-render candidates table or fetch data in real app
    if (typeof loadCandidatesList === 'function') {
        loadCandidatesList();
    }
}
