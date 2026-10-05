"""
digest.py
~~~~~~~~~
Email Mehdi what the bot found, grouped by verdict:
  BON MATCH       apply (packet ready, or already emailed)
  CONTACT RÉSEAU  short honest message ready, gap named
  À ÉVITER        one line each on why it was skipped
Each entry shows what is covered, the gaps, and any earlier contact.
"""
import json
import logging
from typing import List, Dict

from .config import DIGEST_TO, EMAIL_ADDRESS
from .email_sender import send_email
from .matcher import BON_MATCH, CONTACT_RESEAU, A_EVITER, summary_lines

logger = logging.getLogger(__name__)

_MAX_AVOID_LINES = 15


def _eval(job: Dict) -> Dict:
    try:
        return json.loads(job.get("evaluation") or "{}")
    except json.JSONDecodeError:
        return {}


def _block(job: Dict) -> str:
    ev = _eval(job)
    head = f"{job.get('title', '')} @ {job.get('company', '')}"
    if job.get("status") == "applied" and job.get("applied_via") == "email":
        head += f"   ✉ emailed to {job.get('contact_email', '')}"
    parts = [head, f"    {job.get('location', '') or 'n/a'} · {job.get('source', '')}"]
    if ev:
        parts += [f"    {line}" for line in summary_lines(ev)[1:]]
    if job.get("prior_contact"):
        parts.append(f"    ⚠ {job['prior_contact']}")
    parts.append(f"    apply: {job.get('url', '')}")
    if job.get("packet_path"):
        parts.append(f"    packet: {job['packet_path']}")
    return "\n".join(parts)


def build_digest_body(jobs: List[Dict]) -> str:
    good = [j for j in jobs if j.get("category") == BON_MATCH]
    net = [j for j in jobs if j.get("category") == CONTACT_RESEAU]
    avoid = [j for j in jobs if j.get("category") == A_EVITER]
    out = [f"{len(good)} bon(s) match · {len(net)} contact(s) réseau · {len(avoid)} à éviter", "=" * 60]
    if good:
        out += ["", "BON MATCH — candidature complète prête (cover_letter.txt + CV)", ""]
        out += ["\n\n".join(_block(j) for j in good)]
    if net:
        out += ["", "=" * 60, "CONTACT RÉSEAU — écart bloquant, message court et honnête prêt (network_message.txt)", ""]
        out += ["\n\n".join(_block(j) for j in net)]
    if avoid:
        out += ["", "=" * 60, "À ÉVITER — aucun message généré", ""]
        for j in avoid[:_MAX_AVOID_LINES]:
            reasons = "; ".join(_eval(j).get("reasons", [])) or "?"
            out.append(f"- {j.get('title', '')} @ {j.get('company', '')}: {reasons}")
        if len(avoid) > _MAX_AVOID_LINES:
            out.append(f"  … et {len(avoid) - _MAX_AVOID_LINES} autre(s)")
    return "\n".join(out)


def send_digest(jobs: List[Dict], dry_run: bool = False) -> bool:
    """Email the grouped shortlist. Returns True if sent. Nothing is sent when
    the run found no job worth acting on (À ÉVITER alone is not news)."""
    actionable = [j for j in jobs if j.get("category") in (BON_MATCH, CONTACT_RESEAU)]
    if not actionable:
        logger.info("Digest: nothing actionable to report.")
        return False
    good = sum(1 for j in jobs if j.get("category") == BON_MATCH)
    subject = f"[Job Bot] {good} bon(s) match, {len(actionable) - good} contact(s) réseau"
    ok = send_email(DIGEST_TO or EMAIL_ADDRESS, subject, build_digest_body(jobs),
                    pdf_path=None, dry_run=dry_run)
    if ok:
        logger.info("Digest sent to %s (%d jobs)", DIGEST_TO or EMAIL_ADDRESS, len(jobs))
    return ok
