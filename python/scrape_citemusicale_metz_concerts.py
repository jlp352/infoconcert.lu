#!/usr/bin/env python3
"""
Scraper des concerts disponibles sur https://www.citemusicale-metz.fr/
Salles BAM et Trinitaires — utilise l'API REST Hydra du site (Nuxt SSR).

API endpoint  : https://www.citemusicale-metz.fr/api/events
Filtre lieu   : tagGroup[0][]=bam & tagGroup[0][]=trinitaires  (condition OR)
Filtre date   : sortingLastDateTime[after]=AUJOURD'HUI  (événements à venir uniquement)

Fichiers générés dans des sous-dossiers relatifs à l'emplacement du script :
    ./JSON/scrape_citemusicale_metz_concerts.json
    ./CSV/scrape_citemusicale_metz_concerts.csv
    ./Log/scrape_citemusicale_metz_concerts.log

Usage:
    python scrape_citemusicale_metz_concerts.py                      # JSON (défaut)
    python scrape_citemusicale_metz_concerts.py -f csv               # CSV
    python scrape_citemusicale_metz_concerts.py -s "sold_out"        # Exclure des statuts
"""

import argparse
import csv
import io
import json
import logging
import os
import re
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote as _url_quote, urlencode
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR  = Path(__file__).resolve().parent
SCRIPT_NAME = Path(__file__).stem
DIR_JSON    = SCRIPT_DIR / "JSON"
DIR_CSV     = SCRIPT_DIR / "CSV"
DIR_LOG     = SCRIPT_DIR / "Log"

BASE_URL    = "https://www.citemusicale-metz.fr"
API_EVENTS  = f"{BASE_URL}/api/events"
ASSETS_URL  = f"{BASE_URL}/assets"
# Préfixe de redimensionnement pour les images (qualité 90, largeur 1024px)
ASSETS_IMG_PREFIX = "q90-w1024"

USER_AGENT   = "CiteMusicaleMetzScraper/1.0"
MAX_RETRIES  = 3
RETRY_DELAY  = 5   # secondes entre chaque retry
ITEMS_PER_PAGE = 100

# Lieux inclus dans ce scraper (slug → (location, adresse))
VENUES = {
    "bam":         ("BAM - Metz",         "1 rue de la Citadelle, 57000 Metz, France"),
    "trinitaires": ("Trinitaires - Metz", "12 rue des Trinitaires, 57000 Metz, France"),
}

# Mapping disponibilité → statut
_AVAIL_STATUS = {
    "AVAILABLE":   "buy_now",
    "LAST_SEATS":  "buy_now",   # dernières places
    "NO_VACANCY":  "sold_out",
}

CSV_COLUMNS = [
    "id", "artist", "date_live", "doors_time", "location",
    "address", "genres", "status", "url", "buy_link", "image",
    "price", "date_created",
]

logger = logging.getLogger("citemusicale_metz_scraper")


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

def _request(url: str, *, retries: int = MAX_RETRIES) -> dict:
    """GET JSON avec retry automatique. Retourne le dict parsé."""
    last_exc: Exception | None = None
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/ld+json",
    }
    for attempt in range(1, retries + 1):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
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
# Récupération des événements via l'API
# ---------------------------------------------------------------------------

def _build_api_url(today_str: str, page: int = 1) -> str:
    """
    Construit l'URL de l'API pour une page donnée.

    Les paramètres multi-valeurs (tagGroup, type) sont répétés manuellement
    car urlencode ne supporte pas la notation bracket [] nativement.
    """
    fixed = urlencode([
        ("node.visible",               "1"),
        ("sortingLastDateTime[after]", today_str),
        ("order[sortingFirstDateTime]", "asc"),
        ("itemsPerPage",               str(ITEMS_PER_PAGE)),
        ("page",                       str(page)),
        ("_locale",                    "fr"),
    ])
    # Filtre lieu : BAM OU Trinitaires (même groupe → OR)
    places = "tagGroup[0][]=bam&tagGroup[0][]=trinitaires"
    return f"{API_EVENTS}?{fixed}&{places}"


