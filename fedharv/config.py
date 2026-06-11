# SPDX-License-Identifier: AGPL-3.0-only
import os
import sys
import re
import argparse
import configparser
import datetime
import logging
from dotenv import load_dotenv

__version__ = "1.0.1"

# API URL Constants
OPENALEX_WORKS_URL = "https://api.openalex.org/works"
OPENALEX_INST_URL = "https://api.openalex.org/institutions"
UNPAYWALL_API = "https://api.unpaywall.org/v2/"
SHERPA_API_URL = "https://v2.sherpa.ac.uk/cgi/retrieve"
CROSSREF_API_URL = "https://api.crossref.org/works"
DATACITE_API_URL = "https://api.datacite.org/dois/"
DOAJ_SEARCH_URL = "https://doaj.org/api/v2/search/journals/"

# Rate limiting
CALLS = 10
RATE_LIMIT_PERIOD = 1

# Publisher & PDF Configuration
DOI_PDF_PATTERNS = {
    "10.3233/": "https://ebooks.iospress.nl/pdf/doi/{doi}",
    "10.2478/": "https://sciendo.com/pdf/{doi}",
    "10.1049/": "https://ietresearch.onlinelibrary.wiley.com/doi/pdf/{doi}",
    "10.1007/": "https://link.springer.com/content/pdf/{doi}.pdf",
    "10.1021/": "https://pubs.acs.org/doi/pdf/{doi}",
    "10.1002/": "https://onlinelibrary.wiley.com/doi/pdf/{doi}",
    "10.1111/": "https://onlinelibrary.wiley.com/doi/pdf/{doi}",
    "10.1155/": "https://onlinelibrary.wiley.com/doi/pdf/{doi}",
    "10.1080/": "https://www.tandfonline.com/doi/pdf/{doi}",
    "10.1177/": "https://journals.sagepub.com/doi/pdf/{doi}",
    "10.3390/": "https://www.mdpi.com/{doi}/pdf",
    "10.3389/": "https://www.frontiersin.org/articles/{doi}/pdf",
    "10.1371/": "https://journals.plos.org/plosone/article/file?id={doi}&type=printable",
    "10.3934/": "https://www.aimspress.com/article/{doi}/pdf",
    "10.16995/": "https://pr.openlibhums.org/article/id/{doi_suffix}/download/pdf/",
    "10.1590/": "https://www.scielo.br/j/hcsm/a/{doi_suffix}/?format=pdf&lang=en",
}

def _wiley_url_transform(url):
    """Handle Wiley-specific URL transformations."""
    if "/pdfdirect/" in url: return url
    if "/epdf/" in url: return url.replace("/epdf/", "/pdf/")
    if "/pdf/" in url: return url
    return url.replace("/full/", "/pdf/").replace("/abs/", "/pdf/").replace("/doi/", "/doi/pdf/")

def _ieee_url_transform(url):
    """Handle IEEE-specific URL transformations."""
    if "/document/" not in url: return url
    doc_id = url.split("/document/")[-1].split("/")[0]
    return f"https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber={doc_id}"

def _cambridge_url_transform(url):
    """Handle Cambridge-specific URL transformations."""
    parts = url.split('/')
    return f"https://www.cambridge.org/core/services/aop-cambridge-core/content/view/{parts[-1]}/{parts[-1]}.pdf" if parts else url

