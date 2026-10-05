"""
matcher.py
~~~~~~~~~~
Criteria (extractor.py) + profile (profile.py) -> an honest evaluation.

Pure Python, no API calls: the same ad always gets the same verdict, and every
verdict can be traced to a rule below.

Per required skill:
  core skill in profile          -> covered, weight 1.0
  secondary skill in profile     -> partial, weight 0.4
  transferable (taxonomy)        -> partial, weight = coefficient (never 1.0)
  absent + non-transferable      -> BLOCKING gap
  unknown technology ("?term")   -> BLOCKING gap (conservative: can't vouch for it)
Nice-to-have skills follow the same credits at half weight and are NEVER blocking.

Other blocking gaps (only with a quote from the ad as evidence):
  experience  -> min_years > profile years, or senior/lead title with no year
                 figure <= profile years
  language    -> explicit requirement >= 2 CEFR steps above the profile level
  location    -> employer cannot hire from Morocco (hireability.py)

Categories (rules, not a score threshold):
  A_EVITER        location/visa blocker, or >= 2 blocking gaps,
                  or 1 blocking gap with low coverage, or coverage < 40 %
  CONTACT_RESEAU  exactly 1 blocking gap, or no blocker but coverage < 75 %
  BON_MATCH       no blocker and >= 75 % of required skills covered
The numeric score only orders jobs inside a category; it never decides one.
"""
from typing import Dict, List, Optional

from . import taxonomy
from .profile import (YEARS_EXPERIENCE, SKILL_WEIGHT, LANGUAGES, LANGUAGE_SCALE,
                      level_of)

BON_MATCH = "BON_MATCH"
CONTACT_RESEAU = "CONTACT_RESEAU"
A_EVITER = "A_EVITER"

GOOD_COVERAGE = 0.75
MIN_COVERAGE = 0.40
NICE_WEIGHT = 0.5

LABEL_FR = {BON_MATCH: "Bon match", CONTACT_RESEAU: "Contact réseau", A_EVITER: "À éviter"}

_LANG_FR = {"fr": "français", "en": "anglais", "de": "allemand", "nl": "néerlandais",
            "es": "espagnol", "it": "italien", "ar": "arabe"}


def _assess_skill(skill: str) -> Dict:
    """How the profile stands against one skill."""
    if skill.startswith("?"):
        return {"skill": skill[1:], "label": skill[1:], "status": "unknown", "credit": 0.0}
    lvl = level_of(skill)
    if lvl:
        return {"skill": skill, "label": taxonomy.label(skill),
                "status": "exact" if lvl == "core" else "secondary",
                "credit": SKILL_WEIGHT[lvl]}
    if skill in taxonomy.TRANSFERABLE:
        via, coef = taxonomy.TRANSFERABLE[skill]
        return {"skill": skill, "label": taxonomy.label(skill), "status": "transferable",
                "via": via, "via_label": taxonomy.label(via), "credit": coef}
    return {"skill": skill, "label": taxonomy.label(skill), "status": "absent", "credit": 0.0}


def _lang_steps(required: str, have: Optional[str]) -> int:
    if have is None:
        return len(LANGUAGE_SCALE)          # language not spoken at all
    return LANGUAGE_SCALE.index(required) - LANGUAGE_SCALE.index(have)


