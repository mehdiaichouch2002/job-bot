"""
harvest.py
~~~~~~~~~~
Find the email addresses a company PUBLISHES on its own website, for
spontaneous applications (outreach.py). Nothing is guessed: an address is kept
only if it appears on one of the company's pages (text, mailto: link or
Cloudflare-protected email), and its domain accepts mail (MX).

    python run.py --harvest data/companies.txt

companies.txt: one "Company name | City | domain" per line. New targets are
appended to data/outreach_targets.csv (git-ignored); existing ones are skipped.
"""
import csv
import logging
import re
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests

logger = logging.getLogger(__name__)

_PAGES = ["", "/contact", "/contact-us", "/contactez-nous", "/nous-contacter", "/fr/contact",
          "/recrutement", "/carriere", "/carrieres", "/careers", "/jobs", "/rejoignez-nous",
          "/nous-rejoindre", "/about", "/a-propos"]
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_JUNK = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js)$|example\.|sentry|wixpress|domain\.com"
                   r"|yourname|email\.com|@2x|u00"
                   # inboxes a job application must not land in (sales, support, billing, bots)
                   # (suporte@ / soporte@: Portuguese / Spanish support, suporte@miztec.pt 2026-10-05)
                   r"|^(sales|support|suporte|soporte|billing|invoice|facturation|compta|comptable|comptabilite|accounting|finance"
                   r"|helpdesk|ticket|tickets|commercial|ventes?|noreply|no-reply|donotreply"
                   # data-protection / legal inboxes: an application sent to privacy@sqli.com
                   # on 2026-09-24 reached nobody who hires
                   r"|privacy|dpo|gdpr|rgpd|legal|juridique|abuse|security|webmaster"
                   # course / booking inboxes (register@uits.ma, 2026-09-30, is a training-school sign-up box)
                   r"|register|registration|inscriptions?|newsletter|booking|reservations?|orders?|commandes?"
                   # purchasing inboxes (achats@logicatel.ma, 2026-09-30)
                   r"|achats?|purchasing|procurement|fournisseurs?|suppliers?"
                   # customer-service inboxes of retail sites (serviceclient@sarouty.ma, 2026-10-05)
                   r"|service[._-]?clients?|customer[._-]?service|customercare|sav|reclamations?"
                   # form placeholders copied off pages (name@capgemini.com bounced on 2026-09-25)
                   r"|name|nom|prenom|firstname|email|e-mail|votre[.\w]*|your[.\w]*|example|exemple|user|test)@",
                   re.I)
# Best first: an inbox meant for hiring, then a general one.
_HIRING = ("recrutement", "recrutements", "rh", "hr", "jobs", "job", "careers", "career", "carriere",
           "emploi", "candidature", "talent", "talents", "join", "rejoindre", "work")
_GENERAL = ("contact", "info", "hello", "bonjour", "salam", "admin", "direction")


def _cfdecode(hexstr: str) -> str:
    key = int(hexstr[:2], 16)
    return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))


def emails_on_page(html: str) -> List[str]:
    found = set(e.lower() for e in _EMAIL.findall(html))
    found |= set(e.lower() for e in re.findall(r"mailto:([^\"'?>\s]+)", html))
    for h in re.findall(r'data-cfemail="([0-9a-fA-F]+)"', html):
        try:
            found.add(_cfdecode(h).lower())
        except ValueError:
            pass
    cleaned = {e.strip(".,;") for e in found}
    return sorted(e for e in cleaned
                  if _EMAIL.fullmatch(e) and not _JUNK.search(e) and len(e) < 60)


def _brand(domain: str) -> str:
    """'www.brandora.ma' -> 'brandora' (the label before the public suffix)."""
    parts = domain.lower().split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "net", "org", "gov", "ac"):
        return parts[-3]
    return parts[-2] if len(parts) >= 2 else parts[0]


