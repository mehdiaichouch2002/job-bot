"""The real send path — send_email() building the SMTP message — with only the
Gmail server faked, so what would leave the machine can be inspected byte for
byte. Run: python -m unittest discover tests"""
import email
import os
import unittest
from email import policy
from unittest import mock

import job_bot.email_sender as sender
import job_bot.healthcheck as healthcheck
from job_bot.packet import _pick_cv
from tests.test_pipeline import TempDB
from tests import fixtures as fx
from job_bot.config import CV_PDF_MAGENTO_EN

# The public repo carries no CV PDFs (in the cloud they come from the encrypted state),
# so the tests that check the attached CV only run where the PDFs exist.
_needs_cv = unittest.skipUnless(os.path.exists(CV_PDF_MAGENTO_EN), "CV PDFs not present (public repo)")


class FakeSMTP:
    """Stands in for smtplib.SMTP; records the conversation."""
    sent = []
    logins = []
    fail_login = False

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def starttls(self):
        self.tls = True

    def login(self, user, password):
        if FakeSMTP.fail_login:
            import smtplib
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
        FakeSMTP.logins.append((user, password, getattr(self, "tls", False)))

    def sendmail(self, frm, to, raw):
        FakeSMTP.sent.append({"from": frm, "to": to,
                              "msg": email.message_from_string(raw, policy=policy.default)})


def _reset():
    FakeSMTP.sent, FakeSMTP.logins, FakeSMTP.fail_login = [], [], False


class SendEmailTest(unittest.TestCase):
    def setUp(self):
        _reset()
        self.p = [mock.patch.object(sender.smtplib, "SMTP", FakeSMTP),
                  mock.patch.object(sender, "EMAIL_PASSWORD", "abcd efgh ijkl mnop")]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()

    @_needs_cv
    def test_message_on_the_wire(self):
        cv = _pick_cv(fx.job(fx.MAGENTO_GOOD), "fr")
        body = "Bonjour Jane,\n\nDéveloppeur Magento 2 : 50 000+ utilisateurs, -45 % de bugs.\n\nCordialement,"
        self.assertTrue(sender.send_email("jane.doe@shopcraft.io", "Candidature : Développeur Magento 2",
                                          body, pdf_path=cv))
        self.assertEqual(FakeSMTP.logins, [(sender.EMAIL_ADDRESS, "abcd efgh ijkl mnop", True)])  # TLS before login
        out = FakeSMTP.sent[0]
        msg = out["msg"]
        self.assertEqual(out["to"], "jane.doe@shopcraft.io")
        self.assertEqual(msg["To"], "jane.doe@shopcraft.io")
        self.assertEqual(msg["From"], f"{sender.SENDER_NAME} <{sender.EMAIL_ADDRESS}>")
        self.assertTrue(msg["Date"])                                                 # spam filters penalise a missing Date
        self.assertTrue(msg["Message-ID"].endswith("@gmail.com>"))
        self.assertEqual(msg["Subject"], "Candidature : Développeur Magento 2")      # accents survive
        parts = list(msg.iter_parts())
        self.assertEqual(parts[0].get_content().strip(), body.strip())              # utf-8 body intact
        att = parts[1]
        self.assertEqual(att.get_content_type(), "application/pdf")
        self.assertEqual(att.get_filename(), os.path.basename(cv))
        with open(cv, "rb") as f:
            self.assertEqual(att.get_content(), f.read())                           # the real CV, byte for byte

    def test_auth_failure_returns_false(self):
        FakeSMTP.fail_login = True
        self.assertFalse(sender.send_email("a@b.com", "s", "b"))
        self.assertEqual(FakeSMTP.sent, [])

    def test_no_password_sends_nothing(self):
        with mock.patch.object(sender, "EMAIL_PASSWORD", ""):
            self.assertFalse(sender.send_email("a@b.com", "s", "b"))
        self.assertEqual(FakeSMTP.sent, [])

    def test_placeholder_password_counts_as_unset(self):
        import importlib
        import job_bot.config as config
        with mock.patch.dict(os.environ, {"EMAIL_PASSWORD": "your_16_char_gmail_app_password"}):
            importlib.reload(config)
            self.assertEqual(config.EMAIL_PASSWORD, "")
        importlib.reload(config)


