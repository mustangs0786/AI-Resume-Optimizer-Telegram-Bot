"""
job_fetcher.py — Fetch latest jobs from LinkedIn guest API
===========================================================
No login needed. Uses LinkedIn's public guest jobs endpoint.
Install: uv pip install requests beautifulsoup4

Location IDs:
  105214831 = Bengaluru, Karnataka, India
  102713980 = India (country-wide)
"""

import re
import time
import requests
from dataclasses import dataclass
from typing import Optional
from bs4 import BeautifulSoup

# ── Location ──────────────────────────────────────────────────────────────────
BENGALURU_ID = "105214831"
INDIA_GEO_ID = "102713980"

# ── Time filter presets ───────────────────────────────────────────────────────
TIME_FILTERS = {
    "1 hour":   "r3600",
    "24 hours": "r86400",
    "7 days":   "r604800",
    "30 days":  "r2592000",
}

# ── FAANG + Big Tech company IDs (LinkedIn internal IDs) ─────────────────────
# Used as f_C parameter in LinkedIn search URL
# Find any company ID: go to linkedin.com/company/<name>, check the URL

BIG_TECH_COMPANIES = {
    # FAANG / MAANG
    "Google":        "1441",
    "Meta":          "10667",
    "Amazon":        "1586",
    "Apple":         "162479",
    "Netflix":       "165158",
    "Microsoft":     "1035",
    # Big Tech
    "Nvidia":        "1560",
    "Salesforce":    "3779",
    "Adobe":         "1709",
    "Intel":         "1053",
    "IBM":           "1009",
    "Oracle":        "1066",
    "SAP":           "1373",
    "Qualcomm":      "3144",
    "Uber":          "19271500",
    "Airbnb":        "391850",
    "Twitter/X":     "162479",
    "LinkedIn":      "1337",
    # India Big Tech
    "Flipkart":      "2748527",
    "Swiggy":        "6905435",
    "Zomato":        "1353539",
    "Paytm":         "3180475",
    "PhonePe":       "15142873",
    "Razorpay":      "11458159",
    "Zepto":         "73267544",
    "CRED":          "18647291",
    # MNCs with big India offices
    "Goldman Sachs": "1067",
    "JPMorgan":      "1068",
    "Morgan Stanley":"7036",
    "Deutsche Bank": "10334",
    "Visa":          "1828",
    "Mastercard":    "2068",
    "Walmart":       "1466",
    "Accenture":     "1033",
    "McKinsey":      "3756",
    "Deloitte":      "1038",
    "Optum":         "357412",
    "UnitedHealth":  "2564",
    "Thoughtworks":  "4836",
    "Atlassian":     "10455",
    "Elastic":       "6689",
    "Databricks":    "3344252",
    "Snowflake":     "3812658",
    "Stripe":        "3651435",
}

# Grouped for bot buttons
COMPANY_GROUPS = {
    "FAANG":        ["Google", "Meta", "Amazon", "Apple", "Netflix", "Microsoft"],
    "Big Tech":     ["Nvidia", "Salesforce", "Adobe", "Uber", "Airbnb", "Databricks", "Snowflake", "Atlassian", "Stripe"],
    "India Unicorns": ["Flipkart", "Swiggy", "Zomato", "PhonePe", "Razorpay", "CRED", "Zepto", "Paytm"],
    "MNC Finance":  ["Goldman Sachs", "JPMorgan", "Morgan Stanley", "Visa", "Mastercard", "Optum"],
    "All Big Tech": list(BIG_TECH_COMPANIES.keys()),  # everything
}

