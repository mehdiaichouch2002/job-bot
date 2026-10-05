"""
Arbeitnow scraper (replaces Indeed RSS which is now blocked).
Arbeitnow provides a free, open job board API — no key needed.
API docs: https://arbeitnow.com/api
"""
import logging
import requests
from typing import List, Dict
from .base import BaseJobScraper

logger = logging.getLogger(__name__)

_API_URL = "https://arbeitnow.com/api/job-board-api"

_LOCATION_TO_COUNTRY = {
    "france": "FR", "germany": "DE", "deutschland": "DE",
    "united kingdom": "GB", "uk": "GB", "england": "GB",
    "switzerland": "CH", "suisse": "CH", "schweiz": "CH",
    "belgium": "BE", "belgique": "BE", "belgien": "BE",
    "netherlands": "NL", "canada": "CA", "australia": "AU",
    "spain": "ES", "espagne": "ES", "italy": "IT", "italia": "IT",
    "morocco": "MA", "maroc": "MA",
    "united states": "US", "usa": "US",
}


def _guess_country(location: str) -> str:
    loc = location.lower()
    for name, code in _LOCATION_TO_COUNTRY.items():
        if name in loc:
            return code
    return ""
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; JobBot/1.0)",
    "Accept": "application/json",
}

# Arbeitnow search tags that match our target roles
_SEARCH_TAGS = ["magento", "php", "laravel", "ecommerce", "react"]


class IndeedScraper(BaseJobScraper):
    """Kept as IndeedScraper name for backwards-compat; actually scrapes Arbeitnow."""
    SOURCE_NAME = "arbeitnow"

    def fetch_jobs(self, keywords: List[str]) -> List[Dict]:
        jobs = []
        seen = set()
        kw_lower = [k.lower() for k in keywords]

        try:
            # Fetch up to 3 pages (each page ≈ 100 jobs)
            for page in range(1, 4):
                resp = requests.get(
                    _API_URL,
                    params={"page": page},
                    headers=_HEADERS,
                    timeout=15,
                )
                if resp.status_code != 200:
                    logger.warning("Arbeitnow page %d → HTTP %s", page, resp.status_code)
                    break
                data = resp.json()
                items = data.get("data", [])
                if not items:
                    break

                for item in items:
                    title = item.get("title", "")
                    desc  = self.clean_html(item.get("description", ""))
                    tags  = [t.lower() for t in item.get("tags", [])]
                    slug  = item.get("slug", "")
                    url   = f"https://arbeitnow.com/view/{slug}" if slug else item.get("url", "")

                    # Filter: keyword must appear in title, tags, or description
                    combined = f"{title} {' '.join(tags)} {desc[:500]}".lower()
                    if not any(kw in combined for kw in kw_lower):
                        continue

                    job_id = self.make_id(url, title)
                    if job_id in seen:
                        continue
                    seen.add(job_id)

                    # Arbeitnow includes location info
                    location = item.get("location", "")
                    # Extract ISO country code from location string when possible
                    country  = _guess_country(location)

                    jobs.append({
                        "id": job_id,
                        "title": title,
                        "company": item.get("company_name", ""),
                        "location": location,
                        "country": country,
                        "description": desc[:2000],
                        "url": url,
                        "contact_email": self.extract_email(desc),
                        "source": self.SOURCE_NAME,
                    })

        except Exception as e:
            logger.error("Arbeitnow error: %s", e)

        logger.info("Arbeitnow: %d jobs", len(jobs))
        return jobs
