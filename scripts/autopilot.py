"""Autopilot (started at Mehdi's explicit request, 2026-09-30): spontaneous applications, one company
at a time.

For each "Company | City/Country | domain" line of the queue files (in order):
  harvest the address the company PUBLISHES on its own site (harvest.py: owned domain, no guessing),
  check MX, skip anyone contacted before (contacts / outreach tables) or who declined,
  build the message with outreach.first_message, run the honesty check, send with the CV.
Limits: DAILY_CAP sends per calendar day (counted in contacts), sending hours (AUTOPILOT_HOURS, local),
a pause between sends. Progress: data/autopilot_log.csv (one row per company), data/autopilot_state.txt
(done domains).

Two modes:
    python scripts/autopilot.py               # local loop: runs until the queues are empty; every 3 h
                                              # it also runs the job bot pipeline (inbox, boards, digest)
    python scripts/autopilot.py --batch 12    # cloud (GitHub Actions, 2026-10-05): send at most 12, stop
                                              # after AUTOPILOT_BUDGET_SEC, then exit; the workflow runs
                                              # the pipeline itself with `run.py --once`
    add --dry to send nothing.

Only ONE autopilot may run at a time (local OR cloud), otherwise two runners can mail the same company.
"""
import csv, os, random, re, sqlite3, sys, time
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
import logging  # noqa: E402
logging.basicConfig(level=logging.WARNING)
from job_bot import contacts                                              # noqa: E402
from job_bot.config import (CV_PDF_MAGENTO_EN, CV_PDF_FULLSTACK_FR,       # noqa: E402
                            CV_PDF_MAGENTO_FR)
from job_bot.email_finder import _domain_has_mx                          # noqa: E402
from job_bot.email_sender import send_email                              # noqa: E402
from job_bot.harvest import harvest_domain                               # noqa: E402
from job_bot.outreach import first_message, _validate                    # noqa: E402

DRY = "--dry" in sys.argv
BATCH = int(sys.argv[sys.argv.index("--batch") + 1]) if "--batch" in sys.argv else 0
DAILY_CAP = int(os.getenv("AUTOPILOT_DAILY_CAP", "90"))
# sending window (local hours); AUTOPILOT_HOURS="0-24" lifts it (Mehdi asked to start at night, 2026-10-05)
HOURS = tuple(int(h) for h in os.getenv("AUTOPILOT_HOURS", "8-20").split("-"))
# cloud batch: stop starting new companies after this many seconds (the job has a hard timeout)
BUDGET_SEC = int(os.getenv("AUTOPILOT_BUDGET_SEC", "600"))
# queue file, segment, language, CV, note — files may be appended to while the autopilot runs.
# Morocco is GMT+1 all year: same hour as France in winter, one hour behind in summer.
REMOTE_FR = ("Je peux travailler en télétravail depuis le Maroc, sur vos horaires (même fuseau que la France "
             "à une heure près)")
QUEUES = [
    ("data/companies_autopilot_ma.txt", "apply,fullstack,maroc", "fr", CV_PDF_FULLSTACK_FR, ""),
    ("data/companies_hyva_all.txt", "apply,relocate", "en", CV_PDF_MAGENTO_EN, ""),
    ("data/companies_autopilot_fr_magento.txt", "apply", "fr", CV_PDF_MAGENTO_FR, REMOTE_FR),
    ("data/companies_autopilot_fr_laravel.txt", "apply,fullstack", "fr", CV_PDF_FULLSTACK_FR, REMOTE_FR),
    ("data/companies_autopilot_abroad.txt", "apply,relocate", "en", CV_PDF_MAGENTO_EN, ""),
    # Quebec agencies: French message; no claim about French certification (see CLAUDE.md, Canada pitch)
    ("data/companies_autopilot_canada.txt", "apply,fullstack", "fr", CV_PDF_FULLSTACK_FR,
     "Je suis ouvert à une relocalisation au Canada avec un permis de travail, et je peux travailler à "
     "distance depuis le Maroc en attendant"),
]
STATE = "data/autopilot_state.txt"
LOG = "data/autopilot_log.csv"
GENERIC = {"info", "contact", "hello", "office", "jobs", "job", "careers", "career", "hr", "recrutement", "rh",
           "admin", "mail", "team", "bonjour", "salam", "welcome", "hi", "support", "sales", "work", "join",
           "talent", "talents", "emploi", "candidature", "hallo", "kontakt", "bureau", "biuro", "hej", "post"}


def log(domain, company, email, result):
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "domain", "company", "email", "result"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), domain, company, email, result])
    print(f"{datetime.now():%m-%d %H:%M} {domain:<32} {email:<38} {result}", flush=True)


def _db():
    return sqlite3.connect("data/jobs.db", timeout=30)


