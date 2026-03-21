"""
apply_handler.py — Self-contained auto-apply module
====================================================
Drop this file in the same folder as bot.py.

.env variables needed (add these):
    APPLY_EMAIL=youremail@gmail.com
    APPLY_PASSWORD=YourPassword@123

Install:
    pip install playwright
    playwright install chromium

4 minimal changes needed in bot.py — see bot_changes.txt
"""

import os
import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import Callable, Optional

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, CommandHandler, filters
from telegram.constants import ParseMode, ChatAction
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ── New conversation states (10 and 11 — bot.py uses 0-9) ────────────────────
WAITING_APPLY_CONFIRM = 10
WAITING_APPLY_STUCK   = 11

# ── Per-user reply queues (for stuck field answers + email verify) ────────────
_reply_queues: dict[int, asyncio.Queue] = {}

def get_reply_queue(user_id: int) -> asyncio.Queue:
    if user_id not in _reply_queues:
        _reply_queues[user_id] = asyncio.Queue()
    return _reply_queues[user_id]


# ══════════════════════════════════════════════════════════════════════════════
# PROFILE MANAGER
# Stored at: user_profiles/<telegram_user_id>/profile.json
#            user_profiles/<telegram_user_id>/apply_log.json
# email + password NEVER stored on disk — always from .env
# ══════════════════════════════════════════════════════════════════════════════

PROFILES_DIR = Path("user_profiles")
PROFILES_DIR.mkdir(exist_ok=True)

FIELD_MAP = {
    "full name": "full_name", "name": "full_name",
    "first name": "first_name", "given name": "first_name",
    "last name": "last_name", "surname": "last_name",
    "email": "email", "email address": "email", "email id": "email",
    "phone": "phone", "phone number": "phone", "mobile": "phone", "contact number": "phone",
    "location": "location", "city": "city", "country": "country", "address": "location",
    "linkedin": "linkedin", "linkedin url": "linkedin", "linkedin profile": "linkedin",
    "portfolio": "portfolio", "website": "portfolio",
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
    "willing to relocate": "willing_to_relocate",
    "work authorization": "work_authorization", "authorized to work": "work_authorization",
    "gender": "gender",
    "graduation year": "graduation_year", "year of graduation": "graduation_year",
    "degree": "degree", "highest qualification": "degree",
    "university": "university", "college": "university", "institution": "university",
    "cgpa": "cgpa", "gpa": "cgpa", "percentage": "cgpa",
}

def _profile_path(user_id: int) -> Path:
    d = PROFILES_DIR / str(user_id)
    d.mkdir(exist_ok=True)
    return d / "profile.json"

def load_profile(user_id: int) -> dict:
    path = _profile_path(user_id)
    stored = {}
    if path.exists():
        try: stored = json.loads(path.read_text(encoding="utf-8"))
        except Exception: pass
    profile = {**stored}
    profile.setdefault("screening", {})
    # Always from .env — never stored to disk
    profile["email"]    = os.getenv("APPLY_EMAIL", stored.get("email", ""))
    profile["password"] = os.getenv("APPLY_PASSWORD", stored.get("password", ""))
    return profile

def save_profile(user_id: int, profile: dict):
    to_store = {k: v for k, v in profile.items() if k not in ("email", "password")}
    _profile_path(user_id).write_text(
        json.dumps(to_store, indent=2, ensure_ascii=False), encoding="utf-8"
    )

def learn_answer(user_id: int, question: str, answer: str) -> dict:
    """Save new answer immediately — self-learning."""
    profile = load_profile(user_id)
    profile["screening"][question.lower().strip()] = answer
    save_profile(user_id, profile)
    return profile

def get_field_value(label: str, profile: dict) -> Optional[str]:
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

