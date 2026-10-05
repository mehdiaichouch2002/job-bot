"""
taxonomy.py
~~~~~~~~~~~
Skill reference: how each technology is written in job ads (aliases), which
missing skills are transferable from something in the profile (and how much
they count), and which are not transferable at all.

This table is the matcher's judgement, written down so it can be read and
corrected by a human — the LLM only extracts, it never decides what transfers.

Invariant (checked by tests): every skill here is either in the profile,
in TRANSFERABLE, or in NON_TRANSFERABLE. Nothing falls through the cracks.
"""
import re
from typing import Optional

# canonical key -> regex matched against lowercase text
ALIASES = {
    # ── profile core ──
    "magento2":      r"\bmagento(?:\s*2)?\b|adobe\s+commerce",
    "hyva":          r"\bhyv[aä]\b",
    "php":           r"\bphp\s*\d*\b",
    "laravel":       r"\blaravel\b",
    "react":         r"\breact(?:\.?js)?\b(?!\s*native)",
    "alpinejs":      r"\balpine(?:\.?js)?\b",
    "tailwind":      r"\btailwind(?:\s*css)?\b",
    "javascript":    r"\bjavascript\b|\bes6\+?\b|\becmascript\b|\bvanilla\s+js\b",
    "mysql":         r"\bmysql\b|\bmariadb\b",
    "elasticsearch": r"\belastic\s*search\b|\bopensearch\b",
    "redis":         r"\bredis\b",
    "varnish":       r"\bvarnish\b",
    "docker":        r"\bdocker(?:\s*compose)?\b",
    "git":           r"\bgit\b|\bgithub\b|\bgitlab\b|\bbitbucket\b",
    "rest_api":      r"\brestful\b|\brest\s*apis?\b|\bapis?\s+rest\b|\brest\s+services?\b",
    "knockoutjs":    r"\bknockout(?:\.?js)?\b",
    "html_css":      r"\bhtml5?\b|\bcss3?\b|\bsass\b|\bscss\b|\bless\b(?=\s*[,/)])",
    "linux":         r"\blinux\b|\bubuntu\b|\bdebian\b|\bbash\b",
    "nginx":         r"\bnginx\b|\bapache\b(?!\s+(?:kafka|spark|camel|flink|solr))",
    "composer":      r"\bcomposer\b",
    "oauth_jwt":     r"\boauth\s*2?\b|\bjwt\b|\bsso\b|\bopenid\b",
    "ecommerce":     r"\be-?commerce\b",
    "b2b_commerce":  r"\bb2b\b",
    # ── profile secondary ──
    "java":          r"\bjava\b(?!\s*script)",
    "csharp":        r"(?<!\w)c#|\.net\b|\bdotnet\b|\basp\.net\b",
    "spring_boot":   r"\bspring(?:\s*boot)?\b",
    "java_ee":       r"\bjava\s*ee\b|\bjakarta\s*ee\b|\bj2ee\b",
    "python":        r"\bpython\b",
    "ci_cd":         r"\bci\s*/\s*cd\b|\bcontinuous\s+integration\b|\bgithub\s+actions\b|\bgitlab\s*ci\b|\bjenkins\b|\bint[ée]gration\s+continue\b",
    # ── not in profile: transferable ──
    "symfony":       r"\bsymfony\b",
    "typescript":    r"\btypescript\b|\bts\b(?=\s*[,/)])",
    "nextjs":        r"\bnext\.?js\b",
    "vue":           r"\bvue(?:\.?js)?\b|\bnuxt\b",
    "angular":       r"\bangular(?:js)?\b",
    "nodejs":        r"\bnode(?:\.?js)?\b|\bexpress\.?js\b|\bnestjs\b",
    "postgresql":    r"\bpostgres(?:ql)?\b",
    "mongodb":       r"\bmongo(?:db)?\b",
    "graphql":       r"\bgraphql\b",
    "shopware":      r"\bshopware\b",
    "prestashop":    r"\bprestashop\b",
    "woocommerce":   r"\bwoocommerce\b",
    "shopify":       r"\bshopify\b",
    "sylius":        r"\bsylius\b",
    "wordpress":     r"\bwordpress\b",
    "drupal":        r"\bdrupal\b",
    "livewire":      r"\blivewire\b|\binertia(?:\.?js)?\b",
    "phpunit":       r"\bphpunit\b|\bpest\b|\bunit\s+tests?\b|\btests?\s+unitaires\b|\btdd\b",
    "jest":          r"\bjest\b|\bcypress\b|\bplaywright\b",
    "aws":           r"\baws\b|\bamazon\s+web\s+services\b",
    "gcp":           r"\bgcp\b|\bgoogle\s+cloud\b",
    "azure":         r"\bazure\b",
    "rabbitmq":      r"\brabbitmq\b|\bmessage\s+queues?\b",
    "kafka":         r"\bkafka\b",
    "api_platform":  r"\bapi\s+platform\b",
    "doctrine":      r"\bdoctrine\b",
    # ── not in profile: NOT transferable ──
    "kubernetes":    r"\bkubernetes\b|\bk8s\b|\bhelm\b|\bopenshift\b",
    "terraform":     r"\bterraform\b|\bansible\b|\bpulumi\b|\bcloudformation\b",
    "react_native":  r"\breact\s*native\b|\bexpo\b",
    "mobile_native": r"\bswift\b|\bkotlin\b|\bflutter\b|\bdart\b|\bios\s+develop|\bandroid\s+develop|\bobjective-c\b",
    "golang":        r"\bgolang\b|\bgo\s+(?:developer|engineer|lang)\b",
    "rust":          r"\brust\b",
    "ruby":          r"\bruby\b|\brails\b",
    "scala":         r"\bscala\b",
    "elixir":        r"\belixir\b|\bphoenix\b",
    "salesforce":    r"\bsalesforce\b|\bsfcc\b|\bdemandware\b",
    "sap":           r"\bsap\b|\bhybris\b",
    "data_ml":       r"\bmachine\s+learning\b|\bpytorch\b|\btensorflow\b|\bspark\b|\bdata\s+engineer",
    "cpp":           r"\bc\+\+",
}