def sent_today() -> int:
    c = _db()
    n = c.execute("SELECT COUNT(*) FROM contacts WHERE substr(last_at,1,10)=?",
                  (datetime.now().strftime("%Y-%m-%d"),)).fetchone()[0]
    c.close()
    return n


def known_email(email: str) -> bool:
    c = _db()
    a = c.execute("SELECT 1 FROM contacts WHERE lower(email)=?", (email,)).fetchone()
    b = c.execute("SELECT 1 FROM outreach WHERE lower(email)=?", (email,)).fetchone()
    c.close()
    return bool(a or b)


def in_window() -> bool:
    return HOURS[0] <= datetime.now().hour < HOURS[1] and sent_today() < DAILY_CAP


def wait_for_window():
    while not in_window():
        time.sleep(600)


def pending():
    """(company, place, domain, seg, lang, cv) not done yet, re-read each time so new lines are picked up."""
    done = set(open(STATE, encoding="utf-8").read().split()) if os.path.exists(STATE) else set()
    for path, seg, lang, cv, note in QUEUES:
        if not os.path.exists(path):
            continue
        for line in open(path, encoding="utf-8"):
            p = [x.strip() for x in line.split("|")]
            if line.startswith("#") or len(p) < 3 or not p[2]:
                continue
            if p[2].lower() not in done:
                return p[0], p[1], p[2].lower(), seg, lang, cv, note
    return None


def process(company, domain, seg, lang, cv, note=""):
    res = harvest_domain(domain)
    email = res.get("email", "")
    if not email:
        return email, "no published email"
    if known_email(email):
        return email, "already contacted"
    prior = contacts.find_prior({"contact_email": email, "company": company})
    if prior and (prior["match"] == "person" or prior.get("outcome") == "rejection"):
        return email, "contacted before: " + contacts.prior_summary(prior)
    if not _domain_has_mx(email.rsplit("@", 1)[1]):
        return email, "no MX"
    local = email.split("@")[0]
    name = "team"
    # only a "first.last@" / "first_last@" address is a person; a single word (paris@, info@) is not
    m = re.fullmatch(r"([a-z]{3,})[._-][a-z]{2,}", local)
    if m and local not in GENERIC and m.group(1) not in GENERIC:
        name = m.group(1).capitalize()          # a person's published address: greet by first name
    t = {"name": name, "email": email, "company": company, "lang": lang, "segment": seg,
         "note": note, "role": "", "website": f"https://{domain}"}
    msg = first_message(t)
    problems = _validate(t, msg)
    if problems:
        return email, f"honesty check: {problems}"
    if DRY:
        return email, "would send"
    ok = send_email(email, msg["subject"], msg["body"], pdf_path=cv)
    if ok:
        contacts.record_contact({"contact_email": email, "contact_name": name, "company": company,
                                 "title": "spontaneous application"}, msg["subject"])
    return email, "SENT" if ok else "send failed"


def _step(nxt) -> str:
    company, place, domain, seg, lang, cv, note = nxt
    try:
        email, result = process(company, domain, seg, lang, cv, note)
    except Exception as e:
        email, result = "", f"error: {e}"
    if DRY:                             # a dry run must not mark companies as done
        print(f"[dry] {domain:<32} {email:<38} {result}", flush=True)
        return result
    log(domain, company, email, result)
    with open(STATE, "a", encoding="utf-8") as f:
        f.write(domain + "\n")
    return result


def batch(max_sends: int):
    """Cloud mode: a bounded run that always exits, so the workflow can save state."""
    start, sent = time.time(), 0
    while sent < max_sends and time.time() - start < BUDGET_SEC:
        if not DRY and not in_window():
            print("outside sending hours or daily cap reached", flush=True)
            break
        nxt = pending()
        if not nxt:
            print("queues empty", flush=True)
            break
        if DRY:                         # pending() would return the same company forever
            _step(nxt)
            break
        if _step(nxt) == "SENT":
            sent += 1
            time.sleep(random.randint(20, 40))
    print(f"batch done: {sent} sent in {int(time.time() - start)} s", flush=True)


def main():
    last_inbox = 0.0
    while True:
        nxt = pending()
        if not nxt:
            if DRY:
                break
            time.sleep(900)            # queues empty: wait for new lines
            continue
        if DRY:                         # a dry run checks one company and stops
            _step(nxt)
            break
        wait_for_window()
        if time.time() - last_inbox > 3 * 3600:
            # the job bot's own pipeline (inbox feedback, job boards, follow-ups, digest), every 3 h
            try:
                from job_bot.main import run_pipeline
                run_pipeline(dry_run=False)
                print("job bot run done", flush=True)
            except Exception as e:  # the bot must never stop the autopilot
                print("job bot error:", e, flush=True)
            last_inbox = time.time()
        if _step(nxt) == "SENT":
            time.sleep(random.randint(45, 90))
    print("queues empty", flush=True)


if __name__ == "__main__":
    batch(BATCH) if BATCH else main()
