"""Step 2: turn raw EDGAR HTML into clean text split by section.

For every document in data/raw/manifest.json this writes data/parsed/<doc_id>.json with the
manifest metadata plus a list of sections:
  10-K : Risk Factors (Item 1A), MD&A (Item 7)
  10-Q : MD&A (Part I Item 2), Risk Factors (Part II Item 1A)
  8-K  : Earnings Release (exhibit 99.1, kept whole)
  8-K event : the item text plus exhibit 99.1 if any, one section named after the items
Incremental: documents already parsed are skipped unless --force is given.

Run from the project root:
    python scripts/02_parse_sections.py [--force]
"""
import json
import re
import sys
import warnings
from pathlib import Path

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

BLOCK_TAGS = ["p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section"]
ITEM_RE = re.compile(r"^item\s*(\d{1,2}[a-c]?)\s*[\.:\-–—]?\s*(.*)$", re.IGNORECASE)
PART_RE = re.compile(r"^part\s+(i{1,2}|iv|iii)\b", re.IGNORECASE)
KNOWN_TITLE_RE = re.compile(
    r"risk factors|management.s discussion|quantitative and qualitative|financial statements|"
    r"unresolved staff|cybersecurity|properties|legal proceedings|mine safety|market for|"
    r"reserved|changes in and disagreements|controls and procedures|other information|exhibits|"
    r"business|unregistered sales|defaults upon|directors|executive compensation|security ownership|"
    r"certain relationships|principal account|form 10-k summary|selected financial", re.IGNORECASE)
# 10-Q item titles that exist in only one part (Item 4 is ambiguous: Controls in I, Mine Safety in II).
PART_I_TITLE_RE = re.compile(r"^\W*(financial statements|management.s discussion|quantitative and qualitative)",
                             re.IGNORECASE)
PART_II_TITLE_RE = re.compile(r"^\W*(legal proceedings|risk factors|unregistered sales|defaults upon|"
                              r"mine safety|other information|exhibits)", re.IGNORECASE)
MDNA_START_RE =re.compile(r"^(?:introduction|executive overview)$", re.IGNORECASE | re.MULTILINE)
MDNA_END_RE = re.compile(r"^forward-looking statements$", re.IGNORECASE | re.MULTILINE)

# (part or None, item) -> section name. Part only matters for 10-Q, where item numbers repeat.
WANTED = {
    "10-K": {(None, "1A"): "Risk Factors", (None, "7"): "MD&A"},
    "10-Q": {("I", "2"): "MD&A", ("II", "1A"): "Risk Factors"},
}
MIN_SECTION_CHARS = 1500   # shorter spans are table-of-contents entries or cross-references


