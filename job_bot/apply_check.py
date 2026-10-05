"""
apply_check.py
~~~~~~~~~~~~~~
Decide whether a listing is an "Easy Apply" job — one you apply to directly
(in a couple of clicks) rather than being bounced to an external company ATS
with a long form.

LinkedIn: fetch the guest job page; if it routes to an external company site
("offsite" / "apply on company website") it's NOT easy apply. Absence of that
marker means you apply through LinkedIn = Easy Apply.

Non-LinkedIn boards (Remotive, WeWorkRemotely, Arbeitnow, RemoteOK, Jobicy) are
direct-apply by nature, so they count as easy apply.
"""
import logging
import requests

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}

# Markers that mean the application is hosted off LinkedIn (external ATS).
_OFFSITE_MARKERS = ("apply on company website", "offsite", "apply externally")
# Markers that mean the listing is closed / expired / removed.
_CLOSED_MARKERS = (
    "no longer accepting applications", "no longer available",
    "this job has been closed", "job is not available",
    "the job you were looking for", "this job is no longer",
)

_cache: dict = {}


def check_listing(job: dict) -> dict:
    """Fetch the listing once and report {'available': .., 'easy_apply': ..}.

    Values are True / False / None (unknown). Used to keep closed listings and
    external-ATS jobs out of the digest.
    """
    url = job.get("url", "")
    source = job.get("source", "")

    # Non-LinkedIn boards: assume live and direct-apply (they expire off-list).
    if source and source != "linkedin" and "linkedin.com" not in url:
        return {"available": True, "easy_apply": True}
    if not url or "linkedin.com" not in url:
        return {"available": None, "easy_apply": None}
    if url in _cache:
        return _cache[url]

    result = {"available": None, "easy_apply": None}
    try:
        r = requests.get(url, headers=_HEADERS, timeout=12)
        if r.status_code in (404, 410):
            result = {"available": False, "easy_apply": None}
        elif r.status_code == 200:
            text = r.text.lower()
            if any(m in text for m in _CLOSED_MARKERS):
                result = {"available": False, "easy_apply": None}
            else:
                result = {"available": True,
                          "easy_apply": not any(m in text for m in _OFFSITE_MARKERS)}
    except Exception as e:
        logger.debug("Listing check failed for %s: %s", url, e)
    _cache[url] = result
    return result


def is_easy_apply(job: dict):
    """Back-compat: True (easy), False (external), or None (unknown)."""
    return check_listing(job)["easy_apply"]
