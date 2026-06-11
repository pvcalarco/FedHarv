# SPDX-License-Identifier: AGPL-3.0-only
import os
import csv
import xml.etree.ElementTree as ET
from xml.dom import minidom
from collections import defaultdict

from .config import CC_LICENSE_NAMES
from .utils import clean_text, sanitize_filename, clean_abstract

def map_to_dublin_core(item, enrich, all_affils, target_orcids):
    md = [] 
    def add(s, e, q, v):
        if v: md.append({'schema': s, 'element': e, 'qualifier': q, 'value': clean_text(v)})
    
    add('dc', 'title', None, item['title'])
    
    # Ensure full date string is captured
    pub_date = item.get('date')
    if pub_date:
        add('dc', 'date', 'issued', pub_date) 

    # Ensure DOI is always included when available from OpenAlex or CrossRef
    doi_value = item.get('doi')
    if not doi_value:
        raw_data = item.get('raw', {})
        if item.get('source') == 'openalex':
            doi_value = raw_data.get('doi')
        elif item.get('source') == 'crossref':
            doi_value = raw_data.get('DOI')
    
    add('dc', 'identifier', 'doi', doi_value)
    if doi_value: add('dc', 'identifier', 'uri', f"https://doi.org/{doi_value}")
    
    # Enhanced Creative Commons license reporting
    lic_uri = enrich.get('license')
    if lic_uri and lic_uri in CC_LICENSE_NAMES:
        add('dc', 'rights', None, CC_LICENSE_NAMES[lic_uri])
        add('dc', 'rights', 'uri', lic_uri)
    elif item.get('oa_status') == 'Diamond':
        add('dc', 'rights', None, 'Creative Commons CC-BY 4.0 International')
        add('dc', 'rights', 'uri', 'https://creativecommons.org/licenses/by/4.0/')
    else:
        add('dc', 'rights', None, item.get('oa_status'))
        if lic_uri:
            add('dc', 'rights', 'uri', lic_uri)
    
    add('dc', 'rights', 'policy', enrich.get('sherpa_uri'))
    for f in enrich.get('funders', []): add('dc', 'description', 'sponsorship', f)
    for aff in all_affils: add('organization', 'legalName', None, aff)
    for auth in item.get('norm_authors', []): 
        if isinstance(auth, dict): add('dc', 'contributor', 'author', auth.get('name'))
        else: add('dc', 'contributor', 'author', auth)
    for oid in target_orcids: add('person', 'identifier', 'orcid', oid)
    
    add('oaire', 'citation', 'title', item.get('journal'))
    if item.get('volume'): add('oaire', 'citation', 'volume', item['volume'])
    if item.get('issue'): add('oaire', 'citation', 'issue', item['issue'])
    if item.get('pages'): 
        add('dc', 'format', 'extent', item['pages'])
        parts = str(item['pages']).split('-')
        add('oaire', 'citation', 'startPage', parts[0])
        if len(parts) > 1: add('oaire', 'citation', 'endPage', parts[1])
    if item.get('issn'): add('dc', 'identifier', 'issn', item['issn'])
    
    abstract = enrich.get('abstract')
    if abstract: add('dc', 'description', 'abstract', clean_abstract(abstract))
    pub = item.get('publisher') or enrich.get('publisher')
    if pub: add('dc', 'publisher', None, pub)
    add('dc', 'type', None, item.get('doctype', 'Article'))
    add('dc', 'language', 'iso', 'en_CA')
    return md

def write_saf(metadata, item_dir, bitstream_file):
    schema_map = defaultdict(ET.Element)
    for field in metadata:
        prefix = field['schema']
        if prefix not in schema_map:
            root = ET.Element('dublin_core')
            root.set('schema', prefix)
            schema_map[prefix] = root
        el = ET.SubElement(schema_map[prefix], 'dcvalue')
        el.set('element', field['element'])
        if field['qualifier']: el.set('qualifier', field['qualifier'])
        else: el.set('qualifier', 'none')
        el.text = field['value']
    for prefix, root in schema_map.items():
        fname = "dublin_core.xml" if prefix == 'dc' else f"metadata_{prefix}.xml"
        with open(os.path.join(item_dir, fname), 'w', encoding='utf-8') as f:
            f.write(minidom.parseString(ET.tostring(root)).toprettyxml(indent="  "))
    if bitstream_file:
        with open(os.path.join(item_dir, 'contents'), 'w', encoding='utf-8') as f: f.write(bitstream_file)

def generate_import_scripts(base_dir, dspace_bin, dspace_email, collections=None, default_collection="123456789/0"):
    script_file = os.path.join(base_dir, "import_batch.sh")
    with open(script_file, 'w') as f:
        f.write("#!/bin/bash\n\n")
        for root, dirs, files in os.walk(base_dir):
            if any(fl == "dublin_core.xml" for fl in files): continue
            if any(d.startswith("item_") for d in dirs):
                 handle = (collections or {}).get(os.path.basename(root), default_collection)
                 f.write(f"echo 'Importing: {root}'\n")
                 f.write(f"{dspace_bin} import --add --eperson={dspace_email} --collection={handle} --source={root} --mapfile=mapfile_{sanitize_filename(root)}\n\n")

