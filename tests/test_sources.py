"""Tests for the added sources: event 8-Ks, Form 4, FRED macro and news (no network)."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from stockagent import context, debate_graph  # noqa: E402
from stockagent.db import connect  # noqa: E402
from stockagent.retriever import cite  # noqa: E402


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), ROOT / "scripts" / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_event_8ks_selected_by_item_and_lookback():
    dl = _script("01_download_filings.py")
    f = lambda acc, d, items: {"accessionNumber": acc, "filingDate": d, "form": "8-K", "items": items}  # noqa: E731
    filings = [f("a", "2026-11-01", "2.02,9.01"),      # earnings
               f("b", "2026-10-15", "5.02"),           # executive change -> event
               f("c", "2026-10-01", "5.07,9.01"),      # shareholder vote only -> skipped
               f("d", "2026-09-01", "1.01,2.03,9.01"),  # material agreement + debt -> event
               f("e", "2025-06-01", "8.01")]           # older than the lookback -> skipped
    picked = dl.select_filings(filings, {"8-K-earnings": 4, "8-K-event": 8}, today="2026-11-17")
    assert [(k, x["accessionNumber"]) for k, x in picked] == [("8-K-earnings", "a"), ("8-K-event", "b"),
                                                              ("8-K-event", "d")]
    assert dl.event_items("1.01,2.03,9.01") == ["1.01", "2.03"]


def test_exhibit_names_without_a_sub_number():
    pat = _script("01_download_filings.py").EX99_PATTERN
    num = lambda name: (lambda m: int(m.group(1) or 1) if m else None)(pat.search(name))  # noqa: E731
    assert num("msft-ex99_1.htm") == 1 and num("a8-kex992.htm") == 2 and num("jpm-exhibit991narrative.htm") == 1
    assert num("acme-ex99.htm") == 1                      # plain EX-99 counts as the release
    assert num("q2fy27pr.htm") is None                     # left to the index-page fallback


def test_10q_part_inferred_from_item_titles():
    """WSM: "PART I" appears only in the table of contents, so body items inherited Part II from it."""
    parse = _script("02_parse_sections.py")
    filler = "Sales rose on strong demand.\n" * 80
    text = ("PART I. FINANCIAL INFORMATION\nItem 1. | Financial Statements | 1\nItem 2. | Management's Discussion "
            "and Analysis | 14\nPART II. OTHER INFORMATION\nItem 1A. | Risk Factors | 22\n"
            f"ITEM 1. FINANCIAL STATEMENTS\n{filler}ITEM 2. MANAGEMENT'S DISCUSSION AND ANALYSIS\n{filler}"
            f"PART II - OTHER INFORMATION\nITEM 1A. RISK FACTORS\n{filler}ITEM 6. EXHIBITS\n")
    sections = {s["section"]: s for s in parse.extract_sections(text, "10-Q")}
    assert sections["MD&A"]["text"].startswith("ITEM 2. MANAGEMENT") and filler in sections["MD&A"]["text"]
    assert sections["Risk Factors"]["text"].startswith("ITEM 1A. RISK FACTORS")


def test_earnings_8k_without_exhibit_uses_body():
    parse = _script("02_parse_sections.py")
    body = ("UNITED STATES SECURITIES AND EXCHANGE COMMISSION\nForm 8-K\n"
            "Item 2.02 Results of Operations\nRevenue was $3.8 billion.\nSIGNATURES\nBy: /s/ CFO")
    assert parse.trim_8k_body(body) == "Item 2.02 Results of Operations\nRevenue was $3.8 billion."
    # Amazon-style table of contents with its own items and signatures line
    toc = ("Form 8-K\nITEM 8.01. OTHER EVENTS. | 3\nSIGNATURES | 4\n"
           "ITEM 8.01. OTHER EVENTS.\nAmazon issued $15 billion of notes.\nSIGNATURES\nBy: /s/ CFO")
    assert parse.trim_8k_body(toc) == "ITEM 8.01. OTHER EVENTS.\nAmazon issued $15 billion of notes."


FORM4 = """<?xml version="1.0"?><ownershipDocument><aff10b5One>true</aff10b5One>
<reportingOwner><reportingOwnerId><rptOwnerName>Doe Jane</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isDirector>0</isDirector><isOfficer>1</isOfficer>
<officerTitle>Chief Financial Officer</officerTitle></reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable><nonDerivativeTransaction><transactionDate><value>2026-10-02</value></transactionDate>
<transactionCoding><transactionCode>S</transactionCode></transactionCoding><transactionAmounts>
<transactionShares><value>1000</value></transactionShares><transactionPricePerShare><value>250.5</value>
</transactionPricePerShare><transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
</transactionAmounts></nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"""


def test_parse_form4():
    rows = _script("11_insider_trades.py").parse_form4(FORM4)
    assert rows == [{"owner": "Doe Jane", "relationship": "Chief Financial Officer", "txn_date": "2026-10-02",
                     "code": "S", "shares": 1000.0, "price": 250.5, "acquired_disposed": "D", "planned": 1}]


def test_insider_evidence_respects_as_of(tmp_path):
    conn = connect(tmp_path / "t.db")
    assert context.insider_evidence(conn, "AAA", "2026-11-17") is None        # never fetched: unknown, not "none"
    conn.execute("INSERT INTO insider_filings VALUES ('x1','AAA','2026-10-05','u',1)")
    conn.execute("INSERT INTO insider_filings VALUES ('x2','AAA','2026-11-20','u',1)")
    conn.execute("INSERT INTO insider_trades VALUES ('x1',0,'AAA','2026-10-05','Doe Jane','CFO','2026-10-02',"
                 "'S',1000,250.5,'D',1)")
    conn.execute("INSERT INTO insider_trades VALUES ('x2',0,'AAA','2026-11-20','Roe Rob','CEO','2026-11-18',"
                 "'P',500,240,'A',0)")   # filed after the as-of date: must not appear
    e = context.insider_evidence(conn, "AAA", "2026-11-17")
    assert "purchases (code P): none" in e["text"]
    assert "1 transactions by 1 insiders, 1,000 shares, about $250,500" in e["text"]
    assert "1 of the 1 sales were under a Rule 10b5-1" in e["text"] and e["filing_date"] == "2026-10-05"


def test_macro_evidence_waits_for_publication(tmp_path):
    conn = connect(tmp_path / "t.db")
    conn.executemany("INSERT INTO macro_series VALUES (?,?,?)",
                     [("CPIAUCSL", "2025-09-01", 300.0), ("CPIAUCSL", "2026-09-01", 309.0),
                      ("UNRATE", "2026-09-01", 4.2), ("DGS10", "2026-08-03", 4.0), ("DGS10", "2026-11-03", 4.5)])
    early = context.macro_evidence(conn, "AAA", "2026-10-05")    # September CPI not yet published
    assert "CPI" not in early["text"] and "Unemployment" not in early["text"]
    later = context.macro_evidence(conn, "AAA", "2026-11-17")
    assert "CPI inflation, year over year: 3.0% for 2026-09" in later["text"]
    assert "10-year Treasury yield: 4.50% on 2026-11-03; about 3 months earlier (2026-08-03) 4.00%" in later["text"]


def test_news_keeps_only_trusted_new_items():
    news = _script("13_news.py")
    raw = [{"id": 1, "datetime": 1794900000, "source": "Reuters", "headline": "Acme wins <b>deal</b>",
            "summary": "Acme said...", "url": "https://r/1"},
           {"id": 2, "datetime": 1794900100, "source": "Yahoo", "headline": "Acme stock jumps", "url": "https://y/2"},
           {"id": 3, "datetime": 1794900200, "source": "CNBC", "headline": "Acme wins deal", "url": "https://c/3"},
           {"id": 4, "datetime": 1794900300, "source": "MarketWatch", "headline": "Old item", "url": "https://m/4"}]
    items, skipped = news.select_items(raw, "ACME", known={"finnhub-4"})
    assert [i["news_id"] for i in items] == ["finnhub-3"]        # duplicate headline from Reuters dropped
    assert items[0]["headline"] == "Acme wins deal" and skipped == {"Yahoo": 1}
    doc = news.to_parsed_doc(items[0])
    assert doc["form_type"] == "news" and doc["sections"][0]["section"] == "News: CNBC"
    assert cite({**doc, "section": "News: CNBC"}) == f"CNBC news report on ACME, published {doc['filing_date']}"


def test_news_only_thesis_capped_in_debate():
    state = {"momentum": 0.1, "evidence": [
        {"eid": "E1", "form_type": "news", "text": "x"}, {"eid": "E2", "form_type": "10-Q", "text": "y"}]}
    s = {"evidence_support": 9, "relevance_90d": 8, "materiality": 8, "reason": "r"}
    news_only = debate_graph._weighted(state, {"id": "B1", "side": "bull", "text": "t", "evidence_ids": ["E1"]}, s, [])
    with_filing = debate_graph._weighted(state, {"id": "B2", "side": "bull", "text": "t",
                                                 "evidence_ids": ["E1", "E2"]}, s, [])
    assert news_only["parts"]["evidence_support"] == 6 and with_filing["parts"]["evidence_support"] == 9
    assert news_only["ratings"]["evidence_support"] == 9   # raw rating kept; the cap is re-applied on re-scores
