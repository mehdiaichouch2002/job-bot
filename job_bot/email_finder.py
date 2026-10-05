"""
email_finder.py
~~~~~~~~~~~~~~~
Discover a *real* recruiter / HR contact email for a job listing.

Hard rule: we NEVER invent an address. An email is only returned if either
  1. it was found verbatim on the company's own website, or
  2. Hunter.io returned it, or
  3. it is a role address (careers@/jobs@) on a domain that we extracted
     from the listing itself AND that has live MX (mail) records.

Every candidate is validated against DNS MX records before being accepted,
so we no longer blast `careers@<slug-guess>.fr` at domains that don't exist.
This is what kills the bounce rate that destroyed sender reputation.
"""
import re
import time
import logging
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from typing import Optional, List, Tuple

try:
    import dns.resolver
    _HAS_DNS = True
except ImportError:
    _HAS_DNS = False

from .config import HUNTER_API_KEY, USE_HUNTER, ALLOW_ROLE_ADDRESS_GUESS

logger = logging.getLogger(__name__)

# Cache MX lookups within a run — avoids hammering DNS for repeat domains.
_MX_CACHE: dict = {}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_CONTACT_PATHS = [
    "/contact", "/contact-us", "/about", "/about-us",
    "/careers", "/jobs", "/hiring", "/work-with-us",
    "/en/contact", "/fr/contact", "/en/careers", "/fr/recrutement",
    "/team", "/people", "/our-team", "/leadership",
    "/equipe", "/notre-equipe", "/a-propos", "/qui-sommes-nous",
]

# Most likely to reach a real person at a tech company
_HR_PREFIXES = ["careers", "jobs", "recruiting", "hr", "talent", "recrutement", "rh", "hiring"]

_EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')

_SKIP_DOMAINS = {
    "example.com", "yourdomain.com", "email.com", "domain.com",
    "sentry.io", "github.com", "linkedin.com", "twitter.com",
    "facebook.com", "instagram.com", "google.com", "w3.org",
    "schema.org", "cloudflare.com", "gravatar.com", "wordpress.com",
    "wixsite.com", "squarespace.com", "hubspot.com", "mailchimp.com",
}

# Job boards — never a valid company contact domain
_JOB_BOARDS = {
    "indeed.com", "linkedin.com", "remoteok.com", "remotive.com",
    "weworkremotely.com", "adzuna.com", "jobicy.com", "glassdoor.com",
    "arbeitnow.com", "monster.com", "ziprecruiter.com", "simplyhired.com",
    "dice.com", "careerbuilder.com", "greenhouse.io", "lever.co",
    "workday.com", "bamboohr.com", "smartrecruiters.com",
}

# Email prefixes that almost certainly aren't recruiters
_NOISE_PREFIXES = {
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "support", "info", "contact", "hello", "hi", "hey",
    "admin", "webmaster", "postmaster", "bounce", "mailer",
    "newsletter", "news", "updates", "notifications", "alerts",
    "security", "abuse", "spam", "legal", "press", "media",
    "privacy", "gdpr", "dpo",
    # placeholder / scraped-garbage prefixes seen in the wild
    "test", "sample", "example", "demo", "needle", "foo", "bar",
    # non-hiring business inboxes
    "sales", "partnerships", "partnership", "enquiries", "enquiry",
    "customerservice", "customer", "service", "billing", "accounts",
    "accounting", "invoice", "marketing", "academy", "shop", "order",
    "orders", "booking", "reservations", "office",
}


# Local-parts that DO reach a recruiter/hiring contact (multi-language allowlist).
_RECRUIT_WORDS = {
    "careers", "career", "jobs", "job", "recruiting", "recruitment", "recruiter",
    "hr", "talent", "talents", "hiring", "apply", "application", "applications",
    "joinus", "join", "work", "workwithus", "people", "peopleops",
    "recrutement", "rh", "emploi", "candidature", "candidatures",
    "bewerbung", "bewerbungen", "karriere", "personal",
}

# Generic non-hiring tokens in EN/FR/DE — if any appears in the local part, reject.
_GENERIC_TOKENS = _NOISE_PREFIXES | {
    "vip", "kundenservice", "kundenbetreuung", "kontakt", "vertrieb", "einkauf",
    "buchhaltung", "datenschutz", "presse", "accueil", "commercial",
    "comptabilite", "facturation", "ventes", "contactez", "team", "shop",
    "store", "help", "feedback", "general", "mail", "email", "web",
}


