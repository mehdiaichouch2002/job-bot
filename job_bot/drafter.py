"""
drafter.py
~~~~~~~~~~
Writes the message a category calls for, and refuses to oversell.

  BON_MATCH       -> full application email (+ cover letter for the packet)
  CONTACT_RESEAU  -> short, honest note that NAMES the gap and asks whether a
                     better-suited role exists — never a full application
  A_EVITER        -> nothing

The LLM only writes body paragraphs, from a whitelist built from the
evaluation: proven skills may be claimed; transferable ones only in
"my X lets me ramp up quickly on Y" form; missing ones not at all; figures
only from profile.FACTS. Greeting, prior-contact sentence and signature are
added in code, so they cannot drift.

Every draft (LLM or template) goes through validate(). A draft that fails is
rewritten once with the violations spelled out, then replaced by the
deterministic template — which is itself covered by the same checks in tests.
"""
import logging
import re
from typing import Dict, List, Optional, Tuple

from . import taxonomy
from .profile import YEARS_EXPERIENCE, FACTS, ALLOWED_NUMBERS, level_of
from .matcher import BON_MATCH, CONTACT_RESEAU
from .email_generator import (call_llm, llm_available, _sig, _parse_response,
                              _canada_pitch, _BANNED_EN, _BANNED_FR)

logger = logging.getLogger(__name__)

Y = YEARS_EXPERIENCE

# Which facts are relevant to which skills (used to pick the 2 best examples).
_FACT_SKILLS = {
    "users":      {"magento2", "ecommerce", "b2b_commerce"},
    "plp":        {"elasticsearch", "mysql", "redis", "varnish", "magento2"},
    "hyva":       {"hyva", "alpinejs", "tailwind", "javascript", "html_css"},
    "extensions": {"magento2", "php"},
    "refactor":   {"php", "docker", "git"},
    "laravel":    {"laravel", "rest_api", "php", "mysql"},
    "hr_app":     {"php", "mysql"},
}
_LANG_NAME = {"en": {"fr": "French", "en": "English", "de": "German", "nl": "Dutch", "es": "Spanish", "it": "Italian"},
              "fr": {"fr": "français", "en": "anglais", "de": "allemand", "nl": "néerlandais", "es": "espagnol", "it": "italien"}}


# ═══ validation ══════════════════════════════════════════════════

_NUM_PATTERNS = [
    re.compile(r"(\d+(?:[.,]\d+)?)\s*%"),
    re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:s|sec|seconds?|secondes?)\b"),
    re.compile(r"(\d{1,3}(?:[ ,.]\d{3})+|\d+)\s*\+"),
    re.compile(r"(\d+)\s*k\b"),
    re.compile(r"(\d+(?:[.,]\d+)?)\s*x\b"),
    re.compile(r"(\d{1,3}(?:[ ,.]\d{3})+)"),
]
_YEARS_CLAIM = re.compile(r"(\d{1,2})\s*\+?\s*(?:years?|yrs?|ans|ann[ée]es)\b", re.I)
_WORD_YEARS = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|un|deux|trois|quatre|cinq|six|sept|huit|neuf|dix)"
    r"\s+(?:\w+\s+)?(?:years?|ans|ann[ée]es)\b", re.I)
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "un": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "sept": 7,
          "huit": 8, "neuf": 9, "dix": 10}
# A sentence quoting the AD's requirement ("the role asks for 5 years") is fine.
_REQ_CONTEXT = re.compile(r"\b(requi|demand|exig|ask|asks|looking for|recherch|minimum|mentionn|calls? for|vise)", re.I)

_TRANSFER = re.compile(
    r"monter\s+(?:rapidement\s+)?en\s+comp[ée]tences?|ramp(?:ing)?\s+up|get\s+up\s+to\s+speed"
    r"|transf[ée]r|pick(?:ing)?\s+(?:it\s+)?up|me\s+permet|lets\s+me|allows?\s+me|build\s+on"
    r"|m'appuyer|s'appuie|apprendre|learn|proche\s+de|close\s+to|similar\s+to", re.I)