DOMAIN_URL_TRANSFORMS = {
    "scielo.br": lambda url: url if "format=pdf" in url else url + "&format=pdf",
    "scielo.org": lambda url: url if "format=pdf" in url else url + "&format=pdf",
    "sciendo.com": lambda url: url.replace("/article/", "/pdf/") if "/article/" in url else url,
    "pnas.org": lambda url: url.replace("/doi/", "/doi/pdf/") if "/doi/pdf/" not in url else url,
    "aimspress.com": lambda url: url.replace("/article/doi/", "/article/") + "/pdf",
    "f1000research.com": lambda url: url + "/pdf",
    "pubs.acs.org": lambda url: url.replace("/doi/", "/doi/pdf/"),
    "nature.com": lambda url: url + ".pdf" if "/articles/" in url else url,
    "mdpi.com": lambda url: url + "/pdf" if "/pdf" not in url else url,
    "pubs.rsc.org": lambda url: url.replace("/articlelanding/", "/articlepdf/"),
    "onlinelibrary.wiley.com": _wiley_url_transform,
    "link.springer.com": lambda url: url.replace("/article/", "/content/pdf/") + ".pdf",
    "journals.sagepub.com": lambda url: url.replace("/full/", "/pdf/").replace("/doi/", "/doi/pdf/"),
    "tandfonline.com": lambda url: url.split("?")[0].replace("/full/", "/pdf/") if "?" in url else url.replace("/full/", "/pdf/"),
    "ieeexplore.ieee.org": _ieee_url_transform,
    "iopscience.iop.org": lambda url: url + "/pdf",
    "cambridge.org": _cambridge_url_transform,
    "ncbi.nlm.nih.gov": lambda url: url.rstrip('/') + "/pdf" if "/pmc/" in url else url,
    "frontiersin.org": lambda url: url.replace("/full", "/pdf"),
    "projecteuclid.org": lambda url: url.replace(".full", ".pdf"),
    "journals.asm.org": lambda url: url.replace("/doi/", "/doi/pdf/"),
    "journals.plos.org": lambda url: url.replace("/article?", "/article/file?") + "&type=printable",
    "pensoft.net": lambda url: url.rstrip('/') + "/download/pdf/" if "/download/pdf/" not in url else url,
    "science.org": lambda url: url.replace("/doi/", "/doi/pdf/"),
    "cdnsciencepub.com": lambda url: url.replace("/abs/", "/pdf/").replace("/full/", "/pdf/").replace("/doi/", "/doi/pdf/"),
}

DOCTYPE_MAPPINGS = {
    "review": "Article",
    "chapter": "Book Chapter", "ch": "Book Chapter", "book-chapter": "Book Chapter",
    "book": "Book", "bk": "Book",
    "conference": "Conference Paper", "proceeding": "Conference Paper", "cp": "Conference Paper",
    "data": "Dataset", "dataset": "Dataset", "dp": "Dataset",
    "report": "Technical Report", "rp": "Technical Report",
    "letter": "Article", "le": "Article", "note": "Article", "no": "Article",
    "short survey": "Article", "sh": "Article",
    "journal-article": "Article",
    "book-section": "Book Chapter"
}

OA_STATUS_MAPPINGS = {
    "diamond": "Diamond",
    "fullgold": "Gold", "gold": "Gold",
    "hybrid": "Hybrid",
    "green": "Green", "repository": "Green",
    "bronze": "Bronze"
}

LICENSE_URI_MAPPINGS = {
    "cc by": "https://creativecommons.org/licenses/by/4.0/",
    "cc-by": "https://creativecommons.org/licenses/by/4.0/",
    "cc by-sa": "https://creativecommons.org/licenses/by-sa/4.0/",
    "cc-by-sa": "https://creativecommons.org/licenses/by-sa/4.0/",
    "cc by-nc": "https://creativecommons.org/licenses/by-nc/4.0/",
    "cc-by-nc": "https://creativecommons.org/licenses/by-nc/4.0/",
    "cc by-nd": "https://creativecommons.org/licenses/by-nd/4.0/",
    "cc-by-nd": "https://creativecommons.org/licenses/by-nd/4.0/",
    "cc by-nc-nd": "https://creativecommons.org/licenses/by-nc-nd/4.0/",
    "cc-by-nc-nd": "https://creativecommons.org/licenses/by-nc-nd/4.0/",
    "cc by-nc-sa": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
    "cc-by-nc-sa": "https://creativecommons.org/licenses/by-nc-sa/4.0/"
}

CC_LICENSE_NAMES = {
    "https://creativecommons.org/licenses/by/4.0/": "Creative Commons CC-BY 4.0 International",
    "https://creativecommons.org/licenses/by-sa/4.0/": "Creative Commons CC-BY-SA 4.0 International",
    "https://creativecommons.org/licenses/by-nc/4.0/": "Creative Commons CC-BY-NC 4.0 International",
    "https://creativecommons.org/licenses/by-nd/4.0/": "Creative Commons CC-BY-ND 4.0 International",
    "https://creativecommons.org/licenses/by-nc-nd/4.0/": "Creative Commons CC-BY-NC-ND 4.0 International",
    "https://creativecommons.org/licenses/by-nc-sa/4.0/": "Creative Commons CC-BY-NC-SA 4.0 International"
}

def validate_date(date_str):
    """Ensure date is YYYY-MM-DD format."""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        logging.error(f"Date '{date_str}' must be in YYYY-MM-DD format.")
        sys.exit(1)
    try:
        datetime.datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        logging.error(f"'{date_str}' is not a valid calendar date.")
        sys.exit(1)

def resolve_output_dir_template(output_dir, start_date, end_date):
    """Expand configured OutputDir placeholders using configured date values."""
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

