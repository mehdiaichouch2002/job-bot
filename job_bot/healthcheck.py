"""
healthcheck.py
~~~~~~~~~~~~~~
`python run.py --check` — prove the bot can really work before trusting it:

  1. credentials present in .env (not the .env.example placeholders)
  2. the four CV PDFs are found
  3. the database opens and the contact history is loaded
  4. Groq answers, for both the drafting and the extraction model
  5. Gmail SMTP login works, and a sample application is sent TO YOURSELF
     (never to a company) — exactly what a recruiter would receive, CV attached
  6. Gmail IMAP login works (reply / bounce tracking)

Prints PASS / FAIL per step with the reason, returns True only if all passed.
"""
import imaplib
import os
import smtplib

from .config import (EMAIL_ADDRESS, EMAIL_PASSWORD, GROQ_API_KEY, SMTP_SERVER, SMTP_PORT,
                     CV_PDF_MAGENTO_EN, CV_PDF_MAGENTO_FR, CV_PDF_FULLSTACK_EN,
                     CV_PDF_FULLSTACK_FR)

_SAMPLE_JOB = {
    "id": "healthcheck", "title": "Magento 2 Developer (Remote)", "company": "Sample Shop",
    "location": "Remote - Worldwide", "country": "", "language": "en", "source": "healthcheck",
    "url": "https://example.com", "contact_email": EMAIL_ADDRESS, "contact_name": "Mehdi",
    "description": (
        "Requirements: - 3+ years of experience with Magento 2 - Strong PHP 8 skills "
        "- MySQL, Elasticsearch and Redis - Git. Nice to have: - Hyvä themes - Symfony. "
        "We offer: fully remote, work from anywhere."
    ),
}


def _line(ok: bool, name: str, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def run_check(send_sample: bool = True) -> bool:
    print("\n=== Job bot health check ===\n")
    results = []

    # 1. credentials
    results.append(_line(bool(EMAIL_PASSWORD), "EMAIL_PASSWORD in .env",
                         "" if EMAIL_PASSWORD else "missing or still the placeholder — create a Gmail "
                         "App Password at https://myaccount.google.com/apppasswords"))
    results.append(_line(bool(GROQ_API_KEY), "GROQ_API_KEY in .env",
                         "" if GROQ_API_KEY else "empty — free key at https://console.groq.com "
                         "(the bot still runs without it, on regex + templates)"))

    # 2. CVs
    cvs = [CV_PDF_MAGENTO_EN, CV_PDF_MAGENTO_FR, CV_PDF_FULLSTACK_EN, CV_PDF_FULLSTACK_FR]
    missing = [os.path.basename(p) for p in cvs if not os.path.isfile(p)]
    results.append(_line(not missing, "CV PDFs found", ", ".join(missing) or
                         ", ".join(os.path.basename(p) for p in cvs)))

    # 3. database
    try:
        from .database import init_db, get_stats
        init_db()
        s = get_stats()
        results.append(_line(True, "Database", f"{s['total']} jobs, {s.get('contacts', 0)} contacts in history"))
    except Exception as e:
        results.append(_line(False, "Database", str(e)))

    # 4. Groq
    if GROQ_API_KEY:
        from .email_generator import call_llm, MODEL, EXTRACT_MODEL
        for label, model, json_mode in (("drafting", MODEL, False), ("extraction", EXTRACT_MODEL, True)):
            try:
                out = call_llm("Reply with a JSON object {\"ok\": true}." if json_mode else "Reply with: ok",
                               "ping", model=model, temperature=0, json_mode=json_mode, max_tokens=20)
                results.append(_line(bool(out), f"Groq {label} model ({model})", (out or "").strip()[:40]))
            except Exception as e:
                results.append(_line(False, f"Groq {label} model ({model})", str(e)[:200]))

    # 5. Gmail SMTP + sample application to yourself
    smtp_ok = False
    if EMAIL_PASSWORD:
        try:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as s:
                s.ehlo()
                s.starttls()
                s.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            smtp_ok = True
            results.append(_line(True, "Gmail SMTP login", EMAIL_ADDRESS))
        except smtplib.SMTPAuthenticationError:
            results.append(_line(False, "Gmail SMTP login", "rejected: use a Gmail App Password "
                                 "(16 letters), not your normal password; 2-step verification must be on"))
        except Exception as e:
            results.append(_line(False, "Gmail SMTP login", str(e)))
    if smtp_ok and send_sample:
        try:
            from .extractor import extract
            from .matcher import evaluate
            from .drafter import draft_application_email
            from .packet import _pick_cv
            from .email_sender import send_email
            job = dict(_SAMPLE_JOB)
            ev = evaluate(job, extract(job, use_llm=bool(GROQ_API_KEY)))
            draft = draft_application_email(job, ev)
            cv = _pick_cv(job, "en")
            ok = send_email(EMAIL_ADDRESS, f"[TEST bot] {draft['subject']}", draft["body"], pdf_path=cv)
            results.append(_line(ok, "Sample application sent to yourself",
                                 f"{EMAIL_ADDRESS}, verdict {ev['category']}, draft by {draft['source']}, "
                                 f"CV {os.path.basename(cv)} — check your inbox"))
        except Exception as e:
            results.append(_line(False, "Sample application sent to yourself", str(e)))

    # 6. Gmail IMAP
    if EMAIL_PASSWORD:
        try:
            m = imaplib.IMAP4_SSL("imap.gmail.com", timeout=30)
            m.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            m.logout()
            results.append(_line(True, "Gmail IMAP login (reply / bounce tracking)"))
        except Exception as e:
            results.append(_line(False, "Gmail IMAP login (reply / bounce tracking)",
                                 f"{e} — enable IMAP in Gmail settings if it is off"))

    ok = all(results)
    print(f"\n{'ALL GOOD — the bot can send.' if ok else 'NOT READY — fix the FAIL lines above.'}\n")
    return ok
