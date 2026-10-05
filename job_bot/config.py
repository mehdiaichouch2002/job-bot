import os
from dotenv import load_dotenv

load_dotenv()

# --- Email ---
# Use `or default` (not getenv's default arg) so an EMPTY env var — e.g. an
# unset GitHub Actions secret passed as "" — still falls back to the default.
SMTP_SERVER = os.getenv("SMTP_SERVER") or "smtp.gmail.com"
SMTP_PORT = int(os.getenv("SMTP_PORT") or "587")
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS") or "mehdi2002aichouch@gmail.com"
# Display name in the From header: a bare address reads as bulk mail to spam filters.
SENDER_NAME = os.getenv("SENDER_NAME") or "Mehdi Aichouch"
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
# The .env.example placeholder is not a password: treat it as unset so the bot
# says so plainly instead of failing Gmail login on every run.
if EMAIL_PASSWORD.strip() in ("", "your_16_char_gmail_app_password"):
    EMAIL_PASSWORD = ""

# --- Groq API (free LLM for email generation) ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
# Messages use the large model; criteria extraction uses the small one, which has
# a much larger free daily token allowance and is checked against the ad anyway.
GROQ_MODEL = os.getenv("GROQ_MODEL") or "llama-3.3-70b-versatile"
GROQ_EXTRACT_MODEL = os.getenv("GROQ_EXTRACT_MODEL") or "llama-3.1-8b-instant"

# --- Hunter.io API (OFF by default) ---
# Hunter's free tier is capped at 25 lookups/month. To keep the bot on purely
# free + unlimited methods, it is disabled unless you explicitly opt in with
# USE_HUNTER=true. The bot works fully without it (site scraping + Clearbit).
HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "")
USE_HUNTER = os.getenv("USE_HUNTER", "false").lower() == "true"

# --- Adzuna API (optional) ---
ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID", "")
ADZUNA_API_KEY = os.getenv("ADZUNA_API_KEY", "")

# --- Bot settings ---
MAX_JOBS_PER_RUN = int(os.getenv("MAX_JOBS_PER_RUN", "10"))
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# ── AUTO-EMAIL: OFF (2026-07-28) ─────────────────────────────────
# 4-month result of the auto-email channel: 1,775 sent → 14 responses
# (12 auto-replies, 2 rejections) → 0 interviews. 93% of sends went to
# `careers@`/`recrutement@` mailboxes the bot INVENTED — the June fix only
# verified that the *domain* accepted mail, never that the *mailbox* existed,
# so the sends stopped bouncing and started silently disappearing instead.
#
# The bot is now a FINDER, not a sender: it scores real jobs, builds a
# ready-to-submit packet (tailored CV + cover letter + direct apply link) and
# emails Mehdi a ranked shortlist he submits himself through the official ATS.
#
# ── RE-ENABLED (2026-09-23) at Mehdi's request, with the July guards kept:
# no invented addresses (ALLOW_ROLE_ADDRESS_GUESS off), named contact only,
# hireable-from-Morocco only, BON_MATCH verdict only (matcher.py). Expect a
# handful of sends a week, not hundreds — the packet lane + digest still carry
# everything that has no real contact.
AUTO_EMAIL = os.getenv("AUTO_EMAIL", "true").lower() == "true"

# Time budgets so a GitHub Actions run (12-min hard cap) always reaches the
# packet + digest steps. The site crawl costs ~1–3 min per company.
EMAIL_FINDER_BUDGET_SEC = int(os.getenv("EMAIL_FINDER_BUDGET_SEC", "180"))
SEND_DEADLINE_SEC = int(os.getenv("SEND_DEADLINE_SEC", "420"))   # stop sending after 7 min of run time

# Even when AUTO_EMAIL is on, never mail an invented role address (careers@,
# recrutement@, hr@…) that was constructed rather than found. Guessed mailboxes
# are what produced 4 months of silence.
ALLOW_ROLE_ADDRESS_GUESS = os.getenv("ALLOW_ROLE_ADDRESS_GUESS", "false").lower() == "true"
# When auto-email is on, only contact a NAMED human (person address scraped from
# the company's own site). Generic inboxes never converted.
REQUIRE_NAMED_CONTACT = os.getenv("REQUIRE_NAMED_CONTACT", "true").lower() == "true"

