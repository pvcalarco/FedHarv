# SPDX-License-Identifier: AGPL-3.0-only
"""
----------------------------------------------------
Zotero processor for FedHarv

For PDFs not retrieved by one of the harvesters, these will be saved in a citations.ris file in the output folder.

After importing citations.ris into Zotero, you can select all of these citations in Zotero, left click, and try to "Find fulltext". After the process is complete, remember to export all items and PDFs before closing the window.

These will be available in a new folder "Exported Items" in your original output folder.

Copyright (C) 2026 Pascal V. Calarco <pcalarco@uwindsor.ca>

 This program is free software: you can redistribute it and/or modify
 it under the terms of the GNU Affero General Public License as
 published by the Free Software Foundation, either version 3 of the
 License. 

 This program is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU Affero General Public License for more details.

 You should have received a copy of the GNU Affero General Public License
 along with this program.  If not, see <https://www.gnu.org/licenses/>.

## 🤖 AI Assistance & Authorship Disclosure

**FedHarv** was designed, architected, and verified by **Pascal Calarco**. 

During the development process, AI-augmented coding tools (Google Gemini and GitHub Copilot) were utilized to:
* Generate boilerplate code and initial function structures.
* Refactor logic for performance (e.g., implementing multi-threading).
* Assist with documentation, licensing (AGPL-v3), and testing suites.

All AI-generated suggestions have been manually reviewed, tested, and integrated by the author to ensure technical accuracy, scholarly metadata standards, and adherence to best practices in library and information science.
----------------------------------------------------
"""

import os
import shutil
import re
import sys
import configparser
import csv
import requests
import time
from urllib.parse import unquote, urljoin, urlparse, urlunparse

def _normalize_doi(value):
    if not value:
        return None
    doi = str(value).strip().lower().replace('https://doi.org/', '')
    return doi if doi.startswith('10.') else None

def _load_exported_dois(exported_csv_path):
    if not os.path.exists(exported_csv_path):
        return set()

    with open(exported_csv_path, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        return set()

    doi_col = None
    for c in ('DOI', 'Doi', 'doi', 'DO'):
        if c in rows[0]:
            doi_col = c
            break
    if not doi_col:
        return set()

    dois = set()
    for row in rows:
        doi = _normalize_doi(row.get(doi_col))
        if doi:
            dois.add(doi)
    return dois

def _load_report_dois(report_path):
    try:
        with open(report_path, 'r', encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            dois = set()
            for row in reader:
                doi = _normalize_doi(row.get('DOI'))
                if doi:
                    dois.add(doi)
            return dois
    except Exception:
        return set()

def resolve_base_dir(script_dir, preferred_base_dir):
    """Pick the output folder and year whose harvest report best matches Exported Items DOIs."""
    exported_csv = os.path.join(script_dir, 'Exported Items', 'Exported Items.csv')
    exported_dois = _load_exported_dois(exported_csv)
    if not exported_dois:
        return preferred_base_dir, None

    best_base = preferred_base_dir
    best_overlap = 0
    best_year = None

    for root, _, files in os.walk(script_dir):
        for name in files:
            if not (name.startswith('harvest_report_') and name.endswith('.csv')):
                continue
            report_path = os.path.join(root, name)
            report_dois = _load_report_dois(report_path)
            if not report_dois:
                continue
            overlap = len(exported_dois.intersection(report_dois))
            if overlap > best_overlap:
                best_overlap = overlap
                best_base = os.path.dirname(report_path)
                m = re.search(r'harvest_report_(\d{4})\d{4}_\d{8}\.csv$', name)
                if m:
                    best_year = m.group(1)

    if best_overlap > 0 and os.path.abspath(best_base) != os.path.abspath(preferred_base_dir):
        print(f"Auto-detected output folder from DOI overlap: {best_base} ({best_overlap} matches)")

    return best_base, best_year

def _resolve_output_dir_template(output_dir, start_date, end_date):
    if not output_dir:
        return output_dir

    start_year = start_date.split('-')[0] if start_date else ''
    end_year = end_date.split('-')[0] if end_date else ''

    replacements = {
        "StartDate": start_date,
        "EndDate": end_date,
        "StartYear": start_year,
        "EndYear": end_year,
    }

    expanded = str(output_dir)
    for key, value in replacements.items():
        expanded = expanded.replace(f"{{{key}}}", value)
        expanded = expanded.replace(f"[{key}]", value)
    return expanded

# CONFIGURATION
def load_config():
    config = configparser.ConfigParser()
    # Preserve key case
    config.optionxform = str  # type: ignore[assignment]

    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_candidates = [
        os.path.join(script_dir, "config.ini"),
        os.path.join(os.path.dirname(script_dir), "config.ini")
    ]

    config_path = None
    for candidate in config_candidates:
        if os.path.exists(candidate):
            config_path = candidate
            break

    if not config_path:
        print("CRITICAL: Config file not found. Checked:")
        for candidate in config_candidates:
            print(f" - {candidate}")
        sys.exit(1)
    
    config.read(config_path)
    
    try:
        output_dir = config.get('General', 'OutputDir').strip()
        email = config.get('General', 'Email').strip().strip('[]')
        start_date = config.get('Search', 'StartDate').strip()
        end_date = config.get('Search', 'EndDate').strip()
        output_dir = _resolve_output_dir_template(output_dir, start_date, end_date)

        if config.has_option('Search', 'Year'):
            year = config.get('Search', 'Year').strip()
        else:
            # Backward-compatible fallback for modern config files.
            year = start_date.split('-')[0]
    except (configparser.NoSectionError, configparser.NoOptionError) as e:
        print(f"CRITICAL: Missing configuration in config.ini: {e}")
        sys.exit(1)

    if not os.path.isabs(output_dir):
        base_dir = os.path.abspath(os.path.join(script_dir, output_dir))
    else:
        base_dir = output_dir

    base_dir, detected_year = resolve_base_dir(script_dir, base_dir)
    if detected_year and detected_year != year:
        print(f"Auto-detected year label from matched report: {detected_year}")
        year = detected_year

    return base_dir, year, email

def fetch_green_oa_url(doi, email):
    try:
        url = f"https://api.unpaywall.org/v2/{doi}?email={email}"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            best_loc = data.get('best_oa_location')
            if best_loc and best_loc.get('url_for_pdf'):
                return best_loc.get('url_for_pdf')
            # Fallback: Check other locations
            for loc in data.get('oa_locations', []):
                if loc.get('url_for_pdf'):
                    return loc.get('url_for_pdf')
    except Exception as e:
        print(f"Error checking Unpaywall for {doi}: {e}")
    return None

def _build_http_session():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'application/pdf,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Connection': 'keep-alive'
    })
    return session

