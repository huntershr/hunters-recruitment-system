import os
import google.generativeai as genai
import json
import time
import random
import logging
from dotenv import load_dotenv

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

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
            model = genai.GenerativeModel(model_name)
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
1. name - Full name of the candidate
2. email - Email address (or empty string if not found)
3. phone - Phone number (or empty string if not found)
4. experience_years - Total years of professional work experience as an INTEGER (count years across all jobs; if a fresh graduate with no work exp use 0)
5. education - Highest education level and institution (e.g. "BSc Computer Science, Cairo University")
6. skills - Key technical and professional skills as a comma-separated string
7. last_title - The most recent or current job title (e.g. "Senior Software Engineer", "Marketing Manager")
8. last_employer - The most recent or current employer/company name (e.g. "Microsoft", "Vodafone Egypt")

CV TEXT:
{(cv_text or '')[:6000]}

Return ONLY a valid JSON object with exactly these 8 keys. Use null for any field not found, except experience_years which must always be an integer.
Example: {{"name":"Ahmed Ali","email":"ahmed@example.com","phone":"01012345678","experience_years":5,"education":"BSc Engineering, AUC","skills":"Python, SQL, Power BI","last_title":"Data Analyst","last_employer":"Raya Holding"}}"""

    model_name = os.getenv("GEMINI_MODEL", "models/gemini-2.5-flash")
    max_retries = 3

    for attempt in range(max_retries):
        try:
            model = genai.GenerativeModel(model_name)
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
                    "name": "Unknown",
                    "email": "",
                    "phone": "",
                    "experience_years": 0,
                    "education": "",
                    "skills": "",
                    "last_title": "",
                    "last_employer": "",
                }
    return {}

def evaluate_candidate(job, candidate):
    """
    Evaluates a candidate based on job requirements using Google's Gemini API.
    """
    prompt = f"""
    You are a Senior Executive Recruiter. Your task is to perform a deep screening of the candidate against the specific Job Description provided.
    
    JOB TITLE: {job.job_title}
    JOB DESCRIPTION: 
    {job.job_description}

    REQUIREMENTS:
    - Required Skills: {job.required_skills}
    - Behavioral Skills: {getattr(job, 'behavioral_skills', 'Not specified')}
    - Industry Experience Preference: {getattr(job, 'industry_experience', 'Not specified')}
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

    SCORING CRITERIA (Weights):
    - Experience Relevance: {job.weight_experience}x
    - Technical Skills match: {job.weight_skills}x
    - Education alignment: {job.weight_education}x
    - Behavioral & Industry alignment: {getattr(job, 'weight_behavioral', 0.2)}x

    INSTRUCTIONS:
    1. Analyze if the candidate's experience is relevant to the Job Description and the specified Industry Preference.
    2. Check for technical "Required Skills" AND "Behavioral Skills" in the CV text.
    3. Produce an overall_score as an INTEGER from 0 to 100 (weighted match). Do not use fractional overall scores except through rounding at the very end if needed — the field must serialize as an integer.
    4. Provide score_breakdown with integer sub-scores 0–100 each: "experience", "skills", "education", "behavioral" reflecting how strongly the CV matches those dimensions relative to job weights.
    5. Make a decision: "Shortlist" only if overall_score ≥ 65; use "Maybe" for borderline profiles; otherwise "Reject". Never return "Shortlist" if overall_score is below 30.
    6. Provide a professional reasoning paragraph.

    Return ONLY a valid JSON object with keys:
    - "overall_score" (integer 0–100)
    - "score_breakdown" (object with keys experience, skills, education, behavioral — each integer 0–100)
    - "decision", "reason", "strengths", "weaknesses", "suggested_interview_questions" (last as array of strings)
    Optionally include deprecated "score" as a duplicate of overall_score divided by 10 for backward parsers (not required).
    """

    model_name = os.getenv("GEMINI_MODEL", "models/gemini-2.5-flash")
    max_retries = 3

    for attempt in range(max_retries):
        try:
            model = genai.GenerativeModel(model_name,
                system_instruction="You are a senior HR recruitment assistant. You must output only valid JSON without any markdown blocks or explanations."
            )
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                )
            )
            
            result_content = response.text
            # Clean up in case Gemini wraps in ```json
            result_content = result_content.strip()
            if result_content.startswith("```json"):
                result_content = result_content[7:]
            if result_content.startswith("```"):
                result_content = result_content[3:]
            if result_content.endswith("```"):
                result_content = result_content[:-3]
            
            result_json = json.loads(result_content.strip())
            return finalize_evaluation(result_json)
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                wait_time = (2 ** attempt) * 5 + random.random() # Longer wait for evaluation
                print(f"Rate limit hit during evaluation. Retrying in {wait_time:.2f}s...")
                time.sleep(wait_time)
                continue
            
            print(f"Error during evaluation (Attempt {attempt+1}): {e}")
            if attempt == max_retries - 1:
                return finalize_evaluation({
                    "overall_score": 0,
                    "score_breakdown": {"experience": 0, "skills": 0, "education": 0, "behavioral": 0},
                    "decision": "Reject",
                    "reason": f"Evaluation failed after retries: {str(e)}",
                    "strengths": "",
                    "weaknesses": "",
                    "suggested_interview_questions": [],
                })
    return finalize_evaluation({})
