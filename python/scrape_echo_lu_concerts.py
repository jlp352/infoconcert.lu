#!/usr/bin/env python3
"""
Scraper des concerts dans les centres culturels luxembourgeois via echo.lu.
Utilise l'API Firestore publique (Firebase) du projet lu-echo-prod.

Salles couvertes :
  - Aalt Stadhaus (Differdange)
  - Cube 521 (Marnach)
  - CAPE (Ettelbruck)
  - Kinneksbond (Mamer)
  - Mierscher Theater (Mersch)
  - opderschmelz (Dudelange)
  - Artikuss (Soleuvre)
  - Prabbeli (Wiltz)
  - Maacher (Grevenmacher)
  - Trifolion (Echternach)

Les fichiers sont générés automatiquement dans des sous-dossiers
relatifs à l'emplacement du script :
    ./JSON/scrape_echo_lu_concerts.json
    ./CSV/scrape_echo_lu_concerts.csv
    ./Log/scrape_echo_lu_concerts.log

Usage:
    python scrape_echo_lu_concerts.py                          # JSON (défaut)
    python scrape_echo_lu_concerts.py -f csv                   # CSV
    python scrape_echo_lu_concerts.py -f csv -s "sold_out"     # Exclure des statuts
"""

import argparse
import csv
import io
import json
import logging
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# ---------------------------------------------------------------------------
# Chemins et constantes
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT_NAME = Path(__file__).stem  # "scrape_echo_lu_concerts"
DIR_JSON = SCRIPT_DIR / "JSON"
DIR_CSV = SCRIPT_DIR / "CSV"
DIR_LOG = SCRIPT_DIR / "Log"

FIRESTORE_PROJECT = "lu-echo-prod"
FIRESTORE_BASE = (
    f"https://firestore.googleapis.com/v1/projects/{FIRESTORE_PROJECT}"
    "/databases/(default)/documents"
)
FIRESTORE_QUERY_URL = FIRESTORE_BASE + ":runQuery"
ECHO_LU_BASE = "https://www.echo.lu/en/experiences"

USER_AGENT = "EchoLuConcertScraper/1.0"
MAX_WORKERS = 6
MAX_RETRIES = 3
RETRY_DELAY = 5  # secondes entre chaque retry

# Catégories Firestore retenues comme "concerts"
CONCERT_CATEGORIES = {"concerts", "concerts-other", "music"}

# Fuseau horaire du Luxembourg : UTC+1 (hiver) / UTC+2 (été)
# Calcul de l'offset DST européen (dernier dimanche de mars/octobre)
def _lux_utc_offset(dt_utc: datetime) -> int:
    """Retourne l'offset UTC en heures pour le Luxembourg (+1 hiver, +2 été)."""
    year = dt_utc.year
    # Dernier dimanche de mars
    march_31 = datetime(year, 3, 31, 1, 0, tzinfo=timezone.utc)
    dst_start = march_31 - timedelta(days=march_31.weekday() + 1)
    # Dernier dimanche d'octobre
    oct_31 = datetime(year, 10, 31, 1, 0, tzinfo=timezone.utc)
    dst_end = oct_31 - timedelta(days=oct_31.weekday() + 1)
    if dst_start <= dt_utc < dst_end:
        return 2  # CEST
    return 1      # CET


def _utc_to_lux(dt_utc: datetime) -> datetime:
    """Convertit un datetime UTC en heure locale luxembourgeoise."""
    return dt_utc + timedelta(hours=_lux_utc_offset(dt_utc))


# ---------------------------------------------------------------------------
# Définition des salles
# ---------------------------------------------------------------------------

