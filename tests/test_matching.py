"""Extraction + matching rules. Run: python -m unittest discover tests"""
import json
import unittest
from unittest import mock

from job_bot import taxonomy, profile
from job_bot.extractor import extract, regex_extract
from job_bot.matcher import evaluate, BON_MATCH, CONTACT_RESEAU, A_EVITER
from tests import fixtures as fx


def _eval(ad, **kw):
    j = fx.job(ad)
    return evaluate(j, extract(j, use_llm=False), **kw)


class TaxonomyTest(unittest.TestCase):
    def test_every_skill_is_classified(self):
        # Nothing may fall through: in the profile, transferable, or not transferable.
        for key in taxonomy.ALIASES:
            self.assertTrue(profile.level_of(key) or key in taxonomy.TRANSFERABLE
                            or key in taxonomy.NON_TRANSFERABLE, key)
            self.assertIn(key, taxonomy.LABELS, key)

    def test_transfer_sources_are_core_and_partial(self):
        for target, (src, coef) in taxonomy.TRANSFERABLE.items():
            self.assertEqual(profile.level_of(src), "core", target)
            self.assertLess(coef, 1.0, target)

    def test_profile_rules_from_brief(self):
        for missing in ("symfony", "typescript", "nextjs", "kubernetes", "postgresql",
                        "react_native", "terraform"):
            self.assertIsNone(profile.level_of(missing), missing)
        for secondary in ("java", "csharp", "spring_boot", "java_ee", "python"):
            self.assertEqual(profile.level_of(secondary), "secondary")
        self.assertIn("kubernetes", taxonomy.NON_TRANSFERABLE)
        self.assertIn("react_native", taxonomy.NON_TRANSFERABLE)
        self.assertEqual(taxonomy.TRANSFERABLE["symfony"][0], "laravel")
        self.assertEqual(taxonomy.TRANSFERABLE["typescript"][0], "javascript")

    def test_aliases(self):
        self.assertEqual(taxonomy.normalize("Adobe Commerce"), "magento2")
        self.assertEqual(taxonomy.normalize("GitHub Actions"), "ci_cd")
        self.assertEqual(taxonomy.normalize("React Native"), "react_native")
        self.assertEqual(taxonomy.normalize("ReactJS"), "react")
        self.assertEqual(taxonomy.normalize("Apache Kafka"), "kafka")
        self.assertNotIn("java", taxonomy.find_all("JavaScript and TypeScript"))
        self.assertNotIn("react", taxonomy.find_all("React Native developer"))


class RegexExtractionTest(unittest.TestCase):
    def test_required_and_nice_kept_apart(self):
        c = regex_extract(fx.job(fx.MAGENTO_GOOD))
        for s in ("magento2", "php", "mysql", "elasticsearch", "redis", "git"):
            self.assertIn(s, c["required"])
        for s in ("hyva", "symfony", "kubernetes"):
            self.assertIn(s, c["nice_to_have"])
            self.assertNotIn(s, c["required"])
        self.assertEqual(c["min_years"], 3)

    def test_years_need_experience_context(self):
        j = {"title": "PHP Developer", "language": "en",
             "description": "Founded 10 years ago. Requirements: - 4 years of experience in PHP."}
        self.assertEqual(regex_extract(j)["min_years"], 4)

    def test_years_in_nice_sentence_ignored(self):
        j = {"title": "PHP Developer", "language": "en",
             "description": "Requirements: - PHP. Nice to have: - 6+ years of experience would be a plus."}
        self.assertIsNone(regex_extract(j)["min_years"])

    def test_language_and_seniority(self):
        c = regex_extract(fx.job(fx.FRENCH_FLUENT))
        self.assertEqual(c["languages"]["fr"], {"level": "C1", "explicit": True})
        self.assertIn("vue", c["nice_to_have"])
        self.assertEqual(regex_extract(fx.job(fx.SYMFONY_SENIOR))["seniority"], "senior")


class LlmExtractionTest(unittest.TestCase):
    def _run(self, payload, ad=fx.SYMFONY_SENIOR):
        with mock.patch("job_bot.email_generator.call_llm", return_value=json.dumps(payload)):
            return extract(fx.job(ad), use_llm=True)

    def test_hallucinations_are_dropped(self):
        c = self._run({
            "required": [{"skill": "PHP", "quote": "PHP"}, {"skill": "Kubernetes", "quote": "k8s"},
                         {"skill": "Symfony", "quote": "Symfony"}],
            "nice_to_have": [{"skill": "Vue.js", "quote": "Vue.js"}],
            "min_years": {"value": 8, "quote": "8 years of experience"},   # not in the ad
            "seniority": {"value": "lead", "quote": "tech lead wanted"},    # not in the ad
            "languages": [{"lang": "de", "level": "C1", "quote": "fluent German"}],
        })
        self.assertNotIn("kubernetes", c["required"])        # the ad never mentions it
        self.assertIn("symfony", c["required"])
        self.assertEqual(c["min_years"], 5)                  # regex reading wins, 8 rejected
        self.assertEqual(c["seniority"], "senior")           # made-up "lead" rejected
        self.assertNotIn("de", c["languages"])

    def test_llm_cannot_downgrade_title_skill(self):
        c = self._run({"required": [{"skill": "PHP", "quote": "PHP"}],
                       "nice_to_have": [{"skill": "Symfony", "quote": "Symfony"}]})
        self.assertIn("symfony", c["required"])              # in the job title

    def test_unknown_required_tech_is_kept(self):
        ad = dict(fx.MAGENTO_GOOD, description=fx.MAGENTO_GOOD["description"] + " Requirements: Akeneo PIM.")
        c = self._run({"required": [{"skill": "Akeneo PIM", "quote": "Akeneo PIM"}]}, ad)
        self.assertIn("?Akeneo PIM", c["required"])

    def test_llm_failure_falls_back_to_regex(self):
        with mock.patch("job_bot.email_generator.call_llm", side_effect=RuntimeError("429")):
            c = extract(fx.job(fx.MAGENTO_GOOD), use_llm=True)
        self.assertEqual(c["source"], "regex")
        self.assertIn("magento2", c["required"])