# ── Keyword-based fallback filter (when f_C not used) ────────────────────────
# If LinkedIn ignores f_C (it often does on guest API), we filter post-fetch
# Keep this list BROAD — better to show a few extra than miss real results
BIG_COMPANY_KEYWORDS = [
    # FAANG / MAANG
    "google", "meta", "facebook", "amazon", "aws", "apple", "netflix", "microsoft",
    # Big Tech
    "nvidia", "salesforce", "adobe", "uber", "airbnb", "databricks", "snowflake",
    "stripe", "atlassian", "elastic", "thoughtworks", "qualcomm", "intel",
    "servicenow", "workday", "twilio", "datadog", "confluent", "hashicorp",
    "cloudera", "splunk", "palo alto", "crowdstrike", "okta", "zendesk",
    # India Big Tech / Unicorns
    "flipkart", "swiggy", "zomato", "paytm", "phonepe", "razorpay", "cred",
    "zepto", "ola", "oyo", "meesho", "groww", "upstox", "navi", "slice",
    "byjus", "unacademy", "freshworks", "zoho", "chargebee", "postman",
    "browserstack", "hasura", "setu", "sarvam", "krutrim",
    # MNCs with large India engineering offices
    "goldman sachs", "jpmorgan", "morgan stanley", "deutsche bank", "visa",
    "mastercard", "walmart", "accenture", "mckinsey", "deloitte", "optum",
    "unitedhealth", "ibm", "oracle", "sap", "cisco", "vmware", "broadcom",
    "texas instruments", "amd", "arm", "siemens", "bosch", "continental",
    "mercedes", "toyota", "boeing", "ge", "honeywell", "3m",
    "barclays", "hsbc", "standard chartered", "ubs", "credit suisse",
    "blackrock", "fidelity", "jpmc", "chase",
    # Consulting / Services (large)
    "bcg", "bain", "pwc", "kpmg", "ey", "ernst", "tcs", "infosys", "wipro",
    "hcl", "tech mahindra", "cognizant", "capgemini", "mphasis",
]

# ── Search configs — Bengaluru only ───────────────────────────────────────────
DEFAULT_SEARCHES = [
    {"label": "Data Scientist",  "keywords": "data scientist"},
    {"label": "ML Engineer",     "keywords": "machine learning engineer"},
    {"label": "AI Engineer",     "keywords": "AI engineer GenAI"},
    {"label": "Data Analyst",    "keywords": "data analyst"},
]

# ── Job data model ────────────────────────────────────────────────────────────
@dataclass
class Job:
    title:    str
    company:  str
    location: str
    url:      str
    job_id:   str = ""
    posted:   str = ""

    def apply_url(self) -> str:
        """Clean apply URL — strip tracking params."""
        return self.url.split("?")[0] if self.url else ""

# ── LinkedIn guest API ────────────────────────────────────────────────────────
# This is the same endpoint LinkedIn's own job listing pages use.
# Works without login. Returns HTML job cards we can parse with BeautifulSoup.

GUEST_API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.linkedin.com/jobs/search/",
}


def build_url(
    keywords: str,
    time_filter: str = "r86400",
    start: int = 0,
    company_ids: list[str] = None,
) -> str:
    """
    Build LinkedIn guest API URL for Bengaluru jobs.
    f_PP=105214831  → Bengaluru only
    f_TPR=r86400    → last 24h (use r3600 for 1h)
    f_C=1441,10667  → company filter (optional)
    sortBy=DD       → newest first
    """
    kw = keywords.strip().replace(" ", "%20")
    url = (
        f"{GUEST_API}"
        f"?keywords={kw}"
        f"&geoId={INDIA_GEO_ID}"
        f"&f_PP={BENGALURU_ID}"
        f"&f_TPR={time_filter}"
        f"&sortBy=DD"
        f"&start={start}"
        f"&count=25"
    )
    if company_ids:
        url += f"&f_C={','.join(company_ids)}"
    return url


def is_big_company(company_name: str) -> bool:
    """Check if a company name matches our big tech list (keyword fallback)."""
    name_lower = company_name.lower()
    return any(kw in name_lower for kw in BIG_COMPANY_KEYWORDS)


