"""
extractor.py
~~~~~~~~~~~~
Job ad -> structured criteria, keeping REQUIRED and NICE-TO-HAVE apart.

Two extractors, merged:
  - regex (always runs, free): section headers ("Requirements", "Nice to have",
    "Profil recherché", "Un plus"…) plus sentence markers ("must", "is a plus").
  - LLM (Groq, JSON mode) when available and within the run's budget.

The LLM is never trusted blindly. A skill is kept only if the ad actually
mentions it; years / seniority / language requirements are kept only if the
quote the LLM gives really appears in the ad. Hallucinated criteria can
therefore never create a blocking gap.

Criteria dict (see matcher.py for how it is used):
  required:      [skill key | "?free text"]   unknown terms are prefixed "?"
  nice_to_have:  [...]
  min_years:     int | None,  evidence["min_years"] = quote
  seniority:     "junior" | "mid" | "senior" | "lead" | None
  work_mode:     "remote" | "hybrid" | "onsite" | None
  languages:     {lang: {"level": CEFR|"native", "explicit": bool}}
  evidence:      {criterion: quote from the ad}
  source:        "llm+regex" | "regex"
"""
import json
import logging
import re
from typing import Dict, List, Optional

from . import taxonomy
from .profile import LANGUAGE_SCALE

logger = logging.getLogger(__name__)

_DESC_LIMIT = 3500   # characters of the ad sent to the LLM

# ── helpers ──────────────────────────────────────────────────────

def _norm(s: str) -> str:
    s = (s or "").lower().replace(" ", " ").replace("’", "'")
    return re.sub(r"\s+", " ", s).strip()


def _quote_in(quote: str, text_norm: str) -> bool:
    q = _norm(quote).strip(" .,;:\"'«»")
    return len(q) >= 4 and q in text_norm


def _empty() -> Dict:
    return {"required": [], "nice_to_have": [], "min_years": None, "seniority": None,
            "work_mode": None, "languages": {}, "evidence": {}, "source": "regex"}


# ── regex extractor ──────────────────────────────────────────────

_REQ_HDR = (r"requirements?|required(?:\s+skills)?|must[- ]haves?|what you(?:'ll)?\s+(?:need|bring)"
            r"|qualifications|who you are|your profile|about you|what we(?:'re| are)\s+looking for"
            r"|we(?:'re| are)\s+looking for|profil recherch[ée]|votre profil|le profil"
            r"|comp[ée]tences\s+(?:requises|techniques)|pr[ée]\s?requis|exigences"
            r"|ce que nous recherchons|vous (?:avez|poss[ée]dez)")
_NICE_HDR = (r"nice[- ]to[- ]haves?|bonus(?:\s+points)?|preferred(?:\s+qualifications)?"
             r"|good to have|ideally|plus$|atouts?|serait un plus|un plus|souhait[ée]s?"
             r"|id[ée]alement|appr[ée]ci[ée]s?")
_OTHER_HDR = (r"benefits|what we offer|perks|about us|who we are|nous offrons|avantages"
              r"|responsibilities|your (?:mission|role)|vos missions|missions?|le poste"
              r"|what you(?:'ll)? do|compensation|salary|salaire|how to apply")
_HDR_RE = re.compile(
    rf"(?P<req>\b(?:{_REQ_HDR})\b)|(?P<nice>\b(?:{_NICE_HDR})\b)|(?P<other>\b(?:{_OTHER_HDR})\b)",
    re.I,
)
# A header is only a header when followed by ":" / newline / bullet, or
# written like a title — otherwise "ideally" mid-sentence would open a section.
_HDR_TAIL = re.compile(r"^\s*(?::|\n|•|-|–|\*)")

_NICE_MARK = re.compile(
    r"\b(is|are|would be|will be)\s+(a\s+)?(big\s+|huge\s+|strong\s+)?(plus|bonus|advantage)\b"
    r"|\bnice[- ]to[- ]have\b|\bpreferred\b|\bideally\b|\bbonus\b|\ba plus\b|\bun (gros )?plus\b"
    r"|\bserait (un )?(plus|atout)\b|\bappr[ée]ci[ée]e?s?\b|\bsouhait[ée]e?s?\b|\bid[ée]alement\b"
    r"|\bavantage\b|\batout\b|\bfamiliarity with\b|\bexposure to\b",
    re.I,
)
_REQ_MARK = re.compile(
    r"\bmust\b|\brequired\b|\bmandatory\b|\bessential\b|\bstrong (?:experience|knowledge)\b"
    r"|\bobligatoire\b|\bexig[ée]e?s?\b|\bindispensable\b|\bimp[ée]ratif\b|\bma[iî]trise\b",
    re.I,
)
_SENT_SPLIT = re.compile(r"(?<=[.;!?])\s+|\s*[•·▪●•]\s*|\s+-\s+|\n+")

