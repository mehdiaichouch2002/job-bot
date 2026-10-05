import os
import sqlite3
import logging
from datetime import datetime, timedelta
from .config import DB_PATH

logger = logging.getLogger(__name__)


# Columns added after the original schema shipped — applied as lightweight
# migrations so existing databases keep working.
_MIGRATIONS = {
    "fit_score":     "ALTER TABLE jobs ADD COLUMN fit_score INTEGER DEFAULT 0",
    "fit_reasons":   "ALTER TABLE jobs ADD COLUMN fit_reasons TEXT",
    "packet_path":   "ALTER TABLE jobs ADD COLUMN packet_path TEXT",
    "digest_sent_at":"ALTER TABLE jobs ADD COLUMN digest_sent_at TEXT",
    "applied_via":   "ALTER TABLE jobs ADD COLUMN applied_via TEXT",
    # 1 = apply on LinkedIn (Easy Apply), 0 = external company site, NULL = unknown
    "easy_apply":    "ALTER TABLE jobs ADD COLUMN easy_apply INTEGER",
    # named contact the application is addressed to (HR / chef de projet / …)
    "contact_name":  "ALTER TABLE jobs ADD COLUMN contact_name TEXT",
    "contact_title": "ALTER TABLE jobs ADD COLUMN contact_title TEXT",
    # inbox feedback loop
    "bounced":       "ALTER TABLE jobs ADD COLUMN bounced INTEGER DEFAULT 0",
    "replied":       "ALTER TABLE jobs ADD COLUMN replied INTEGER DEFAULT 0",
    "reply_at":      "ALTER TABLE jobs ADD COLUMN reply_at TEXT",
    "reply_type":    "ALTER TABLE jobs ADD COLUMN reply_type TEXT",  # positive|rejection|auto
    # matcher.py verdict: BON_MATCH | CONTACT_RESEAU | A_EVITER, full breakdown as JSON
    "category":      "ALTER TABLE jobs ADD COLUMN category TEXT",
    "evaluation":    "ALTER TABLE jobs ADD COLUMN evaluation TEXT",
    "prior_contact": "ALTER TABLE jobs ADD COLUMN prior_contact TEXT",
}


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id            TEXT PRIMARY KEY,
            title         TEXT NOT NULL,
            company       TEXT,
            location      TEXT,
            country       TEXT,
            description   TEXT,
            url           TEXT,
            contact_email TEXT,
            source        TEXT,
            language      TEXT DEFAULT 'en',
            found_at      TEXT NOT NULL,
            status        TEXT DEFAULT 'found',
            email_sent_at TEXT,
            email_subject TEXT,
            email_body    TEXT
        )
    """)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    for col, ddl in _MIGRATIONS.items():
        if col not in existing:
            conn.execute(ddl)
            logger.info("DB migration: added column %s", col)
    # Addresses that bounced — never send to them again.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dead_emails (
            email   TEXT PRIMARY KEY,
            seen_at TEXT NOT NULL
        )
    """)
    # Everyone ever contacted, per person and company (see contacts.py).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            email          TEXT PRIMARY KEY,
            name           TEXT,
            company        TEXT,
            company_key    TEXT,
            domain         TEXT,
            first_at       TEXT,
            last_at        TEXT,
            count          INTEGER DEFAULT 1,
            last_job_title TEXT,
            last_subject   TEXT,
            outcome        TEXT DEFAULT 'silence'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contacts_domain ON contacts(domain)")
    conn.commit()
    from .contacts import backfill_if_empty
    seeded = backfill_if_empty(conn)
    if seeded:
        logger.info("Contacts history seeded from %d past sends", seeded)
    conn.close()
    logger.info("Database ready at %s", DB_PATH)


def job_exists(job_id: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,))
    exists = cur.fetchone() is not None
    conn.close()
    return exists


