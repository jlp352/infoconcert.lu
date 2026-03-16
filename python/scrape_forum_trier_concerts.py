#!/usr/bin/env python3
"""
Scraper des concerts du Forum Concert / Metropolis - Trier (Allemagne).

Source : widget Eventim Light embarqué sur https://www.forum-concert.com/
  URL iframe : https://www.eventim-light.com/de/a/5e56da3c25c0670998736d41/iframe/

Les requêtes Eventim Light transitent par curl (subprocess stdlib) afin de
présenter une empreinte TLS identique à Chrome et contourner Cloudflare.
Les événements sont extraits depuis window.__INITIAL_STATE__ (SSR JSON).
Les requêtes Deezer utilisent urllib classique (pas de protection TLS).

Fichiers générés :
    ./JSON/scrape_forum_trier_concerts.json
    ./CSV/scrape_forum_trier_concerts.csv
    ./Log/scrape_forum_trier_concerts.log

Usage :
    python scrape_forum_trier_concerts.py               # JSON (défaut)
    python scrape_forum_trier_concerts.py -f csv        # CSV
    python scrape_forum_trier_concerts.py -s "sold_out" # Exclure des statuts
"""

import argparse
import csv
import io
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote as _url_quote
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR  = Path(__file__).resolve().parent
SCRIPT_NAME = Path(__file__).stem
DIR_JSON    = SCRIPT_DIR / "JSON"
DIR_CSV     = SCRIPT_DIR / "CSV"
DIR_LOG     = SCRIPT_DIR / "Log"

# Widget Eventim Light — Forum Concert / Metropolis Trier
EVENTIM_LIGHT_URL = (
    "https://www.eventim-light.com/de/a/5e56da3c25c0670998736d41/iframe/"
)

SOURCE_URL = "https://www.forum-concert.com/index.html#events"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
MAX_RETRIES = 3
RETRY_DELAY = 5  # secondes entre chaque retry

FORUM_LOCATION = "Forum Concert - Trier"
FORUM_ADDRESS  = "Gerty-Spies-Str. 4, 54290 Trier, Allemagne"

CSV_COLUMNS = [
    "id", "artist", "date_live", "doors_time", "location",
    "address", "genres", "status", "url", "buy_link", "image",
    "price", "date_created",
]

logger = logging.getLogger("forum_trier_scraper")


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
# Requêtes via curl (contourne le TLS fingerprinting Cloudflare / Eventim)
# ---------------------------------------------------------------------------

# Headers pour l'iframe Eventim Light (chargée depuis forum-concert.com)
_EVENTIM_CURL_HEADERS = [
    ("Accept",             "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
    ("Accept-Language",    "de,en;q=0.5"),
    ("Referer",            "https://www.forum-concert.com/"),
    ("sec-fetch-site",     "cross-site"),
    ("sec-fetch-mode",     "navigate"),
    ("sec-fetch-dest",     "iframe"),
    ("sec-ch-ua",          '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"'),
    ("sec-ch-ua-mobile",   "?0"),
    ("sec-ch-ua-platform", '"Windows"'),
]


def _curl_get(url: str, extra_headers: list[tuple[str, str]] | None = None) -> str:
    """
    Effectue un GET via curl et retourne le body en str.
    Lève RuntimeError si curl échoue (code ≥ 1) ou si HTTP ≥ 400.
    """
    cmd = [
        "curl",
        "--silent",          # pas de barre de progression
        "--compressed",      # accepte gzip/br, décompresse automatiquement
        "--location",        # suit les redirections
        "--max-time", "30",
        "-A", USER_AGENT,
        # --write-out sépare le body du code HTTP (dernière ligne)
        "--write-out", "\n===HTTP_STATUS===%{http_code}",
    ]
    for key, val in (_EVENTIM_CURL_HEADERS + (extra_headers or [])):
        cmd += ["--header", f"{key}: {val}"]
    cmd.append(url)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=False, timeout=35  # bytes, pas text
        )
    except FileNotFoundError:
        raise RuntimeError(
            "curl introuvable. Installez curl ou ajoutez-le au PATH."
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"curl timeout sur {url}")

    if result.returncode not in (0, 22):
        stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"curl a retourné le code {result.returncode} pour {url}\n"
            f"stderr : {stderr}"
        )

    # Décode en UTF-8 (la réponse Eventim est UTF-8, pas cp1252)
    raw = (result.stdout or b"").decode("utf-8", errors="replace")

    # Sépare le body du code HTTP injecté en fin de réponse
    body, _, status_str = raw.rpartition("\n===HTTP_STATUS===")
    http_code = int(status_str.strip()) if status_str.strip().isdigit() else 0

    if http_code >= 400:
        preview = body[:300].strip() if body else "(body vide)"
        raise RuntimeError(f"HTTP {http_code} pour {url} — réponse : {preview}")

    return body