# Matching (2026-09-23): the old 0–100 keyword score and its MIN_FIT thresholds
# are gone. matcher.py sorts each job into BON_MATCH / CONTACT_RESEAU / A_EVITER
# by explicit rules; only BON_MATCH jobs can be auto-emailed.
# LLM criteria extraction runs on at most this many jobs per run, and stops
# after EXTRACT_BUDGET_SEC; the rest use the regex extractor (free, instant).
LLM_EXTRACT_MAX = int(os.getenv("LLM_EXTRACT_MAX", "12"))
EXTRACT_BUDGET_SEC = int(os.getenv("EXTRACT_BUDGET_SEC", "90"))
# Networking outreach (outreach.py): short emails to named people from
# data/outreach_targets.csv. Capped hard — the same Gmail sends applications,
# and spam reports would put that account at risk.
OUTREACH_ENABLED = os.getenv("OUTREACH_ENABLED", "true").lower() == "true"
OUTREACH_DAILY_MAX = int(os.getenv("OUTREACH_DAILY_MAX", "5"))       # first messages + follow-ups
OUTREACH_PER_RUN = int(os.getenv("OUTREACH_PER_RUN", "1"))           # spreads sends across the day
OUTREACH_FOLLOWUP_DAYS = int(os.getenv("OUTREACH_FOLLOWUP_DAYS", "7"))
OUTREACH_HOURS = os.getenv("OUTREACH_HOURS") or "9-18"               # local time, Mon–Fri
# Where to send the daily shortlist digest (defaults to the user's own inbox).
DIGEST_TO = os.getenv("DIGEST_TO", EMAIL_ADDRESS)
SEND_DIGEST = os.getenv("SEND_DIGEST", "true").lower() == "true"

# --- Paths ---
_BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DB_PATH = os.path.join(_BASE_DIR, "data", "jobs.db")
LOG_PATH = os.path.join(_BASE_DIR, "logs", "bot.log")
APPLICATIONS_DIR = os.path.join(_BASE_DIR, "applications")  # ready-to-submit packets
OUTREACH_TARGETS_CSV = os.path.join(_BASE_DIR, "data", "outreach_targets.csv")  # git-ignored
# Role-tailored CVs (2026-09-23). A Magento-focused résumé and a Full-Stack
# (PHP/Laravel/Symfony + React) résumé, each in EN + FR. The packet builder picks
# the variant that matches the posting — a Magento role gets the Magento CV,
# anything else gets the Full-Stack CV. Canadian jobs use the same role CVs.
#
# Files are named "<DDMMYYYY>-Mehdi_Aichouch_<role>_<lang>.pdf". We match on the
# suffix and take the newest date prefix, so dropping in a re-dated CV just works
# without editing this file.
def _latest_cv(suffix: str) -> str:
    matches = [f for f in os.listdir(_BASE_DIR) if f.endswith(suffix)]

    def _date_key(name: str):
        d = name.split("-", 1)[0]
        return (d[4:8], d[2:4], d[0:2]) if len(d) == 8 and d.isdigit() else ("", "", "")

    matches.sort(key=_date_key)
    return os.path.join(_BASE_DIR, matches[-1] if matches else suffix)


CV_PDF_MAGENTO_EN = _latest_cv("Mehdi_Aichouch_Magento2_Developer_EN.pdf")
CV_PDF_MAGENTO_FR = _latest_cv("Mehdi_Aichouch_Developpeur_Magento2_FR.pdf")
CV_PDF_FULLSTACK_EN = _latest_cv("Mehdi_Aichouch_FullStack_Developer_EN.pdf")
CV_PDF_FULLSTACK_FR = _latest_cv("Mehdi_Aichouch_Developpeur_FullStack_FR.pdf")

# Default CV = the Magento variant (Mehdi's primary specialism).
CV_PDF_EN = CV_PDF_MAGENTO_EN
CV_PDF_FR = CV_PDF_MAGENTO_FR

# --- Canada relocation track ---
# When True, the bot also targets Canadian listings and writes recruiter emails that pitch the work-authorization angle so a
# Morocco-based applicant isn't auto-rejected.
CANADA_TRACK = os.getenv("CANADA_TRACK", "true").lower() == "true"