def _candidate_pdf_urls(url):
    candidates = []

    def add(u):
        if u and u not in candidates:
            candidates.append(u)

    add(url)
    parsed = urlparse(url)

    # Upgrade http to https when possible.
    if parsed.scheme == 'http':
        add(urlunparse(parsed._replace(scheme='https')))

    host = parsed.netloc.lower()
    path = parsed.path

    # Drop noisy query strings that often break direct PDF fetches.
    if parsed.query:
        add(urlunparse(parsed._replace(query='')))

    # Publisher-specific cleanups.
    if 'mdpi.com' in host and '/pdf' in path and parsed.query:
        add(urlunparse(parsed._replace(query='')))

    if 'onlinelibrary.wiley.com' in host and '/pdfdirect/' in path:
        add(url.replace('/pdfdirect/', '/pdf/'))

    if 'tandfonline.com' in host and 'needAccess=' in parsed.query:
        add(urlunparse(parsed._replace(query='')))

    return candidates

def _extract_pdf_url_from_html(html, page_url):
    if not html:
        return None

    patterns = [
        r'<meta\s+name=["\']citation_pdf_url["\']\s+content=["\'](.*?)["\']',
        r'<meta\s+property=["\']citation_pdf_url["\']\s+content=["\'](.*?)["\']',
        r'href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']',
    ]

    for pattern in patterns:
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            return urljoin(page_url, m.group(1).strip())
    return None

def _stream_pdf_to_path(session, url, dest_path, verify_ssl=True):
    try:
        with session.get(url, stream=True, timeout=45, allow_redirects=True, verify=verify_ssl) as r:
            if r.status_code != 200:
                return False, False

            chunk_iter = r.iter_content(chunk_size=8192)
            first_chunk = next(chunk_iter, b'')

            content_type = (r.headers.get('Content-Type') or '').lower()
            content_disposition = (r.headers.get('Content-Disposition') or '').lower()
            looks_like_pdf = (
                b'%PDF-' in first_chunk[:2048] or
                'application/pdf' in content_type or
                '.pdf' in content_disposition
            )

            if not looks_like_pdf:
                return False, False

            with open(dest_path, 'wb') as f:
                if first_chunk:
                    f.write(first_chunk)
                for chunk in chunk_iter:
                    if chunk:
                        f.write(chunk)
            return True, False
    except requests.exceptions.SSLError:
        return False, True
    except Exception:
        return False, False

