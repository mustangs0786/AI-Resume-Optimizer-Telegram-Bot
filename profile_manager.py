"""
profile_manager.py — Per-user profile storage + self-learning
=============================================================
Storage per Telegram user_id:
  user_profiles/<user_id>/
    profile.json      ← grows automatically with every new answer
    apply_log.json    ← every application attempt

.env variables:
  APPLY_EMAIL=youremail@gmail.com
  APPLY_PASSWORD=YourPassword@123
"""

import os
import json
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

PROFILES_DIR = Path("user_profiles")
PROFILES_DIR.mkdir(exist_ok=True)

PROFILE_SKELETON = {
    "full_name": "", "first_name": "", "last_name": "",
    "phone": "", "location": "", "city": "", "country": "India",
    "linkedin": "", "portfolio": "", "github": "",
    "current_company": "", "current_title": "", "years_experience": "",
    "notice_period": "", "expected_ctc": "", "current_ctc": "",
    "willing_to_relocate": "", "work_authorization": "",
    "gender": "", "graduation_year": "", "degree": "",
    "university": "", "cgpa": "",
    "screening": {}   # learned answers: {question_lower: answer}
}

FIELD_MAP = {
    "full name": "full_name", "name": "full_name",
    "first name": "first_name", "given name": "first_name",
    "last name": "last_name", "surname": "last_name", "family name": "last_name",
    "email": "email", "email address": "email", "email id": "email",
    "phone": "phone", "phone number": "phone", "mobile": "phone",
    "mobile number": "phone", "contact number": "phone",
    "location": "location", "city": "city", "country": "country", "address": "location",
    "linkedin": "linkedin", "linkedin url": "linkedin", "linkedin profile": "linkedin",
    "portfolio": "portfolio", "website": "portfolio", "personal website": "portfolio",
    "github": "github", "github url": "github",
    "current company": "current_company", "current employer": "current_company",
    "company name": "current_company", "employer": "current_company",
    "current role": "current_title", "current title": "current_title",
    "job title": "current_title", "designation": "current_title",
    "years of experience": "years_experience", "total experience": "years_experience",
    "experience": "years_experience",
    "notice period": "notice_period", "notice": "notice_period",
    "expected ctc": "expected_ctc", "expected salary": "expected_ctc",
    "current ctc": "current_ctc", "current salary": "current_ctc",
    "willing to relocate": "willing_to_relocate", "open to relocation": "willing_to_relocate",
    "work authorization": "work_authorization", "authorized to work": "work_authorization",
    "gender": "gender",
    "graduation year": "graduation_year", "year of graduation": "graduation_year",
    "degree": "degree", "highest qualification": "degree",
    "university": "university", "college": "university", "institution": "university",
    "cgpa": "cgpa", "gpa": "cgpa", "percentage": "cgpa",
}

IMPORTANT_FIELDS = [
    "full_name", "phone", "city", "linkedin",
    "current_title", "years_experience", "notice_period",
    "expected_ctc", "current_ctc",
]


def get_user_dir(user_id: int) -> Path:
    d = PROFILES_DIR / str(user_id)
    d.mkdir(exist_ok=True)
    return d

def load_profile(user_id: int) -> dict:
    path = get_user_dir(user_id) / "profile.json"
    stored = {}
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    profile = {**PROFILE_SKELETON, **stored}
    profile["screening"] = {**PROFILE_SKELETON["screening"], **stored.get("screening", {})}
    # Always pull from .env — never stored on disk
    profile["email"]    = os.getenv("APPLY_EMAIL", stored.get("email", ""))
    profile["password"] = os.getenv("APPLY_PASSWORD", stored.get("password", ""))
    return profile

def save_profile(user_id: int, profile: dict):
    """Persist profile. email/password never stored — they stay in .env."""
    to_store = {k: v for k, v in profile.items() if k not in ("email", "password")}
    path = get_user_dir(user_id) / "profile.json"
    path.write_text(json.dumps(to_store, indent=2, ensure_ascii=False), encoding="utf-8")

def profile_exists(user_id: int) -> bool:
    path = get_user_dir(user_id) / "profile.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text())
        return bool(data.get("full_name") or data.get("phone"))
    except Exception:
        return False