def write_report(filepath, row):
    exists = os.path.isfile(filepath)
    with open(filepath, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['DOI', 'Title', 'ISSN', 'Doc_Type', 'Source', 'Folder_Type', 'Dept', 'PDF_Status', 'PDF_Source', 'Sherpa_Policy', 'OA_Status', 'Notes'])
        if not exists: w.writeheader()
        w.writerow(row)

def generate_ris_block(item, enrich):
    dt = item.get('doctype', 'Article').lower()
    ty = "JOUR" # Default
    if "book chapter" in dt: ty = "CHAP"
    elif "book" in dt: ty = "BOOK"
    elif "conference" in dt or "proceeding" in dt: ty = "CONF"
    elif "dataset" in dt: ty = "DATA"
    elif "report" in dt: ty = "RPRT"
    
    lines = [f"TY  - {ty}"]
    lines.append(f"TI  - {clean_text(item.get('title'))}")
    
    for auth in item.get('norm_authors', []):
        if isinstance(auth, dict): name = auth.get('name')
        else: name = auth
        if name: lines.append(f"AU  - {name}")
        
    lines.append(f"JO  - {clean_text(item.get('journal'))}")
    
    date_val = item.get('date')
    if date_val:
        date_val = str(date_val).replace('-', '/')
        lines.append(f"PY  - {date_val}")
        
    if item.get('volume'): lines.append(f"VL  - {item['volume']}")
    if item.get('issue'): lines.append(f"IS  - {item['issue']}")
    
    if item.get('pages'):
        parts = str(item['pages']).split('-')
        lines.append(f"SP  - {parts[0]}")
        if len(parts) > 1: lines.append(f"EP  - {parts[1]}")
        
    if item.get('issn'): lines.append(f"SN  - {item['issn']}")
    if item.get('doi'): lines.append(f"DO  - {item['doi']}")
    
    abstract = enrich.get('abstract')
    if abstract: lines.append(f"AB  - {clean_text(abstract)}")
    
    if item.get('doi'): lines.append(f"UR  - https://doi.org/{item['doi']}")
    
    pub = item.get('publisher') or enrich.get('publisher')
    if pub: lines.append(f"PB  - {pub}")
    
    lines.append("ER  - \n")
    return "\n".join(lines)


class MetadataExporter:
    """Manages writing harvested data to CSV reports, RIS blocks, DSpace import packages (SAF), and author lists."""
    def __init__(self, output_dir, csv_file, ris_file, author_file, publisher_report_file):
        self.output_dir = output_dir
        self.csv_file = csv_file
        self.ris_file = ris_file
        self.author_file = author_file
        self.publisher_report_file = publisher_report_file
        self.csv_handle = None
        self.csv_writer = None
        self.ris_handle = None

    def open_handles(self):
        self.csv_handle = open(self.csv_file, 'a', newline='', encoding='utf-8')
        self.csv_writer = csv.DictWriter(self.csv_handle, fieldnames=['DOI', 'Title', 'ISSN', 'Doc_Type', 'Source', 'Folder_Type', 'Dept', 'PDF_Status', 'PDF_Source', 'Sherpa_Policy', 'OA_Status', 'Notes'])
        if os.path.getsize(self.csv_file) == 0:
            self.csv_writer.writeheader()
        
        self.ris_handle = open(self.ris_file, 'a', encoding='utf-8')

    def close_handles(self):
        if self.csv_handle:
            self.csv_handle.close()
        if self.ris_handle:
            self.ris_handle.close()

    def write_csv_row(self, row):
        if self.csv_writer:
            self.csv_writer.writerow(row)
            self.csv_handle.flush()

    def write_ris_entry(self, item, enrich):
        if self.ris_handle:
            ris_entry = generate_ris_block(item, enrich)
            self.ris_handle.write(ris_entry + "\n")
            self.ris_handle.flush()

    def generate_publisher_report(self, dept_publisher_breakdown):
        with open(self.publisher_report_file, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['Department', 'Publisher', 'Count'])
            for dept, pubs in sorted(dept_publisher_breakdown.items()):
                for pub, count in pubs.most_common():
                    w.writerow([dept, pub, count])

    def generate_author_registry(self, author_db, target_affil=None):
        header = f"Author Registry - {target_affil}" if target_affil else "Author Registry"
        with open(self.author_file, 'w', encoding='utf-8') as af:
            af.write(f"{header}\n{'=' * len(header)}\n")
            for name, data in sorted(author_db.items()):
                depts = "; ".join(sorted(data['depts'])) if data['depts'] else "Unknown"
                emails = ", ".join(sorted([e for e in data['emails'] if e])) if data['emails'] else "N/A"
                orcids = ", ".join(sorted([o for o in data['orcids'] if o])) if data['orcids'] else "N/A"
                af.write(f"{name} : {depts} : {emails} : {orcids}\n")