_ACK = re.compile(
    r"pas\s+(?:encore\s+)?(?:d'exp|utilis|pratiqu|travaill)|not\s+(?:yet\s+)?(?:used|worked|practi)"
    r"|haven't|have\s+not|n'ai\s+pas|ne\s+l'ai\s+pas|don't\s+have|do\s+not\s+have|without"
    r"|formation|studies|studied|academic|acad[ée]mique|pas\s+en\s+production|not\s+in\s+production"
    r"|prérequis|requirement|requis|required|asks?\s+for|demande|gap|écart|lacks?|manque", re.I)
_SENT = re.compile(r"(?<=[.!?;])\s+|\n+")


def _norm_num(s: str) -> str:
    return s.lower().replace(" ", " ").replace(" ", " ").strip()


def check_numbers(text: str, extra_years: set) -> List[str]:
    problems = []
    for sent in _SENT.split(text):
        for rx in _NUM_PATTERNS:
            for m in rx.finditer(sent):
                n = _norm_num(m.group(1))
                if n not in ALLOWED_NUMBERS and n.replace(" ", ",") not in ALLOWED_NUMBERS:
                    problems.append(f"chiffre non sourcé « {m.group(0).strip()} »")
        for m in _YEARS_CLAIM.finditer(sent):
            n = int(m.group(1))
            if n == Y:
                continue
            if n in extra_years and _REQ_CONTEXT.search(sent):
                continue
            problems.append(f"durée non conforme au profil « {m.group(0)} » (profil : {Y} ans)")
        for m in _WORD_YEARS.finditer(sent):
            n = _WORDS[m.group(1).lower()]
            if n == Y or (n in extra_years and _REQ_CONTEXT.search(sent)):
                continue
            problems.append(f"durée non conforme au profil « {m.group(0)} » (profil : {Y} ans)")
    return problems


def check_claims(text: str, ev: Dict) -> List[str]:
    """Any skill that is not a core production skill may only appear with a
    ramp-up or gap-acknowledging phrasing in the same sentence."""
    unknown = [b["label"] for b in ev.get("blocking", []) if b.get("type") == "skill"
               and b.get("skill") and b["skill"] not in taxonomy.ALIASES]
    problems = []
    for sent in _SENT.split(text):
        low = sent.lower()
        risky = [k for k in taxonomy.find_all(sent) if level_of(k) != "core"]
        risky += [u for u in unknown if u.lower() in low]
        if risky and not (_TRANSFER.search(sent) or _ACK.search(sent)):
            names = ", ".join(taxonomy.label(k) for k in risky)
            problems.append(f"affirmation non autorisée sur {names} : « {sent.strip()[:120]} »")
    return problems


def check_gap_named(text: str, ev: Dict, lang: str) -> List[str]:
    """A network message must say out loud what the gap is."""
    low = text.lower()
    problems = []
    for b in ev.get("blocking", []):
        if b["type"] == "skill":
            ok = b["label"].lower() in low or taxonomy.mentioned(b.get("skill", ""), text)
        elif b["type"] == "experience":
            ok = (bool(re.search(rf"\b{b['required_years']}\b", low)) if b.get("required_years")
                  else bool(re.search(r"senior|lead|principal|architect", low)))
        elif b["type"] == "language":
            ok = _LANG_NAME[lang].get(b["lang"], b["lang"]).lower() in low
        else:
            ok = True
        if not ok:
            problems.append(f"l'écart bloquant n'est pas mentionné : {b['detail']}")
    if not re.search(r"autre\s+poste|plus\s+adapt|other\s+(?:role|position|opening)|better[- ]suited"
                     r"|better\s+fit|poste\s+qui|prochainement|future|upcoming", low):
        problems.append("le message ne demande pas s'il existe un poste plus adapté")
    return problems