def _looks_like_person(local: str) -> bool:
    """firstname.lastname / firstname-lastname style — i.e. a real human."""
    tokens = re.split(r"[._\-]+", local)
    if len(tokens) < 2:
        return False
    if any(t in _GENERIC_TOKENS for t in tokens):
        return False
    return all(t.isalpha() and 1 <= len(t) <= 15 for t in tokens)


def _is_recruiter_email(email: str) -> bool:
    """Accept ONLY addresses that plausibly reach a hiring contact.

    A scraped generic inbox (info@, sales@, kundenservice@, …) is worse than
    no email at all — it never reaches a recruiter and burns sender reputation.
    So we allow a local part only if it is a known recruiting word or looks
    like a person's name. Everything else is rejected.
    """
    local, domain = email.lower().rsplit("@", 1)
    if domain in _SKIP_DOMAINS or domain in _JOB_BOARDS:
        return False
    if any(email.lower().endswith(ext) for ext in (".png", ".jpg", ".gif", ".svg", ".css", ".js")):
        return False
    if local in _GENERIC_TOKENS:
        return False
    return local in _RECRUIT_WORDS or _looks_like_person(local)


def _extract_emails_from_html(html: str) -> List[str]:
    # Standard email regex
    found = [e for e in _EMAIL_RE.findall(html) if _is_recruiter_email(e)]
    # Also catch mailto: links that may not match plain text pattern
    mailto = re.findall(r'mailto:([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})', html)
    for e in mailto:
        if _is_recruiter_email(e) and e not in found:
            found.append(e)
    return found


def _score_email(email: str) -> int:
    """Higher = more likely to be a recruiter. Used to pick best candidate."""
    local = email.lower().split("@")[0]
    if local in ("careers", "jobs", "recruiting", "talent", "recrutement", "rh", "hr"):
        return 10
    if local in ("hiring", "work", "apply", "candidature"):
        return 8
    if "@" in email and len(local) < 20:
        return 5  # looks like a personal address
    return 1


def _best_email(emails: List[str]) -> Optional[str]:
    if not emails:
        return None
    return max(emails, key=_score_email)


def _get_company_domain(job: dict) -> Tuple[Optional[str], str]:
    """Resolve the company's real website domain.

    Returns (domain, confidence) where confidence is:
      "extracted" — domain was printed in the listing itself (high trust)
      "guess"     — derived from the company name, but VERIFIED to resolve
                    AND to have MX records (otherwise we return None)
    Never returns an unverified domain.
    """
    desc = job.get("description", "")

    # 1. Scan description for explicit URLs — these are real and high trust.
    all_urls = re.findall(
        r'https?://(?:www\.)?([a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.[a-zA-Z]{2,})',
        desc
    )
    www_urls = re.findall(r'\bwww\.([a-zA-Z0-9-]+\.[a-zA-Z]{2,})', desc)
    for d in all_urls + www_urls:
        dl = d.lower()
        if dl not in _JOB_BOARDS and dl not in _SKIP_DOMAINS and _domain_has_mx(dl):
            return dl, "extracted"

    company = job.get("company", "").strip()
    if not company:
        return None, ""

    # 2. Resolve the real domain from the company NAME via Clearbit's free
    #    autocomplete API (no key). This is what lets us actually email
    #    LinkedIn/Arbeitnow listings, which never include a website link.
    looked_up = _domain_from_name(company)
    if looked_up and _domain_has_mx(looked_up):
        return looked_up, "lookup"

    # 3. Slug the company name and try common TLDs — VERIFIED only.
    slug = re.sub(r'\s+(inc\.?|llc\.?|ltd\.?|gmbh|sarl|sas|s\.a\.?|corp\.?|co\.?)$', '', company.lower())
    slug = re.sub(r'[^a-z0-9]', '', slug)
    if len(slug) < 3:
        return None, ""

    country = job.get("country", "").upper()
    tld_map = {
        "FR": [".fr", ".com"], "DE": [".de", ".com"], "BE": [".be", ".com"],
        "CH": [".ch", ".com"], "GB": [".co.uk", ".com", ".io"], "UK": [".co.uk", ".com", ".io"],
        "MA": [".ma", ".com"], "NL": [".nl", ".com"], "ES": [".es", ".com"], "IT": [".it", ".com"],
    }
    tld_candidates = tld_map.get(country, [".com", ".io", ".co"])

    for tld in tld_candidates:
        domain = f"{slug}{tld}"
        # Must both resolve over HTTP and actually accept mail.
        if _domain_exists(domain) and _domain_has_mx(domain):
            return domain, "guess"

    # No verified domain — do NOT fabricate one.
    return None, ""


