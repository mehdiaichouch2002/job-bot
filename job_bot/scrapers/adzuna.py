import logging
import requests
from typing import List, Dict
from .base import BaseJobScraper
from ..config import ADZUNA_APP_ID, ADZUNA_API_KEY

logger = logging.getLogger(__name__)

_COUNTRIES = ["gb", "us", "fr", "de", "ca", "au", "be", "ch"]


class AdzunaScraper(BaseJobScraper):
    SOURCE_NAME = "adzuna"
    BASE_URL = "https://api.adzuna.com/v1/api/jobs"

    def fetch_jobs(self, keywords: List[str]) -> List[Dict]:
        if not ADZUNA_APP_ID or not ADZUNA_API_KEY:
            logger.debug("Adzuna credentials not set, skipping")
            return []

        jobs = []
        seen = set()

        for country in _COUNTRIES:
            for keyword in keywords[:5]:
                try:
                    resp = requests.get(
                        f"{self.BASE_URL}/{country}/search/1",
                        params={
                            "app_id": ADZUNA_APP_ID,
                            "app_key": ADZUNA_API_KEY,
                            "what": keyword,
                            "results_per_page": 10,
                            "sort_by": "date",
                        },
                        timeout=15,
                    )
                    resp.raise_for_status()
                    for item in resp.json().get("results", []):
                        url = item.get("redirect_url", "")
                        title = item.get("title", "")
                        job_id = self.make_id(url, title)
                        if job_id in seen:
                            continue
                        seen.add(job_id)

                        desc = self.clean_html(item.get("description", ""))
                        jobs.append({
                            "id": job_id,
                            "title": title,
                            "company": item.get("company", {}).get("display_name", ""),
                            "location": item.get("location", {}).get("display_name", ""),
                            "country": country.upper(),
                            "description": desc[:2000],
                            "url": url,
                            "contact_email": self.extract_email(desc),
                            "source": self.SOURCE_NAME,
                        })
                except Exception as e:
                    logger.error("Adzuna %s/%s error: %s", country, keyword, e)

        logger.info("Adzuna: %d matching jobs", len(jobs))
        return jobs
