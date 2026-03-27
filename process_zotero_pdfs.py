# Zotero processor for FedHarv
#
# For PDFs not retrieved by one of the harvesters, these will be saved in a citations.ris file in the output folder.
# After importing citations.ris into Zotero, you can select all of these citations in Zotero, left click, and try to 
# "Find fulltext". After the process is complete, remember to export all items and PDFs before closing the window.
# These will be available in a new folder "Exported Items" in your original output folder.

License: MIT License
Copyright 2026 Pascal V. Calarco <pcalarco@uwindsor.ca>
This script was developed with the help of Google Gemini Pro 3.1 and Microsoft Copilot

import os
import shutil
import re
import sys
import configparser
import csv
import requests
import time
from urllib.parse import unquote

# CONFIGURATION
def load_config():
    config = configparser.ConfigParser()
    # Preserve key case
    config.optionxform = str
    
    config_path = r"C:\Users\pvcal\Documents\Scripts\config.ini"
    if not os.path.exists(config_path):
        print(f"CRITICAL: Config file not found at {config_path}")
        sys.exit(1)
    
    config.read(config_path)
    
    try:
        output_dir = config.get('General', 'OutputDir')
        year = config.get('Search', 'Year')
        email = config.get('General', 'Email')
    except (configparser.NoSectionError, configparser.NoOptionError) as e:
        print(f"CRITICAL: Missing configuration in config.ini: {e}")
        sys.exit(1)
    
    base_dir = os.path.join(r"C:\Users\pvcal\Documents\Scripts", output_dir)
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

def download_pdf(url, dest_path):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        with requests.get(url, stream=True, timeout=30, headers=headers) as r:
            if r.status_code == 200:
                # Basic check for PDF content type or magic bytes
                chunk = next(r.iter_content(chunk_size=1024))
                if b'%PDF-' in chunk:
                    with open(dest_path, 'wb') as f:
                        f.write(chunk)
                        for chunk in r.iter_content(chunk_size=4096):
                            f.write(chunk)
                    return True
    except Exception as e:
        print(f"Download failed for {url}: {e}")
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
