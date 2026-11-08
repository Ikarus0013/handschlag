"""Build outreach.html - email queue + 10 buyer-inquiry templates.

    python3 outreach.py            # uses data/dealers.csv
"""
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent


def town(addr):
    m = re.search(r"\b\d{5}\s+([^,]+)", addr or "")
    return m.group(1).strip() if m else ""


def main():
    src = ROOT / "data" / "dealers.csv"
    if not src.exists():
        raise SystemExit(f"{src} not found. Run 'python3 run.py' first.")
    rows = list(csv.DictReader(open(src, encoding="utf-8")))
    leads = [r for r in rows if r["category"] == "car" and r["chain"] == "False" and r["email"]]
    leads.sort(key=lambda r: -int(r["lead_score"]))
    data = [{
        "id": r["place_id"], "name": r["name"], "email": r["email"], "person": r["contact_person"].split(",")[0].strip(),
        "phone": r["phone"], "town": town(r["address"]), "km": float(r["distance_km"] or 0),
        "rating": r["rating"], "reviews": r["reviews"], "lead": int(r["lead_score"]),
        "site": r["website"], "top": r["findings"].split(" | ")[:3],
    } for r in leads]
    tpl = (ROOT / "handschlag" / "outreach_template.html").read_text(encoding="utf-8")
    (ROOT / "outreach.html").write_text(tpl.replace("/*DATA*/[]", json.dumps(data, ensure_ascii=False)),
                                        encoding="utf-8")
    print(f"outreach.html: {len(data)} dealers with email")


if __name__ == "__main__":
    main()
