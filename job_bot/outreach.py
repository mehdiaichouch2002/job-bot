"""
outreach.py
~~~~~~~~~~~
Networking outreach: short, honest emails to named people who could help
(agency tech leads / CTOs, recruiters), sent a few at a time from a target list.

Targets live in data/outreach_targets.csv (git-ignored, personal data), one row
per person: name, email, company, role, website, lang, segment, note, source.
Rows without an email but with a website are enriched from the company's own
site — and kept only if the address found is a named person, never a guess.

Every run (hourly), within the daily cap and office hours:
  1. import new CSV rows into the `outreach` table
  2. send at most OUTREACH_PER_RUN first messages, skipping anyone already in
     the contact history (contacts.py), bounced, or not MX-valid
  3. send ONE follow-up after OUTREACH_FOLLOWUP_DAYS if there was no reply
  4. never write again after a reply or an opt-out (inbox feedback marks both)

The message asks a question (do you hire / who should I talk to), carries no CV,
and uses only facts from profile.py. It ends with an opt-out line.
"""
import csv
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from . import contacts, database
from .config import (OUTREACH_ENABLED, OUTREACH_DAILY_MAX, OUTREACH_PER_RUN,
                     OUTREACH_FOLLOWUP_DAYS, OUTREACH_HOURS, OUTREACH_TARGETS_CSV)
from .database import is_dead_email
from .email_generator import _sig
from .profile import YEARS_EXPERIENCE, FACTS

logger = logging.getLogger(__name__)

_FIELDS = ["name", "email", "company", "role", "website", "lang", "segment", "note", "source"]
_FACT = {f["id"]: f for f in FACTS}


def _conn():
    # Read the path at call time (not import time) so it always follows
    # database.DB_PATH — the same file every other module writes to.
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_table():
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS outreach (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT,
            email           TEXT UNIQUE,
            company         TEXT,
            role            TEXT,
            website         TEXT,
            lang            TEXT DEFAULT 'en',
            segment         TEXT,
            note            TEXT,
            source          TEXT,
            status          TEXT DEFAULT 'queued',  -- queued|sent|followed_up|replied|skipped|no_email
            reason          TEXT,
            added_at        TEXT,
            first_sent_at   TEXT,
            followup_at     TEXT,
            subject         TEXT
        )
    """)
    conn.commit()
    conn.close()


def write_template_csv(path: str = OUTREACH_TARGETS_CSV):
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(_FIELDS)


def import_targets(path: str = OUTREACH_TARGETS_CSV) -> int:
    """Add CSV rows not yet in the table. Returns how many were added."""
    init_table()
    if not os.path.exists(path):
        write_template_csv(path)
        return 0
    added = 0
    conn = _conn()
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {k: (row.get(k) or "").strip() for k in _FIELDS}
            if not row["name"] or not (row["email"] or row["website"]):
                continue
            email = row["email"].lower() or None
            exists = conn.execute(
                "SELECT 1 FROM outreach WHERE (email = ? AND email IS NOT NULL) OR "
                "(lower(name) = lower(?) AND lower(company) = lower(?))",
                (email, row["name"], row["company"])).fetchone()
            if exists:
                continue
            conn.execute("""
                INSERT INTO outreach (name, email, company, role, website, lang, segment,
                                      note, source, status, added_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (row["name"], email, row["company"], row["role"], row["website"],
                  row["lang"] if row["lang"] in ("fr", "en") else "en", row["segment"],
                  row["note"], row["source"], "queued" if email else "no_email",
                  datetime.now().isoformat()))
            added += 1
    conn.commit()
    conn.close()
    return added


def _enrich_missing(limit: int = 2):
    """Find a named-person address on the company site for rows without one."""
    from .email_finder import find_contact, _looks_like_person
    conn = _conn()
    rows = conn.execute("SELECT * FROM outreach WHERE status = 'no_email' AND website != '' "
                        "LIMIT ?", (limit,)).fetchall()
    for r in rows:
        website = r["website"] if r["website"].startswith("http") else "https://" + r["website"]
        # find_contact trusts a URL printed in the description over any lookup,
        # so the domain searched is exactly the one on the target list.
        c = find_contact({"company": r["company"], "description": website, "language": r["lang"]})
        email = (c.get("email") or "").lower()
        if email and _looks_like_person(email.split("@")[0]):
            conn.execute("UPDATE outreach SET email = ?, status = 'queued' WHERE id = ?", (email, r["id"]))
            logger.info("Outreach: found %s for %s @ %s", email, r["name"], r["company"])
        else:
            conn.execute("UPDATE outreach SET status = 'skipped', reason = ? WHERE id = ?",
                         ("no named address on the company site", r["id"]))
    conn.commit()
    conn.close()


