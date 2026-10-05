import logging
import feedparser
from typing import List, Dict
from .base import BaseJobScraper

logger = logging.getLogger(__name__)

_FEEDS = {
    "programming":  "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "back-end":     "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
    "full-stack":   "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
}


class WeWorkRemotelyScraper(BaseJobScraper):
    SOURCE_NAME = "weworkremotely"

    def fetch_jobs(self, keywords: List[str]) -> List[Dict]:
        jobs = []
        seen = set()
        try:
            for feed_url in _FEEDS.values():
                feed = feedparser.parse(feed_url)
                for entry in feed.entries:
                    raw_title = entry.get("title", "")
                    link = entry.get("link", "")
                    summary = self.clean_html(entry.get("summary", ""))

                    # WWR format: "Company: Job Title"
                    if ": " in raw_title:
                        company, title = raw_title.split(": ", 1)
                    else:
                        company, title = "", raw_title

                    combined = f"{title} {company} {summary}".lower()
                    if not any(kw.lower() in combined for kw in keywords):
                        continue

                    job_id = self.make_id(link, title)
                    if job_id in seen:
                        continue
                    seen.add(job_id)

                    jobs.append({
                        "id": job_id,
                        "title": title.strip(),
                        "company": company.strip(),
                        "location": "Remote",
                        "country": "",
                        "description": summary[:2000],
                        "url": link,
                        "contact_email": self.extract_email(summary),
                        "source": self.SOURCE_NAME,
                    })

            logger.info("WeWorkRemotely: %d matching jobs", len(jobs))
        except Exception as e:
            logger.error("WeWorkRemotely error: %s", e)
        return jobs
