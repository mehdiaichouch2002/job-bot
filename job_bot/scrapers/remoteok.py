import time
import logging
import requests
from typing import List, Dict
from .base import BaseJobScraper

logger = logging.getLogger(__name__)


class RemoteOKScraper(BaseJobScraper):
    SOURCE_NAME = "remoteok"
    API_URL = "https://remoteok.com/api"

    def fetch_jobs(self, keywords: List[str]) -> List[Dict]:
        jobs = []
        try:
            time.sleep(2)  # RemoteOK asks for a delay
            resp = requests.get(
                self.API_URL,
                headers={"User-Agent": "JobBot/1.0 (mehdi2002aichouch@gmail.com)"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            listings = [d for d in data if isinstance(d, dict) and d.get("position")]

            for item in listings:
                title = item.get("position", "")
                company = item.get("company", "")
                desc = self.clean_html(item.get("description", ""))
                tags = " ".join(item.get("tags", []))
                combined = f"{title} {company} {desc} {tags}".lower()

                if not any(kw.lower() in combined for kw in keywords):
                    continue

                url = item.get("url") or f"https://remoteok.com/remote-jobs/{item.get('slug', '')}"
                jobs.append({
                    "id": self.make_id(url, title),
                    "title": title,
                    "company": company,
                    "location": "Remote",
                    "country": "",
                    "description": desc[:2000],
                    "url": url,
                    "contact_email": self.extract_email(desc),
                    "source": self.SOURCE_NAME,
                })

            logger.info("RemoteOK: %d matching jobs", len(jobs))
        except Exception as e:
            logger.error("RemoteOK error: %s", e)
        return jobs