# ── messages ─────────────────────────────────────────────────────

def _first(name: str) -> str:
    return (name or "").split()[0] if name else ""


def is_team_inbox(t: Dict) -> bool:
    """A company-published hiring inbox (careers@, hr@…) rather than a person:
    the CSV name is "team"."""
    return (t.get("name") or "").strip().lower() in ("team", "recruitment team", "hiring team")


def wants_cv(t: Dict) -> bool:
    """segment "apply": the company explicitly invites CVs at this address, so
    the CV goes with the first message instead of being offered."""
    return "apply" in (t.get("segment") or "")


def _greeting(t: Dict) -> str:
    company = t.get("company") or ""
    if is_team_inbox(t):
        return (f"Bonjour l'équipe {company}," if t["lang"] == "fr" else f"Hello {company} team,")
    first = _first(t["name"])
    return f"Bonjour {first}," if t["lang"] == "fr" else f"Hi {first},"


def is_fullstack(t: Dict) -> bool:
    """segment "fullstack": a general web / PHP company, not a Magento shop —
    pitch the full-stack profile and attach the Full-Stack CV."""
    return "fullstack" in (t.get("segment") or "")


def first_message(t: Dict) -> Dict:
    lang, company = t["lang"], t["company"] or ""
    fact = _FACT["plp"]
    note = (t["note"] or "").strip()
    if note and not note.endswith((".", "!", "?")):
        note += "."
    seg = t["segment"] or ""
    canada = seg.startswith("canada")
    relocate = "relocate" in seg
    maroc = "maroc" in seg
    cv = wants_cv(t)
    offer = "offer" in seg      # replying to a published job offer, not a spontaneous application
    fs = is_fullstack(t)
    if lang == "fr":
        role = "développeur full-stack PHP / Laravel / React" if fs else "développeur Magento 2 / Adobe Commerce"
        role_short = "développeur web full-stack" if fs else "développeur Magento"
        body = (
            f"{_greeting(t)}\n\n"
            f"Je suis {role} basé à Fès, avec {YEARS_EXPERIENCE} ans d'expérience sur les plateformes "
            f"e-commerce de Carhartt WIP, Anita et Edwin Europe. Par exemple : {fact['fr']}."
            + (f" {note}" if note else "") + "\n\n"
            + ("Je prépare ma relocalisation au Canada et je peux démarrer à distance en attendant. "
               if canada else "")
            + ("Je suis mobile partout au Maroc, ouvert au télétravail comme au présentiel, et "
               "disponible immédiatement. " if maroc else "")
            + ("Je vous adresse ma candidature pour ce poste : mon CV est joint, et je peux partager "
               "des exemples de code si c'est utile."
               if cv and offer else
               f"Je vous adresse ma candidature spontanée pour un poste de {role_short} chez "
               f"{company} : mon CV est joint, et je peux partager des exemples de code si c'est utile."
               if cv else
               f"{company or 'Votre équipe'} recrute-t-elle des {role_short}s en ce moment, ou "
               "y a-t-il quelqu'un à qui vous me conseilleriez d'écrire ? Je peux vous envoyer mon CV "
               "et des exemples de code si c'est utile.")
            + "\n\n"
            + ("" if cv else
               "Si vous préférez ne pas recevoir d'autre message de ma part, dites-le-moi simplement.\n\n")
            + f"Cordialement,\n{_sig('fr')}"
        )
        title = "Développeur Full-Stack PHP / Laravel / React" if fs else "Développeur Magento 2"
        subject = (f"Candidature — {title}" if cv and offer else
                   f"Candidature spontanée — {title}" if cv else f"{title} — question rapide")
    else:
        role = "full-stack PHP / Laravel / React developer" if fs else "Magento 2 / Adobe Commerce developer"
        role_short = "full-stack developer" if fs else "Magento developer"
        body = (
            f"{_greeting(t)}\n\n"
            f"I'm a {role} based in Fès, Morocco, with {YEARS_EXPERIENCE} years of experience on the "
            f"e-commerce platforms of Carhartt WIP, Anita and Edwin Europe. Among other things, I "
            f"{fact['en']}."
            + (f" {note}" if note else "") + "\n\n"
            + ("I'm relocating to Canada and can start remotely in the meantime. " if canada else "")
            # Said up front: an employer abroad must know it would sponsor a work visa.
            + ("I'm open to relocating and would need a work visa (sponsorship); I can also start "
               "remotely from Morocco. " if relocate else "")
            + ("I'd like to apply for this role: my CV is attached, and I'm happy to share code samples."
               if cv and offer else
               f"I'd like to be considered for {role_short} roles at {company}: my CV is attached, "
               "and I'm happy to share code samples."
               if cv else
               f"Is {company or 'your team'} hiring {role_short}s at the moment, or is there "
               "someone you'd suggest I talk to? Happy to send my CV and code samples if useful.")
            + "\n\n"
            + ("" if cv else "If you'd rather not hear from me again, just say so and I won't write again.\n\n")
            + f"Best regards,\n{_sig('en')}"
        )
        title = "Full-stack PHP / Laravel / React developer" if fs else "Magento 2 developer"
        subject = (f"Application — {title}" if cv and offer else
                   f"Open application — {title}" if cv else f"{title} — quick question")
    return {"subject": subject, "body": body}


