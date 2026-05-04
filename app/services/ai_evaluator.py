import os
import google.generativeai as genai
import json
import time
import random
from dotenv import load_dotenv

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

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
    3. Determine a score from 0.0 to 10.0 based on the provided weights.
    4. Make a decision: "Shortlist" (strong match), "Maybe" (some gaps), or "Reject" (poor match).
    5. Provide a professional reasoning.

    Return ONLY a valid JSON object with: "score", "decision", "reason", "strengths", "weaknesses", "suggested_interview_questions".
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
            return result_json
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                wait_time = (2 ** attempt) * 5 + random.random() # Longer wait for evaluation
                print(f"Rate limit hit during evaluation. Retrying in {wait_time:.2f}s...")
                time.sleep(wait_time)
                continue
            
            print(f"Error during evaluation (Attempt {attempt+1}): {e}")
            if attempt == max_retries - 1:
                return {
                    "score": 0.0,
                    "decision": "Reject",
                    "reason": f"Evaluation failed after retries: {str(e)}",
                    "strengths": "",
                    "weaknesses": "",
                    "suggested_interview_questions": []
                }
    return {}
