"""
EudraGMDP India GMP Certificate Monitor
========================================
Scans EudraGMDP for new GMP certificates issued to Indian manufacturers.
Sends WhatsApp alerts via Twilio for new certificates and non-compliance reports.

Author: Shade Capital PMS — Compliance & Research Team
"""

import os
import json
import time
import logging
import requests
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup

# ─── LOGGING SETUP ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

# ─── CONFIGURATION ────────────────────────────────────────────────────────────

# EudraGMDP base URL for individual certificate pages
CERT_BASE_URL = "https://eudragmdp.ema.europa.eu/inspections/gmpc/prepReviewSubmittedGMPC.do?key="

# EudraGMDP search URL — search all GMP certificates for India
SEARCH_URL = (
    "https://eudragmdp.ema.europa.eu/inspections/gmpc/searchGMPCompliance.do"
    "?ctrl=searchGMPCResultControlList&action=Search"
    "&manufacturerCountry=IN"          # IN = India country code
    "&certificateType=GMPC"            # GMP Compliance Certificate only
    "&pageSize=100"
)

# Non-compliance search URL
NON_COMPLIANCE_URL = (
    "https://eudragmdp.ema.europa.eu/inspections/gmpc/searchGMPNonCompliance.do"
    "?ctrl=searchGMPNCResultControlList&action=Search"
    "&manufacturerCountry=IN"
    "&pageSize=100"
)

# Keywords to detect Indian addresses (fallback if country filter misses any)
INDIA_KEYWORDS = [
    "india", "maharashtra", "gujarat", "karnataka", "andhra pradesh",
    "telangana", "rajasthan", "himachal pradesh", "uttarakhand", "punjab",
    "haryana", "tamil nadu", "kerala", "west bengal", "uttar pradesh",
    "madhya pradesh", "sikkim", "goa", "mumbai", "pune", "ahmedabad",
    "hyderabad", "bangalore", "bengaluru", "chennai", "kolkata", "baddi",
    "vapi", "ankleshwar", "haridwar", "roorkee", "nalagarh", "solan",
    "paonta", "nagpur", "aurangabad", "vadodara", "surat", "indore",
    "bhopal", "dehradun", "chandigarh", "ludhiana", "amritsar", "jaipur",
    "udaipur", "kochi", "coimbatore", "vizag", "visakhapatnam", "mysuru",
    "mysore", "mangalore", "hubli", "nashik", "thane", "navi mumbai",
    "panvel", "silvassa", "daman", "diu", "pondicherry", "puducherry"
]

# Watchlist — companies to flag as PRIORITY alerts
# Edit this list to add/remove companies
WATCHLIST = [
    "zim laboratories",
    "sun pharmaceutical",
    "sun pharma",
    "cipla",
    "dr. reddy",
    "dr reddy",
    "lupin",
    "aurobindo",
    "zydus",
    "cadila",
    "torrent",
    "alkem",
    "glenmark",
    "ipca",
    "wockhardt",
    "biocon",
    "divi's laboratories",
    "divi laboratories",
    "granules",
    "laurus labs",
    "macleods",
    "mankind",
    "intas",
    "natco",
    "strides",
    "jubilant",
    "piramal",
    "hetero",
    "emcure",
    "unichem",
    "ajanta",
]

# Local state file — stores last seen certificate keys
STATE_FILE = Path("data/state.json")

# Twilio credentials — loaded from GitHub Secrets (environment variables)
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "")   # whatsapp:+14155238886
ALERT_TO_NUMBER    = os.environ.get("ALERT_TO_NUMBER", "")       # whatsapp:+91XXXXXXXXXX

# Email fallback (optional — uses Gmail SMTP)
GMAIL_USER     = os.environ.get("GMAIL_USER", "")
GMAIL_PASSWORD = os.environ.get("GMAIL_PASSWORD", "")  # App password, not real password
ALERT_EMAIL_TO = os.environ.get("ALERT_EMAIL_TO", "")

# Request headers — polite scraping
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; GMP-Monitor-Bot/1.0; "
        "+https://github.com/YOUR_USERNAME/eudragmdp-alert)"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

# Delay between requests — be polite to EMA servers
REQUEST_DELAY_SECONDS = 2


# ─── STATE MANAGEMENT ─────────────────────────────────────────────────────────

def load_state() -> dict:
    """Load previously seen certificate IDs from local state file."""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "seen_certificates": [],     # List of cert IDs already alerted
        "seen_noncompliance": [],    # List of non-compliance IDs already alerted
        "last_run": None,
        "total_india_certs": 0
    }