def followup_message(t: Dict) -> Dict:
    date = (t["first_sent_at"] or "")[:10]
    if t["lang"] == "fr":
        body = (f"{_greeting(t)}\n\nUne courte relance de mon message du {date} : "
                "y a-t-il quelqu'un dans votre équipe à qui je devrais parler au sujet des postes "
                "Magento ? Je n'écrirai pas davantage sans réponse.\n\n"
                f"Merci et bonne journée,\n{_sig('fr')}")
    else:
        body = (f"{_greeting(t)}\n\nA short follow-up on my note from {date}: is there anyone on your "
                "side I should talk to about Magento roles? I won't write again after this one.\n\n"
                f"Thanks,\n{_sig('en')}")
    return {"subject": "Re: " + (t["subject"] or "Magento 2 developer"), "body": body}


def _validate(t: Dict, msg: Dict) -> List[str]:
    """Same honesty checks as job messages: figures and skill claims."""
    from .drafter import validate
    ev = {"covered": [], "partial": [], "gaps": [], "blocking": []}
    return validate(msg["body"], ev, "application", t["lang"], set(),
                    {"title": "", "company": t["company"] or ""})


# ── the run ──────────────────────────────────────────────────────

def _office_hours(now: datetime) -> bool:
    start, end = (int(x) for x in OUTREACH_HOURS.split("-"))
    return now.weekday() < 5 and start <= now.hour < end


def _sent_today(conn, now: datetime) -> int:
    today = now.date().isoformat()
    return conn.execute(
        "SELECT (SELECT COUNT(*) FROM outreach WHERE substr(first_sent_at,1,10) = ?) + "
        "(SELECT COUNT(*) FROM outreach WHERE substr(followup_at,1,10) = ?)", (today, today)
    ).fetchone()[0]


def _replied(email: str) -> bool:
    p = contacts.find_prior({"contact_email": email, "company": ""})
    return bool(p and p["match"] == "person" and p.get("outcome") not in ("silence", None))