# Clé = slug Firestore (dernier segment de l'URL echo.lu/en/venues/...)
# Valeur = (nom affiché, URL echo.lu de la salle)
VENUES = {
    "aalt-stadhaus-39otSR": (
        "Aalt Stadhaus",
        "https://www.echo.lu/en/venues/aalt-stadhaus-39otSR",
    ),
    "cube-521-gkQWpB": (
        "Cube 521",
        "https://www.echo.lu/en/venues/cube-521-gkQWpB",
    ),
    "cape-centre-des-arts-pluriels-ettelbruck-sJ237P": (
        "CAPE",
        "https://www.echo.lu/en/venues/cape-centre-des-arts-pluriels-ettelbruck-sJ237P",
    ),
    "kinneksbond-centre-culturel-mamer-6S5DWP": (
        "Kinneksbond",
        "https://www.echo.lu/en/venues/kinneksbond-centre-culturel-mamer-6S5DWP",
    ),
    "mierscher-kulturhaus-pMxRma": (
        "Mierscher Theater",
        "https://www.echo.lu/en/venues/mierscher-kulturhaus-pMxRma",
    ),
    "centre-culturel-opderschmelz-centre-culturel-regional-dudelange-eGkHxP": (
        "opderschmelz",
        "https://www.echo.lu/en/venues/centre-culturel-opderschmelz-centre-culturel-regional-dudelange-eGkHxP",
    ),
    "artikuss-aRnbum": (
        "Artikuss",
        "https://www.echo.lu/en/venues/artikuss-aRnbum",
    ),
    "centre-socioculturel-regional-prabbeli-7hTND4": (
        "Prabbeli",
        "https://www.echo.lu/en/venues/centre-socioculturel-regional-prabbeli-7hTND4",
    ),
    "machera-centre-culturel-grevenmacher-63RrPS": (
        "Maacher",
        "https://www.echo.lu/en/venues/machera-centre-culturel-grevenmacher-63RrPS",
    ),
    "trifolion-echternach-8rGXZ7": (
        "Trifolion",
        "https://www.echo.lu/en/venues/trifolion-echternach-8rGXZ7",
    ),
}

CSV_COLUMNS = [
    "id", "artist", "date_live", "doors_time", "location",
    "address", "genres", "status", "url", "buy_link", "image",
    "price", "date_created",
]

logger = logging.getLogger("echo_lu_scraper")

# Cache adresses de salles
_venue_address_cache: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging() -> Path:
    DIR_LOG.mkdir(exist_ok=True)
    log_file = DIR_LOG / f"{SCRIPT_NAME}.log"
    file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    file_handler.setFormatter(fmt)
    console_handler.setFormatter(fmt)
    logger.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return log_file


# ---------------------------------------------------------------------------
# Helpers réseau (avec retry)
# ---------------------------------------------------------------------------

def _request(url: str, *, method: str = "GET", body: bytes | None = None,
             as_json: bool = False, retries: int = MAX_RETRIES,
             extra_headers: dict | None = None):
    """GET/POST avec retry automatique."""
    last_exc = None
    headers = {"User-Agent": USER_AGENT, **(extra_headers or {})}
    if body is not None:
        headers["Content-Type"] = "application/json"
    for attempt in range(1, retries + 1):
        try:
            req = Request(url, data=body, headers=headers, method=method)
            with urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if as_json else raw
        except (HTTPError, URLError, TimeoutError, ConnectionError) as exc:
            last_exc = exc
            if attempt < retries:
                logger.warning(
                    "Tentative %d/%d échouée pour %s : %s — retry dans %ds",
                    attempt, retries, url, exc, RETRY_DELAY,
                )
                time.sleep(RETRY_DELAY)
            else:
                logger.error(
                    "Échec définitif après %d tentatives pour %s : %s",
                    retries, url, exc,
                )
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Helpers pour les valeurs Firestore
# ---------------------------------------------------------------------------

def _fs_str(field: dict) -> str:
    """Extrait la stringValue d'un champ Firestore."""
    return field.get("stringValue", "")


def _fs_array(field: dict) -> list:
    return field.get("arrayValue", {}).get("values", [])


def _fs_map(field: dict) -> dict:
    return field.get("mapValue", {}).get("fields", {})


