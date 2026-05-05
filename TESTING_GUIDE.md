# AI Recruitment System - Testing & Deployment Guide

## System Architecture

### Multi-Tenant Security Model
**High Security: Admin approves companies AND jobs**

```
Company Registration → Pending Approval → Admin Approves → Company Active → Company Posts Jobs
                                                         ↓
                                          Admin Reviews Jobs → Approves/Rejects
                                                         ↓
                                          Approved Jobs → Public Visibility
```

## User Roles

1. **Admin Users**
   - Can approve/reject company registrations
   - Can approve/reject job postings
   - Can view all companies and jobs
   - Access: Admin Dashboard (`index.html`)

2. **Company Users**
   - Can register their company
   - Can create job postings (pending admin approval)
   - Can view candidates who applied
   - Can export candidate data
   - Access: Company Dashboard (`company-dashboard.html`)

3. **Public/Candidates**
   - Can view approved jobs from approved companies
   - Can apply to jobs
   - Can see AI evaluation scores
   - Access: Public Job Board (`jobs.html`)

## URLs

### Public
- **Home**: `https://web-production-5b39b.up.railway.app/jobs.html`
- **Admin Login**: `https://web-production-5b39b.up.railway.app/` (default: admin@example.com / admin123)
- **Company Registration**: `https://web-production-5b39b.up.railway.app/register-company.html`
- **Company Dashboard**: `https://web-production-5b39b.up.railway.app/company-dashboard.html` (after login)
- **Admin Approval Dashboard**: `https://web-production-5b39b.up.railway.app/admin-approval-dashboard.html`

## API Endpoints

### Authentication
```
POST /auth/login                  - Login (returns JWT token)
GET  /auth/me                     - Get current user profile
```

### Companies
```
POST /companies/register          - Register new company (pending approval)
GET  /companies/pending           - Get pending companies (admin only)
POST /companies/approve/{id}      - Approve company (admin only)
POST /companies/reject/{id}       - Reject company (admin only) - query param: reason
GET  /companies/                  - Get all companies (admin only)
GET  /companies/approved          - Get approved companies (public)
GET  /companies/{id}              - Get company details
```

### Jobs
```
POST /jobs                        - Create job (company users)
GET  /jobs                        - Get user's jobs (company users)
GET  /jobs/{id}                   - Get job details
PUT  /jobs/{id}                   - Update job
DELETE /jobs/{id}                 - Delete job
GET  /jobs/{id}/candidates        - Get job candidates (company users)
GET  /jobs/admin/all              - Get all jobs (admin only)
GET  /jobs/admin/pending          - Get pending jobs (admin only)
POST /jobs/admin/approve/{id}     - Approve job (admin only)
POST /jobs/admin/reject/{id}      - Reject job (admin only)
GET  /public/jobs                 - Get public jobs (approved only)
```

### Candidates
```
POST /public/apply/{job_id}       - Submit application
GET  /public/evaluation/{cand_id} - Get candidate score
POST /candidates/export/csv       - Export candidates (admin)
```

## Testing Flow

### Option 1: Manual Testing (UI)

#### Step 1: Test Company Registration
1. Go to `register-company.html`
2. Fill form:
   - Company Name: `TechCorp Inc`
   - Company Email: `hr@techcorp.com`
   - Website: `https://techcorp.com`
   - Registration: `TC123456`
   - Contact: `John Doe`
   - Contact Email: `john@techcorp.com`
   - Password: `TechPass123`
3. Click "Submit Registration"
4. See success message: "Registration submitted for admin review"

#### Step 2: Admin Approves Company
1. Login as admin: `admin@example.com` / `admin123`
2. Click "Approvals" button (top right)
3. Go to "Companies" tab
4. See pending company
5. Click "Approve"
6. Confirm success

#### Step 3: Company User Logs In
1. Go to `index.html` (login page)
2. Login as: `john@techcorp.com` / `TechPass123`
3. Should redirect to `company-dashboard.html`

#### Step 4: Company Posts Job
1. On company dashboard, click "Post New Job"
2. Fill form:
   - Title: `Senior Developer`
   - Location: `San Francisco, CA`
   - Salary: `$120000 - $150000`
   - Experience: `5`
   - Description: `Looking for experienced developer`
   - Skills: `Python, FastAPI, React`
3. Click "Submit for Approval"
4. See: "Job posted successfully! It will be visible once approved by admin"

#### Step 5: Admin Approves Job
1. Login as admin
2. Click "Approvals" → "Jobs"
3. See pending job from TechCorp
4. Click "Approve"
5. Confirm success

#### Step 6: Public Sees Job
1. Go to `jobs.html`
2. Should see "Senior Developer" from TechCorp
3. Click "Apply"
4. Fill candidate form
5. See success + score polling

---

### Option 2: API Testing (Automated)

Run the test script locally:

```bash
cd /path/to/ai_recruitment
python test_api.py
```

Output:
```
Admin login successful
Company registered: TechCorp Inc (ID: 1)
Retrieved 1 pending companies
Company approved
Company user login successful
Job created: Senior Python Developer (ID: 1)
Retrieved 1 pending jobs
Job approved
Retrieved 1 public jobs
✓ All critical flows tested successfully!
```