def learn_answer(user_id: int, question: str, answer: str) -> dict:
    """
    Save a new answer to screening dict. Called every time user answers
    an unknown field during application. Immediately persisted.
    Returns updated profile.
    """
    profile = load_profile(user_id)
    profile["screening"][question.lower().strip()] = answer
    save_profile(user_id, profile)
    return profile

def update_field(user_id: int, key: str, value: str):
    """Update a single top-level profile field and persist."""
    profile = load_profile(user_id)
    profile[key] = value
    save_profile(user_id, profile)

def get_field_value(label: str, profile: dict) -> str | None:
    """Return profile value for a form field label. None = must ask user."""
    key = label.lower().strip()
    if key in FIELD_MAP:
        val = profile.get(FIELD_MAP[key], "")
        if val: return str(val)
    for mk, pk in FIELD_MAP.items():
        if mk in key or key in mk:
            val = profile.get(pk, "")
            if val: return str(val)
    for q, a in profile.get("screening", {}).items():
        if q in key or key in q:
            return str(a)
    return None

def get_missing_fields(profile: dict) -> list:
    return [f for f in IMPORTANT_FIELDS if not profile.get(f)]

def profile_completeness(profile: dict) -> int:
    filled = sum(1 for f in IMPORTANT_FIELDS if profile.get(f))
    return int(filled / len(IMPORTANT_FIELDS) * 100)


# ── Apply log ─────────────────────────────────────────────────────────────────

def log_application(user_id: int, entry: dict):
    path = get_user_dir(user_id) / "apply_log.json"
    log = []
    if path.exists():
        try: log = json.loads(path.read_text(encoding="utf-8"))
        except Exception: pass
    entry.setdefault("timestamp", datetime.now().isoformat())
    log.append(entry)
    path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")

def get_apply_stats(user_id: int) -> dict:
    path = get_user_dir(user_id) / "apply_log.json"
    if not path.exists(): return {"total": 0}
    try: log = json.loads(path.read_text(encoding="utf-8"))
    except Exception: return {"total": 0}
    total     = len(log)
    submitted = sum(1 for e in log if e.get("status") == "success")
    failed    = sum(1 for e in log if e.get("status") == "failed")
    portals   = {}
    for e in log:
        p = e.get("portal", "unknown")
        portals[p] = portals.get(p, 0) + 1
    last = log[-1] if log else {}
    return {
        "total": total, "submitted": submitted, "failed": failed,
        "portals": portals,
        "last_applied": last.get("timestamp", ""),
        "last_company": last.get("company", ""),
    }


# ── Extract profile from resume text ─────────────────────────────────────────

def build_extract_prompt(resume_text: str) -> str:
    return f"""Extract candidate details from this resume. Return ONLY JSON, no markdown.

{{
  "full_name": "", "first_name": "", "last_name": "",
  "phone": "", "city": "", "linkedin": "", "portfolio": "", "github": "",
  "current_company": "", "current_title": "", "years_experience": "",
  "graduation_year": "", "degree": "", "university": "", "cgpa": ""
}}

Resume:
{resume_text[:3000]}"""

def merge_resume_into_profile(user_id: int, resume_text: str, gemini_client, model: str) -> list:
    """
    Extract fields from resume text → merge into profile (only fills empty fields).
    Returns list of newly filled field names.
    """
    try:
        from google.genai import types as gt
        r = gemini_client.models.generate_content(
            model=model,
            contents=build_extract_prompt(resume_text),
            config=gt.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json"
            )
        )
        raw = r.text.strip().replace("```json","").replace("```","").strip()
        extracted = json.loads(raw)
    except Exception as e:
        print(f"  Profile extract failed: {e}")
        return []

    profile = load_profile(user_id)
    newly_filled = []
    for key, value in extracted.items():
        if value and not profile.get(key):
            profile[key] = value
            newly_filled.append(key)

    # Auto-split full_name → first/last
    if profile.get("full_name") and not profile.get("first_name"):
        parts = profile["full_name"].strip().split()
        if len(parts) >= 2:
            profile["first_name"] = parts[0]
            profile["last_name"]  = " ".join(parts[1:])
            if "first_name" not in newly_filled: newly_filled.append("first_name")
            if "last_name"  not in newly_filled: newly_filled.append("last_name")

    if newly_filled:
        save_profile(user_id, profile)
    return newly_filled