def evaluate(job: Dict, crit: Dict, hireable: bool = True,
             hire_reasons: Optional[List[str]] = None) -> Dict:
    covered, partial, gaps, blocking = [], [], [], []
    ev = crit.get("evidence", {})

    # ── location / work authorisation ──
    if not hireable:
        blocking.append({"type": "location",
                         "detail": (hire_reasons or ["employeur ne peut pas recruter depuis le Maroc"])[0],
                         "evidence": job.get("location", "")})

    # ── skills ──
    req_credits, nice_credits = [], []
    for required, pool, credits in ((True, crit.get("required", []), req_credits),
                                    (False, crit.get("nice_to_have", []), nice_credits)):
        for skill in pool:
            a = _assess_skill(skill)
            a["required"] = required
            credits.append(a["credit"])
            if a["status"] == "exact":
                covered.append(a)
            elif a["status"] in ("secondary", "transferable"):
                partial.append(a)
            elif required:
                a["blocking"] = True
                blocking.append({"type": "skill", "skill": a["skill"], "label": a["label"],
                                 "detail": (f"{a['label']} requis, absent du profil"
                                            + (" et hors référentiel" if a["status"] == "unknown"
                                               else " et non transférable")),
                                 "evidence": ""})
                gaps.append(a)
            else:
                a["blocking"] = False
                gaps.append(a)

    # ── experience ──
    min_years = crit.get("min_years")
    seniority = crit.get("seniority")
    if min_years is not None and min_years > YEARS_EXPERIENCE:
        blocking.append({"type": "experience", "required_years": min_years,
                         "detail": f"{min_years} ans d'expérience demandés, profil : {YEARS_EXPERIENCE}",
                         "evidence": ev.get("min_years", "")})
    elif seniority == "lead":
        blocking.append({"type": "experience", "seniority": "lead",
                         "detail": "poste de lead / principal / architecte",
                         "evidence": ev.get("seniority", "")})
    elif seniority == "senior" and min_years is None:
        blocking.append({"type": "experience", "seniority": "senior",
                         "detail": f"profil senior demandé, profil : {YEARS_EXPERIENCE} ans",
                         "evidence": ev.get("seniority", "")})
    elif seniority == "senior":
        # A year figure the profile meets outweighs the "senior" label.
        gaps.append({"skill": "seniority", "label": "intitulé senior", "status": "gap",
                     "required": True, "blocking": False,
                     "detail": f"intitulé senior, mais {min_years} ans demandés (profil : {YEARS_EXPERIENCE})"})

    # ── human languages ──
    for lang, req in (crit.get("languages") or {}).items():
        have = LANGUAGES.get(lang)
        steps = _lang_steps(req["level"], have)
        if steps <= 0:
            continue
        name = _LANG_FR.get(lang, lang)
        detail = f"{name} {req['level']} demandé, profil : {have or 'non parlé'}"
        if req.get("explicit") and steps >= 2:
            blocking.append({"type": "language", "lang": lang, "level": req["level"],
                             "have": have, "detail": detail,
                             "evidence": ev.get(f"lang_{lang}", "")})
        else:
            gaps.append({"skill": f"lang_{lang}", "label": name, "status": "gap",
                         "required": bool(req.get("explicit")), "blocking": False,
                         "detail": detail + ("" if req.get("explicit") else " (implicite)")})

    # ── coverage & category ──
    if req_credits:
        coverage = sum(req_credits) / len(req_credits)
    elif nice_credits:
        coverage = sum(nice_credits) / len(nice_credits)
    else:
        coverage = 0.0
    nice_cov = sum(nice_credits) / len(nice_credits) if nice_credits else 0.0

    n_block = len(blocking)
    location_block = any(b["type"] == "location" for b in blocking)
    if location_block or n_block >= 2:
        category = A_EVITER
    elif n_block == 1:
        category = CONTACT_RESEAU if coverage >= MIN_COVERAGE else A_EVITER
    elif coverage >= GOOD_COVERAGE:
        category = BON_MATCH
    elif coverage >= MIN_COVERAGE:
        category = CONTACT_RESEAU
    else:
        category = A_EVITER

    reasons = [b["detail"] for b in blocking]
    if category == A_EVITER and not reasons:
        reasons = [f"trop éloigné du profil : {round(coverage * 100)} % des compétences requises couvertes"]
    elif category == CONTACT_RESEAU and not blocking:
        missing = [p["label"] for p in partial if p["required"]] or [g["label"] for g in gaps if g.get("required")]
        reasons = [f"couverture partielle ({round(coverage * 100)} %)"
                   + (f" : {', '.join(missing[:3])} pas encore pratiqué(s) en production" if missing else "")]

    score = round(100 * coverage + 10 * nice_cov * NICE_WEIGHT - 15 * n_block)
    return {
        "category": category,
        "score": max(0, min(100, score)),
        "coverage": round(coverage, 2),
        # "mentions" = the ad had no identifiable requirements, so coverage is
        # measured on the stack it merely mentions — a weaker signal.
        "basis": "required" if req_credits else "mentions",
        "covered": covered,
        "partial": partial,
        "gaps": gaps,
        "blocking": blocking,
        "reasons": reasons,
        "criteria": {k: v for k, v in crit.items() if not k.startswith("_")},
    }


def summary_lines(ev: Dict) -> List[str]:
    """Human-readable breakdown for application.md / the digest / --report."""
    def fmt(items, with_via=False):
        out = []
        for a in items:
            tag = "requis" if a.get("required") else "un plus"
            via = f" (via {a['via_label']})" if with_via and a.get("via_label") else ""
            if a["status"] == "secondary":
                via = " (connu, pas en production)"
            out.append(f"{a['label']}{via} [{tag}]")
        return out

    basis = ("" if ev.get("basis", "required") == "required"
             else " (exigences non identifiées : calculée sur la stack mentionnée, à vérifier)")
    lines = [f"Catégorie : {LABEL_FR[ev['category']]} — couverture {round(ev['coverage'] * 100)} %{basis}"]
    if ev["covered"]:
        lines.append("Couvert : " + ", ".join(fmt(ev["covered"])))
    if ev["partial"]:
        lines.append("Partiel : " + ", ".join(fmt(ev["partial"], with_via=True)))
    soft_gaps = [g for g in ev["gaps"] if not g.get("blocking")]
    if soft_gaps:
        lines.append("Écarts non bloquants : " + ", ".join(g.get("detail") or g["label"] for g in soft_gaps))
    if ev["blocking"]:
        lines.append("ÉCARTS BLOQUANTS : " + " ; ".join(
            b["detail"] + (f" — « {b['evidence'][:120]} »" if b.get("evidence") else "")
            for b in ev["blocking"]))
    if ev["reasons"] and not ev["blocking"]:
        lines.append("Motif : " + " ; ".join(ev["reasons"]))
    return lines
