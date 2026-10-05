"""
packet.py
~~~~~~~~~
Build a ready-to-use folder for a job the matcher kept (BON_MATCH or
CONTACT_RESEAU). What goes in depends on the verdict:

  BON_MATCH       cover_letter.txt  (+ email_draft.txt when a draft exists)
  CONTACT_RESEAU  network_message.txt — short, names the gap, asks for a better-suited role
  both            application.md    verdict, criteria covered / partial / gaps,
                                    prior contact, apply link, what to do
                  CV_<lang>.pdf     the role-matched résumé

applications/<YYYY-MM-DD>/<company>__<title>__<id>/
"""
import os
import re
import shutil
import logging
from datetime import date

from .config import (
    APPLICATIONS_DIR,
    CV_PDF_MAGENTO_EN, CV_PDF_MAGENTO_FR,
    CV_PDF_FULLSTACK_EN, CV_PDF_FULLSTACK_FR,
)
from .matcher import BON_MATCH, LABEL_FR, summary_lines
from .contacts import prior_summary

logger = logging.getLogger(__name__)


def _slug(text: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return (s[:maxlen] or "untitled").strip("-")


def _pick_cv(job: dict, lang: str) -> str:
    """Pick the résumé that mirrors the posting: Magento roles get the Magento CV,
    everything else gets the Full-Stack CV. Canadian jobs follow the same rule
    (the C16 angle lives in the cover letter, not the CV)."""
    title = (job.get("title") or "").lower()
    text = f"{title} {(job.get('description') or '').lower()}"
    is_magento = bool(
        re.search(r"\bmagento\b|adobe commerce", title)
        or re.search(r"\bmagento\b|adobe commerce", text)
    )
    if is_magento:
        return CV_PDF_MAGENTO_FR if lang == "fr" else CV_PDF_MAGENTO_EN
    return CV_PDF_FULLSTACK_FR if lang == "fr" else CV_PDF_FULLSTACK_EN


def _write(folder: str, name: str, text: str):
    with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
        f.write(text)


def build_packet(job: dict, ev: dict, draft: dict = None, prior: dict = None) -> str:
    """Generate the packet folder for an evaluated job. Returns the folder path."""
    from .drafter import draft_cover_letter, draft_network_message   # lazy: import order
    lang = job.get("language", "en")
    company = job.get("company", "") or "company"
    title = job.get("title", "") or "role"
    good = ev["category"] == BON_MATCH

    # Short id suffix keeps distinct postings (same company+title) from colliding.
    uid = (job.get("id") or "")[:6]
    folder = os.path.join(
        APPLICATIONS_DIR,
        date.today().isoformat(),
        f"{_slug(company, 30)}__{_slug(title, 40)}__{uid}",
    )
    os.makedirs(folder, exist_ok=True)
    cv_name = "CV_FR.pdf" if lang == "fr" else "CV_EN.pdf"

    # 1. The message the verdict calls for
    if good:
        _write(folder, "cover_letter.txt", draft_cover_letter(job, ev)["text"])
        if draft and draft.get("kind") == "application":
            _write(folder, "email_draft.txt", f"Subject: {draft['subject']}\n\n{draft['body']}")
        how = ("1. Open the apply link below.\n"
               "2. Paste `cover_letter.txt` into the cover-letter field.\n"
               f"3. Attach `{cv_name}` and submit.\n")
    else:
        draft = draft or draft_network_message(job, ev, prior)
        _write(folder, "network_message.txt", f"Subject: {draft['subject']}\n\n{draft['body']}")
        how = ("This is NOT a full application: the role has a clear gap (see above).\n"
               "1. Find the recruiter or hiring manager (LinkedIn, the company's team page).\n"
               "2. Send `network_message.txt`: it states the gap openly and asks for a better-suited role.\n"
               f"3. Attach `{cv_name}` only if they ask for it.\n")

    # 2. Brief: verdict, criteria, history, next step
    prior_line = prior_summary(prior)
    brief = (
        f"# {title} — {company}\n\n"
        f"**{LABEL_FR[ev['category']]}**\n\n"
        + "".join(f"- {line}\n" for line in summary_lines(ev))
        + (f"\n> ⚠ {prior_line}\n" if prior_line else "")
        + f"\n- **Location:** {job.get('location', 'n/a')} ({job.get('country', '?')})\n"
        f"- **Source:** {job.get('source', 'n/a')} · **Language:** {lang.upper()}\n"
        f"- **Apply here:** {job.get('url', 'n/a')}\n\n"
        f"## What to do\n{how}"
    )
    _write(folder, "application.md", brief)

    # 3. Copy the role-matched CV in (Magento vs Full-Stack)
    src = _pick_cv(job, lang)
    if not os.path.isfile(src):
        logger.warning("CV not found, packet has no CV: %s", src)
    else:
        try:
            shutil.copy(src, os.path.join(folder, cv_name))
        except Exception as e:
            logger.warning("Could not copy CV into packet: %s", e)

    logger.info("Packet ready [%s, %s]: %s", lang.upper(), ev["category"], folder)
    return folder