def validate(text: str, ev: Dict, kind: str, lang: str, extra_years: set = None,
             job: Dict = None) -> List[str]:
    extra_years = extra_years or set()
    # The ad's own title / company name may contain figures or tech names
    # ("Senior Dev 5+ years") — quoting them is not a claim.
    for quoted in ((job or {}).get("title"), (job or {}).get("company"), _sig("en")):
        if quoted and len(quoted) > 2:
            text = re.sub(re.escape(quoted), " ", text, flags=re.I)
    problems = check_numbers(text, extra_years) + check_claims(text, ev)
    if kind == "network":
        problems += check_gap_named(text, ev, lang)
    return problems


# ═══ shared pieces ═══════════════════════════════════════════════

def _first_name(job: Dict) -> str:
    name = (job.get("contact_name") or "").strip()
    return name.split()[0] if name else ""


def _greeting(job: Dict, lang: str) -> str:
    first = _first_name(job)
    if lang == "fr":
        return f"Bonjour {first}," if first else "Bonjour,"
    return f"Hi {first}," if first else "Hello,"


def _closing(lang: str) -> str:
    return "Cordialement," if lang == "fr" else "Best regards,"


def _date_fr(iso: str) -> str:
    d = (iso or "")[:10]
    return f"{d[8:10]}/{d[5:7]}/{d[0:4]}" if len(d) == 10 else d


def prior_sentence(prior: Optional[Dict], job: Dict, lang: str) -> str:
    """Reference the earlier exchange instead of starting from zero — but only
    when it demonstrably reached them. A silent send to a company-level address
    may never have arrived (93% of past sends went to invented careers@
    addresses, some at the wrong company), so claiming "I applied before" would
    be untrue. It is still flagged to Mehdi in the packet and digest."""
    if not prior:
        return ""
    if prior["match"] == "company" and prior.get("outcome") in ("silence", "bounce", None):
        return ""
    prev = prior.get("last_job_title") or ("un autre poste" if lang == "fr" else "another role")
    if lang == "fr":
        date = _date_fr(prior.get("last_at"))
        if prior["match"] == "person":
            return f"Je vous avais écrit le {date} au sujet du poste de {prev} ; je reviens vers vous pour cette nouvelle offre."
        return (f"J'avais déjà candidaté chez {job.get('company') or prior.get('company')} le {date} "
                f"(poste de {prev}) ; cette nouvelle offre m'amène à reprendre contact.")
    date = (prior.get("last_at") or "")[:10]
    if prior["match"] == "person":
        return f"I wrote to you on {date} about the {prev} role, and I'm following up because of this new opening."
    return (f"I applied to {job.get('company') or prior.get('company')} on {date} for the {prev} role, "
            f"and this new opening is why I'm getting back in touch.")


def _assemble(job: Dict, lang: str, prior: Optional[Dict], paragraphs: str) -> str:
    parts = [_greeting(job, lang)]
    ps = prior_sentence(prior, job, lang)
    if ps:
        parts.append(ps)
    parts.append(paragraphs.strip())
    parts.append(f"{_closing(lang)}\n{_sig(lang)}")
    return "\n\n".join(parts)


def _strip_frame(text: str) -> str:
    """Remove any greeting / sign-off / signature the LLM added anyway."""
    lines = [l for l in (text or "").strip().splitlines()]
    while lines and re.match(r"^\s*(hi|hello|dear|bonjour|madame|monsieur)\b", lines[0], re.I):
        lines.pop(0)
    for i, l in enumerate(lines):
        if re.match(r"^\s*(best regards|kind regards|regards|cordialement|bien cordialement|mehdi aichouch)\b", l, re.I):
            lines = lines[:i]
            break
    return "\n".join(lines).strip()


def pick_facts(ev: Dict, n: int = 2) -> List[Dict]:
    have = {a["skill"] for a in ev.get("covered", [])}
    ranked = sorted(FACTS, key=lambda f: -len(_FACT_SKILLS.get(f["id"], set()) & have))
    return ranked[:n]


