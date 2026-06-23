from datetime import date
from fastapi import HTTPException

PLAN_LIMITS = {
    "starter": {
        "jobs": 3,
        "bulk_screenings_per_month": 90,
        "invitations_per_month": 30,
        "users": 2,
        "cvs_per_job": 150,
    },
    "growth": {
        "jobs": 8,
        "bulk_screenings_per_month": 140,
        "invitations_per_month": 50,
        "users": 2,
        "cvs_per_job": 150,
    },
    "enterprise": {
        "jobs": 12,
        "bulk_screenings_per_month": 240,
        "invitations_per_month": 100,
        "users": 3,
        "cvs_per_job": 150,
    },
}

# Map legacy/alias plan names to canonical keys
_PLAN_ALIAS: dict[str, str] = {
    "free": "starter",
    "starter": "starter",
    "growth": "growth",
    "professional": "growth",
    "enterprise": "enterprise",
}


def get_plan_limits(plan: str) -> dict:
    key = _PLAN_ALIAS.get((plan or "starter").lower(), "starter")
    return PLAN_LIMITS[key]


def reset_monthly_usage_if_needed(company, db) -> None:
    today = date.today()
    reset = company.usage_reset_date
    if not reset or reset.year != today.year or reset.month != today.month:
        company.bulk_screening_used_this_month = 0
        company.invitations_used_this_month = 0
        company.usage_reset_date = today
        db.commit()


def plan_limit_exceeded(resource: str, used: int, limit: int, plan: str,
                        base_limit: int = None, addon_slots: int = None):
    detail = {
        "error": "plan_limit_exceeded",
        "resource": resource,
        "used": used,
        "limit": limit,
        "plan": plan,
    }
    if base_limit is not None:
        detail["base_limit"] = base_limit
    if addon_slots is not None:
        detail["addon_slots"] = addon_slots
    raise HTTPException(status_code=403, detail=detail)
