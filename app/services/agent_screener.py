import os
import logging
import httpx

_logger = logging.getLogger(__name__)

_SCREEN_URL = os.getenv(
    "SCREEN_SERVICE_URL",
    "https://hunters-screening-service-production.up.railway.app",
)
_SCREEN_KEY = os.getenv("SCREEN_API_KEY", "")


def call_agent_screen(cv_text: str, job) -> dict | None:
    """
    POST cv_text + job to the Node screening service.
    Returns the JSON response dict on success, None on any error.
    Never raises — callers must handle None gracefully.
    """
    skills_raw = job.required_skills or ""
    skills_list = [s.strip() for s in skills_raw.split(",") if s.strip()] if skills_raw else []

    payload = {
        "cv_text": cv_text,
        "job": {
            "title": job.job_title or "",
            "description": job.job_description or "",
            "required_skills": skills_list,
            "required_experience": job.min_experience or 0,
            "required_industry": job.industry_experience or "",
            "min_education": job.education_level or "",
        },
    }
    try:
        _endpoint = _SCREEN_URL if _SCREEN_URL.rstrip("/").endswith("/api/screen") else f"{_SCREEN_URL.rstrip('/')}/api/screen"
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                _endpoint,
                json=payload,
                headers={"X-API-Key": _SCREEN_KEY},
            )
        if resp.status_code == 200:
            return resp.json()
        _logger.warning(
            "Agent screen returned HTTP %d: %s", resp.status_code, resp.text[:300]
        )
        return None
    except Exception as exc:
        _logger.error("Agent screen request failed: %s", exc)
        return None