def save_state(state: dict):
    """Persist state to file — committed back to repo by GitHub Actions."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)
    log.info(f"State saved — {len(state['seen_certificates'])} known certificates")


# ─── SCRAPING ─────────────────────────────────────────────────────────────────

def fetch_page(url: str, retries: int = 3) -> BeautifulSoup | None:
    """Fetch a URL and return parsed BeautifulSoup, with retry logic."""
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            time.sleep(REQUEST_DELAY_SECONDS)
            return BeautifulSoup(response.text, "html.parser")
        except requests.RequestException as e:
            log.warning(f"Attempt {attempt + 1} failed for {url}: {e}")
            time.sleep(5 * (attempt + 1))
    log.error(f"All retries failed for {url}")
    return None


def is_india_address(text: str) -> bool:
    """Check if an address string refers to an Indian location."""
    lower = text.lower()
    return any(keyword in lower for keyword in INDIA_KEYWORDS)


def is_watchlist_company(company_name: str) -> bool:
    """Check if a company is on the priority watchlist."""
    lower = company_name.lower()
    return any(watch in lower for watch in WATCHLIST)


def parse_certificate_row(row) -> dict | None:
    """
    Parse a single row from the EudraGMDP search results table.
    Returns a dict with certificate details, or None if not India.
    """
    try:
        cells = row.find_all("td")
        if len(cells) < 5:
            return None

        # Extract fields — column order: Cert Number | Manufacturer | Address | Authority | Date
        cert_number  = cells[0].get_text(strip=True)
        company_name = cells[1].get_text(strip=True)
        address      = cells[2].get_text(strip=True)
        authority    = cells[3].get_text(strip=True)
        issue_date   = cells[4].get_text(strip=True)

        # Get the link to the full certificate page
        link_tag = cells[0].find("a")
        cert_url = ""
        cert_key = ""
        if link_tag and link_tag.get("href"):
            href = link_tag["href"]
            cert_url = f"https://eudragmdp.ema.europa.eu{href}" if href.startswith("/") else href
            # Extract the key parameter
            if "key=" in href:
                cert_key = href.split("key=")[-1].split("&")[0]

        # Skip if not an Indian manufacturer
        if not is_india_address(address) and "india" not in company_name.lower():
            return None

        return {
            "id": cert_key or cert_number,
            "cert_number": cert_number,
            "company": company_name,
            "address": address,
            "authority": authority,
            "issue_date": issue_date,
            "url": cert_url,
            "is_watchlist": is_watchlist_company(company_name),
            "status": "compliant",
            "detected_at": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        log.debug(f"Row parse error: {e}")
        return None


def scrape_certificates() -> list[dict]:
    """
    Scrape EudraGMDP GMP compliance certificates for India.
    Returns list of certificate dicts.
    """
    log.info("Scraping GMP Compliance Certificates for India...")
    certificates = []
    page = 1

    while True:
        url = f"{SEARCH_URL}&pageNumber={page}"
        log.info(f"Fetching page {page}...")
        soup = fetch_page(url)

        if not soup:
            break

        # Find results table
        table = soup.find("table", {"class": "tableContent"}) or soup.find("table")
        if not table:
            log.info("No table found — end of results or site structure changed")
            break

        rows = table.find_all("tr")[1:]  # Skip header row
        if not rows:
            log.info(f"No rows on page {page} — done")
            break

        found_on_page = 0
        for row in rows:
            cert = parse_certificate_row(row)
            if cert:
                certificates.append(cert)
                found_on_page += 1

        log.info(f"Page {page}: {found_on_page} India certificates found")

        # Check for next page
        next_btn = soup.find("a", string=lambda t: t and "next" in t.lower())
        if not next_btn:
            break
        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    log.info(f"Total India GMP certificates found: {len(certificates)}")
    return certificates


def scrape_non_compliance() -> list[dict]:
    """
    Scrape EudraGMDP Non-Compliance reports for India.
    These are higher priority — companies that FAILED GMP inspection.
    """
    log.info("Scraping Non-Compliance reports for India...")
    reports = []
    page = 1

    while True:
        url = f"{NON_COMPLIANCE_URL}&pageNumber={page}"
        soup = fetch_page(url)
        if not soup:
            break

        table = soup.find("table", {"class": "tableContent"}) or soup.find("table")
        if not table:
            break

        rows = table.find_all("tr")[1:]
        if not rows:
            break

        for row in rows:
            try:
                cells = row.find_all("td")
                if len(cells) < 4:
                    continue

                company_name = cells[0].get_text(strip=True)
                address      = cells[1].get_text(strip=True)
                authority    = cells[2].get_text(strip=True)
                report_date  = cells[3].get_text(strip=True)

                if not is_india_address(address):
                    continue

                link_tag = cells[0].find("a")
                report_url = ""
                report_key = ""
                if link_tag and link_tag.get("href"):
                    href = link_tag["href"]
                    report_url = f"https://eudragmdp.ema.europa.eu{href}"
                    if "key=" in href:
                        report_key = href.split("key=")[-1].split("&")[0]

                reports.append({
                    "id": report_key or f"NC-{company_name[:20]}",
                    "company": company_name,
                    "address": address,
                    "authority": authority,
                    "issue_date": report_date,
                    "url": report_url,
                    "is_watchlist": is_watchlist_company(company_name),
                    "status": "non-compliant",
                    "detected_at": datetime.utcnow().isoformat(),
                })
            except Exception as e:
                log.debug(f"Non-compliance row error: {e}")
                continue

        next_btn = soup.find("a", string=lambda t: t and "next" in t.lower())
        if not next_btn:
            break
        page += 1

    log.info(f"Total India Non-Compliance reports found: {len(reports)}")
    return reports


# ─── ALERT FUNCTIONS ──────────────────────────────────────────────────────────

def send_whatsapp(message: str, is_urgent: bool = False):
    """Send a WhatsApp message via Twilio."""
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, ALERT_TO_NUMBER]):
        log.warning("Twilio credentials not configured — skipping WhatsApp alert")
        return False

    try:
        from twilio.rest import Client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        msg = client.messages.create(
            from_=TWILIO_FROM_NUMBER,
            to=ALERT_TO_NUMBER,
            body=message
        )
        log.info(f"WhatsApp sent — SID: {msg.sid}")
        return True
    except Exception as e:
        log.error(f"WhatsApp send failed: {e}")
        return False


def send_email(subject: str, body: str):
    """Send an email alert via Gmail SMTP."""
    if not all([GMAIL_USER, GMAIL_PASSWORD, ALERT_EMAIL_TO]):
        log.warning("Email credentials not configured — skipping email alert")
        return False

    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = GMAIL_USER
        msg["To"]      = ALERT_EMAIL_TO
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASSWORD)
            server.sendmail(GMAIL_USER, ALERT_EMAIL_TO, msg.as_string())

        log.info(f"Email sent to {ALERT_EMAIL_TO}")
        return True
    except Exception as e:
        log.error(f"Email send failed: {e}")
        return False


def format_whatsapp_alert(cert: dict, alert_type: str = "new") -> str:
    """Format a WhatsApp message for a certificate alert."""
    priority = "🚨 URGENT" if cert["is_watchlist"] or cert["status"] == "non-compliant" else "📋 NEW"
    status_emoji = "✅" if cert["status"] == "compliant" else "❌"

    if cert["status"] == "non-compliant":
        msg = f"""🚨 *EU GMP NON-COMPLIANCE ALERT*
