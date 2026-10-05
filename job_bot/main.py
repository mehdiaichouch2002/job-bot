import json
import logging
import random
import re
import time
from typing import Dict, List

try:
    from langdetect import detect, LangDetectException
    _HAS_LANGDETECT = True
except ImportError:
    _HAS_LANGDETECT = False

from .config import (SEARCH_KEYWORDS, MAX_JOBS_PER_RUN, DRY_RUN, FRENCH_COUNTRIES, SEND_DIGEST,
                     EXCLUDED_COUNTRIES, EXCLUDED_LOCATION_KEYWORDS, CANADA_LOCATION_KEYWORDS,
                     QUEBEC_KEYWORDS, AUTO_EMAIL, REQUIRE_NAMED_CONTACT,
                     EMAIL_FINDER_BUDGET_SEC, SEND_DEADLINE_SEC, LLM_EXTRACT_MAX,
                     EXTRACT_BUDGET_SEC, EMAIL_PASSWORD)
from .database import (init_db, job_exists, save_job, mark_email_sent, get_stats,
                       update_contact_email, mark_packet_ready, set_evaluation,
                       get_digest_candidates, mark_digested, acted_company_titles, _norm_key,
                       set_easy_apply, is_dead_email)
from .apply_check import check_listing
from .feedback import process_inbox
from .scrapers import (RemoteOKScraper, RemotiveScraper, WeWorkRemotelyScraper,
                       AdzunaScraper, IndeedScraper, LinkedInScraper, JobicyScraper)
from .email_generator import llm_available
from .email_sender import send_email
from .email_finder import enrich_jobs_with_emails, _valid_email
from .extractor import extract
from .matcher import evaluate, BON_MATCH, CONTACT_RESEAU, A_EVITER
from .contacts import find_prior, may_auto_send, record_contact, prior_summary
from .drafter import draft_application_email
from .hireability import classify as classify_hireability
from .packet import build_packet
from .digest import send_digest
from .outreach import run_outreach

logger = logging.getLogger(__name__)

_SCRAPERS = [
    RemoteOKScraper(),
    RemotiveScraper(),
    WeWorkRemotelyScraper(),
    AdzunaScraper(),
    IndeedScraper(),
    JobicyScraper(),
    LinkedInScraper(),   # last — slowest (fetches individual pages)
]

_FRENCH_WORDS = [
    "développeur", "recherchons", "poste", "entreprise",
    "compétences", "rejoindre", "équipe", "expérience requise",
    "offre d'emploi", "candidature",
]

# ── Relevance filtering ──────────────────────────────────────────
# Mehdi's stack: Magento 2 / PHP / Laravel / Symfony / ecommerce + React frontend.
# These keep the bot from emailing .NET / Node / data / thesis postings.

# Title contains one of these → definitely relevant, accept regardless of noise.
_STRONG_POSITIVE = (
    "magento", "adobe commerce", "hyva", "hyvä", "php", "laravel",
    "symfony", "woocommerce", "shopware", "prestashop",
)
# Weak signals — relevant only if no hard-negative tech is present.
_WEAK_POSITIVE = (
    "ecommerce", "e-commerce", "full stack", "full-stack", "fullstack",
    "backend", "back-end", "back end", "web developer", "react", "frontend",
    "front-end", "front end",
)
# Other stacks / non-dev roles he should not be emailing.
_HARD_NEGATIVE = (
    ".net", "c#", "node", "nodejs", "node.js", "golang", " go ", "rust",
    "python", "django", "ruby", "rails", "java ", "kotlin", "scala", "salesforce",
    "sap ", "wordpress", "data engineer", "data scientist", "machine learning",
    "devops", "sre", "qa ", "tester", "designer", "ui/ux", "ux ", "product manager",
    "project manager", "scrum master", "sales", "account ", "marketing", "recruiter",
    "thesis", "phd", "werkstudent", "praktikum", "ausbildung", "internship", "intern ",
)


