"""
hireability.py
~~~~~~~~~~~~~~
Can this employer actually hire Mehdi — who lives in Fès, Morocco?

This is the filter that was missing for the first four months. The bot happily
applied to onsite roles in Munich, Amsterdam and Copenhagen that were never
going to hire a non-resident without sponsorship, and to `Alternant` /
`Werkstudent` postings that legally require enrolment at a local school. 62% of
sends scored below 55 fit and a large share were structurally impossible.

A job is only worth Mehdi's time if one of these is true:

  morocco    — the job is in Morocco (no friction at all)
  remote     — genuinely remote and NOT geo-locked to a region that excludes him
  freelance  — contract/freelance work that can be invoiced from Morocco
  sponsor    — the employer explicitly offers visa sponsorship / relocation
  canada     — the Canada relocation track (handled by the caller)

Everything else is a structural no, however good the keyword match looks.

Pure regex — no API calls, runs on every job for free.
"""
import re
from typing import Dict, List, Tuple

# ── Positive signals ─────────────────────────────────────────────
_REMOTE_RE = re.compile(
    r"\b(fully|100%|100 %|completely)?\s*remote\b|\bwork from home\b|\bwfh\b"
    r"|t[ée]l[ée]travail|home ?office|\banywhere\b|\bdistributed team\b",
    re.I,
)
_FREELANCE_RE = re.compile(
    r"\b(freelance|freelancer|contractor|contract role|independent contractor"
    r"|b2b contract|self-?employed|mission|prestataire|ind[ée]pendant)\b",
    re.I,
)
_SPONSOR_RE = re.compile(
    r"\b(visa sponsorship|sponsor(?:ship)? (?:is )?(?:available|provided|offered)"
    r"|we sponsor|relocation (?:package|support|assistance|bonus)"
    r"|will sponsor|happy to sponsor|work permit (?:support|assistance|provided))\b",
    re.I,
)
_MOROCCO_RE = re.compile(
    r"\b(morocco|maroc|marokko|casablanca|rabat|f[eè]s|fez|marrakech|marrakesh"
    r"|tangier|tanger|agadir|kenitra|t[ée]touan|oujda)\b",
    re.I,
)

# ── Blocking signals ─────────────────────────────────────────────
# Remote, but locked to a region Mehdi is not in.
_GEO_LOCKED_RE = re.compile(
    r"\b(?:"
    r"(?:must (?:be|reside|live)|based|located|residing|reside|authorized to work"
    r"|work authorization|eligible to work|legally authori[sz]ed)"
    r"[^.\n]{0,40}\b(?:in |within )?(?:the )?"
    r"(?:us|u\.s\.?|usa|united states|uk|united kingdom|eu|e\.u\.?|eea|europe(?:an union)?"
    r"|germany|deutschland|netherlands|france|canada|australia|india)\b"
    r"|\b(?:us|usa|uk|eu|eea|canada|us-based|uk-based|eu-based)[ -]only\b"
    r"|\bonly[^.\n]{0,20}\b(?:us|uk|eu|eea|canadian|american)\s+(?:citizens?|residents?|applicants?)\b"
    r"|\bno (?:visa )?sponsorship\b|\bcannot sponsor\b|\bnot able to sponsor\b"
    r"|\bunable to (?:provide )?sponsor(?:ship)?\b"
    r"|\bexisting (?:right to work|work authorization)\b"
    r"|\bright to work in\b"
    r")",
    re.I,
)
# Onsite / hybrid required — needs physical presence.
_ONSITE_RE = re.compile(
    r"\b(on-?site only|fully on-?site|no remote|remote is not|not a remote"
    r"|hybrid (?:role|position|model|working|setup)?[^.\n]{0,25}(?:required|expected|mandatory)"
    r"|\d\s*days?\s*(?:per|a|/)\s*week\s*(?:in|at)\s*(?:the\s*)?office"
    r"|in-?office\s*\d\s*days|pr[ée]sentiel)\b",
    re.I,
)
# Legally requires local student status — Mehdi cannot take these.
_STUDENT_CONTRACT_RE = re.compile(
    r"\b(alternan(?:ce|t|te)|apprenti(?:ssage|e)?|contrat pro(?:fessionnalisation)?"
    r"|werkstudent|praktikum|ausbildung|duales studium|stage(?:iaire)?"
    r"|internship|intern\b|trainee|graduate programme|working student)\b",
    re.I,
)
# Fluency in a language Mehdi does not have (AR native, EN B2, FR B1).
_LANG_BARRIER_RE = re.compile(
    r"\b(?:fluent|native|verhandlungssicher|muttersprach\w*|c1|c2|business[- ]level)"
    r"[^.\n]{0,30}\b(german|deutsch|dutch|nederlands|danish|swedish|norwegian|finnish"
    r"|polish|italian|spanish|portuguese)\b"
    r"|\b(german|dutch|danish|swedish|polish)\s+(?:language\s+)?(?:skills?\s+)?"
    r"(?:is|are)?\s*(?:required|mandatory|a must)\b"
    r"|\bsehr gute deutschkenntnisse\b|\bdeutsch\w* (?:in wort und schrift|erforderlich)\b",
    re.I,
)


