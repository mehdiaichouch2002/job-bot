"""Contact history + the full pipeline on a temp DB, with sending faked.
Run: python -m unittest discover tests"""
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

import job_bot.config as config
import job_bot.contacts as contacts
import job_bot.database as db
import job_bot.drafter as drafter
import job_bot.main as main
import job_bot.packet as packet
from tests import fixtures as fx


class TempDB(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        path = os.path.join(self.dir, "jobs.db")
        self.patches = [mock.patch.object(m, "DB_PATH", path) for m in (db, contacts, config)]
        self.patches.append(mock.patch.object(packet, "APPLICATIONS_DIR", os.path.join(self.dir, "apps")))
        # Pipeline tests never run outreach (it reads the real target list);
        # OutreachTest turns it back on against a temp CSV.
        import job_bot.outreach as outreach
        self.patches.append(mock.patch.object(outreach, "OUTREACH_ENABLED", False))
        for p in self.patches:
            p.start()
        self.path = path
        # Guard: no test may ever touch the real database.
        real = os.path.abspath(os.path.join(os.path.dirname(config.__file__), "..", "data", "jobs.db"))
        assert os.path.abspath(db.DB_PATH) != real

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def sql(self, q, *a):
        c = sqlite3.connect(self.path)
        rows = c.execute(q, a).fetchall()
        c.commit()
        c.close()
        return rows

    def legacy_send(self, email, company, title, days_ago, name=None, **flags):
        at = (datetime.now() - timedelta(days=days_ago)).isoformat()
        self.sql("""INSERT INTO jobs (id, title, company, found_at, status, applied_via, contact_email,
                    contact_name, email_sent_at, email_subject, bounced, replied, reply_type)
                    VALUES (?, ?, ?, ?, 'applied', 'email', ?, ?, ?, 'Application', ?, ?, ?)""",
                 f"old-{email}", title, company, at, email, name, at,
                 flags.get("bounced", 0), flags.get("replied", 0), flags.get("reply_type"))


class ContactsTest(TempDB):
    def test_backfill_and_lookup(self):
        db.init_db()
        self.legacy_send("jane@shopcraft.io", "Shopcraft GmbH", "PHP Developer", 90, "Jane Roe")
        self.legacy_send("careers@other.com", "Other", "Dev", 90, bounced=1)
        self.sql("DELETE FROM contacts")
        self.assertEqual(contacts.backfill_if_empty(), 2)
        self.assertEqual(contacts.backfill_if_empty(), 0)         # only once

        by_person = contacts.find_prior({"company": "x", "contact_email": "Jane@Shopcraft.io"})
        self.assertEqual(by_person["match"], "person")
        by_company = contacts.find_prior({"company": "Shopcraft", "contact_email": ""})
        self.assertEqual(by_company["match"], "company")           # "GmbH" normalised away
        by_domain = contacts.find_prior({"company": "SC Labs", "contact_email": "tom@shopcraft.io"})
        self.assertEqual(by_domain["match"], "company")
        self.assertIsNone(contacts.find_prior({"company": "Nobody", "contact_email": "a@gmail.com"}))
        self.assertIn("Déjà sollicité", contacts.prior_summary(by_person))

    def test_init_db_seeds_existing_database(self):
        # The real path: an old DB with sends but no contacts table yet.
        db.init_db()
        self.legacy_send("jane@shopcraft.io", "Shopcraft", "PHP Developer", 90, "Jane Roe")
        self.sql("DROP TABLE contacts")
        db.init_db()
        self.assertEqual(self.sql("SELECT email, company_key FROM contacts"),
                         [("jane@shopcraft.io", "shopcraft")])

    def test_auto_send_policy(self):
        now = datetime.now().isoformat()
        old = (datetime.now() - timedelta(days=60)).isoformat()
        ok = contacts.may_auto_send
        self.assertTrue(ok(None)[0])
        self.assertFalse(ok({"match": "person", "last_at": now, "outcome": "silence"})[0])
        self.assertTrue(ok({"match": "person", "last_at": old, "outcome": "silence"})[0])
        self.assertTrue(ok({"match": "company", "last_at": now, "outcome": "silence"})[0])
        self.assertFalse(ok({"match": "company", "last_at": old, "outcome": "rejection"})[0])
        self.assertFalse(ok({"match": "person", "last_at": old, "outcome": "bounce"})[0])

    def test_feedback_updates_outcome(self):
        db.init_db()
        contacts.record_contact({"contact_email": "a@b.com", "company": "B", "title": "Dev"}, "Hi")
        db.mark_email_replied("a@b.com", "rejection")
        self.assertEqual(self.sql("SELECT outcome FROM contacts")[0][0], "rejection")


class PipelineTest(TempDB):
    CONTACTS = {"Shopcraft": ("jane.doe@shopcraft.io", "Jane Doe")}

    def run_pipeline(self, ads, dry=False, contacts_map=None):
        sent = []
        contacts_map = self.CONTACTS if contacts_map is None else contacts_map

        class Scraper:
            SOURCE_NAME = "fixture"

            def fetch_jobs(self, kw):
                return [fx.job(a) for a in ads]

        def enrich(jobs, budget_sec=None):
            for j in jobs:
                if j["company"] in contacts_map:
                    j["contact_email"], j["contact_name"] = contacts_map[j["company"]]

        def fake_send(to, subject, body, pdf_path=None, dry_run=False):
            sent.append({"to": to, "subject": subject, "body": body,
                         "cv": os.path.basename(pdf_path or ""), "dry": dry_run})
            return True

        with mock.patch.object(main, "_SCRAPERS", [Scraper()]), \
                mock.patch.object(main, "process_inbox", lambda: {"error": "test"}), \
                mock.patch.object(main, "llm_available", return_value=False), \
                mock.patch.object(drafter, "llm_available", return_value=False), \
                mock.patch.object(main, "enrich_jobs_with_emails", enrich), \
                mock.patch.object(main, "_valid_email", lambda e: "@" in (e or "")), \
                mock.patch.object(main, "send_email", fake_send), \
                mock.patch.object(main, "send_digest", lambda c, dry_run=False: True), \
                mock.patch.object(main, "check_listing", lambda j: {"available": True, "easy_apply": False}), \
                mock.patch.object(main.time, "sleep", lambda s: None):
            result = main.run_pipeline(dry_run=dry)
        return result, sent

    def row(self, job_id):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        r = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        c.close()
        return dict(r) if r else None

    def test_end_to_end(self):
        result, sent = self.run_pipeline(fx.ALL)
        self.assertEqual((result["bon_match"], result["contact_reseau"], result["a_eviter"]), (1, 2, 1))

        # BON_MATCH with a named contact → emailed, with the Magento CV, recorded as contact.
        self.assertEqual([s["to"] for s in sent], ["jane.doe@shopcraft.io"])
        self.assertIn("Magento2_Developer_EN", sent[0]["cv"])
        self.assertTrue(sent[0]["body"].startswith("Hi Jane,"))
        good = self.row("fx-magento")
        self.assertEqual((good["status"], good["category"]), ("applied", "BON_MATCH"))
        self.assertEqual(self.sql("SELECT email FROM contacts")[0][0], "jane.doe@shopcraft.io")

        # CONTACT_RESEAU → packet with a short network message naming the gap, never emailed.
        net = self.row("fx-symfony")
        self.assertEqual((net["status"], net["category"]), ("packet_ready", "CONTACT_RESEAU"))
        files = os.listdir(net["packet_path"])
        self.assertIn("network_message.txt", files)
        self.assertNotIn("cover_letter.txt", files)
        msg = open(os.path.join(net["packet_path"], "network_message.txt"), encoding="utf-8").read()
        self.assertIn("5 years of experience and I have 3", msg)
        brief = open(os.path.join(net["packet_path"], "application.md"), encoding="utf-8").read()
        self.assertIn("ÉCARTS BLOQUANTS", brief)
        self.assertIn("Partiel : ", brief)

        # À ÉVITER → no message, no packet, reasons stored.
        bad = self.row("fx-mobile")
        self.assertEqual((bad["status"], bad["category"], bad["packet_path"]), ("avoid", "A_EVITER", None))
        self.assertIn("Kubernetes", " ".join(json.loads(bad["evaluation"])["reasons"]))

    def _prior_run(self, **flags):
        db.init_db()
        self.legacy_send("careers@fintrail.de", "Fintrail GmbH", "Backend Developer", 45, **flags)
        self.sql("DELETE FROM contacts")
        contacts.backfill_if_empty()
        self.run_pipeline([fx.SYMFONY_SENIOR])
        r = self.row("fx-symfony")
        msg = open(os.path.join(r["packet_path"], "network_message.txt"), encoding="utf-8").read()
        return r, msg

    def test_silent_company_contact_is_flagged_not_claimed(self):
        # A silent send to careers@ may never have been read: flag it for
        # Mehdi, but the message must not claim "I applied before".
        r, msg = self._prior_run()
        self.assertIn("Déjà sollicité (même entreprise)", r["prior_contact"])
        self.assertIn("adresse générique", r["prior_contact"])
        self.assertNotIn("I applied to", msg)

    def test_acknowledged_company_contact_is_referenced(self):
        r, msg = self._prior_run(replied=1, reply_type="auto")
        self.assertIn("I applied to Fintrail GmbH on", msg)
        self.assertIn("Backend Developer", msg)

    def test_recently_contacted_person_is_not_auto_emailed(self):
        db.init_db()
        self.legacy_send("jane.doe@shopcraft.io", "Shopcraft", "PHP Developer", 5, "Jane Doe")
        self.sql("DELETE FROM contacts")
        contacts.backfill_if_empty()
        _, sent = self.run_pipeline([fx.MAGENTO_GOOD])
        self.assertEqual(sent, [])
        r = self.row("fx-magento")
        self.assertEqual(r["status"], "packet_ready")
        draft = open(os.path.join(r["packet_path"], "email_draft.txt"), encoding="utf-8").read()
        self.assertIn("I wrote to you on", draft)                # references the earlier email

    def test_unnamed_contact_goes_to_packet(self):
        _, sent = self.run_pipeline([fx.MAGENTO_GOOD],
                                    contacts_map={"Shopcraft": ("careers@shopcraft.io", None)})
        self.assertEqual(sent, [])
        self.assertIn("cover_letter.txt", os.listdir(self.row("fx-magento")["packet_path"]))

    def test_dry_run_marks_nothing(self):
        _, sent = self.run_pipeline([fx.MAGENTO_GOOD], dry=True)
        self.assertTrue(sent and sent[0]["dry"])
        self.assertIsNone(self.row("fx-magento")["email_sent_at"])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM contacts")[0][0], 0)

    def test_digest_groups_by_category(self):
        from job_bot.digest import build_digest_body
        self.run_pipeline(fx.ALL)
        self.sql("UPDATE jobs SET digest_sent_at = NULL")          # the run already digested them
        body = build_digest_body(db.get_digest_candidates())
        self.assertLess(body.index("BON MATCH"), body.index("CONTACT RÉSEAU"))
        self.assertLess(body.index("CONTACT RÉSEAU"), body.index("À ÉVITER"))
        self.assertIn("1 bon(s) match · 2 contact(s) réseau · 1 à éviter", body)


if __name__ == "__main__":
    unittest.main()
