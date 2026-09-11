from __future__ import annotations

import html
import json
import re
import subprocess
from datetime import datetime, date
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


IST = ZoneInfo("Asia/Kolkata")
BASE_API = "https://webnodejs.investorgain.com/cloud/v2/report/data-read/331/1"
INVESTORGAIN_BASE = "https://www.investorgain.com"
OUTPUT_DIR = Path("output")
HTML_PATH = OUTPUT_DIR / "index.html"
PNG_PATH = OUTPUT_DIR / "ipo_gmp_latest.png"

REQUEST_TIMEOUT = 30
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0 Safari/537.36"
)


def fmt_date(iso: str | None) -> str:
    """'2026-09-07' -> '7-Sep'  (keeps ISO value for sorting, formats only for display)."""
    if not iso:
        return "—"
    try:
        d = date.fromisoformat(iso)
        return f"{d.day}-{d.strftime('%b')}"
    except ValueError:
        return iso

def current_ist() -> datetime:
    return datetime.now(IST)


def financial_year(dt: datetime) -> str:
    start_year = dt.year if dt.month >= 4 else dt.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def api_url(dt: datetime) -> str:
    return (
        f"{BASE_API}/{dt.month:02d}/{dt.year}/{financial_year(dt)}/0/all"
        "?search="
    )


