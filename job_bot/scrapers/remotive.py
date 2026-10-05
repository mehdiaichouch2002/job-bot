import logging
import requests
from typing import List, Dict
from .base import BaseJobScraper

logger = logging.getLogger(__name__)


class RemotiveScraper(BaseJobScraper):
    SOURCE_NAME = "remotive"
    API_URL = "https://remotive.com/api/remote-jobs"

    def fetch_jobs(self, keywords: List[str]) -> List[Dict]:
        jobs = []
        seen = set()
        try:
            for keyword in keywords[:3]:
                resp = requests.get(
                    self.API_URL,
                    params={"search": keyword, "limit": 20},
                    timeout=15,
                )
                resp.raise_for_status()
                for item in resp.json().get("jobs", []):
                    url = item.get("url", "")
                    title = item.get("title", "")
                    job_id = self.make_id(url, title)
                    if job_id in seen:
                        continue
                    seen.add(job_id)

                    desc = self.clean_html(item.get("description", ""))
                    jobs.append({
                        "id": job_id,
                        "title": title,
                        "company": item.get("company_name", ""),
                        "location": item.get("candidate_required_location", "Remote"),
                        "country": "",
                        "description": desc[:2000],
                        "url": url,
                        "contact_email": self.extract_email(desc),
                        "source": self.SOURCE_NAME,
                    })

            logger.info("Remotive: %d matching jobs", len(jobs))
        except Exception as e:
            logger.error("Remotive error: %s", e)
        return jobs
