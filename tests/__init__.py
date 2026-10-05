"""Test package safety net — runs before any job_bot module is imported.

Tests must never reach the real world. On 2026-09-23 a pipeline test ran the
outreach step against the real target list with the real Gmail password from
.env and sent six duplicate emails to a company. So, before config.py loads
.env (python-dotenv never overrides a variable that is already set):

  - EMAIL_PASSWORD is empty  -> send_email() refuses to send anything; tests
    that exercise the send path patch in a fake SMTP server explicitly
  - GROQ_API_KEY is empty    -> no LLM calls
  - OUTREACH_ENABLED false   -> the pipeline never touches the target list;
    outreach tests enable it on a temp CSV
"""
import os

os.environ["EMAIL_PASSWORD"] = ""
os.environ["GROQ_API_KEY"] = ""
os.environ["OUTREACH_ENABLED"] = "false"
