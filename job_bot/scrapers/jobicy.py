import logging
import requests
from typing import List, Dict
from .base import BaseJobScraper

logger = logging.getLogger(__name__)

# Jobicy free public API — no key needed
# tag filter is unreliable; we fetch all and filter by keyword locally
_API_URL = "https://jobicy.com/api/v2/remote-jobs"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; JobBot/1.0; +bot)",
    "Accept": "application/json",
}
_COUNTRY_MAP = {
    "France": "FR", "United Kingdom": "GB", "Germany": "DE",
    "Canada": "CA", "Belgium": "BE", "Switzerland": "CH",
    "Netherlands": "NL", "Spain": "ES", "Poland": "PL",
}


class JobicyScraper(BaseJobScraper):
    SOURCE_NAME = "jobicy"

    def fetch_jobs(self, keywords: List[str]) -> List[Dict]:
        jobs = []
        seen = set()
        kw_lower = [k.lower() for k in keywords]

        try:
            resp = requests.get(
                _API_URL,
                params={"count": 50},
                headers=_HEADERS,
                timeout=15,
            )
            data = resp.json()
            for item in data.get("jobs", []):
                title = item.get("jobTitle", "")
                desc  = self.clean_html(item.get("jobDescription", "") or item.get("jobExcerpt", ""))
                url   = item.get("url", "")

                # Filter by keyword in title or description
                combined = f"{title} {desc[:500]}".lower()
                if not any(kw in combined for kw in kw_lower):
                    continue

                job_id = self.make_id(url, title)
                if job_id in seen:
                    continue
                seen.add(job_id)

                geo     = item.get("jobGeo", "")
                country = _COUNTRY_MAP.get(geo, geo)

                jobs.append({
                    "id": job_id,
                    "title": title,
                    "company": item.get("companyName", ""),
                    "location": geo,
                    "country": country,
                    "description": desc[:2000],
                    "url": url,
                    "contact_email": self.extract_email(desc),
                    "source": self.SOURCE_NAME,
                })

        except Exception as e:
            logger.error("Jobicy error: %s", e)

        logger.info("Jobicy: %d jobs", len(jobs))
        return jobs