━━━━━━━━━━━━━━━━━━━━
*Company:* {cert['company']}
*Location:* {cert['address']}
*Authority:* {cert['authority']}
*Date:* {cert['issue_date']}
❌ *STATUS: NON-COMPLIANT*
{'⭐ WATCHLIST COMPANY' if cert['is_watchlist'] else ''}

*View Report:*
{cert['url']}

_EudraGMDP India Monitor | Shade Capital PMS_"""
    else:
        msg = f"""{priority} *EU GMP CERTIFICATE — INDIA*
━━━━━━━━━━━━━━━━━━━━
{status_emoji} *Company:* {cert['company']}
📍 *Location:* {cert['address']}
🏛 *Authority:* {cert['authority']}
📄 *Cert No:* {cert.get('cert_number', 'N/A')}
📅 *Date:* {cert['issue_date']}
{'⭐ WATCHLIST COMPANY' if cert['is_watchlist'] else ''}

*View Certificate:*
{cert['url']}

_EudraGMDP India Monitor | Shade Capital PMS_"""
    return msg


def format_digest_message(new_certs: list, new_nc: list) -> str:
    """Format a summary digest message when multiple alerts fire together."""
    total = len(new_certs) + len(new_nc)
    watchlist_hits = [c for c in (new_certs + new_nc) if c["is_watchlist"]]

    msg = f"""📊 *EudraGMDP India Scan Report*