class MatcherTest(unittest.TestCase):
    def test_fixture_verdicts(self):
        for ad in fx.ALL:
            with self.subTest(ad["id"]):
                self.assertEqual(_eval(ad)["category"], ad["expect"])

    def test_good_match_breakdown(self):
        ev = _eval(fx.MAGENTO_GOOD)
        self.assertEqual(ev["blocking"], [])
        self.assertEqual(ev["coverage"], 1.0)
        partial = {p["skill"]: p for p in ev["partial"]}
        self.assertEqual(partial["symfony"]["status"], "transferable")
        self.assertEqual(partial["symfony"]["via"], "laravel")
        self.assertFalse(partial["symfony"]["required"])
        # Kubernetes is only nice-to-have: a gap, never blocking.
        k8s = [g for g in ev["gaps"] if g["skill"] == "kubernetes"][0]
        self.assertFalse(k8s["blocking"])

    def test_experience_gap_is_blocking_not_points(self):
        ev = _eval(fx.SYMFONY_SENIOR)
        self.assertEqual([b["type"] for b in ev["blocking"]], ["experience"])
        self.assertEqual(ev["blocking"][0]["required_years"], 5)
        self.assertIn("5+ years", ev["blocking"][0]["evidence"])
        symfony = [p for p in ev["partial"] if p["skill"] == "symfony"][0]
        self.assertLess(symfony["credit"], 1.0)              # never a full match

    def test_senior_title_without_years_blocks(self):
        j = {"title": "Senior Magento Developer", "language": "en",
             "description": "Requirements: - Magento 2 - PHP."}
        ev = evaluate(j, extract(j, use_llm=False))
        self.assertEqual(ev["category"], CONTACT_RESEAU)
        self.assertEqual(ev["blocking"][0]["type"], "experience")

    def test_senior_title_with_met_years_is_soft(self):
        j = {"title": "Senior Magento Developer", "language": "en",
             "description": "Requirements: - 3+ years of experience with Magento 2 - PHP."}
        ev = evaluate(j, extract(j, use_llm=False))
        self.assertEqual(ev["category"], BON_MATCH)

    def test_required_non_transferable_is_blocking(self):
        ev = _eval(fx.MOBILE)
        skills = {b.get("skill") for b in ev["blocking"] if b["type"] == "skill"}
        self.assertTrue({"react_native", "kubernetes"} <= skills)
        self.assertEqual(ev["category"], A_EVITER)

    def test_unknown_required_skill_is_blocking(self):
        crit = {"required": ["magento2", "php", "?Akeneo PIM"], "nice_to_have": [],
                "min_years": None, "seniority": None, "languages": {}, "evidence": {}}
        ev = evaluate({"title": "Magento dev"}, crit)
        self.assertEqual(ev["blocking"][0]["label"], "Akeneo PIM")
        self.assertEqual(ev["category"], CONTACT_RESEAU)

    def test_language(self):
        ev = _eval(fx.FRENCH_FLUENT)
        self.assertEqual(ev["blocking"][0]["type"], "language")
        # An implicit French requirement (ad written in French) is never blocking.
        j = dict(fx.job(fx.FRENCH_FLUENT), description="Profil recherché : - PHP - Laravel - MySQL.")
        ev = evaluate(j, extract(j, use_llm=False))
        self.assertEqual(ev["category"], BON_MATCH)
        self.assertTrue(any(g["skill"] == "lang_fr" for g in ev["gaps"]))

    def test_location_blocker_is_avoid(self):
        ev = _eval(fx.MAGENTO_GOOD, hireable=False, hire_reasons=["onsite in Munich"])
        self.assertEqual(ev["category"], A_EVITER)
        self.assertEqual(ev["reasons"], ["onsite in Munich"])

    def test_secondary_skill_is_partial(self):
        j = {"title": "Java Developer", "language": "en",
             "description": "Requirements: - Java - Spring Boot."}
        ev = evaluate(j, extract(j, use_llm=False))
        self.assertEqual({p["status"] for p in ev["partial"]}, {"secondary"})
        self.assertEqual(ev["category"], CONTACT_RESEAU)          # 40 % coverage

    def test_deterministic(self):
        self.assertEqual(_eval(fx.SYMFONY_SENIOR), _eval(fx.SYMFONY_SENIOR))


if __name__ == "__main__":
    unittest.main()
