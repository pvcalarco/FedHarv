# FedHarv — User Guide

This guide takes you from a clean machine to a finished harvest and a DSpace import. For how the
system works internally, see **[DOCUMENTATION.md](DOCUMENTATION.md)**.

FedHarv discovers open-access articles for your institution over a date range and packages them
as **DSpace Simple Archive Format (SAF)** — ready to import — plus citation and report files.

---

## 1. Prerequisites

- **Python 3.9 or newer.**
- **Internet access** to the scholarly APIs (OpenAlex, Crossref, Unpaywall, DataCite, DOAJ).
- A **headless Chromium** browser for the PDF fallback (installed in step 2).
- *Optional:* an **Elsevier Scopus** API key (PDFs for Elsevier/ScienceDirect) and a **Sherpa
  Romeo** key (self-archiving policy). A **Crossref Plus** token gives faster, more reliable
  Crossref access.
- *For import:* access to a **DSpace 7/8** instance and its CLI.

---

## 2. Installation

Run everything from the repository root.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium          # one-time; required for the PDF browser fallback
```

> **Tip:** to also run the test suite, `pip install -r requirements-dev.txt` (adds `pytest`),
> then `pytest`.

---

## 3. Configuration

Copy the example and edit it:

```bash
cp config.example.FedHarv.ini config.ini    # Windows: copy config.example.FedHarv.ini config.ini
```

A minimal working `config.ini`:

```ini
[General]
Email = you@institution.edu          ; required for the polite API pool
OutputDir = FedHarv_{StartDate}_{EndDate}

[Search]
Affiliation = Your University Name    ; the institution to harvest
StartDate = 2026-01-01
EndDate   = 2026-03-31
StrictAffiliationMatch = yes
```

Key points (full reference: DOCUMENTATION.md §7):

- **`Email`** — strongly recommended; OpenAlex/Unpaywall/Crossref give faster, more reliable
  responses to identified callers. Can instead be set via `.env` as `OPENALEX_EMAIL`.
- **`Affiliation`** — how your institution appears in author affiliations (e.g.
  `University of Windsor`). Matching is fuzzy, so the common short name usually works.
- **`OutputDir`** — supports `{StartDate}`, `{EndDate}`, `{StartYear}`, `{EndYear}` placeholders.
- **Secrets** — put API keys under `[Authentication]` (`ScopusKey`, `SherpaKey`,
  `CrossrefPlusToken`), or supply `SCOPUS_API_KEY` via a `.env` file. **`config.ini` always wins
  over `.env`.**

### Department folders (`[Mappings]`)

Map affiliation keywords to the sub-folders items are filed under. The **longest** matching
keyword wins; items matching several map to a `Multiple` folder; unmatched items use the
affiliation name.

```ini
[Mappings]
computer science = School_of_Computer_Science
biology          = Faculty_of_Science_Biological_Sciences
```

### DSpace collection handles (`[Collections]`, optional)

Map each department folder to its DSpace collection handle so the generated import script targets
the right collection. Folders without an entry use `[DSpace] DefaultCollection`.

```ini
[DSpace]
AdminEmail = admin@institution.edu
BinPath = /dspace/bin/dspace
DefaultCollection = 123456789/0

[Collections]
School_of_Computer_Science            = 123456789/101
Faculty_of_Science_Biological_Sciences = 123456789/102
```

---

## 4. Running a harvest

```bash
python run_harvester.py                  # uses ./config.ini
python run_harvester.py --config other.ini
```

Progress is logged to the console **and** to `fedharv.log` (in the working directory). A run:
discovers from OpenAlex + Crossref, de-duplicates, enriches and filters to open-access items,
fetches PDFs (falling back to a headless browser), and writes the SAF packages and reports under
`OutputDir`.

> The output directory is **cleared at the start of each run**. The API cache
> (`.fedharv_cache/`) lives outside it and **persists**, so a re-run over the same range is much
> faster and lighter on the APIs.

---

## 5. Understanding the output

Under your `OutputDir`:

| Item | What it is |
|------|-----------|
| `Items_With_PDF/<dept>/item_NNN/` | Full SAF package **with** `article.pdf`. |
| `Items_Only_Link/<dept>/item_NNN/` | SAF package with a `link.txt` (DOI) — an OA item whose PDF couldn't be fetched. |
| `Green/<dept>/item_NNN/` | Green-OA items as metadata + DOI link (no PDF is fetched for green items). |
| `harvest_report_<start>_<end>.csv` | One row per packaged item (DOI, title, OA status, folder, PDF source…). |
| `citations.ris` | RIS citations for every link-only item — importable into Zotero/EndNote. |
| `department_publisher_report.csv` | Counts by department and publisher. |
| `author_registry.txt` | Your institution's authors seen in the harvest (depts, emails, ORCIDs). |
| `import_batch.sh` | Ready-to-run DSpace import commands. |
| `harvest_summary.txt` | Totals and breakdowns for the run. |

Only **Gold, Hybrid, Diamond, and Green** open-access items are packaged; Closed and Bronze are
skipped.

---

## 6. Importing into DSpace

`import_batch.sh` contains one `dspace import` command per department folder, targeting the
collection handle from `[Collections]` (or `DefaultCollection`). Review it, then run it on the
DSpace server (it expects the `BinPath` and `AdminEmail` from your config):

```bash
bash import_batch.sh
```

---

## 7. Backfilling PDFs (Zotero companion)

`process_zotero_pdfs.py` is a separate tool that tries to fill in PDFs for `Items_Only_Link/`
items — first from a **Zotero RIS export** (after using Zotero's "Find Available PDFs"), then from
**Unpaywall Green OA**. It reads the same `config.ini`. See **README.Zotero_Processor.md** for the
full workflow.

```bash
python process_zotero_pdfs.py
```

---

## 8. Caching & re-runs

- The cache (`.fedharv_cache/`) is keyed by DOI/ISSN and survives between runs.
- Tune freshness with `[General] CacheMaxAgeDays` (default 30; `0` = never expire).
- To force a fully fresh harvest, delete the cache directory.

---

## 9. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ModuleNotFoundError: No module named 'fedharv'` | Run from the **repository root** (where `run_harvester.py` and the `fedharv/` package live). |
| `playwright` errors / no PDFs from the browser step | Run `playwright install chromium` once in your venv. |
| Lots of `429` / rate-limit warnings | Expected under load — FedHarv backs off and retries. Set `Email` (and a `CrossrefPlusToken` if you have one) to improve your quota. |
| Empty or tiny output | Check `Affiliation` matches how your institution is named in author affiliations; try a shorter form. Widen the date range. |
| `WARNING: Email contact not provided…` | Set `[General] Email` (or `OPENALEX_EMAIL` in `.env`). |
| Want to inspect what happened | Read `fedharv.log` and `harvest_summary.txt`. |

---

## 10. Quick reference

```bash
# Setup
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
cp config.example.FedHarv.ini config.ini              # then edit

# Run
python run_harvester.py
# (optional) backfill PDFs, then import
python process_zotero_pdfs.py
bash OutputDir/import_batch.sh
```