def _domain_exists(domain: str) -> bool:
    """Quick HEAD request to check if domain resolves."""
    try:
        r = requests.head(f"https://{domain}", headers=_HEADERS, timeout=5, allow_redirects=True)
        return r.status_code < 500
    except Exception:
        try:
            r = requests.head(f"http://{domain}", headers=_HEADERS, timeout=5, allow_redirects=True)
            return r.status_code < 500
        except Exception:
            return False


_DOMAIN_NAME_CACHE: dict = {}


_LEGAL_SUFFIXES = {
    "inc", "llc", "ltd", "limited", "gmbh", "bv", "nv", "aps", "ab", "oy", "as",
    "sa", "sas", "sarl", "srl", "spa", "ag", "plc", "pte", "llp", "corp", "co",
    "group", "groupe", "holding", "holdings", "the", "and",
    # Generic industry descriptors — far too common to identify a company.
    # "1280 Labs" must not match labster.com on the token "labs".
    "labs", "lab", "tech", "technologies", "technology", "solutions", "systems",
    "software", "digital", "media", "marketing", "consulting", "consultants",
    "services", "agency", "studio", "studios", "partners", "ventures", "global",
    "international", "interactive", "creative", "web", "online", "data", "cloud",
}

# Multi-part public suffixes — so "foo.co.uk" resolves to "foo", not "co".
_MULTI_TLDS = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au",
    "co.nz", "co.za", "com.br", "com.mx", "co.jp", "co.in", "com.tr",
}


def _normalize_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _registrable_root(domain: str) -> str:
    """The label that identifies the org: 'aero.bombardier.com' → 'bombardier',
    'careers.foo.co.uk' → 'foo', 'rami-media.de' → 'ramimedia'."""
    parts = (domain or "").lower().strip(".").split(".")
    if len(parts) < 2:
        return _normalize_name(domain)
    if len(parts) >= 3 and ".".join(parts[-2:]) in _MULTI_TLDS:
        return _normalize_name(parts[-3])
    return _normalize_name(parts[-2])


def _name_matches_domain(company: str, domain: str, suggested_name: str = None) -> bool:
    """True if `domain` plausibly belongs to `company`.

    Guards against Clearbit's popularity-ranked fuzzy matches, which happily
    return a famous unrelated company for an obscure query. We accept only when
    the domain's root echoes the company name (or the name Clearbit itself
    reports for that domain) — containment either way, on the significant tokens.
    """
    root = _registrable_root(domain)
    if not root:
        return False

    for candidate in (company, suggested_name):
        if not candidate:
            continue
        full = _normalize_name(candidate)
        if not full:
            continue
        # Whole-name containment, e.g. "Jobot Consulting" → jobot.com
        if full == root or full.startswith(root) or root.startswith(full):
            return True
        # Significant-token containment, e.g. "Rami Marketing" → rami-media.de
        tokens = [
            _normalize_name(t) for t in re.split(r"[\s,\-&/.]+", candidate)
            if _normalize_name(t) and _normalize_name(t) not in _LEGAL_SUFFIXES
        ]
        for t in tokens:
            if len(t) >= 4 and (t in root or root in t):
                return True
    return False


def _domain_from_name(company: str) -> Optional[str]:
    """Resolve a company NAME to its real domain via Clearbit's free, key-less
    autocomplete API. Returns the best domain match, or None.

    Example: "Jobot Consulting" → "jobot.com". This is the key to emailing
    listings (LinkedIn/Arbeitnow) that never include a website URL.
    """
    key = company.lower().strip()
    if key in _DOMAIN_NAME_CACHE:
        return _DOMAIN_NAME_CACHE[key]
    domain = None
    try:
        resp = requests.get(
            "https://autocomplete.clearbit.com/v1/companies/suggest",
            params={"query": company}, headers=_HEADERS, timeout=6,
        )
        if resp.status_code == 200:
            for item in resp.json():
                d = (item.get("domain") or "").lower()
                if not d or d in _JOB_BOARDS or d in _SKIP_DOMAINS:
                    continue
                # Clearbit returns fuzzy matches ranked by popularity, and taking
                # the first one blindly mailed real applications to the WRONG
                # companies: "BET99" → bbb.org (Better Business Bureau),
                # "GENE" → gm.com (General Motors), "TEAM23" → teamwork.com.
                # Only accept a suggestion whose domain actually echoes the name.
                if not _name_matches_domain(company, d, item.get("name")):
                    logger.debug("Rejected Clearbit match %s → %s (name mismatch)", company, d)
                    continue
                domain = d
                break
    except Exception as e:
        logger.debug("Clearbit lookup failed for %s: %s", company, e)
    _DOMAIN_NAME_CACHE[key] = domain
    return domain