def merge_resume_into_profile(user_id: int, resume_text: str, gemini_client, model: str) -> list:
    """Extract fields from resume text → fill empty profile fields. Returns list of newly filled keys."""
    prompt = f"""Extract candidate details from this resume. Return ONLY a JSON object, no markdown.
Keys: full_name, first_name, last_name, phone, city, linkedin, portfolio, github,
      current_company, current_title, years_experience, graduation_year, degree, university, cgpa
Empty string if not found.
Resume: {resume_text[:3000]}"""
    try:
        from google.genai import types as gt
        r = gemini_client.models.generate_content(
            model=model,
            contents=prompt,
            config=gt.GenerateContentConfig(temperature=0.1, response_mime_type="application/json")
        )
        extracted = json.loads(r.text.strip().replace("```json","").replace("```","").strip())
    except Exception as e:
        logger.warning(f"Profile extract failed: {e}")
        return []

    profile = load_profile(user_id)
    newly_filled = []
    for key, value in extracted.items():
        if value and not profile.get(key):
            profile[key] = value
            newly_filled.append(key)
    if profile.get("full_name") and not profile.get("first_name"):
        parts = profile["full_name"].strip().split()
        if len(parts) >= 2:
            profile["first_name"] = parts[0]
            profile["last_name"]  = " ".join(parts[1:])
    if newly_filled:
        save_profile(user_id, profile)
    return newly_filled

def log_application(user_id: int, entry: dict):
    path = PROFILES_DIR / str(user_id) / "apply_log.json"
    log = []
    if path.exists():
        try: log = json.loads(path.read_text(encoding="utf-8"))
        except Exception: pass
    entry.setdefault("timestamp", datetime.now().isoformat())
    log.append(entry)
    path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")

def get_apply_stats(user_id: int) -> dict:
    path = PROFILES_DIR / str(user_id) / "apply_log.json"
    if not path.exists(): return {"total": 0}
    try: log = json.loads(path.read_text(encoding="utf-8"))
    except Exception: return {"total": 0}
    portals = {}
    for e in log:
        p = e.get("portal", "unknown")
        portals[p] = portals.get(p, 0) + 1
    last = log[-1] if log else {}
    return {
        "total":        len(log),
        "submitted":    sum(1 for e in log if e.get("status") == "success"),
        "failed":       sum(1 for e in log if e.get("status") == "failed"),
        "portals":      portals,
        "last_applied": last.get("timestamp", "")[:10],
        "last_company": last.get("company", ""),
    }


# ══════════════════════════════════════════════════════════════════════════════
# AUTO APPLY ENGINE (Playwright + Gemini HTML field detection)
# ══════════════════════════════════════════════════════════════════════════════

def detect_portal(url: str) -> str:
    u = url.lower()
    if "lever.co"        in u: return "lever"
    if "greenhouse.io"   in u: return "greenhouse"
    if "ashbyhq.com"     in u: return "ashby"
    if "linkedin.com"    in u: return "linkedin"
    if "workday.com"     in u: return "workday"
    if "myworkdayjobs"   in u: return "workday"
    if "smartrecruiters" in u: return "smartrecruiters"
    return "custom"

class ApplyResult:
    def __init__(self):
        self.status         = "pending"
        self.portal         = "unknown"
        self.company        = ""
        self.job_title      = ""
        self.fields_filled  = []
        self.fields_skipped = []
        self.fields_learned = []
        self.screenshot_path= ""
        self.error          = ""

async def _gemini_detect_fields(html: str, gemini_client, model: str) -> list:
    prompt = f"""You are analyzing the HTML of a job application form.
List every input field the candidate needs to fill.
Return ONLY a JSON array — no markdown.
Each item: {{"label": "field label", "name": "input name/id attr", "type": "text|email|phone|textarea|select|file|checkbox"}}
Ignore: hidden inputs, submit buttons, CSRF tokens.
HTML: {html[:8000]}"""
    try:
        from google.genai import types as gt
        r = gemini_client.models.generate_content(
            model=model, contents=prompt,
            config=gt.GenerateContentConfig(temperature=0.1, response_mime_type="application/json")
        )
        return json.loads(r.text.strip().replace("```json","").replace("```","").strip())
    except Exception as e:
        logger.warning(f"Field detection failed: {e}")
        return []

async def _gemini_answer_field(question: str, profile: dict, gemini_client, model: str) -> Optional[str]:
    safe = {k: v for k, v in profile.items() if k != "password" and v}
    prompt = f"""You are filling a job application on behalf of a candidate.
Candidate profile: {json.dumps(safe, indent=2)}
Form field: "{question}"
Give a SHORT direct answer (1 sentence or less) from the candidate's profile.
If you cannot answer from the profile, reply with exactly: UNSURE"""
    try:
        from google.genai import types as gt
        r = gemini_client.models.generate_content(
            model=model, contents=prompt,
            config=gt.GenerateContentConfig(temperature=0.2)
        )
        answer = r.text.strip()
        return None if answer == "UNSURE" else answer
    except Exception as e:
        logger.warning(f"Field answer failed: {e}")
        return None