def save_job(job: dict):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT OR IGNORE INTO jobs
            (id, title, company, location, country, description, url,
             contact_email, source, language, found_at, status,
             fit_score, fit_reasons)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'found', ?, ?)
    """, (
        job["id"],
        job.get("title", ""),
        job.get("company", ""),
        job.get("location", ""),
        job.get("country", ""),
        job.get("description", ""),
        job.get("url", ""),
        job.get("contact_email", ""),
        job.get("source", ""),
        job.get("language", "en"),
        datetime.now().isoformat(),
        int(job.get("fit_score", 0)),
        ", ".join(job.get("fit_reasons", [])) if isinstance(job.get("fit_reasons"), list) else (job.get("fit_reasons") or ""),
    ))
    conn.commit()
    conn.close()


def mark_email_sent(job_id: str, subject: str, body: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        UPDATE jobs
        SET status = 'applied', applied_via = 'email',
            email_sent_at = ?, email_subject = ?, email_body = ?
        WHERE id = ?
    """, (datetime.now().isoformat(), subject, body, job_id))
    conn.commit()
    conn.close()


def mark_packet_ready(job_id: str, packet_path: str):
    """A ready-to-submit application packet was generated; awaits manual send."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        UPDATE jobs SET status = 'packet_ready', packet_path = ? WHERE id = ?
    """, (packet_path, job_id))
    conn.commit()
    conn.close()


def set_evaluation(job_id: str, category: str, score: int, evaluation: str,
                   prior_contact: str = None, status: str = None):
    """Store the matcher verdict. status='avoid' for À ÉVITER jobs."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        UPDATE jobs SET category = ?, fit_score = ?, evaluation = ?, prior_contact = ?,
               status = COALESCE(?, status)
        WHERE id = ?
    """, (category, score, evaluation, prior_contact, status, job_id))
    conn.commit()
    conn.close()


def get_digest_candidates(limit: int = 40, max_age_days: int = 7) -> list:
    """Jobs for the digest: evaluated (so legacy rows never reappear), not yet
    digested, found within the last `max_age_days`. Includes À ÉVITER rows so
    the digest can say why they were skipped."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
    cur = conn.execute("""
        SELECT * FROM jobs
        WHERE category IS NOT NULL
          AND status IN ('packet_ready', 'applied', 'avoid')
          AND (digest_sent_at IS NULL OR digest_sent_at = '')
          AND found_at >= ?
        ORDER BY CASE category WHEN 'BON_MATCH' THEN 0 WHEN 'CONTACT_RESEAU' THEN 1 ELSE 2 END,
                 fit_score DESC, found_at DESC
        LIMIT ?
    """, (cutoff, limit))
    jobs = [dict(row) for row in cur.fetchall()]
    conn.close()
    return jobs


def set_easy_apply(job_id: str, value):
    """Store Easy Apply status: 1 (LinkedIn apply), 0 (external), or None (unknown)."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE jobs SET easy_apply = ? WHERE id = ?",
                 (None if value is None else int(bool(value)), job_id))
    conn.commit()
    conn.close()


def mark_digested(job_ids: list):
    if not job_ids:
        return
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now().isoformat()
    conn.executemany("UPDATE jobs SET digest_sent_at = ? WHERE id = ?",
                     [(now, jid) for jid in job_ids])
    conn.commit()
    conn.close()


