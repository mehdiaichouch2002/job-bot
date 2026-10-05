"""
feedback.py
~~~~~~~~~~~
Close the loop: read the bot's own Gmail inbox (IMAP, same free account) to learn
what happened to the applications it sent.

  • Bounces  → blacklist the dead address so it's never used again + flag the job.
  • Replies  → a company/recruiter we emailed wrote back → mark the job "replied".

This is what turns the bot from fire-and-forget into fire-measure-improve, and it
gives a real response-rate number. 100% free — no MCP, no third-party service.
"""
import re
import email
import imaplib
import logging
from datetime import datetime, timedelta
from email.utils import parseaddr

from .config import EMAIL_ADDRESS, EMAIL_PASSWORD
from .database import get_sent_emails, mark_email_bounced, mark_email_replied
from .email_generator import classify_reply

logger = logging.getLogger(__name__)

_IMAP_HOST = "imap.gmail.com"
_EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
_BOUNCE_SENDERS = ("mailer-daemon", "postmaster", "mail-daemon")
_BOUNCE_SUBJECTS = (
    "delivery status notification", "undelivered", "delivery failure",
    "returned mail", "mail delivery failed", "failure notice",
    "undeliverable", "delivery has failed",
)


def _body_text(msg) -> str:
    """Best-effort plain-text extraction from an email message."""
    parts = []
    if msg.is_multipart():
        for p in msg.walk():
            if p.get_content_type() in ("text/plain", "message/delivery-status", "text/rfc822-headers"):
                try:
                    parts.append(p.get_payload(decode=True).decode(errors="ignore"))
                except Exception:
                    pass
    else:
        try:
            parts.append(msg.get_payload(decode=True).decode(errors="ignore"))
        except Exception:
            pass
    return " ".join(parts)


def process_inbox(days: int = 14) -> dict:
    """Scan the inbox for bounces and replies to our applications."""
    result = {"bounces": 0, "replies": 0, "checked": 0, "error": None}
    if not EMAIL_PASSWORD:
        result["error"] = "EMAIL_PASSWORD not set"
        return result

    sent = get_sent_emails()
    if not sent:
        return result

    try:
        M = imaplib.IMAP4_SSL(_IMAP_HOST, timeout=30)
        M.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        M.select("INBOX", readonly=True)
        since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")

        # --- Bounces: targeted search (few messages), fetch their bodies ---
        seen_bounced = set()
        for sender in _BOUNCE_SENDERS:
            typ, data = M.search(None, f'(SINCE {since}) FROM "{sender}"')
            for num in (data[0].split() if data and data[0] else []):
                typ, md = M.fetch(num, "(RFC822)")
                if not md or not md[0]:
                    continue
                msg = email.message_from_bytes(md[0][1])
                hits = {e.lower() for e in _EMAIL_RE.findall(_body_text(msg))} & sent
                for e in hits - seen_bounced:
                    mark_email_bounced(e)
                    seen_bounced.add(e)
                    result["bounces"] += 1
                    logger.info("Bounce detected → blacklisted %s", e)

        # --- Replies: batched header fetch to find them, then classify each ---
        typ, data = M.search(None, f'(SINCE {since})')
        ids = data[0].split() if data and data[0] else []
        result["checked"] = len(ids)
        seen_replied = set()
        if ids:
            ids = ids[-800:]
            typ, msgs = M.fetch(b",".join(ids), "(BODY.PEEK[HEADER.FIELDS (FROM)])")
            # map each reply message id → sender, keeping only who we emailed
            reply_ids = []
            for i, part in enumerate(msgs):
                if not isinstance(part, tuple) or not part[1]:
                    continue
                from_addr = parseaddr(email.message_from_bytes(part[1]).get("From", ""))[1].lower()
                if from_addr in sent and from_addr not in seen_replied:
                    seen_replied.add(from_addr)
                    # part[0] looks like b'123 (BODY[...' → the message id is the first token
                    mid = part[0].split()[0]
                    reply_ids.append((mid, from_addr))

            for mid, from_addr in reply_ids:
                typ, md = M.fetch(mid, "(RFC822)")
                rtype = "unknown"
                if md and md[0]:
                    msg = email.message_from_bytes(md[0][1])
                    rtype = classify_reply(msg.get("Subject", ""), _body_text(msg))
                mark_email_replied(from_addr, rtype)
                result["replies"] += 1
                result.setdefault("by_type", {}).setdefault(rtype, 0)
                result["by_type"][rtype] += 1
                logger.info("Reply from %s → %s", from_addr, rtype)

        M.logout()
    except Exception as e:
        result["error"] = str(e)
        logger.warning("Inbox feedback skipped: %s", e)
    return result