_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "un": 1, "deux": 2, "trois": 3, "quatre": 4,
             "cinq": 5, "sept": 7, "huit": 8, "neuf": 9, "dix": 10}
_YEARS_RE = re.compile(
    r"(?P<n>\d{1,2}|" + "|".join(_WORD_NUM) + r")\s*(?:\+|plus)?\s*"
    r"(?:(?:-|to|à|a)\s*\d{1,2}\s*)?(?:years?|yrs?|ans|ann[ée]es)\b",
    re.I,
)
_EXP_WORD = re.compile(r"exp[ée]rience|\bexp\b|in (?:a|the) similar|professional|professionnelle|minimum|at least|au moins", re.I)

_SENIOR_TITLE = re.compile(r"\b(senior|sr\.?|confirm[ée]e?|exp[ée]riment[ée]e?)\b", re.I)
_LEAD_TITLE = re.compile(r"\b(lead|principal|staff|head of|architect|architecte|tech lead|cto)\b", re.I)
_JUNIOR_TITLE = re.compile(r"\b(junior|jr\.?|d[ée]butant|entry[- ]level|graduate)\b", re.I)
_MID_TITLE = re.compile(r"\b(mid(?:[- ]level)?|medior|intermediate|interm[ée]diaire)\b", re.I)

_LANG_NAMES = {
    "fr": r"fran[cç]ais|french", "en": r"anglais|english", "de": r"allemand|german|deutsch",
    "nl": r"n[ée]erlandais|dutch", "es": r"espagnol|spanish", "it": r"italien|italian",
}
_LEVEL_WORDS = [
    ("native", r"native|mother tongue|langue maternelle|natif|native[- ]level"),
    ("C2", r"\bc2\b"),
    ("C1", r"\bc1\b|fluent|fluency|courant|bilingue|bilingual|excellent|parfait|full professional|business[- ]level|ma[iî]trise"),
    ("B2", r"\bb2\b|professional|professionnel|good|bon(?:ne)?|working knowledge|op[ée]rationnel"),
    ("B1", r"\bb1\b|intermediate|interm[ée]diaire"),
]


def _sections(text: str) -> List[tuple]:
    """[(kind, start, end)] where kind in req/nice/other."""
    marks = []
    for m in _HDR_RE.finditer(text):
        tail = text[m.end():m.end() + 3]
        if not _HDR_TAIL.match(tail):
            continue
        marks.append((m.lastgroup, m.start()))
    out = []
    for i, (kind, start) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else len(text)
        out.append((kind, start, end))
    return out


def _section_at(sections, pos) -> Optional[str]:
    for kind, s, e in sections:
        if s <= pos < e:
            return kind
    return None


def _years_in(sentence: str) -> Optional[int]:
    best = None
    for m in _YEARS_RE.finditer(sentence):
        window = sentence[max(0, m.start() - 40): m.end() + 60]
        if not _EXP_WORD.search(window):
            continue            # "founded 10 years ago" is not a requirement
        raw = m.group("n").lower()
        n = int(raw) if raw.isdigit() else _WORD_NUM.get(raw)
        if n and n <= 20:
            best = n if best is None else max(best, n)
    return best


def _year_numbers(quote: str) -> set:
    """Every "N years" figure in a quote, experience word or not."""
    out = set()
    for m in _YEARS_RE.finditer(quote or ""):
        raw = m.group("n").lower()
        n = int(raw) if raw.isdigit() else _WORD_NUM.get(raw)
        if n:
            out.add(n)
    return out


def _languages_in(sentence: str) -> Dict[str, str]:
    found = {}
    for lang, names in _LANG_NAMES.items():
        for m in re.finditer(names, sentence, re.I):
            window = sentence[max(0, m.start() - 50): m.end() + 50]
            for level, rx in _LEVEL_WORDS:
                if re.search(rx, window, re.I):
                    found[lang] = level
                    break
    return found