def acted_company_titles() -> set:
    """Normalized (company|title) keys already emailed or packeted — so we don't
    re-apply to the same role reposted under a new listing id (agency spam)."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("""
        SELECT company, title FROM jobs WHERE status IN ('applied', 'packet_ready')
    """)
    keys = {_norm_key(c, t) for c, t in cur.fetchall()}
    conn.close()
    return keys


def _norm_key(company: str, title: str) -> str:
    import re
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
    return f"{norm(company)}|{norm(title)}"


def get_sent_emails() -> set:
    """Distinct lowercased addresses we've emailed — applications and outreach —
    so the inbox feedback loop recognises replies and bounces from all of them."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("""
        SELECT DISTINCT lower(contact_email) FROM jobs
        WHERE email_sent_at IS NOT NULL
          AND contact_email != '' AND contact_email IS NOT NULL
        UNION
        SELECT email FROM contacts
    """)
    emails = {row[0] for row in cur.fetchall()}
    conn.close()
    return emails


def add_dead_email(email: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR IGNORE INTO dead_emails (email, seen_at) VALUES (?, ?)",
                 (email.lower(), datetime.now().isoformat()))
    conn.commit()
    conn.close()


def is_dead_email(email: str) -> bool:
    if not email:
        return False
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT 1 FROM dead_emails WHERE email = ?", (email.lower(),))
    dead = cur.fetchone() is not None
    conn.close()
    return dead


def mark_email_bounced(email: str):
    """Record a bounce: blacklist the address and flag its job(s)."""
    add_dead_email(email)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE jobs SET bounced = 1 WHERE lower(contact_email) = ?", (email.lower(),))
    conn.execute("UPDATE contacts SET outcome = 'bounce' WHERE email = ?", (email.lower(),))
    conn.commit()
    conn.close()


def mark_email_replied(email: str, reply_type: str = None):
    """Record that a contact we emailed replied to us (with classification)."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE jobs SET replied = 1, reply_at = ?, reply_type = ? "
        "WHERE lower(contact_email) = ? AND replied = 0",
        (datetime.now().isoformat(), reply_type, email.lower())
    )
    conn.execute("UPDATE contacts SET outcome = ? WHERE email = ?",
                 (reply_type or "reply", email.lower()))
    conn.commit()
    conn.close()


def get_report(limit: int = 30) -> list:
    """Recent scored activity for the --report CLI view. Excludes the legacy
    fit-0 sends made before scoring existed, so the report shows real matches."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute("""
        SELECT title, company, fit_score, status, applied_via, contact_email,
               packet_path, url, found_at, category, evaluation, prior_contact
        FROM jobs
        WHERE category IS NOT NULL AND status IN ('applied', 'packet_ready', 'avoid')
        ORDER BY found_at DESC LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def update_contact_email(job_id: str, email: str, name: str = None, title: str = None):
    """Persist a contact email (and named person, if known) found after save."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE jobs SET contact_email = ?, contact_name = ?, contact_title = ? WHERE id = ?",
        (email, name, title, job_id)
    )
    conn.commit()
    conn.close()


def get_stats() -> dict:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    stats = {}
    for key, query in [
        ("total",         "SELECT COUNT(*) FROM jobs"),
        ("applied",       "SELECT COUNT(*) FROM jobs WHERE status = 'applied'"),
        ("emailed",       "SELECT COUNT(*) FROM jobs WHERE status = 'applied' AND applied_via = 'email'"),
        ("packets_ready", "SELECT COUNT(*) FROM jobs WHERE status = 'packet_ready'"),
        ("with_email",    "SELECT COUNT(*) FROM jobs WHERE contact_email != '' AND contact_email IS NOT NULL"),
        ("manual_review", "SELECT COUNT(*) FROM jobs WHERE status = 'found' AND (contact_email = '' OR contact_email IS NULL)"),
        ("bon_match",     "SELECT COUNT(*) FROM jobs WHERE category = 'BON_MATCH'"),
        ("contact_reseau","SELECT COUNT(*) FROM jobs WHERE category = 'CONTACT_RESEAU'"),
        ("a_eviter",      "SELECT COUNT(*) FROM jobs WHERE category = 'A_EVITER'"),
        ("contacts",      "SELECT COUNT(*) FROM contacts"),
        ("replied",       "SELECT COUNT(*) FROM jobs WHERE replied = 1"),
        ("positive_replies", "SELECT COUNT(*) FROM jobs WHERE reply_type = 'positive'"),
        ("bounced",       "SELECT COUNT(*) FROM jobs WHERE bounced = 1"),
        ("dead_addrs",    "SELECT COUNT(*) FROM dead_emails"),
    ]:
        cur.execute(query)
        stats[key] = cur.fetchone()[0]
    conn.close()
    return stats
