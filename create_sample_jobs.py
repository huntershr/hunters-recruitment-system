"""
Seed real Hunters HR jobs into the database on startup.
Runs automatically via railway.json start command.
"""
import sys, os
sys.path.insert(0, '/app')

from app.database import SessionLocal
from app import models

JOBS = [
    {
        "job_title": "Education Consultant",
        "job_description": "The Education Consultant is responsible for leading the academic establishment and development of Elite Generation International School. The role provides strategic and operational leadership over curriculum design, teaching and learning policies, academic structures, and staff development to ensure full alignment with Cambridge International standards.",
        "min_experience": 10,
        "required_skills": "Cambridge International Curriculum, Academic Leadership, Curriculum Design, Staff Development, Quality Assurance",
        "nice_to_have_skills": "ISCL platform experience, Arabic language",
        "education_level": "Master's degree in Education, Curriculum Design, or Educational Leadership",
        "salary_range": "",
        "behavioral_skills": "Strategic thinking, Stakeholder management, Coaching, Communication",
        "industry_experience": "British/Cambridge international schools, school startup or expansion",
        "weight_experience": 0.3, "weight_skills": 0.4, "weight_education": 0.1, "weight_behavioral": 0.2,
    },
    {
        "job_title": "HOD – Head of Department",
        "job_description": "The Head of Department is responsible for leading the academic delivery and quality assurance of the Cambridge curriculum within their subject area. The role ensures curriculum planning, teaching, assessment, and reporting are implemented in full alignment with Cambridge International standards.",
        "min_experience": 5,
        "required_skills": "Cambridge Curriculum, Curriculum Planning, Lesson Observation, Assessment Design, ISCL platform",
        "nice_to_have_skills": "Arabic language, Master's degree",
        "education_level": "Bachelor's degree in subject specialization with a recognized teaching qualification",
        "salary_range": "",
        "behavioral_skills": "Academic leadership, Constructive feedback, Data-driven intervention, Coaching",
        "industry_experience": "British/Cambridge international schools including leadership or coordination experience",
        "weight_experience": 0.3, "weight_skills": 0.4, "weight_education": 0.1, "weight_behavioral": 0.2,
    },
    {
        "job_title": "Floating Teacher",
        "job_description": "The Floating Teacher ensures continuity of high-quality education across the school by covering teacher absences and maintaining instructional standards aligned with the Cambridge curriculum. The role includes classroom teaching, lesson planning support, assessment responsibilities, and active engagement with the SCL platform.",
        "min_experience": 1,
        "required_skills": "Cambridge Curriculum, Classroom Management, Lesson Planning, Assessment, SCL platform",
        "nice_to_have_skills": "British curriculum knowledge",
        "education_level": "Bachelor's degree (Education or relevant field preferred)",
        "salary_range": "",
        "behavioral_skills": "Adaptability, Team player, Responsibility, Positive attitude",
        "industry_experience": "Previous school experience is an advantage",
        "weight_experience": 0.3, "weight_skills": 0.4, "weight_education": 0.1, "weight_behavioral": 0.2,
    },
    {
        "job_title": "Subject Teacher",
        "job_description": "The Teacher is responsible for delivering high-quality teaching and learning aligned with the Cambridge International Curriculum, ensuring academic excellence, student wellbeing, and compliance with school policies and Cambridge assessment standards.",
        "min_experience": 2,
        "required_skills": "Cambridge Curriculum, Lesson Planning, Differentiation, Assessment, ISCL platform",
        "nice_to_have_skills": "Cambridge teaching certification, PGCE",
        "education_level": "Bachelor's degree in Education or relevant subject area",
        "salary_range": "",
        "behavioral_skills": "Child protection commitment, Emotional intelligence, Professional integrity, Adaptability",
        "industry_experience": "British or Cambridge curriculum environment",
        "weight_experience": 0.3, "weight_skills": 0.4, "weight_education": 0.1, "weight_behavioral": 0.2,
    },
    {
        "job_title": "Key Stage Headmistress",
        "job_description": "The Key Stage Headmistress is the primary custodian of academic standards and student welfare for their specific age group. Responsible for leading a team of educators to deliver a world-class Cambridge International education while ensuring the school's strategic vision is felt in every classroom.",
        "min_experience": 5,
        "required_skills": "Cambridge Curriculum, School Leadership, Staff Management, Curriculum Design, ISCL platform",
        "nice_to_have_skills": "Master's degree, QTS or equivalent",
        "education_level": "Bachelor's degree in Education or relevant field; Master's preferred",
        "salary_range": "",
        "behavioral_skills": "Strategic goal setting, Policy integration, Data-driven monitoring, Safeguarding",
        "industry_experience": "Minimum 3 years in a leadership role (HOD, Assistant Head, or similar)",
        "weight_experience": 0.3, "weight_skills": 0.4, "weight_education": 0.1, "weight_behavioral": 0.2,
    },
    {
        "job_title": "English Head of Department",
        "job_description": "Leading the English department in a Cambridge international school. Responsible for curriculum planning, teacher development, student assessment, and ensuring Cambridge standards are met across all year groups.",
        "min_experience": 5,
        "required_skills": "British Curriculum Experience, Curriculum Planning, Student Assessment, Teaching and Learning, Checkpoint Teaching Experience",
        "nice_to_have_skills": "Teaching or Education Certificate",
        "education_level": "Bachelor Degree – English Language",
        "salary_range": "15,000 – 55,000",
        "behavioral_skills": "Leadership, Communication, Data analysis, Coaching",
        "industry_experience": "Cambridge/British curriculum schools",
        "weight_experience": 0.4, "weight_skills": 0.4, "weight_education": 0.2, "weight_behavioral": 0.2,
    },
    {
        "job_title": "Marketing Specialist",
        "job_description": "The Marketing Specialist is responsible for planning and executing marketing strategies that increase school visibility, drive qualified leads, and support admissions targets. The role combines digital marketing, content creation, and campaign execution with strong alignment to the education sector.",
        "min_experience": 3,
        "required_skills": "Digital Ads (Meta, Google Ads), Content creation & copywriting, CRM / lead tracking systems, Social media management tools",
        "nice_to_have_skills": "Marketing, Media, AI certificates",
        "education_level": "Bachelor Degree",
        "salary_range": "12,000 – 20,000",
        "behavioral_skills": "Results-driven mindset, Creativity with structured execution, Strong communication skills, Ability to work in a fast-paced startup environment",
        "industry_experience": "Understanding of parent psychology & education market, Experience in international schools or premium services",
        "weight_experience": 0.3, "weight_skills": 0.4, "weight_education": 0.2, "weight_behavioral": 0.1,
    },
]

def seed_jobs():
    db = SessionLocal()
    try:
        admin = db.query(models.User).filter(models.User.is_admin == True).first()
        if not admin:
            print("No admin user found — skipping job seeding.")
            return

        existing = db.query(models.Job).count()
        if existing > 0:
            print(f"✓ {existing} jobs already exist — skipping seeding.")
            return

        for j in JOBS:
            job = models.Job(
                job_title=j["job_title"],
                job_description=j.get("job_description"),
                job_location="Cairo, Egypt",
                min_experience=j["min_experience"],
                required_skills=j["required_skills"],
                nice_to_have_skills=j.get("nice_to_have_skills"),
                education_level=j["education_level"],
                salary_range=j.get("salary_range", ""),
                behavioral_skills=j.get("behavioral_skills"),
                industry_experience=j.get("industry_experience"),
                weight_experience=j["weight_experience"],
                weight_skills=j["weight_skills"],
                weight_education=j["weight_education"],
                weight_behavioral=j["weight_behavioral"],
                owner_id=admin.id,
                is_approved=True,
            )
            db.add(job)

        db.commit()
        print(f"✓ Seeded {len(JOBS)} jobs successfully.")
    except Exception as e:
        print(f"✗ Job seeding failed: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed_jobs()
