import os
import sys
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.utils import formataddr, formatdate, make_msgid
from .config import SMTP_SERVER, SMTP_PORT, EMAIL_ADDRESS, EMAIL_PASSWORD, SENDER_NAME

logger = logging.getLogger(__name__)

# The genuine SMTP class, captured at import. Tests swap smtplib.SMTP for a fake.
_REAL_SMTP = smtplib.SMTP


def _test_run_hitting_real_gmail() -> bool:
    """True when code runs under unittest but would talk to the real Gmail.

    On 2026-09-23 a test run sent 18 real duplicate applications to two
    companies: tests imported the bot before their safety settings applied.
    This check sits at the last possible point, so no test can ever send a
    real email again — whatever the import order or .env contents."""
    return "unittest" in sys.modules and smtplib.SMTP is _REAL_SMTP


def send_email(to_email: str, subject: str, body: str,
               pdf_path: str = None, dry_run: bool = False) -> bool:
    """Send an email via Gmail SMTP with optional PDF attachment. Returns True on success."""
    if dry_run:
        attachment_note = f" + {os.path.basename(pdf_path)}" if pdf_path else ""
        logger.info("[DRY RUN] To: %s | Subject: %s%s", to_email, subject, attachment_note)
        logger.info("[DRY RUN] Body:\n%s\n", body)
        return True

    if _test_run_hitting_real_gmail():
        logger.error("Refusing to send a real email from a test run (to %s)", to_email)
        return False

    if not EMAIL_PASSWORD:
        logger.error("EMAIL_PASSWORD not set — cannot send email")
        return False

    try:
        msg = MIMEMultipart()
        # A named sender plus Date and Message-ID: missing ones are classic spam-score
        # penalties, and none were set before 2026-09-25.
        msg["From"] = formataddr((SENDER_NAME, EMAIL_ADDRESS))
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=EMAIL_ADDRESS.rsplit("@", 1)[-1])
        msg.attach(MIMEText(body, "plain", "utf-8"))

        if pdf_path and os.path.isfile(pdf_path):
            with open(pdf_path, "rb") as f:
                pdf = MIMEApplication(f.read(), _subtype="pdf")
                pdf.add_header(
                    "Content-Disposition", "attachment",
                    filename=os.path.basename(pdf_path)
                )
                msg.attach(pdf)
            logger.info("Attaching CV: %s", os.path.basename(pdf_path))
        elif pdf_path:
            logger.warning("PDF not found, sending without attachment: %s", pdf_path)

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, to_email, msg.as_string())

        logger.info("Email sent → %s | %s", to_email, subject)
        return True

    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP authentication failed — check EMAIL_PASSWORD (use App Password)")
        return False
    except Exception as e:
        logger.error("Failed to send email to %s: %s", to_email, e)
        return False
