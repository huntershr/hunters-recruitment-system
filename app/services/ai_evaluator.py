import os
import google.generativeai as genai
import json
import time
import random
import logging
from dotenv import load_dotenv
from json_repair import repair_json

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))

_MODEL_NAME = os.getenv("GEMINI_MODEL", "models/gemini-2.5-flash")
_gemini_model = genai.GenerativeModel(_MODEL_NAME)
_gemini_eval_model = genai.GenerativeModel(
    _MODEL_NAME,
    system_instruction="You are a senior HR recruitment assistant. You must output only valid JSON without any markdown blocks or explanations."
)

logger = logging.getLogger(__name__)


def call_agent_screener(cv_text: str, job, candidate_id=None) -> "dict | None":
    """
    Call the Node.js screening agent and map its response to the same dict
    shape returned by evaluate_candidate(). Returns None on any error/timeout.
    Never raises — callers must handle None as a signal to fall back to Gemini.
    """
    try:
        from .agent_screener import call_agent_screen
        raw = call_agent_screen(cv_text, job)
        if not raw:
            return None

        overall = float(raw.get("overall_score") or 0)
        rec = str(raw.get("recommendation") or "").strip()
        _REC_MAP = {
            "Highly Recommended": "Shortlist",
            "Recommended":        "Shortlist",
            "Consider":           "Maybe",
            "Not Recommended":    "Reject",
        }
        decision = _REC_MAP.get(rec, "Reject")

        ds_raw = raw.get("dimension_scores") or {}
        tm = int(ds_raw.get("titleMatch") or 0)
        im = int(ds_raw.get("industryMatch") or 0)
        em = int(ds_raw.get("experienceMatch") or 0)
        sm = int(ds_raw.get("skillsMatch") or 0)

        # Remap to Gemini key names so finalize_evaluation() populates score_breakdown correctly
        dimension_scores_mapped = {
            "job_title_match":     tm,
            "industry_match":      im,
            "years_of_experience": em,
            "skills_match":        sm,
        }

        strengths = raw.get("strengths") or []
        concerns  = raw.get("concerns")  or []

        mapped = {
            "overall_score": overall,
            "score":         overall,
            "decision":      decision,
            "reason":        f"Title: {tm}% | Industry: {im}% | Experience: {em}% | Skills: {sm}%",
            "strengths":     strengths if isinstance(strengths, list) else [strengths],
            "weaknesses":    concerns  if isinstance(concerns,  list) else [concerns],
            "suggested_interview_questions": [],
            "dimension_scores": dimension_scores_mapped,
        }

        result = finalize_evaluation(mapped)

        # Stamp source=agent in the dimension_scores JSON column (no schema change needed)
        if isinstance(result.get("dimension_scores"), dict):
            result["dimension_scores"]["source"] = "agent"
        else:
            result["dimension_scores"] = {"source": "agent"}

        # Carry candidate_profile for callers that do profile back-fill; must be popped before DB write
        result["_candidate_profile"] = raw.get("candidate_profile") or {}

        logger.info(
            "Agent screener OK: score=%.1f decision=%s "
            "(title=%d%% industry=%d%% exp=%d%% skills=%d%%)",
            overall, decision, tm, im, em, sm,
        )
        return result

    except Exception as exc:
        logger.error("call_agent_screener failed: %s", exc)
        return None


def _coerce_percent_from_keys(parsed: dict) -> float:
    """
    Derive unified 0–100 percentage from Gemini output.

    Prefer overall_score (0–100). Fall back to legacy "score":
    fractions (≤1), 0–10 scale, or literal 0–100.
    """
    if not parsed:
        return 0.0

    if parsed.get("overall_score") is not None:
        try:
            pct = float(parsed["overall_score"])
        except (TypeError, ValueError):
            pct = 0.0
        return max(0.0, min(100.0, pct))

    if parsed.get("score") is not None:
        try:
            n = float(parsed["score"])
        except (TypeError, ValueError):
            return 0.0
        if n <= 1.0:
            return max(0.0, min(100.0, n * 100.0))
        if n <= 10.0:
            return max(0.0, min(100.0, n * 10.0))
        return max(0.0, min(100.0, n))

    return 0.0


