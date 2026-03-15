"""
main.py — Entry point for the Resume Optimization Pipeline
===========================================================
Usage:
  python main.py --job "https://careers.expediagroup.com/job/machine-learning-engineer-iii/bangalore-/R-99283/" --resume "D:\Projects\Resume_Builder\deepak_resume.pdf"
  python main.py --job "https://..." --resume "resumes/my_resume.pdf" --output "output/tailored.pdf"
  python main.py --job "https://..." --resume "resumes/my_resume.pdf" --threshold 60
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from agent import run_resume_optimization_agent
from resume_pdf import generate_resume_pdf


# ── Helpers ───────────────────────────────────────────────────────────────────

def print_divider(char="─", width=60):
    print(char * width)

def print_section(title: str):
    print(f"\n{'━' * 60}")
    print(f"  {title}")
    print(f"{'━' * 60}")

def save_json_debug(data: dict, output_dir: Path):
    """Save raw agent output as JSON for debugging."""
    path = output_dir / "debug_output.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  📁 Debug JSON saved → {path}")

def save_resume_text(text: str, output_dir: Path) -> Path:
    """Save the raw resume markdown text."""
    path = output_dir / "optimized_resume.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"  📝 Resume text saved → {path}")
    return path


# ── Display Functions ─────────────────────────────────────────────────────────

def display_analysis(analysis: dict):
    print_section("MATCH ANALYSIS")
    score       = analysis.get("score", 0)
    match_level = analysis.get("match_level", "Unknown")
    rationale   = analysis.get("score_rationale", "")

    # Score bar
    filled = int(score / 5)
    bar = "█" * filled + "░" * (20 - filled)
    print(f"\n  Score: {score}/100  [{bar}]  {match_level}")
    print(f"\n  {rationale}")

    matched = analysis.get("matched_skills", [])
    if matched:
        print(f"\n  ✅ Matched Skills ({len(matched)}):")
        for s in matched:
            print(f"     • {s}")

    missing_crit = analysis.get("missing_critical", [])
    if missing_crit:
        print(f"\n  ❌ Critical Gaps ({len(missing_crit)}):")
        for s in missing_crit:
            print(f"     • {s}")

    missing_pref = analysis.get("missing_preferred", [])
    if missing_pref:
        print(f"\n  ⚠  Preferred Gaps ({len(missing_pref)}):")
        for s in missing_pref:
            print(f"     • {s}")

    ats = analysis.get("ats_keywords_to_add", [])
    if ats:
        print(f"\n  🎯 ATS Keywords to Add:")
        print(f"     {', '.join(ats)}")


def display_low_match(result: dict):
    guidance = result.get("guidance", {})

    print_section("LOW MATCH — IMPROVEMENT ROADMAP")
    print(f"\n  {guidance.get('honest_assessment', '')}")
    print(f"\n  ⏱  Time to become competitive: {guidance.get('estimated_time_to_ready', 'N/A')}")

    roadmap = guidance.get("skill_gap_roadmap", [])
    if roadmap:
        print(f"\n  📚 Skill Gap Plan:")
        for item in roadmap:
            print(f"\n     [{item.get('timeframe', '?')}] {item.get('skill', '')}")
            print(f"     Why: {item.get('why_important', '')}")
            print(f"     How: {item.get('how_to_learn', '')}")

    quick_wins = guidance.get("quick_wins", [])
    if quick_wins:
        print(f"\n  ⚡ Quick Wins (do today):")
        for w in quick_wins:
            print(f"     • {w}")

    alternatives = guidance.get("alternative_roles", [])
    if alternatives:
        print(f"\n  🔀 Better-fit roles right now:")
        for r in alternatives:
            print(f"     • {r}")

    encouragement = guidance.get("encouragement", "")
    if encouragement:
        print(f"\n  💪 {encouragement}")


def display_optimized(result: dict):
    print_section("OPTIMIZATION COMPLETE")

    before = result.get("score", 0)
    after  = result.get("final_score_estimate", 0)
    print(f"\n  Score: {before}/100 → {after}/100 (estimated after tailoring)")

    changes = result.get("changes_made", [])
    if changes:
        print(f"\n  ✍  Changes made:")
        for c in changes:
            print(f"     • {c}")

    hook = result.get("cover_letter_hook", "")
    if hook:
        print(f"\n  ✉  Cover Letter Hook:")
        print_divider()
        print(f"  {hook}")
        print_divider()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Resume Optimization Agent — tailors your resume to a job posting"
    )
    parser.add_argument(
        "--job", "-j",
        required=True,
        help="URL of the job posting"
    )
    parser.add_argument(
        "--resume", "-r",
        required=True,
        help="Path to your resume file (PDF or DOCX)"
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output PDF path (default: output/<timestamp>_resume.pdf)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Save raw JSON output for debugging"
    )
    parser.add_argument(
        "--no-pdf",
        action="store_true",
        help="Skip PDF generation (output text only)"
    )

    args = parser.parse_args()

    # ── Validate inputs ───────────────────────────────────────────────────────
    if not os.path.exists(args.resume):
        print(f"❌ Resume file not found: {args.resume}")
        sys.exit(1)

    # ── Setup output directory ────────────────────────────────────────────────
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_path  = args.output or str(output_dir / f"{timestamp}_resume.pdf")

    # ── Run agent ─────────────────────────────────────────────────────────────
    print(f"\n🚀 Resume Optimization Agent")
    print(f"   Job URL : {args.job}")
    print(f"   Resume  : {args.resume}")
    print(f"   Output  : {pdf_path}\n")

    result = run_resume_optimization_agent(
        job_url=args.job,
        resume_file_path=args.resume,
    )

    # ── Handle error ──────────────────────────────────────────────────────────
    if result.get("status") == "error":
        print(f"\n❌ Pipeline failed: {result.get('error')}")
        sys.exit(1)

    # ── Display analysis (always shown) ───────────────────────────────────────
    display_analysis(result.get("analysis", {}))

    # ── Save debug JSON ───────────────────────────────────────────────────────
    if args.debug:
        save_json_debug(result, output_dir)

    # ── Low match: show roadmap, no PDF ───────────────────────────────────────
    if result["status"] == "low_match":
        display_low_match(result)
        print(f"\n⚠  Resume not rewritten — score too low ({result['score']}/100).")
        print("   Work on the gaps above, then try again.\n")
        sys.exit(0)

    # ── Optimized: show results + generate PDF ────────────────────────────────
    display_optimized(result)

    resume_text = result.get("optimized_resume_text", "")

    if not resume_text:
        print("\n❌ No resume text returned from agent.")
        sys.exit(1)

    # Always save the markdown text
    save_resume_text(resume_text, output_dir)

    # Generate PDF unless --no-pdf
    if not args.no_pdf:
        print(f"\n  📄 Generating PDF...")
        try:
            generate_resume_pdf(resume_text, pdf_path)
            print(f"  ✅ PDF ready → {pdf_path}")
        except FileNotFoundError as e:
            print(f"\n  ⚠  PDF generation skipped: {e}")
            print("  Resume text saved as .md — convert manually.")
        except Exception as e:
            print(f"\n  ⚠  PDF generation failed: {e}")
            print("  Resume text saved as .md — convert manually.")

    print(f"\n{'━' * 60}")
    print(f"  Done! Check the output/ folder.")
    print(f"{'━' * 60}\n")


if __name__ == "__main__":
    main()