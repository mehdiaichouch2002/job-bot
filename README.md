# Job Application Bot

An automated, **assist-first** job-application bot for a Magento 2 / PHP developer.
It scrapes job boards on a schedule, scores each role for fit, writes a tailored
application in the offer's language using a **free LLM**, and either emails it to a
**verified** recruiter address or prepares a ready-to-submit application packet —
then sends you a ranked daily shortlist.

> **Design principle:** never spam. The bot only emails addresses it can verify are
> real (DNS MX-validated); it never fabricates `careers@guessed-domain` addresses.
> Everything it can't verify becomes a one-minute manual application instead.

---

## What it does each run

1. **Scrape** multiple job boards (RemoteOK, Remotive, We Work Remotely, Arbeitnow,
   Jobicy, LinkedIn guest search, Adzuna).
2. **Filter** out off-stack roles (.NET, Node, data, sales, thesis postings, …).
3. **Detect language** (French vs English) from the ad's text, with country / keyword fallback.
4. **Match honestly** (see [Matching](#matching)) — each ad gets a verdict:
   **Bon match**, **Contact réseau** or **À éviter**, with the criteria covered and the gaps.
5. **Find a real contact email** for bon-match jobs — company site scraping, then
   **MX-validate**. Guessed `careers@` addresses are never used.
6. **Act on the verdict:**
   - Bon match + named, verified contact + no recent contact → **auto-email** (CV attached).
   - Bon match otherwise → **packet** (cover letter + CV + apply link).
   - Contact réseau → **packet with a short, honest message** that names the gap.
   - À éviter → nothing, just the reason.
7. **Digest** — email yourself the verdicts, grouped by category.
8. **Track** everything in SQLite: jobs, verdicts, and every contact ever solicited.

## Matching

`profile.py` is the single source of truth (skills and levels, 3 years, languages,
the only figures a message may quote). `taxonomy.py` says which missing skills are
transferable (Laravel → Symfony, JavaScript → TypeScript…) and which are not
(Kubernetes, React Native…). For each ad:

- **Extraction** (`extractor.py`) keeps *required* and *nice-to-have* apart, plus
  minimum years, seniority, work mode and language level. Regex always runs; the
  Groq LLM refines it, but only criteria it can quote from the ad are kept.
- **Matching** (`matcher.py`, no LLM, deterministic): exact core skill = full credit,
  secondary skill (Java, C#…) = 0.4, transferable = partial, never full. Blocking gaps:
  a required non-transferable or unknown skill, more years than the profile, a
  senior/lead title with no year figure, a language 2+ CEFR levels above the profile,
  or an employer that can't hire from Morocco.
- **Verdict**: 0 blockers and ≥ 75 % of required skills covered → Bon match;
  1 blocker (or 40–75 % coverage) → Contact réseau; 2+ blockers, a location blocker,
  or < 40 % → À éviter.
- **Drafting** (`drafter.py`): the LLM may only claim proven skills, must word
  transferable ones as "my X lets me ramp up quickly on Y", and may only use figures
  from `profile.py`. Every draft is checked; a failing draft is retried once, then
  replaced by a checked template.
- **Contacts** (`contacts.py`): every address ever emailed (seeded from the full send
  history) is looked up by person, company name and domain. A prior contact is
  flagged; a message references it only when it demonstrably reached them.

---

## Setup

Requires Python 3.10+.

```bash
# 1. Clone
git clone git@github.com:mehdiaichouch2002/condidature.git
cd condidature

# 2. Virtual environment + dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Configuration
cp .env.example .env
# then edit .env (see below)
```

### Configure `.env`

| Variable | Required | What it's for |
|---|---|---|
| `EMAIL_ADDRESS` | ✅ | Gmail address used to send applications & digest |
| `EMAIL_PASSWORD` | ✅ | Gmail **App Password** (not your login password — [create one](https://myaccount.google.com/apppasswords)) |
| `GROQ_API_KEY` | ✅ | Free LLM for writing emails/cover letters ([console.groq.com](https://console.groq.com)) |
| `HUNTER_API_KEY` | optional | Improves recruiter-email hit rate (free tier: 25/mo) |
| `ADZUNA_APP_ID` / `ADZUNA_API_KEY` | optional | Enables the Adzuna source |
| `AUTO_EMAIL` | optional | Auto-email bon-match jobs to a named, verified contact (default true) |
| `GROQ_MODEL` / `GROQ_EXTRACT_MODEL` | optional | Models for drafting / criteria extraction |
| `LLM_EXTRACT_MAX` / `EXTRACT_BUDGET_SEC` | optional | Cap on LLM extraction per run (default 12 ads / 90 s) |
| `SEND_DIGEST` / `DIGEST_TO` | optional | Toggle the digest email and where it goes |
| `MAX_JOBS_PER_RUN` | optional | Cap on emails/packets per run (default 10) |

Your profile for matching and messages lives in [`job_bot/profile.py`](job_bot/profile.py)
(skills, years, figures); identity and CV text in `CV_DATA` in `config.py`. Keep both in
line with the CV PDFs when your résumé changes.

---

## Running the app

```bash
# Check credentials: Gmail login, Groq, CVs, DB — and email a sample application to yourself
python run.py --check

# Test mode — generate everything but send nothing
python run.py --dry-run --once

# Run the full pipeline once
python run.py --once

# Run now, then on a schedule (default every 15 min; override with --interval MINUTES)
python run.py
python run.py --interval 30

# Reports
python run.py --stats     # counts per verdict, emailed, packets, contacts
python run.py --report    # recent verdicts with criteria covered and gaps

# Tests (no network, no API key needed)
python -m unittest discover tests
python -m unittest tests.test_matching.MatcherTest.test_fixture_verdicts   # a single test
```

### Run it on a schedule (cron)

```bash
crontab -e
```
Add a line (here: every 30 minutes):
```cron
*/30 * * * * /path/to/condidature/venv/bin/python /path/to/condidature/run.py --once >> /path/to/condidature/logs/bot.log 2>&1
```

### Application packets

Packets for bon-match (not emailed) and contact-réseau jobs are written to:
```
applications/<YYYY-MM-DD>/<company>__<role>__<id>/
├── cover_letter.txt     # bon match: tailored, in the offer's language
├── email_draft.txt      # bon match with a known contact that wasn't auto-emailed
├── network_message.txt  # contact réseau: short, names the gap, asks for a better-suited role
├── application.md       # verdict, covered / partial / gaps, prior contact, apply link
└── CV_EN.pdf / CV_FR.pdf
```

---

## Project structure

```
run.py                  Entry point + scheduler + --stats/--report
job_bot/
  config.py             Settings + CV_DATA
  main.py               Pipeline: scrape → filter → match → email/packet → digest
  profile.py            Single source of truth: skills, years, languages, figures
  taxonomy.py           Skill aliases, transferable / non-transferable skills
  extractor.py          Ad → required / nice-to-have / years / languages
  matcher.py            Verdict: Bon match / Contact réseau / À éviter
  drafter.py            Messages per verdict + anti-overselling checks
  contacts.py           History of everyone contacted (person / company)
  hireability.py        Can this employer hire from Morocco?
  email_finder.py       Domain resolution + MX validation (never fabricates)
  email_generator.py    Groq client, signature, reply classification
  email_sender.py       Gmail SMTP
  packet.py             Builds ready-to-submit application packets
  digest.py             Ranked shortlist emailed to you
  database.py           SQLite tracking (data/jobs.db)
  scrapers/             One module per job board
tests/                  unittest suite + hand-labelled ads (tests/fixtures.py)
data/                   SQLite DB (auto-created, git-ignored)
logs/                   Rotating logs (git-ignored)
applications/           Generated packets (git-ignored)
```

## Canada relocation track

When `CANADA_TRACK=true`, the bot also targets **Canadian** developer listings
(Francophone-outside-Quebec hubs prioritized: Ottawa, Moncton/NB, Sudbury, Winnipeg,
Edmonton). For jobs detected as Canadian it:

- attaches the role-matched CV (Magento or Full-Stack; see `_latest_cv` in `config.py`),
- picks **French only for Quebec/Francophone** locations, English elsewhere,
- writes recruiter emails/cover letters that pitch the **LMIA-exempt Francophone
  Mobility (C16)** angle — "hiring me costs $230, no LMIA, work-authorized in weeks" —
  so a Morocco-based applicant isn't auto-rejected.

Jobs without a verified recruiter email become application packets with apply links
in your digest, same as the main track.

## Notes

- Uses a **free** LLM (Groq `llama-3.3-70b-versatile`) — no paid API.
- Clearbit's free autocomplete API is used for company→domain lookup; if it is
  ever discontinued, the bot falls back to verified slug-guessing and keeps working.
- `.env`, the database, logs, and generated packets are **never committed** (see `.gitignore`).
