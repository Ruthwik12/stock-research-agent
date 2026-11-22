"""Step 12: macro data from FRED (Federal Reserve Bank of St. Louis) into market.db (macro_series).

Downloads each series in config.FRED_SERIES as CSV from FRED's public graph endpoint (no API key),
from config.MACRO_HISTORY_START, and upserts the observations. FRED serves the latest vintage, so
revised monthly figures (CPI, unemployment) can differ slightly from what was first published.

Run from the project root:
    python scripts/12_macro.py
"""
import csv
import io
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from stockagent.db import connect  # noqa: E402

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}"


def parse_csv(text: str) -> list[tuple[str, float]]:
    rows = list(csv.reader(io.StringIO(text)))
    out = []
    for r in rows[1:]:   # header: observation_date (or DATE), <series id>
        if len(r) == 2 and r[1] not in ("", "."):   # "." marks a missing day (holiday)
            try:
                out.append((r[0], float(r[1])))
            except ValueError:
                pass
    return out


def main() -> None:
    conn = connect()
    failed = []
    for sid, (label, _, _) in config.FRED_SERIES.items():
        try:
            # Default client headers: FRED's edge stalls on some custom User-Agent strings.
            resp = requests.get(URL.format(sid=sid, start=config.MACRO_HISTORY_START), timeout=30)
            resp.raise_for_status()
            obs = parse_csv(resp.text)
        except Exception as exc:
            failed.append(sid)
            print(f"{sid:14} failed: {exc}")
            continue
        conn.executemany("INSERT OR REPLACE INTO macro_series VALUES (?,?,?)", [(sid, d, v) for d, v in obs])
        conn.commit()
        last = f"{obs[-1][0]} = {obs[-1][1]}" if obs else "no data"
        print(f"{sid:14} {len(obs):>5} observations, latest {last}  ({label})")
    conn.close()
    if len(failed) == len(config.FRED_SERIES):
        sys.exit("FRED unreachable; nothing updated")


if __name__ == "__main__":
    main()