def parse_job_cards(html: str) -> list[Job]:
    """Parse LinkedIn job cards HTML into Job objects."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.find_all("li")
    jobs = []

    for card in cards:
        try:
            # Title
            title_el = card.find("h3", class_=re.compile("base-search-card__title|job-search-card__title"))
            if not title_el:
                title_el = card.find("span", class_=re.compile("title|job-title"))
            title = title_el.get_text(strip=True) if title_el else ""

            # Company
            company_el = card.find("h4", class_=re.compile("base-search-card__subtitle|company"))
            if not company_el:
                company_el = card.find("a", class_=re.compile("company"))
            company = company_el.get_text(strip=True) if company_el else ""

            # Location
            location_el = card.find("span", class_=re.compile("location|job-search-card__location"))
            location = location_el.get_text(strip=True) if location_el else "Bengaluru"

            # URL + job ID
            link_el = card.find("a", class_=re.compile("base-card__full-link|job-card"))
            if not link_el:
                link_el = card.find("a", href=re.compile("/jobs/view/"))
            url = link_el["href"].split("?")[0] if link_el and link_el.get("href") else ""
            job_id = url.split("-")[-1] if url else ""

            # Posted time
            time_el = card.find("time")
            posted = time_el.get("datetime", "") if time_el else ""

            if title and (company or url):
                jobs.append(Job(
                    title=title,
                    company=company,
                    location=location,
                    url=url,
                    job_id=job_id,
                    posted=posted,
                ))
        except Exception:
            continue

    return jobs


def fetch_jobs(
    keywords: str,
    time_filter: str = "r86400",
    max_jobs: int = 15,
    company_group: str = None,   # key from COMPANY_GROUPS e.g. "FAANG", "All Big Tech"
    big_only: bool = True,       # post-fetch keyword filter as fallback
) -> list[Job]:
    """
    Fetch jobs for a keyword in Bengaluru.
    time_filter  : "r3600"=1h, "r86400"=24h, "r604800"=7d
    company_group: filter to a group from COMPANY_GROUPS
    big_only     : also apply keyword-based company filter as safety net
    """
    # Build LinkedIn company ID filter
    company_ids = None
    if company_group and company_group in COMPANY_GROUPS:
        names = COMPANY_GROUPS[company_group]
        company_ids = [
            BIG_TECH_COMPANIES[n]
            for n in names
            if n in BIG_TECH_COMPANIES
        ]
        print(f"  Filtering to {len(company_ids)} companies in group '{company_group}'")

    all_jobs = []
    seen_ids = set()

    for start in range(0, max(max_jobs, 25), 25):
        url = build_url(keywords, time_filter, start, company_ids)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)

            if resp.status_code == 429:
                print("  Rate limited — waiting 30s...")
                time.sleep(30)
                resp = requests.get(url, headers=HEADERS, timeout=15)

            if resp.status_code != 200:
                print(f"  HTTP {resp.status_code} for '{keywords}'")
                break

            jobs = parse_job_cards(resp.text)
            if not jobs:
                break

            for job in jobs:
                # Post-fetch filter logic:
                # If f_C company IDs were sent → trust LinkedIn filtered it, show all
                # If no company filter → apply keyword filter as fallback
                # If big_only=False → show everything
                if not company_ids and big_only and not is_big_company(job.company):
                    continue

                key = job.job_id or job.url
                if key and key not in seen_ids:
                    seen_ids.add(key)
                    all_jobs.append(job)

                if len(all_jobs) >= max_jobs:
                    break

        except Exception as e:
            print(f"  Error: {e}")
            break

        if len(all_jobs) >= max_jobs:
            break
        time.sleep(1)

    return all_jobs


def fetch_latest_jobs(
    searches: list[dict] = None,
    time_filter: str = "r86400",
    max_per_search: int = 10,
) -> list[Job]:
    """
    Fetch latest jobs across all configured searches.
    Deduplicates across searches.
    """
    if searches is None:
        searches = DEFAULT_SEARCHES

    all_jobs = []
    seen_ids = set()

    for s in searches:
        label = s["label"]
        keywords = s["keywords"]
        print(f"  Fetching: {label} in Bengaluru (last 24h)...")

        jobs = fetch_jobs(keywords, time_filter=time_filter, max_jobs=max_per_search)
        for job in jobs:
            key = job.job_id or job.url
            if key not in seen_ids:
                seen_ids.add(key)
                all_jobs.append(job)

        time.sleep(2)  # polite delay between searches

    print(f"  Total unique jobs: {len(all_jobs)}")
    return all_jobs


# ── Telegram formatter ────────────────────────────────────────────────────────

def format_jobs_message(jobs: list[Job], title: str = "Latest Jobs") -> str:
    """Format job list as Telegram-ready markdown message."""
    if not jobs:
        return (
            "No jobs found in Bengaluru for this search.\n\n"
            "Try a wider time range or different role."
        )

    lines = [f"*{title}*\n_{len(jobs)} jobs in Bengaluru_\n"]
    for i, job in enumerate(jobs[:12], 1):
        clean_url = job.url.split("?")[0] if job.url else ""
        posted = f" • {job.posted}" if job.posted else ""
        lines.append(
            f"{i}\\. *{job.title}*\n"
            f"   {job.company}{posted}\n"
            f"   [{clean_url}]({clean_url})\n"
        )

    return "\n".join(lines)


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    role    = sys.argv[1] if len(sys.argv) > 1 else "data scientist"
    period  = sys.argv[2] if len(sys.argv) > 2 else "r86400"

    print(f"\nFetching '{role}' jobs in Bengaluru (filter: {period})...\n")
    jobs = fetch_jobs(role, time_filter=period, max_jobs=10)

    if not jobs:
        print("No jobs found.")
    else:
        for job in jobs:
            print(f"  {job.title}")
            print(f"  {job.company} | {job.location}")
            print(f"  {job.url}\n")

    print(f"Total: {len(jobs)}")