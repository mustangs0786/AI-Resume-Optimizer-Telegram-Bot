"""
auto_apply.py — Automated Job Application Filler
=================================================
Uses Playwright + Gemini (text LLM, NOT vision).
Field detection: grab page HTML → send to Gemini → get field list → fill.

Install:
    pip install playwright google-genai python-dotenv
    playwright install chromium

.env:
    GEMINI_API_KEY=...
    APPLY_EMAIL=youremail@gmail.com
    APPLY_PASSWORD=YourPassword@123

Usage (from bot.py):
    result = await apply_to_job(
        job_url     = "https://jobs.lever.co/...",
        resume_path = "user_profiles/123/resume_v1.pdf",
        user_id     = 123456789,
        gemini_client = gemini_client,
        on_stuck    = async fn(question) -> str,   # asks user via Telegram
        on_verify   = async fn(message) -> bool,   # email verification
        dry_run     = True,
    )
"""

import os
import re
import json
import asyncio
from pathlib import Path
from typing import Callable, Optional
from dotenv import load_dotenv

load_dotenv()

from profile_manager import (
    load_profile, learn_answer, log_application,
    get_field_value, FIELD_MAP,
)


# ── Portal detection ──────────────────────────────────────────────────────────

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


# ── Result model ──────────────────────────────────────────────────────────────

class ApplyResult:
    def __init__(self):
        self.status: str        = "pending"
        self.portal: str        = "unknown"
        self.company: str       = ""
        self.job_title: str     = ""
        self.fields_filled: list  = []
        self.fields_skipped: list = []
        self.fields_learned: list = []   # new answers saved to profile
        self.screenshot_path: str = ""
        self.error: str           = ""
        self.notes: list          = []


# ── Gemini: detect form fields from HTML ──────────────────────────────────────

def build_field_detection_prompt(html: str) -> str:
    return f"""You are analyzing the HTML of a job application form page.

Extract every visible input field the candidate needs to fill.
Return ONLY a JSON array. No markdown. No explanation.

Each item:
{{
  "label": "exact field label text",
  "name": "input name or id attribute if visible",
  "type": "text | email | phone | textarea | select | file | checkbox | radio",
  "required": true or false,
  "options": ["option1", "option2"]   // only for select/radio, else empty list
}}

Ignore: hidden inputs, submit buttons, CSRF tokens, navigation.
Focus on: text fields, dropdowns, file uploads, checkboxes the candidate must answer.

HTML (trimmed):
{html[:8000]}"""

def build_field_answer_prompt(question: str, profile: dict) -> str:
    safe = {k: v for k, v in profile.items() if k != "password" and v}
    return f"""You are filling a job application on behalf of a candidate.

Candidate profile:
{json.dumps(safe, indent=2)}

Application form field: "{question}"

Reply with a SHORT direct answer (1 sentence or fewer) using only the candidate's profile.
If you cannot answer from the profile, reply with exactly: UNSURE

Answer only. No explanation."""

async def gemini_detect_fields(html: str, gemini_client, model: str) -> list:
    """Send page HTML to Gemini, get structured field list back."""
    try:
        from google.genai import types as gt
        r = gemini_client.models.generate_content(
            model=model,
            contents=build_field_detection_prompt(html),
            config=gt.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json"
            )
        )
        raw = r.text.strip().replace("```json","").replace("```","").strip()
        return json.loads(raw)
    except Exception as e:
        print(f"  ⚠ Gemini field detection failed: {e}")
        return []

async def gemini_answer_field(question: str, profile: dict, gemini_client, model: str) -> Optional[str]:
    """Ask Gemini to answer a field from profile context."""
    try:
        from google.genai import types as gt
        r = gemini_client.models.generate_content(
            model=model,
            contents=build_field_answer_prompt(question, profile),
            config=gt.GenerateContentConfig(temperature=0.2)
        )
        answer = r.text.strip()
        return None if answer == "UNSURE" else answer
    except Exception as e:
        print(f"  ⚠ Gemini answer failed: {e}")
        return None