def _is_relevant(job: Dict) -> bool:
    title = f" {job.get('title', '').lower()} "
    if any(p in title for p in _STRONG_POSITIVE):
        return True
    if any(n in title for n in _HARD_NEGATIVE):
        return False
    return any(p in title for p in _WEAK_POSITIVE)


# Whole-word match so "Indiana, USA" isn't caught by "india", etc.
_EXCLUDED_LOC_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in EXCLUDED_LOCATION_KEYWORDS) + r")\b"
)


def _geo_ok(job: Dict) -> bool:
    """False if the job is tied to an excluded market (e.g. India), unless it is
    a generic remote/worldwide listing with no excluded location named."""
    if job.get("country", "").upper() in EXCLUDED_COUNTRIES:
        return False
    return not _EXCLUDED_LOC_RE.search(job.get("location", "").lower())


_CANADA_LOC_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in CANADA_LOCATION_KEYWORDS) + r")\b"
)


# Other countries that share ambiguous city names with Canada (Halifax/UK,
# London/UK, etc.) — if explicitly named without "canada", it's NOT Canada.
_NOT_CANADA_RE = re.compile(
    r"\b(united kingdom|england|scotland|wales|ireland|united states|u\.?s\.?a|australia)\b"
)


def _is_canada(job: Dict) -> bool:
    if job.get("country", "").upper() == "CA":
        return True
    loc = job.get("location", "").lower()
    if "canada" in loc:
        return True
    if _NOT_CANADA_RE.search(loc):   # another country explicitly named → not Canada
        return False
    return bool(_CANADA_LOC_RE.search(loc))


def _is_quebec(job: Dict) -> bool:
    return any(q in job.get("location", "").lower() for q in QUEBEC_KEYWORDS)


def _cv_for(job: Dict) -> str:
    """Pick the role-matched CV PDF: the
    Magento or Full-Stack résumé that mirrors the posting, by language."""
    from .packet import _pick_cv
    return _pick_cv(job, "fr" if job.get("language") == "fr" else "en")


def _detect_language(job: Dict) -> str:
    # 0. Canada is bilingual — French only for Quebec/Francophone, else detect (default EN).
    #    Must run before the FRENCH_COUNTRIES check (which lists CA).
    if _is_canada(job):
        loc = job.get("location", "").lower()
        if any(q in loc for q in QUEBEC_KEYWORDS):
            return "fr"
        text = f"{job.get('title', '')} {job.get('description', '')[:800]}"
        if _HAS_LANGDETECT and len(text) > 50:
            try:
                if detect(text) == "fr":
                    return "fr"
            except LangDetectException:
                pass
        return "en"

    text = f"{job.get('title', '')} {job.get('description', '')[:800]}"

    # 1. What the posting is ACTUALLY written in wins.
    #
    #    The country code used to take priority, and scraper country fields are
    #    frequently wrong: Bisbase ApS (Danish) and DeepData B.V. (Dutch) were
    #    both tagged "FR" and received French-language applications. The text of
    #    the ad is the reliable signal — a French employer writes in French.
    if _HAS_LANGDETECT and len(text) > 50:
        try:
            lang = detect(text)
            if lang in ("fr", "en"):
                return lang
            # Detected a third language (de/nl/da…) — the ad is not French, so
            # write in English rather than trusting a bogus country code.
            return "en"
        except LangDetectException:
            pass

    # 2. Too little text to detect — fall back to the country code.
    if job.get("country", "").upper() in FRENCH_COUNTRIES:
        return "fr"

    # 3. French keyword fallback
    tl = text.lower()
    if sum(1 for w in _FRENCH_WORDS if w in tl) >= 2:
        return "fr"

    return "en"


