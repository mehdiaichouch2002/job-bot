import re
import hashlib
from abc import ABC, abstractmethod
from typing import List, Dict


class BaseJobScraper(ABC):
    SOURCE_NAME = "unknown"

    @abstractmethod
    def fetch_jobs(self, keywords: List[str]) -> List[Dict]:
        """Return a list of job dicts with keys:
        id, title, company, location, country, description, url, contact_email, source
        """

    @staticmethod
    def make_id(url: str, title: str = "") -> str:
        return hashlib.md5(f"{url}|{title}".encode()).hexdigest()

    @staticmethod
    def extract_email(text: str) -> str:
        """Extract first plausible recruiter email from text."""
        if not text:
            return ""
        matches = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', text)
        skip = {"example.com", "yourdomain.com", "email.com", "domain.com"}
        for m in matches:
            domain = m.split("@", 1)[1].lower()
            if domain not in skip and not any(m.lower().endswith(ext) for ext in (".png", ".jpg", ".gif")):
                return m
        return ""

    @staticmethod
    def clean_html(html: str) -> str:
        if not html:
            return ""
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'&[a-z]+;', ' ', text)
        return re.sub(r'\s+', ' ', text).strip()
