#!/usr/bin/env python3
"""
Job Application Bot — Entry Point
Usage:
    python run.py                     # run now, then every 2h
    python run.py --once              # run once and exit
    python run.py --dry-run           # no emails sent
    python run.py --interval 4        # custom interval (hours)
    python run.py --stats             # show database stats and exit
"""

import os
import sys
import logging
import argparse
import schedule
import time

# ── Logging setup ────────────────────────────────────────────
def _setup_logging(log_path: str):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    root = logging.getLogger()
    if root.handlers:          # already configured — don't add duplicate handlers
        return
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    root.setLevel(logging.INFO)
    root.addHandler(logging.FileHandler(log_path, encoding="utf-8"))
    root.addHandler(logging.StreamHandler(sys.stdout))
    for h in root.handlers:
        h.setFormatter(logging.Formatter(fmt))

# Import after env is available
from job_bot.config import LOG_PATH, DRY_RUN
_setup_logging(LOG_PATH)

from job_bot.main import run_pipeline
from job_bot.database import init_db, get_stats, get_report

logger = logging.getLogger(__name__)


def _show_stats():
    init_db()
    s = get_stats()
    print("\n=== Job Bot Statistics ===")
    print(f"  Total jobs found     : {s['total']}")
    print(f"  Bon match / réseau / à éviter : "
          f"{s.get('bon_match', 0)} / {s.get('contact_reseau', 0)} / {s.get('a_eviter', 0)}")
    print(f"  Applied (all)        : {s['applied']}")
    print(f"    └ via auto-email   : {s.get('emailed', 0)}")
    print(f"  Packets ready to send: {s.get('packets_ready', 0)}")
    print(f"  Contacts in history  : {s.get('contacts', 0)}")
    from job_bot.outreach import status_counts
    o = status_counts()
    print("  Outreach             : " + (", ".join(f"{k} {v}" for k, v in sorted(o.items())) or "no targets yet"))
    print(f"  Replies received     : {s.get('replied', 0)}")
    print(f"  Bounced / dead addrs : {s.get('bounced', 0)} / {s.get('dead_addrs', 0)}")
    print()


def _show_report():
    import json
    from job_bot.matcher import summary_lines
    init_db()
    rows = get_report()
    if not rows:
        print("\nNo evaluated jobs yet. Run: python run.py --once\n")
        return
    order = {"BON_MATCH": 0, "CONTACT_RESEAU": 1, "A_EVITER": 2}
    print("\n=== Recent verdicts ===\n")
    for r in sorted(rows, key=lambda x: (order.get(x["category"], 3), -(x.get("fit_score") or 0))):
        tag = {"applied": "✉ emailed", "packet_ready": "📋 packet", "avoid": "✗ skipped"}.get(r["status"], r["status"])
        print(f"{tag}  {r['title']} @ {r['company']}")
        try:
            for line in summary_lines(json.loads(r["evaluation"] or "{}")):
                print(f"            {line}")
        except (ValueError, KeyError):
            pass
        if r.get("prior_contact"):
            print(f"            ⚠ {r['prior_contact']}")
        where = r.get("contact_email") if r["status"] == "applied" else r.get("packet_path")
        if where:
            print(f"            → {where}")
        print(f"            {r.get('url','')}\n")


def main():
    parser = argparse.ArgumentParser(description="Mehdi's Job Application Bot")
    parser.add_argument("--dry-run",  action="store_true", help="Generate emails but don't send them")
    parser.add_argument("--once",     action="store_true", help="Run once then exit")
    parser.add_argument("--interval", type=int, default=15, help="Scheduler interval in minutes (default: 15)")
    parser.add_argument("--stats",    action="store_true", help="Show DB stats and exit")
    parser.add_argument("--report",   action="store_true", help="Show recent applications & ready packets")
    parser.add_argument("--check",    action="store_true",
                        help="Test Gmail, Groq, CVs and DB; email a sample application to yourself")
    parser.add_argument("--harvest",  metavar="FILE",
                        help="Collect published emails for 'Company | City | domain' lines into the outreach list")
    args = parser.parse_args()

    if args.harvest:
        from job_bot.harvest import harvest_file
        print(harvest_file(args.harvest))
        return

    if args.check:
        from job_bot.healthcheck import run_check
        sys.exit(0 if run_check() else 1)

    if args.stats:
        _show_stats()
        return

    if args.report:
        _show_report()
        return

    dry = args.dry_run or DRY_RUN
    logger.info("Mode: %s | Interval: %dmin", "DRY RUN" if dry else "LIVE", args.interval)

    if args.once:
        run_pipeline(dry_run=dry)
        return

    # Run immediately, then on schedule
    run_pipeline(dry_run=dry)
    schedule.every(args.interval).minutes.do(run_pipeline, dry_run=dry)
    logger.info("Scheduler active — next run in %dmin. Press Ctrl+C to stop.", args.interval)

    try:
        while True:
            schedule.run_pending()
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")


if __name__ == "__main__":
    main()
