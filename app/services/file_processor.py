import pandas as pd
from pypdf import PdfReader
import docx
import io
import logging

logger = logging.getLogger(__name__)

def extract_text_from_pdf(file_content):
    try:
        reader = PdfReader(io.BytesIO(file_content))
        text = ""
        for page in reader.pages:
            try:
                # layout mode preserves reading order in multi-column CVs
                page_text = page.extract_text(extraction_mode="layout")
            except TypeError:
                page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        return text.strip()
    except Exception as e:
        logger.error(f"Error extracting PDF: {e}")
        return ""

def extract_text_from_docx(file_content):
    try:
        doc = docx.Document(io.BytesIO(file_content))
        parts = []
        for para in doc.paragraphs:
            if para.text.strip():
                parts.append(para.text)
        # Also extract text from tables (two-column DOCX CVs use table layouts)
        for table in doc.tables:
            for row in table.rows:
                row_parts = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if row_parts:
                    parts.append("  ".join(row_parts))
        return "\n".join(parts).strip()
    except Exception as e:
        logger.error(f"Error extracting Word: {e}")
        return ""

def process_excel_candidates(file_content):
    """
    Returns a list of dictionaries from Excel for candidates.
    Expected columns: Name, Email, Phone, Experience, Education, Skills, Salary
    """
    try:
        df = pd.read_excel(io.BytesIO(file_content))
        # Convert NaN to empty string
        df = df.fillna("")
        return df.to_dict(orient="records")
    except Exception as e:
        logger.error(f"Error processing Excel candidates: {e}")
        return []

def process_excel_jobs(file_content):
    """
    Returns a list of dictionaries from Excel for jobs.
    Expected columns: Title, Description, Skills, Experience, Education, Salary Range
    """
    try:
        df = pd.read_excel(io.BytesIO(file_content))
        df = df.fillna("")
        return df.to_dict(orient="records")
    except Exception as e:
        logger.error(f"Error processing Excel jobs: {e}")
        return []

def extract_text_from_file(filename, file_content):
    if filename.endswith(".pdf"):
        return extract_text_from_pdf(file_content)
    elif filename.endswith(".docx") or filename.endswith(".doc"):
        return extract_text_from_docx(file_content)
    return ""