def _headline(job: Dict, lang: str) -> str:
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    if re.search(r"\bmagento\b|adobe commerce", text):
        return "développeur Magento 2 / Adobe Commerce" if lang == "fr" else "Magento 2 / Adobe Commerce developer"
    return "développeur full-stack PHP / Laravel / React" if lang == "fr" else "full-stack PHP / Laravel / React developer"


def _labels(items, k=4) -> List[str]:
    return [a["label"] for a in items[:k]]


def _join(xs: List[str], lang: str) -> str:
    if len(xs) <= 1:
        return "".join(xs)
    return ", ".join(xs[:-1]) + (" et " if lang == "fr" else " and ") + xs[-1]


def _at(job, lang):
    c = job.get("company") or ""
    return (f" chez {c}" if lang == "fr" else f" at {c}") if c else ""


def _extra_years(ev: Dict) -> set:
    return {b["required_years"] for b in ev.get("blocking", []) if b.get("required_years")}


# ═══ templates (deterministic fallback) ═════════════════════════

def _availability(job: Dict, lang: str) -> str:
    if job.get("market") == "canada":
        if lang == "fr":
            return "Je prépare activement ma relocalisation au Canada et je peux démarrer à distance en attendant."
        return _canada_pitch(job)
    if lang == "fr":
        return ("Je suis basé à Fès (GMT+1, mêmes horaires que l'Europe), j'ai l'habitude de travailler à "
                "distance avec des équipes européennes et je suis disponible immédiatement.")
    return ("I'm based in Fès, Morocco (GMT+1, same working hours as Europe), used to working remotely "
            "with European teams, and available immediately.")


def _partial_sentences(ev: Dict, lang: str) -> List[str]:
    out = []
    for p in [p for p in ev.get("partial", []) if p.get("required")][:2]:
        if p["status"] == "transferable":
            out.append(
                f"Je n'ai pas encore utilisé {p['label']} en production ; ma maîtrise de {p['via_label']} "
                f"me permet de monter rapidement en compétence dessus." if lang == "fr" else
                f"I haven't used {p['label']} in production yet; my {p['via_label']} experience lets me "
                f"ramp up on it quickly.")
        else:
            out.append(
                f"{p['label']}, je l'ai pratiqué en formation, pas encore en production." if lang == "fr" else
                f"I've worked with {p['label']} in my studies, not yet in production.")
    return out


def _template_application(job: Dict, ev: Dict, lang: str) -> Tuple[str, str]:
    title = job.get("title", "")
    exact = _labels(ev.get("covered", []))
    facts = pick_facts(ev)
    if lang == "fr":
        p1 = (f"Je vous écris au sujet du poste de {title}{_at(job, lang)}. Je suis {_headline(job, lang)} "
              f"avec {Y} ans d'expérience" + (f", et votre offre recoupe ce que je fais au quotidien : "
                                               f"{_join(exact, lang)}." if exact else "."))
        p3 = "Deux exemples concrets : " + " ; ".join(f["fr"] for f in facts) + "."
        close = "Je serais heureux d'en parler lors d'un court échange."
        subject = f"Candidature : {title}"
    else:
        p1 = (f"I'm writing about the {title} role{_at(job, lang)}. I'm a {_headline(job, lang)} with "
              f"{Y} years of experience" + (f", and your ad overlaps with what I do every day: "
                                            f"{_join(exact, lang)}." if exact else "."))
        p3 = "Two concrete examples: " + "; ".join(f["en"] for f in facts) + "."
        close = "Happy to talk it through in a short call."
        subject = f"Application: {title}"
    paras = [p1] + _partial_sentences(ev, lang) + [p3, _availability(job, lang), close]
    return subject, "\n\n".join(paras)


