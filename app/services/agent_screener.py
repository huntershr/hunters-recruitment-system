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
    try:
        import re as _re
        skills_raw = getattr(job, "required_skills", None) or ""
        # Handle both comma-separated (legacy) and newline-separated (current) formats
        raw_items = [s.strip() for s in _re.split(r"[,\n]+", skills_raw) if s.strip()]
        # Parse * prefix: starred skills become essential_skills; strip * from the skills list
        essential_from_stars = [s.lstrip("*").strip() for s in raw_items if s.startswith("*")]
        skills_list = [s.lstrip("*").strip() for s in raw_items]
        # Prefer explicit deal_breakers (new UI), then essential_skills DB column, then *-prefix parse
        deal_breakers = (
            getattr(job, "deal_breakers", None) or
            getattr(job, "essential_skills", None) or
            essential_from_stars or
            []
        )

        payload = {
            "cv_text": cv_text,
            "job": {
                "title": getattr(job, "job_title", None) or "",
                "description": getattr(job, "job_description", None) or "",
                "required_skills": skills_list,
                "required_experience": getattr(job, "min_experience", None) or 0,
                "required_industry": getattr(job, "department", None) or getattr(job, "industry_experience", None) or "",
                "min_education": getattr(job, "education_level", None) or "",
                "weights": {
                    "title":      getattr(job, "agent_weight_title",      None) or 25,
                    "industry":   getattr(job, "agent_weight_industry",   None) or 25,
                    "experience": getattr(job, "agent_weight_experience", None) or 25,
                    "skills":     getattr(job, "agent_weight_skills",     None) or 25,
                },
                "deal_breakers": deal_breakers,
            },
        }
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