# The relocation pitch injected into Canada-targeted emails/cover letters.
#
# HONESTY NOTE (2026-07-28): this previously asserted that Mehdi "already meets
# the language requirement" for Francophone Mobility (C16). CV_DATA lists his
# French as B1/Intermediate; C16 is assessed on a certified NCLC score, which he
# has not yet sat. Overclaiming a visa qualification in writing is the kind of
# thing that surfaces later at the immigration stage and kills the offer — so
# the pitch now states only verifiable facts (French-medium engineering degree,
# working French, willingness to certify) and does not assert eligibility.
CANADA_PITCH = (
    "I'm based in Morocco and actively relocating to Canada. My engineering education was "
    "delivered entirely in French and I work in English daily with European teams. I'm "
    "pursuing the LMIA-exempt Francophone Mobility (C16) route, which is employer-light — a "
    "compliance fee and an offer in the portal, with no LMIA, advertising or recruitment test — "
    "and I'm booking the TEF Canada exam to certify my French for it. I'm ready to relocate at "
    "my own expense and can start remotely in the meantime."
)
# Quebec uses its own immigration system (C16 is outside-Quebec only), so the
# pitch for Quebec roles must NOT mention C16.
CANADA_PITCH_QUEBEC = (
    "I'm a French-educated developer based in Morocco, actively relocating to Quebec. My "
    "engineering degree was taught in French and I work in English daily with European teams, "
    "which fits Quebec's French-speaking worker pathways. Ready to relocate at my own expense, "
    "and available to start remotely in the meantime."
)

# --- Job search keywords ---
# Used as search queries for API-based scrapers (Remotive, Adzuna)
# and as substring matches for tag/content scrapers (RemoteOK, WWR)
# Ordered by priority: Magento / PHP core first, JS/React/Laravel secondary.
# matcher.py then judges each ad against profile.py — keywords here only decide
# what gets fetched, not what counts as a match.
SEARCH_KEYWORDS = [
    # ── Primary: Magento + PHP ──
    "magento", "magento 2", "magento developer", "adobe commerce", "hyva",
    "php", "php developer", "symfony", "laravel",
    # ── Secondary: full-stack / e-commerce / frontend ──
    "ecommerce developer", "backend developer",
    "full stack", "full-stack",
    "react", "reactjs",
]

# --- CV data (used in email generation) ---
# Identity + CV content. Every figure and skill level here must agree with
# profile.py (the source of truth for matching and messages) and with the
# 23092026 CV PDFs — all three say 3 years.
CV_DATA = {
    "name": "Mehdi Aichouch",
    "email": "mehdi2002aichouch@gmail.com",
    # from the environment, not the code, since the code is published (public repo, 2026-10-05)
    "phone": os.getenv("CONTACT_PHONE") or "",
    "location": "Fès, Morocco",
    "linkedin": "linkedin.com/in/aichouch-mehdi",
    "github": "github.com/mehdiaichouch2002",
    "portfolio": "mehdi-aichouch.vercel.app",
    "title": "Magento 2 / Adobe Commerce Developer",
    "summary": (
        "Magento 2 / Adobe Commerce developer with 3 years of experience on international B2B and "
        "B2C storefronts for Carhartt WIP, Anita and Edwin Europe, serving 50,000+ monthly users "
        "across Europe. Custom modules on Service Contracts, REST integrations and Hyvä storefronts, "
        "with a focus on performance: product listing pages went from 3.2s to 0.8s. Preparing the "
        "Adobe Commerce Developer Professional certification (AD0-E724)."
    ),
    "experience": [
        {
            "title": "Magento 2 Developer",
            "company": "Cartware / Morocommerce",
            "period": "January 2024 – July 2026",
            "achievements": [
                "Built the REST API layer for the Carhartt WIP DAM integration (repositories, service contracts, webapi.xml, Amplience CDN)",
                "Integrated Microsoft Entra ID single sign-on (JWKS-based JWT validation) and Apple Pay domain verification on Adyen for Edwin Europe",
                "Cut product listing load time from 3.2s to 0.8s (Elasticsearch mapping, MySQL queries and indexes, Redis, Varnish FPC)",
                "Delivered Hyvä storefronts (Alpine.js, Tailwind CSS): 40% faster page loads, Google PageSpeed above 95",
                "Built B2B workflows: tiered and customer-group pricing, catalogue permissions, inventory sync over REST",
                "Replaced third-party extensions with in-house plugins and observers, cutting extension costs by 30%",
                "Refactored 50,000+ lines of legacy code and stabilised Docker and CI/CD pipelines, cutting production bugs by 45%",
            ],
        },
        {
            "title": "Laravel Developer (Internship)",
            "company": "Cartware / Morocommerce",
            "period": "August – December 2023",
            "achievements": [
                "Internal management platform on Laravel 10 and Tailwind CSS, used daily by 25+ employees",
                "15+ REST endpoints with proper HTTP semantics, request validation and Eloquent relationships",
                "Role-based access control with optimised queries, halving response times",
            ],
        },
        {
            "title": "Web Developer (Internship)",
            "company": "Sidi Mohamed Ben Abdellah University",
            "period": "March – April 2023",
            "achievements": [
                "PHP / MySQL HR app automating leave requests and payroll tracking, removing 70% of manual HR work",
            ],
        },
    ],
    "skills": {
        "ecommerce": ["Magento 2.4.x", "Hyvä Theme", "B2B Commerce", "Custom Modules", "Plugins & Observers", "Service Contracts", "REST APIs"],
        "performance": ["Elasticsearch / OpenSearch", "Redis", "Varnish FPC", "MySQL tuning", "Nginx"],
        "backend": ["PHP 8", "Laravel", "MySQL", "Composer"],
        "frontend": ["Hyvä Theme", "Alpine.js", "Tailwind CSS", "React", "Knockout.js", "JavaScript ES6+"],
        "tools": ["Git/GitHub", "Docker", "Linux", "Xdebug", "CI/CD (existing pipelines)"],
        # Known from studies / side use, not daily production — never claimed as mastery.
        "also_known": ["Java / Spring Boot", "Java EE", "C# / .NET", "Python"],
    },
    "languages": {
        "Arabic": "Native",
        "English": "Professional Working Proficiency (B2)",
        "French": "Intermediate (B1)",
    },
    "education": [
        {
            # A D.U. is a university diploma, not the national Licence; diplomas carry no dates
            "degree": "University Diploma (D.U., Bac+3) in Web Development Frameworks & Java EE",
            "school": "ENSA Fès, Sidi Mohamed Ben Abdellah University",
            "honours": "Highest honours (mention Très Bien)",
            "focus": "Java EE, Spring Boot, C#/.NET, software architecture",
        },
        {
            "degree": "Specialized Technician Diploma in Digital Development",
            "school": "OFPPT Fès",
            "focus": "Full-stack web development: PHP, MySQL, JavaScript, OOP",
        },
    ],
    "availability": (
        "Based in Fès, Morocco (GMT+1, full overlap with European hours). Has worked remotely "
        "with European teams (Carhartt WIP, Anita, Edwin Europe). Available immediately for "
        "remote full-time, contract or freelance roles."
    ),
}

