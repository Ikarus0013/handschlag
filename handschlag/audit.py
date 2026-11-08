"""Stages 1+2 - Gate + Website audit.

Fetches a dealer's homepage (plus a few cheap extras: inventory page,
sitemap) and records raw signals. Scoring lives in score.py so we can tune
weights without re-crawling. Results are cached per domain.
"""
import json
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
TIMEOUT = 15

NOT_OWN_SITE = {
    "facebook.com": "facebook", "instagram.com": "instagram",
    "mobile.de": "mobile.de", "autoscout24.de": "autoscout24",
    "autoscout24.com": "autoscout24", "google.com": "google", "business.site": "google",
    "linktr.ee": "linktree", "ebay.de": "ebay", "kleinanzeigen.de": "kleinanzeigen",
}
OEM = ("porsche.com", "bmw.de", "bmw.com", "mercedes-benz.de", "mercedes-benz.com", "audi.de",
       "volkswagen.de", "vw.de", "skoda-auto.de", "seat.de", "cupra.com", "ford.de", "toyota.de",
       "renault.de", "dacia.de", "hyundai.de", "hyundai.com", "kia.com", "opel.de", "peugeot.de",
       "citroen.de", "tesla.com", "mini.de", "volvocars.com", "nissan.de", "mazda.de", "fiat.de",
       "jeep.de", "honda.de", "suzuki.de", "mitsubishi-motors.de", "polestar.com", "byd.com",
       "mg.co.uk", "mgmotor.de", "subaru.de", "lexus.de", "jaguar.de", "landrover.de", "smart.com",
       "mclaren.com", "emilfrey.de", "ferrari.com", "lamborghini.com", "bentleymotors.com",
       "astonmartin.com", "maserati.com", "alfaromeo.de", "cupraofficial.de")
OEM_PREFIX = re.compile(r"^(mazda|toyota|kia|hyundai|renault|dacia|opel|ford|skoda|vw|seat|nissan|"
                        r"peugeot|citroen|suzuki|honda|mitsubishi|subaru|volvo|mg)-(autohaus|service|partner|haendler|händler)")
RETRY_STATUS = {403, 429, 500, 502, 503, 504}
BUILDERS = {
    "jimdo": r"jimdo", "wix": r"wix\.com|wixstatic", "ionos/1&1 baukasten": r"ionos|1und1|mywebsite-editor|homepagebaukasten",
    "strato baukasten": r"strato-editor|sitebuilder\.strato", "webnode": r"webnode",
    "squarespace": r"squarespace", "weebly": r"weebly", "site123": r"site123",
}
INVENTORY_PROVIDERS = {
    "mobile.de embed": r"home\.mobile\.de|mobile\.de/home|ws\.mobile\.de",
    "autoscout24 embed": r"autoscout24\.[a-z]+/(haendler|dealer|embed)|as24",
    "modix": r"modix", "eautoseller": r"eautoseller", "autrado": r"autrado",
    "pixelconcept": r"pixelconcept|pixel-concept", "dealerdesk": r"dealerdesk",
    "carlo": r"carlo-?online|carlo\.de", "autentio": r"autentio", "cosmo": r"cosmo-?consult",
    "dataforce": r"dataforce", "autolinkinx": r"autolink",
}
CONSENT_TOOLS = (r"cookiebot|usercentrics|borlabs|complianz|cookie-?law|klaro|consentmanager|"
                 r"ccm19|real-cookie-banner|cookieyes|onetrust|cookie-?notice|cookie-?consent|"
                 r"iubenda|cookiefirst|tarteaucitron|gdpr-cookie")
INVENTORY_LINK = re.compile(r"fahrzeug|bestand|gebrauchtwagen|angebot|b[öo]rse|autos|lager|"
                            r"inventory|vehicles|neuwagen|jahreswagen", re.I)
PRICE = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d{2}|,-)?\s?(?:€|EUR)|€\s?\d{1,3}(?:\.\d{3})+")


def domain_of(url):
    host = urlparse(url if "//" in url else "http://" + url).hostname or ""
    return host.lower().removeprefix("www.")


def _get(session, url, retries=0):
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt < retries:
                time.sleep(4 * (attempt + 1))
                continue
            raise
        if r.status_code in RETRY_STATUS and attempt < retries:
            time.sleep(4 * (attempt + 1))
            continue
        return r, round(time.time() - t0, 2)


def _text_years(pattern, text):
    return [int(y) for y in re.findall(pattern, text) if 1995 <= int(y) <= date.today().year + 1]


def probe_https(host):
    """What does a visitor typing/clicking https://host see? valid | cert_invalid | none"""
    try:
        r = requests.get(f"https://{host}/", headers={"User-Agent": UA}, timeout=(5, 10),
                         allow_redirects=True)
        return "valid" if r.url.startswith("https://") else "redirects_to_http"
    except requests.exceptions.SSLError:
        return "cert_invalid"
    except Exception:
        return "none"