_COMPILED = {k: re.compile(v, re.I) for k, v in ALIASES.items()}

# missing skill -> (profile skill it builds on, credit 0..1). Never 1.0: a
# transferable skill is a partial match by definition.
TRANSFERABLE = {
    "symfony":      ("laravel", 0.5),
    "typescript":   ("javascript", 0.5),
    "nextjs":       ("react", 0.5),
    "vue":          ("react", 0.4),
    "angular":      ("react", 0.3),
    "nodejs":       ("javascript", 0.3),
    "postgresql":   ("mysql", 0.6),
    "mongodb":      ("mysql", 0.3),
    "graphql":      ("rest_api", 0.5),
    "shopware":     ("magento2", 0.4),
    "prestashop":   ("magento2", 0.4),
    "woocommerce":  ("php", 0.4),
    "shopify":      ("magento2", 0.3),
    "sylius":       ("magento2", 0.3),
    "wordpress":    ("php", 0.4),
    "drupal":       ("php", 0.3),
    "livewire":     ("laravel", 0.6),
    "phpunit":      ("php", 0.5),
    "jest":         ("javascript", 0.4),
    "aws":          ("docker", 0.3),
    "gcp":          ("docker", 0.2),
    "azure":        ("docker", 0.2),
    "rabbitmq":     ("redis", 0.3),
    "kafka":        ("redis", 0.2),
    "api_platform": ("rest_api", 0.4),
    "doctrine":     ("laravel", 0.4),
}

NON_TRANSFERABLE = {
    "kubernetes", "terraform", "react_native", "mobile_native", "golang", "rust",
    "ruby", "scala", "elixir", "salesforce", "sap", "data_ml", "cpp",
}

