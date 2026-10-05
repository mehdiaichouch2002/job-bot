"""Networking outreach: caps, office hours, dedupe, follow-up, stop on reply.
Run: python -m unittest discover tests"""
import csv
import os
from datetime import datetime, timedelta
from unittest import mock

import job_bot.database as db
import job_bot.outreach as outreach
from tests.test_pipeline import TempDB

MONDAY_10 = datetime(2026, 9, 21, 10, 0)


class OutreachTest(TempDB):
    def setUp(self):
        super().setUp()
        self.csv = os.path.join(self.dir, "targets.csv")
        self.sent = []
        self.p2 = [
            mock.patch.object(outreach, "OUTREACH_ENABLED", True),   # off by default in tests
            mock.patch.object(outreach, "OUTREACH_DAILY_MAX", 5),     # independent of the local .env
            mock.patch.object(outreach, "OUTREACH_PER_RUN", 1),
            mock.patch.object(outreach, "OUTREACH_TARGETS_CSV", self.csv),
            mock.patch("job_bot.outreach.import_targets.__defaults__", (self.csv,)),
            mock.patch("job_bot.email_finder._valid_email", lambda e: "@" in (e or "")),
            mock.patch("job_bot.email_finder._domain_has_mx", lambda d: True),
            mock.patch("job_bot.email_sender.send_email", self._fake_send),
        ]
        for p in self.p2:
            p.start()
        db.init_db()

    def tearDown(self):
        for p in self.p2:
            p.stop()
        super().tearDown()

    def _fake_send(self, to, subject, body, pdf_path=None, dry_run=False):
        self.sent.append({"to": to, "subject": subject, "body": body, "pdf": pdf_path, "dry": dry_run})
        return True

    def targets(self, *rows):
        with open(self.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(outreach._FIELDS)
            for r in rows:
                w.writerow([r.get(k, "") for k in outreach._FIELDS])

    def people(self, n, **kw):
        return [dict({"name": f"Person{i} Last", "email": f"person{i}.last@agency{i}.com",
                      "company": f"Agency{i}", "lang": "en", "segment": "magento"}, **kw)
                for i in range(n)]

    def test_one_per_run_and_daily_cap(self):
        self.targets(*self.people(8))
        for hour in range(9, 18):
            outreach.run_outreach(now=MONDAY_10.replace(hour=hour))
        self.assertEqual(len(self.sent), 5)                       # OUTREACH_DAILY_MAX
        self.assertEqual(len({s["to"] for s in self.sent}), 5)     # never twice the same person
        self.assertTrue(all(s["pdf"] is None for s in self.sent))  # no CV attached

    def test_office_hours_and_weekend(self):
        self.targets(*self.people(2))
        outreach.run_outreach(now=MONDAY_10.replace(hour=7))
        outreach.run_outreach(now=datetime(2026, 9, 26, 11, 0))   # Saturday
        self.assertEqual(self.sent, [])

    def test_already_contacted_person_is_skipped(self):
        from job_bot import contacts
        self.targets({"name": "Jane Roe", "email": "jane.roe@shop.io", "company": "Shop", "lang": "en"})
        contacts.record_contact({"contact_email": "jane.roe@shop.io", "company": "Shop", "title": "Dev"}, "x")
        r = outreach.run_outreach(now=MONDAY_10)
        self.assertEqual((r["sent"], r["skipped"]), (0, 1))
        self.assertEqual(outreach.status_counts(), {"skipped": 1})

    def test_followup_once_then_never_again(self):
        self.targets(*self.people(1))
        outreach.run_outreach(now=MONDAY_10)
        outreach.run_outreach(now=MONDAY_10 + timedelta(days=3))            # too early
        outreach.run_outreach(now=MONDAY_10 + timedelta(days=8))            # follow-up
        outreach.run_outreach(now=MONDAY_10 + timedelta(days=16))           # nothing more
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(self.sent[1]["subject"].startswith("Re: "))
        self.assertIn("won't write again", self.sent[1]["body"])

    def test_reply_stops_the_follow_up(self):
        self.targets(*self.people(1))
        outreach.run_outreach(now=MONDAY_10)
        db.mark_email_replied("person0.last@agency0.com", "positive")      # inbox feedback
        outreach.run_outreach(now=MONDAY_10 + timedelta(days=8))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(outreach.status_counts(), {"replied": 1})

    def test_replies_are_tracked_by_inbox_feedback(self):
        self.targets(*self.people(1))
        outreach.run_outreach(now=MONDAY_10)
        self.assertIn("person0.last@agency0.com", db.get_sent_emails())

    def test_dry_run_records_nothing(self):
        self.targets(*self.people(1))
        outreach.run_outreach(dry_run=True, now=MONDAY_10)
        self.assertTrue(self.sent[0]["dry"])
        self.assertEqual(outreach.status_counts(), {"queued": 1})

    def test_messages_pass_honesty_checks(self):
        for lang in ("en", "fr"):
            for seg in ("magento", "canada"):
                t = {"name": "Jane Roe", "email": "jane.roe@shop.io", "company": "Shop", "lang": lang,
                     "segment": seg, "note": "I liked your Hyvä migration for Brand X",
                     "first_sent_at": "2026-09-21T10:00:00", "subject": "x"}
                for msg in (outreach.first_message(t), outreach.followup_message(t)):
                    with self.subTest(lang=lang, seg=seg):
                        self.assertEqual(outreach._validate(t, msg), [], msg["body"])
                        self.assertIn("Mehdi Aichouch", msg["body"])
        body = outreach.first_message(dict(t, lang="en", segment="magento"))["body"]
        self.assertIn("If you'd rather not hear from me again", body)
        self.assertIn("3 years", body)

    def test_team_inbox_that_asks_for_cvs(self):
        self.targets({"name": "team", "email": "careers@atwix.com", "company": "Atwix",
                      "lang": "en", "segment": "apply,remote"})
        outreach.run_outreach(now=MONDAY_10)
        s = self.sent[0]
        self.assertTrue(s["body"].startswith("Hello Atwix team,"))
        self.assertEqual(s["subject"], "Open application — Magento 2 developer")
        self.assertIn("my CV is attached", s["body"])
        self.assertIn("Magento2_Developer_EN", os.path.basename(s["pdf"]))
        outreach.run_outreach(now=MONDAY_10 + timedelta(days=8))
        self.assertTrue(self.sent[1]["body"].startswith("Hello Atwix team,"))
        self.assertIsNone(self.sent[1]["pdf"])                     # no second CV on the follow-up

    def test_team_inbox_messages_pass_checks_fr(self):
        t = {"name": "team", "email": "rh@x.fr", "company": "Synolia", "lang": "fr", "segment": "apply",
             "note": "", "first_sent_at": "2026-09-21T10:00:00", "subject": "x"}
        for msg in (outreach.first_message(t), outreach.followup_message(t)):
            self.assertEqual(outreach._validate(t, msg), [], msg["body"])
            self.assertTrue(msg["body"].startswith("Bonjour l'équipe Synolia,"))

    def test_moroccan_fullstack_company(self):
        self.targets({"name": "team", "email": "recrutement@webco.ma", "company": "WebCo",
                      "lang": "fr", "segment": "apply,fullstack,maroc",
                      "note": "J'ai vu votre annonce pour des développeurs web à Casablanca"})
        outreach.run_outreach(now=MONDAY_10)
        s = self.sent[0]
        self.assertEqual(s["subject"], "Candidature spontanée — Développeur Full-Stack PHP / Laravel / React")
        self.assertIn("Je suis développeur full-stack PHP / Laravel / React basé à Fès", s["body"])
        self.assertIn("mobile partout au Maroc", s["body"])
        self.assertIn("FullStack_FR", os.path.basename(s["pdf"]))
        t = {"name": "team", "company": "WebCo", "lang": "fr", "segment": "apply,fullstack,maroc",
             "note": "J'ai vu votre annonce pour des développeurs web à Casablanca"}
        self.assertEqual(outreach._validate(t, outreach.first_message(t)), [])

    def test_dishonest_note_is_blocked(self):
        self.targets({"name": "Jane Roe", "email": "jane.roe@shop.io", "company": "Shop", "lang": "en",
                      "note": "I have 6 years of Symfony expertise"})
        outreach.run_outreach(now=MONDAY_10)
        self.assertEqual(self.sent, [])

    def test_csv_import_is_idempotent_and_needs_a_name(self):
        self.targets(*self.people(2), {"name": "", "email": "x@y.com"})
        self.assertEqual(outreach.import_targets(self.csv), 2)
        self.assertEqual(outreach.import_targets(self.csv), 0)


if __name__ == "__main__":
    import unittest
    unittest.main()
