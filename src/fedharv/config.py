# SPDX-License-Identifier: AGPL-3.0-only
import os
import sys
import re
import logging
import configparser
import datetime
from urllib.parse import urlparse
from dotenv import load_dotenv

__version__ = "1.1.3"

# API URL Constants
OPENALEX_WORKS_URL = "https://api.openalex.org/works"
OPENALEX_INST_URL = "https://api.openalex.org/institutions"
UNPAYWALL_API = "https://api.unpaywall.org/v2/"
SHERPA_API_URL = "https://v2.sherpa.ac.uk/cgi/retrieve"
CROSSREF_API_URL = "https://api.crossref.org/works"
DATACITE_API_URL = "https://api.datacite.org/dois/"
DOAJ_SEARCH_URL = "https://doaj.org/api/v2/search/journals/"

# Rate limiting defaults (requests per period, per host)
CALLS = 10
RATE_LIMIT_PERIOD = 1


class ConfigError(ValueError):
    """Raised when configuration loading or validation fails."""


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


def _replace_doi_to_pdf(url):
    """Normalize Atypon/HighWire-style /doi/{abs,full,epdf}/ URLs to /doi/pdf/ without duplication."""
    if "/doi/pdfdirect/" in url or "/doi/pdf/" in url:
        return url
    for marker in ("/doi/epdf/", "/doi/full/", "/doi/abs/"):
        if marker in url:
            return url.replace(marker, "/doi/pdf/", 1)
    if "/doi/" in url:
        return url.replace("/doi/", "/doi/pdf/", 1)
    return url


def _wiley_url_transform(url):
    """Handle Wiley-specific URL transformations."""
    return _replace_doi_to_pdf(url)


def _ieee_url_transform(url):
    """Handle IEEE-specific URL transformations."""
    if "/document/" not in url:
        return url
    doc_id = url.split("/document/", 1)[-1].split("/", 1)[0].split("?", 1)[0]
    if not doc_id:
        return url
    return f"https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber={doc_id}"


def _cambridge_url_transform(url):
    """Handle Cambridge-specific URL transformations."""
    parsed = urlparse(url)
    segments = [seg for seg in parsed.path.split("/") if seg]
    if not segments:
        return url
    article_id = segments[-1]
    return f"https://www.cambridge.org/core/services/aop-cambridge-core/content/view/{article_id}/{article_id}.pdf"


def _scielo_url_transform(url):
    """Append format=pdf using the proper query separator."""
    if "format=pdf" in url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}format=pdf"


def _tandfonline_url_transform(url):
    base = url.split("?", 1)[0]
    return _replace_doi_to_pdf(base)


DOMAIN_URL_TRANSFORMS = {
    "scielo.br": _scielo_url_transform,
    "scielo.org": _scielo_url_transform,
    "sciendo.com": lambda url: url.replace("/article/", "/pdf/", 1) if "/article/" in url else url,
    "pnas.org": _replace_doi_to_pdf,
    "aimspress.com": lambda url: url.replace("/article/doi/", "/article/", 1).rstrip("/") + "/pdf",
    "f1000research.com": lambda url: url.rstrip("/") + "/pdf" if not url.rstrip("/").endswith("/pdf") else url,
    "pubs.acs.org": _replace_doi_to_pdf,
    "nature.com": lambda url: url + ".pdf" if "/articles/" in url and not url.endswith(".pdf") else url,
    "mdpi.com": lambda url: url.rstrip("/") + "/pdf" if "/pdf" not in url else url,
    "pubs.rsc.org": lambda url: url.replace("/articlelanding/", "/articlepdf/", 1),
    "onlinelibrary.wiley.com": _wiley_url_transform,
    "link.springer.com": lambda url: url.replace("/article/", "/content/pdf/", 1) + ".pdf" if "/article/" in url else url,
    "journals.sagepub.com": _replace_doi_to_pdf,
    "tandfonline.com": _tandfonline_url_transform,
    "ieeexplore.ieee.org": _ieee_url_transform,
    "iopscience.iop.org": lambda url: url.rstrip("/") + "/pdf" if not url.rstrip("/").endswith("/pdf") else url,
    "cambridge.org": _cambridge_url_transform,
    "ncbi.nlm.nih.gov": lambda url: url.rstrip("/") + "/pdf" if "/pmc/" in url and not url.rstrip("/").endswith("/pdf") else url,
    "frontiersin.org": lambda url: re.sub(r"/full(?=$|[/?#])", "/pdf", url),
    "projecteuclid.org": lambda url: url.replace(".full", ".pdf"),
    "journals.asm.org": _replace_doi_to_pdf,
    "journals.plos.org": lambda url: url.replace("/article?", "/article/file?", 1) + "&type=printable" if "/article?" in url else url,
    "pensoft.net": lambda url: url.rstrip("/") + "/download/pdf/" if "/download/pdf/" not in url else url,
    "science.org": _replace_doi_to_pdf,
    "cdnsciencepub.com": _replace_doi_to_pdf,
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
    "book-section": "Book Chapter",
}