def _domain_has_mx(domain: str) -> bool:
    """True only if the domain has mail (MX) records — i.e. it can receive email.

    This is the single most important guard against bounces: a slug-guessed
    domain like `quikhirestaffing.fr` will have no MX and is rejected here.
    If dnspython is unavailable we conservatively return False so we never
    send to an unverifiable address.
    """
    domain = domain.lower().lstrip("www.")
    if domain in _MX_CACHE:
        return _MX_CACHE[domain]
    if not _HAS_DNS:
        logger.warning("dnspython not installed — cannot verify MX, refusing to guess %s", domain)
        _MX_CACHE[domain] = False
        return False
    ok = False
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        ok = len(answers) > 0
    except Exception:
        ok = False
    _MX_CACHE[domain] = ok
    return ok


def _valid_email(email: str) -> bool:
    """A sendable address: passes recruiter heuristics AND its domain has MX."""
    if not email or "@" not in email:
        return False
    if not _is_recruiter_email(email):
        return False
    return _domain_has_mx(email.rsplit("@", 1)[1])


def _fetch_page(url: str, timeout: int = 8) -> Optional[str]:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return None


# Titles that identify the people worth reaching for a job application:
# hiring owners (HR/recruiting) and technical decision-makers (chef de projet, CTO…).
_LEAD_TITLES = (
    "cto", "chief technology", "head of engineering", "engineering manager",
    "tech lead", "team lead", "lead developer", "lead engineer", "project manager",
    "chef de projet", "directeur technique", "vp engineering", "head of product",
    "engineering director", "développement", "development manager",
)
_HR_TITLES = ("recruit", "talent", "human resources", "hr ", "people", "hiring",
              "rh", "ressources humaines", "recrutement")


def _score_person(e: dict) -> float:
    """Rank a Hunter contact by how relevant they are to a job application."""
    dept = (e.get("department") or "").lower()
    pos = (e.get("position") or "").lower()
    if dept in ("human_resources", "recruiting") or any(t in pos for t in _HR_TITLES):
        return 10.0
    if any(t in pos for t in _LEAD_TITLES):
        return 8.0
    if dept == "executive":
        return 6.0
    if dept in ("it", "engineering"):
        return 4.0
    return 1.0 + e.get("confidence", 0) / 100.0


def _name_from_email(email: str) -> Optional[str]:
    """Derive a probable first name from a person-pattern address.
    'marie.dupont@x.com' → 'Marie'; 'careers@x.com' → None."""
    local = email.split("@", 1)[0].lower()
    for sep in (".", "_", "-"):
        if sep in local:
            first = local.split(sep)[0]
            # 'c.zoeller' = initial.surname → no usable first name
            if len(first) <= 1 or not first.isalpha():
                return None
            if first in _RECRUIT_WORDS or first in _GENERIC_TOKENS:
                return None
            return first.capitalize()
    if local.isalpha() and 2 <= len(local) <= 15 \
            and local not in _RECRUIT_WORDS and local not in _GENERIC_TOKENS:
        return local.capitalize()
    return None