# ── Safe fill helpers ─────────────────────────────────────────────────────────

async def safe_fill(page, selector: str, value: str, label: str = "") -> bool:
    try:
        el = page.locator(selector).first
        if await el.count() == 0 or not await el.is_visible():
            return False
        await el.click()
        await el.fill("")
        await el.type(value, delay=25)
        print(f"    ✅ '{label or selector}' = {value[:50]}")
        return True
    except Exception as e:
        print(f"    ⚠ Fill failed '{label}': {str(e)[:60]}")
        return False

async def safe_upload(page, resume_path: str, result: ApplyResult) -> bool:
    for sel in ["input[type='file']", "input[accept*='pdf']", "#resume", "[data-testid*='resume']"]:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                await page.set_input_files(sel, resume_path)
                print(f"    ✅ Resume uploaded")
                result.fields_filled.append("Resume")
                return True
        except Exception:
            continue
    result.fields_skipped.append("Resume")
    print(f"    ⚠ Resume upload failed")
    return False

async def safe_select(page, selector: str, value: str, label: str = "") -> bool:
    try:
        el = page.locator(selector).first
        if await el.count() == 0: return False
        try: await el.select_option(label=value)
        except Exception:
            try: await el.select_option(value=value)
            except Exception: return False
        print(f"    ✅ Selected '{label}' = {value}")
        return True
    except Exception:
        return False


# ── Known portal templates (Layer 1) ─────────────────────────────────────────

SKIP_LABELS = {
    "name", "email", "phone", "resume", "cover letter", "linkedin",
    "portfolio", "github", "website", "first name", "last name",
    "full name", "email address", "phone number", "mobile number",
}

async def fill_greenhouse(page, profile, resume_path, result):
    print("  [Greenhouse] Filling standard fields...")
    fields = [
        ("#first_name",  profile["first_name"],  "First name"),
        ("#last_name",   profile["last_name"],   "Last name"),
        ("#email",       profile["email"],        "Email"),
        ("#phone",       profile["phone"],        "Phone"),
    ]
    for sel, val, label in fields:
        if val and await safe_fill(page, sel, val, label):
            result.fields_filled.append(label)
        else:
            result.fields_skipped.append(label)
    for sel in ["#job_application_location", "input[placeholder*='ocation' i]"]:
        if await safe_fill(page, sel, profile.get("city",""), "Location"):
            result.fields_filled.append("Location"); break
    for sel in ["input[name*='linkedin' i]", "input[placeholder*='linkedin' i]"]:
        if await safe_fill(page, sel, profile.get("linkedin",""), "LinkedIn"):
            result.fields_filled.append("LinkedIn"); break
    await safe_upload(page, resume_path, result)

async def fill_lever(page, profile, resume_path, result):
    print("  [Lever] Filling standard fields...")
    fields = [
        ("input[name='name']",           profile.get("full_name",""),       "Full name"),
        ("input[name='email']",          profile.get("email",""),           "Email"),
        ("input[name='phone']",          profile.get("phone",""),           "Phone"),
        ("input[name='org']",            profile.get("current_company",""), "Company"),
        ("input[name='urls[LinkedIn]']", profile.get("linkedin",""),        "LinkedIn"),
        ("input[name='urls[Portfolio]']",profile.get("portfolio",""),       "Portfolio"),
        ("input[name='urls[GitHub]']",   profile.get("github",""),          "GitHub"),
    ]
    for sel, val, label in fields:
        if val and await safe_fill(page, sel, val, label):
            result.fields_filled.append(label)
    await safe_upload(page, resume_path, result)

