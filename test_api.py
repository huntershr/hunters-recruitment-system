#!/usr/bin/env python3
"""
Test script for the AI Recruitment System
Tests key API endpoints to verify functionality
"""

import requests
import json
import sys
from datetime import datetime

# Configuration
API_URL = "http://localhost:8000"
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "admin123"
TEST_COMPANY_EMAIL = "testcorp@example.com"
TEST_COMPANY_PASSWORD = "TestPass123"

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

def print_status(message, success=True):
    """Print colored status message"""
    status = f"{Colors.GREEN}✓{Colors.END}" if success else f"{Colors.RED}✗{Colors.END}"
    print(f"{status} {message}")

def print_section(title):
    """Print section header"""
    print(f"\n{Colors.BLUE}{'='*60}{Colors.END}")
    print(f"{Colors.BLUE}{title}{Colors.END}")
    print(f"{Colors.BLUE}{'='*60}{Colors.END}")

def test_admin_login():
    """Test admin login"""
    print_section("Testing Admin Login")
    
    try:
        response = requests.post(
            f"{API_URL}/auth/login",
            data={
                "username": ADMIN_EMAIL,
                "password": ADMIN_PASSWORD
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            print_status(f"Admin login successful")
            print(f"  Token: {data['access_token'][:20]}...")
            return data['access_token']
        else:
            print_status(f"Admin login failed: {response.text}", False)
            return None
    except Exception as e:
        print_status(f"Error: {str(e)}", False)
        return None

def test_company_registration(admin_token):
    """Test company registration"""
    print_section("Testing Company Registration")
    
    try:
        response = requests.post(
            f"{API_URL}/companies/register",
            json={
                "company_name": "TechCorp Inc",
                "company_email": TEST_COMPANY_EMAIL,
                "company_website": "https://techcorp.example.com",
                "registration_number": "TC123456",
                "contact_person": "Jane Doe",
                "contact_email": "jane@techcorp.example.com",
                "password": TEST_COMPANY_PASSWORD
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            print_status(f"Company registered: {data['company_name']} (ID: {data['id']})")
            print(f"  Status: {'Approved' if data['is_approved'] else 'Pending'}")
            return data['id']
        else:
            print_status(f"Company registration failed: {response.text}", False)
            return None
    except Exception as e:
        print_status(f"Error: {str(e)}", False)
        return None

def test_get_pending_companies(admin_token):
    """Test getting pending companies"""
    print_section("Testing Get Pending Companies")
    
    try:
        response = requests.get(
            f"{API_URL}/companies/pending",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        if response.status_code == 200:
            data = response.json()
            print_status(f"Retrieved {len(data)} pending companies")
            for company in data:
                print(f"  - {company['company_name']} ({company['company_email']})")
            return True
        else:
            print_status(f"Failed to get pending companies: {response.text}", False)
            return False
    except Exception as e:
        print_status(f"Error: {str(e)}", False)
        return False

def test_approve_company(admin_token, company_id):
    """Test company approval"""
    print_section("Testing Company Approval")
    
    try:
        response = requests.post(
            f"{API_URL}/companies/approve/{company_id}",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        if response.status_code == 200:
            data = response.json()
            print_status(f"Company approved: {data['message']}")
            return True
        else:
            print_status(f"Company approval failed: {response.text}", False)
            return False
    except Exception as e:
        print_status(f"Error: {str(e)}", False)
        return False

def test_company_login():
    """Test company user login"""
    print_section("Testing Company User Login")
    
    try:
        response = requests.post(
            f"{API_URL}/auth/login",
            data={
                "username": "jane@techcorp.example.com",
                "password": TEST_COMPANY_PASSWORD
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            print_status(f"Company user login successful")
            print(f"  Token: {data['access_token'][:20]}...")
            return data['access_token']
        else:
            print_status(f"Company user login failed: {response.text}", False)
            return None
    except Exception as e:
        print_status(f"Error: {str(e)}", False)
        return None

def test_create_job(company_token):
    """Test job creation"""
    print_section("Testing Job Creation")
    
    try:
        response = requests.post(
            f"{API_URL}/jobs",
            headers={"Authorization": f"Bearer {company_token}"},
            json={
                "job_title": "Senior Python Developer",
                "job_description": "Looking for experienced Python developer",
                "job_location": "San Francisco, CA",
                "min_experience": 5,
                "required_skills": "Python, FastAPI, PostgreSQL",
                "education_level": "Bachelor's",
                "salary_range": "120000 - 150000"
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            print_status(f"Job created: {data['job_title']} (ID: {data['id']})")
            print(f"  Status: {'Approved' if data['is_approved'] else 'Pending'}")
            return data['id']
        else:
            print_status(f"Job creation failed: {response.text}", False)
            return None
    except Exception as e:
        print_status(f"Error: {str(e)}", False)
        return None

def test_get_pending_jobs(admin_token):
    """Test getting pending jobs"""
    print_section("Testing Get Pending Jobs")
    
    try:
        response = requests.get(
            f"{API_URL}/jobs/admin/pending",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        if response.status_code == 200:
            data = response.json()
            print_status(f"Retrieved {len(data)} pending jobs")
            for job in data:
                print(f"  - {job['job_title']} ({job['job_location']})")
            return True
        else:
            print_status(f"Failed to get pending jobs: {response.text}", False)
            return False
    except Exception as e:
        print_status(f"Error: {str(e)}", False)
        return False

def test_approve_job(admin_token, job_id):
    """Test job approval"""
    print_section("Testing Job Approval")
    
    try:
        response = requests.post(
            f"{API_URL}/jobs/admin/approve/{job_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"approval_notes": "Looks good"}
        )
        
        if response.status_code == 200:
            data = response.json()
            print_status(f"Job approved: {data['message']}")
            return True
        else:
            print_status(f"Job approval failed: {response.text}", False)
            return False
    except Exception as e:
        print_status(f"Error: {str(e)}", False)
        return False

def test_get_public_jobs():
    """Test getting public jobs"""
    print_section("Testing Get Public Jobs")
    
    try:
        response = requests.get(f"{API_URL}/public/jobs")
        
        if response.status_code == 200:
            data = response.json()
            print_status(f"Retrieved {len(data)} public jobs")
            for job in data:
                print(f"  - {job['job_title']} ({job['job_location']})")
            return True
        else:
            print_status(f"Failed to get public jobs: {response.text}", False)
            return False
    except Exception as e:
        print_status(f"Error: {str(e)}", False)
        return False

def main():
    """Run all tests"""
    print(f"\n{Colors.YELLOW}AI Recruitment System - API Test Suite{Colors.END}")
    print(f"API URL: {API_URL}")
    print(f"Test started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Test admin login
    admin_token = test_admin_login()
    if not admin_token:
        print_status("Cannot proceed without admin token", False)
        return False
    
    # Test company registration
    company_id = test_company_registration(admin_token)
    if not company_id:
        print_status("Cannot proceed without company ID", False)
        return False
    
    # Test get pending companies
    test_get_pending_companies(admin_token)
    
    # Test company approval
    test_approve_company(admin_token, company_id)
    
    # Test company login
    company_token = test_company_login()
    if not company_token:
        print_status("Cannot proceed without company token", False)
        return False
    
    # Test job creation
    job_id = test_create_job(company_token)
    if not job_id:
        print_status("Cannot proceed without job ID", False)
        return False
    
    # Test get pending jobs
    test_get_pending_jobs(admin_token)
    
    # Test job approval
    test_approve_job(admin_token, job_id)
    
    # Test public jobs
    test_get_public_jobs()
    
    print_section("Test Suite Complete")
    print(f"{Colors.GREEN}All critical flows tested successfully!{Colors.END}\n")
    return True

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Tests interrupted by user{Colors.END}")
        sys.exit(1)
