import logging
import requests
from bs4 import BeautifulSoup
from typing import List, Dict
from .base import BaseJobScraper

logger = logging.getLogger(__name__)

# LinkedIn public job search — no auth needed for first ~25 results per query
_SEARCHES = [
    {"keywords": "magento 2 developer",  "location": ""},
    {"keywords": "magento developer",    "location": "France"},
    {"keywords": "magento developer",    "location": "United Kingdom"},
    {"keywords": "magento developer",    "location": "Germany"},
    {"keywords": "php magento",          "location": ""},
    # ── Canada relocation track ──
    {"keywords": "magento developer",    "location": "Canada"},
    {"keywords": "php developer",        "location": "Canada"},
    {"keywords": "développeur php",      "location": "Ottawa, Ontario, Canada"},
    {"keywords": "php laravel developer","location": "Ontario, Canada"},
    {"keywords": "développeur web",      "location": "Moncton, New Brunswick, Canada"},
]

# LinkedIn guest search API (no login required)
_BASE_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_LOCATION_COUNTRY = {
    "France": "FR",
    "United Kingdom": "GB",
    "Germany": "DE",
}


class LinkedInScraper(BaseJobScraper):
    SOURCE_NAME = "linkedin"

    def fetch_jobs(self, keywords: List[str]) -> List[Dict]:
        jobs = []
        seen = set()

        for search in _SEARCHES:
            kw = search["keywords"]
            loc = search["location"]
            country = _LOCATION_COUNTRY.get(loc, "")
            if not country and "canada" in loc.lower():
                country = "CA"
            try:
                params = {
                    "keywords": kw,
                    "location": loc,
                    "start": 0,
                    "sortBy": "DD",       # date descending
                    "f_TPR": "r259200",   # posted in last 3 days
                }
                resp = requests.get(_BASE_URL, params=params,
                                    headers=_HEADERS, timeout=10)
                if resp.status_code != 200:
                    logger.warning("LinkedIn search '%s' → HTTP %s", kw, resp.status_code)
                    continue

                soup = BeautifulSoup(resp.text, "lxml")
                cards = soup.select("li")

                for card in cards:
                    title_el    = card.select_one(".base-search-card__title")
                    company_el  = card.select_one(".base-search-card__subtitle")
                    location_el = card.select_one(".job-search-card__location")
                    link_el     = card.select_one("a.base-card__full-link")

                    if not title_el or not link_el:
                        continue

                    title    = title_el.get_text(strip=True)
                    company  = company_el.get_text(strip=True) if company_el else ""
                    location = location_el.get_text(strip=True) if location_el else loc
                    url      = link_el.get("href", "").split("?")[0]

                    job_id = self.make_id(url, title)
                    if job_id in seen:
                        continue
                    seen.add(job_id)

                    # Use card snippet as description (avoids slow individual-page fetches)
                    snippet_el = card.select_one(".base-search-card__metadata")
                    desc = snippet_el.get_text(separator=" ", strip=True) if snippet_el else ""

                    jobs.append({
                        "id": job_id,
                        "title": title,
                        "company": company,
                        "location": location,
                        "country": country,
                        "description": desc[:2000],
                        "url": url,
                        "contact_email": "",   # LinkedIn never shows emails publicly
                        "source": self.SOURCE_NAME,
                    })

            except Exception as e:
                logger.error("LinkedIn search '%s' error: %s", kw, e)

        logger.info("LinkedIn: %d jobs", len(jobs))
        return jobs
