import os
import requests
import logging
import json
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

def get_apps_script_url():
    url = os.getenv("GOOGLE_APPS_SCRIPT_URL")
    if not url or url == "your_google_apps_script_web_app_url_here":
        return None
    return url

def update_candidate_row(candidate_email: str, evaluation_dict: dict):
    """
    Sends the evaluation results to a Google Apps Script Web App webhook.
    """
    url = get_apps_script_url()
    if not url:
        logger.warning("GOOGLE_APPS_SCRIPT_URL is not configured. Skipping Google Sheets update.")
        return

    try:
        payload = {
            "action": "update_evaluation",
            "email": candidate_email,
            "evaluation": evaluation_dict
        }
        
        response = requests.post(url, json=payload)
        response.raise_for_status()
        
        logger.info(f"Successfully sent evaluation to Google Sheets for candidate {candidate_email}.")
    except Exception as e:
        logger.error(f"Failed to update Google Sheet via Webhook: {e}")

