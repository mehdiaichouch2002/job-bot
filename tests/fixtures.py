"""Hand-labelled job ads. `expect` is what an honest recruiter would decide."""

MAGENTO_GOOD = {
    "id": "fx-magento", "title": "Magento 2 Developer (Remote)", "company": "Shopcraft",
    "location": "Remote - Worldwide", "country": "", "source": "fixture", "language": "en",
    "url": "https://example.com/magento", "contact_email": "",
    "description": (
        "About us: We are an e-commerce agency building stores for European brands. "
        "Requirements: - 3+ years of experience with Magento 2 - Strong PHP 8 skills "
        "- MySQL, Elasticsearch and Redis - Git. Nice to have: - Hyvä themes - Symfony "
        "- Kubernetes. We offer: fully remote, work from anywhere, flexible hours."
    ),
    "expect": "BON_MATCH",
}

SYMFONY_SENIOR = {
    "id": "fx-symfony", "title": "Senior PHP Symfony Developer", "company": "Fintrail GmbH",
    "location": "Remote (EMEA)", "country": "", "source": "fixture", "language": "en",
    "url": "https://example.com/symfony", "contact_email": "",
    "description": (
        "Your mission: build our payments platform. Requirements: - 5+ years of professional "
        "experience with PHP - Solid Symfony and Doctrine knowledge - PostgreSQL - Docker. "
        "Nice to have: - Vue.js. Benefits: remote-first team across EMEA."
    ),
    "expect": "CONTACT_RESEAU",
}

MOBILE = {
    "id": "fx-mobile", "title": "Full-Stack Developer (React Native)", "company": "Appvia",
    "location": "Remote", "country": "", "source": "fixture", "language": "en",
    "url": "https://example.com/mobile", "contact_email": "",
    "description": (
        "Requirements: - React Native and Expo - TypeScript - Node.js - Kubernetes "
        "- 5 years of experience shipping mobile apps. Benefits: remote."
    ),
    "expect": "A_EVITER",
}

FRENCH_FLUENT = {
    "id": "fx-french", "title": "Développeur PHP Laravel", "company": "Atelier Web",
    "location": "Télétravail, France", "country": "FR", "source": "fixture", "language": "fr",
    "url": "https://example.com/laravel", "contact_email": "",
    "description": (
        "Le poste : vous rejoignez notre équipe produit en télétravail complet. "
        "Profil recherché : - maîtrise de PHP et Laravel - MySQL - Git "
        "- Français courant exigé. Un plus : - Vue.js."
    ),
    "expect": "CONTACT_RESEAU",
}

ALL = [MAGENTO_GOOD, SYMFONY_SENIOR, MOBILE, FRENCH_FLUENT]


def job(fx):
    """A copy without the label, as a scraper would produce it."""
    return {k: v for k, v in fx.items() if k != "expect"}