async def fill_ashby(page, profile, resume_path, result):
    print("  [Ashby] Filling standard fields...")
    for sel, val, label in [
        ("input[name='name']",  profile.get("full_name",""), "Full name"),
        ("input[name='email']", profile.get("email",""),     "Email"),
        ("input[name='phone']", profile.get("phone",""),     "Phone"),
    ]:
        if val and await safe_fill(page, sel, val, label):
            result.fields_filled.append(label)
    await safe_upload(page, resume_path, result)


# ── AI-driven field filling (Layer 2) ─────────────────────────────────────────

async def fill_fields_with_ai(
    page, profile: dict, resume_path: str,
    result: ApplyResult, user_id: int,
    gemini_client, model: str,
    on_stuck: Callable = None,
):
    """
    Get page HTML → Gemini detects all fields → fill each one.
    Unknown fields → ask user → save to profile (self-learning).
    """
    print("  [AI] Extracting page HTML for field detection...")
    try:
        html = await page.content()
    except Exception as e:
        print(f"  ⚠ Could not get page HTML: {e}")
        return

    fields = await gemini_detect_fields(html, gemini_client, model)
    print(f"  [AI] Gemini detected {len(fields)} fields")

    for field in fields:
        label = field.get("label", "").strip()
        ftype = field.get("type", "text")
        fname = field.get("name", "")

        if not label:
            continue

        # File upload
        if ftype == "file":
            await safe_upload(page, resume_path, result)
            continue

        # Skip already handled
        if any(s in label.lower() for s in SKIP_LABELS):
            continue

        # ── Get value: profile → Gemini → ask user ────────────────────────
        value = get_field_value(label, profile)

        if not value:
            value = await gemini_answer_field(label, profile, gemini_client, model)

        if not value and on_stuck:
            print(f"    ❓ Unknown: '{label}' — asking user via Telegram")
            value = await on_stuck(label)
            if value:
                # Self-learning: save answer to profile immediately
                updated = learn_answer(user_id, label, value)
                profile.update(updated)  # update in-memory too
                result.fields_learned.append(f"{label}: {value}")
                print(f"    💾 Learned: '{label}' = {value[:40]}")

        if not value:
            result.fields_skipped.append(label)
            continue

        # ── Try to find and fill the element ─────────────────────────────
        filled = False

        # Try by name attribute first (most reliable)
        if fname:
            for sel in [f"[name='{fname}']", f"#{fname}", f"[id='{fname}']"]:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        if ftype == "select":
                            filled = await safe_select(page, sel, value, label)
                        elif ftype in ("text", "email", "phone", "textarea"):
                            await el.fill(value)
                            filled = True
                            print(f"    ✅ '{label}' = {value[:50]}")
                        if filled: break
                except Exception:
                    continue

        # Try by label text → for attribute → input id
        if not filled:
            try:
                lbl = page.locator(f"label:has-text('{label}')").first
                if await lbl.count() > 0:
                    for_id = await lbl.get_attribute("for")
                    if for_id:
                        el = page.locator(f"#{for_id}").first
                        if ftype == "select":
                            filled = await safe_select(page, f"#{for_id}", value, label)
                        else:
                            await el.fill(value)
                            filled = True
                            print(f"    ✅ '{label}' = {value[:50]}")
            except Exception:
                pass

        # Try by placeholder/aria-label
        if not filled:
            for sel in [
                f"[aria-label='{label}']",
                f"[placeholder='{label}']",
                f"[placeholder*='{label[:20]}']",
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0 and await el.is_visible():
                        await el.fill(value)
                        filled = True
                        print(f"    ✅ '{label}' = {value[:50]}")
                        break
                except Exception:
                    continue

        if filled:
            result.fields_filled.append(label)
        else:
            result.fields_skipped.append(label)
            print(f"    ⚠ Could not locate element for '{label}'")


# ── Account creation + email verification ─────────────────────────────────────

async def handle_login_or_signup(page, profile, result, on_verify):
    """Detect login/signup wall, register with APPLY_EMAIL/APPLY_PASSWORD."""
    print("  [Account] Checking for login/signup requirement...")

    signup_texts = ["Create account", "Sign up", "Register", "Create an account"]
    for text in signup_texts:
        try:
            el = page.locator(f"text={text}").first
            if await el.count() > 0 and await el.is_visible():
                print(f"  [Account] Found '{text}' — registering...")
                await el.click()
                await page.wait_for_load_state("networkidle", timeout=10000)
                break
        except Exception:
            continue

    reg_fields = [
        ("input[type='email'], input[name='email']",       profile["email"]),
        ("input[type='password'], input[name='password']", profile["password"]),
        ("input[name='firstName'], input[name='first_name']", profile.get("first_name","")),
        ("input[name='lastName'],  input[name='last_name']",  profile.get("last_name","")),
    ]
    for sel, val in reg_fields:
        if val:
            try:
                el = page.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.fill(val)
            except Exception:
                continue

    for sel in ["button[type='submit']", "button:has-text('Continue')", "button:has-text('Sign up')"]:
        try:
            btn = page.locator(sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                await page.wait_for_load_state("networkidle", timeout=10000)
                break
        except Exception:
            continue

    if on_verify:
        result.status = "needs_verification"
        print(f"  [Account] Waiting for email verification...")
        verified = await on_verify(
            f"Please check *{profile['email']}* for a verification email.\n"
            "Click the link, then reply *done* here."
        )
        if verified:
            result.status = "pending"
            result.notes.append("Email verified by user")
            await page.wait_for_load_state("networkidle", timeout=20000)


# ── Submit ────────────────────────────────────────────────────────────────────

async def try_submit(page, result) -> bool:
    selectors = [
        "button[type='submit']",
        "button:has-text('Submit Application')",
        "button:has-text('Submit')",
        "button:has-text('Apply Now')",
        "button:has-text('Apply')",
        "button:has-text('Send Application')",
        "input[type='submit']",
        "[data-qa='btn-submit']",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                await page.wait_for_load_state("networkidle", timeout=20000)
                result.notes.append(f"Submit clicked: {sel}")
                return True
        except Exception:
            continue
    return False


# ── Main entry point ──────────────────────────────────────────────────────────

async def apply_to_job(
    job_url:      str,
    resume_path:  str,
    user_id:      int,
    gemini_client,
    model:        str        = "gemini-2.0-flash",
    on_stuck:     Callable   = None,   # async fn(question: str) -> str
    on_verify:    Callable   = None,   # async fn(message: str) -> bool
    on_screenshot: Callable  = None,   # async fn(path: str) — send screenshot to user
    headless:     bool       = True,
    dry_run:      bool       = True,
) -> ApplyResult:
    """
    Navigate to job URL, fill the application form, ask user when stuck.

    Args:
        job_url       : Direct URL to the application page
        resume_path   : Path to resume PDF
        user_id       : Telegram user ID (for profile load/save)
        gemini_client : Shared Gemini client from bot.py
        model         : Gemini model name
        on_stuck      : Async callback when bot can't answer a field
        on_verify     : Async callback for email verification
        on_screenshot : Async callback to send screenshot to user
        headless      : Run browser invisibly
        dry_run       : Fill but don't submit
    """
    from playwright.async_api import async_playwright

    profile = load_profile(user_id)
    result  = ApplyResult()
    result.portal = detect_portal(job_url)

    print(f"\n{'━'*52}")
    print(f"  Auto Apply")
    print(f"  URL    : {job_url}")
    print(f"  Portal : {result.portal}")
    print(f"  User   : {user_id}")
    print(f"  Mode   : {'DRY RUN' if dry_run else '⚠ LIVE — will submit'}")
    print(f"{'━'*52}\n")

    Path("output").mkdir(exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await ctx.new_page()

        try:
            # ── Step 1: Load page ──────────────────────────────────────────
            print("[1/5] Loading application page...")
            await page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2500)

            # Try to get job title + company from page title
            try:
                title = await page.title()
                result.job_title = title.split("|")[0].split("–")[0].strip()[:80]
                result.company   = title.split("|")[-1].split("–")[-1].strip()[:60] if "|" in title or "–" in title else ""
            except Exception:
                pass

            # ── Step 2: Login/signup check ─────────────────────────────────
            needs_account = await page.locator(
                "input[type='password'], text=Sign in, text=Log in, text=Create account"
            ).count() > 0

            if needs_account:
                print("[2/5] Login/signup required...")
                await handle_login_or_signup(page, profile, result, on_verify)
                await page.wait_for_timeout(2000)
            else:
                print("[2/5] No login required ✅")

            await page.wait_for_load_state("networkidle", timeout=15000)
            await page.wait_for_timeout(1500)

            # ── Step 3: Fill standard fields (known portal template) ───────
            print(f"[3/5] Filling form ({result.portal})...")
            if result.portal == "greenhouse":
                await fill_greenhouse(page, profile, resume_path, result)
            elif result.portal == "lever":
                await fill_lever(page, profile, resume_path, result)
            elif result.portal == "ashby":
                await fill_ashby(page, profile, resume_path, result)

            # ── Step 4: AI fills remaining / unknown fields ────────────────
            print("[4/5] AI scanning for remaining fields...")
            await fill_fields_with_ai(
                page, profile, resume_path, result,
                user_id, gemini_client, model, on_stuck
            )
            await page.wait_for_timeout(1000)

            # ── Step 5: Screenshot ─────────────────────────────────────────
            print("[5/5] Taking screenshot of filled form...")
            ss = f"output/apply_{user_id}_{result.portal}.png"
            await page.screenshot(path=ss, full_page=True)
            result.screenshot_path = ss
            print(f"  Screenshot → {ss}")

            if on_screenshot:
                await on_screenshot(ss)

            # ── Submit ─────────────────────────────────────────────────────
            if dry_run:
                result.status = "dry_run"
                print("\n  ⏸ DRY RUN — filled but not submitted")
            else:
                submitted = await try_submit(page, result)
                if submitted:
                    result.status = "success"
                    await page.wait_for_timeout(3000)
                    ss2 = f"output/apply_{user_id}_{result.portal}_submitted.png"
                    await page.screenshot(path=ss2, full_page=True)
                    if on_screenshot:
                        await on_screenshot(ss2)
                    print("\n  ✅ Application submitted!")
                else:
                    result.status = "failed"
                    result.error  = "Submit button not found"
                    print("\n  ❌ Could not find submit button")

        except Exception as e:
            result.status = "failed"
            result.error  = str(e)
            print(f"\n  ❌ Error: {e}")
            try:
                err_ss = f"output/apply_{user_id}_error.png"
                await page.screenshot(path=err_ss)
                result.screenshot_path = err_ss
                if on_screenshot:
                    await on_screenshot(err_ss)
            except Exception:
                pass
        finally:
            await browser.close()

    # ── Log this application ───────────────────────────────────────────────
    log_application(user_id, {
        "job_url":        job_url,
        "job_title":      result.job_title,
        "company":        result.company,
        "portal":         result.portal,
        "status":         result.status,
        "fields_filled":  result.fields_filled,
        "fields_skipped": result.fields_skipped,
        "fields_learned": result.fields_learned,
        "error":          result.error,
    })

    # ── Summary ────────────────────────────────────────────────────────────
    print(f"\n{'━'*52}")
    print(f"  Result   : {result.status.upper()}")
    print(f"  Filled   : {len(result.fields_filled)} — {result.fields_filled}")
    print(f"  Skipped  : {len(result.fields_skipped)} — {result.fields_skipped}")
    print(f"  Learned  : {len(result.fields_learned)} — {result.fields_learned}")
    print(f"{'━'*52}\n")

    return result