async def _safe_fill(page, selector: str, value: str, label: str = "") -> bool:
    try:
        el = page.locator(selector).first
        if await el.count() == 0 or not await el.is_visible(): return False
        await el.click()
        await el.fill("")
        await el.type(value, delay=25)
        logger.info(f"    Filled '{label or selector}'")
        return True
    except Exception: return False

async def _safe_upload(page, resume_path: str, result: ApplyResult) -> bool:
    for sel in ["input[type='file']", "input[accept*='pdf']", "#resume"]:
        try:
            if await page.locator(sel).count() > 0:
                await page.set_input_files(sel, resume_path)
                result.fields_filled.append("Resume")
                return True
        except Exception: continue
    result.fields_skipped.append("Resume")
    return False

# Known portal templates (Layer 1 — fast, reliable selectors)
SKIP_LABELS = {
    "name","email","phone","resume","cover letter","linkedin","portfolio",
    "github","website","first name","last name","full name","email address",
    "phone number","mobile number",
}

async def _fill_greenhouse(page, profile, resume_path, result):
    for sel, val, label in [
        ("#first_name",  profile.get("first_name",""),  "First name"),
        ("#last_name",   profile.get("last_name",""),   "Last name"),
        ("#email",       profile.get("email",""),        "Email"),
        ("#phone",       profile.get("phone",""),        "Phone"),
    ]:
        if val and await _safe_fill(page, sel, val, label):
            result.fields_filled.append(label)
    for sel in ["#job_application_location", "input[placeholder*='ocation' i]"]:
        if await _safe_fill(page, sel, profile.get("city",""), "Location"):
            result.fields_filled.append("Location"); break
    for sel in ["input[name*='linkedin' i]", "input[placeholder*='linkedin' i]"]:
        if await _safe_fill(page, sel, profile.get("linkedin",""), "LinkedIn"):
            result.fields_filled.append("LinkedIn"); break
    await _safe_upload(page, resume_path, result)

async def _fill_lever(page, profile, resume_path, result):
    for sel, val, label in [
        ("input[name='name']",           profile.get("full_name",""),       "Full name"),
        ("input[name='email']",          profile.get("email",""),           "Email"),
        ("input[name='phone']",          profile.get("phone",""),           "Phone"),
        ("input[name='org']",            profile.get("current_company",""), "Company"),
        ("input[name='urls[LinkedIn]']", profile.get("linkedin",""),        "LinkedIn"),
        ("input[name='urls[Portfolio]']",profile.get("portfolio",""),       "Portfolio"),
        ("input[name='urls[GitHub]']",   profile.get("github",""),          "GitHub"),
    ]:
        if val and await _safe_fill(page, sel, val, label):
            result.fields_filled.append(label)
    await _safe_upload(page, resume_path, result)

async def _fill_ashby(page, profile, resume_path, result):
    for sel, val, label in [
        ("input[name='name']",  profile.get("full_name",""), "Full name"),
        ("input[name='email']", profile.get("email",""),     "Email"),
        ("input[name='phone']", profile.get("phone",""),     "Phone"),
    ]:
        if val and await _safe_fill(page, sel, val, label):
            result.fields_filled.append(label)
    await _safe_upload(page, resume_path, result)

