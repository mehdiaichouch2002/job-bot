"""Honesty checks on generated messages. Run: python -m unittest discover tests"""
import unittest
from unittest import mock

from job_bot import drafter
from job_bot.drafter import validate, draft_for, draft_cover_letter, prior_sentence
from job_bot.extractor import extract
from job_bot.matcher import evaluate
from job_bot.profile import YEARS_EXPERIENCE
from tests import fixtures as fx


def _case(ad, lang=None):
    j = fx.job(ad)
    if lang:
        j["language"] = lang
    return j, evaluate(j, extract(j, use_llm=False))


class ValidatorTest(unittest.TestCase):
    def setUp(self):
        self.job, self.ev = _case(fx.SYMFONY_SENIOR)

    def v(self, text, kind="application", lang="en"):
        return validate(text, self.ev, kind, lang, {5}, self.job)

    def test_mastery_of_transferable_skill_is_rejected(self):
        self.assertTrue(self.v("I master Symfony and Doctrine."))
        self.assertTrue(self.v("Je maîtrise Symfony au quotidien.", lang="fr"))
        self.assertTrue(self.v("Stack: PHP, Symfony, PostgreSQL."))   # bare list = claim

    def test_ramp_up_phrasing_is_accepted(self):
        self.assertEqual(self.v("My Laravel experience lets me ramp up quickly on Symfony."), [])
        self.assertEqual(self.v("Ma maîtrise de Laravel me permet de monter rapidement en "
                                "compétence sur Symfony.", lang="fr"), [])

    def test_secondary_skill_needs_context(self):
        _, ev = _case({**fx.job(fx.MAGENTO_GOOD), "expect": None})
        self.assertTrue(validate("I build services in Java every day.", ev, "application", "en"))
        self.assertEqual(validate("I studied Java during my degree.", ev, "application", "en"), [])

    def test_numbers_come_from_profile_only(self):
        self.assertTrue(self.v("I cut load times by 60%."))
        self.assertTrue(self.v("I have 4 years of experience."))
        self.assertTrue(self.v("Two years of PHP in production."))
        self.assertTrue(self.v("Platforms with 80,000+ users."))
        self.assertEqual(self.v(f"{YEARS_EXPERIENCE} years of experience; product pages went from "
                                "3.2s to 0.8s and page loads got 40% faster for 50,000+ users."), [])
        self.assertEqual(self.v("Pages de 3,2 s à 0,8 s, 50 000+ utilisateurs, -45 % de bugs.",
                                lang="fr"), [])

    def test_quoting_the_ads_requirement_is_allowed(self):
        self.assertEqual(self.v("The role asks for 5 years of experience and I have 3."), [])
        self.assertTrue(self.v("I have 5 years of experience."))     # a claim, not a quote

    def test_job_title_figures_are_not_claims(self):
        job = dict(self.job, title="PHP Developer 5+ years")
        self.assertEqual(validate("Applying for the PHP Developer 5+ years role.", self.ev,
                                  "application", "en", set(), job), [])

    def test_network_message_must_name_gap_and_ask(self):
        self.assertTrue(self.v("I'd love to join, I know PHP well.", kind="network"))
        ok = ("To be upfront: the role asks for 5 years of experience and I have 3. "
              "Is there a role that would be a better fit?")
        self.assertEqual(self.v(ok, kind="network"), [])