def _fetch_all_events(today_str: str) -> list[dict]:
    """Récupère tous les événements BAM/Trinitaires à venir (pagination complète)."""
    all_items: list[dict] = []
    page = 1

    while True:
        url = _build_api_url(today_str, page)
        logger.debug("GET %s", url)
        data = _request(url)

        members = data.get("hydra:member", [])
        all_items.extend(members)

        total = data.get("hydra:totalItems", 0)
        logger.info("Page %d : %d/%d événements récupérés", page, len(all_items), total)

        if len(all_items) >= total or not members:
            break
        page += 1

    return all_items


# ---------------------------------------------------------------------------
# Parsing d'un événement API → dict concert
# ---------------------------------------------------------------------------

def _parse_event_id(api_id: str) -> str:
    """'/api/events/967' → '967'"""
    return api_id.rsplit("/", 1)[-1]


def _parse_availability(api_id: str) -> str | None:
    """'/api/availabilities/AVAILABLE' → 'buy_now' etc."""
    key = api_id.rsplit("/", 1)[-1]
    return _AVAIL_STATUS.get(key)


def _parse_dates(array_dates: list) -> tuple[str | None, str | None]:
    """
    Extrait (date_live, doors_time) depuis arrayDates.
    arrayDates[0] = début du concert, format ISO 8601 avec timezone.
    """
    if not array_dates:
        return None, None
    raw = array_dates[0]
    try:
        dt = datetime.fromisoformat(raw)
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
    except (ValueError, TypeError):
        return None, None


def _parse_venue(places_names: dict) -> tuple[str, str]:
    """
    Retourne (location, address) depuis le dict placesNames.
    Ex: {"bam": "BAM"} ou {"trinitaires": "Trinitaires"} ou {"trinitaires, chapelle": "..."}
    Utilise un matching par préfixe/contenu pour gérer les variantes de salle.
    """
    for key in list((places_names or {}).keys()):
        for slug, (location, address) in VENUES.items():
            if key.startswith(slug):
                return location, address
    # Fallback générique si un jour un autre lieu apparaît
    first_val = next(iter((places_names or {}).values()), "Cité Musicale-Metz")
    return f"{first_val} - Metz", "57000 Metz, France"


def _parse_genres(tags: list) -> list[str]:
    """Extrait les genres depuis les tags (parent.slug == 'styles')."""
    return [
        t.get("name", "").strip()
        for t in (tags or [])
        if isinstance(t.get("parent"), dict) and t["parent"].get("slug") == "styles"
        and t.get("name")
    ]


def _parse_image(main_documents: list) -> str | None:
    """
    Construit l'URL complète de l'image depuis mainDocuments[0].
    Le serveur exige un préfixe de redimensionnement (ex: q90-w1024) sinon 404.
    Format : /assets/{prefix}/{relativePath}
    """
    if not main_documents:
        return None
    rel = main_documents[0].get("relativePath")
    if not rel:
        return None
    return f"{ASSETS_URL}/{ASSETS_IMG_PREFIX}/{rel}"


def _parse_price(event: dict) -> tuple[str, str | None]:
    """
    Retourne (price_str, status_override).
    price_str : tarif sur place ex '30.00 EUR', 'Free', 'Price Unavailable'
    status_override : 'free' si entrée libre/gratuit, None sinon.

    Tarif sur place = maxPrice / 1000  (ex: 30000 → 30.00 EUR)
    Gratuit : priceRange contient "gratuit"/"entrée libre" ou maxPrice == 0
    """
    price_range = (event.get("priceRange") or "").strip()
    max_price   = event.get("maxPrice") or 0

    # Gratuit
    free_keywords = ("gratuit", "gratuite", "entrée libre", "free")
    if any(kw in price_range.lower() for kw in free_keywords) or max_price == 0:
        return "Free", "free"

    # Tarif sur place
    if max_price > 0:
        return f"{max_price / 1000:.2f} EUR", None

    return "Price Unavailable", None