def _gap_sentence(ev: Dict, lang: str) -> str:
    fr = lang == "fr"
    blocking = ev.get("blocking", [])
    if blocking:
        b = blocking[0]
        if b["type"] == "experience" and b.get("required_years"):
            return (f"le poste demande {b['required_years']} ans d'expérience et j'en ai {Y}" if fr else
                    f"the role asks for {b['required_years']} years of experience and I have {Y}")
        if b["type"] == "experience" and b.get("seniority") == "lead":
            return (f"le poste est un rôle de lead, et j'ai {Y} ans d'expérience de développeur" if fr else
                    f"the role is a lead position, and I have {Y} years of experience as a developer")
        if b["type"] == "experience":
            return (f"le poste demande un profil senior, et j'ai {Y} ans d'expérience" if fr else
                    f"the role asks for a senior profile, and I have {Y} years of experience")
        if b["type"] == "skill":
            return (f"{b['label']} fait partie des prérequis et je ne l'ai pas pratiqué" if fr else
                    f"{b['label']} is listed as a requirement and I haven't worked with it")
        if b["type"] == "language":
            name = _LANG_NAME[lang].get(b["lang"], b["lang"])
            have = b.get("have")
            if fr:
                return (f"le poste demande un {name} de niveau {b['level']}, et mon niveau actuel est {have}"
                        if have else f"le poste demande le {name}, que je ne parle pas")
            return (f"the role asks for {b['level']} {name}, and my current level is {have}"
                    if have else f"the role asks for {name}, which I don't speak")
    missing = [p["label"] for p in ev.get("partial", []) if p.get("required")][:3]
    if missing:
        return (f"votre offre demande {_join(missing, lang)}, que je n'ai pas encore pratiqués en production" if fr
                else f"the role asks for {_join(missing, lang)}, which I haven't used in production yet")
    return ("mon profil ne couvre qu'une partie des compétences demandées" if fr
            else "my profile only covers part of what the role asks for")


def _template_network(job: Dict, ev: Dict, lang: str) -> Tuple[str, str]:
    title = job.get("title", "")
    exact = _labels(ev.get("covered", []), 3)
    fact = pick_facts(ev, 1)[0]
    gap = _gap_sentence(ev, lang)
    if lang == "fr":
        strengths = (f"En revanche, {_join(exact, lang)} font partie de mon quotidien depuis {Y} ans"
                     if exact else f"J'ai en revanche {Y} ans d'expérience en développement e-commerce")
        body = (f"J'ai vu votre offre de {title}{_at(job, lang)}. Je préfère être transparent : {gap}. "
                f"{strengths}, par exemple : {fact['fr']}.\n\n"
                "Auriez-vous, aujourd'hui ou prochainement, un poste plus adapté à ce profil ? "
                "Je serais heureux d'en discuter.")
        subject = f"Question au sujet du poste {title}"
    else:
        strengths = (f"That said, {_join(exact, lang)} have been my day-to-day work for {Y} years"
                     if exact else f"That said, I have {Y} years of experience in e-commerce development")
        body = (f"I saw your {title} opening{_at(job, lang)}. To be upfront: {gap}. "
                f"{strengths}, for example: {fact['en']}.\n\n"
                "Do you have, now or in the near future, a role that would be a better fit for this profile? "
                "I'd be glad to talk.")
        subject = f"Question about the {title} role"
    return subject, body


# ═══ LLM prompts ═════════════════════════════════════════════════

