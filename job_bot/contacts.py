"""
contacts.py
~~~~~~~~~~~
Who has already been contacted — per person (email) and per company — so a
new message never starts from zero with someone we already wrote to.

The table is seeded once from every email ever sent (jobs.email_sent_at),
so the ~1,775 sends made in Mar–Jul 2026 count as prior contact.
Outcomes are kept up to date by the inbox feedback loop (database.py).
"""
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Optional

from .config import DB_PATH

# A person contacted this recently, or a company that said no, is not
# auto-emailed again — the draft goes to the digest for a human decision.
RECONTACT_DAYS = 30

_FREEMAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com",
             "icloud.com", "proton.me", "protonmail.com", "gmx.de", "gmx.net",
             "yahoo.fr", "hotmail.fr", "orange.fr", "free.fr", "laposte.net"}

_COMPANY_NOISE = re.compile(
    r"\b(gmbh|ag|sa|sas|sarl|srl|bv|b\.v|ltd|limited|llc|inc|corp|co|plc|group|groupe"
    r"|holding|aps|as|oy|ab|spa|s\.a|s\.a\.s|kg|ug|e\.?k)\b"
)


def company_key(name: str) -> str:
    s = (name or "").lower()
    s = _COMPANY_NOISE.sub(" ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _domain(email: str) -> str:
    d = (email or "").rsplit("@", 1)[-1].lower() if "@" in (email or "") else ""
    return "" if d in _FREEMAIL else d


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def backfill_if_empty(conn=None) -> int:
    """Seed from the send history. Called by database.init_db()."""
    own = conn is None
    conn = conn or _conn()
    conn.row_factory = sqlite3.Row
    if conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]:
        if own:
            conn.close()
        return 0
    rows = conn.execute("""
        SELECT lower(contact_email) AS email, contact_name, company, title, email_subject,
               email_sent_at, bounced, replied, reply_type
        FROM jobs
        -- email_sent_at, not applied_via: 1,403 early sends predate that column.
        WHERE email_sent_at IS NOT NULL AND contact_email IS NOT NULL AND contact_email != ''
        ORDER BY email_sent_at ASC
    """).fetchall()
    n = 0
    for r in rows:
        _upsert(conn, r["email"], r["contact_name"], r["company"], r["title"],
                r["email_subject"], r["email_sent_at"],
                _outcome(r["bounced"], r["replied"], r["reply_type"]))
        n += 1
    conn.commit()
    if own:
        conn.close()
    return n


def _outcome(bounced, replied, reply_type) -> str:
    if bounced:
        return "bounce"
    if replied:
        return reply_type or "reply"
    return "silence"


def _upsert(conn, email, name, company, job_title, subject, at, outcome="silence"):
    existing = conn.execute("SELECT count FROM contacts WHERE email = ?", (email,)).fetchone()
    if existing:
        conn.execute("""
            UPDATE contacts SET last_at = ?, count = count + 1, last_job_title = ?,
                   last_subject = ?, name = COALESCE(?, name), outcome = ?,
                   company = COALESCE(NULLIF(?, ''), company),
                   company_key = COALESCE(NULLIF(?, ''), company_key)
            WHERE email = ?
        """, (at, job_title, subject, name or None, outcome, company, company_key(company), email))
    else:
        conn.execute("""
            INSERT INTO contacts (email, name, company, company_key, domain, first_at, last_at,
                                  count, last_job_title, last_subject, outcome)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        """, (email, name or None, company, company_key(company), _domain(email), at, at,
              job_title, subject, outcome))


def record_contact(job: Dict, subject: str):
    """Call after a message was actually sent."""
    email = (job.get("contact_email") or "").lower()
    if not email:
        return
    conn = _conn()
    _upsert(conn, email, job.get("contact_name"), job.get("company", ""), job.get("title", ""),
            subject, datetime.now().isoformat())
    conn.commit()
    conn.close()


def find_prior(job: Dict) -> Optional[Dict]:
    """Most relevant earlier contact: same person first, then same company
    (by name or by email domain). None if never contacted."""
    email = (job.get("contact_email") or "").lower()
    ckey = company_key(job.get("company", ""))
    dom = _domain(email)
    conn = _conn()
    row, match = None, None
    if email:
        row = conn.execute("SELECT * FROM contacts WHERE email = ?", (email,)).fetchone()
        match = "person" if row else None
    if not row and ckey:
        row = conn.execute("SELECT * FROM contacts WHERE company_key = ? ORDER BY last_at DESC LIMIT 1",
                           (ckey,)).fetchone()
        match = "company" if row else None
    if not row and dom:
        row = conn.execute("SELECT * FROM contacts WHERE domain = ? ORDER BY last_at DESC LIMIT 1",
                           (dom,)).fetchone()
        match = "company" if row else None
    conn.close()
    if not row:
        return None
    prior = dict(row)
    prior["match"] = match
    return prior


def may_auto_send(prior: Optional[Dict]) -> (bool, str):
    """Whether a new automatic email is acceptable given the history."""
    if not prior:
        return True, ""
    if prior.get("outcome") == "rejection":
        return False, f"{prior.get('company') or 'cette entreprise'} a déjà refusé une candidature"
    if prior.get("outcome") == "bounce" and prior["match"] == "person":
        return False, "cette adresse a déjà rebondi"
    last = prior.get("last_at") or ""
    try:
        recent = datetime.fromisoformat(last) > datetime.now() - timedelta(days=RECONTACT_DAYS)
    except ValueError:
        recent = False
    if recent and prior["match"] == "person":
        return False, f"même personne contactée il y a moins de {RECONTACT_DAYS} jours"
    return True, ""


def prior_summary(prior: Optional[Dict]) -> str:
    if not prior:
        return ""
    who = prior.get("name") or prior.get("email")
    when = (prior.get("last_at") or "")[:10]
    outcome = {"silence": "sans réponse", "bounce": "adresse invalide", "rejection": "refus",
               "positive": "réponse positive", "auto": "accusé de réception automatique"
               }.get(prior.get("outcome"), prior.get("outcome") or "")
    scope = "même personne" if prior["match"] == "person" else "même entreprise"
    n = prior.get("count") or 1
    generic = _ROLE_LOCAL.match((prior.get("email") or "").split("@")[0])
    return (f"Déjà sollicité ({scope}) : {who}, le {when} pour « {prior.get('last_job_title') or '?'} »"
            f" — {outcome}" + (f", {n} message(s) au total" if n > 1 else "")
            + (" (adresse générique, peut-être jamais lue)" if generic else ""))


_ROLE_LOCAL = re.compile(r"^(careers?|jobs?|hr|rh|recrutement|recruiting|recruitment|talent"
                         r"|apply|contact|info|hello|hiring|emploi|candidature)s?$", re.I)
