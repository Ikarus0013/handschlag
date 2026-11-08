"""Contact extraction: email + owner/managing director from Impressum / Kontakt.

German sites must list an email and the responsible person in the Impressum,
so this is the most reliable public source. Cached per domain.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings()
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"

EMAIL = re.compile(r"[a-z0-9._%+-]+\s?(?:@|\(at\)|\[at\]|\{at\}| at )\s?[a-z0-9.-]+\s?(?:\.|\(dot\)|\[dot\]| dot )\s?[a-z]{2,6}", re.I)
BAD_EMAIL = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js)$|sentry|wixpress|example\.|domain\.|"
                       r"@(2x|3x)|noreply|no-reply|u00|@(.*\.)?(dogado|ionos|strato|hosteurope|all-inkl|df\.eu|1und1|"
                       r"jimdo|wix|webgo|netcup|hetzner|mittwald|checkdomain|domainfactory|"
                       r"united-domains|datenschutz-?generator|e-recht24|ihk)\.", re.I)
PERSON = re.compile(
    r"(?:Geschäftsführer(?:in)?|Geschaeftsfuehrer|Inhaber(?:in)?|Inh\.|Vertreten durch|"
    r"vertretungsberechtigte[r]? (?:Geschäftsführer|Gesellschafter)|Verantwortlich(?:er)?(?: für den Inhalt)?(?: nach [^:]{0,30})?)"
    r"\s*[:\-–]?\s*(?:Herr|Frau)?\s*"
    r"([A-ZÄÖÜ][a-zäöüß\-]+(?:\s+[A-ZÄÖÜ]\.)?(?:\s+(?:von |van |de )?[A-ZÄÖÜ][a-zäöüß\-]+){1,2})")
NOT_NAME = re.compile(r"GmbH|Straße|Str\.|Hamburg|Deutschland|Telefon|Kontakt|Amtsgericht|Handels|"
                      r"Registergericht|Sitz|Gesellschaft|Autohaus|Automobile|Impressum|Geschäftsf|Geschaeftsf|"
                      r"Vertretung|Vertreten|Unsere|Angebot|Inhaber|Herr\b|Frau\b|Team|Fahrzeug|Service|"
                      r"Verantwortlich|Datenschutz|Kunden|Willkommen|Startseite|Hier|Ihr\b|Ihre\b", re.I)


def _get(url):
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=(5, 10), allow_redirects=True, verify=False)
        return r if r.ok else None
    except Exception:
        return None


def _clean_email(raw):
    e = raw.lower().replace(" ", "")
    e = re.sub(r"\(at\)|\[at\]|\{at\}|at(?=[a-z0-9-]+\.)", "@", e) if "@" not in e else e
    e = re.sub(r"\(dot\)|\[dot\]", ".", e)
    return e.strip(".")


STREETISH = re.compile(r"(str|weg|allee|damm|platz|ring|chaussee|gasse|stieg|deich|kamp|hof|"
                       r"tel|fax|mail|email|ust|steuer|registe?r|sitz)$", re.I)


def _tidy_name(raw):
    words = [w for w in raw.split() if not w.endswith("-")]
    while words and STREETISH.search(words[-1]):
        words.pop()
    if len(words) >= 3 and not (re.fullmatch(r"[A-ZÄÖÜ]\.", words[1]) or words[1] in ("von", "van", "de")):
        words = words[:2]
    if len(words) < 2 or any(len(w) < 2 for w in words[-1:]):
        return ""
    return " ".join(words)


def extract(html, own_domain):
    soup = BeautifulSoup(html, "html.parser")
    mails = [a["href"][7:].split("?")[0] for a in soup.find_all("a", href=True)
             if a["href"].lower().startswith("mailto:")]
    text = soup.get_text(" ", strip=True)
    mails += EMAIL.findall(text)
    mails = [_clean_email(m) for m in mails]
    mails = [m for m in dict.fromkeys(mails) if "@" in m and not BAD_EMAIL.search(m) and len(m) < 60]
    # prefer the dealer's own domain, then info@/kontakt@/verkauf@
    def rank(m):
        own = own_domain.split(".")[0] in m.split("@")[1]
        role = re.match(r"(info|kontakt|verkauf|mail|office|service)@", m) is not None
        return (not own, not role, len(m))
    mails.sort(key=rank)
    people = []
    for m in PERSON.finditer(text):
        n = _tidy_name(m.group(1))
        if n and not NOT_NAME.search(n) and n not in people:
            people.append(n)
    return mails, people


def find_contacts(site_url, cache_dir: Path):
    dom = (urlparse(site_url).hostname or "").removeprefix("www.")
    cf = cache_dir / f"{dom}.json"
    if cf.exists():
        return json.loads(cf.read_text())
    out = {"domain": dom, "emails": [], "people": [], "impressum_url": ""}
    home = _get(site_url)
    if home is not None:
        soup = BeautifulSoup(home.text, "html.parser")
        pages = []
        for a in soup.find_all("a", href=True):
            t = (a.get_text(" ", strip=True) + " " + a["href"]).lower()
            if "impressum" in t or "kontakt" in t or "imprint" in t:
                pages.append(urljoin(home.url, a["href"]))
        if not pages:
            pages = [urljoin(home.url, "/impressum"), urljoin(home.url, "/kontakt")]
        html = home.text
        for u in list(dict.fromkeys(pages))[:3]:
            r = _get(u)
            if r is not None:
                html += r.text
                if "impressum" in u.lower() and not out["impressum_url"]:
                    out["impressum_url"] = r.url
        out["emails"], out["people"] = extract(html, dom)
        out["emails"], out["people"] = out["emails"][:3], out["people"][:2]
    cf.parent.mkdir(parents=True, exist_ok=True)
    cf.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out


def run(urls, cache_dir: Path, workers=16):
    urls = list(dict.fromkeys(urls))
    res = {}
    with ThreadPoolExecutor(workers) as ex:
        for i, (u, c) in enumerate(zip(urls, ex.map(lambda u: find_contacts(u, cache_dir), urls)), 1):
            c["people"] = [n for n in c.get("people", []) if not NOT_NAME.search(n)]
            c["emails"] = [m for m in c.get("emails", []) if not BAD_EMAIL.search(m)]
            res[u] = c
            if i % 100 == 0:
                print(f"[contacts] {i}/{len(urls)}", flush=True)
    print(f"[contacts] {sum(1 for c in res.values() if c['emails'])}/{len(urls)} with email, "
          f"{sum(1 for c in res.values() if c['people'])} with owner/GF name")
    return res


def phone_kind(phone):
    d = re.sub(r"\D", "", phone or "")
    d = d[2:] if d.startswith("49") else d.lstrip("0")
    return "mobile" if d.startswith("1") and len(d) >= 10 else ("landline" if d else "")