def finalize_evaluation(parsed: dict) -> dict:
    """
    Normalize model JSON: unify score onto 0–100, reconcile decision vs score.

    Returned dict keeps keys expected by callers (score, overall_score, …).
    """
    if not parsed:
        return {
            "overall_score": 0,
            "score": 0.0,
            "decision": "Reject",
            "reason": "Empty evaluation payload",
            "strengths": "",
            "weaknesses": "",
            "suggested_interview_questions": [],
            "summary_en": "",
            "summary_ar": "",
            "strengths_en": [],
            "strengths_ar": [],
            "gaps_en": [],
            "gaps_ar": [],
            "interview_questions_en": [],
            "interview_questions_ar": [],
            "quick_facts": {},
        }

    out = dict(parsed)
    pct = round(_coerce_percent_from_keys(parsed), 2)
    out["overall_score"] = int(round(pct))
    out["score"] = pct

    dec = str(out.get("decision", "") or "").strip()
    if not dec:
        dec = "Reject"
        out["decision"] = dec
    dl = dec.lower()

    def _adjust(new_dec: str, tag: str, msg: str) -> str:
        logger.warning("Guard fired [%s]: %s score=%.1f → %s", tag, msg, pct, new_dec)
        out["decision"] = new_dec
        if not str(out.get("reason", "")).startswith("[Adjusted"):
            prev = str(out.get("reason", "") or "")
            out["reason"] = f"[Adjusted: {tag}] {prev}"
        return new_dec

    # Guard A (tightened): Shortlist requires score >= 50; below that is at best Maybe
    if "shortlist" in dl and pct < 50:
        dec = _adjust("Maybe", "Shortlist score<50", "Shortlist decision contradicts score")

    # Guard B (new): Reject with score >= 75 is internally contradictory — split to Maybe
    elif "reject" in dl and pct >= 75:
        dec = _adjust("Maybe", "Reject score>=75", "Reject decision contradicts high score")

    # Normalize dimension_scores → score_breakdown so all write paths populate the
    # legacy score_experience/score_skills/score_education/score_behavioral columns.
    # Mapping: years_of_experience→experience, skills_match→skills,
    #          industry_match→education, job_title_match→behavioral.
    # (Column names are legacy; labels in the UI are updated separately.)
    ds = out.get("dimension_scores") or {}
    out["score_breakdown"] = {
        "experience": ds.get("years_of_experience"),
        "skills":     ds.get("skills_match"),
        "education":  ds.get("industry_match"),
        "behavioral": ds.get("job_title_match"),
    }

    return out

def generate_job_details(job_title: str, industry_background: str, additional_context: str = "") -> dict:
    ctx = additional_context.strip() or "None"
    prompt = f"""You are a senior HR specialist at Hunters for HR Transformation & Execution.

Generate professional job posting details for the role below.

Job Title: {job_title}
Industry Background: {industry_background}
Additional Context: {ctx}

Return ONLY a valid JSON object (no markdown, no code blocks) with exactly these keys:
{{
  "job_brief": "3-4 sentence professional description of the role and its responsibilities in the {industry_background} sector",
  "required_skills": "comma-separated list of 6-8 must-have skills for {job_title} in {industry_background}",
  "nice_to_have": "comma-separated list of 4-5 bonus skills for {job_title}",
  "behavioral_skills": "comma-separated list of 4-5 behavioral competencies such as communication, teamwork, adaptability"
}}"""

    model_name = os.getenv("GEMINI_MODEL", "models/gemini-2.5-flash")
    max_retries = 3

    for attempt in range(max_retries):
        try:
            model = _gemini_model
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.4,
                    response_mime_type="application/json",
                ),
            )

            raw = response.text.strip()
            if raw.startswith("```json"):
                raw = raw[7:]
            if raw.startswith("```"):
                raw = raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]

            return json.loads(raw.strip())

        except Exception as e:
            logger.error("generate_job_details attempt %d failed: %s", attempt + 1, e)
            if "429" in str(e) and attempt < max_retries - 1:
                wait = (2 ** attempt) * 2 + random.random()
                time.sleep(wait)
                continue
            if attempt == max_retries - 1:
                raise

    raise RuntimeError("generate_job_details failed after retries")