def regex_extract(job: Dict) -> Dict:
    title = job.get("title", "") or ""
    desc = job.get("description", "") or ""
    crit = _empty()

    title_skills = taxonomy.find_all(title)
    required, nice = set(title_skills), set()
    sections = _sections(desc)

    pos = 0
    for sent in _SENT_SPLIT.split(desc):
        if not sent.strip():
            continue
        start = desc.find(sent, pos)
        pos = start + len(sent) if start >= 0 else pos
        skills = taxonomy.find_all(sent)
        section = _section_at(sections, max(start, 0))
        is_nice = bool(_NICE_MARK.search(sent)) or section == "nice"
        is_req = not is_nice and (bool(_REQ_MARK.search(sent)) or section == "req")

        if skills:
            if is_req:
                required |= skills
            else:
                # Without a clear "requirements" section, an unmarked mention is
                # context (the team's stack), not a hard requirement.
                nice |= skills

        if not is_nice:
            n = _years_in(sent)
            if n is not None and (crit["min_years"] is None or n > crit["min_years"]):
                crit["min_years"] = n
                crit["evidence"]["min_years"] = sent.strip()[:220]
            for lang, level in _languages_in(sent).items():
                if _stronger(level, crit["languages"].get(lang, {}).get("level")):
                    crit["languages"][lang] = {"level": level, "explicit": True}
                    crit["evidence"][f"lang_{lang}"] = sent.strip()[:220]

    # "Nice" never overrides "required".
    crit["required"] = sorted(required)
    crit["nice_to_have"] = sorted(nice - required)

    if _LEAD_TITLE.search(title):
        crit["seniority"] = "lead"
    elif _SENIOR_TITLE.search(title):
        crit["seniority"] = "senior"
    elif _JUNIOR_TITLE.search(title):
        crit["seniority"] = "junior"
    elif _MID_TITLE.search(title):
        crit["seniority"] = "mid"
    if crit["seniority"]:
        crit["evidence"]["seniority"] = title

    text = f"{title} {desc} {job.get('location', '')}".lower()
    if re.search(r"\bhybrid|hybride\b", text):
        crit["work_mode"] = "hybrid"
    elif re.search(r"\bremote\b|t[ée]l[ée]travail|work from home|anywhere", text):
        crit["work_mode"] = "remote"
    elif re.search(r"on[- ]?site|pr[ée]sentiel|in[- ]office|sur site", text):
        crit["work_mode"] = "onsite"

    # An ad written in French implies working French even when unstated.
    # Implicit requirements can create a gap but never a blocking one.
    if job.get("language") == "fr" and "fr" not in crit["languages"]:
        crit["languages"]["fr"] = {"level": "B2", "explicit": False}
    crit["_title_skills"] = sorted(title_skills)
    return crit


def _stronger(a: Optional[str], b: Optional[str]) -> bool:
    if a is None:
        return False
    if b is None:
        return True
    return LANGUAGE_SCALE.index(a) > LANGUAGE_SCALE.index(b)


# ── LLM extractor ────────────────────────────────────────────────

_SYSTEM = """You extract hiring criteria from a job ad. Output ONE JSON object, nothing else.

Rules:
- "required": technologies the ad presents as mandatory (requirements / must have / profil recherché / compétences requises / "you have").
- "nice_to_have": technologies presented as optional (nice to have / a plus / bonus / preferred / idéalement / serait un atout).
- NEVER put the same technology in both lists. If unclear, prefer "nice_to_have".
- Only concrete technologies (languages, frameworks, databases, tools, platforms). No soft skills, no languages like English/French, no degrees.
- Use a key from this list when one fits, else the technology's name as written: {keys}
- For every item give "quote": the exact words from the ad (copy, do not paraphrase).
- "min_years": the minimum years of experience the ad REQUIRES (null if none). Quote the exact sentence.
- "seniority": junior | mid | senior | lead | null, with quote.
- "languages": human languages the ad requires, level as CEFR (A1..C2) or "native"; "fluent"/"courant"/"bilingue" = C1.

JSON shape:
{{"required":[{{"skill":"","quote":""}}],"nice_to_have":[{{"skill":"","quote":""}}],
 "min_years":{{"value":null,"quote":""}},"seniority":{{"value":null,"quote":""}},
 "work_mode":null,"languages":[{{"lang":"fr","level":"C1","quote":""}}]}}"""