class ConfigManager:
    """Manages system configuration loading, dotenv integration, CLI args parsing."""
    def __init__(self, config_path=None):
        self.config_path = config_path
        
        # Load variables from .env if present
        load_dotenv()
        
        # Retreive env variables as fallbacks or primary overrides
        self.ENV_SCOPUS_API_KEY = os.getenv("SCOPUS_API_KEY")
        self.ENV_OPENALEX_EMAIL = os.getenv("OPENALEX_EMAIL")
        
        self.load_config()
        
    def load_config(self):
        if self.config_path:
            config_file = self.config_path
        else:
            parser = argparse.ArgumentParser(description="Harvest OA Content for DSpace")
            parser.add_argument("--config", type=str, default="config.ini", help="Path to config file")
            self.args, unknown = parser.parse_known_args() # Avoid errors if run inside other wrappers
            config_file = self.args.config

        self.config = configparser.ConfigParser()
        self.config.optionxform = str # Preserve case
        
        if not os.path.exists(config_file):
            logging.critical(f"Configuration file '{config_file}' not found.")
            sys.exit(1)
            
        self.config.read(config_file)

        try:
            self.START_DATE = self.config.get('Search', 'StartDate')
            self.END_DATE = self.config.get('Search', 'EndDate')
            self.TARGET_AFFIL = self.config.get('Search', 'Affiliation')
            self.STRICT_AFFILIATION_MATCH = self.config.getboolean('Search', 'StrictAffiliationMatch', fallback=True)

            # Validate the dates pulled from the config
            validate_date(self.START_DATE)
            validate_date(self.END_DATE)

            self.SHERPA_KEY = self.config.get('Authentication', 'SherpaKey', fallback='')
            
            # ScopusKey uses env as fallback, config as primary
            self.SCOPUS_KEY = self.config.get('Authentication', 'ScopusKey', fallback=self.ENV_SCOPUS_API_KEY or '')
            
            # Email uses env as fallback, config as primary
            self.EMAIL_CONTACT = self.config.get('General', 'Email', fallback=self.ENV_OPENALEX_EMAIL or '')
            if not self.EMAIL_CONTACT:
                # OpenAlex requires an email to use their polite pool
                logging.warning("Email contact not provided in config or env. Some APIs may limit access.")
            
            raw_output_dir = self.config.get('General', 'OutputDir', fallback='FedHarv_Output')
            self.OUTPUT_DIR = resolve_output_dir_template(raw_output_dir, self.START_DATE, self.END_DATE)
            # Cache lives OUTSIDE OUTPUT_DIR so it survives run()'s robust_cleanup() of the output tree.
            self.CACHE_DIR = self.config.get('General', 'CacheDir', fallback='.fedharv_cache')
            cache_days = self.config.getint('General', 'CacheMaxAgeDays', fallback=30)
            self.CACHE_MAX_AGE = cache_days * 86400 if cache_days > 0 else None  # None = never expire
            self.AUTHOR_REGISTRY_FILE = self.config.get('General', 'AuthorRegistryFile', fallback='author_registry.txt')

            self.CHECK_DSPACE = self.config.getboolean('DSpace', 'CheckDuplicates', fallback=False)
            self.DSPACE_API = self.config.get('DSpace', 'ApiUrl', fallback='')
            self.DSPACE_EMAIL = self.config.get('DSpace', 'AdminEmail', fallback='admin@example.org')
            self.DSPACE_BIN = self.config.get('DSpace', 'BinPath', fallback='/dspace/bin/dspace')
            self.DEFAULT_COLLECTION = self.config.get('DSpace', 'DefaultCollection', fallback='123456789/0')
            self.CROSSREF_TOKEN = self.config.get('Authentication', 'CrossrefPlusToken', fallback='')
            
            self.UNIT_MAP = {}
            if 'Mappings' in self.config:
                for key, val in self.config.items('Mappings'):
                    self.UNIT_MAP[key.lower()] = val

            # Optional department-folder -> DSpace collection handle map. Keys are the
            # case-sensitive folder names produced by determine_primary_department().
            self.COLLECTIONS = {}
            if 'Collections' in self.config:
                for key, val in self.config.items('Collections'):
                    self.COLLECTIONS[key] = val
        except configparser.NoOptionError as e:
            logging.error(f"Configuration error: missing required setting - {e}")
            sys.exit(1)
        except Exception as e:
            logging.error(f"Configuration error: {e}")
            sys.exit(1)
        
        if not os.path.exists(self.CACHE_DIR):
            os.makedirs(self.CACHE_DIR, exist_ok=True)
