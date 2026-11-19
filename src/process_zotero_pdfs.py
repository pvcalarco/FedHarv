# SPDX-License-Identifier: AGPL-3.0-only
"""
Zotero PDF post-processor for FedHarv.

Integrates PDFs exported from Zotero (RIS with attached files) into FedHarv's
Items_Only_Link SAF packages, falling back to Unpaywall Green OA downloads when
a Zotero PDF is not available.

License: GNU Affero General Public License v3.0 (AGPL-3.0)
Copyright (C) 2026 Pascal V. Calarco <pcalarco@uwindsor.ca>
"""

import argparse
import csv
import os
import re
import shutil
import sys
import time
from urllib.parse import unquote, urlparse, urlunparse

import requests

from fedharv.config import ConfigError, ConfigManager, UNPAYWALL_API
from fedharv.pdf import BROWSER_USER_AGENT, download_file_stream, extract_pdf_url_from_html
from fedharv.utils import is_safe_http_url, normalize_doi


def _normalize_doi(value):
    doi = normalize_doi(value)
    return doi if doi and doi.startswith("10.") else None


def _load_exported_dois(exported_csv_path):
    if not os.path.exists(exported_csv_path):
        return set()

    with open(exported_csv_path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        return set()

    doi_col = next((c for c in ("DOI", "Doi", "doi", "DO") if c in rows[0]), None)
    if not doi_col:
        return set()

    return {doi for row in rows if (doi := _normalize_doi(row.get(doi_col)))}


def _load_report_dois(report_path):
    try:
        with open(report_path, "r", encoding="utf-8-sig", newline="") as f:
            return {doi for row in csv.DictReader(f) if (doi := _normalize_doi(row.get("DOI")))}
    except Exception:
        return set()


def resolve_base_dir(search_root, preferred_base_dir):
    """Pick the output folder and year whose harvest report best matches Exported Items DOIs."""
    exported_csv = os.path.join(preferred_base_dir, "Exported Items", "Exported Items.csv")
    if not os.path.exists(exported_csv):
        exported_csv = os.path.join(search_root, "Exported Items", "Exported Items.csv")
    exported_dois = _load_exported_dois(exported_csv)
    if not exported_dois:
        return preferred_base_dir, None

    best_base = preferred_base_dir
    best_overlap = 0
    best_year = None

    for root, dirs, files in os.walk(search_root):
        dirs[:] = [d for d in dirs if d not in (".git", ".venv", "venv", "__pycache__", "node_modules")]
        for name in files:
            if not (name.startswith("harvest_report_") and name.endswith(".csv")):
                continue
            report_path = os.path.join(root, name)
            report_dois = _load_report_dois(report_path)
            if not report_dois:
                continue
            overlap = len(exported_dois.intersection(report_dois))
            if overlap > best_overlap:
                best_overlap = overlap
                best_base = os.path.dirname(report_path)
                match = re.search(r"harvest_report_(\d{4})\d{4}_\d{8}\.csv$", name)
                if match:
                    best_year = match.group(1)

    if best_overlap > 0 and os.path.abspath(best_base) != os.path.abspath(preferred_base_dir):
        print(f"Auto-detected output folder from DOI overlap: {best_base} ({best_overlap} matches)")

    return best_base, best_year


def load_config(config_path=None):
    cfg = ConfigManager(config_path)
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base_dir = cfg.OUTPUT_DIR

    year = cfg.config.get("Search", "Year", fallback="").strip() or cfg.START_DATE.split("-")[0]
    base_dir, detected_year = resolve_base_dir(repo_root, base_dir)
    if detected_year and detected_year != year:
        print(f"Auto-detected year label from matched report: {detected_year}")
        year = detected_year

    return base_dir, year, cfg.EMAIL_CONTACT


def fetch_green_oa_url(doi, email, session):
    try:
        url = f"{UNPAYWALL_API}{doi}"
        r = session.get(url, params={"email": email}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            best_loc = data.get("best_oa_location") or {}
            if best_loc.get("url_for_pdf"):
                return best_loc["url_for_pdf"]
            for loc in data.get("oa_locations") or []:
                if isinstance(loc, dict) and loc.get("url_for_pdf"):
                    return loc["url_for_pdf"]
    except Exception as e:
        print(f"Error checking Unpaywall for {doi}: {e}")
    return None


def _build_http_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "application/pdf,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    })
    return session


def _candidate_pdf_urls(url):
    candidates = []

    def add(u):
        if u and is_safe_http_url(u) and u not in candidates:
            candidates.append(u)

    add(url)
    parsed = urlparse(url)

    if parsed.scheme == "http":
        add(urlunparse(parsed._replace(scheme="https")))

    if parsed.query:
        add(urlunparse(parsed._replace(query="")))

    if "onlinelibrary.wiley.com" in parsed.netloc.lower() and "/pdfdirect/" in parsed.path:
        add(url.replace("/pdfdirect/", "/pdf/"))

    return candidates


def download_pdf(url, dest_path, session, verify_ssl=True):
    last_error = None
    for candidate_url in _candidate_pdf_urls(url):
        try:
            if download_file_stream(candidate_url, dest_path, session, verify=verify_ssl):
                return True

            page = session.get(candidate_url, timeout=25, allow_redirects=True, verify=verify_ssl)
            if page.status_code == 200 and "text/html" in (page.headers.get("Content-Type") or "").lower():
                extracted_pdf = extract_pdf_url_from_html(page.text, page.url, include_href_fallback=True)
                if extracted_pdf:
                    for html_candidate in _candidate_pdf_urls(extracted_pdf):
                        if download_file_stream(html_candidate, dest_path, session, verify=verify_ssl):
                            return True
        except requests.exceptions.SSLError as e:
            last_error = f"TLS certificate verification failed ({e}). Pass --insecure only if trusted."
        except Exception as e:
            last_error = e

    if last_error:
        print(f"Download failed for {url}: {last_error}")
    return False


def _resolve_contained_path(base_dir, rel_path):
    """Resolve rel_path strictly inside base_dir, blocking absolute paths and '..' traversal."""
    if not base_dir or not rel_path:
        return None
    normalized = rel_path.replace("/", os.sep).replace("\\", os.sep)
    if os.path.isabs(normalized):
        return None

    base_real = os.path.realpath(base_dir)
    candidate_real = os.path.realpath(os.path.join(base_real, normalized))
    try:
        if os.path.commonpath([base_real, candidate_real]) != base_real:
            return None
    except ValueError:
        return None
    return candidate_real


def parse_ris_map(ris_path):
    print(f"Parsing RIS file: {ris_path}")
    mapping = {}
    current_doi = None
    current_pdf = None
    l1_count = 0

    if not ris_path or not os.path.exists(ris_path):
        print(f"Error: RIS file not found at {ris_path}")
        return {}

    try:
        with open(ris_path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DO  -"):
                    current_doi = line[5:].strip()
                elif line.startswith("L1  -"):
                    raw_path = line[5:].strip()
                    if not current_pdf or (
                        raw_path.lower().endswith(".pdf") and not current_pdf.lower().endswith(".pdf")
                    ):
                        current_pdf = raw_path
                    l1_count += 1
                elif line.startswith("ER  -"):
                    if current_doi and current_pdf:
                        clean_doi = _normalize_doi(current_doi)
                        if clean_doi:
                            mapping[clean_doi] = unquote(current_pdf)
                    current_doi = None
                    current_pdf = None
    except Exception as e:
        print(f"Error parsing RIS: {e}")
        return {}

    print(f"Found {len(mapping)} PDF mappings in RIS.")
    if not mapping:
        print("\n" + "=" * 60)
        print("CRITICAL WARNING: No PDF mappings found in the RIS file!")
        print("=" * 60)
        print(f"Scanned {l1_count} 'L1' tags total.")
        print("Ensure 'Export Files' was checked when exporting RIS from Zotero.")
        print("=" * 60 + "\n")

    return mapping


def process_targets(base_dir, zotero_base, mapping, email, verify_ssl=True):
    target_root = os.path.join(base_dir, "Items_Only_Link")
    miss_report_file = os.path.join(base_dir, "zotero_miss_report.csv")

    success_count = 0
    fail_count = 0
    missed_items = []

    print(f"Scanning target folders in: {target_root}")
    if zotero_base:
        print(f"Looking for PDFs in: {zotero_base}")
    else:
        print("No Zotero base directory found. Will rely on Green OA Download.")

    with _build_http_session() as session:
        for root, _, files in os.walk(target_root):
            if "link.txt" not in files:
                continue

            link_path = os.path.join(root, "link.txt")
            try:
                with open(link_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                match = re.search(r"(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", content, re.IGNORECASE)
                if not match:
                    print(f"Skipping {root}: Could not parse DOI from '{content}'")
                    missed_items.append({
                        "Path": root,
                        "DOI": "Parse Error",
                        "Reason": "Could not extract DOI from link.txt",
                    })
                    fail_count += 1
                    continue

                doi = match.group(1).lower()
                zotero_ok = False

                if doi in mapping and zotero_base:
                    rel_path = mapping[doi]
                    source_pdf = _resolve_contained_path(zotero_base, rel_path)
                    if source_pdf is None:
                        print(f"Security Warning: Rejected unsafe RIS path '{rel_path}' for {doi}")
                    elif os.path.isfile(source_pdf):
                        dest_pdf = os.path.join(root, "article.pdf")
                        shutil.copy2(source_pdf, dest_pdf)
                        print(f"Processed (Zotero): {doi} -> {root}")
                        zotero_ok = True
                    else:
                        print(f"Warning: Map exists but file missing at {source_pdf}")

                if not zotero_ok:
                    print(f"Trying Green OA for {doi}...")
                    oa_url = fetch_green_oa_url(doi, email, session)
                    if oa_url:
                        dest_pdf = os.path.join(root, "article.pdf")
                        print(f"Found OA URL: {oa_url}. Downloading...")
                        if download_pdf(oa_url, dest_pdf, session, verify_ssl=verify_ssl):
                            print(f"Processed (Green OA): {doi} -> {root}")
                            zotero_ok = True
                            time.sleep(1)
                        else:
                            print(f"Failed to download PDF from {oa_url}")
                    else:
                        print(f"No Green OA found for {doi}")

                if zotero_ok:
                    contents_file = os.path.join(root, "contents")
                    with open(contents_file, "w", encoding="utf-8") as f:
                        f.write("article.pdf")
                    os.remove(link_path)
                    success_count += 1
                else:
                    missed_items.append({
                        "Path": root,
                        "DOI": doi,
                        "Reason": "DOI not found in Zotero map AND no Green OA PDF found",
                    })
                    fail_count += 1
            except Exception as e:
                print(f"Error processing {root}: {e}")
                missed_items.append({"Path": root, "DOI": "Unknown", "Reason": f"Exception: {e}"})
                fail_count += 1

    if missed_items:
        with open(miss_report_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["Path", "DOI", "Reason"])
            writer.writeheader()
            writer.writerows(missed_items)
        print(f"\nReport of missed items written to: {miss_report_file}")

    print("\n--- Processing Complete ---")
    print(f"Successfully processed: {success_count} items")
    print(f"Skipped/Missing: {fail_count} items")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Merge Zotero-exported PDFs into FedHarv SAF packages")
    parser.add_argument("--config", default=None, help="Path to config.ini")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification for Green OA PDF downloads (not recommended)",
    )
    args = parser.parse_args(argv)

    if args.insecure:
        print("WARNING: TLS certificate verification is disabled (--insecure).")

    try:
        base_dir, year_label, email = load_config(args.config)
    except ConfigError as exc:
        print(f"CRITICAL: {exc}")
        return 1

    print(f"Using Base Directory: {base_dir}")
    print(f"Target Year Label: {year_label}")
    print(f"Contact Email: {email}")

    ris_candidates = [
        (os.path.join(base_dir, "Exported Items", "Exported Items.ris"), os.path.join(base_dir, "Exported Items")),
        (os.path.join(base_dir, f"citations-{year_label}.ris"), base_dir),
        (
            os.path.join(base_dir, f"citations-{year_label}", f"citations-{year_label}.ris"),
            os.path.join(base_dir, f"citations-{year_label}"),
        ),
    ]

    ris_file = None
    zotero_base = None
    for candidate_ris, candidate_base in ris_candidates:
        if os.path.exists(candidate_ris):
            ris_file = candidate_ris
            zotero_base = candidate_base
            print(f"Found RIS: {ris_file}")
            break

    if not ris_file:
        print("Warning: RIS file not found. Proceeding with Green OA download attempt for all items.")

    doi_map = parse_ris_map(ris_file) if ris_file else {}
    process_targets(base_dir, zotero_base, doi_map, email, verify_ssl=not args.insecure)
    return 0


if __name__ == "__main__":
    sys.exit(main())