# AI-driven fill for unknown portals (Layer 2)
async def _fill_with_ai(page, profile, resume_path, result, user_id, gemini_client, model, on_stuck):
    try: html = await page.content()
    except Exception: return

    fields = await _gemini_detect_fields(html, gemini_client, model)
    logger.info(f"  AI detected {len(fields)} fields")

    for field in fields:
        label = field.get("label", "").strip()
        ftype = field.get("type", "text")
        fname = field.get("name", "")
        if not label: continue
        if ftype == "file":
            await _safe_upload(page, resume_path, result); continue
        if any(s in label.lower() for s in SKIP_LABELS): continue

        # Layer 1: profile lookup
        value = get_field_value(label, profile)
        # Layer 2: Gemini answer from profile context
        if not value:
            value = await _gemini_answer_field(label, profile, gemini_client, model)
        # Layer 3: ask user
        if not value and on_stuck:
            logger.info(f"  Asking user for: '{label}'")
            value = await on_stuck(label)
            if value:
                updated = learn_answer(user_id, label, value)
                profile.update(updated)
                result.fields_learned.append(f"{label}: {value}")

        if not value:
            result.fields_skipped.append(label); continue

        # Try to locate and fill the element
        filled = False
        if fname:
            for sel in [f"[name='{fname}']", f"#{fname}"]:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        await el.fill(value); filled = True; break
                except Exception: continue
        if not filled:
            try:
                for_id = await page.locator(f"label:has-text('{label}')").first.get_attribute("for")
                if for_id:
                    await page.locator(f"#{for_id}").first.fill(value); filled = True
            except Exception: pass
        if not filled:
            for sel in [f"[aria-label='{label}']", f"[placeholder*='{label[:20]}']"]:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        await el.fill(value); filled = True; break
                except Exception: continue

        if filled:
            result.fields_filled.append(label)
            logger.info(f"    Filled '{label}'")
        else:
            result.fields_skipped.append(label)

