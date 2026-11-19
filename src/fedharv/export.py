# SPDX-License-Identifier: AGPL-3.0-only
import csv
import os
import shlex
import xml.etree.ElementTree as ET

from .config import CC_LICENSE_NAMES
from .utils import clean_abstract, clean_text, normalize_doi, sanitize_filename

_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _sanitize_csv_cell(value):
    if isinstance(value, str) and value and value[0] in _CSV_FORMULA_PREFIXES:
        return "'" + value
    return value


def map_to_dublin_core(item, enrich, all_affils, paper_orcids):
    md = []

    def add(schema, element, qualifier, value):
        cleaned = clean_text(value) if value else None
        if cleaned:
            md.append({"schema": schema, "element": element, "qualifier": qualifier, "value": cleaned})

    add("dc", "title", None, item.get("title"))

    pub_date = item.get("date")
    if pub_date:
        add("dc", "date", "issued", pub_date)

    doi_value = item.get("doi")
    if not doi_value:
        raw_data = item.get("raw") or {}
        if item.get("source") == "openalex":
            doi_value = raw_data.get("doi")
        elif item.get("source") == "crossref":
            doi_value = raw_data.get("DOI")
    doi_value = normalize_doi(doi_value)

    if doi_value:
        add("dc", "identifier", "doi", doi_value)
        add("dc", "identifier", "uri", f"https://doi.org/{doi_value}")

    lic_uri = enrich.get("license")
    if lic_uri and lic_uri in CC_LICENSE_NAMES:
        add("dc", "rights", None, CC_LICENSE_NAMES[lic_uri])
        add("dc", "rights", "uri", lic_uri)
    else:
        add("dc", "rights", None, item.get("oa_status"))
        if lic_uri:
            add("dc", "rights", "uri", lic_uri)

    add("dc", "rights", "policy", enrich.get("sherpa_uri"))
    for funder in enrich.get("funders") or []:
        add("dc", "description", "sponsorship", funder)
    for aff in all_affils or []:
        add("organization", "legalName", None, aff)
    for auth in item.get("norm_authors") or []:
        if isinstance(auth, dict):
            add("dc", "contributor", "author", auth.get("name"))
        else:
            add("dc", "contributor", "author", auth)
    for oid in paper_orcids or []:
        add("person", "identifier", "orcid", oid)

    add("oaire", "citation", "title", item.get("journal"))
    if item.get("volume"):
        add("oaire", "citation", "volume", item["volume"])
    if item.get("issue"):
        add("oaire", "citation", "issue", item["issue"])
    if item.get("pages"):
        add("dc", "format", "extent", item["pages"])
        parts = str(item["pages"]).split("-")
        add("oaire", "citation", "startPage", parts[0])
        if len(parts) > 1 and parts[1]:
            add("oaire", "citation", "endPage", parts[1])
    if item.get("issn"):
        add("dc", "identifier", "issn", item["issn"])

    abstract = enrich.get("abstract")
    if abstract:
        add("dc", "description", "abstract", clean_abstract(abstract))
    pub = item.get("publisher") or enrich.get("publisher")
    if pub:
        add("dc", "publisher", None, pub)
    add("dc", "type", None, item.get("doctype", "Article"))
    add("dc", "language", "iso", "en_CA")
    return md


def write_saf(metadata, item_dir, bitstream_file):
    schema_map = {}
    for field in metadata:
        prefix = field["schema"]
        if prefix not in schema_map:
            root = ET.Element("dublin_core")
            root.set("schema", prefix)
            schema_map[prefix] = root
        el = ET.SubElement(schema_map[prefix], "dcvalue")
        el.set("element", field["element"])
        el.set("qualifier", field["qualifier"] if field["qualifier"] else "none")
        el.text = field["value"]

    for prefix, root in schema_map.items():
        ET.indent(root, space="  ")
        fname = "dublin_core.xml" if prefix == "dc" else f"metadata_{prefix}.xml"
        tree = ET.ElementTree(root)
        tree.write(os.path.join(item_dir, fname), encoding="utf-8", xml_declaration=True)

    if bitstream_file:
        with open(os.path.join(item_dir, "contents"), "w", encoding="utf-8") as f:
            f.write(bitstream_file)


def generate_import_scripts(
    base_dir, dspace_bin, dspace_email, collection_map=None, default_collection_handle="123456789/0"
):
    collection_map = collection_map or {}
    script_file = os.path.join(base_dir, "import_batch.sh")
    with open(script_file, "w", encoding="utf-8", newline="\n") as f:
        f.write("#!/usr/bin/env bash\nset -euo pipefail\n\n")
        for root, dirs, files in os.walk(base_dir):
            dirs.sort()
            if "dublin_core.xml" in files:
                continue
            if any(d.startswith("item_") for d in dirs):
                dept_key = os.path.basename(root).strip().lower()
                collection_handle = collection_map.get(dept_key, default_collection_handle)
                rel_source = os.path.relpath(root, base_dir).replace("\\", "/")
                mapfile_name = f"mapfile_{sanitize_filename(rel_source)}"
                f.write(f"echo {shlex.quote(f'Importing: {root}')}\n")
                cmd = " ".join([
                    shlex.quote(str(dspace_bin)),
                    "import",
                    "--add",
                    f"--eperson={shlex.quote(str(dspace_email))}",
                    f"--collection={shlex.quote(str(collection_handle))}",
                    f"--source={shlex.quote(root)}",
                    f"--mapfile={shlex.quote(mapfile_name)}",
                ])
                f.write(f"{cmd}\n\n")