OA_STATUS_MAPPINGS = {
    "diamond": "Diamond",
    "fullgold": "Gold", "gold": "Gold",
    "hybrid": "Hybrid",
    "green": "Green", "repository": "Green",
    "bronze": "Bronze",
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
    "cc-by-nc-sa": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
}

CC_LICENSE_NAMES = {
    "https://creativecommons.org/licenses/by/4.0/": "Creative Commons CC-BY 4.0 International",
    "https://creativecommons.org/licenses/by-sa/4.0/": "Creative Commons CC-BY-SA 4.0 International",
    "https://creativecommons.org/licenses/by-nc/4.0/": "Creative Commons CC-BY-NC 4.0 International",
    "https://creativecommons.org/licenses/by-nd/4.0/": "Creative Commons CC-BY-ND 4.0 International",
    "https://creativecommons.org/licenses/by-nc-nd/4.0/": "Creative Commons CC-BY-NC-ND 4.0 International",
    "https://creativecommons.org/licenses/by-nc-sa/4.0/": "Creative Commons CC-BY-NC-SA 4.0 International",
}


def resolve_config_path(config_file):
    """Resolve config path with fallbacks to the src directory and repo root."""
    if not config_file:
        return config_file

    if os.path.isabs(config_file) or os.path.exists(config_file):
        return config_file

    src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    repo_root = os.path.dirname(src_dir)
    for base in (src_dir, repo_root):
        candidate = os.path.join(base, config_file)
        if os.path.exists(candidate):
            return candidate

    return config_file


def validate_date(date_str):
    """Ensure date is valid YYYY-MM-DD format and return a datetime.date."""
    if not date_str or not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        raise ConfigError(f"Date '{date_str}' must be in YYYY-MM-DD format.")
    try:
        return datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ConfigError(f"'{date_str}' is not a valid calendar date.") from exc


def resolve_output_dir_template(output_dir, start_date, end_date):
    """Expand configured OutputDir placeholders using configured date values."""
    if not output_dir:
        return output_dir

    start_year = start_date.split("-")[0] if start_date else ""
    end_year = end_date.split("-")[0] if end_date else ""

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


