"""
profile.py
~~~~~~~~~~
The ONE source of truth about Mehdi for matching and for every generated
message. Nothing else in the codebase may state a skill level, a number of
years or a measured result — it reads it from here.

Figures below are copied from the 23092026 CVs (all four agree), so the email
a recruiter reads and the CV attached to it never contradict each other.
If a CV changes, change it here too.
"""

YEARS_EXPERIENCE = 3

# Skill keys are taxonomy.py canonical keys.
#   core      — used in production, daily. Counts as a full match.
#   secondary — known (studied / side use), not used in production daily.
#               Counts as a partial match and must never be claimed as mastery.
SKILLS = {
    # core — production stack
    "magento2": "core", "php": "core", "laravel": "core", "react": "core",
    "alpinejs": "core", "tailwind": "core", "hyva": "core", "mysql": "core",
    "elasticsearch": "core", "redis": "core", "varnish": "core", "docker": "core",
    "git": "core", "javascript": "core", "rest_api": "core", "knockoutjs": "core",
    "html_css": "core", "linux": "core", "nginx": "core", "composer": "core",
    "oauth_jwt": "core", "ecommerce": "core", "b2b_commerce": "core",
    # secondary — known, not daily production
    "java": "secondary", "csharp": "secondary", "spring_boot": "secondary",
    "java_ee": "secondary", "python": "secondary",
    # basic pipelines only (CV: "stabilised Docker and CI/CD pipelines");
    # complex CI/CD / infra is explicitly NOT in the profile.
    "ci_cd": "secondary",
}

SKILL_WEIGHT = {"core": 1.0, "secondary": 0.4}

# CEFR-style ordering; "native" sits above C2.
LANGUAGE_SCALE = ["A1", "A2", "B1", "B2", "C1", "C2", "native"]
LANGUAGES = {"ar": "native", "en": "B2", "fr": "B1"}

LOCATION = "Fès, Morocco"
CERTIFICATION_IN_PROGRESS = "Adobe Commerce Developer Professional (AD0-E724)"

# Every measurable claim a message may contain. Drafts are checked against
# ALLOWED_NUMBERS (derived below) — a figure that is not here is rejected.
FACTS = [
    {"id": "users",
     "en": "platforms for Carhartt WIP, Anita and Edwin Europe serving 50,000+ monthly users",
     "fr": "plateformes Carhartt WIP, Anita et Edwin Europe (50 000+ utilisateurs par mois)",
     "numbers": ["50,000", "50 000", "50000", "50k"]},
    {"id": "plp",
     "en": "cut product listing load time from 3.2s to 0.8s (Elasticsearch, MySQL, Redis, Varnish)",
     "fr": "temps de chargement des listes produits réduit de 3,2 s à 0,8 s (Elasticsearch, MySQL, Redis, Varnish)",
     "numbers": ["3.2", "3,2", "0.8", "0,8"]},
    {"id": "hyva",
     "en": "Hyvä storefronts (Alpine.js, Tailwind CSS): 40% faster page loads, Google PageSpeed above 95",
     "fr": "vitrines Hyvä (Alpine.js, Tailwind CSS) : chargement 40 % plus rapide, PageSpeed au-dessus de 95",
     "numbers": ["40", "95"]},
    {"id": "extensions",
     "en": "replaced third-party extensions with in-house plugins and observers, cutting extension costs by 30%",
     "fr": "extensions tierces remplacées par des plugins et observers internes : -30 % de coûts",
     "numbers": ["30"]},
    {"id": "refactor",
     "en": "refactored 50,000+ lines of legacy code, cutting production bugs by 45%",
     "fr": "refactorisation de 50 000+ lignes de code legacy : -45 % de bugs en production",
     "numbers": ["50,000", "50 000", "50000", "45"]},
    {"id": "laravel",
     "en": "Laravel 10 internal platform used daily by 25+ employees, 15+ REST endpoints, response times halved",
     "fr": "plateforme interne Laravel 10 utilisée par 25+ employés, 15+ endpoints REST, temps de réponse divisés par deux",
     "numbers": ["25", "15", "10"]},
    {"id": "hr_app",
     "en": "PHP / MySQL HR app removing 70% of manual HR work",
     "fr": "application RH en PHP / MySQL : -70 % d'opérations manuelles",
     "numbers": ["70"]},
]

# Version / identifier numbers that may legitimately appear in any message.
_NEUTRAL_NUMBERS = ["2", "2.4", "8", "10", "1", "724", "2023", "2024", "2025", "2026"]

ALLOWED_NUMBERS = frozenset(
    [str(YEARS_EXPERIENCE)]
    + [n.lower() for f in FACTS for n in f["numbers"]]
    + _NEUTRAL_NUMBERS
)


def level_of(skill: str):
    """'core' | 'secondary' | None"""
    return SKILLS.get(skill)


def facts_block(lang: str) -> str:
    return "\n".join(f"- [{f['id']}] {f[lang if lang in ('en', 'fr') else 'en']}" for f in FACTS)
