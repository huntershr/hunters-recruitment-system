"""
Script to create sample jobs for testing the public job board
"""
import sys
sys.path.insert(0, '/app')

from app.database import SessionLocal
from app import models

def create_sample_jobs():
    db = SessionLocal()
    try:
        # Check if admin user exists
        admin = db.query(models.User).filter(models.User.email == "admin@example.com").first()
        if not admin:
            print("❌ Admin user not found! Create it first.")
            return
        
        # Check if jobs already exist
        existing_jobs = db.query(models.Job).filter(models.Job.owner_id == admin.id).count()
        if existing_jobs > 0:
            print(f"✓ {existing_jobs} jobs already exist")
            return
        
        # Create sample jobs
        jobs = [
            {
                "job_title": "Senior Python Developer",
                "job_description": "We're looking for an experienced Python developer to join our AI team. You'll work on scalable backend systems, FastAPI applications, and contribute to our core product.",
                "min_experience": 5,
                "required_skills": "Python, FastAPI, SQL, Docker",
                "nice_to_have_skills": "Machine Learning, AWS, Kubernetes",
                "education_level": "Bachelor's in CS or related field",
                "salary_range": "$120K - $160K",
                "behavioral_skills": "Team player, Problem solver, Communication",
                "industry_experience": "SaaS, Tech startups"
            },
            {
                "job_title": "Frontend Developer (React)",
                "job_description": "Join our frontend team to build beautiful and responsive user interfaces. You'll work with modern JavaScript frameworks, collaborate with designers, and optimize performance.",
                "min_experience": 3,
                "required_skills": "React, JavaScript, HTML/CSS, Git",
                "nice_to_have_skills": "TypeScript, Next.js, Figma collaboration",
                "education_level": "Bachelor's in CS or equivalent",
                "salary_range": "$90K - $130K",
                "behavioral_skills": "Creative, Detail-oriented, Team collaboration",
                "industry_experience": "Web development, SaaS"
            },
            {
                "job_title": "Data Scientist - AI/ML",
                "job_description": "Help us build intelligent AI systems. You'll develop machine learning models, work with large datasets, and optimize algorithms for production environments.",
                "min_experience": 4,
                "required_skills": "Python, Machine Learning, TensorFlow, SQL",
                "nice_to_have_skills": "Deep Learning, NLP, GPU optimization",
                "education_level": "Master's in CS, Statistics, or related field",
                "salary_range": "$130K - $170K",
                "behavioral_skills": "Analytical, Research-oriented, Innovation",
                "industry_experience": "AI/ML, Data analytics"
            },
            {
                "job_title": "DevOps Engineer",
                "job_description": "Manage and optimize our cloud infrastructure. You'll handle CI/CD pipelines, containerization, monitoring, and ensure system reliability and performance.",
                "min_experience": 3,
                "required_skills": "Docker, Kubernetes, CI/CD, Cloud (AWS/GCP/Azure)",
                "nice_to_have_skills": "Terraform, Prometheus, Linux administration",
                "education_level": "Bachelor's in CS or equivalent",
                "salary_range": "$110K - $150K",
                "behavioral_skills": "Problem-solving, Automation mindset, Communication",
                "industry_experience": "Cloud platforms, Infrastructure"
            },
            {
                "job_title": "Product Manager",
                "job_description": "Lead our product strategy and vision. You'll work cross-functionally with engineers and design, manage roadmap, and drive product adoption.",
                "min_experience": 4,
                "required_skills": "Product strategy, Analytics, Communication",
                "nice_to_have_skills": "Technical background, Data analysis",
                "education_level": "Bachelor's degree (any field)",
                "salary_range": "$120K - $160K",
                "behavioral_skills": "Leadership, Strategic thinking, Adaptability",
                "industry_experience": "SaaS, Tech industry"
            }
        ]
        
        for job_data in jobs:
            job = models.Job(
                owner_id=admin.id,
                weight_experience=0.3,
                weight_skills=0.4,
                weight_education=0.1,
                weight_behavioral=0.2,
                **job_data
            )
            db.add(job)
        
        db.commit()
        print(f"✅ Created {len(jobs)} sample jobs successfully!")
        
        # List created jobs
        all_jobs = db.query(models.Job).filter(models.Job.owner_id == admin.id).all()
        for job in all_jobs:
            print(f"  - {job.job_title}")
            
    except Exception as e:
        print(f"❌ Error creating jobs: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    create_sample_jobs()
