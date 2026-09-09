import os
import shutil
import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote

def _normalize_doi(value):
    if not value:
        return None
    doi = str(value).strip().lower().replace('https://doi.org/', '')
    return doi if doi.startswith('10.') else None

def parse_zotero_rdf(rdf_path):
    """
    Parses Zotero Exported Items.rdf to find mappings between DOI and PDF file paths.
    Returns a dictionary mapping normalized DOI to the relative path of the PDF.
    """
    namespaces = {
        'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
        'z': 'http://www.zotero.org/namespaces/export#',
        'dcterms': 'http://purl.org/dc/terms/',
        'dc': 'http://purl.org/dc/elements/1.1/',
        'bib': 'http://purl.org/net/biblio#',
        'link': 'http://purl.org/rss/1.0/modules/link/'
    }
    
    try:
        tree = ET.parse(rdf_path)
    except Exception as e:
        print(f"Error parsing RDF file {rdf_path}: {e}")
        return {}
        
    root = tree.getroot()
    
    # Map attachment ID -> file path
    attachments = {}
    for attachment in root.findall('z:Attachment', namespaces):
        about = attachment.get(f"{{{namespaces['rdf']}}}about")
        path_elem = attachment.find('z:path', namespaces)
        if about and path_elem is not None:
            resource = path_elem.get(f"{{{namespaces['rdf']}}}resource")
            if resource:
                attachments[about] = unquote(resource)
                
    # Map DOI -> file path
    doi_to_pdf = {}
    for article in root.findall('bib:Article', namespaces):
        doi = None
        ident_elem = article.find('.//dc:identifier/dcterms:URI/rdf:value', namespaces)
        if ident_elem is not None and ident_elem.text:
            doi = _normalize_doi(ident_elem.text)
            
        link_elem = article.find('link:link', namespaces)
        if doi and link_elem is not None:
            resource = link_elem.get(f"{{{namespaces['rdf']}}}resource")
            if resource and resource in attachments:
                doi_to_pdf[doi] = attachments[resource]
                
    return doi_to_pdf

def process_exported_items(base_dir):
    rdf_path = os.path.join(base_dir, 'Exported Items', 'Exported Items.rdf')
    zotero_base = os.path.join(base_dir, 'Exported Items')
    items_only_link_dir = os.path.join(base_dir, 'Items_Only_Link')
    items_with_pdf_dir = os.path.join(base_dir, 'Items_With_PDF')
    
    if not os.path.exists(rdf_path):
        print(f"Error: Could not find Zotero RDF export at {rdf_path}")
        return
        
    doi_to_pdf = parse_zotero_rdf(rdf_path)
    print(f"Found {len(doi_to_pdf)} PDFs in Zotero export.")
    
    if len(doi_to_pdf) == 0:
        print("No PDFs found to process. Exiting.")
        return
        
    moved_count = 0
    not_found_count = 0
    
    # Process items in Items_Only_Link
    if not os.path.exists(items_only_link_dir):
        print(f"Error: {items_only_link_dir} does not exist.")
        return

    # Use list to avoid modifying directory structure during iteration
    for root_dir, dirs, files in list(os.walk(items_only_link_dir, topdown=False)):
        if "link.txt" in files:
            link_path = os.path.join(root_dir, "link.txt")
            doi = None
            try:
                with open(link_path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    match = re.search(r'(10\.\d{4,9}/[-._;()/:A-Z0-9]+)', content, re.IGNORECASE)
                    if match:
                        doi = _normalize_doi(match.group(1))
            except Exception as e:
                print(f"Error reading {link_path}: {e}")
                continue
                
            if doi and doi in doi_to_pdf:
                # We have a PDF for this item
                rel_pdf_path = doi_to_pdf[doi]
                source_pdf = os.path.join(zotero_base, rel_pdf_path)
                
                if os.path.exists(source_pdf):
                    # Directory structure: Items_Only_Link/DeptName/item_XYZ
                    dept = os.path.basename(os.path.dirname(root_dir))
                    item_id = os.path.basename(root_dir)
                    
                    target_dept_dir = os.path.join(items_with_pdf_dir, dept)
                    os.makedirs(target_dept_dir, exist_ok=True)
                    
                    target_item_dir = os.path.join(target_dept_dir, item_id)
                    
                    print(f"Moving {item_id} ({doi}) -> {target_dept_dir}")
                    
                    try:
                        shutil.move(root_dir, target_item_dir)
                        
                        # Copy the PDF to the new location
                        dest_pdf = os.path.join(target_item_dir, "article.pdf")
                        shutil.copy2(source_pdf, dest_pdf)
                        
                        # Clean up link.txt and create contents file
                        moved_link_path = os.path.join(target_item_dir, "link.txt")
                        if os.path.exists(moved_link_path):
                            os.remove(moved_link_path)
                            
                        with open(os.path.join(target_item_dir, "contents"), 'w', encoding='utf-8') as f:
                            f.write("article.pdf")
                            
                        moved_count += 1
                    except Exception as e:
                        print(f"Failed to process {item_id}: {e}")
                else:
                    print(f"Warning: PDF mapped for {doi} but not found at {source_pdf}")
                    not_found_count += 1
            else:
                # Item does not have a PDF, remains in Items_Only_Link
                not_found_count += 1
                
    print(f"\n--- Processing Complete ---")
    print(f"Successfully moved items to Items_With_PDF: {moved_count}")
    print(f"Items remaining in Items_Only_Link: {not_found_count}")

if __name__ == '__main__':
    # You can change this base_dir or pass it as an argument if needed
    base_dir = '/home/pvcalarco/FedHarv/Output-20231231'
    process_exported_items(base_dir)