def _build_concert(event: dict, run_timestamp: str) -> dict | None:
    """Convertit un événement API en dict concert selon le schéma commun."""
    api_id = event.get("@id", "")
    if not api_id:
        return None

    concert_id = _parse_event_id(api_id)
    date_live, doors_time = _parse_dates(event.get("arrayDates", []))
    if not date_live:
        logger.debug("Événement %s ignoré : date introuvable", concert_id)
        return None

    location, address = _parse_venue(event.get("placesNames") or {})
    genres = _parse_genres(event.get("tags") or [])
    image  = _parse_image(event.get("mainDocuments") or [])
    price, status_override = _parse_price(event)

    # Statut de disponibilité (la disponibilité peut être surchargée par le prix)
    avail_id = (event.get("availability") or {}).get("@id", "")
    status   = status_override or _parse_availability(avail_id)

    # URL de la page de l'événement
    event_url = BASE_URL + (event.get("url") or "")

    # URL de réservation (secutix)
    offers = event.get("offersUrl") or {}
    buy_link = offers.get("secutix") or None

    # Nom de l'artiste : name + subtitle si présent
    name     = (event.get("name") or "").strip()
    subtitle = (event.get("subtitle") or "").strip()
    artist   = f"{name} {subtitle}".strip() if subtitle else name

    return {
        "id":           concert_id,
        "artist":       artist,
        "date_live":    date_live,
        "doors_time":   doors_time,
        "location":     location,
        "address":      address,
        "genres":       genres if genres else ["Concerts"],
        "status":       status,
        "url":          event_url,
        "buy_link":     buy_link,
        "image":        image,
        "price":        price,
        "date_created": run_timestamp,
    }


# ---------------------------------------------------------------------------
# Enrichissement des genres via l'API Deezer
# ---------------------------------------------------------------------------

_genre_cache: dict[str, list[str]] = {}


def _fetch_deezer_genres(artist_name: str) -> list[str]:
    """
    Récupère les genres musicaux d'un artiste via l'API Deezer.

    Chaîne d'appels :
      1. GET /search/artist?q=...        → artist_id
      2. GET /artist/{id}/top?limit=1    → album_id du premier titre
      3. GET /album/{album_id}           → genres.data[].name

    Retourne ["Concerts"] en cas d'échec ou d'absence de données.
    Résultats mis en cache par nom d'artiste.
    """
    # Nettoyer le nom : enlever parenthèses de pays ex: "(FR)"
    clean_name = re.sub(r'\s*\([A-Z]{2,3}\)\s*$', '', artist_name).strip()
    key = clean_name.lower()

    if key in _genre_cache:
        return _genre_cache[key]

    fallback = ["Concerts"]
    deezer_headers = {"Accept-Language": "en"}

    try:
        url1  = f"https://api.deezer.com/search/artist?q={_url_quote(clean_name)}&limit=1"
        data1 = _request(url1)
        artists = data1.get("data", [])
        if not artists:
            _genre_cache[key] = fallback
            return fallback
        artist_id = artists[0]["id"]

        url2  = f"https://api.deezer.com/artist/{artist_id}/top?limit=1"
        data2 = _request(url2)
        tracks = data2.get("data", [])
        if not tracks:
            _genre_cache[key] = fallback
            return fallback
        album_id = tracks[0].get("album", {}).get("id")
        if not album_id:
            _genre_cache[key] = fallback
            return fallback

        url3  = f"https://api.deezer.com/album/{album_id}"
        req3  = Request(url3, headers={"User-Agent": USER_AGENT, **deezer_headers})
        with urlopen(req3, timeout=30) as resp:
            data3 = json.loads(resp.read().decode("utf-8"))
        genres = [
            g["name"]
            for g in data3.get("genres", {}).get("data", [])
            if g.get("name")
        ]
        result = genres if genres else fallback
        _genre_cache[key] = result
        logger.debug("Genres Deezer pour '%s' : %s", clean_name, result)
        return result

    except Exception as exc:
        logger.debug("Genres Deezer non récupérés pour '%s' : %s", clean_name, exc)
        _genre_cache[key] = fallback
        return fallback


# ---------------------------------------------------------------------------
# Fonction principale de collecte
# ---------------------------------------------------------------------------