def _owned(email: str, domains) -> bool:
    """The address belongs to the company whose site we read: its own domain
    (or one the site redirects to), or the brand name appears in the address.
    Anything else is someone else's address that happened to be on the page —
    typically the web agency credited in the footer — and sending there puts
    the application at the wrong company (see career/10_reset: gm.com, bbb.org)."""
    local, dom = email.lower().split("@", 1)
    for d in domains:
        if dom == d or dom.endswith("." + d) or d.endswith("." + dom):
            return True
        b = _brand(d)
        if len(b) >= 4 and (b == _brand(dom) or b in local):
            return True
    return False


def _rank(email: str, domain) -> int:
    domains = [domain] if isinstance(domain, str) else list(domain)
    if not _owned(email, domains):
        return 9                     # never used
    local = email.split("@", 1)[0].lower()
    if any(local.startswith(w) for w in _HIRING):
        return 0
    if any(local.startswith(w) for w in _GENERAL):
        return 1
    return 4                         # a person's address on the company's domain


def harvest_domain(domain: str, timeout: int = 8) -> Dict:
    """{'email': best published address or '', 'all': [...], 'pages': n}"""
    base = f"https://{domain}"
    seen, pages = set(), 0
    domains = {domain}               # plus wherever the site redirects (rebrands)
    for path in _PAGES:
        try:
            r = requests.get(urljoin(base + "/", path.lstrip("/")), headers=_HEADERS,
                             timeout=timeout, allow_redirects=True)
        except requests.RequestException:
            if path == "":
                try:
                    r = requests.get(f"http://{domain}", headers=_HEADERS, timeout=timeout)
                except requests.RequestException:
                    return {"email": "", "all": [], "pages": 0, "error": "site unreachable"}
            else:
                continue
        if r.status_code >= 400 or "text/html" not in r.headers.get("content-type", ""):
            continue
        pages += 1
        host = urlparse(r.url).hostname or ""
        if host:
            domains.add(host.lower().removeprefix("www."))
        seen.update(emails_on_page(r.text))
        if any(_rank(e, domains) == 0 for e in seen):
            break                    # a hiring inbox on the company's own domain: done
    ranked = sorted(seen, key=lambda e: _rank(e, domains))
    best = next((e for e in ranked if _rank(e, domains) < 9), "")
    return {"email": best, "all": ranked, "pages": pages}


def _mx_ok(email: str) -> bool:
    from .email_finder import _domain_has_mx
    return _domain_has_mx(email.rsplit("@", 1)[1])


def harvest_file(path: str, targets_csv: Optional[str] = None, segment: str = "apply,fullstack,maroc",
                 lang: str = "fr") -> Dict[str, int]:
    from .config import OUTREACH_TARGETS_CSV
    from .outreach import _FIELDS, write_template_csv
    targets_csv = targets_csv or OUTREACH_TARGETS_CSV
    write_template_csv(targets_csv)
    with open(targets_csv, newline="", encoding="utf-8-sig") as f:
        known = {(r.get("email") or "").lower() for r in csv.DictReader(f)}
    stats = {"companies": 0, "added": 0, "no_email": 0, "no_mx": 0, "known": 0, "errors": 0}
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3 or not parts[2] or line.startswith("#"):
                continue
            name, city, domain = parts[0], parts[1], parts[2].lower()
            stats["companies"] += 1
            try:
                res = harvest_domain(domain)
            except Exception as e:           # one odd site must not lose the whole run
                stats["errors"] += 1
                logger.info("harvest: %-28s error: %s", domain, e)
                continue
            email = res["email"]
            if not email:
                stats["no_email"] += 1
                logger.info("harvest: %-28s no published email (%d page(s))", domain, res["pages"])
                continue
            if email in known:
                stats["known"] += 1
                continue
            if not _mx_ok(email):
                stats["no_mx"] += 1
                continue
            known.add(email)
            row = {"name": "team", "email": email, "company": name, "role": "published inbox",
                   "website": f"https://{domain}", "lang": lang, "segment": segment, "note": "",
                   "source": f"published on {domain} ({city})"}
            # Written as found, so an interruption never loses earlier results.
            with open(targets_csv, "a", newline="", encoding="utf-8") as out:
                csv.writer(out).writerow([row[k] for k in _FIELDS])
            stats["added"] += 1
            logger.info("harvest: %-28s -> %s", domain, email)
    return stats