def download_pdf(url, dest_path):
    session = _build_http_session()
    last_error = None

    for candidate_url in _candidate_pdf_urls(url):
        try:
            ok, ssl_error = _stream_pdf_to_path(session, candidate_url, dest_path, verify_ssl=True)
            if ok:
                return True

            # Some endpoints have broken cert chains; retry without SSL verification only in that case.
            if ssl_error:
                ok, _ = _stream_pdf_to_path(session, candidate_url, dest_path, verify_ssl=False)
                if ok:
                    return True

            # If direct PDF fetch failed, try parsing landing HTML for citation_pdf_url/pdf links.
            page = session.get(candidate_url, timeout=25, allow_redirects=True, verify=True)
            if page.status_code == 200 and 'text/html' in (page.headers.get('Content-Type') or '').lower():
                extracted_pdf = _extract_pdf_url_from_html(page.text, page.url)
                if extracted_pdf:
                    for html_candidate in _candidate_pdf_urls(extracted_pdf):
                        ok, ssl_error = _stream_pdf_to_path(session, html_candidate, dest_path, verify_ssl=True)
                        if ok:
                            return True
                        if ssl_error:
                            ok, _ = _stream_pdf_to_path(session, html_candidate, dest_path, verify_ssl=False)
                            if ok:
                                return True
        except Exception as e:
            last_error = e

    if last_error:
        print(f"Download failed for {url}: {last_error}")
    return False

def parse_ris_map(ris_path):
    print(f"Parsing RIS file: {ris_path}")
    mapping = {} # DOI -> Relative Path (L1)
    
    current_doi = None
    current_pdf = None
    l1_count = 0
    
    if not os.path.exists(ris_path):
        print(f"Error: RIS file not found at {ris_path}")
        return {}

    try:
        with open(ris_path, 'r', encoding='utf-8-sig') as f:
            for line in f:
                line = line.strip()
                if line.startswith("DO  -"):
                    current_doi = line[5:].strip()
                elif line.startswith("L1  -"):
                    # Potential file link
                    raw_path = line[5:].strip()
                    # Zotero sometimes exports: files/123/filename.pdf
                    # We want to capture it, but watch out for duplicates (unlikely in standardized RIS but possible)
                    # If multiple L1s exist (rare in Zotero RIS export for one item), prefer the one ending in .pdf
                    if not current_pdf or (raw_path.lower().endswith('.pdf') and not current_pdf.lower().endswith('.pdf')):
                         current_pdf = raw_path
                    l1_count += 1
                elif line.startswith("ER  -"):
                    if current_doi:
                        # normalize DOI
                        clean_doi = current_doi.lower().replace('https://doi.org/', '').strip()
                        if current_pdf:
                            # URL decode the path (e.g. %20 -> space)
                            clean_pdf = unquote(current_pdf)
                            mapping[clean_doi] = clean_pdf
                        else:
                            # Optional: Track items without PDF in RIS if needed for deeper debugging
                            pass
                    current_doi = None
                    current_pdf = None
    except Exception as e:
        print(f"Error parsing RIS: {e}")
        return {}
        
    print(f"Found {len(mapping)} PDF mappings in RIS.")
    
    if len(mapping) == 0:
        print("\n" + "="*60)
        print("CRITICAL WARNING: No PDF mappings found in the RIS file!")
        print("="*60)
        print(f"Scanned {l1_count} 'L1' tags total.")
        print("Possible causes:")
        print("1. You did not select 'Export Files' when exporting from Zotero.")
        print("2. The export format was not RIS.")
        print("3. The items in Zotero do not actually have attached PDF files.")
        print("Please re-export from Zotero ensuring 'Export Files' is CHECKED.")
        print("="*60 + "\n")
        
    return mapping

