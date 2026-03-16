#!/usr/bin/env python3
"""
Scraper des concerts disponibles sur https://xceed.me/fr/luxembourg/venue/lenox-club
(Lenox Club Luxembourg)

Scraping via le payload RSC (React Server Components) embarqué dans le HTML Next.js.
Les événements sont dans le cache TanStack Query sérialisé sous forme de JSON
double-échappé dans les balises <script>self.__next_f.push([1,"..."])</script>.

Les fichiers sont générés automatiquement dans des sous-dossiers
relatifs à l'emplacement du script :
    ./JSON/scrape_lenox_concerts.json
    ./CSV/scrape_lenox_concerts.csv
    ./Log/scrape_lenox_concerts.log

Usage:
    python scrape_lenox_concerts.py                          # JSON (défaut)
    python scrape_lenox_concerts.py -f csv                   # CSV
    python scrape_lenox_concerts.py -f csv -s "sold_out"     # Exclure des statuts
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote as _url_quote
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT_NAME = Path(__file__).stem
DIR_JSON = SCRIPT_DIR / "JSON"
DIR_CSV = SCRIPT_DIR / "CSV"
DIR_LOG = SCRIPT_DIR / "Log"

SOURCE_URL = "https://xceed.me/fr/luxembourg/venue/lenox-club"
VENUE_URL = "https://www.lenox.lu"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
MAX_RETRIES = 3
RETRY_DELAY = 5  # secondes entre chaque retry

LENOX_LOCATION = "Lenox Club"
LENOX_ADDRESS = "58 Rue du Fort Neipperg, 2230 Luxembourg, Luxembourg"

CSV_COLUMNS = [
    "id", "artist", "date_live", "doors_time", "location",
    "address", "genres", "status", "url", "buy_link", "image",
    "price", "date_created",
]

logger = logging.getLogger("lenox_scraper")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging() -> Path:
    """Configure le logging vers fichier fixe (append) + console."""
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

def _request(url: str, *, retries: int = MAX_RETRIES,
             extra_headers: dict | None = None) -> tuple[str, dict]:
    """GET avec retry automatique. Retourne (body_str, response_headers)."""
    last_exc = None
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr,en;q=0.5",
        **(extra_headers or {}),
    }
    for attempt in range(1, retries + 1):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                return body, resp_headers
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


def _request_json(url: str, *, retries: int = MAX_RETRIES,
                  extra_headers: dict | None = None) -> tuple[list | dict, dict]:
    """GET JSON avec retry automatique. Retourne (parsed_json, response_headers)."""
    body, headers = _request(url, retries=retries, extra_headers=extra_headers)
    return json.loads(body), headers


# ---------------------------------------------------------------------------
# Parsing du payload RSC (React Server Components / Next.js)
# ---------------------------------------------------------------------------

# Regex pour extraire les blocs self.__next_f.push([1, "..."]) du HTML
_RSC_PUSH_RE = re.compile(
    r'self\.__next_f\.push\(\[1,\s*"(.*?)"\]\)',
    re.DOTALL,
)

# Dans le JSON double-échappé du payload RSC :
#   - les guillemets JSON deviennent \"  (i.e. backslash + doublequote dans le HTML)
#   - chaque champ JSON: \"field\":\"value\"
#
# Un champ string ne peut PAS contenir \"  (le terminateur de champ).
# Il peut contenir \\  (backslash échappé) ou \u  (escape unicode), etc.
# _FV = valeur de champ sans backtracking inter-champs :
#   [^\\"]  = tout caractère sauf \ et "
#   \\[^"]  = un \ suivi d'un non-"  (donc \u, \\, \n… mais pas \")
_FV = r'(?:[^\\"]|\\[^"])*'

# Suffixe "| Lenox DD.MM" dans les noms d'artistes xceed.me → artiste seul
_ARTIST_SUFFIX_RE = re.compile(r"\s*\|.*$")

_EV_RE = re.compile(
    r'\\"legacyId\\":(\d+)'
    r',\\"name\\":\\"(' + _FV + r')\\"'
    r',\\"slug\\":\\"(' + _FV + r')\\"'
    r',\\"startingTime\\":(\d{10})'
    r'[\s\S]*?'                            # jusqu'au coverUrl de cet événement
    r'\\"coverUrl\\":\\"(' + _FV + r')\\"',
)


def _decode_rsc_string(s: str) -> str:
    """
    Décode un extrait de chaîne JS (un niveau d'échappement).
    Convertit les séquences \\uXXXX → caractères Unicode.
    Convertit les séquences \\/ → /.
    """
    # Séquences \uXXXX (unicode escapes dans la chaîne JS)
    s = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), s)
    # Slashes échappés (\/) dans les URLs JSON
    s = s.replace('\\/', '/')
    return s


def _last_sunday(year: int, month: int) -> date:
    """Retourne le dernier dimanche du mois donné."""
    # Premier jour du mois suivant, moins 1 jour = dernier jour du mois
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    # Recule jusqu'au dimanche (weekday 6)
    days_back = (last_day.weekday() + 1) % 7  # +1 car weekday() 0=lundi, 6=dimanche
    return last_day - timedelta(days=days_back)


def _luxembourg_utc_offset(dt_utc: datetime) -> int:
    """
    Retourne le décalage UTC (en heures) pour Europe/Luxembourg
    (CET = +1 en hiver, CEST = +2 en été).

    Heure d'été :  dernier dimanche de mars     à 01:00 UTC
    Heure d'hiver: dernier dimanche d'octobre   à 01:00 UTC
    """
    year = dt_utc.year
    cest_start = datetime(
        *_last_sunday(year, 3).timetuple()[:3], 1, 0, 0, tzinfo=timezone.utc
    )
    cet_start = datetime(
        *_last_sunday(year, 10).timetuple()[:3], 1, 0, 0, tzinfo=timezone.utc
    )
    if cest_start <= dt_utc < cet_start:
        return 2  # CEST (heure d'été)
    return 1  # CET (heure d'hiver)


def _parse_rsc_events(html: str, today: date) -> list[dict]:
    """
    Extrait les événements du payload RSC embarqué dans le HTML Next.js.

    Retourne une liste de dicts avec les clés :
        id, artist, date_live, doors_time, url, buy_link, image
    """
    # Concaténer tous les fragments RSC trouvés dans la page
    rsc_fragments = _RSC_PUSH_RE.findall(html)
    if not rsc_fragments:
        logger.warning("Aucun fragment RSC (self.__next_f.push) trouvé dans le HTML")
        return []
    rsc_payload = "".join(rsc_fragments)
    logger.debug("Payload RSC total : %d caractères", len(rsc_payload))

    # Extraire les événements via regex sur le JSON double-échappé
    matches = _EV_RE.findall(rsc_payload)
    logger.info("%d blocs événements trouvés dans le payload RSC", len(matches))

    seen_ids: set[str] = set()
    events: list[dict] = []

    for legacy_id, raw_name, raw_slug, ts_str, raw_cover in matches:
        if legacy_id in seen_ids:
            continue
        seen_ids.add(legacy_id)

        # Décoder les échappements JS
        name = _ARTIST_SUFFIX_RE.sub("", _decode_rsc_string(raw_name)).strip()
        slug = _decode_rsc_string(raw_slug)
        cover_url = _decode_rsc_string(raw_cover)

        # Convertir le timestamp Unix (UTC) en heure locale Luxembourg
        ts = int(ts_str)
        dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
        offset_hours = _luxembourg_utc_offset(dt_utc)
        dt_local = dt_utc + timedelta(hours=offset_hours)

        date_live = dt_local.strftime("%Y-%m-%d")
        doors_time = dt_local.strftime("%H:%M")

        # Filtrer les événements passés
        if date_live < today.isoformat():
            logger.debug("Événement passé ignoré : %s le %s", name, date_live)
            continue

        event_url = f"https://xceed.me/fr/luxembourg/event/{slug}/{legacy_id}"

        events.append({
            "id": f"lenox-{legacy_id}",
            "artist": name,
            "date_live": date_live,
            "doors_time": doors_time,
            "url": event_url,
            "buy_link": event_url,
            "image": cover_url or None,
        })
        logger.debug("Événement extrait : %s le %s", name, date_live)

    return events


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

    Retourne ["Concerts"] en cas d'échec. Résultats mis en cache.
    """
    clean_name = re.sub(r"\s*\([A-Z]{2,3}\)\s*$", "", artist_name).strip()
    key = clean_name.lower()

    if key in _genre_cache:
        return _genre_cache[key]

    fallback = ["Concerts"]
    deezer_headers = {"Accept-Language": "en"}

    try:
        url1 = f"https://api.deezer.com/search/artist?q={_url_quote(clean_name)}&limit=1"
        data1, _ = _request_json(url1)
        artists = data1.get("data", [])
        if not artists:
            _genre_cache[key] = fallback
            return fallback
        artist_id = artists[0]["id"]

        url2 = f"https://api.deezer.com/artist/{artist_id}/top?limit=1"
        data2, _ = _request_json(url2)
        tracks = data2.get("data", [])
        if not tracks:
            _genre_cache[key] = fallback
            return fallback
        album_id = tracks[0].get("album", {}).get("id")
        if not album_id:
            _genre_cache[key] = fallback
            return fallback

        url3 = f"https://api.deezer.com/album/{album_id}"
        data3, _ = _request_json(url3, extra_headers=deezer_headers)
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
    Récupère la liste complète des concerts à venir depuis xceed.me/lenox-club.

    Étapes :
      1. Fetch HTML de la page venue xceed.me (Next.js SSR)
      2. Extraction des événements depuis le payload RSC (regex)
      3. Enrichissement des genres via l'API Deezer
    """
    run_timestamp = datetime.now(timezone.utc).isoformat()
    today = date.today()

    # --- 1. Récupération de la page ---
    logger.info("Récupération de la page %s …", SOURCE_URL)
    html, _ = _request(SOURCE_URL)
    logger.info("Page récupérée (%d octets)", len(html))

    # Vérification minimale
    if "startingTime" not in html and "__next_f" not in html:
        raise ValueError(
            "Ni 'startingTime' ni '__next_f' trouvés dans la page — "
            "la structure de xceed.me a peut-être changé."
        )

    # --- 2. Extraction depuis le payload RSC ---
    events_raw = _parse_rsc_events(html, today)
    logger.info("%d concerts à venir trouvés", len(events_raw))

    if not events_raw:
        logger.warning("Aucun concert à venir trouvé.")
        return {
            "scraped_at": run_timestamp,
            "source": SOURCE_URL,
            "total": 0,
            "concerts": [],
        }

    # --- 3. Enrichissement genres via Deezer ---
    logger.info("Récupération des genres Deezer pour %d artistes…", len(events_raw))
    for ev in events_raw:
        ev["deezer_genres"] = _fetch_deezer_genres(ev["artist"])
    logger.info("Genres Deezer récupérés.")

    # --- 4. Assemblage final ---
    concerts = []
    for ev in events_raw:
        concerts.append({
            "id": ev["id"],
            "artist": ev["artist"],
            "date_live": ev["date_live"],
            "doors_time": ev.get("doors_time"),
            "location": LENOX_LOCATION,
            "address": LENOX_ADDRESS,
            "genres": ev.get("deezer_genres") or ["Concerts"],
            "status": "buy_now",
            "url": ev["url"],
            "buy_link": ev["buy_link"],
            "image": ev.get("image"),
            "price": "Price Unavailable",
            "date_created": run_timestamp,
        })

    # --- 5. Filtres optionnels ---
    excluded_genres = _parse_exclusion_list(exclude_genres)
    if excluded_genres:
        before = len(concerts)
        concerts = [
            c for c in concerts
            if not any(g.lower() in excluded_genres for g in (c.get("genres") or []))
        ]
        logger.info(
            "Filtre genres %s : %d → %d concerts", excluded_genres, before, len(concerts)
        )

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
        "source": SOURCE_URL,
        "total": len(concerts),
        "concerts": concerts,
    }


# ---------------------------------------------------------------------------
# Écriture sécurisée (atomic write)
# ---------------------------------------------------------------------------

def _safe_write(target: Path, content: str) -> None:
    """Écrit dans un fichier temporaire puis renomme vers la cible."""
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
        description="Récupère la liste des concerts depuis xceed.me (Lenox Club Luxembourg)"
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
        help='Genres à exclure, séparés par des points-virgules (ex: "Techno")',
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
        "Démarrage du scraper Lenox/xceed (format=%s, exclude_genres=%s, exclude_statuses=%s)",
        args.format, args.exclude_genres, args.exclude_statuses,
    )

    try:
        data = fetch_concerts(
            exclude_genres=args.exclude_genres,
            exclude_statuses=args.exclude_statuses,
        )

        out_file = (
            (DIR_CSV / f"{SCRIPT_NAME}.csv")
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
        logger.error("❌ ERREUR STRUCTURE — la structure du site a changé : %s", exc)
        logger.info("Le fichier de sortie précédent n'a pas été modifié")
        sys.exit(1)
    except Exception as exc:
        logger.exception("❌ ERREUR INATTENDUE : %s", exc)
        logger.info("Le fichier de sortie précédent n'a pas été modifié")
        sys.exit(1)

    logger.info("Fin du scraper")


if __name__ == "__main__":
    main()