class TemplateTest(unittest.TestCase):
    """The deterministic fallback must itself pass every check, in both languages."""

    def test_templates_pass_validation(self):
        for ad in (fx.MAGENTO_GOOD, fx.SYMFONY_SENIOR, fx.FRENCH_FLUENT):
            for lang in ("en", "fr"):
                with self.subTest(ad=ad["id"], lang=lang), \
                        mock.patch.object(drafter, "llm_available", return_value=False):
                    job, ev = _case(ad, lang)
                    d = draft_for(job, ev)
                    kind = "network" if ev["category"] == "CONTACT_RESEAU" else "application"
                    self.assertEqual(d["source"], "template")
                    self.assertEqual(validate(d["body"], ev, kind, lang,
                                              drafter._extra_years(ev), job), [], d["body"])
                    self.assertIn("Mehdi Aichouch", d["body"])

    def test_network_template_content(self):
        with mock.patch.object(drafter, "llm_available", return_value=False):
            job, ev = _case(fx.SYMFONY_SENIOR)
            d = draft_for(job, ev)
        self.assertEqual(d["kind"], "network")
        self.assertIn("5 years of experience and I have 3", d["body"])
        self.assertIn("better fit", d["body"])
        self.assertLess(len(d["body"].split()), 120)                 # short, not an application

    def test_transferable_wording_in_application(self):
        with mock.patch.object(drafter, "llm_available", return_value=False):
            job, ev = _case(fx.SYMFONY_SENIOR)
            ev = dict(ev, blocking=[], category="BON_MATCH")          # pretend it matched
            body = draft_for(job, ev)["body"]
        self.assertIn("my Laravel experience lets me ramp up on it quickly", body)
        self.assertNotRegex(body.lower(), r"i (know|master|use) symfony")

    def test_avoid_gets_no_message(self):
        job, ev = _case(fx.MOBILE)
        self.assertIsNone(draft_for(job, ev))

    def test_cover_letter_template(self):
        with mock.patch.object(drafter, "llm_available", return_value=False):
            job, ev = _case(fx.MAGENTO_GOOD)
            text = draft_cover_letter(job, ev)["text"]
        self.assertIn(f"{YEARS_EXPERIENCE} years of experience", text)


class LlmLoopTest(unittest.TestCase):
    GOOD = "SUBJECT: Magento role\nBODY:\nI work daily with Magento 2 and PHP; product pages went from 3.2s to 0.8s."
    BAD = "SUBJECT: Magento role\nBODY:\nWith 5 years of Symfony expertise I cut costs by 60%."

    def _draft(self, replies):
        job, ev = _case(fx.MAGENTO_GOOD)
        with mock.patch.object(drafter, "llm_available", return_value=True), \
                mock.patch.object(drafter, "call_llm", side_effect=replies) as m:
            return draft_for(job, ev), m

    def test_valid_llm_draft_is_used(self):
        d, m = self._draft([self.GOOD])
        self.assertEqual(d["source"], "llm")
        self.assertIn("3.2s to 0.8s", d["body"])
        self.assertEqual(m.call_count, 1)

    def test_rejected_draft_is_retried_with_the_violations(self):
        d, m = self._draft([self.BAD, self.GOOD])
        self.assertEqual(d["source"], "llm")
        self.assertEqual(m.call_count, 2)
        self.assertIn("60%", m.call_args_list[1][0][1])              # told what was wrong
        self.assertTrue(d["rejected_issues"])

    def test_twice_invalid_falls_back_to_template(self):
        d, _ = self._draft([self.BAD, self.BAD])
        self.assertEqual(d["source"], "template")
        self.assertNotIn("60%", d["body"])

    def test_llm_greeting_and_signature_are_replaced(self):
        raw = "SUBJECT: x\nBODY:\nDear team,\nI work daily with Magento 2.\nBest regards,\nMehdi"
        d, _ = self._draft([raw])
        self.assertTrue(d["body"].startswith("Hello,"))
        self.assertEqual(d["body"].count("Best regards,"), 1)


class PriorContactSentenceTest(unittest.TestCase):
    def test_person_and_company(self):
        job = {"company": "Shopcraft"}
        person = {"match": "person", "last_at": "2026-06-12T10:00:00", "last_job_title": "PHP Developer"}
        self.assertIn("12/06/2026", prior_sentence(person, job, "fr"))
        self.assertIn("PHP Developer", prior_sentence(person, job, "en"))
        company = dict(person, match="company", outcome="auto")
        self.assertIn("Shopcraft", prior_sentence(company, job, "fr"))
        # Silent company-level send: maybe never received — not claimed.
        self.assertEqual(prior_sentence(dict(company, outcome="silence"), job, "fr"), "")

    def test_prior_sentence_goes_after_greeting(self):
        prior = {"match": "person", "last_at": "2026-06-12T10:00:00", "last_job_title": "PHP Developer"}
        with mock.patch.object(drafter, "llm_available", return_value=False):
            job, ev = _case(fx.MAGENTO_GOOD)
            body = draft_for(job, ev, prior)["body"]
        lines = body.split("\n\n")
        self.assertEqual(lines[0], "Hello,")
        self.assertIn("I wrote to you on 2026-06-12", lines[1])


if __name__ == "__main__":
    unittest.main()