def _llm_raw(job: Dict) -> Optional[Dict]:
    from .email_generator import call_llm, EXTRACT_MODEL   # lazy: avoids import cycle
    user = (f"Title: {job.get('title', '')}\nLocation: {job.get('location', '')}\n\n"
            f"Ad:\n{(job.get('description') or '')[:_DESC_LIMIT]}")
    raw = call_llm(_SYSTEM.format(keys=", ".join(taxonomy.ALIASES)), user,
                   model=EXTRACT_MODEL, temperature=0, json_mode=True, max_tokens=900)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\{.*\}", raw or "", re.S)
        return json.loads(m.group(0)) if m else None


def _validate_llm(data: Dict, job: Dict) -> Dict:
    """Keep only what the ad demonstrably says."""
    full = f"{job.get('title', '')} {job.get('description', '')}"
    text_norm = _norm(full)
    out = _empty()
    out["source"] = "llm"

    def _skills(items) -> List[str]:
        keep = []
        for it in items or []:
            term = (it.get("skill") if isinstance(it, dict) else str(it)) or ""
            if not term or taxonomy.is_soft(term):
                continue
            key = taxonomy.normalize(term)
            if key and taxonomy.mentioned(key, full):
                keep.append(key)
            elif not key and _norm(term) in text_norm and len(term) >= 2:
                keep.append("?" + term.strip())          # unknown technology
        return keep

    req = _skills(data.get("required"))
    out["required"] = sorted(set(req))
    out["nice_to_have"] = sorted(set(_skills(data.get("nice_to_have"))) - set(req))

    my = data.get("min_years") or {}
    if isinstance(my, dict) and my.get("value") not in (None, "", 0):
        try:
            n = int(my["value"])
            q = my.get("quote", "")
            if _quote_in(q, text_norm) and n in _year_numbers(q):
                out["min_years"] = n
                out["evidence"]["min_years"] = q
        except (TypeError, ValueError):
            pass

    sen = data.get("seniority") or {}
    if isinstance(sen, dict) and sen.get("value") in ("junior", "mid", "senior", "lead"):
        if _quote_in(sen.get("quote", ""), text_norm):
            out["seniority"] = sen["value"]
            out["evidence"]["seniority"] = sen["quote"]

    for lg in data.get("languages") or []:
        if not isinstance(lg, dict):
            continue
        lang, level = (lg.get("lang") or "").lower()[:2], lg.get("level")
        if level in LANGUAGE_SCALE and lang and _quote_in(lg.get("quote", ""), text_norm):
            if _stronger(level, out["languages"].get(lang, {}).get("level")):
                out["languages"][lang] = {"level": level, "explicit": True}
                out["evidence"][f"lang_{lang}"] = lg["quote"]

    if data.get("work_mode") in ("remote", "hybrid", "onsite"):
        out["work_mode"] = data["work_mode"]
    return out


def _merge(llm: Dict, rx: Dict) -> Dict:
    """LLM decides required vs nice (it reads structure better); regex adds
    anything the LLM missed as nice-to-have, and the stricter experience /
    language reading wins so a requirement is never silently dropped."""
    out = dict(llm)
    out["evidence"] = dict(rx["evidence"], **llm["evidence"])
    # Title skills are the role itself: required even if the LLM said "nice".
    required = set(llm["required"]) | set(rx["_title_skills"])
    out["required"] = sorted(required)
    # Anything the regex saw that the LLM left out is kept, but only as
    # nice-to-have: a missed mention must never become a blocking gap.
    out["nice_to_have"] = sorted((set(llm["nice_to_have"]) | set(rx["required"])
                                  | set(rx["nice_to_have"])) - required)
    if rx["min_years"] and (not llm["min_years"] or rx["min_years"] > llm["min_years"]):
        out["min_years"] = rx["min_years"]
        out["evidence"]["min_years"] = rx["evidence"]["min_years"]
    if not llm["seniority"]:
        out["seniority"] = rx["seniority"]
    langs = dict(rx["languages"])
    for lang, v in llm["languages"].items():
        if lang not in langs or _stronger(v["level"], langs[lang]["level"]) or not langs[lang]["explicit"]:
            langs[lang] = v
    out["languages"] = langs
    out["work_mode"] = llm["work_mode"] or rx["work_mode"]
    out["_title_skills"] = rx["_title_skills"]
    out["source"] = "llm+regex"
    return out


def extract(job: Dict, use_llm: bool = True) -> Dict:
    rx = regex_extract(job)
    if not use_llm:
        return rx
    try:
        data = _llm_raw(job)
    except Exception as e:
        logger.info("LLM extraction failed (%s) — regex only", e)
        data = None
    if not data:
        return rx
    return _merge(_validate_llm(data, job), rx)