def _fs_multilang(field: dict, langs: tuple = ("en", "fr", "de", "lb")) -> str:
    """Extrait une valeur depuis un champ multi-langue ou une stringValue directe."""
    if "stringValue" in field:
        return field["stringValue"]
    if "mapValue" in field:
        m = _fs_map(field)
        for lang in langs:
            val = m.get(lang, {}).get("stringValue", "").strip()
            if val:
                return val
        for v in m.values():
            val = v.get("stringValue", "").strip()
            if val:
                return val
    return ""


# ---------------------------------------------------------------------------
# Récupération de l'adresse d'une salle depuis Firestore
# ---------------------------------------------------------------------------

def _fetch_venue_address(venue_slug: str) -> str:
    """Retourne l'adresse formatée d'une salle ('N Rue, L-XXXX Ville')."""
    if venue_slug in _venue_address_cache:
        return _venue_address_cache[venue_slug]

    try:
        url = f"{FIRESTORE_BASE}/venues/{venue_slug}"
        data = _request(url, as_json=True, retries=2)
        f = data.get("fields", {})
        loc = _fs_map(f.get("location", {}))
        addr = _fs_map(loc.get("address", {}))
        number = _fs_str(addr.get("number", {}))
        street = _fs_str(addr.get("street", {}))
        postcode = _fs_str(addr.get("postcode", {}))
        town = _fs_str(addr.get("town", {})) or _fs_str(addr.get("commune", {})).capitalize()
        parts = [f"{number} {street}".strip(), f"{postcode} {town}".strip()]
        address = ", ".join(p for p in parts if p)
        _venue_address_cache[venue_slug] = address
        logger.debug("Adresse de '%s' : %s", venue_slug, address)
        return address
    except Exception as exc:
        logger.warning("Impossible de récupérer l'adresse de '%s' : %s", venue_slug, exc)
        _venue_address_cache[venue_slug] = ""
        return ""


# ---------------------------------------------------------------------------
# Requête Firestore : expériences par salle
# ---------------------------------------------------------------------------

def _query_experiences(venue_slug: str, limit: int = 500) -> list[dict]:
    """
    Récupère toutes les expériences pour une salle via l'API Firestore.
    Utilise la requête ARRAY_CONTAINS sur le champ 'venues'.
    Gère la pagination via offset si nécessaire.
    """
    all_docs = []
    offset = 0

    while True:
        query = {
            "structuredQuery": {
                "from": [{"collectionId": "experiences"}],
                "where": {
                    "fieldFilter": {
                        "field": {"fieldPath": "venues"},
                        "op": "ARRAY_CONTAINS",
                        "value": {"stringValue": venue_slug},
                    }
                },
                "limit": limit,
                "offset": offset,
            }
        }
        body = json.dumps(query).encode()
        results = _request(FIRESTORE_QUERY_URL, method="POST", body=body, as_json=True)

        if not isinstance(results, list):
            break

        docs = [r for r in results if "document" in r]
        all_docs.extend(docs)
        logger.debug(
            "Salle '%s' : %d documents récupérés (offset=%d)", venue_slug, len(docs), offset
        )

        if len(docs) < limit:
            break  # Dernière page
        offset += limit

    return all_docs


# ---------------------------------------------------------------------------
# Parsing d'une expérience Firestore → concerts
# ---------------------------------------------------------------------------