class LivePipelineSendTest(TempDB):
    """A LIVE (not dry) pipeline run where only the Gmail server is fake: the
    application and the digest must both really go through send_email → SMTP."""
    CONTACTS = {"Shopcraft": ("jane.doe@shopcraft.io", "Jane Doe")}

    @_needs_cv
    def test_live_run_sends_application_and_digest(self):
        _reset()
        import job_bot.main as main
        with mock.patch.object(sender.smtplib, "SMTP", FakeSMTP), \
                mock.patch.object(sender, "EMAIL_PASSWORD", "app-password"), \
                mock.patch.object(main, "EMAIL_PASSWORD", "app-password"):
            self._run_live(fx.ALL)
        to = [s["to"] for s in FakeSMTP.sent]
        # Exactly two messages leave the machine: the application and the digest.
        self.assertEqual(sorted(to), sorted(["jane.doe@shopcraft.io", sender.EMAIL_ADDRESS]))
        app = next(s["msg"] for s in FakeSMTP.sent if s["to"] == "jane.doe@shopcraft.io")
        self.assertTrue(app.get_body(("plain",)).get_content().startswith("Hi Jane,"))
        self.assertIn("Magento2_Developer_EN", [p.get_filename() for p in app.iter_attachments()][0])
        dig = next(s["msg"] for s in FakeSMTP.sent if s["to"] == sender.EMAIL_ADDRESS)
        self.assertIn("1 bon(s) match", dig["Subject"])
        self.assertIn("CONTACT RÉSEAU", dig.get_body(("plain",)).get_content())

    def _run_live(self, ads):
        import job_bot.main as main
        import job_bot.drafter as drafter

        class Scraper:
            SOURCE_NAME = "fixture"

            def fetch_jobs(self, kw):
                return [fx.job(a) for a in ads]

        def enrich(jobs, budget_sec=None):
            for j in jobs:
                if j["company"] in self.CONTACTS:
                    j["contact_email"], j["contact_name"] = self.CONTACTS[j["company"]]

        with mock.patch.object(main, "_SCRAPERS", [Scraper()]), \
                mock.patch.object(main, "process_inbox", lambda: {"error": "test"}), \
                mock.patch.object(main, "llm_available", return_value=False), \
                mock.patch.object(drafter, "llm_available", return_value=False), \
                mock.patch.object(main, "enrich_jobs_with_emails", enrich), \
                mock.patch.object(main, "_valid_email", lambda e: "@" in (e or "")), \
                mock.patch.object(main, "check_listing", lambda j: {"available": True, "easy_apply": False}), \
                mock.patch.object(main.time, "sleep", lambda s: None):
            return main.run_pipeline(dry_run=False)


class HealthcheckTest(TempDB):
    @_needs_cv
    def test_sample_goes_only_to_yourself(self):
        _reset()

        class FakeIMAP:
            def __init__(self, *a, **k):
                pass

            def login(self, u, p):
                pass

            def logout(self):
                pass

        with mock.patch.object(sender.smtplib, "SMTP", FakeSMTP), \
                mock.patch.object(healthcheck.smtplib, "SMTP", FakeSMTP), \
                mock.patch.object(healthcheck.imaplib, "IMAP4_SSL", FakeIMAP), \
                mock.patch.object(sender, "EMAIL_PASSWORD", "pw"), \
                mock.patch.object(healthcheck, "EMAIL_PASSWORD", "pw"), \
                mock.patch.object(healthcheck, "GROQ_API_KEY", ""), \
                mock.patch("builtins.print"):
            ok = healthcheck.run_check()
        self.assertFalse(ok)                                   # no Groq key → reported
        self.assertEqual([s["to"] for s in FakeSMTP.sent], [sender.EMAIL_ADDRESS])
        msg = FakeSMTP.sent[0]["msg"]
        self.assertTrue(msg["Subject"].startswith("[TEST bot]"))
        self.assertEqual(len(list(msg.iter_attachments())), 1)


if __name__ == "__main__":
    unittest.main()