def run_outreach(dry_run: bool = False, now: Optional[datetime] = None) -> Dict:
    from .email_sender import send_email
    from .email_finder import _domain_has_mx
    now = now or datetime.now()
    result = {"imported": 0, "sent": 0, "followups": 0, "skipped": 0}
    if not OUTREACH_ENABLED:
        return result
    init_table()
    result["imported"] = import_targets()
    if not _office_hours(now):
        logger.info("Outreach: outside office hours (%s, Mon–Fri) — nothing sent", OUTREACH_HOURS)
        return result
    _enrich_missing()

    conn = _conn()
    # Stop sequences for people who answered (inbox feedback updates contacts).
    for r in conn.execute("SELECT id, email FROM outreach WHERE status IN ('sent','followed_up')").fetchall():
        if _replied(r["email"]):
            conn.execute("UPDATE outreach SET status = 'replied' WHERE id = ?", (r["id"],))
    conn.commit()

    budget = min(OUTREACH_PER_RUN, OUTREACH_DAILY_MAX - _sent_today(conn, now))

    def _send(t, msg, kind):
        problems = _validate(t, msg)
        if problems:
            logger.warning("Outreach %s to %s blocked by honesty check: %s", kind, t["email"], problems)
            return False
        cv = None
        if kind == "message" and wants_cv(t):
            from .packet import _pick_cv
            cv = _pick_cv({"title": "Full-stack developer" if is_fullstack(t) else "Magento 2 developer"},
                          t["lang"])
        return send_email(t["email"], msg["subject"], msg["body"], pdf_path=cv, dry_run=dry_run)

    # Follow-ups first: they complete a conversation already started.
    cutoff = (now - timedelta(days=OUTREACH_FOLLOWUP_DAYS)).isoformat()
    for t in conn.execute("SELECT * FROM outreach WHERE status = 'sent' AND first_sent_at <= ? "
                          "ORDER BY first_sent_at", (cutoff,)).fetchall():
        if budget <= 0:
            break
        t = dict(t)
        if _replied(t["email"]) or is_dead_email(t["email"]):
            continue
        msg = followup_message(t)
        if _send(t, msg, "follow-up"):
            budget -= 1
            result["followups"] += 1
            if not dry_run:
                conn.execute("UPDATE outreach SET status = 'followed_up', followup_at = ? WHERE id = ?",
                             (now.isoformat(), t["id"]))
                conn.commit()   # release the write lock before contacts opens its own connection
                contacts.record_contact({"contact_email": t["email"], "contact_name": t["name"],
                                         "company": t["company"], "title": "outreach follow-up"},
                                        msg["subject"])

    # Best first: an inbox the company set up for hiring, then Moroccan companies
    # (no work-permit question), then the rest in the order they were added.
    def _priority(t):
        local = (t["email"] or "").split("@", 1)[0].lower()
        hiring = local.startswith(("recrutement", "careers", "career", "jobs", "job", "hr", "rh",
                                   "talent", "emploi", "candidature", "work", "join"))
        return (0 if hiring else 1, 0 if "maroc" in (t["segment"] or "") else 1, t["id"])
    queued = sorted(conn.execute("SELECT * FROM outreach WHERE status = 'queued'").fetchall(), key=_priority)
    for t in queued:
        if budget <= 0:
            break
        t = dict(t)
        prior = contacts.find_prior({"contact_email": t["email"], "company": t["company"]})
        reason = None
        if prior and prior["match"] == "person":
            reason = "already contacted: " + contacts.prior_summary(prior)
        elif prior and prior.get("outcome") == "rejection":
            reason = "company declined before"
        # Targets are addresses the company itself published (or a person's
        # published address), never guesses — so a generic contact@ is fine here;
        # only deliverability (MX) and past bounces are checked.
        elif is_dead_email(t["email"]) or not _domain_has_mx(t["email"].rsplit("@", 1)[1]):
            reason = "address not deliverable (MX / bounced)"
        if reason:
            conn.execute("UPDATE outreach SET status = 'skipped', reason = ? WHERE id = ?", (reason, t["id"]))
            conn.commit()
            result["skipped"] += 1
            logger.info("Outreach: skipped %s <%s> — %s", t["name"], t["email"], reason)
            continue
        msg = first_message(t)
        if _send(t, msg, "message"):
            budget -= 1
            result["sent"] += 1
            if not dry_run:
                conn.execute("UPDATE outreach SET status = 'sent', first_sent_at = ?, subject = ? "
                             "WHERE id = ?", (now.isoformat(), msg["subject"], t["id"]))
                conn.commit()   # release the write lock before contacts opens its own connection
                contacts.record_contact({"contact_email": t["email"], "contact_name": t["name"],
                                         "company": t["company"], "title": "outreach"}, msg["subject"])
    conn.close()
    logger.info("Outreach: %d new message(s), %d follow-up(s), %d skipped, %d target(s) imported",
                result["sent"], result["followups"], result["skipped"], result["imported"])
    return result


def status_counts() -> Dict[str, int]:
    init_table()
    conn = _conn()
    rows = conn.execute("SELECT status, COUNT(*) FROM outreach GROUP BY status").fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}
