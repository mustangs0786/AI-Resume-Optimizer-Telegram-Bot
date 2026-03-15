"""
agent.py — Resume Optimization Agent
Compatible with: google-genai (NEW SDK)
Install: uv pip install google-genai python-dotenv selenium webdriver-manager
"""

import os
import json
import time
from google import genai
from google.genai import types
from dotenv import load_dotenv

from scraper import scrape_url_content
from parser import parse_resume_with_gemini
from prompts import (
    build_analysis_prompt,
    build_rewrite_prompt,
    build_low_score_guidance_prompt,
    REWRITE_THRESHOLD,
)

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY not found. Please set it in your .env file.")

client = genai.Client(api_key=api_key)
# Models tried in order — falls back automatically on 503/429
LLM_MODELS = [
    "gemini-flash-lite-latest"
]

def call_llm(prompt: str, retries: int = 3) -> dict:
    last_error = None
    for model in LLM_MODELS:
        for attempt in range(retries):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.3,
                        response_mime_type="application/json",
                    )
                )
                raw = response.text.strip().replace("```json", "").replace("```", "").strip()
                try:
                    return json.loads(raw)
                except json.JSONDecodeError as e:
                    raise ValueError(f"Invalid JSON from LLM: {e}\nRaw:\n{raw[:500]}")

            except Exception as e:
                err_str = str(e)
                if "503" in err_str or "429" in err_str or "overloaded" in err_str.lower():
                    wait = 3 * (attempt + 1)  # 3s, 6s, 9s backoff
                    print(f"  ⚠ {model} overloaded (attempt {attempt+1}), retrying in {wait}s...")
                    last_error = e
                    time.sleep(wait)
                    continue
                else:
                    raise  # non-retriable error

        print(f"  ⚠ {model} exhausted, trying next model...")

    raise RuntimeError(f"All models failed. Last error: {last_error}")


def run_resume_optimization_agent(job_url: str, resume_file_path: str) -> dict:
    print("\n━━━ Resume Optimization Agent ━━━\n")

    print(f"[1/4] Scraping: {job_url}")
    job_description = scrape_url_content(job_url)
    if not job_description:
        return {"status": "error", "error": "Failed to scrape job description."}
    print("      ✅ Scraped.")

    print(f"[2/4] Parsing resume: {resume_file_path}")
    resume_text = parse_resume_with_gemini(resume_file_path)
    if not resume_text or resume_text.lower().startswith("error") or resume_text.lower().startswith("an error"):
        return {"status": "error", "error": f"Failed to parse resume: {resume_text}"}
    print("      ✅ Parsed.")

    print("[3/4] Analyzing match...")
    try:
        analysis = call_llm(build_analysis_prompt(job_description, resume_text))
    except Exception as e:
        return {"status": "error", "error": f"Analysis failed: {e}"}

    score = analysis.get("score", 0)
    match_level = analysis.get("match_level", "Unknown")
    print(f"      ✅ Score: {score}/100 ({match_level})")
    print(f"      📌 {analysis.get('score_rationale', '')}")

    if score < REWRITE_THRESHOLD:
        print(f"\n[4/4] Score {score} < {REWRITE_THRESHOLD} → building roadmap...")
        try:
            guidance = call_llm(build_low_score_guidance_prompt(job_description, resume_text, analysis))
        except Exception as e:
            return {"status": "error", "error": f"Guidance failed: {e}"}
        print("      ✅ Roadmap ready.")
        return {
            "status": "low_match",
            "score": score,
            "match_level": match_level,
            "analysis": analysis,
            "guidance": guidance,
        }

    print(f"\n[4/4] Score {score} >= {REWRITE_THRESHOLD} → tailoring resume...")
    try:
        rewrite = call_llm(build_rewrite_prompt(job_description, resume_text, analysis))
    except Exception as e:
        return {"status": "error", "error": f"Rewrite failed: {e}"}

    print(f"      ✅ Done. Estimated post-optimization score: {rewrite.get('final_score_estimate')}/100")
    return {
        "status": "optimized",
        "score": score,
        "match_level": match_level,
        "analysis": analysis,
        "optimized_resume_text": rewrite.get("optimized_resume_text", ""),
        "changes_made": rewrite.get("changes_made", []),
        "final_score_estimate": rewrite.get("final_score_estimate"),
        "cover_letter_hook": rewrite.get("cover_letter_hook", ""),
    }


if __name__ == "__main__":
    import sys
    test_job_url = "https://www.google.com/about/careers/applications/jobs/results/131545468301902534"
    test_resume_path = "resumes/sample_resume.pdf"
    if not os.path.exists(test_resume_path):
        print(f"❌ Resume not found: {test_resume_path}")
        sys.exit(1)
    result = run_resume_optimization_agent(test_job_url, test_resume_path)
    print(json.dumps(result, indent=2))