def process_targets(base_dir, zotero_base, mapping, email):
    target_root = os.path.join(base_dir, "Items_Only_Link") 
    miss_report_file = os.path.join(base_dir, "zotero_miss_report.csv")

    success_count = 0
    fail_count = 0
    
    print(f"Scanning target folders in: {target_root}")
    if zotero_base:
        print(f"Looking for PDFs in: {zotero_base}")
    else:
        print("No Zotero base directory found. Will rely on Green OA Download.")
    
    missed_items = []

    for root, dirs, files in os.walk(target_root):
        if "link.txt" in files:
            link_path = os.path.join(root, "link.txt")
            try:
                # 1. Read DOI from link.txt
                with open(link_path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    # Format: DOI: https://doi.org/10.1234/...
                    match = re.search(r'(10\.\d{4,9}/[-._;()/:A-Z0-9]+)', content, re.IGNORECASE)
                    if not match:
                        print(f"Skipping {root}: Could not parse DOI from '{content}'")
                        missed_items.append({'Path': root, 'DOI': 'Parse Error', 'Reason': 'Could not extract DOI from link.txt'})
                        fail_count += 1
                        continue
                        
                    doi = match.group(1).lower()
                
                # Check Zotero Map First
                zotero_ok = False
                if doi in mapping and zotero_base:
                    rel_path = mapping[doi]
                    rel_path = rel_path.replace('/', os.sep).replace('\\', os.sep)
                    source_pdf = os.path.join(zotero_base, rel_path)
                    
                    if os.path.exists(source_pdf):
                        # Proceed with Zotero PDF
                        dest_pdf = os.path.join(root, "article.pdf")
                        shutil.copy2(source_pdf, dest_pdf)
                        print(f"Processed (Zotero): {doi} -> {root}")
                        zotero_ok = True
                    else:
                        print(f"Warning: Map exists but file missing at {source_pdf}")

                if not zotero_ok:
                    # Fallback to Green OA
                    print(f"Trying Green OA for {doi}...")
                    oa_url = fetch_green_oa_url(doi, email)
                    if oa_url:
                        dest_pdf = os.path.join(root, "article.pdf")
                        print(f"Found OA URL: {oa_url}. Downloading...")
                        if download_pdf(oa_url, dest_pdf):
                            print(f"Processed (Green OA): {doi} -> {root}")
                            zotero_ok = True
                            time.sleep(1) # Be polite to servers
                        else:
                            print(f"Failed to download PDF from {oa_url}")
                    else:
                        print(f"No Green OA found for {doi}")

                if zotero_ok:
                    # Update Metadata
                    # Move Operation successful, finalize
                    contents_file = os.path.join(root, "contents")
                    with open(contents_file, 'w', encoding='utf-8') as f:
                        f.write("article.pdf")
                    os.remove(link_path)
                    success_count += 1
                else:
                    missed_items.append({'Path': root, 'DOI': doi, 'Reason': 'DOI not found in Zotero map AND no Green OA PDF found'})
                    fail_count += 1
                
            except Exception as e:
                print(f"Error processing {root}: {e}")
                missed_items.append({'Path': root, 'DOI': 'Unknown', 'Reason': f'Exception: {str(e)}'})
                fail_count += 1

    # Write Miss Report
    if missed_items:
        with open(miss_report_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['Path', 'DOI', 'Reason'])
            writer.writeheader()
            writer.writerows(missed_items)
        print(f"\nReport of missed items written to: {miss_report_file}")

    print(f"\n--- Processing Complete ---")
    print(f"Successfully processed: {success_count} items")
    print(f"Skipped/Missing: {fail_count} items")

if __name__ == "__main__":
    base_dir, year_label, email = load_config()
    print(f"Using Base Directory: {base_dir}")
    print(f"Target Year Label: {year_label}")
    print(f"Contact Email: {email}")
    
    # Check RIS location
    ris_file_root = os.path.join(base_dir, f"citations-{year_label}.ris")
    ris_file_sub = os.path.join(base_dir, f"citations-{year_label}", f"citations-{year_label}.ris")
    ris_file_exported = os.path.join(base_dir, "Exported Items", "Exported Items.ris")
    
    ris_file = None
    zotero_base = None

    if os.path.exists(ris_file_exported):
        ris_file = ris_file_exported
        zotero_base = os.path.join(base_dir, "Exported Items")
        print(f"Found RIS in 'Exported Items' folder: {ris_file}")
    elif os.path.exists(ris_file_root):
        ris_file = ris_file_root
        zotero_base = base_dir 
        print(f"Found RIS at root: {ris_file}")
    elif os.path.exists(ris_file_sub):
        ris_file = ris_file_sub
        zotero_base = os.path.join(base_dir, f"citations-{year_label}")
        print(f"Found RIS in subfolder: {ris_file}")
    else:
        print(f"Warning: RIS file not found.")
        print(f"Checked: {ris_file_root}")
        print(f"Checked: {ris_file_sub}")
        print("Proceeding with Green OA Download attempt for ALL items (since no RIS map).")
        # sys.exit(1) # Don't exit, allow Green OA to try
        
    doi_map = parse_ris_map(ris_file) if ris_file else {}
    process_targets(base_dir, zotero_base, doi_map, email)