def _whitelist(ev: Dict, lang: str) -> str:
    fr = lang == "fr"
    proven = ", ".join(a["label"] for a in ev.get("covered", [])) or "-"
    transfer = "; ".join(
        (f"{p['label']} → écrire : « ma maîtrise de {p['via_label']} me permet de monter rapidement en compétence sur {p['label']} »"
         if fr else
         f"{p['label']} → write: \"my {p['via_label']} experience lets me ramp up quickly on {p['label']}\"")
        for p in ev.get("partial", []) if p["status"] == "transferable") or "-"
    studied = ", ".join(p["label"] for p in ev.get("partial", []) if p["status"] == "secondary") or "-"
    missing = ", ".join(g["label"] for g in ev.get("gaps", [])) or "-"
    facts = "\n".join(f"- {f[lang]}" for f in pick_facts(ev, 3))
    if fr:
        return (f"COMPÉTENCES PROUVÉES (seules à pouvoir être affirmées) : {proven}\n"
                f"TRANSFÉRABLES (formulation imposée, jamais « je maîtrise ») : {transfer}\n"
                f"CONNUES EN FORMATION, PAS EN PRODUCTION : {studied}\n"
                f"ABSENTES (ne pas les mentionner comme acquises) : {missing}\n"
                f"EXPÉRIENCE : exactement {Y} ans. Aucune autre durée.\n"
                f"FAITS CHIFFRÉS AUTORISÉS (les seuls chiffres permis, recopiés fidèlement) :\n{facts}")
    return (f"PROVEN SKILLS (the only ones you may claim): {proven}\n"
            f"TRANSFERABLE (mandatory phrasing, never 'I know/master'): {transfer}\n"
            f"STUDIED, NOT IN PRODUCTION: {studied}\n"
            f"MISSING (never present as acquired): {missing}\n"
            f"EXPERIENCE: exactly {Y} years. No other duration.\n"
            f"ALLOWED FACTS (the only figures allowed, copied faithfully):\n{facts}")


def _system(kind: str, lang: str, job: Dict, ev: Dict) -> str:
    fr = lang == "fr"
    banned = _BANNED_FR if fr else _BANNED_EN
    avail = _availability(job, lang)
    if kind == "application":
        task = ("Rédige le CORPS d'un email de candidature (110–150 mots, 3 ou 4 paragraphes courts) : "
                "le poste visé et une raison concrète liée à l'offre ; deux faits chiffrés en prose ; "
                f"cette phrase de disponibilité, reformulée sans rien ajouter : « {avail} » ; "
                "une phrase de clôture sobre." if fr else
                "Write the BODY of an application email (110–150 words, 3 or 4 short paragraphs): the role "
                "and one concrete reason tied to the ad; two quantified facts woven into prose; this "
                f"availability sentence, reworded without adding anything: \"{avail}\"; a plain closing line.")
    elif kind == "cover":
        task = ("Rédige le CORPS d'une lettre de motivation (200–260 mots, 3 paragraphes) : accroche liée "
                f"au poste ; deux ou trois faits chiffrés ; disponibilité : « {avail} »." if fr else
                "Write the BODY of a cover letter (200–260 words, 3 paragraphs): a hook tied to the role; "
                f"two or three quantified facts; availability: \"{avail}\".")
    else:
        gap = _gap_sentence(ev, lang)
        task = ("Rédige un message COURT (60–90 mots, 2 paragraphes) à un recruteur. Ce n'est PAS une "
                f"candidature complète. Dis honnêtement l'écart : « {gap} ». Cite ensuite UN point fort "
                "réel. Termine en demandant s'il existe, maintenant ou prochainement, un poste plus adapté."
                if fr else
                "Write a SHORT message (60–90 words, 2 paragraphs) to a recruiter. It is NOT a full "
                f"application. State the gap honestly: \"{gap}\". Then mention ONE real strength. End by "
                "asking whether there is, now or soon, a role that would be a better fit.")
    if fr:
        intro = "Tu écris pour Mehdi Aichouch, développeur basé au Maroc."
        rules = "RÈGLES D'HONNÊTETÉ (en enfreindre une invalide le texte) :\n"
        frame = "Pas de formule d'appel, pas de formule de politesse, pas de signature : uniquement les paragraphes."
        out = "SORTIE :\nOBJET: <objet court>\nCORPS:\n<paragraphes>"
        banned_hdr = "INTERDIT"
    else:
        intro = "You write for Mehdi Aichouch, a developer based in Morocco."
        rules = "HONESTY RULES (breaking one invalidates the text):\n"
        frame = "No greeting, no sign-off, no signature: paragraphs only."
        out = "OUTPUT:\nSUBJECT: <short subject>\nBODY:\n<paragraphs>"
        banned_hdr = "BANNED"
    return (f"{intro}\n{task}\n\n{rules}{_whitelist(ev, lang)}\n\n"
            f"{banned_hdr} : {banned}\n{frame}\n" + ("" if kind == "cover" else out))


