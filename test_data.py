import requests
import time
import sys

BASE_URL = "http://localhost:8000"

def run_tests():
    print("Testing the AI Recruitment System API...\n")
    
    # 1. Create a Job
    print("1. Creating a Job...")
    job_payload = {
        "job_title": "Senior Backend Developer",
        "min_experience": 5,
        "required_skills": "Python, FastAPI, PostgreSQL, AWS, Docker",
        "nice_to_have_skills": "Kubernetes, Machine Learning basics",
        "education_level": "Bachelor's in Computer Science or equivalent",
        "weight_experience": 1.5,
        "weight_skills": 2.0,
        "weight_education": 1.0,
        "salary_range": "$120k - $150k"
    }
    
    try:
        response = requests.post(f"{BASE_URL}/jobs/", json=job_payload)
        response.raise_for_status()
        job = response.json()
        print(f"SUCCESS: Job created successfully! ID: {job['id']}\n")
    except Exception as e:
        print(f"ERROR: Failed to create job: {e}")
        sys.exit(1)

    # 2. Submit a Candidate
    print("2. Submitting a Candidate...")
    candidate_payload = {
        "name": "Jane Doe",
        "email": "jane.doe@example.com",
        "phone": "+1234567890",
        "job_applied": job["id"],
        "experience_years": 6,
        "education": "Master's in Computer Science from MIT",
        "skills": "Python, Django, FastAPI, React, PostgreSQL, Docker, Kubernetes",
        "expected_salary": "$140k",
        "cv_text": "Experienced backend developer with 6 years of building scalable microservices in Python. Extensive experience with PostgreSQL and Docker. Master's degree in CS."
    }

    try:
        response = requests.post(f"{BASE_URL}/candidates/", json=candidate_payload)
        response.raise_for_status()
        candidate = response.json()
        print(f"SUCCESS: Candidate submitted successfully! ID: {candidate['id']}\n")
    except Exception as e:
        print(f"ERROR: Failed to submit candidate: {e}")
        sys.exit(1)

    print("WAIT: Waiting for the background evaluation to complete (5 seconds)...")
    time.sleep(5)

    # 3. Check Results
    print("\n3. Checking Evaluation Results...")
    try:
        response = requests.get(f"{BASE_URL}/results")
        response.raise_for_status()
        results = response.json()
        if results:
            print(f"SUCCESS: Evaluation Results:")
            for res in results:
                print(f"- Candidate ID: {res['candidate_id']}")
                print(f"  Score: {res['score']}")
                print(f"  Decision: {res['decision']}")
                print(f"  Reason: {res['reason']}")
                if res.get('strengths'):
                    print(f"  Strengths: {res['strengths']}")
                if res.get('weaknesses'):
                    print(f"  Weaknesses: {res['weaknesses']}")
                if res.get('suggested_interview_questions'):
                    print(f"  Suggested Questions:")
                    for q in res['suggested_interview_questions']:
                        print(f"    - {q}")
        else:
            print("WARNING: No evaluation results found. The AI evaluation might still be running or failed.")
    except Exception as e:
        print(f"ERROR: Failed to get results: {e}")

if __name__ == "__main__":
    run_tests()
