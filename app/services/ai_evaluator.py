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

def extract_candidate_info(cv_text):
    """
    Extracts structured candidate information from CV text using Gemini.
    """
    prompt = f"""
    You are an expert HR Data Analyst. Extract the following information from the CV text provided:
    - Full Name
    - Email Address
    - Phone Number
    - Total Years of Experience (as an integer)
    - Highest Education Level
    - Key Skills (comma separated)
    - Current/Last Job Title

    CV TEXT:
    {cv_text[:5000]}

    Return ONLY a valid JSON object with keys: "name", "email", "phone", "experience_years", "education", "skills", "last_title".
    If a field is not found, use null or 0 for experience.
    """

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
            return json.loads(response.text.strip())
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
                    "last_title": ""
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
    {candidate.cv_text[:3000]}

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