def extract_candidate_info(cv_text):
    """
    Extracts structured candidate information from CV text using Gemini.
    """
    prompt = f"""You are an expert HR Data Analyst. Read the CV text below carefully and extract the following fields.

FIELDS TO EXTRACT:
1. name - Full name of the candidate (null if not found)
2. email - Email address (null if not found)
3. phone - Phone number (null if not found)
4. experience_years - Total years of professional work experience as an INTEGER (count years across all jobs; fresh graduate with no work exp = 0)
5. education - Highest education level and institution as a single string (e.g. "BSc Computer Science, Cairo University")
6. skills - Key technical and professional skills as a comma-separated string
7. last_title - The most recent or current job title
8. last_employer - The most recent or current employer/company name
9. summary - 2-3 sentence professional summary of the candidate based on their CV
10. experiences - Array of work experience entries. Each entry: {{"title":"job title","employer":"company name","start":"year or Month YYYY","end":"year/Month YYYY or Present","description":"brief role description"}}
11. education_history - Array of education entries. Each entry: {{"degree":"degree name","institution":"school name","year":"graduation year"}}
12. languages - Array of language entries. Each entry: {{"language":"language name","proficiency":"Native/Fluent/Intermediate/Basic"}}

CV TEXT:
{(cv_text or '')[:6000]}

Return ONLY a valid JSON object with exactly these 12 keys. experience_years must always be an integer. Use null for missing scalar fields, [] for missing array fields.
Example: {{"name":"Ahmed Ali","email":"ahmed@example.com","phone":"01012345678","experience_years":5,"education":"BSc Engineering, AUC","skills":"Python, SQL, Power BI","last_title":"Data Analyst","last_employer":"Raya Holding","summary":"Experienced data analyst with 5 years in BI and reporting.","experiences":[{{"title":"Data Analyst","employer":"Raya Holding","start":"2019","end":"Present","description":"Led data analysis and BI dashboards."}}],"education_history":[{{"degree":"BSc Engineering","institution":"AUC","year":"2019"}}],"languages":[{{"language":"Arabic","proficiency":"Native"}},{{"language":"English","proficiency":"Fluent"}}]}}"""

    model_name = os.getenv("GEMINI_MODEL", "models/gemini-2.5-flash")
    max_retries = 3

    for attempt in range(max_retries):
        try:
            model = _gemini_model
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                )
            )
            data = json.loads(response.text.strip())
            # Ensure experience_years is always an integer
            try:
                import re as _re
                raw_exp = data.get("experience_years")
                if isinstance(raw_exp, (int, float)):
                    data["experience_years"] = int(raw_exp)
                elif isinstance(raw_exp, str):
                    nums = _re.findall(r'\d+', raw_exp)
                    data["experience_years"] = int(nums[0]) if nums else 0
                else:
                    data["experience_years"] = 0
            except Exception:
                data["experience_years"] = 0
            return data
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                wait_time = (2 ** attempt) + random.random()
                print(f"Rate limit hit during extraction. Retrying in {wait_time:.2f}s...")
                time.sleep(wait_time)
                continue
            print(f"Error during extraction (Attempt {attempt+1}): {e}")
            if attempt == max_retries - 1:
                return {
                    "name": None,
                    "email": "",
                    "phone": "",
                    "experience_years": 0,
                    "education": "",
                    "skills": "",
                    "last_title": "",
                    "last_employer": "",
                    "summary": None,
                    "experiences": [],
                    "education_history": [],
                    "languages": [],
                }
    return {}