def run_pipeline(dry_run: bool = None) -> Dict:
    if dry_run is None:
        dry_run = DRY_RUN

    logger.info("=" * 60)
    logger.info("Job bot pipeline starting  [dry_run=%s]", dry_run)
    logger.info("=" * 60)

    run_start = time.monotonic()
    init_db()
    if not dry_run and not EMAIL_PASSWORD:
        logger.error("EMAIL_PASSWORD is not set (.env) — nothing can be emailed this run: "
                     "no applications, no digest. Fill it in, then run: python run.py --check")
    if not llm_available():
        logger.warning("GROQ_API_KEY is not set — regex extraction and template drafts only")

    # 0. Feedback loop — read our inbox for replies & bounces (learn before sending).
    if not dry_run:
        fb = process_inbox()
        if fb.get("error"):
            logger.info("Inbox feedback: skipped (%s)", fb["error"])
        else:
            logger.info("Inbox feedback: %d repl%s, %d bounce(s) across %d messages",
                        fb["replies"], "y" if fb["replies"] == 1 else "ies",
                        fb["bounces"], fb["checked"])

    # 1. Scrape all sources
    all_jobs: List[Dict] = []
    for scraper in _SCRAPERS:
        try:
            jobs = scraper.fetch_jobs(SEARCH_KEYWORDS)
            with_contact = sum(1 for j in jobs if j.get("contact_email"))
            logger.info("%-20s → %3d jobs, %d with email",
                        scraper.SOURCE_NAME, len(jobs), with_contact)
            all_jobs.extend(jobs)
        except Exception as e:
            logger.error("Scraper %s crashed: %s", scraper.SOURCE_NAME, e)

    logger.info("Total fetched: %d", len(all_jobs))

    # 2a. Drop off-stack / non-dev listings and excluded-market locations
    on_stack = [j for j in all_jobs if _is_relevant(j)]
    relevant = [j for j in on_stack if _geo_ok(j)]
    logger.info("Relevant to profile: %d / %d (off-target %d, excluded-location %d)",
                len(relevant), len(all_jobs),
                len(all_jobs) - len(on_stack), len(on_stack) - len(relevant))

    # 2b. Filter jobs not seen before (by listing id)
    new_jobs = [j for j in relevant if not job_exists(j["id"])]

    # 2c. Dedupe by company+title — collapse the same role reposted under
    #     multiple listing ids (e.g. agency spam), within this run and against
    #     roles already emailed/packeted in past runs.
    acted = acted_company_titles()
    deduped, seen_keys = [], set()
    for j in new_jobs:
        key = _norm_key(j.get("company", ""), j.get("title", ""))
        if key in acted or key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(j)
    dropped_dupes = len(new_jobs) - len(deduped)
    new_jobs = deduped
    logger.info("New jobs: %d (deduped %d repeat company+title)", len(new_jobs), dropped_dupes)

    # 3. Language, market and hireability — then persist.
    for job in new_jobs:
        job["language"] = _detect_language(job)
        job["market"] = "canada" if _is_canada(job) else "intl"
        job["quebec"] = job["market"] == "canada" and _is_quebec(job)

        # Can this employer actually hire someone living in Morocco? 92% of the
        # jobs emailed in the first four months could not — onsite EU roles,
        # geo-locked "remote", apprenticeships. A "no" here makes the job
        # À ÉVITER without spending an LLM call on it.
        hireable, channel, hire_reasons = classify_hireability(job)
        if job["market"] == "canada" and not hireable:
            # The Canada track is an explicit relocation goal, so a Canadian
            # onsite role is still worth applying to — that's the point of it.
            hireable, channel = True, "canada"
            hire_reasons = ["Canada relocation target"]
        job["hireable"] = hireable
        job["channel"] = channel
        job["hire_reasons"] = hire_reasons
        job["fit_reasons"] = [f"[{channel}] {hire_reasons[0]}" if hire_reasons else ""]
        save_job(job)

    # 4. Criteria extraction + honest matching (extractor.py → matcher.py).
    #    A free regex pass evaluates everything; the LLM then re-reads the most
    #    promising hireable ads, capped in count and time so the run stays
    #    inside the Actions limit. The matcher itself never calls an LLM.
    for job in new_jobs:
        job["eval"] = evaluate(job, extract(job, use_llm=False),
                               job["hireable"], job["hire_reasons"])
    llm_read = 0
    if llm_available():
        pool = sorted((j for j in new_jobs if j["hireable"]),
                      key=lambda j: -j["eval"]["score"])[:LLM_EXTRACT_MAX]
        extract_start = time.monotonic()
        for job in pool:
            if time.monotonic() - extract_start > EXTRACT_BUDGET_SEC:
                logger.info("LLM extraction budget (%ds) reached — regex verdicts kept for the rest",
                            EXTRACT_BUDGET_SEC)
                break
            job["eval"] = evaluate(job, extract(job, use_llm=True), True, job["hire_reasons"])
            llm_read += 1

    good = sorted((j for j in new_jobs if j["eval"]["category"] == BON_MATCH),
                  key=lambda j: -j["eval"]["score"])
    network = sorted((j for j in new_jobs if j["eval"]["category"] == CONTACT_RESEAU),
                     key=lambda j: -j["eval"]["score"])
    avoid = [j for j in new_jobs if j["eval"]["category"] == A_EVITER]
    logger.info("Verdicts: %d bon match · %d contact réseau · %d à éviter (LLM read %d ad(s))",
                len(good), len(network), len(avoid), llm_read)

    # 5. Email finder: crawl company sites only for BON_MATCH jobs, the only
    #    ones that may be auto-emailed. Best-first, under a time budget.
    missing_email = [j for j in good if not j.get("contact_email")] if AUTO_EMAIL else []
    if missing_email:
        logger.info("Running email finder on %d bon-match job(s) without contact email…",
                    len(missing_email))
        enrich_jobs_with_emails(missing_email, budget_sec=EMAIL_FINDER_BUDGET_SEC)
        for job in missing_email:
            if job.get("contact_email"):
                update_contact_email(job["id"], job["contact_email"],
                                     job.get("contact_name"), job.get("contact_title"))

    # 6. Contact history (contacts.py): same person or same company already
    #    solicited? Recorded on the job and shown in the packet and digest.
    for job in new_jobs:
        job["prior"] = find_prior(job) if job["eval"]["category"] != A_EVITER else None
        set_evaluation(job["id"], job["eval"]["category"], job["eval"]["score"],
                       json.dumps(job["eval"], ensure_ascii=False),
                       prior_summary(job["prior"]) or None,
                       status="avoid" if job["eval"]["category"] == A_EVITER else None)

    # 7. AUTO-EMAIL: BON_MATCH only, to a verified, named contact, and only if
    #    the history allows it (no recent mail to the same person, no refusal).
    def _sendable(j):
        e = j.get("contact_email", "")
        return _valid_email(e) and not is_dead_email(e)

    eligible, unnamed, unverifiable, held = [], 0, 0, 0
    for job in (good if AUTO_EMAIL else []):
        if not job.get("contact_email"):
            continue
        if REQUIRE_NAMED_CONTACT and not (job.get("contact_name") or "").strip():
            # A generic inbox is not a contact. Every one of the 1,775 emails
            # that went to an unnamed alias drew zero interviews.
            unnamed += 1
            continue
        if not _sendable(job):
            unverifiable += 1
            continue
        ok, why = may_auto_send(job["prior"])
        if not ok:
            logger.info("Not auto-emailing %s @ %s: %s — draft goes to the packet instead",
                        job.get("title"), job.get("company"), why)
            held += 1
            continue
        eligible.append(job)
    if AUTO_EMAIL:
        logger.info("Eligible to auto-email: %d bon match (skipped: %d unnamed, %d unverifiable, "
                    "%d held by contact history)", len(eligible), unnamed, unverifiable, held)
    else:
        logger.info("AUTO_EMAIL is off — bon-match jobs go to the packet lane")

    sent = failed = 0
    emailed_ids = set()
    for job in eligible[:MAX_JOBS_PER_RUN]:
        if time.monotonic() - run_start > SEND_DEADLINE_SEC:
            logger.info("Send deadline (%ds) reached — remaining jobs get a packet instead",
                        SEND_DEADLINE_SEC)
            break
        try:
            draft = draft_application_email(job, job["eval"], job["prior"])
            job["draft"] = draft
            ok = send_email(job["contact_email"], draft["subject"], draft["body"],
                            pdf_path=_cv_for(job), dry_run=dry_run)
            if ok:
                if not dry_run:     # a dry run must not mark the job as applied
                    mark_email_sent(job["id"], draft["subject"], draft["body"])
                    record_contact(job, draft["subject"])
                emailed_ids.add(job["id"])
                sent += 1
                if not dry_run and sent < MAX_JOBS_PER_RUN:
                    time.sleep(random.uniform(20, 45))
            else:
                failed += 1
        except Exception as e:
            logger.error("Failed to email '%s': %s", job.get("title"), e)
            failed += 1

    # 8. PACKETS: every BON_MATCH not emailed (full application) and every
    #    CONTACT_RESEAU (short honest message), best first. À ÉVITER: nothing.
    packet_candidates = [j for j in good if j["id"] not in emailed_ids] + network
    packets = skipped_closed = 0
    for job in packet_candidates[:MAX_JOBS_PER_RUN]:
        try:
            info = check_listing(job)
            if info["available"] is False:      # listing closed/expired — don't queue it
                skipped_closed += 1
                continue
            draft = job.get("draft")
            if (not draft and job["eval"]["category"] == BON_MATCH and job.get("contact_email")):
                draft = draft_application_email(job, job["eval"], job["prior"])
            path = build_packet(job, job["eval"], draft=draft, prior=job["prior"])
            mark_packet_ready(job["id"], path)
            set_easy_apply(job["id"], info["easy_apply"])
            packets += 1
        except Exception as e:
            logger.error("Failed to build packet for '%s': %s", job.get("title"), e)
    if skipped_closed:
        logger.info("Skipped %d closed/expired listing(s)", skipped_closed)

    # 9. DIGEST: grouped by verdict, with covered criteria, gaps and history.
    digest_sent = False
    if SEND_DIGEST:
        candidates = get_digest_candidates(max_age_days=7)
        if candidates and send_digest(candidates, dry_run=dry_run):
            mark_digested([j["id"] for j in candidates])
            digest_sent = True

    # 10. OUTREACH: a few networking emails to named people (outreach.py).
    try:
        outreach = run_outreach(dry_run=dry_run)
    except Exception as e:
        logger.error("Outreach failed: %s", e)
        outreach = {}

    stats = get_stats()
    result = {
        "total_fetched": len(all_jobs),
        "new_jobs": len(new_jobs),
        "bon_match": len(good),
        "contact_reseau": len(network),
        "a_eviter": len(avoid),
        "emails_sent": sent,
        "emails_failed": failed,
        "packets_built": packets,
        "digest_sent": digest_sent,
        "outreach": outreach,
        "db_stats": stats,
    }

    logger.info("\n=== SUMMARY ===")
    logger.info("New jobs         : %d → %d bon match, %d contact réseau, %d à éviter",
                len(new_jobs), len(good), len(network), len(avoid))
    logger.info("Auto-emailed     : %s", f"{sent} (bon match, named contact)" if AUTO_EMAIL
                else "0 — AUTO_EMAIL off")
    logger.info("Packets prepared : %d", packets)
    logger.info("Digest emailed   : %s", "yes" if digest_sent else "no")
    logger.info("Replies / bounces: %d / %d (all-time)",
                stats.get("replied", 0), stats.get("bounced", 0))
    logger.info("=" * 60)

    return result