def parse_number(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", value.replace(",", ""))
    if not cleaned or cleaned in {"-", "."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def indian_number(value: float | int) -> str:
    number = int(round(value))
    sign = "-" if number < 0 else ""
    digits = str(abs(number))
    if len(digits) <= 3:
        return sign + digits

    last3 = digits[-3:]
    rest = digits[:-3]
    groups: list[str] = []
    while rest:
        groups.append(rest[-2:])
        rest = rest[:-2]
    return sign + ",".join(reversed(groups)) + "," + last3


def currency(value: float | int) -> str:
    return f"₹{indian_number(value)}"


def clean_text(value: str | None) -> str:
    return " ".join((value or "").split()).strip()


def parse_name(raw: str | None) -> dict[str, object]:
    soup = BeautifulSoup(raw or "", "html.parser")

    # Company link: the anchor pointing to the /gmp/ detail page.
    anchor = soup.find("a", href=re.compile(r"/gmp/")) or soup.find("a", href=True)

    name = clean_text(anchor.get_text(" ", strip=True) if anchor else soup.get_text(" ", strip=True))
    href = anchor.get("href") if anchor else None

    badges: list[dict] = []
    for span in soup.find_all("span"):
        text = clean_text(span.get_text(" ", strip=True))
        if not text:
            continue

        # If this badge sits inside its own link (e.g. the Allotment
        # badge -> kfintech), capture it. Badges without a link get url=None.
        parent_a = span.find_parent("a", href=True)
        url = None
        if parent_a is not None and parent_a is not anchor:
            url = urljoin(INVESTORGAIN_BASE, parent_a["href"])

        badges.append({"label": text, "url": url})

    return {
        "name": name or "Unknown IPO",
        "href": urljoin(INVESTORGAIN_BASE, href) if href else None,
        "badges": badges,
    }


def parse_gmp(raw: str | None) -> tuple[float, str, str]:
    soup = BeautifulSoup(raw or "", "html.parser")
    text = clean_text(soup.get_text(" ", strip=True))

    # GMP amount: first rupee amount after ₹ / &#8377;.
    amount_match = re.search(r"₹\s*([\d,]+(?:\.\d+)?)", text)
    amount = parse_number(amount_match.group(1)) if amount_match else None

    # GMP percentage: first percentage in the field.
    pct_match = re.search(r"\(\s*([-+]?\d+(?:\.\d+)?)\s*%\s*\)", text)
    percentage = pct_match.group(1) if pct_match else None

    if amount is None:
        amount = 0.0

    amount_display = "—" if amount_match is None else currency(amount)
    percentage_display = f"({percentage}%)" if percentage is not None else ""

    return amount, amount_display, percentage_display


def parse_updated(raw: str | None) -> str:
    soup = BeautifulSoup(raw or "", "html.parser")
    return clean_text(soup.get_text(" ", strip=True)) or "—"


def status_class(status: str) -> str:
    if status == "O":
        return "status-open"
    if status == "U":
        return "status-upcoming"
    return "status-other"


def status_label(status: str) -> str:
    return {
        "O": "Open",
        "U": "Upcoming",
        "C": "Closed",
        "CT": "Closed",
    }.get(status, status or "Other")


def parse_record(record: dict) -> dict:
    status = clean_text(str(record.get("~ipo_status1", ""))).upper()
    name_info = parse_name(record.get("Name"))
    gmp_amount, gmp_display, gmp_percentage = parse_gmp(record.get("GMP"))

    lot = parse_number(str(record.get("Lot", ""))) or 0
    price = parse_number(str(record.get("Price (₹)", "")))

    return {
        "status": status,
        "status_class": status_class(status),
        "status_label": status_label(status),
        "name": str(name_info["name"]),
        "href": name_info["href"],
        "badges": name_info["badges"],
        "sub": clean_text(str(record.get("Sub", "—"))) or "—",
        "price": price,
        "price_display": currency(price) if price is not None else "—",
        "gmp_amount": gmp_amount,
        "gmp_display": gmp_display,
        "gmp_percentage": gmp_percentage,
        "lot": int(lot) if lot.is_integer() else lot,
        "profit": gmp_amount * lot,
        "open": clean_text(str(record.get("~Srt_Open", "—"))) or "—",
        "close": clean_text(str(record.get("~Srt_Close", "—"))) or "—",
        "boa": clean_text(str(record.get("~Srt_BoA_Dt", "—"))) or "—",
        "listing": clean_text(str(record.get("Listing", "—"))) or "—",
        "updated": parse_updated(record.get("Updated-On")),
    }


def fetch_data(dt: datetime) -> list[dict]:
    url = api_url(dt)
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError("InvestorGain returned invalid JSON.") from exc

    records = payload.get("reportTableData")
    if not isinstance(records, list):
        raise RuntimeError("InvestorGain response did not contain reportTableData.")

    filtered = [
        record
        for record in records
        if record.get("~IPO_Category") == "IPO"
        and str(record.get("~ipo_status1", "")).upper() in {"C", "O", "CT", "U"}
    ]

    if not filtered:
        raise RuntimeError(
            "API request succeeded, but no matching Mainboard IPO records were found."
        )

    rows = [parse_record(record) for record in filtered]
    rows.sort(key=lambda row: row["profit"], reverse=True)

    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank

    return rows


def badge_html(badge: dict) -> str:
    label = html.escape(str(badge["label"]))
    normalized = str(badge["label"]).upper()

    if normalized == "IPO":
        cls = "badge badge-ipo"
    elif normalized in {"O", "U", "C", "CT"}:
        cls = {
            "O": "badge badge-open",
            "U": "badge badge-upcoming",
            "C": "badge badge-closed",
            "CT": "badge badge-closed",
        }[normalized]
    elif "ALLOT" in normalized:                     # "Allotted" / "Allotment" / ...
        cls = "badge badge-allotted"
    else:
        cls = "badge badge-neutral"

    url = badge.get("url")
    if url:
        return (
            f'<a class="{cls}" href="{html.escape(url, quote=True)}" '
            f'target="_blank" rel="noopener noreferrer">{label}</a>'
        )
    return f'<span class="{cls}">{label}</span>'


def render_name(row: dict) -> str:
    name = html.escape(str(row["name"]))
    if row["href"]:
        name_html = (
            f'<a class="ipo-name" href="{html.escape(str(row["href"]), quote=True)}" '
            f'target="_blank" rel="noopener noreferrer">{name}</a>'
        )
    else:
        name_html = f'<span class="ipo-name">{name}</span>'

    badges = " ".join(badge_html(b) for b in row["badges"])
    return f'<div class="name-wrap"><div>{name_html}</div><div class="badges">{badges}</div></div>'

def rank_html(rank: int) -> str:
    medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank)
    return f'<span class="rank-medal">{medal}</span>' if medal else str(rank)


def render_gmp(row: dict) -> str:
    pct = html.escape(str(row["gmp_percentage"]))
    return (
        f'<div class="gmp-value">{html.escape(str(row["gmp_display"]))}</div>'
        f'<div class="gmp-percent">{pct}</div>'
    )


def render_table(rows: list[dict]) -> str:
    body = []
    for row in rows:
        body.append(
            f"""
            <tr class="{row["status_class"]}">
                <td class="rank-cell">{rank_html(int(row["rank"]))}</td>
                <td class="name-cell">{render_name(row)}</td>
                <td>{html.escape(str(row["sub"]))}</td>
                <td>{html.escape(str(row["price_display"]))}</td>
                <td class="gmp-cell">{render_gmp(row)}</td>
                <td>{html.escape(str(row["lot"]))}</td>
                <td class="profit-cell">{currency(row["profit"])}</td>
                <td>{html.escape(fmt_date(row["open"]))}</td>
                <td>{html.escape(fmt_date(row["close"]))}</td>
                <td>{html.escape(fmt_date(row["boa"]))}</td>
                <td>{html.escape(fmt_date(row["listing"]))}</td>
                <td class="updated-cell">{html.escape(str(row["updated"]))}</td>
            </tr>
            """
        )
    return "\n".join(body)

def build_html(rows: list[dict], generated: datetime) -> str:
    total = len(rows)
    highest = max((row["profit"] for row in rows), default=0)
    lowest = min((row["profit"] for row in rows), default=0)
    average = sum(row["profit"] for row in rows) / total if total else 0

    table = render_table(rows)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IPO Pulse — Mainboard GMP Tracker</title>
<style>
:root {{
    --navy: #061d3d;
    --navy-2: #082b57;
    --blue: #1677ff;
    --green: #27d98b;
    --text: #12233f;
    --muted: #61718a;
    --line: #d7e0eb;
    --open: #eafbea;
    --upcoming: #fff9df;
    --other: #fff0ed;
}}
* {{ box-sizing: border-box; }}
body {{
    margin: 0;
    background: linear-gradient(135deg, #04172f 0%, #082d58 55%, #071c3b 100%);
    color: var(--text);
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}}
.page {{
    max-width: 1900px;
    margin: 0 auto;
    padding: 28px;
}}
.hero {{
    color: white;
    padding: 18px 8px 24px;
    display: flex;
    justify-content: space-between;
    gap: 28px;
    align-items: flex-end;
}}
.brand {{
    display: flex;
    gap: 16px;
    align-items: center;
}}
.brand-icon {{
    font-size: 42px;
    filter: drop-shadow(0 4px 12px rgba(0,0,0,.25));
}}
h1 {{
    margin: 0;
    font-size: clamp(30px, 3.2vw, 54px);
    letter-spacing: -1.5px;
    line-height: 1;
}}
h1 span {{ color: var(--green); }}
.subtitle {{
    margin-top: 10px;
    color: #d9e6f7;
    font-size: 17px;
    font-weight: 600;
}}
.meta {{
    text-align: right;
    color: #d6e4f5;
    font-size: 14px;
    line-height: 1.6;
}}
.meta strong {{ color: white; }}
.card {{
    background: #f7fbff;
    border: 1px solid rgba(255,255,255,.24);
    border-radius: 18px;
    overflow: hidden;
    box-shadow: 0 18px 55px rgba(0,0,0,.24);
}}
.table-scroll {{
    overflow-x: auto;
}}
table {{
    width: 100%;
    min-width: 1500px;
    border-collapse: separate;
    border-spacing: 0;
    font-size: 15px;
}}
thead th {{
    position: sticky;
    top: 0;
    z-index: 2;
    background: linear-gradient(180deg, #0d4c88, #083d73);
    color: white;
    padding: 15px 12px;
    text-align: center;
    border-right: 1px solid rgba(255,255,255,.18);
    font-size: 12px;
    letter-spacing: .8px;
    text-transform: uppercase;
    white-space: nowrap;
}}
thead th:nth-child(2) {{ text-align: left; width: 340px; min-width: 340px; }}
tbody td {{
    padding: 13px 12px;
    text-align: center;
    border-right: 1px solid var(--line);
    border-bottom: 1px solid var(--line);
    white-space: nowrap;
}}
tbody tr:last-child td {{ border-bottom: 0; }}
tbody tr.status-open td {{ background: var(--open); }}
tbody tr.status-upcoming td {{ background: var(--upcoming); }}
tbody tr.status-other td {{ background: var(--other); }}
tbody tr:hover td {{ filter: brightness(.985); }}
.name-cell {{
    text-align: left !important;
    white-space: normal !important;
    width: 340px;
    min-width: 340px;
}}
.name-wrap {{
    line-height: 1.35;
}}
.ipo-name {{
    color: #075fe5;
    text-decoration: none;
    font-weight: 700;
    overflow-wrap: anywhere;
}}
.ipo-name:hover {{ text-decoration: underline; }}
.badges {{
    margin-top: 6px;
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
    text-decoration: none;
}}
.badge {{
    display: inline-flex;
    align-items: center;
    padding: 3px 7px;
    border-radius: 999px;
    font-size: 10px;
    line-height: 1;
    font-weight: 800;
    letter-spacing: .2px;
    text-decoration: none;
}}
.badge-ipo {{ background: #6f7882; color: white; }}
.badge-open {{ background: #64a70b; color: white; }}
.badge-upcoming {{ background: #b39400; color: white; }}
.badge-closed {{ background: #356db4; color: white; }}
.badge-neutral {{ background: #e4e9ef; color: #46566b; }}
.badge-allotted {{background: #16a34a; color: #ffffff;}}
.rank-cell {{
    width: 70px;
    min-width: 70px;
    font-weight: 800;
}}
.rank-medal {{ font-size: 24px; }}
.gmp-cell {{ min-width: 125px; }}
.gmp-value {{
    font-weight: 800;
    color: #138d5d;
    font-size: 17px;
}}
.gmp-percent {{
    margin-top: 2px;
    color: #172a45;
    font-size: 13px;
}}
.profit-cell {{
    color: #078454;
    font-weight: 850;
    font-size: 16px;
}}
.updated-cell {{
    color: #006cff;
    font-weight: 700;
}}
.summary {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    padding: 16px;
    background: #eef5fc;
    border-top: 1px solid var(--line);
}}
.metric {{
    background: white;
    border: 1px solid #d9e4ef;
    border-radius: 12px;
    padding: 13px 15px;
}}
.metric-label {{
    color: var(--muted);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: .7px;
    font-weight: 750;
}}
.metric-value {{
    margin-top: 4px;
    color: var(--navy);
    font-size: 19px;
    font-weight: 850;
}}
.footer {{
    color: #bfd0e5;
    padding: 18px 5px 4px;
    display: flex;
    justify-content: space-between;
    gap: 15px;
    font-size: 12px;
}}
.actions {{
    display: flex;
    justify-content: center;
    padding: 20px;
    background: #edf5fd;
    border-top: 1px solid var(--line);
}}
.download {{
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: #0b7a52;
    color: white;
    border: 0;
    border-radius: 10px;
    padding: 12px 20px;
    text-decoration: none;
    font-weight: 800;
    box-shadow: 0 7px 18px rgba(11,122,82,.2);
}}
.download:hover {{ filter: brightness(1.08); }}
@media (max-width: 800px) {{
    .page {{ padding: 14px; }}
    .hero {{ align-items: flex-start; flex-direction: column; }}
    .meta {{ text-align: left; }}
    .summary {{ grid-template-columns: 1fr 1fr; }}
    .footer {{ flex-direction: column; }}
}}
@media print {{
    body {{ background: white; }}
    .page {{ max-width: none; padding: 0; }}
    .hero {{ color: black; }}
    .card {{ box-shadow: none; }}
    .actions {{ display: none; }}
}}
</style>
</head>
<body>
<div class="page">
    <header class="hero">
        <div>
            <div class="brand">
                <div class="brand-icon">📈</div>
                <div>
                    <h1>IPO <span>PULSE</span></h1>
                    <div class="subtitle">Mainboard IPO GMP Tracker</div>
                </div>
            </div>
        </div>
        <div class="meta">
            <div><strong>Latest Update</strong></div>
            <div>{generated.strftime("%d %b %Y • %I:%M %p IST")}</div>
            <div>Made with ❤️ by <strong><a style="text-decoration: none; color: inherit;" href="https://t.me/PERU_Yash" target="_blank" rel="noopener noreferrer">Peru Yash</a></strong></div>
        </div>
    </header>

    <main class="card">
        <div class="table-scroll">
            <table id="ipo-table">
                <thead>
                    <tr>
                        <th>Rank</th>
                        <th>IPO Name</th>
                        <th>Sub</th>
                        <th>Price (₹)</th>
                        <th>GMP</th>
                        <th>Lot</th>
                        <th>Est. Profit (₹)</th>
                        <th>Open</th>
                        <th>Close</th>
                        <th>Allotment Dt</th>
                        <th>Listing</th>
                        <th>Updated-On</th>
                    </tr>
                </thead>
                <tbody>
                    {table}
                </tbody>
            </table>
        </div>

        <section class="summary">
            <div class="metric">
                <div class="metric-label">Mainboard IPOs</div>
                <div class="metric-value">{total}</div>
            </div>
            <div class="metric">
                <div class="metric-label">Highest Est. Profit / Lot</div>
                <div class="metric-value">{currency(highest)}</div>
            </div>
            <div class="metric">
                <div class="metric-label">Lowest Est. Profit / Lot</div>
                <div class="metric-value">{currency(lowest)}</div>
            </div>
            <div class="metric">
                <div class="metric-label">Average Est. Profit / Lot</div>
                <div class="metric-value">{currency(average)}</div>
            </div>
        </section>

        <div class="actions">
            <button id="dl-png" class="download">
                ⬇ Download Full Table as PNG
            </button>
        </div>
    </main>

    <footer class="footer">
        <div>Made with ❤️ by <strong><a style="text-decoration: none; color: inherit;" href="https://t.me/PERU_Yash" target="_blank" rel="noopener noreferrer">Peru Yash</a></strong></div>
        <div>Data Source: InvestorGain · IPO GMP Live</div>
        <div>GMP is unofficial and may change at any time.</div>
    </footer>
</div>
<script src="https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js"></script>
<script>
document.getElementById("dl-png").addEventListener("click", function () {{
  var btn = this;
  btn.disabled = true;
html2canvas(document.body, {{
    scale: 2,
    backgroundColor: "#ffffff",
    windowWidth: 1550        // tell the clone: "pretend the window is 1280px wide"
}}).then(function (canvas) {{
    var a = document.createElement("a");
    a.download = "ipo-gmp-report.png";
    a.href = canvas.toDataURL("image/png");
    a.click();
    btn.disabled = false;
  }}).catch(function () {{
    btn.disabled = false;
  }});
}});
</script>
</body>
</html>
"""


def main() -> None:
    generated = current_ist()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = fetch_data(generated)
    HTML_PATH.write_text(build_html(rows, generated), encoding="utf-8")

    print(f"Generated {HTML_PATH}")
    print(f"Generated {PNG_PATH}")
    print(f"Rows: {len(rows)}")
    print(f"Generated: {generated.isoformat()}")


if __name__ == "__main__":
    main()