def html_to_text(raw: bytes) -> str:
    soup = BeautifulSoup(raw, "lxml")
    # Hidden inline-XBRL header and scripts/styles carry no readable content.
    for tag in soup.find_all(["script", "style", "ix:header"]):
        tag.decompose()
    for tag in soup.find_all(style=re.compile(r"display:\s*none", re.IGNORECASE)):
        tag.decompose()
    # Tables become one line per row with cells separated by " | ", so figures keep their labels.
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            cells = [c for c in cells if c and c not in ("$", "%", ")")]
            if cells:
                rows.append(" | ".join(cells))
        table.replace_with("\n" + "\n".join(rows) + "\n")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(BLOCK_TAGS):
        tag.append("\n")
    text = soup.get_text("")
    text = text.replace("\xa0", " ").replace("​", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Drop bare page numbers and running page footers.
    text = re.sub(r"\n(?:\d{1,3}|Page \d+)\n", "\n", text)
    return text.strip()


def find_headings(text: str) -> list[tuple[int, str | None, str]]:
    """(char offset, part, item) for every 'Item N. <Title>' heading.

    A bare "Item 1A" line followed by body text is a running page header (Microsoft prints one
    on every page), not a section start, so a heading must carry a known title on the same line
    or the next one.
    """
    headings, part, pos = [], None, 0
    lines = text.split("\n")
    for n, line in enumerate(lines):
        stripped = line.strip()
        if len(stripped) < 200:
            pm = PART_RE.match(stripped)
            if pm:
                part = pm.group(1).upper()
            im = ITEM_RE.match(stripped)
            if im:
                title = im.group(2) or (lines[n + 1].strip() if n + 1 < len(lines) else "")
                # Some 10-Qs print "PART I" only in the table of contents; the item title tells the part.
                if PART_I_TITLE_RE.search(title[:120]):
                    part = "I"
                elif PART_II_TITLE_RE.search(title[:120]):
                    part = "II"
                if KNOWN_TITLE_RE.search(title[:120]):
                    headings.append((pos, part, im.group(1).upper()))
        pos += len(line) + 1
    return headings


def fallback_mdna(text: str) -> str:
    """MD&A for filers that don't put 'Item 7' / 'Item 2' headings in the body (e.g. JPMorgan).

    Starts at the first INTRODUCTION / EXECUTIVE OVERVIEW heading past the table of contents and
    ends at the last FORWARD-LOOKING STATEMENTS heading (where JPMorgan closes its MD&A).
    """
    skip_toc = len(text) // 100
    start = MDNA_START_RE.search(text, skip_toc)
    if not start:
        return ""
    ends = [m.start() for m in MDNA_END_RE.finditer(text, start.start())]
    return text[start.start():ends[-1]].strip() if ends else ""


def extract_sections(text: str, form: str) -> list[dict]:
    headings = find_headings(text)
    wanted = WANTED[form]
    best: dict[str, str] = {}
    for i, (start, part, item) in enumerate(headings):
        key = (part, item) if form == "10-Q" else (None, item)
        name = wanted.get(key)
        if not name:
            continue
        end = headings[i + 1][0] if i + 1 < len(headings) else len(text)
        span = text[start:end].strip()
        # The table of contents repeats every heading, so keep the longest span per section.
        if len(span) > len(best.get(name, "")):
            best[name] = span
    sections = [{"section": n, "text": t, "chars": len(t), "method": "item-heading"}
                for n, t in best.items() if len(t) >= MIN_SECTION_CHARS]
    # A short "MD&A" span is usually a cross-reference ("see pages 40-160"), not the MD&A itself.
    if len(best.get("MD&A", "")) < 10_000:
        mdna = fallback_mdna(text)
        if len(mdna) >= 10_000:
            sections = [s for s in sections if s["section"] != "MD&A"]
            sections.append({"section": "MD&A", "text": mdna, "chars": len(mdna), "method": "fallback-headings"})
    return sections


EVENT_BODY_START_RE = re.compile(r"^item\s+\d\.\d\d\b", re.IGNORECASE | re.MULTILINE)
EVENT_BODY_END_RE = re.compile(r"^signatures?\b", re.IGNORECASE | re.MULTILINE)
MAX_EXHIBIT_CHARS = 30_000   # long exhibits (merger agreements) would flood the index


def trim_8k_body(body_text: str) -> str:
    """The item text of an 8-K: cover page (before the first 'Item N.NN') and signatures cut.

    Some filers (Amazon) print a table of contents with its own "Item 8.01 ... | 3" and "Signatures | 4"
    lines, so each item start is tried and the longest span up to the next signature line wins."""
    best = ""
    for start in EVENT_BODY_START_RE.finditer(body_text):
        body = body_text[start.start():]
        end = EVENT_BODY_END_RE.search(body)
        span = (body[:end.start()] if end else body).strip()
        if len(span) > len(best):
            best = span
    return best or body_text.strip()


def event_section(doc: dict, body_text: str) -> dict:
    """Material-event 8-K: the item text plus exhibit 99.1 if present."""
    text = trim_8k_body(body_text)
    if doc.get("exhibit_path"):
        exhibit = html_to_text((config.PROJECT_ROOT / doc["exhibit_path"]).read_bytes())[:MAX_EXHIBIT_CHARS]
        text += "\n\nExhibit 99.1:\n" + exhibit
    names = ", ".join(f"{i} {config.EVENT_8K_ITEMS[i]}" for i in doc.get("items", []))
    when = f" on {doc['event_date']}" if doc.get("event_date") else ""
    return {"section": f"Event{when}: {names}", "text": text, "chars": len(text)}


def main() -> None:
    force = "--force" in sys.argv
    manifest = json.loads(config.MANIFEST_PATH.read_text())
    config.PARSED_DIR.mkdir(parents=True, exist_ok=True)
    done = skipped = 0

    for doc in manifest:
        out_path = config.PARSED_DIR / f"{doc['doc_id']}.json"
        if out_path.exists() and not force:
            skipped += 1
            continue
        text = html_to_text((config.PROJECT_ROOT / doc["local_path"]).read_bytes())
        if doc["form_type"] == "8-K-earnings" and doc.get("earnings_from_body"):
            body = trim_8k_body(text)   # no press-release exhibit: the results are in the 8-K items
            sections = [{"section": "Earnings (8-K text)", "text": body, "chars": len(body)}]
        elif doc["form_type"] == "8-K-earnings":
            sections = [{"section": "Earnings Release", "text": text, "chars": len(text)}]
        elif doc["form_type"] == "8-K-event":
            sections = [event_section(doc, text)]
        else:
            sections = extract_sections(text, doc["form_type"])

        missing = set(WANTED.get(doc["form_type"], {}).values()) - {s["section"] for s in sections}
        out_path.write_text(json.dumps({**doc, "full_text_chars": len(text), "sections": sections,
                                        "missing_sections": sorted(missing)}, indent=1))
        done += 1
        summary = ", ".join(f"{s['section']}={s['chars']:,}" for s in sections) or "NO SECTIONS"
        flag = f"  MISSING: {', '.join(sorted(missing))}" if missing else ""
        print(f"{doc['doc_id']:38} text={len(text):>9,}  {summary}{flag}")

    print(f"\nParsed {done}, skipped {skipped} already parsed. Output: {config.PARSED_DIR.relative_to(config.PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