📅 {datetime.utcnow().strftime('%d %b %Y, %H:%M UTC')}
━━━━━━━━━━━━━━━━━━━━
✅ New Certificates: {len(new_certs)}
❌ Non-Compliance Reports: {len(new_nc)}
⭐ Watchlist Matches: {len(watchlist_hits)}
━━━━━━━━━━━━━━━━━━━━"""

    if watchlist_hits:
        msg += "\n\n*⭐ WATCHLIST COMPANIES:*"
        for c in watchlist_hits:
            emoji = "✅" if c["status"] == "compliant" else "❌"
            msg += f"\n{emoji} {c['company']}"

    if new_nc:
        msg += "\n\n*❌ NON-COMPLIANCE ALERTS:*"
        for c in new_nc[:5]:  # Limit to 5 in digest
            msg += f"\n• {c['company']} ({c['authority']})"

    if new_certs:
        msg += "\n\n*✅ NEW CERTIFICATES:*"
        for c in new_certs[:5]:
            msg += f"\n• {c['company']}"

    msg += f"\n\n🔗 https://eudragmdp.ema.europa.eu"
    msg += f"\n_Shade Capital PMS | EudraGMDP Monitor_"

    return msg


# ─── MAIN LOGIC ───────────────────────────────────────────────────────────────

def run():
    """Main execution function — scrape, compare, alert."""
    log.info("=" * 60)
    log.info("EudraGMDP India GMP Monitor — Starting scan")
    log.info(f"Run time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    log.info("=" * 60)

    # Load previous state
    state = load_state()
    seen_certs = set(state.get("seen_certificates", []))
    seen_nc    = set(state.get("seen_noncompliance", []))

    # ── SCRAPE ──
    all_certs = scrape_certificates()
    all_nc    = scrape_non_compliance()

    # ── FIRST RUN CHECK ──
    # On the first run, create a baseline without sending alerts.
    is_first_run = (
        not seen_certs
        and not seen_nc
        and state.get("last_run") is None
    )

    if is_first_run:
        log.info("FIRST RUN: Creating baseline — no alerts will be sent")
        new_certs = []
        new_nc = []
    else:
        # ── FIND NEW ONES ──
        new_certs = [c for c in all_certs if c["id"] not in seen_certs]
        new_nc = [c for c in all_nc if c["id"] not in seen_nc]

    log.info(f"New certificates found: {len(new_certs)}")
    log.info(f"New non-compliance reports: {len(new_nc)}")

    # ── SEND ALERTS ──
    if new_certs or new_nc:
        # Watchlist items get individual WhatsApp messages
        watchlist_certs = [c for c in (new_certs + new_nc) if c["is_watchlist"]]
        for cert in watchlist_certs:
            msg = format_whatsapp_alert(cert)
            send_whatsapp(msg, is_urgent=True)
            time.sleep(2)  # Avoid rate limiting

        # Non-compliance reports get individual alerts even if not on watchlist
        non_watchlist_nc = [c for c in new_nc if not c["is_watchlist"]]
        for cert in non_watchlist_nc:
            msg = format_whatsapp_alert(cert)
            send_whatsapp(msg, is_urgent=True)
            time.sleep(2)

        # Send a digest for all new items (summary)
        total = len(new_certs) + len(new_nc)
        if total > 0:
            digest = format_digest_message(new_certs, new_nc)

            # Email the full digest always
            email_body = digest.replace("*", "").replace("━", "-")
            send_email(
                subject=f"[EudraGMDP] {total} new India GMP alert(s) — {datetime.utcnow().strftime('%d %b %Y')}",
                body=email_body
            )

            # WhatsApp digest only if no individual alerts were sent
            if not watchlist_certs and not non_watchlist_nc:
                send_whatsapp(digest)

    else:
        log.info("No new certificates — no alerts sent")

    # ── UPDATE STATE ──
    state["seen_certificates"]  = list(seen_certs | {c["id"] for c in all_certs})
    state["seen_noncompliance"] = list(seen_nc    | {c["id"] for c in all_nc})
    state["last_run"]           = datetime.utcnow().isoformat()
    state["total_india_certs"]  = len(all_certs)
    save_state(state)

    log.info("=" * 60)
    log.info(f"Scan complete. Total India certs on record: {len(state['seen_certificates'])}")
    log.info("=" * 60)

    return {
        "new_certificates": len(new_certs),
        "new_non_compliance": len(new_nc),
        "total_india_certs": len(all_certs),
    }


if __name__ == "__main__":
    result = run()
    print(f"\nScan result: {result}")