---

### Option 3: cURL Testing

```bash
# 1. Admin Login
curl -X POST http://localhost:8000/auth/login \
  -d "username=admin@example.com&password=admin123"
# Response: { "access_token": "...", "token_type": "bearer" }

# 2. Register Company
curl -X POST http://localhost:8000/companies/register \
  -H "Content-Type: application/json" \
  -d '{
    "company_name": "TechCorp",
    "company_email": "hr@techcorp.com",
    "company_website": "https://techcorp.com",
    "registration_number": "TC123456",
    "contact_person": "John Doe",
    "contact_email": "john@techcorp.com",
    "password": "TechPass123"
  }'

# 3. Get Pending Companies (requires admin token)
curl -X GET http://localhost:8000/companies/pending \
  -H "Authorization: Bearer <admin_token>"

# 4. Approve Company
curl -X POST http://localhost:8000/companies/approve/1 \
  -H "Authorization: Bearer <admin_token>"

# 5. Company User Login
curl -X POST http://localhost:8000/auth/login \
  -d "username=john@techcorp.com&password=TechPass123"

# 6. Create Job (requires company token)
curl -X POST http://localhost:8000/jobs \
  -H "Authorization: Bearer <company_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "job_title": "Senior Developer",
    "job_location": "San Francisco",
    "min_experience": 5,
    "required_skills": "Python, FastAPI",
    "education_level": "Bachelor",
    "salary_range": "120000 - 150000"
  }'

# 7. Get Pending Jobs (requires admin token)
curl -X GET http://localhost:8000/jobs/admin/pending \
  -H "Authorization: Bearer <admin_token>"

# 8. Approve Job
curl -X POST http://localhost:8000/jobs/admin/approve/1 \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"approval_notes": "Approved"}'

# 9. Get Public Jobs
curl -X GET http://localhost:8000/public/jobs
```

---

## Verification Checklist

- [ ] Admin can login with `admin@example.com` / `admin123`
- [ ] Company registration page works
- [ ] Admin can see pending companies
- [ ] Admin can approve company
- [ ] Company user can login after approval
- [ ] Company can create jobs (pending approval)
- [ ] Admin can see pending jobs
- [ ] Admin can approve jobs
- [ ] Public can see only approved jobs from approved companies
- [ ] Candidates can apply to approved jobs
- [ ] Candidates see evaluation scores

---

## Troubleshooting

### Issue: "Could not validate credentials"
**Solution**: Ensure token is valid and not expired. Tokens expire after 24 hours.

### Issue: "Admin access required"
**Solution**: Ensure user is logged in as admin. Check `is_admin` field in user profile.

### Issue: Job doesn't appear on public board
**Solution**: Check both company AND job approval status:
- Company must have `is_approved = true`
- Job must have `is_approved = true`

### Issue: Company user login fails after approval
**Solution**: Clear browser cache/localStorage and try again.

### Issue: "Job not found" when approving
**Solution**: Ensure job_id exists and admin has access (all jobs visible to admin).

---

## Sample Test Data

### Admin Account
- Email: `admin@example.com`
- Password: `admin123`
- Role: Admin (full access)

### Sample Company Registration
- Company: `TechCorp Inc`
- Email: `hr@techcorp.com`
- Website: `https://techcorp.com`
- Reg Number: `TC123456`
- Contact: `Jane Doe`
- Contact Email: `jane@techcorp.com`
- Password: `TechPass123`

### Sample Job
- Title: `Senior Python Developer`
- Location: `San Francisco, CA`
- Experience: `5 years`
- Salary: `$120,000 - $150,000`
- Skills: `Python, FastAPI, PostgreSQL, React`
- Description: `We're looking for an experienced Python developer to join our team...`

---

## Deployment Notes

### Railway Deployment
- Platform: Railway.app
- Runtime: Python 3.10
- Database: PostgreSQL
- Start Command: `sh -c 'python create_sample_jobs.py && uvicorn app.main:app --host 0.0.0.0 --port $PORT'`

### Environment Variables
```
GEMINI_API_KEY=<your_gemini_key>
GOOGLE_APPS_SCRIPT_URL=<optional>
SECRET_KEY=<random_secret>
DATABASE_URL=<postgresql_url>
```

### Auto-Creation on Deploy
- Default admin: `admin@example.com` / `admin123`
- Sample jobs: 5 jobs with locations

---

## Next Steps (Optional Enhancements)

1. **Email Notifications**
   - Send emails when company is approved
   - Notify company when job is approved/rejected
   - Send evaluation results to candidates

2. **Analytics Dashboard**
   - Track application counts
   - View approval timelines
   - Candidate score distributions

3. **Advanced Filtering**
   - Filter by company, location, salary range
   - Search by skills
   - Filter by approval status

4. **Audit Logging**
   - Track all admin approvals/rejections
   - Maintain activity history
   - Export audit trail

5. **Company Profile**
   - Upload company logo
   - Add company description
   - Display company on job cards

---

**Last Updated**: May 5, 2026
**System Status**: ✓ Production Ready