def default_cache_dir():
    """Return a persistent, per-user cache directory outside harvest output."""
    if sys.platform == "win32":
        base = os.getenv("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "FedHarv", "cache")
    base = os.getenv("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "fedharv")


def default_author_registry_filename(target_affil):
    """Generate a stable author registry filename from target affiliation."""
    raw = (target_affil or "authors").strip().lower()
    safe = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    return f"{safe or 'authors'}_authors.txt"


def _clean_email(value):
    return (value or "").strip().strip("[]<>").strip()


def _resolve_relative_output_dir(output_dir, resolved_config_path):
    """Resolve relative OutputDir consistently regardless of current working directory."""
    if not output_dir or os.path.isabs(output_dir):
        return output_dir
    config_dir = os.path.dirname(os.path.abspath(resolved_config_path))
    base_dir = os.path.dirname(config_dir) if os.path.basename(config_dir).lower() == "src" else config_dir
    return os.path.abspath(os.path.join(base_dir, output_dir))


class ConfigManager:
    """Manages configuration loading, validation, and dotenv fallback integration."""

    def __init__(self, config_path=None):
        self.config_path = config_path
        load_dotenv()
        self.load_config()

    def _get_optional(self, section, option, env_vars=(), default=""):
        """Read an optional setting where blank INI values fall back to env vars then default."""
        raw = self.config.get(section, option, fallback="").strip()
        if raw:
            return raw
        for var in env_vars:
            env_val = (os.getenv(var) or "").strip()
            if env_val:
                return env_val
        return default

    def load_config(self):
        config_file = resolve_config_path(self.config_path or "config.ini")
        self.resolved_config_path = os.path.abspath(config_file)

        self.config = configparser.ConfigParser()
        self.config.optionxform = str  # Preserve key case

        if not os.path.exists(config_file):
            raise ConfigError(f"Configuration file '{config_file}' not found.")

        self.config.read(config_file, encoding="utf-8")

        try:
            self.START_DATE = self.config.get("Search", "StartDate").strip()
            self.END_DATE = self.config.get("Search", "EndDate").strip()
            self.TARGET_AFFIL = self.config.get("Search", "Affiliation").strip()
            self.STRICT_AFFILIATION_MATCH = self.config.getboolean(
                "Search", "StrictAffiliationMatch", fallback=True
            )

            start_dt = validate_date(self.START_DATE)
            end_dt = validate_date(self.END_DATE)
            if start_dt > end_dt:
                raise ConfigError(
                    f"StartDate ({self.START_DATE}) must be on or before EndDate ({self.END_DATE})."
                )

            self.SHERPA_KEY = self._get_optional("Authentication", "SherpaKey", env_vars=("SHERPA_API_KEY",))
            self.SCOPUS_KEY = self._get_optional("Authentication", "ScopusKey", env_vars=("SCOPUS_API_KEY",))
            self.CROSSREF_TOKEN = self._get_optional(
                "Authentication", "CrossrefPlusToken", env_vars=("CROSSREF_TOKEN", "CROSSREF_PLUS_TOKEN")
            )
            self.OPENALEX_KEY = self._get_optional("Authentication", "OpenAlexKey", env_vars=("OPENALEX_API_KEY",))

            self.EMAIL_CONTACT = _clean_email(
                self._get_optional("General", "Email", env_vars=("OPENALEX_EMAIL",))
            )
            if not self.EMAIL_CONTACT:
                logging.warning("Email contact not provided in config or env. Some APIs may limit access.")

            raw_output_dir = self._get_optional("General", "OutputDir", default="FedHarv_Output")
            expanded_output = resolve_output_dir_template(raw_output_dir, self.START_DATE, self.END_DATE)
            self.OUTPUT_DIR = _resolve_relative_output_dir(expanded_output, self.resolved_config_path)

            raw_cache_dir = self._get_optional("General", "CacheDir", default=default_cache_dir())
            self.CACHE_DIR = resolve_output_dir_template(raw_cache_dir, self.START_DATE, self.END_DATE)

            self.CHECK_DSPACE = self.config.getboolean("DSpace", "CheckDuplicates", fallback=False)
            self.DSPACE_API = self._get_optional("DSpace", "ApiUrl")
            self.DSPACE_EMAIL = _clean_email(
                self._get_optional("DSpace", "AdminEmail", default="admin@example.org")
            )
            self.DSPACE_BIN = self._get_optional("DSpace", "BinPath", default="/dspace/bin/dspace")
            self.DEFAULT_COLLECTION_HANDLE = self._get_optional(
                "DSpace", "DefaultCollectionHandle", default="123456789/0"
            )
            self.AUTHOR_REGISTRY_FILE = self._get_optional(
                "General",
                "AuthorRegistryFile",
                default=default_author_registry_filename(self.TARGET_AFFIL),
            )

            self.UNIT_MAP = {}
            if "Mappings" in self.config:
                for key, val in self.config.items("Mappings"):
                    key_clean = key.strip().lower()
                    val_clean = val.strip()
                    if key_clean and val_clean:
                        self.UNIT_MAP[key_clean] = val_clean

            self.COLLECTION_MAP = {}
            if "Collections" in self.config:
                for key, val in self.config.items("Collections"):
                    key_clean = key.strip().lower()
                    handle = val.strip()
                    if key_clean and handle:
                        self.COLLECTION_MAP[key_clean] = handle
        except ConfigError:
            raise
        except (configparser.NoSectionError, configparser.NoOptionError, ValueError) as exc:
            raise ConfigError(f"Configuration Error: {exc}") from exc

        os.makedirs(self.CACHE_DIR, exist_ok=True)