def _parse_experience(
    doc: dict,
    venue_slug: str,
    venue_name: str,
    venue_address: str,
    run_timestamp: str,
    now: datetime,
) -> list[dict]:
    """
    Convertit un document Firestore 'experience' en liste d'entrées concert.
    Retourne une entrée par date future (une expérience peut avoir plusieurs dates).
    """
    f = doc["document"]["fields"]
    doc_name = doc["document"]["name"]
    exp_id = _fs_str(f.get("id", {})) or doc_name.split("/")[-1]

    # --- Filtre modération ---
    if _fs_str(f.get("moderation", {})) != "validated":
        return []

    # --- Filtre catégorie ---
    cats = {_fs_str(v) for v in _fs_array(f.get("categories", {}))}
    if not (cats & CONCERT_CATEGORIES):
        return []

    # --- Titre ---
    title = _fs_multilang(f.get("title", {})).strip()
    if not title:
        return []

    # --- Image ---
    image = ""
    pics = _fs_array(f.get("pictures", {}))
    if pics:
        pf = _fs_map(pics[0])
        featured = _fs_map(
            _fs_map(pf.get("previews", {})).get("featured", {})
        ).get("url", {})
        image = _fs_str(featured) or _fs_str(pf.get("url", {}))

    # --- Prix ---
    price_type = _fs_str(f.get("priceType", {}))
    if price_type == "free":
        price = "Free"
    else:
        tickets = _fs_array(f.get("tickets", {}))
        prices = []
        for t in tickets:
            tf = _fs_map(t)
            ticket_title = _fs_str(tf.get("title", {})).lower()
            if "kulturpass" in ticket_title:
                continue
            raw_price = _fs_str(tf.get("price", {}))
            try:
                p = float(raw_price)
                if p > 0:
                    prices.append(p)
            except (ValueError, TypeError):
                pass
        price = f"{min(prices):.2f} EUR" if prices else "Price Unavailable"

    exp_url = f"{ECHO_LU_BASE}/{exp_id}"

    # --- Dates futures ---
    concerts = []
    for date_entry in _fs_array(f.get("dates", {})):
        dfields = _fs_map(date_entry)
        ts_str = dfields.get("from", {}).get("timestampValue", "")
        if not ts_str:
            continue

        try:
            dt_utc = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            logger.debug("Timestamp invalide '%s' pour l'expérience '%s'", ts_str, exp_id)
            continue

        if dt_utc <= now:
            continue  # Date passée

        dt_local = _utc_to_lux(dt_utc)
        date_live = dt_local.strftime("%Y-%m-%d")

        per_date_link = _fs_str(dfields.get("purchaseLink", {}))
        global_link = _fs_str(f.get("purchaseLink", {}))
        buy_link = per_date_link or global_link or None

        concerts.append({
            "id": f"{exp_id}_{date_live}",
            "artist": title,
            "date_live": date_live,
            "doors_time": dt_local.strftime("%Hh%M"),
            "location": venue_name,
            "address": venue_address,
            "genres": ["Concerts"],
            "status": "buy_now",
            "url": exp_url,
            "buy_link": buy_link,
            "image": image,
            "price": price,
            "date_created": run_timestamp,
        })

    return concerts


# ---------------------------------------------------------------------------
# Collecte principale
# ---------------------------------------------------------------------------