def generate_ris_block(item, enrich):
    dt = (item.get("doctype") or "Article").lower()
    ty = "JOUR"
    if "book chapter" in dt:
        ty = "CHAP"
    elif "book" in dt:
        ty = "BOOK"
    elif "conference" in dt or "proceeding" in dt:
        ty = "CONF"
    elif "dataset" in dt:
        ty = "DATA"
    elif "report" in dt:
        ty = "RPRT"

    lines = [f"TY  - {ty}"]
    title = clean_text(item.get("title"))
    if title:
        lines.append(f"TI  - {title}")

    for auth in item.get("norm_authors") or []:
        name = auth.get("name") if isinstance(auth, dict) else auth
        if name:
            lines.append(f"AU  - {name}")

    journal = clean_text(item.get("journal"))
    if journal:
        lines.append(f"JO  - {journal}")

    date_val = item.get("date")
    if date_val:
        lines.append(f"PY  - {str(date_val).replace('-', '/')}")

    if item.get("volume"):
        lines.append(f"VL  - {item['volume']}")
    if item.get("issue"):
        lines.append(f"IS  - {item['issue']}")

    if item.get("pages"):
        parts = str(item["pages"]).split("-")
        lines.append(f"SP  - {parts[0]}")
        if len(parts) > 1 and parts[1]:
            lines.append(f"EP  - {parts[1]}")

    if item.get("issn"):
        lines.append(f"SN  - {item['issn']}")

    doi = normalize_doi(item.get("doi"))
    if doi:
        lines.append(f"DO  - {doi}")

    abstract = enrich.get("abstract")
    if abstract:
        cleaned_abs = clean_text(abstract)
        if cleaned_abs:
            lines.append(f"AB  - {cleaned_abs}")

    if doi:
        lines.append(f"UR  - https://doi.org/{doi}")

    pub = item.get("publisher") or enrich.get("publisher")
    if pub:
        lines.append(f"PB  - {pub}")

    lines.append("ER  - \n")
    return "\n".join(lines)


class MetadataExporter:
    """Manages writing harvested data to CSV reports, RIS blocks, and author registries."""

    CSV_FIELDNAMES = [
        "DOI", "Title", "ISSN", "Doc_Type", "Source", "Folder_Type",
        "Dept", "PDF_Status", "PDF_Source", "Sherpa_Policy", "OA_Status",
    ]

    def __init__(self, output_dir, csv_file, ris_file, author_file, publisher_report_file, institution_name):
        self.output_dir = output_dir
        self.csv_file = csv_file
        self.ris_file = ris_file
        self.author_file = author_file
        self.publisher_report_file = publisher_report_file
        self.institution_name = institution_name
        self.csv_handle = None
        self.csv_writer = None
        self.ris_handle = None

    def open_handles(self):
        self.csv_handle = open(self.csv_file, "a", newline="", encoding="utf-8")
        self.csv_writer = csv.DictWriter(
            self.csv_handle, fieldnames=self.CSV_FIELDNAMES, extrasaction="ignore"
        )
        if os.path.getsize(self.csv_file) == 0:
            self.csv_writer.writeheader()

        self.ris_handle = open(self.ris_file, "a", encoding="utf-8")

    def close_handles(self):
        if self.csv_handle:
            self.csv_handle.close()
            self.csv_handle = None
            self.csv_writer = None
        if self.ris_handle:
            self.ris_handle.close()
            self.ris_handle = None

    def __enter__(self):
        self.open_handles()
        return self

    def __exit__(self, *_exc):
        self.close_handles()
        return False

    def write_csv_row(self, row):
        if self.csv_writer:
            safe_row = {k: _sanitize_csv_cell(v) for k, v in row.items()}
            self.csv_writer.writerow(safe_row)
            self.csv_handle.flush()

    def write_ris_entry(self, item, enrich):
        if self.ris_handle:
            ris_entry = generate_ris_block(item, enrich)
            self.ris_handle.write(ris_entry + "\n")
            self.ris_handle.flush()

    def generate_publisher_report(self, dept_publisher_breakdown):
        with open(self.publisher_report_file, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Department", "Publisher", "Count"])
            for dept, pubs in sorted(dept_publisher_breakdown.items()):
                for pub, count in pubs.most_common():
                    w.writerow([_sanitize_csv_cell(dept), _sanitize_csv_cell(pub), count])

    def generate_author_registry(self, author_db):
        with open(self.author_file, "w", encoding="utf-8") as af:
            af.write(f"{self.institution_name} Author Registry\n")
            af.write("=" * (len(self.institution_name) + 16) + "\n")
            for name, data in sorted(author_db.items()):
                depts = "; ".join(sorted(data["depts"])) if data["depts"] else "Unknown"
                emails = ", ".join(sorted(e for e in data["emails"] if e)) if data["emails"] else "N/A"
                orcids = ", ".join(sorted(o for o in data["orcids"] if o)) if data["orcids"] else "N/A"
                af.write(f"{name} : {depts} : {emails} : {orcids}\n")
