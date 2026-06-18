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
    if "shortlist" in dl and pct < 30:
        logger.error(
            "Invalid evaluation: Shortlist with overall_score %.1f (< 30). Overriding decision to Maybe.",
            pct,
        )
        dec = "Maybe"
        out["decision"] = dec
        if not str(out.get("reason", "")).startswith("[Adjusted"):
            prev = str(out.get("reason", "") or "")
            out["reason"] = f"[Adjusted: score under 30 for Shortlist] {prev}"

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
    Evaluates a candidate against a job using Gemini, returning bilingual structured JSON.
    """
    prompt = f"""You are a senior executive recruiter and HR analyst at a professional recruitment firm.
Perform a deep screening of the candidate below against the job requirements and output ONLY a valid JSON object — no markdown, no explanation.

JOB TITLE: {job.job_title}
JOB DESCRIPTION:
{job.job_description}

REQUIREMENTS:
- Required Skills: {job.required_skills}
- Behavioral Skills: {getattr(job, 'behavioral_skills', 'Not specified')}
- Industry Experience: {getattr(job, 'industry_experience', 'Not specified')}
- Min Experience: {job.min_experience} years
- Education Level: {job.education_level}
- Salary Range: {job.salary_range}

CANDIDATE DATA:
- Name: {candidate.name}
- Total Experience: {candidate.experience_years} years
- Stated Skills: {candidate.skills}
- Education: {candidate.education}
- CV TEXT EXTRACT:
{(candidate.cv_text or '')[:3000]}

SCORING WEIGHTS:
- Experience Relevance: {job.weight_experience}x
- Technical Skills Match: {job.weight_skills}x
- Education Alignment: {job.weight_education}x
- Behavioral & Industry Fit: {getattr(job, 'weight_behavioral', 0.2)}x

INSTRUCTIONS:
1. FIRST, decide if the candidate's actual profession/field is fundamentally relevant to this specific role (e.g. a medical doctor applying to a teaching role is NOT field-relevant, even if some words on their CV overlap with the job description). Field relevance is the primary gate — strong experience in the WRONG field does not make someone a good fit.
2. Based on field relevance plus experience/skills/education fit, make your "decision" first: "Shortlist" only for candidates who are field-relevant AND strong overall; "Maybe" for partial fit; "Reject" for poor fit or field-irrelevant candidates.
3. ONLY AFTER deciding, produce "score" as an INTEGER 0-100 that is CONSISTENT WITH and DERIVED FROM the decision you just made — the score MUST fall inside the band for that decision: Shortlist = 65-100, Maybe = 40-64, Reject = 0-39. NEVER Shortlist below 30. Do not let keyword overlap inflate the score above its decision's band.
4. Produce "score_breakdown" with integer sub-scores 0-100 for: experience, skills, education, behavioral.
5. summary_en: 2-3 professional English sentences summarizing candidate fit for this specific role.
6. summary_ar: IDENTICAL content in Modern Standard Arabic.
7. strengths_en: EXACTLY 3 strings in English — specific strengths this candidate brings to the role.
8. strengths_ar: same 3 strengths translated to Arabic.
9. gaps_en: EXACTLY 2 strings in English — most significant gaps vs. role requirements.
10. gaps_ar: same 2 gaps in Arabic.
11. quick_facts: extract from CV — years_experience (int), current_title (str or null), current_employer (str or null), education_level (str or null), key_skills_found (array of up to 5 skill strings found in CV), languages (array of spoken languages mentioned in CV).
12. interview_questions_en: EXACTLY 3 specific, role-tailored English interview questions for this candidate.
13. interview_questions_ar: same 3 questions in Arabic.

Return ONLY this JSON structure, with fields generated IN THIS ORDER (decision and score must come first, in this sequence, before any other field):
{{
  "decision": "<Shortlist|Maybe|Reject>",
  "score": <integer 0-100>,
  "score_breakdown": {{"experience": <int>, "skills": <int>, "education": <int>, "behavioral": <int>}},
  "summary_en": "<2-3 sentence English summary>",
  "summary_ar": "<2-3 جملة ملخص عربي>",
  "strengths_en": ["<strength 1>", "<strength 2>", "<strength 3>"],
  "strengths_ar": ["<قوة 1>", "<قوة 2>", "<قوة 3>"],
  "gaps_en": ["<gap 1>", "<gap 2>"],
  "gaps_ar": ["<فجوة 1>", "<فجوة 2>"],
  "quick_facts": {{
    "years_experience": <int>,
    "current_title": "<str or null>",
    "current_employer": "<str or null>",
    "education_level": "<str or null>",
    "key_skills_found": ["<skill1>", "<skill2>"],
    "languages": ["<language1>"]
  }},
  "interview_questions_en": ["<Q1>", "<Q2>", "<Q3>"],
  "interview_questions_ar": ["<س1>", "<س2>", "<س3>"]
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
