"""Stage 3 - Scoring.

site_score   0-100  higher = more outdated / more to fix  (= bigger opportunity)
dealer_score 0-100  higher = better reputation & more established (= worth pitching / buying from)
lead_score   0-100  combination used for ranking
Each penalty produces a human-readable finding we can reuse in the pitch pack.
"""
import math
import re
from datetime import date

YEAR = date.today().year

# (code, points, finding text)  - tune weights here, no re-crawl needed
def site_findings(a):
    f = []
    add = lambda code, pts, txt: f.append((code, pts, txt))

    if a.get("site_type") == "oem":
        return f  # manufacturer platform - dealer can't change it, not a lead
    if a.get("blocked"):
        add("blocked", 0, f"Could not audit - site blocks bots (HTTP {a.get('status')}), check by hand")
        return f
    if a.get("site_type") != "own":
        add("no_own_site", 100, f"No own website - Google profile links to {a.get('site_type')}")
        return f
    if not a.get("ok"):
        add("site_down", 85, f"Website not reachable ({a.get('error') or a.get('status')})")
        return f

    js = a.get("js_rendered")
    if js:
        add("js_rendered", 0, "JS-rendered site - link-based checks skipped, verify by hand")
    # tech
    hs = a.get("https_status")
    if hs == "cert_invalid" and a.get("google_link_https"):
        add("ssl_warning_google", 20, "Google profile link opens a full-page browser security warning ('Your connection is not private')")
    elif hs == "cert_invalid":
        add("ssl_warning", 14, "Broken SSL certificate - https:// shows a browser security warning")
    elif not a.get("https"):
        add("no_https", 12, "No HTTPS - browsers show 'Not secure'")
    if not a.get("viewport"):
        add("no_viewport", 15, "Not mobile-optimised (no viewport tag)")
    if a.get("flash") or a.get("frameset") or a.get("marquee"):
        add("ancient_tech", 15, "Ancient web tech (Flash / frames / marquee)")
    if a.get("layout_tables", 0) >= 5:
        add("table_layout", 6, "Table-based layout (1990s/2000s style)")
    if a.get("fixed_width_iframes"):
        add("fixed_iframe", 6, "Fixed-width embed breaks on phones")
    if a.get("builder"):
        add("builder", 5, f"DIY website builder ({a['builder']})")
    jq = a.get("jquery") or ""
    if jq and jq.split(".")[0] in ("1", "2"):
        add("old_jquery", 4, f"Outdated jQuery {jq}")
    cy, uy = a.get("copyright_year"), a.get("newest_upload_year")
    if cy and cy <= YEAR - 2:
        add("old_copyright", 8, f"Footer says © {cy}")
    if uy and uy <= YEAR - 4:
        add("stale_content", 8, f"Newest uploaded image is from {uy}")
    if (a.get("load_secs") or 0) > 3:
        add("slow", 6, f"Slow first response ({a['load_secs']}s)")
    if (a.get("html_kb") or 0) > 1500:
        add("heavy", 3, f"Heavy page ({a['html_kb']} KB HTML)")

    # seo
    title = (a.get("title") or "").strip()
    if not title:
        add("no_title", 6, "Missing page title")
    elif len(title) < 25 or not re.search(r"auto|gebraucht|fahrzeug|kfz|wagen|car", title, re.I):
        add("weak_title", 4, f"Weak title: '{title[:60]}'")
    if not a.get("meta_description"):
        add("no_meta_desc", 5, "No meta description")
    if not a.get("h1_count"):
        add("no_h1", 3, "No H1 headline")
    if not a.get("schema_local"):
        add("no_local_schema", 6, "No LocalBusiness/AutoDealer schema (invisible to AI answers)")
    if not a.get("og"):
        add("no_og", 3, "No Open Graph - shared links look broken on WhatsApp")
    if not a.get("sitemap"):
        add("no_sitemap", 3, "No sitemap.xml")

    # inventory
    if a.get("inventory_iframe"):
        add("inventory_iframe", 10, "Cars shown in mobile.de/AutoScout iframe - Google can't index them")
    elif a.get("external_inventory_link") and a.get("price_mentions", 0) < 3:
        add("inventory_external", 8, "Cars only via link to mobile.de/AutoScout - traffic leaks away")
    elif a.get("price_mentions", 0) < 3 and not a.get("inventory_provider") and not js:
        add("no_inventory", 6, "No vehicles visible on the website")

    # conversion
    if not a.get("tel_link") and not js:
        add("no_tel_link", 4, "Phone number not tappable on mobile")
    if not a.get("whatsapp"):
        add("no_whatsapp", 2, "No WhatsApp contact")
    if not a.get("opening_hours"):
        add("no_hours", 2, "Opening hours not on homepage")

    # legal - DE specific, strong hooks
    if not a.get("impressum") and not js:
        add("no_impressum", 12, "No Impressum link (legal requirement, abmahnfähig)")
    if not a.get("datenschutz") and not js:
        add("no_datenschutz", 10, "No Datenschutz link (GDPR)")
    if a.get("google_fonts_external") and not a.get("consent_tool"):
        add("google_fonts", 6, "Loads Google Fonts from Google servers (known Abmahnung risk)")
    if a.get("tracking") and not a.get("consent_tool"):
        add("tracking_no_consent", 8, "Tracking without detectable cookie consent (GDPR, verify)")
    return f


def site_score(findings):
    return min(100, sum(p for _, p, _ in findings))


def dealer_score(rating, reviews):
    if not rating:
        return 15  # unknown - small but not zero
    r = max(0.0, min(1.0, (rating - 3.5) / 1.4))           # 3.5 -> 0, 4.9 -> 1
    n = max(0.0, min(1.0, math.log10(max(reviews, 1)) / math.log10(400)))  # 400+ reviews -> 1
    return round(100 * (0.55 * r + 0.45 * n))


CATEGORY_RULES = [
    ("camper", r"reisemobil|wohnmobil|caravan|camper|wohnwagen"),
    ("motorbike", r"motorrad|bike|roller|moto|harley|ducati|yamaha|kawasaki"),
    ("truck", r"lkw|nutzfahrzeug|truck|transporter-?center|trailer|anhänger"),
    ("rental", r"vermietung|rent|sixt|europcar|hertz|avis"),
]


def category(name):
    n = (name or "").lower()
    if re.search(r"werkstatt|reparatur|autoservice|kfz-?service|reifen|lackier|abschlepp|autoglas|tuning|aufbereitung|pflege", n) \
            and not re.search(r"automobil|autohaus|handel|cars|autos|gebrauchtwagen|verkauf", n):
        return "workshop"
    for cat, pat in CATEGORY_RULES:
        if re.search(pat, n):
            return cat
    return "car"


def lead_score(s_score, d_score, is_chain):
    # opportunity weighted by dealer quality; chains usually have a central agency
    v = s_score * (0.35 + 0.65 * d_score / 100)
    if is_chain:
        v *= 0.4
    return round(v)
