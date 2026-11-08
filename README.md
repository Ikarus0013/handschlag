# Handschlag

Handschlag finds car dealers in an area, audits their websites, and makes a lead report and an email queue.

## What it does

- Finds all car dealers in a radius around a point. It uses the Google Places API (New).
- Finds the real website of dealers that have no website on Google. It guesses domains, and it can also use the Brave Search API.
- Audits each dealer website. It checks HTTPS, mobile support, old web technology, page speed, and more.
- Gets the public email address and the managing director from the Impressum or Kontakt page.
- Gives each dealer a site score, a dealer score, and a lead score.
- Writes `data/dealers.csv` and `report.html`, a lead table that you can sort and filter.
- Writes `outreach.html`, an email queue with German message templates.

## Requirements

- Python 3.9 or newer
- A Google Cloud project with the "Places API (New)" enabled, and an API key
- Optional: a Brave Search API key
- An internet connection

**CAUTION:** The Google Places API and the Brave Search API can cost money. Look at the prices and quotas of each API before you start.

## Install

1. Clone the repository:

   ```bash
   git clone https://github.com/Ikarus0013/handschlag.git
   cd handschlag
   ```

2. Make a virtual environment:

   ```bash
   python3 -m venv .venv
   ```

3. Activate the virtual environment:

   ```bash
   source .venv/bin/activate
   ```

4. Install the dependencies:

   ```bash
   pip install -r requirements.txt
   ```

## Configure

1. Copy the example file:

   ```bash
   cp .env.example .env
   ```

2. Open `.env` and add your API keys.

**WARNING:** Do not commit the `.env` file. It contains your API keys. The `.gitignore` file excludes it.

| Variable | Required | Description |
|---|---|---|
| `GOOGLE_PLACES_KEY` | Yes | Google Places API (New) key. Get it in the Google Cloud Console (APIs & Services > Credentials). |
| `BRAVE_API_KEY` | No | Brave Search API key. Get it in the Brave Search API dashboard. The tool uses it only with `--brave-min-dealer`. |
| `HANDSCHLAG_LAT` | No | Latitude of the default search center. Default: `53.5511` (Hamburg). |
| `HANDSCHLAG_LON` | No | Longitude of the default search center. Default: `9.9937` (Hamburg). |

Environment variables have priority over the values in `.env`.

## Use

1. Show all options:

   ```bash
   python3 run.py --help
   ```

2. Start a search with a center point and a radius in km:

   ```bash
   python3 run.py --lat 53.55 --lon 9.99 --radius 30
   ```

3. Open `report.html` in a web browser.
4. Optional: search with Brave for missing websites of dealers with a dealer score of 60 or more:

   ```bash
   python3 run.py --brave-min-dealer 60
   ```

5. Make the email queue from `data/dealers.csv`:

   ```bash
   python3 outreach.py
   ```

6. Open `outreach.html` in a web browser.
7. Type your name, phone, budget, and town in the fields at the top.

The terminal shows the progress of each stage and the top 15 leads. The tool writes these files:

| Output | Content |
|---|---|
| `data/dealers.csv` | One row for each dealer, with scores, contact data, and findings |
| `report.html` | Lead table with filters, status, notes, and CSV export |
| `outreach.html` | Email queue with 10 German templates |

The status and notes in `report.html` and `outreach.html` stay in the local storage of your browser.

Other options:

| Option | Description |
|---|---|
| `--refresh` | Audit the websites again. The Places results stay in the cache. |
| `--max-calls N` | Set the maximum number of Google Places API calls. Default: 600. |
| `--workers N` | Set the number of parallel website audits. Default: 8. |
| `--ingest FILE ...` | Verify and add website URLs from JSON files. Each item has the keys `place_id` and `url`. |

To correct a website by hand, add a line to `data/overrides.csv` with the columns `name,website`.

## How it works

1. **Discover** (`handschlag/discover.py`): The tool divides the area into squares. It sends one Nearby Search request for each square. If a square gives 20 results, the tool divides it into 4 smaller squares. The results go into `data/places.json`.
2. **Find websites** (`handschlag/sitefinder.py`, `handschlag/brave.py`): For dealers without their own website, the tool guesses domains or uses Brave Search. It accepts a website only if the page shows the phone number or the street and postal code of the dealer.
3. **Audit** (`handschlag/audit.py`): The tool gets the homepage and some extra pages of each website. It records technical signals.
4. **Contacts** (`handschlag/contacts.py`): The tool reads the Impressum and Kontakt pages. It gets email addresses and the names of owners or managing directors.
5. **Score** (`handschlag/score.py`): The tool calculates the scores and the findings. A high site score means an old website. A high dealer score means a good reputation.
6. **Report** (`run.py`, `outreach.py`): The tool writes the CSV file and the HTML files from the templates.

All API results and website audits stay in a cache in `data/`. A second run with the same parameters does not use API calls again.

## Files

| File | Description |
|---|---|
| `run.py` | Main pipeline: discover, find websites, audit, contacts, score, report |
| `outreach.py` | Makes `outreach.html` from `data/dealers.csv` |
| `handschlag/discover.py` | Google Places search and `.env` key loader |
| `handschlag/sitefinder.py` | Domain guess and website verification |
| `handschlag/brave.py` | Brave Search website finder |
| `handschlag/audit.py` | Website audit |
| `handschlag/contacts.py` | Email and contact person extraction |
| `handschlag/score.py` | Scores and findings |
| `handschlag/report_template.html` | Template for `report.html` |
| `handschlag/outreach_template.html` | Template for `outreach.html` |
| `.env.example` | Example configuration |

## Limits

- The tool searches only the Google Places type `car_dealer`. The scores and templates are for German dealers.
- The website checks use text patterns. They can give wrong results. Check each lead by hand.
- Websites change. The audit and the contact extraction can stop working on some sites.
- The tool collects public business contact data. Obey the data protection law (for example GDPR) and the rules for commercial email in your country.
- Respect the terms of use of the Google Places API, the Brave Search API, and each website. Respect `robots.txt`.
- The website requests do not verify TLS certificates. This lets the tool audit sites with broken certificates.

## License

MIT. See [LICENSE](LICENSE).