def _curl_request_json(url: str, retries: int = MAX_RETRIES) -> dict:
    """
    GET JSON via curl avec retry automatique.
    Retourne le dict Python parsé.
    """
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            body = _curl_get(url)
            return json.loads(body)
        except (RuntimeError, json.JSONDecodeError) as exc:
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
# Requêtes urllib classiques (Deezer — pas de protection TLS)
# ---------------------------------------------------------------------------

def _request(
    url: str,
    *,
    as_json: bool = False,
    retries: int = MAX_RETRIES,
    encoding: str = "utf-8",
    extra_headers: dict | None = None,
) -> str | dict:
    """GET urllib avec retry automatique. Retourne str ou dict selon as_json."""
    last_exc: Exception | None = None
    headers = {
        "User-Agent":      USER_AGENT,
        "Accept-Language": "de,en;q=0.5",
        "Accept-Encoding": "identity",
        **(extra_headers or {}),
    }
    for attempt in range(1, retries + 1):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=30) as resp:
                body = resp.read().decode(encoding, errors="replace")
                return json.loads(body) if as_json else body
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
# Fetching + parsing Eventim Light (via curl)
# ---------------------------------------------------------------------------

def _extract_page_context(html: str) -> dict | None:
    """
    Extrait le JSON du script <script id="vike_pageContext"> dans le HTML
    server-side rendered d'Eventim Light.
    C'est là que réside initialStoreState.events.eventOverviewItems.
    """
    m = re.search(
        r'<script[^>]+id="vike_pageContext"[^>]*>(.*?)</script>',
        html, re.DOTALL,
    )
    if not m:
        logger.warning("Script vike_pageContext introuvable dans le HTML")
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        logger.warning("JSON vike_pageContext invalide : %s", exc)
        return None


def _extract_events_from_context(ctx: dict) -> list[dict]:
    """Navigue vers initialStoreState.events.eventOverviewItems."""
    try:
        items = ctx["initialStoreState"]["events"]["eventOverviewItems"]
        return items if isinstance(items, list) else []
    except (KeyError, TypeError):
        logger.warning(
            "Chemin initialStoreState→events→eventOverviewItems introuvable"
        )
        return []


def _fetch_all_forum_events() -> list[dict]:
    """
    Récupère les événements depuis l'iframe Eventim Light (via curl).
    Les données sont dans le JSON du script vike_pageContext (SSR Vike).
    """
    logger.info("Fetch de l'iframe Eventim Light (via curl)…")
    html = _curl_get(EVENTIM_LIGHT_URL)
    logger.debug("HTML reçu : %d caractères", len(html))

    ctx = _extract_page_context(html)
    if ctx is None:
        return []

    events = _extract_events_from_context(ctx)
    logger.info("%d événement(s) trouvés dans vike_pageContext", len(events))
    return events


# ---------------------------------------------------------------------------
# Parsing des dates (ISO 8601 avec offset tz — déjà en heure locale)
# ---------------------------------------------------------------------------

def _parse_iso_date(raw: str | None) -> tuple[str | None, str | None]:
    """
    Parse une date ISO 8601 avec offset tz (ex: "2026-04-16T20:00:00+02:00").
    Les dates Eventim Light sont déjà en heure locale — pas de conversion UTC.
    Retourne (date "YYYY-MM-DD", heure "HH:MM") ou (None, None).
    """
    if not raw:
        return None, None
    # Normalise "+02:00" → "+0200" pour strptime
    s = re.sub(r"([+-]\d{2}):(\d{2})$", r"\1\2", raw.strip())
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            date_str = dt.strftime("%Y-%m-%d")
            time_str = dt.strftime("%H:%M") if (dt.hour or dt.minute) else None
            return date_str, time_str
        except ValueError:
            continue
    logger.warning("Impossible de parser la date : %r", raw)
    return None, None


