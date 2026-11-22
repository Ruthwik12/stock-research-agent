"""Daily job: download any new filings, parse them, add them to the vector index, refresh market data,
and run the screener; then prepare filings for the top new names on the shortlist (config.ON_DEMAND_NIGHTLY).

Each step is incremental, so a day with no new filings does almost nothing. Output is appended
to logs/update_daily.log. Run by the Windows scheduled task (see scripts/schedule_daily_update.ps1)
or by hand:
    python scripts/update_daily.py
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from stockagent import forecaster, ondemand, reflector, sell_monitor  # noqa: E402
from stockagent.ondemand import pipeline_lock  # noqa: E402

# 13 (news) writes documents that 03 indexes, so it runs before 03.
STEPS = ["01_download_filings.py", "02_parse_sections.py", "13_news.py", "03_ingest.py", "07_market_data.py",
         "08_screen.py", "10_backtest.py", "11_insider_trades.py", "12_macro.py"]
OPTIONAL = {"13_news.py", "11_insider_trades.py", "12_macro.py"}   # a failure is logged; the run continues


def main() -> int:
    log_path = ROOT / "logs" / "update_daily.log"
    log_path.parent.mkdir(exist_ok=True)
    # Same lock as on-demand preparation: only one run writes to the index at a time.
    with pipeline_lock(), open(log_path, "a", encoding="utf-8") as log:
        log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        for step in STEPS:
            log.write(f"--- {step}\n")
            log.flush()
            result = subprocess.run([sys.executable, str(ROOT / "scripts" / step)], cwd=ROOT,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=3 * 3600)
            if result.returncode != 0 and step in OPTIONAL:
                log.write(f"!!! {step} failed with exit code {result.returncode}; continuing without it\n")
            elif result.returncode != 0:
                log.write(f"!!! {step} failed with exit code {result.returncode}; later steps skipped\n")
                return result.returncode
        log.write(f"--- resolved {forecaster.resolve_open()} predictions whose 90 days have passed\n")
        r = reflector.reflect(save=True)   # no LLM; proposals wait for human approval
        log.write(f"--- reflector: {r['proposals_new_or_refreshed']} rule proposals new/refreshed, "
                  f"{r['candidates_rejected_out_of_sample']} rejected out of sample\n")
        flagged = [p for p in sell_monitor.check_positions(thesis_check=False) if p["flags"]]   # free checks only
        log.write(f"--- sell monitor: {len(flagged)} paper positions flagged: "
                  f"{', '.join(p['ticker'] + ' ' + '/'.join(p['flags']) for p in flagged) or 'none'}\n")
        # Prepare filings for the best new names on today's shortlist, so they are researchable tomorrow.
        tickers = ondemand.nightly_candidates()
        log.write(f"--- on-demand filings for: {', '.join(tickers) or 'none'}\n")
        log.flush()
        if tickers:
            ondemand.prepare_locked(tickers)
            for t in tickers:
                s = ondemand.get_status(t)
                log.write(f"    {t}: {s['status']} ({s['message']})\n")
        log.write("OK\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