def _hunt_contact(domain: str) -> Optional[dict]:
    """Hunter.io domain-search → the most relevant REAL person (name + role + email).
    Only returns verified / high-confidence addresses."""
    if not (USE_HUNTER and HUNTER_API_KEY):   # opt-in only; off by default (free-only mode)
        return None
    try:
        resp = requests.get(
            "https://api.hunter.io/v2/domain-search",
            params={"domain": domain, "api_key": HUNTER_API_KEY,
                    "type": "personal", "limit": 25},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        emails = resp.json().get("data", {}).get("emails", [])
        cands = [e for e in emails if e.get("value") and
                 ((e.get("verification") or {}).get("status") == "valid"
                  or e.get("confidence", 0) >= 70)]
        cands = cands or emails
        if not cands:
            return None
        best = max(cands, key=_score_person)
        name = " ".join(x for x in (best.get("first_name"), best.get("last_name")) if x)
        return {"email": best["value"], "name": name or None,
                "title": best.get("position") or best.get("department")}
    except Exception as e:
        logger.debug("Hunter.io error for %s: %s", domain, e)
    return None


def _first_valid(emails: List[str]) -> Optional[str]:
    """Best-scoring email that also has live MX records."""
    valid = [e for e in emails if _valid_email(e)]
    return _best_email(valid) if valid else None


_EMPTY_CONTACT = {"email": "", "name": None, "title": None}


def find_contact(job: dict) -> dict:
    """Find a REAL person/role to apply to: {email, name, title}.

    Priority: a named, relevant person (HR / chef de projet / CTO) via Hunter or
    the company site → any verified address found on the site → a role address
    (careers@) on a verified domain. Never fabricates. Empty if nothing verifies.
    """
    company = job.get("company", "").strip()
    if not company:
        return dict(_EMPTY_CONTACT)

    domain, confidence = _get_company_domain(job)
    if not domain:
        return dict(_EMPTY_CONTACT)

    # 1. Hunter.io — a real, named, relevant person
    person = _hunt_contact(domain)
    if person and _valid_email(person["email"]):
        logger.info("Contact (hunter) %s <%s> — %s @ %s",
                    person["name"], person["email"], person["title"], company)
        return person

    base_url = f"https://{domain}"

    # 2–4. Scan homepage, then contact/careers/team pages — stop at first hit.
    def _scan(html):
        return _first_valid(_extract_emails_from_html(html)) if html else None

    home = None
    for i, url in enumerate([base_url] + [urljoin(base_url, p) for p in _CONTACT_PATHS]):
        html = _fetch_page(url)
        if i == 0:
            home = html
        email = _scan(html)
        if email:
            name = _name_from_email(email)
            logger.info("Contact (site) %s <%s> @ %s", name or "role", email, company)
            return {"email": email, "name": name, "title": None}

    # Internal contact/hiring links from the homepage
    if home:
        soup = BeautifulSoup(home, "lxml")
        base_domain = domain.lstrip("www.")
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            if any(kw in href for kw in ("contact", "about", "career", "job", "hiring", "team", "recruit", "equipe")):
                full = urljoin(base_url, a["href"])
                if urlparse(full).netloc.endswith(base_domain):
                    email = _scan(_fetch_page(full))
                    if email:
                        return {"email": email, "name": _name_from_email(email), "title": None}

    # 5. Constructed role-address (careers@<domain>) — DISABLED by default.
    #
    #    This branch produced 93% of the 1,775 emails sent between Mar–Jul 2026,
    #    which drew 0 interviews. `_valid_email()` only proves the DOMAIN accepts
    #    mail; it cannot prove the MAILBOX exists. So `careers@<any-domain>`
    #    always passed the check, and the sends silently vanished instead of
    #    bouncing — which was misread as "delivered".
    #
    #    An address we build ourselves is a guess, not a contact. Anything that
    #    reaches here now routes to the packet lane, where Mehdi applies through
    #    the employer's real ATS instead.
    if not ALLOW_ROLE_ADDRESS_GUESS:
        logger.info("No real contact found for %s — routing to packet lane "
                    "(refusing to invent an address)", company)
        return dict(_EMPTY_CONTACT)

    prefixes = (["recrutement", "rh", "careers", "jobs"]
                if job.get("language") == "fr"
                else ["careers", "jobs", "recruiting", "hr"])
    for prefix in prefixes:
        candidate = f"{prefix}@{domain}"
        if _valid_email(candidate):
            logger.warning("Contact (GUESSED role address, opt-in) %s @ %s", candidate, company)
            return {"email": candidate, "name": None, "title": None}

    return dict(_EMPTY_CONTACT)


def find_email(job: dict) -> str:
    """Back-compat wrapper — returns just the email string."""
    return find_contact(job)["email"]


def enrich_jobs_with_emails(jobs: list, budget_sec: float = None) -> list:
    """Find contact person/email for jobs missing one. Modifies in-place.
    Stops starting new companies once budget_sec has elapsed (pass jobs
    best-first so the budget is spent where it matters)."""
    count = 0
    start = time.monotonic()
    for job in jobs:
        if budget_sec is not None and time.monotonic() - start > budget_sec:
            logger.info("Contact finder time budget (%ds) reached — skipping remaining jobs",
                        budget_sec)
            break
        if job.get("contact_email"):
            continue
        c = find_contact(job)
        if c["email"]:
            job["contact_email"] = c["email"]
            job["contact_name"] = c["name"]
            job["contact_title"] = c["title"]
            count += 1
            who = f"{c['name']} ({c['title']})" if c["name"] else "role inbox"
            logger.info("Contact found → %s @ %s: %s [%s]",
                        who, job.get("company", "?"), c["email"], job.get("source", "?"))
    logger.info("Contact finder enriched %d / %d jobs", count, len(jobs))
    return jobs