# ---------------------------------------------------------------------------
# Parsing d'un événement Eventim Light (champs réels vike_pageContext)
# ---------------------------------------------------------------------------

def _parse_event(ev: dict) -> dict | None:
    """
    Normalise un événement eventOverviewItems vers le schéma interne.
    Champs réels confirmés : id, title, start, doorsOpen, minPrice, soldout, image.id
    Retourne None si les champs essentiels sont absents.
    """
    ev_id = str(ev.get("id") or "").strip()
    if not ev_id:
        return None

    artist = (ev.get("title") or "").strip()
    if not artist:
        return None

    # Heure de spectacle (start) et d'ouverture des portes (doorsOpen)
    date_live, show_time = _parse_iso_date(ev.get("start"))
    _, doors_time        = _parse_iso_date(ev.get("doorsOpen"))

    # Prix et statut
    mp        = ev.get("minPrice") or {}
    price_val = mp.get("value")
    currency  = mp.get("currency", "EUR")
    sold_out  = ev.get("soldout", False)

    if sold_out:
        price_str = f"{price_val:.2f} {currency}" if price_val is not None else "Price Unavailable"
        status = "sold_out"
    elif price_val is None:
        price_str, status = "Price Unavailable", "buy_now"
    elif price_val == 0:
        price_str, status = "Free", "free"
    else:
        price_str = f"{price_val:.2f} {currency}"
        status    = "buy_now"

    # Image via l'API Eventim Light
    image_id = (ev.get("image") or {}).get("id")
    image = (
        f"https://www.eventim-light.com/de/api/image/{image_id}/shop_cover_v3/webp"
        if image_id else None
    )

    # URL : page officielle de la salle (widget Eventim Light intégré)
    # Les routes internes du widget ne sont pas accessibles hors navigateur.
    url = SOURCE_URL

    # buy_link : recherche sur Eventim.de — artiste + "trier" pour cibler la salle
    buy_link = (
        f"https://www.eventim.de/search/"
        f"?affiliate=EVE&searchterm={_url_quote(artist + ' trier')}"
    )

    return {
        "id":         f"forum_trier_{ev_id}",
        "artist":     artist,
        "date_live":  date_live,
        "doors_time": doors_time or show_time,
        "image":      image,
        "price":      price_str,
        "status":     status,
        "url":        url,
        "buy_link":   buy_link,
    }


# ---------------------------------------------------------------------------
# Enrichissement des genres via l'API Deezer
# ---------------------------------------------------------------------------

_genre_cache: dict[str, list[str]] = {}