def _ensure_https_probe(a, cache_file):
    if a.get("site_type") == "own" and "https_status" not in a and a.get("ok") is not None:
        host = urlparse(a.get("input_url") if "//" in a.get("input_url", "") else "http://" + a.get("input_url", "")).hostname
        if host:
            a["https_status"] = probe_https(host)
            a["google_link_https"] = a.get("input_url", "").startswith("https://")
            _save(cache_file, a)
    return a


def audit_site(url: str, cache_dir: Path, refresh=False):
    dom = domain_of(url)
    cache_file = cache_dir / f"{dom}.json"
    if cache_file.exists() and not refresh:
        cached = json.loads(cache_file.read_text())
        if cached.get("input_url") != url and cached.get("site_type") == "own":
            cached["input_url"] = url
            cached.pop("https_status", None)
        if cached.get("ok") or cached.get("site_type") != "own" or cached.get("retried"):
            return _ensure_https_probe(cached, cache_file)

    a = {"input_url": url, "domain": dom, "audited": date.today().isoformat()}
    for suffix, kind in NOT_OWN_SITE.items():
        if dom == suffix or dom.endswith("." + suffix):
            a.update(site_type=kind, ok=False)
            return _save(cache_file, a)
    if any(dom == o or dom.endswith("." + o) for o in OEM) or OEM_PREFIX.match(dom):
        a.update(site_type="oem", ok=False)
        return _save(cache_file, a)
    a["site_type"] = "own"
    a["retried"] = True

    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"})
    s.verify = True
    try:
        try:
            r, secs = _get(s, url, retries=2)
        except requests.exceptions.SSLError:
            s.verify = False
            requests.packages.urllib3.disable_warnings()
            r, secs = _get(s, url, retries=1)
            a["ssl_error"] = True
    except Exception as e:
        a.update(ok=False, error=type(e).__name__)
        return _save(cache_file, a)

    a.update(ok=r.ok, status=r.status_code, final_url=r.url, load_secs=secs,
             html_kb=round(len(r.content) / 1024))
    if not r.ok:
        a["blocked"] = r.status_code in (401, 403, 429, 503)
        return _save(cache_file, a)

    html = r.text
    soup = BeautifulSoup(html, "html.parser")
    final = r.url
    t = (soup.title.get_text(strip=True) if soup.title else "").lower()
    if len(r.content) < 8000 and re.search(r"sicherheitspr|just a moment|attention required|captcha|challenge|ddos|bot protection|access denied", t + " " + html.lower()[:3000]):
        a.update(ok=False, blocked=True, status=f"{r.status_code} bot-challenge")
        return _save(cache_file, a)

    # follow framesets / meta-refresh to the real content (old sites)
    a["frameset"] = bool(soup.find(["frameset", "frame"]))
    for _ in range(2):
        nxt = None
        mr = soup.find("meta", attrs={"http-equiv": re.compile("refresh", re.I)})
        if mr and "url=" in (mr.get("content") or "").lower():
            nxt = urljoin(final, re.split(r"url=", mr["content"], flags=re.I)[1].strip(" '\""))
        elif a["frameset"]:
            frames = [f.get("src") for f in soup.find_all("frame") if f.get("src")]
            if frames:
                nxt = urljoin(final, frames[-1])  # usually the main content frame
        if not nxt:
            break
        try:
            r2, _ = _get(s, nxt, retries=1)
            if not r2.ok:
                break
            html, final = html + r2.text, r2.url
            soup = BeautifulSoup(html, "html.parser")
            a["followed"] = final
        except Exception:
            break
    low = html.lower()

    # --- tech ---
    a["https"] = final.startswith("https://")
    vp = soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)})
    a["viewport"] = bool(vp)
    gen = soup.find("meta", attrs={"name": re.compile("^generator$", re.I)})
    a["generator"] = (gen.get("content") or "").strip()[:80] if gen else ""
    a["builder"] = next((k for k, p in BUILDERS.items() if re.search(p, low)), "")
    m = re.findall(r"jquery[.-]?(?:min[.-])?(\d)\.(\d{1,2})(?:\.(\d{1,2}))?", low) or \
        re.findall(r"jquery\.min\.js\?ver=(\d)\.(\d{1,2})", low)
    a["jquery"] = ".".join(x for x in m[0] if x) if m else ""
    a["flash"] = bool(re.search(r"\.swf\b|shockwave", low))
    a["marquee"] = bool(soup.find("marquee"))
    a["layout_tables"] = len(soup.find_all("table"))
    a["fixed_width_iframes"] = sum(
        1 for f in soup.find_all("iframe")
        if (f.get("width") or "").isdigit() and int(f.get("width")) >= 800)
    footer_text = " ".join(t.get_text(" ") for t in soup.find_all(["footer"])) or soup.get_text(" ")[-3000:]
    cy = _text_years(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?(\d{4})", footer_text.lower() + " " + low[-6000:])
    a["copyright_year"] = max(cy) if cy else None
    uy = _text_years(r"wp-content/uploads/(\d{4})/", low)
    a["newest_upload_year"] = max(uy) if uy else None
    a["scripts"] = len(soup.find_all("script", src=True))

    # --- seo ---
    a["title"] = (soup.title.get_text(strip=True) if soup.title else "")[:120]
    md = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    a["meta_description"] = bool(md and (md.get("content") or "").strip())
    a["h1_count"] = len(soup.find_all("h1"))
    ld = " ".join(t.get_text() for t in soup.find_all("script", type="application/ld+json"))
    a["schema_any"] = bool(ld.strip())
    a["schema_local"] = bool(re.search(r'"(AutoDealer|AutomotiveBusiness|LocalBusiness|AutoRepair)"', ld))
    a["schema_vehicle"] = bool(re.search(r'"(Vehicle|Car|Product|Offer)"', ld))
    a["og"] = bool(soup.find("meta", property=re.compile("^og:")))
    a["lang_attr"] = bool(soup.html and soup.html.get("lang"))

    # --- conversion ---
    a["tel_link"] = "href=\"tel:" in low or "href='tel:" in low
    a["whatsapp"] = bool(re.search(r"wa\.me|api\.whatsapp|whatsapp\.com/send", low))
    a["form"] = bool(soup.find("form"))
    a["opening_hours"] = bool(re.search(r"öffnungszeit|oeffnungszeit|geöffnet|mo\.?\s*[-–]\s*fr", low))

    # --- legal (strong German pitch hooks) ---
    links = [(x.get("href") or "", x.get_text(" ", strip=True).lower()) for x in soup.find_all("a")]
    a["impressum"] = any("impressum" in h.lower() or "impressum" in t for h, t in links)
    a["datenschutz"] = any(re.search(r"datenschutz|privacy", h.lower() + " " + t) for h, t in links)
    a["js_rendered"] = len(links) < 12 and a["scripts"] >= 5
    for key, paths in (("impressum", ("/impressum", "/impressum/", "/impressum.html", "/impressum.php")),
                       ("datenschutz", ("/datenschutz", "/datenschutz/", "/datenschutzerklaerung/", "/datenschutz.html"))):
        if a[key]:
            continue
        for pth in paths:
            try:
                rr = s.get(urljoin(final, pth), timeout=8, allow_redirects=True)
                if rr.ok and key[:8] in rr.text.lower():
                    a[key] = True
                    break
            except Exception:
                pass
    a["google_fonts_external"] = "fonts.googleapis.com" in low or "fonts.gstatic.com" in low
    a["tracking"] = bool(re.search(r"googletagmanager|google-analytics|gtag\(|fbq\(|facebook\.net/.*/fbevents", low))
    a["consent_tool"] = bool(re.search(CONSENT_TOOLS, low))

    # --- inventory ---
    inv_links = [urljoin(final, h) for h, t in links
                 if INVENTORY_LINK.search(t) or INVENTORY_LINK.search(urlparse(h).path)]
    inv_links = [u for u in dict.fromkeys(inv_links) if u.startswith("http")]
    a["external_inventory_link"] = next((u for u in inv_links if domain_of(u) != dom and
                                         re.search(r"mobile\.de|autoscout", u)), "")
    inv_html = html
    own_inv = next((u for u in inv_links if domain_of(u) == dom), "")
    if own_inv:
        try:
            ri, _ = _get(s, own_inv)
            if ri.ok:
                inv_html = html + ri.text
                a["inventory_page"] = ri.url
        except Exception:
            pass
    il = inv_html.lower()
    a["inventory_provider"] = next((k for k, p in INVENTORY_PROVIDERS.items() if re.search(p, il)), "")
    a["inventory_iframe"] = bool(re.search(r"<iframe[^>]+(mobile\.de|autoscout24)", il))
    a["price_mentions"] = len(PRICE.findall(inv_html))

    # --- sitemap ---
    try:
        sm = s.get(urljoin(final, "/sitemap.xml"), timeout=8, allow_redirects=True)
        head = sm.text[:3000].lower()
        a["sitemap"] = sm.ok and ("<urlset" in head or "<sitemapindex" in head)
    except Exception:
        a["sitemap"] = False

    _save(cache_file, a)
    return _ensure_https_probe(a, cache_file)


def _save(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return data