def _txt(job: Dict) -> str:
    return f"{job.get('title', '')}\n{job.get('description', '')}"


def _loc(job: Dict) -> str:
    return f"{job.get('location', '')} {job.get('country', '')}"


def classify(job: Dict) -> Tuple[bool, str, List[str]]:
    """Return (hireable, channel, reasons).

    `channel` is one of morocco / remote / freelance / sponsor / blocked, and
    describes HOW this employer could realistically take him on.
    """
    text = _txt(job)
    loc = _loc(job)
    both = f"{text}\n{loc}"
    reasons: List[str] = []

    # 1. Morocco — no immigration friction whatsoever, always hireable.
    if _MOROCCO_RE.search(loc) or job.get("country", "").upper() == "MA":
        return True, "morocco", ["in Morocco — no visa friction"]

    # 2. Hard blocks. Student contracts are legally impossible for him.
    if _STUDENT_CONTRACT_RE.search(job.get("title", "")):
        return False, "blocked", ["apprenticeship/internship — requires local student status"]

    if _LANG_BARRIER_RE.search(text):
        return False, "blocked", ["requires fluency in a language he doesn't have"]

    # 3. Explicit sponsorship beats everything below — they've said they'll do it.
    if _SPONSOR_RE.search(text):
        reasons.append("employer offers sponsorship/relocation")
        return True, "sponsor", reasons

    # 4. Geo-locked postings: "remote (US only)", "must have EU work authorization".
    if _GEO_LOCKED_RE.search(both):
        return False, "blocked", ["remote but geo-locked / no sponsorship"]

    # 5. Onsite or hybrid outside Morocco → needs to already live there.
    if _ONSITE_RE.search(text):
        return False, "blocked", ["on-site/hybrid abroad — needs existing residency"]

    # 6. Freelance/contract — invoiceable from Morocco, no work permit needed.
    if _FREELANCE_RE.search(text):
        reasons.append("freelance/contract — invoiceable from Morocco")
        return True, "freelance", reasons

    # 7. Genuinely remote and not geo-locked.
    if _REMOTE_RE.search(both):
        reasons.append("remote, no geographic restriction stated")
        return True, "remote", reasons

    # 8. Nothing said either way. An unstated location is usually an onsite local
    #    hire — that assumption is what produced 1,775 unanswered emails, so the
    #    default is now "no" rather than "try anyway".
    return False, "blocked", ["no remote/sponsorship signal — likely local-only hire"]


def channel_label(channel: str) -> str:
    return {
        "morocco": "🇲🇦 Morocco",
        "remote": "🌍 Remote",
        "freelance": "💼 Freelance",
        "sponsor": "✈️ Sponsors visa",
        "canada": "🇨🇦 Canada track",
        "blocked": "⛔ Not hireable",
    }.get(channel, channel)