# Canada detection — country code or any of these place names in the location.
# Francophone-outside-Quebec hubs are listed first (best fit for C16).
CANADA_LOCATION_KEYWORDS = {
    "canada", "ontario", "quebec", "québec", "ottawa", "toronto", "montreal", "montréal",
    "moncton", "new brunswick", "nouveau-brunswick", "sudbury", "winnipeg", "manitoba",
    "edmonton", "calgary", "alberta", "vancouver", "british columbia", "halifax",
    "nova scotia", "saskatchewan", "regina", "saskatoon", "gatineau", "mississauga",
}
# Provinces/cities that are NOT Quebec (Francophone Mobility C16 requires outside Quebec).
QUEBEC_KEYWORDS = {"quebec", "québec", "montreal", "montréal", "gatineau", "laval", "quebec city"}

# Countries where French email should be used (ISO 3166-1 alpha-2)
FRENCH_COUNTRIES = {
    "FR", "BE", "CH", "LU", "MC", "CA", "MA", "TN", "DZ", "SN",
    "CI", "CM", "MG", "ML", "BF", "NE", "BJ", "TG", "GA", "CG",
    "CD", "RW", "BI", "DJ", "KM", "MU", "SC", "GN", "GW", "CF",
}

# ── Geographic exclusion ─────────────────────────────────────────
# Drop listings tied to far-timezone / local-hire markets that won't fit a
# Morocco-based developer targeting EU & remote roles. A job is only dropped
# when one of these is named in its location/country — generic "Remote",
# "Worldwide", "Anywhere", or EU/US listings are kept.
EXCLUDED_COUNTRIES = {
    "IN",  # India
    "PK", "BD", "LK", "NP",        # Pakistan, Bangladesh, Sri Lanka, Nepal
    "PH", "ID", "VN", "MY", "TH",  # Philippines, Indonesia, Vietnam, Malaysia, Thailand
    "CN", "JP", "KR", "AU", "NZ",  # China, Japan, Korea, Australia, New Zealand (far timezones)
}
EXCLUDED_LOCATION_KEYWORDS = {
    "india", "bengaluru", "bangalore", "mumbai", "delhi", "new delhi", "hyderabad",
    "chennai", "pune", "noida", "gurgaon", "gurugram", "kolkata", "ahmedabad", "jaipur",
    "pakistan", "karachi", "lahore", "islamabad",
    "bangladesh", "dhaka", "sri lanka", "colombo", "nepal", "kathmandu",
    "philippines", "manila", "cebu", "indonesia", "jakarta",
    "vietnam", "hanoi", "ho chi minh", "malaysia", "kuala lumpur", "thailand", "bangkok",
}