def _user(job: Dict) -> str:
    return (f"Role: {job.get('title', '')}\nCompany: {job.get('company') or '-'}\n"
            f"Ad (excerpt):\n{(job.get('description') or '')[:1200]}")


def _llm_draft(kind: str, job: Dict, ev: Dict, lang: str) -> Optional[Tuple[str, str, List[str]]]:
    """(subject, paragraphs, violations_fixed) or None if no valid LLM draft."""
    if not llm_available():
        return None
    system = _system(kind, lang, job, ev)
    user = _user(job)
    fixed: List[str] = []
    for attempt in range(2):
        try:
            raw = call_llm(system, user, temperature=0.6 if attempt == 0 else 0.2,
                           max_tokens=700 if kind == "cover" else 500)
        except Exception as e:
            logger.info("LLM draft failed (%s) — template", e)
            return None
        if not raw:
            return None
        if kind == "cover":
            subject, body = "", raw
        else:
            subject, body = _parse_response(raw, job, lang)
        body = _strip_frame(body)
        problems = validate(f"{subject}\n{body}", ev, kind, lang, _extra_years(ev), job)
        if not problems:
            return subject, body, fixed
        fixed = problems
        logger.info("Draft rejected (%s, attempt %d): %s", kind, attempt + 1, "; ".join(problems[:3]))
        user = (_user(job) + "\n\n" + ("Ton brouillon précédent enfreignait ces règles, corrige-les :\n"
                if lang == "fr" else "Your previous draft broke these rules, fix them:\n")
                + "\n".join(f"- {p}" for p in problems))
    return None


# ═══ public API ══════════════════════════════════════════════════

def draft_application_email(job: Dict, ev: Dict, prior: Optional[Dict] = None) -> Dict:
    lang = job.get("language", "en")
    got = _llm_draft("application", job, ev, lang)
    source = "llm"
    if got:
        subject, paras, fixed = got
    else:
        subject, paras = _template_application(job, ev, lang)
        fixed, source = [], "template"
    subject = subject or (f"Candidature : {job.get('title', '')}" if lang == "fr"
                          else f"Application: {job.get('title', '')}")
    return {"kind": "application", "subject": subject, "body": _assemble(job, lang, prior, paras),
            "source": source, "rejected_issues": fixed}


def draft_cover_letter(job: Dict, ev: Dict) -> Dict:
    lang = job.get("language", "en")
    got = _llm_draft("cover", job, ev, lang)
    if got:
        text, source = got[1], "llm"
    else:
        text, source = _template_application(job, ev, lang)[1], "template"
    return {"kind": "cover", "text": f"{text}\n\n{_closing(lang)}\n{_sig(lang)}", "source": source}


def draft_network_message(job: Dict, ev: Dict, prior: Optional[Dict] = None) -> Dict:
    lang = job.get("language", "en")
    got = _llm_draft("network", job, ev, lang)
    source = "llm"
    if got:
        subject, paras, fixed = got
    else:
        subject, paras = _template_network(job, ev, lang)
        fixed, source = [], "template"
    subject = subject or (f"Question au sujet du poste {job.get('title', '')}" if lang == "fr"
                          else f"Question about the {job.get('title', '')} role")
    return {"kind": "network", "subject": subject, "body": _assemble(job, lang, prior, paras),
            "source": source, "rejected_issues": fixed}


def draft_for(job: Dict, ev: Dict, prior: Optional[Dict] = None) -> Optional[Dict]:
    if ev["category"] == BON_MATCH:
        return draft_application_email(job, ev, prior)
    if ev["category"] == CONTACT_RESEAU:
        return draft_network_message(job, ev, prior)
    return None