# Human-readable names for messages and reports.
LABELS = {
    "magento2": "Magento 2", "hyva": "Hyvä", "php": "PHP", "laravel": "Laravel",
    "react": "React", "alpinejs": "Alpine.js", "tailwind": "Tailwind CSS",
    "javascript": "JavaScript", "mysql": "MySQL", "elasticsearch": "Elasticsearch",
    "redis": "Redis", "varnish": "Varnish", "docker": "Docker", "git": "Git",
    "rest_api": "REST APIs", "knockoutjs": "Knockout.js", "html_css": "HTML/CSS",
    "linux": "Linux", "nginx": "Nginx", "composer": "Composer", "oauth_jwt": "OAuth2/JWT",
    "ecommerce": "e-commerce", "b2b_commerce": "B2B commerce", "java": "Java",
    "csharp": "C#/.NET", "spring_boot": "Spring Boot", "java_ee": "Java EE",
    "python": "Python", "ci_cd": "CI/CD", "symfony": "Symfony", "typescript": "TypeScript",
    "nextjs": "Next.js", "vue": "Vue.js", "angular": "Angular", "nodejs": "Node.js",
    "postgresql": "PostgreSQL", "mongodb": "MongoDB", "graphql": "GraphQL",
    "shopware": "Shopware", "prestashop": "PrestaShop", "woocommerce": "WooCommerce",
    "shopify": "Shopify", "sylius": "Sylius", "wordpress": "WordPress", "drupal": "Drupal",
    "livewire": "Livewire/Inertia", "phpunit": "PHPUnit / unit testing", "jest": "Jest/Cypress",
    "aws": "AWS", "gcp": "Google Cloud", "azure": "Azure", "rabbitmq": "RabbitMQ",
    "kafka": "Kafka", "api_platform": "API Platform", "doctrine": "Doctrine",
    "kubernetes": "Kubernetes", "terraform": "Terraform/IaC", "react_native": "React Native/Expo",
    "mobile_native": "native mobile (iOS/Android)", "golang": "Go", "rust": "Rust",
    "ruby": "Ruby/Rails", "scala": "Scala", "elixir": "Elixir", "salesforce": "Salesforce",
    "sap": "SAP", "data_ml": "data / machine learning", "cpp": "C++",
}

# Non-technical words an extractor may return as "skills" — ignored, never blocking.
_SOFT = re.compile(
    r"\b(communication|team\s*work|teamwork|autonom|rigueur|rigor|english|anglais|french|fran[cç]ais"
    r"|agile|scrum|kanban|jira|problem[- ]solving|curiosit|esprit|mindset|ownership|passion"
    r"|degree|dipl[oô]me|bac\s*\+|bachelor|master|experience|exp[ée]rience|years?|ans"
    r"|code\s+review|clean\s+code|solid|design\s+patterns?|oop|poo|mvc|microservices?|api)\b",
    re.I,
)


def label(skill: str) -> str:
    return LABELS.get(skill, skill)


def normalize(term: str) -> Optional[str]:
    """Map a free-text technology name to its canonical key, or None."""
    t = (term or "").strip().lower()
    if not t:
        return None
    if t in ALIASES:
        return t
    # Longest match wins: "GitHub Actions" is CI/CD, not Git.
    best, best_len = None, 0
    for key, rx in _COMPILED.items():
        m = rx.search(t)
        if m and len(m.group(0)) > best_len:
            best, best_len = key, len(m.group(0))
    return best


def is_soft(term: str) -> bool:
    return bool(_SOFT.search(term or ""))


def find_all(text: str) -> set:
    """Every taxonomy skill mentioned anywhere in text."""
    t = (text or "").lower()
    return {k for k, rx in _COMPILED.items() if rx.search(t)}


def mentioned(skill: str, text: str) -> bool:
    rx = _COMPILED.get(skill)
    return bool(rx and rx.search((text or "").lower()))