async def _try_submit(page, result) -> bool:
    for sel in [
        "button[type='submit']",
        "button:has-text('Submit Application')",
        "button:has-text('Submit')",
        "button:has-text('Apply Now')",
        "button:has-text('Apply')",
        "input[type='submit']",
    ]:
        try:
            btn = page.locator(sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                await page.wait_for_load_state("networkidle", timeout=20000)
                return True
        except Exception: continue
    return False

async def run_apply(
    job_url:      str,
    resume_path:  str,
    user_id:      int,
    gemini_client,
    model:        str,
    on_stuck:     Callable = None,
    on_verify:    Callable = None,
    on_screenshot:Callable = None,
    dry_run:      bool = True,
) -> ApplyResult:
    from playwright.async_api import async_playwright

    profile = load_profile(user_id)
    result  = ApplyResult()
    result.portal = detect_portal(job_url)
    Path("output").mkdir(exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await ctx.new_page()

        try:
            await page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2500)

            # Extract job title / company from page title
            try:
                t = await page.title()
                result.job_title = t.split("|")[0].split("–")[0].strip()[:80]
                result.company   = (t.split("|")[-1].strip() if "|" in t else "")[:60]
            except Exception: pass

            # Login/signup check
            if await page.locator("input[type='password'], text=Sign in, text=Create account").count() > 0:
                # Fill email + password
                for sel, val in [
                    ("input[type='email'], input[name='email']",         profile["email"]),
                    ("input[type='password'], input[name='password']",   profile["password"]),
                    ("input[name='firstName'], input[name='first_name']",profile.get("first_name","")),
                    ("input[name='lastName'],  input[name='last_name']", profile.get("last_name","")),
                ]:
                    if val:
                        try:
                            el = page.locator(sel).first
                            if await el.count() > 0 and await el.is_visible():
                                await el.fill(val)
                        except Exception: continue
                # Submit signup form
                for sel in ["button[type='submit']", "button:has-text('Continue')", "button:has-text('Sign up')"]:
                    try:
                        btn = page.locator(sel).first
                        if await btn.count() > 0 and await btn.is_visible():
                            await btn.click()
                            await page.wait_for_load_state("networkidle", timeout=10000)
                            break
                    except Exception: continue
                # Email verification
                if on_verify:
                    verified = await on_verify(
                        f"Check *{profile['email']}* for a verification email. Click the link, then reply *done*."
                    )
                    if verified:
                        await page.wait_for_load_state("networkidle", timeout=20000)

            await page.wait_for_load_state("networkidle", timeout=15000)
            await page.wait_for_timeout(1500)

            # Fill known portals with templates
            if result.portal == "greenhouse":
                await _fill_greenhouse(page, profile, resume_path, result)
            elif result.portal == "lever":
                await _fill_lever(page, profile, resume_path, result)
            elif result.portal == "ashby":
                await _fill_ashby(page, profile, resume_path, result)

            # AI fills remaining / unknown fields
            await _fill_with_ai(page, profile, resume_path, result, user_id, gemini_client, model, on_stuck)
            await page.wait_for_timeout(1000)

            # Screenshot
            ss = f"output/apply_{user_id}_{result.portal}.png"
            await page.screenshot(path=ss, full_page=True)
            result.screenshot_path = ss
            if on_screenshot:
                await on_screenshot(ss)

            # Submit
            if dry_run:
                result.status = "dry_run"
            else:
                ok = await _try_submit(page, result)
                result.status = "success" if ok else "failed"
                if not ok: result.error = "Submit button not found"
                else:
                    await page.wait_for_timeout(3000)
                    ss2 = f"output/apply_{user_id}_submitted.png"
                    await page.screenshot(path=ss2, full_page=True)
                    if on_screenshot: await on_screenshot(ss2)

        except Exception as e:
            result.status = "failed"
            result.error  = str(e)
            logger.error(f"Apply error: {e}")
            try:
                ss_err = f"output/apply_{user_id}_error.png"
                await page.screenshot(path=ss_err)
                result.screenshot_path = ss_err
                if on_screenshot: await on_screenshot(ss_err)
            except Exception: pass
        finally:
            await browser.close()

    log_application(user_id, {
        "job_url": job_url, "job_title": result.job_title, "company": result.company,
        "portal": result.portal, "status": result.status,
        "fields_filled": result.fields_filled, "fields_skipped": result.fields_skipped,
        "fields_learned": result.fields_learned, "error": result.error,
    })
    return result


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

async def show_apply_prompt(message, context, job_url: str = ""):
    """
    Call this after resume PDF is sent to user (both optimize + update paths).
    Shows 'Shall I apply now?' button.
    """
    if job_url:
        context.user_data["pending_job_url"] = job_url

    has_url = bool(context.user_data.get("pending_job_url") or context.user_data.get("job_url"))

    if has_url:
        # Use whichever URL we have
        if not context.user_data.get("pending_job_url"):
            context.user_data["pending_job_url"] = context.user_data.get("job_url","")
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🚀 Yes, apply now!", callback_data="apply_now"),
                InlineKeyboardButton("❌ No thanks",       callback_data="apply_skip"),
            ]
        ])
        await message.reply_text(
            "🎯 *Resume is ready!*\n\nShall I auto-fill and submit the job application now?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
    else:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📋 Yes, I'll paste the job URL", callback_data="apply_want_url"),
                InlineKeyboardButton("❌ No thanks",                    callback_data="apply_skip"),
            ]
        ])
        await message.reply_text(
            "🎯 *Resume is ready!*\n\nWant me to auto-apply to a job? Paste the job URL.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
    return WAITING_APPLY_CONFIRM


async def handle_apply_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User tapped 'Yes, apply now!'"""
    query   = update.callback_query
    user_id = update.effective_user.id
    await query.answer()

    job_url     = context.user_data.get("pending_job_url", "")
    resume_path = context.user_data.get("last_pdf_path") or context.user_data.get("resume_path","")

    if not job_url:
        await query.edit_message_text("Please paste the job URL:")
        return WAITING_APPLY_CONFIRM

    if not resume_path or not Path(resume_path).exists():
        await query.edit_message_text("❌ Resume not found. Please /start and upload again.")
        return

    await query.edit_message_text(
        "🤖 *Starting application...*\n\nI'll fill the form and ask if I get stuck.\n"
        "You'll see a screenshot to review before I submit.",
        parse_mode=ParseMode.MARKDOWN,
    )
    asyncio.create_task(_run_apply_task(update, context, user_id, job_url, resume_path))
    return WAITING_APPLY_STUCK


async def handle_apply_want_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User wants to apply but didn't have a URL — ask for it."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Please paste the job application URL:")
    return WAITING_APPLY_CONFIRM


async def handle_apply_url_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User pasted a job URL while in WAITING_APPLY_CONFIRM."""
    text = update.message.text.strip()
    if not text.startswith("http"):
        await update.message.reply_text("That doesn't look like a URL. Please paste the full https:// link.")
        return WAITING_APPLY_CONFIRM

    context.user_data["pending_job_url"] = text
    resume_path = context.user_data.get("last_pdf_path") or context.user_data.get("resume_path","")
    user_id = update.effective_user.id

    if not resume_path or not Path(resume_path).exists():
        await update.message.reply_text("❌ Resume not found. Please /start and upload again.")
        return

    await update.message.reply_text(
        "🤖 *Starting application...*\n\nI'll fill the form and ask if I get stuck.",
        parse_mode=ParseMode.MARKDOWN,
    )
    asyncio.create_task(_run_apply_task(update, context, user_id, text, resume_path))
    return WAITING_APPLY_STUCK


async def handle_apply_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("No problem! Your resume is saved. Use /start anytime.")
    return 1  # WAITING_MAIN_CHOICE


async def handle_apply_stuck_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Any text while WAITING_APPLY_STUCK = user answering a stuck field."""
    user_id = update.effective_user.id
    text    = update.message.text.strip()
    await get_reply_queue(user_id).put(text)
    await update.message.reply_text(
        f"✅ Got it: *{text}*\n_Continuing..._",
        parse_mode=ParseMode.MARKDOWN,
    )
    return WAITING_APPLY_STUCK


async def handle_apply_submit_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User confirmed 'Submit application!'"""
    query   = update.callback_query
    user_id = update.effective_user.id
    await query.answer()
    await query.edit_message_text("🚀 Submitting now...")

    job_url     = context.user_data.get("pending_job_url","")
    resume_path = context.user_data.get("last_pdf_path") or context.user_data.get("resume_path","")
    chat_id     = update.effective_chat.id
    bot         = context.application.bot
    queue       = get_reply_queue(user_id)
    gemini_client = context.application.bot_data.get("gemini_client")
    model         = context.application.bot_data.get("model", "gemini-2.0-flash")

    async def on_stuck(q): # noqa
        await bot.send_message(chat_id=chat_id,
            text=f"❓ *Form is asking:*\n\n`{q}`\n\nPlease reply:",
            parse_mode=ParseMode.MARKDOWN)
        try: return await asyncio.wait_for(queue.get(), timeout=180)
        except asyncio.TimeoutError: return ""

    async def on_screenshot(path):
        try:
            with open(path,"rb") as f:
                await bot.send_photo(chat_id=chat_id, photo=f, caption="📸 Submitted!")
        except Exception: pass

    result = await run_apply(
        job_url=job_url, resume_path=resume_path, user_id=user_id,
        gemini_client=gemini_client, model=model,
        on_stuck=on_stuck, on_screenshot=on_screenshot,
        dry_run=False,
    )
    await _send_result_message(bot, chat_id, result)
    return 1  # WAITING_MAIN_CHOICE


async def handle_apply_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Application cancelled. Use /start to go back.")
    return 1  # WAITING_MAIN_CHOICE


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/report — show application stats."""
    user_id = update.effective_user.id
    stats   = get_apply_stats(user_id)
    if stats["total"] == 0:
        await update.message.reply_text("No applications yet. Use /start to get going!")
        return
    portals = "\n".join(f"  • {p}: {c}" for p, c in stats.get("portals",{}).items())
    await update.message.reply_text(
        f"📊 *Application Report*\n\n"
        f"Total      : *{stats['total']}*\n"
        f"Submitted  : *{stats['submitted']}*\n"
        f"Failed     : *{stats['failed']}*\n\n"
        f"*By portal:*\n{portals}\n\n"
        f"Last applied : {stats.get('last_applied','—')}\n"
        f"Last company : {stats.get('last_company','—')}",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _run_apply_task(update, context, user_id, job_url, resume_path):
    """Runs as asyncio.create_task — keeps bot responsive during apply."""
    bot     = context.application.bot
    chat_id = update.effective_chat.id
    queue   = get_reply_queue(user_id)
    gemini_client = context.application.bot_data.get("gemini_client")
    model         = context.application.bot_data.get("model", "gemini-2.0-flash")

    async def on_stuck(question: str) -> str:
        await bot.send_message(
            chat_id=chat_id,
            text=(f"❓ *The form is asking:*\n\n`{question}`\n\n"
                  "Reply with your answer — I'll fill it and remember it for next time."),
            parse_mode=ParseMode.MARKDOWN,
        )
        try: return await asyncio.wait_for(queue.get(), timeout=180)
        except asyncio.TimeoutError:
            await bot.send_message(chat_id=chat_id, text="⏰ No reply — skipping that field.")
            return ""

    async def on_verify(message: str) -> bool:
        await bot.send_message(chat_id=chat_id,
            text=f"📧 *Email verification needed*\n\n{message}", parse_mode=ParseMode.MARKDOWN)
        try:
            reply = await asyncio.wait_for(queue.get(), timeout=300)
            return "done" in reply.lower()
        except asyncio.TimeoutError: return False

    async def on_screenshot(path: str):
        try:
            with open(path,"rb") as f:
                await bot.send_photo(chat_id=chat_id, photo=f,
                                     caption="📸 Here's the filled form — review before submitting")
        except Exception as e:
            logger.warning(f"Screenshot send failed: {e}")

    try:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        result = await run_apply(
            job_url=job_url, resume_path=resume_path, user_id=user_id,
            gemini_client=gemini_client, model=model,
            on_stuck=on_stuck, on_verify=on_verify, on_screenshot=on_screenshot,
            dry_run=True,   # fill but don't submit yet — user confirms first
        )

        if result.status == "dry_run":
            filled_text  = "\n".join(f"  ✅ {f}" for f in result.fields_filled[:10])
            learned_text = (
                "\n\n💾 *Saved to your profile:*\n" +
                "\n".join(f"  • {l}" for l in result.fields_learned)
            ) if result.fields_learned else ""
            skipped_text = (
                "\n\n⚠️ *Could not fill:*\n" +
                "\n".join(f"  • {f}" for f in result.fields_skipped[:5])
            ) if result.fields_skipped else ""

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Submit application!", callback_data="apply_submit_confirm"),
                    InlineKeyboardButton("❌ Cancel",              callback_data="apply_cancel"),
                ]
            ])
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"✅ *Form filled!*\n\n"
                    f"*Fields filled:*\n{filled_text}"
                    f"{learned_text}{skipped_text}\n\n"
                    "Check the screenshot above, then confirm submission."
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
            )
            # Store for confirm handler
            context.user_data["pending_job_url"] = job_url
            context.user_data["pending_resume"]  = resume_path

        elif result.status == "failed":
            await bot.send_message(
                chat_id=chat_id,
                text=f"❌ *Application failed*\n\n`{result.error or 'Unknown error'}`",
                parse_mode=ParseMode.MARKDOWN,
            )

    except Exception as e:
        logger.error(f"Apply task error user {user_id}: {e}")
        await bot.send_message(chat_id=chat_id,
            text=f"❌ Something went wrong: `{e}`", parse_mode=ParseMode.MARKDOWN)


async def _send_result_message(bot, chat_id: int, result: ApplyResult):
    learned = (
        "\n\n💾 *Saved to your profile:*\n" +
        "\n".join(f"  • {l}" for l in result.fields_learned)
    ) if result.fields_learned else ""

    if result.status == "success":
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"🎉 *Application submitted!*\n\n"
                f"🏢 Company : {result.company or 'N/A'}\n"
                f"💼 Role    : {result.job_title or 'N/A'}\n"
                f"🌐 Portal  : {result.portal}\n"
                f"✅ Fields  : {len(result.fields_filled)} filled"
                f"{learned}\n\n_Good luck! 🤞_"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await bot.send_message(
            chat_id=chat_id,
            text=f"❌ Submission failed: `{result.error}`\n\nPlease apply manually.",
            parse_mode=ParseMode.MARKDOWN,
        )


# ══════════════════════════════════════════════════════════════════════════════
# get_apply_handlers() — call this in bot.py main() to register everything
# ══════════════════════════════════════════════════════════════════════════════

def get_apply_handlers():
    """
    Returns (conv_states_dict, global_handlers_list, commands_list).
    Use in bot.py main() — see bot_changes.txt for exact insertion points.
    """
    conv_states = {
        WAITING_APPLY_CONFIRM: [
            CallbackQueryHandler(handle_apply_now,      pattern="^apply_now$"),
            CallbackQueryHandler(handle_apply_want_url, pattern="^apply_want_url$"),
            CallbackQueryHandler(handle_apply_skip,     pattern="^apply_skip$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_apply_url_input),
        ],
        WAITING_APPLY_STUCK: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_apply_stuck_reply),
        ],
    }
    global_handlers = [
        CallbackQueryHandler(handle_apply_submit_confirm, pattern="^apply_submit_confirm$"),
        CallbackQueryHandler(handle_apply_cancel,         pattern="^apply_cancel$"),
        CallbackQueryHandler(handle_apply_now,            pattern="^apply_now$"),
        CallbackQueryHandler(handle_apply_skip,           pattern="^apply_skip$"),
    ]
    commands = [
        CommandHandler("report", cmd_report),
    ]
    return conv_states, global_handlers, commands