def _fetch_deezer_genres(artist_name: str) -> list[str]:
    """
    Récupère les genres musicaux d'un artiste via l'API Deezer.
    Chaîne : search/artist → artist/top → album → genres.data
    Résultats mis en cache par nom d'artiste.
    """
    clean_name = re.sub(r"\s*\([A-Z]{2,3}\)\s*$", "", artist_name).strip()
    key = clean_name.lower()

    if key in _genre_cache:
        return _genre_cache[key]

    fallback = ["Concerts"]

    try:
        data1 = _request(
            f"https://api.deezer.com/search/artist?q={_url_quote(clean_name)}&limit=1",
            as_json=True,
        )
        artists = data1.get("data", [])
        if not artists:
            _genre_cache[key] = fallback
            return fallback
        artist_id = artists[0]["id"]

        data2 = _request(
            f"https://api.deezer.com/artist/{artist_id}/top?limit=1",
            as_json=True,
        )
        tracks = data2.get("data", [])
        if not tracks:
            _genre_cache[key] = fallback
            return fallback
        album_id = tracks[0].get("album", {}).get("id")
        if not album_id:
            _genre_cache[key] = fallback
            return fallback

        data3 = _request(
            f"https://api.deezer.com/album/{album_id}",
            as_json=True,
            extra_headers={"Accept-Language": "en"},
        )
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
# Collecte principale
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
    Récupère la liste complète des concerts Forum Trier.

    Étapes :
      1. API publique Eventim via curl (filtrage Trier → Forum Concert)
      2. Parsing et normalisation des événements
      3. Enrichissement des genres via API Deezer
    """
    run_timestamp = datetime.now(timezone.utc).isoformat()

    # --- 1. Fetch via l'API Eventim (curl) ---
    raw_events = _fetch_all_forum_events()
    if not raw_events:
        logger.warning(
            "Aucun événement trouvé — la salle n'a peut-être pas de concerts "
            "programmés, ou le filtre par ville/salle n'a rien retourné."
        )
        return {
            "scraped_at": run_timestamp,
            "source":     SOURCE_URL,
            "total":      0,
            "concerts":   [],
        }

    logger.info("%d événements bruts trouvés via l'API Eventim", len(raw_events))

    # --- 2. Parsing ---
    parsed = []
    for ev in raw_events:
        concert = _parse_event(ev)
        if concert is None:
            logger.debug("Événement ignoré (champs essentiels manquants) : %s", ev)
            continue
        parsed.append(concert)

    logger.info("%d événement(s) valides après parsing", len(parsed))

    # --- 3. Enrichissement Deezer ---
    logger.info(
        "Enrichissement des genres via Deezer pour %d artistes…", len(parsed)
    )
    for ev in parsed:
        ev["genres"] = _fetch_deezer_genres(ev["artist"])
    logger.info("Genres Deezer récupérés.")

    # --- 4. Assemblage final ---
    concerts = [
        {
            "id":           ev["id"],
            "artist":       ev["artist"],
            "date_live":    ev["date_live"],
            "doors_time":   ev["doors_time"],
            "location":     FORUM_LOCATION,
            "address":      FORUM_ADDRESS,
            "genres":       ev.get("genres") or ["Concerts"],
            "status":       ev.get("status"),
            "url":          ev.get("url"),
            "buy_link":     ev.get("buy_link"),
            "image":        ev.get("image"),
            "price":        ev.get("price", "Price Unavailable"),
            "date_created": run_timestamp,
        }
        for ev in parsed
    ]

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
        "source":     SOURCE_URL,
        "total":      len(concerts),
        "concerts":   concerts,
    }


# ---------------------------------------------------------------------------
# Écriture atomique
# ---------------------------------------------------------------------------

def _safe_write(target: Path, content: str) -> None:
    """Écrit dans un fichier temporaire puis renomme vers la cible (atomic write)."""
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
        description=(
            "Récupère les concerts du Forum Concert / Metropolis de Trier "
            "via l'API Eventim publique (requêtes curl)"
        )
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
        help='Genres à exclure, séparés par des points-virgules (ex: "Pop")',
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
        "Démarrage du scraper Forum Concert Trier — source: API Eventim via curl "
        "(format=%s, exclude_genres=%s, exclude_statuses=%s)",
        args.format, args.exclude_genres, args.exclude_statuses,
    )

    try:
        data = fetch_concerts(
            exclude_genres=args.exclude_genres,
            exclude_statuses=args.exclude_statuses,
        )

        out_file = (
            DIR_CSV  / f"{SCRIPT_NAME}.csv"
            if args.format == "csv"
            else DIR_JSON / f"{SCRIPT_NAME}.json"
        )

        if args.format == "csv":
            _safe_write(out_file, concerts_to_csv(data["concerts"]))
        else:
            _safe_write(out_file, json.dumps(data, ensure_ascii=False, indent=2))

        logger.info("✅ %d concerts sauvegardés → %s", data["total"], out_file)

    except RuntimeError as exc:
        # Erreur curl (curl absent, HTTP 4xx/5xx, timeout…)
        logger.error("❌ ERREUR CURL (Eventim Light) : %s", exc)
        logger.info("Le fichier de sortie précédent n'a pas été modifié.")
        sys.exit(1)
    except (HTTPError, URLError) as exc:
        logger.error("❌ ERREUR RÉSEAU (Deezer) : %s", exc)
        logger.info("Le fichier de sortie précédent n'a pas été modifié.")
        sys.exit(1)
    except ValueError as exc:
        logger.error("❌ ERREUR STRUCTURE — %s", exc)
        logger.info("Le fichier de sortie précédent n'a pas été modifié.")
        sys.exit(1)
    except Exception as exc:
        logger.exception("❌ ERREUR INATTENDUE : %s", exc)
        logger.info("Le fichier de sortie précédent n'a pas été modifié.")
        sys.exit(1)

    logger.info("Fin du scraper")


if __name__ == "__main__":
    main()