def evaluate_candidate(job, candidate):
    """
    Evaluates a candidate against a job using Gemini — 4-dimension screener.
    """
    cv_text = (candidate.cv_text or '')[:6000]
    job_title = job.job_title
    experience_required = job.min_experience
    skills_required = job.required_skills or ''
    job_description = job.job_description or ''
    department = (
        getattr(job, 'industry_experience', None) or
        getattr(job, 'department', None) or
        'Not specified'
    )

    prompt = f"""You are an expert HR recruitment screener for a professional recruitment platform.

Analyze the candidate's CV against the job requirements below using 4 specific dimensions.

CV TEXT:
{cv_text}

JOB REQUIREMENTS:
Title: {job_title}
Experience required: {experience_required}
Skills required: {skills_required}
Description: {job_description}
Department/Industry: {department}

SCORING — analyze each dimension and score 0 to 100:

DIMENSION 1 — Job Title Match (weight 25%):
Read the last 3 job titles from the CV work history.
Compare them to the required job title.
Exact or equivalent title = 90–100. Same subject/specialization, different seniority = 70–89. Same broad field but DIFFERENT SUBJECT (e.g. Science teacher applying for Math Teacher, or English teacher for Arabic) = 20–40. Unrelated field entirely = 0–19.

DIMENSION 2 — Industry Match (weight 25%):
Read the last employer's industry or company type.
Compare to the job's department/industry context.
Same industry AND same sub-specialization (e.g. British curriculum school for British curriculum role) = 90–100. Same broad industry but different specialization (e.g. Education generally but wrong subject or wrong curriculum) = 40–60. Unrelated industry = 0–39.

DIMENSION 3 — Years of Experience (weight 25%):
Sum total years of work experience from all roles in the CV using date ranges.
Compare to the required experience in the job.
Meets or exceeds requirement = 90–100. Up to 1 year short = 60–74. 2 years short = 35–59. 3+ years short = below 35.

DIMENSION 4 — Skills Match (weight 25%):
List ONLY skills EXPLICITLY STATED in the CV text — do NOT infer or assume a skill exists because of the candidate's job title or field. For example, do not assume "lesson planning" or "classroom management" are present just because the candidate is a teacher — only count them if the CV text actually names or describes them.
Compare to the required skills listed in the job.
Score = percentage of required skills EXPLICITLY found in the CV. A candidate with 1 of 5 required skills explicitly stated should score ~20, not be rounded up.

Be a strict, skeptical screener. Do not give benefit-of-the-doubt credit for adjacent or assumed qualifications. A mismatch in core subject, curriculum, or explicitly named requirement should weigh heavily even if other dimensions are strong.

Overall score = average of all 4 dimension scores.

Decision rule:
- overall_score >= 75 → "Shortlist"
- overall_score >= 50 → "Maybe"
- overall_score < 50 → "Reject"

Return ONLY a valid JSON object — no markdown, no explanation, no extra text before or after:
{{
  "score": <integer 0–100, overall weighted average>,
  "decision": "Shortlist" | "Maybe" | "Reject",
  "reason": "<2–3 sentence summary explaining the overall match based on the 4 dimensions>",
  "strengths": ["<specific strength from CV matching job requirement>", "<strength 2>", "<strength 3>"],
  "weaknesses": ["<specific gap or missing requirement>", "<gap 2>"],
  "suggested_interview_questions": ["<question targeting a gap or validating a strength>", "<question 2>", "<question 3>"],
  "dimension_scores": {{
    "job_title_match": <integer 0–100>,
    "industry_match": <integer 0–100>,
    "years_of_experience": <integer 0–100>,
    "skills_match": <integer 0–100>
  }}
}}"""

    max_retries = 3

    for attempt in range(max_retries):
        try:
            model = _gemini_eval_model
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                )
            )

            result_content = response.text.strip()
            if result_content.startswith("```json"):
                result_content = result_content[7:]
            if result_content.startswith("```"):
                result_content = result_content[3:]
            if result_content.endswith("```"):
                result_content = result_content[:-3]

            result_json = json.loads(repair_json(result_content.strip()))
            return finalize_evaluation(result_json)
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                wait_time = (2 ** attempt) * 5 + random.random()
                print(f"Rate limit hit during evaluation. Retrying in {wait_time:.2f}s...")
                time.sleep(wait_time)
                continue

            print(f"Error during evaluation (Attempt {attempt+1}): {e}")
            if attempt == max_retries - 1:
                return finalize_evaluation({
                    "score": 0,
                    "score_breakdown": {"experience": 0, "skills": 0, "education": 0, "behavioral": 0},
                    "decision": "Reject",
                    "summary_en": f"Evaluation failed after retries: {str(e)}",
                    "summary_ar": "فشل التقييم بعد عدة محاولات.",
                    "strengths_en": [],
                    "strengths_ar": [],
                    "gaps_en": [],
                    "gaps_ar": [],
                    "interview_questions_en": [],
                    "interview_questions_ar": [],
                    "quick_facts": {},
                })
    return finalize_evaluation({})