def _parse_exclusion_list(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {v.strip().lower() for v in raw.split(";") if v.strip()}


def fetch_concerts(exclude_statuses: str | None = None) -> dict:
    """
    Récupère les concerts de tous les centres culturels via echo.lu / Firestore.

    Étapes :
      1. Récupération des adresses des salles via Firestore /venues/{slug}
      2. Requête Firestore /experiences (ARRAY_CONTAINS venues) pour chaque salle
      3. Filtrage : catégorie concerts*, modération validated, dates futures
    """
    run_timestamp = datetime.now(timezone.utc).isoformat()
    now = datetime.now(timezone.utc)

    # --- 1. Adresses des salles (parallélisé) ---
    logger.info("Récupération des adresses des %d salles…", len(VENUES))
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_fetch_venue_address, slug): slug for slug in VENUES}
        for future in as_completed(futures):
            logger.debug("  %s → %s", futures[future], future.result())
    logger.info("Adresses récupérées.")

    # --- 2. Expériences par salle (parallélisé) + 3. Parsing ---
    logger.info("Interrogation de Firestore pour %d salles…", len(VENUES))
    concerts: list[dict] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_slug = {executor.submit(_query_experiences, slug): slug for slug in VENUES}
        for future in as_completed(future_to_slug):
            slug = future_to_slug[future]
            venue_name, _ = VENUES[slug]
            venue_address = _venue_address_cache.get(slug, "")
            docs = future.result()
            logger.info("  %s : %d expériences Firestore", venue_name, len(docs))
            for doc in docs:
                concerts.extend(
                    _parse_experience(doc, slug, venue_name, venue_address, run_timestamp, now)
                )

    logger.info("%d concerts avec dates futures trouvés (toutes salles)", len(concerts))

    concerts.sort(key=lambda c: (c.get("date_live") or "", c.get("location") or ""))

    excluded_statuses = _parse_exclusion_list(exclude_statuses)
    if excluded_statuses:
        before = len(concerts)
        concerts = [
            c for c in concerts
            if (c.get("status") or "").lower() not in excluded_statuses
        ]
        logger.info(
            "Filtre statuts %s : %d → %d concerts", excluded_statuses, before, len(concerts)
        )

    return {
        "scraped_at": run_timestamp,
        "source": "echo.lu (Firestore)",
        "total": len(concerts),
        "concerts": concerts,
    }


# ---------------------------------------------------------------------------
# Écriture sécurisée (atomic write)
# ---------------------------------------------------------------------------

def _safe_write(target: Path, content: str) -> None:
    target.parent.mkdir(exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.stem}_", suffix=target.suffix,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        Path(tmp_path).replace(target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Export CSV
# ---------------------------------------------------------------------------

def concerts_to_csv(concerts: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=CSV_COLUMNS,
        extrasaction="ignore",
        quoting=csv.QUOTE_MINIMAL,
    )
    writer.writeheader()
    for c in concerts:
        row = dict(c)
        row["genres"] = "; ".join(row.get("genres") or [])
        writer.writerow(row)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Récupère les concerts des centres culturels luxembourgeois "
            "via echo.lu (Firestore)"
        )
    )
    parser.add_argument(
        "-f", "--format",
        choices=["json", "csv"],
        default="json",
        help="Format de sortie : json (défaut) ou csv",
    )
    parser.add_argument(
        "-s", "--exclude-statuses",
        metavar="STATUSES",
        help='Statuts à exclure, séparés par des points-virgules (ex: "sold_out")',
    )
    args = parser.parse_args()

    _setup_logging()
    logger.info("=" * 60)
    logger.info(
        "Démarrage du scraper echo.lu (format=%s, exclude_statuses=%s)",
        args.format, args.exclude_statuses,
    )

    try:
        data = fetch_concerts(exclude_statuses=args.exclude_statuses)

        if args.format == "csv":
            out_file = DIR_CSV / f"{SCRIPT_NAME}.csv"
            _safe_write(out_file, concerts_to_csv(data["concerts"]))
        else:
            out_file = DIR_JSON / f"{SCRIPT_NAME}.json"
            _safe_write(out_file, json.dumps(data, ensure_ascii=False, indent=2))

        logger.info("✅ %d concerts sauvegardés → %s", data["total"], out_file)

    except (HTTPError, URLError) as exc:
        logger.error("❌ ERREUR RÉSEAU — Firestore indisponible : %s", exc)
        logger.info("Le fichier de sortie précédent n'a pas été modifié")
        sys.exit(1)
    except ValueError as exc:
        logger.error("❌ ERREUR STRUCTURE — format Firestore inattendu : %s", exc)
        logger.info("Le fichier de sortie précédent n'a pas été modifié")
        sys.exit(1)
    except Exception as exc:
        logger.exception("❌ ERREUR INATTENDUE : %s", exc)
        logger.info("Le fichier de sortie précédent n'a pas été modifié")
        sys.exit(1)

    logger.info("Fin du scraper echo.lu")


if __name__ == "__main__":
    main()
