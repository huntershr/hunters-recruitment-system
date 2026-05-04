# AI Recruitment System

An AI-powered recruitment backend system built with FastAPI, PostgreSQL (via SQLAlchemy), and the OpenAI API.

## Features
- **Job Management:** Create and list job positions with specific weights for different criteria.
- **AI-Powered CV Extraction:** Automatically extract candidate details (Name, Email, Skills, Experience) from uploaded PDF and Word CVs using Gemini.
- **Candidate Submission:** Submit candidates manually or via bulk Excel/CV uploads.
- **AI-Powered Evaluation:** Automatically evaluate candidates in the background using Google's Gemini API.
- **Evaluation Results:** View the score, decision (Shortlist/Maybe/Reject), and reasoning.
- **Google Sheets Sync:** Automatically push evaluation results to Google Sheets via Apps Script webhooks.

## Tech Stack
- FastAPI
- SQLAlchemy (SQLite/PostgreSQL)
- Google Gemini API (via google-generativeai)
- PyPDF2 & python-docx (for text extraction)
- pandas (for Excel processing)
- Google Apps Script (for Sheets integration)

## Project Structure
```
ai_recruitment/
├── .env                  # Environment variables
├── docker-compose.yml    # For running PostgreSQL locally
├── requirements.txt      # Python dependencies
├── test_data.py          # Script to run a full test pipeline
└── app/
    ├── __init__.py
    ├── main.py           # FastAPI app initialization
    ├── database.py       # Database connection and setup
    ├── models.py         # SQLAlchemy database models
    ├── schemas.py        # Pydantic schemas for data validation
    ├── services/
    │   └── ai_evaluator.py # OpenAI integration logic
    └── routers/          # API endpoint routers
        ├── jobs.py
        ├── candidates.py
        └── evaluations.py
```

## Setup Instructions

### 1. Prerequisites
- Python 3.9+
- Docker (optional, if you want to use PostgreSQL)
- OpenAI API Key

### 2. Configure Environment
Open the `.env` file and set your `GEMINI_API_KEY` and `GOOGLE_APPS_SCRIPT_URL`:

```env
GEMINI_API_KEY=your-actual-api-key-here
GOOGLE_APPS_SCRIPT_URL=your-apps-script-url
```
By default, the application will use a local SQLite database (`recruitment.db`) if no PostgreSQL `DATABASE_URL` is provided. If you want to use PostgreSQL, uncomment the `DATABASE_URL` line and run Docker.

### 3. Install Dependencies
It's recommended to create a virtual environment:
```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# Mac/Linux:
# source venv/bin/activate

pip install -r requirements.txt
```

### 4. (Optional) Start PostgreSQL using Docker
If you want to use PostgreSQL instead of SQLite, run:
```bash
docker-compose up -d
```
And make sure the `DATABASE_URL` in `.env` is uncommented and points to the PostgreSQL instance.

### 5. Run the Application
Start the FastAPI server using Uvicorn:
```bash
uvicorn app.main:app --reload
```
The server will start at `http://localhost:8000`.

### 6. Explore the API Docs
Go to `http://localhost:8000/docs` in your browser. You will see the interactive Swagger UI where you can test all the endpoints manually.

### 7. Run the Test Script
To see the system in action with sample data, open a new terminal (while the server is running) and run:
```bash
python test_data.py
```
This script will:
1. Create a Job.
2. Submit a Candidate.
3. Wait 5 seconds for the background AI evaluation to finish.
4. Fetch and print the evaluation results.

## Customization

### Changing the AI Model
By default, the AI evaluation uses `gpt-4o-mini` for fast and cost-effective processing. If you wish to use Anthropic's Claude instead, you can either:
1. Use a proxy service (like OpenRouter or Together AI) by changing the `base_url` in `app/services/ai_evaluator.py`.
2. Or install the `anthropic` Python package and modify `evaluate_candidate` to use `anthropic.Anthropic().messages.create(...)`.
