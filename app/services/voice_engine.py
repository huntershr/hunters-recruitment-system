"""
Voice Engine — V1 uses browser Web Speech API (no server-side TTS/STT).
V2 scaffold: edge-tts for TTS, openai-whisper for STT.
"""
import os
import json
import logging
import google.generativeai as genai

logger = logging.getLogger(__name__)

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

_ANALYSIS_PROMPT = """You are an HR screening assistant. The candidate may have answered in Arabic, English, or a mix of both. Analyze this screening transcript and return a JSON object with exactly these keys:
- english_level: the candidate's language proficiency level, one of "Basic" / "Intermediate" / "Advanced" / "Native-like" — assess whichever language (Arabic or English) the candidate primarily used
- fluency_assessment: one sentence describing speech fluency
- clarity_assessment: one sentence describing answer clarity
- experience_match: one sentence on relevance of experience to the role
- language_notes: note which language the candidate used (Arabic, English, or mixed) and any specific observations
- ai_summary: exactly 3 bullet points (plain text, each starting with "• ") covering (1) relevant experience summary, (2) availability and expected salary, (3) whether the candidate had additional questions

Job Title: {job_title}
Transcript:
{transcript}

Return ONLY valid JSON. No markdown. No preamble."""


class VoiceEngine:

    @staticmethod
    def speak(text: str) -> str:
        """V1: return text to frontend — browser SpeechSynthesis speaks it.
           V2: generate audio via edge-tts, return file path."""
        return text

    @staticmethod
    def transcribe(audio_data) -> str:
        """V1: transcript arrives from browser Web Speech API — pass through.
           V2: run openai-whisper on audio_data bytes."""
        return str(audio_data or "")

    @staticmethod
    def analyze_with_gemini(transcript: str, job_title: str) -> dict:
        model_name = os.getenv("GEMINI_MODEL", "models/gemini-2.5-flash")
        prompt = _ANALYSIS_PROMPT.format(job_title=job_title, transcript=transcript)
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(temperature=0.2),
            )
            raw = (response.text or "").strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw)
        except Exception as exc:
            logger.error(f"Gemini voice analysis failed: {exc}")
            return {
                "english_level": "Unknown",
                "fluency_assessment": "Analysis unavailable",
                "clarity_assessment": "Analysis unavailable",
                "experience_match": "Analysis unavailable",
                "language_notes": "",
                "ai_summary": "• Analysis unavailable\n• —\n• —",
            }
