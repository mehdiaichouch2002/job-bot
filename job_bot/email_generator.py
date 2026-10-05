"""
email_generator.py
~~~~~~~~~~~~~~~~~~
Shared LLM plumbing (Groq, free tier) and message helpers: signature, greeting,
Canada pitch, reply classification. The messages themselves are written by
drafter.py, which works from matcher.py's evaluation and profile.py's facts.
"""
import logging
from typing import Optional, Tuple
from .config import (GROQ_API_KEY, GROQ_MODEL, GROQ_EXTRACT_MODEL, CV_DATA,
                     CANADA_PITCH, CANADA_PITCH_QUEBEC)

logger = logging.getLogger(__name__)

MODEL = GROQ_MODEL
EXTRACT_MODEL = GROQ_EXTRACT_MODEL

_client = None


def _get_client():
    global _client
    if _client is None and GROQ_API_KEY:
        from groq import Groq
        # One retry only: on the free tier a rate limit means "fall back now",
        # not "wait a minute" inside a 12-minute CI job.
        _client = Groq(api_key=GROQ_API_KEY, max_retries=1, timeout=30)
    return _client


def llm_available() -> bool:
    return _get_client() is not None


def call_llm(system: str, user: str, model: str = None, temperature: float = 0.7,
             json_mode: bool = False, max_tokens: int = 650) -> Optional[str]:
    """One chat completion, or None when no API key is configured."""
    client = _get_client()
    if not client:
        return None
    kwargs = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(
        model=model or MODEL,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        **kwargs,
    )
    return resp.choices[0].message.content


_BANNED_EN = (
    "passionate, excited, thrilled, leverage, synergy, dynamic, dedicated, motivated, driven, "
    "seamless, robust, cutting-edge, fast-paced, results-driven, detail-oriented, "
    "'wealth of experience', 'proven track record', 'hit the ground running', "
    "'game-changer', 'I am confident that', 'great fit', 'perfect fit', 'reaching out', "
    "'I came across', 'delve', 'spearheaded', 'furthermore', 'moreover', \"in today's\", "
    "'I am writing to express my interest', 'I hope this email finds you well', "
    "'Please find attached', 'I would be a great fit', bullet points, numbered lists, "
    "overusing em-dashes, exclamation marks"
)

_BANNED_FR = (
    "passionné, enthousiaste, motivé, dynamique, proactif, rigoureux, incontournable, "
    "'Je me permets de', 'N'hésitez pas', 'Je suis convaincu', 'fort de mon expérience', "
    "'à la pointe', 'dans un monde', 'force de proposition', listes à puces, "
    "'Veuillez trouver ci-joint', points d'exclamation, tirets cadratins en excès"
)


def classify_reply(subject: str, body: str) -> str:
    """Classify a reply to a job application: 'positive', 'rejection', or 'auto'.
    Uses the free Groq LLM; returns 'unknown' if unavailable."""
    client = _get_client()
    if not client:
        return "unknown"
    system = (
        "Classify a reply to a job application into EXACTLY one lowercase word:\n"
        "positive = human interest, interview request, screening call, or next steps\n"
        "rejection = application declined / not moving forward\n"
        "auto = automated acknowledgement ('we received your application'), receipt, or newsletter\n"
        "Output ONLY the single word, nothing else."
    )
    user = f"Subject: {subject}\n\n{body[:1500]}"
    try:
        raw = (call_llm(system, user, temperature=0) or "").strip().lower()
        for label in ("positive", "rejection", "auto"):
            if label in raw:
                return label
    except Exception as e:
        logger.debug("Reply classification failed: %s", e)
    return "unknown"


def _recipient_note(job: dict, lang: str) -> str:
    """Instruction telling the model to greet a known contact by first name."""
    name = (job.get("contact_name") or "").strip()
    if not name:
        return ""
    first = name.split()[0]
    title = (job.get("contact_title") or "").strip()
    role = f" ({title})" if title else ""
    if lang == "fr":
        return (f"Le destinataire est {name}{role}. Commence par « Bonjour {first}, » "
                f"et adresse-toi directement à cette personne.\n\n")
    return (f"The recipient is {name}{role}. Open with \"Hi {first},\" and address "
            f"this person directly.\n\n")


def _canada_pitch(job: dict):
    """Return the right relocation pitch for a job, or None for the intl track."""
    if job.get("market") != "canada":
        return None
    return CANADA_PITCH_QUEBEC if job.get("quebec") else CANADA_PITCH


def _parse_response(text: str, job: dict, lang: str) -> Tuple[str, str]:
    subject = ""
    body_lines = []
    in_body = False
    subject_line_idx = -1

    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        for prefix in ("SUBJECT:", "Subject:", "OBJET:", "Objet:"):
            if stripped.startswith(prefix):
                subject = stripped[len(prefix):].strip()
                subject_line_idx = i
                break
        if stripped in ("BODY:", "Body:", "CORPS:", "Corps:"):
            in_body = True
            continue
        if in_body:
            body_lines.append(line)

    body = "\n".join(body_lines).strip()

    if not subject:
        subject = (f"Candidature — {job['title']}" if lang == "fr"
                   else f"Application — {job['title']}")

    if not body:
        start = subject_line_idx + 1 if subject_line_idx >= 0 else 0
        body = "\n".join(lines[start:]).strip()

    return subject, body


_SIG_EN = "Mehdi Aichouch\n{email} | {phone}\n{linkedin} | {portfolio}"
_SIG_FR = "Mehdi Aichouch\n{email} | {phone}\n{linkedin} | {portfolio}"


def _sig(lang: str) -> str:
    tpl = _SIG_EN if lang == "en" else _SIG_FR
    sig = tpl.format(
        email=CV_DATA["email"], phone=CV_DATA["phone"],
        linkedin=CV_DATA["linkedin"], portfolio=CV_DATA["portfolio"],
    )
    return sig.replace(" | \n", "\n")   # CONTACT_PHONE not set: no dangling separator