def _parse_exclusion_list(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {v.strip().lower() for v in raw.split(";") if v.strip()}


def fetch_concerts(
    exclude_genres: str | None = None,
    exclude_statuses: str | None = None,
) -> dict:
    """
    Récupère tous les événements BAM & Trinitaires à venir.

    Étapes :
      1. Appel API REST (pagination complète) → liste d'événements bruts
      2. Conversion vers le schéma commun
      3. Enrichissement des genres via Deezer (séquentiel, avec cache)
         — utilisé uniquement si le site ne fournit pas de genre (styles)
    """
    run_timestamp = datetime.now(timezone.utc).isoformat()
    today_str     = date.today().isoformat()   # "YYYY-MM-DD"

    # 1. Récupération API
    logger.info("Récupération des événements BAM/Trinitaires (Cité Musicale-Metz)…")
    raw_events = _fetch_all_events(today_str)
    logger.info("%d événements bruts récupérés depuis l'API", len(raw_events))

    if not raw_events:
        logger.warning("Aucun événement trouvé — vérifier l'API ou les filtres")
        return {
            "scraped_at": run_timestamp,
            "source":     BASE_URL,
            "total":      0,
            "concerts":   [],
        }

    # 2. Conversion vers le schéma commun
    concerts: list[dict] = []
    for ev in raw_events:
        concert = _build_concert(ev, run_timestamp)
        if concert:
            concerts.append(concert)

    logger.info("%d concerts après parsing", len(concerts))

    # 3. Enrichissement Deezer pour les concerts sans genre (ou genre = ["Concerts"])
    no_genre = [c for c in concerts if c["genres"] == ["Concerts"]]
    if no_genre:
        logger.info(
            "Récupération des genres via Deezer pour %d artistes sans genre…",
            len(no_genre),
        )
        for concert in no_genre:
            concert["genres"] = _fetch_deezer_genres(concert["artist"])
        logger.info("Genres Deezer récupérés.")

    # 4. Filtres
    excluded_genres = _parse_exclusion_list(exclude_genres)
    if excluded_genres:
        before = len(concerts)
        concerts = [
            c for c in concerts
            if not any(g.lower() in excluded_genres for g in (c.get("genres") or []))
        ]
        logger.info("Filtre genres %s : %d → %d concerts", excluded_genres, before, len(concerts))

    excluded_statuses = _parse_exclusion_list(exclude_statuses)
    if excluded_statuses:
        before = len(concerts)
        concerts = [
            c for c in concerts
            if (c.get("status") or "").lower() not in excluded_statuses
        ]
        logger.info("Filtre statuts %s : %d → %d concerts", excluded_statuses, before, len(concerts))

    return {
        "scraped_at": run_timestamp,
        "source":     BASE_URL,
        "total":      len(concerts),
        "concerts":   concerts,
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

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Récupère les événements BAM & Trinitaires depuis citemusicale-metz.fr"
    )
    parser.add_argument(
        "-f", "--format",
        choices=["json", "csv"],
        default="json",
        help="Format de sortie : json (défaut) ou csv",
    )
    parser.add_argument(
        "-g", "--exclude-genres",
        metavar="GENRES",
        help='Genres à exclure, séparés par des points-virgules (ex: "Concerts")',
    )
    parser.add_argument(
        "-s", "--exclude-statuses",
        metavar="STATUSES",
        help='Statuts à exclure, séparés par des points-virgules (ex: "sold_out; free")',
    )
    args = parser.parse_args()

    _setup_logging()
    logger.info("=" * 60)
    logger.info(
        "Démarrage du scraper Cité Musicale-Metz "
        "(format=%s, exclude_genres=%s, exclude_statuses=%s)",
        args.format, args.exclude_genres, args.exclude_statuses,
    )

    try:
        data = fetch_concerts(
            exclude_genres=args.exclude_genres,
            exclude_statuses=args.exclude_statuses,
        )

        out_file = (
            (DIR_CSV  / f"{SCRIPT_NAME}.csv")
            if args.format == "csv"
            else (DIR_JSON / f"{SCRIPT_NAME}.json")
        )

        if args.format == "csv":
            _safe_write(out_file, concerts_to_csv(data["concerts"]))
        else:
            _safe_write(out_file, json.dumps(data, ensure_ascii=False, indent=2))

        logger.info("✅ %d concerts sauvegardés → %s", data["total"], out_file)

    except (HTTPError, URLError) as exc:
        logger.error("❌ ERREUR RÉSEAU — site indisponible ou URL modifiée : %s", exc)
        logger.info("Le fichier de sortie précédent n'a pas été modifié")
        sys.exit(1)
    except ValueError as exc:
        logger.error("❌ ERREUR STRUCTURE — la structure de l'API a changé : %s", exc)
        logger.info("Le fichier de sortie précédent n'a pas été modifié")
        sys.exit(1)
    except Exception as exc:
        logger.exception("❌ ERREUR INATTENDUE : %s", exc)
        logger.info("Le fichier de sortie précédent n'a pas été modifié")
        sys.exit(1)

    logger.info("Fin du scraper")


if __name__ == "__main__":
    main()
