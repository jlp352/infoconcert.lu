#!/usr/bin/env python3
"""
Scraper des concerts du Mergener Hof (MJC Trier) - Trier (Allemagne).

Source : https://mjctrier.de/events/kategorie/konzert/liste/
         (The Events Calendar — WordPress, filtre catégorie Konzert)

Stratégie :
  1. Parcours des pages liste /kategorie/konzert/ avec pagination (seite/N/)
  2. Chaque article avec la classe CSS cat_konzert est parsé (titre, date,
     heure, image, URL).
  3. Fetch de chaque page de détail → heure d'ouverture (Einlass) + lien
     de billetterie (Eventim, Ticket-Regional, Reservix…).
  4. Récupération du prix : ticket-regional.de (urllib) ou eventim.de (curl
     avec headers anti-Akamai) → champ "price" en "XX.XX EUR".
  5. Enrichissement des genres via l'API Deezer.

Fichiers générés :
    ./JSON/scrape_mergener_hof_trier_concerts.json
    ./CSV/scrape_mergener_hof_trier_concerts.csv
    ./Log/scrape_mergener_hof_trier_concerts.log

Usage :
    python scrape_mergener_hof_trier_concerts.py               # JSON (défaut)
    python scrape_mergener_hof_trier_concerts.py -f csv        # CSV
    python scrape_mergener_hof_trier_concerts.py -s "sold_out" # Exclure statuts
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
from html import unescape
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

LIST_URL = "https://mjctrier.de/events/kategorie/konzert/liste/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
MAX_RETRIES = 3
RETRY_DELAY = 5  # secondes entre chaque retry

MJC_LOCATION = "Mergener Hof - Trier"
MJC_ADDRESS  = "Rindertanzstraße 4, 54290 Trier, Allemagne"

CSV_COLUMNS = [
    "id", "artist", "date_live", "doors_time", "location",
    "address", "genres", "status", "url", "buy_link", "image",
    "price", "date_created",
]

logger = logging.getLogger("mjc_trier_scraper")


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
# Requêtes HTTP avec retry
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
# Parsing de la page liste
# ---------------------------------------------------------------------------

def _strip_html(text: str) -> str:
    """Supprime les balises HTML et nettoie les entités et espaces."""
    text = re.sub(r'<[^>]+>', '', text)
    return re.sub(r'\s+', ' ', unescape(text)).strip()


def _parse_show_time(time_text: str) -> str | None:
    """
    Extrait l'heure de début depuis le texte d'un élément <time>.
    Formats : "März 27 @ 20:00 - 23:00", "April 5 @ 19:30"
    Retourne "HH:MM" ou None.
    """
    m = re.search(r'@\s*(\d{1,2}):(\d{2})', time_text)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return None


def _extract_image(article_html: str) -> str | None:
    """
    Extrait l'URL de l'image depuis le wrapper tribe-events…featured-image.
    Cherche le premier src= après le marqueur de classe.
    """
    m = re.search(
        r'class="tribe-events-calendar-list__event-featured-image',
        article_html, re.IGNORECASE,
    )
    if not m:
        return None
    src_m = re.search(r'src="([^"]+)"', article_html[m.start():])
    return src_m.group(1) if src_m else None


def _parse_next_page_url(html: str) -> str | None:
    """
    Retourne l'URL de la page suivante depuis le bouton tribe-events-c-nav__next,
    ou None si c'est la dernière page.
    Fonctionne quel que soit l'ordre des attributs dans la balise <a>.
    """
    m = re.search(r'tribe-events-c-nav__next', html, re.IGNORECASE)
    if not m:
        return None
    # Remonter jusqu'au début de la balise <a> englobante
    start = html.rfind('<a', 0, m.start())
    if start == -1:
        return None
    end = html.find('>', start)
    if end == -1:
        return None
    tag = html[start:end + 1]
    href_m = re.search(r'href="([^"]+)"', tag)
    return unescape(href_m.group(1)) if href_m else None


def _parse_list_articles(html: str) -> list[dict]:
    """
    Extrait les concerts (articles .cat_konzert) depuis une page liste.

    Stratégie : découpage du HTML sur les balises <article>, puis filtrage
    sur la présence de la classe CSS cat_konzert.
    """
    results = []
    # Découper sur chaque ouverture <article
    parts = re.split(r'(?=<article\b)', html, flags=re.IGNORECASE)

    for part in parts:
        if 'cat_konzert' not in part:
            continue

        # --- Titre + URL ---
        # Chercher la div .tribe-events-calendar-list__event-title, puis le
        # premier <a href="...">...</a> qui suit
        title_marker = re.search(
            r'tribe-events-calendar-list__event-title', part, re.IGNORECASE,
        )
        if not title_marker:
            continue
        a_m = re.search(
            r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            part[title_marker.start():],
            re.DOTALL | re.IGNORECASE,
        )
        if not a_m:
            continue

        url   = unescape(a_m.group(1).strip())
        title = _strip_html(a_m.group(2))
        if not title or not url:
            continue

        # --- ID depuis le slug de l'URL ---
        slug     = url.rstrip('/').rsplit('/', 1)[-1]
        event_id = f"mjc_trier_{slug}"

        # --- Date ISO + texte horaire depuis <time datetime="YYYY-MM-DD"> ---
        dt_m = re.search(
            r'<time[^>]+datetime="(\d{4}-\d{2}-\d{2})"[^>]*>(.*?)</time>',
            part, re.DOTALL | re.IGNORECASE,
        )
        date_live = dt_m.group(1) if dt_m else None
        time_text = _strip_html(dt_m.group(2)) if dt_m else ""
        show_time = _parse_show_time(time_text)

        # --- Image ---
        image = _extract_image(part)

        results.append({
            "id":        event_id,
            "artist":    title,
            "date_live": date_live,
            "show_time": show_time,
            "url":       url,
            "image":     image,
        })
        logger.debug("Concert trouvé : %s (%s)", title, date_live)

    return results


# ---------------------------------------------------------------------------
# Parsing de la page de détail
# ---------------------------------------------------------------------------

# Heure d'ouverture des portes (Einlass: 19:00 Uhr)
_EINLASS_RE = re.compile(
    r'Einlass[:\s]+(\d{1,2})[:.Hh](\d{2})',
    re.IGNORECASE,
)

# Liens de billetterie courants
_BUY_LINK_RE = re.compile(
    r'href="(https?://[^"]*(?:'
    r'eventim\.de|ticket-regional\.de|reservix\.de|'
    r'tickets\.de|ticketmaster\.de|pretix\.eu|'
    r'eticket\.de|koka36\.de'
    r')[^"]*)"',
    re.IGNORECASE,
)


def _parse_detail_page(html: str) -> dict:
    """
    Extrait depuis une page de détail d'événement :
      - doors_time : heure Einlass ("HH:MM") ou None
      - buy_link   : premier lien de billetterie reconnu, ou None
    """
    m_einlass = _EINLASS_RE.search(html)
    doors_time = (
        f"{int(m_einlass.group(1)):02d}:{m_einlass.group(2)}"
        if m_einlass else None
    )

    m_buy  = _BUY_LINK_RE.search(html)
    buy_link = unescape(m_buy.group(1)) if m_buy else None

    return {"doors_time": doors_time, "buy_link": buy_link}


# ---------------------------------------------------------------------------
# Récupération du prix depuis ticket-regional.de et eventim.de
# ---------------------------------------------------------------------------

# ticket-regional.de : <td class="categoryCosts">&euro; 28.00</td>
_TR_COST_RE = re.compile(
    r'class="categoryCosts"[^>]*>\s*&euro;\s*([\d.,]+)',
    re.IGNORECASE,
)

# eventim.de : JSON-LD schema.org MusicEvent → offers → lowPrice
# Ex: {"@type":"AggregateOffer","lowPrice":"24.80","priceCurrency":"EUR"}
_EV_PRICE_RE = re.compile(
    r'"@type"\s*:\s*"AggregateOffer"[^}]*"lowPrice"\s*:\s*"([\d.]+)"'
    r'[^}]*"priceCurrency"\s*:\s*"([A-Z]+)"',
    re.DOTALL,
)

# Headers curl qui contournent la protection Akamai d'eventim.de
_EVENTIM_CURL_HEADERS = [
    ("Accept",             "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
    ("Accept-Language",    "de-DE,de;q=0.9,en;q=0.8"),
    ("sec-fetch-site",     "none"),
    ("sec-fetch-mode",     "navigate"),
    ("sec-fetch-dest",     "document"),
    ("sec-ch-ua",          '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"'),
    ("sec-ch-ua-mobile",   "?0"),
    ("sec-ch-ua-platform", '"Windows"'),
]


def _curl_get_eventim(url: str) -> str:
    """
    Fetch curl d'une page eventim.de avec les headers anti-Akamai.
    Retourne le body HTML ou lève RuntimeError.
    """
    cmd = [
        "curl", "--silent", "--compressed", "--location",
        "--max-time", "30",
        "-A", USER_AGENT,
        "--write-out", "\n===HTTP_STATUS===%{http_code}",
    ]
    for key, val in _EVENTIM_CURL_HEADERS:
        cmd += ["--header", f"{key}: {val}"]
    cmd.append(url)

    try:
        result = subprocess.run(cmd, capture_output=True, text=False, timeout=35)
    except FileNotFoundError:
        raise RuntimeError("curl introuvable")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"curl timeout sur {url}")

    if result.returncode not in (0, 22):
        raise RuntimeError(f"curl code {result.returncode} pour {url}")

    raw = (result.stdout or b"").decode("utf-8", errors="replace")
    body, _, status_str = raw.rpartition("\n===HTTP_STATUS===")
    http_code = int(status_str.strip()) if status_str.strip().isdigit() else 0
    if http_code >= 400:
        raise RuntimeError(f"HTTP {http_code} pour {url}")
    return body


def _fetch_buy_link_price(url: str) -> str:
    """
    Récupère le prix minimum depuis une page de billetterie.
    Supporte : ticket-regional.de (urllib) et eventim.de (curl).
    Retourne "XX.XX EUR", "Free" ou "Price Unavailable".
    """
    if not url:
        return "Price Unavailable"

    try:
        # --- ticket-regional.de ---
        if "ticket-regional.de" in url:
            html = _request(url)
            prices = []
            for m in _TR_COST_RE.finditer(html):
                raw = m.group(1).replace(",", ".")
                try:
                    prices.append(float(raw))
                except ValueError:
                    pass
            if not prices:
                return "Price Unavailable"
            min_price = min(prices)
            return "Free" if min_price == 0 else f"{min_price:.2f} EUR"

        # --- eventim.de ---
        if "eventim.de" in url:
            # Ne garder que la partie path/slug, sans les paramètres de tracking
            clean_url = url.split("?")[0].rstrip("/") + "/"
            html = _curl_get_eventim(clean_url)
            m = _EV_PRICE_RE.search(html)
            if not m:
                return "Price Unavailable"
            raw_price = m.group(1)   # "24.80"
            currency  = m.group(2)   # "EUR"
            val = float(raw_price)
            return "Free" if val == 0 else f"{val:.2f} {currency}"

    except Exception as exc:
        logger.debug("Prix non récupéré pour %s : %s", url, exc)

    return "Price Unavailable"


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
    Récupère la liste complète des concerts Mergener Hof Trier.

    Étapes :
      1. Parcours des pages liste (catégorie Konzert) avec pagination
      2. Fetch de chaque page de détail : heure d'ouverture + lien billet
      3. Fetch du prix depuis ticket-regional.de (quand buy_link disponible)
      4. Enrichissement des genres via l'API Deezer
    """
    run_timestamp = datetime.now(timezone.utc).isoformat()

    # --- 1. Pages liste ---
    raw_events: list[dict] = []
    page = 1
    current_url: str | None = LIST_URL
    visited_urls: set[str] = set()  # protège contre le wrap-around nav

    while current_url and current_url not in visited_urls:
        visited_urls.add(current_url)
        logger.info("Scraping page %d : %s", page, current_url)
        html = _request(current_url)
        batch = _parse_list_articles(html)
        logger.info("  → %d concert(s) trouvé(s) sur cette page", len(batch))
        raw_events.extend(batch)

        current_url = _parse_next_page_url(html)
        page += 1
        if current_url and current_url not in visited_urls:
            time.sleep(1)  # politesse entre pages liste

    if not raw_events:
        logger.warning(
            "Aucun concert trouvé — vérifier si le site est en maintenance "
            "ou si la structure HTML a changé."
        )
        return {
            "scraped_at": run_timestamp,
            "source":     LIST_URL,
            "total":      0,
            "concerts":   [],
        }

    logger.info("%d concert(s) trouvé(s) au total sur toutes les pages", len(raw_events))

    # --- 2. Pages de détail (doors_time + buy_link) ---
    logger.info("Fetch des pages de détail pour %d concerts…", len(raw_events))
    for ev in raw_events:
        try:
            detail_html = _request(ev["url"])
            detail = _parse_detail_page(detail_html)
            # Préférer l'heure Einlass ; sinon conserver l'heure de début
            ev["doors_time"] = detail["doors_time"] or ev.get("show_time")
            ev["buy_link"]   = detail["buy_link"]
        except Exception as exc:
            logger.warning("Détail non récupéré pour %s : %s", ev["url"], exc)
            ev["doors_time"] = ev.get("show_time")
            ev["buy_link"]   = None
        time.sleep(0.5)  # politesse entre requêtes détail

    # --- 3. Prix depuis ticket-regional.de et eventim.de ---
    priced_events = [
        ev for ev in raw_events
        if any(p in (ev.get("buy_link") or "") for p in ("ticket-regional.de", "eventim.de"))
    ]
    if priced_events:
        logger.info(
            "Récupération du prix (ticket-regional + eventim) pour %d concerts…",
            len(priced_events),
        )
    for ev in priced_events:
        ev["price"] = _fetch_buy_link_price(ev["buy_link"])
        logger.debug("Prix %s → %s", ev["artist"], ev["price"])
        time.sleep(0.5)

    # --- 4. Enrichissement Deezer ---
    logger.info("Enrichissement des genres via Deezer pour %d artistes…", len(raw_events))
    for ev in raw_events:
        ev["genres"] = _fetch_deezer_genres(ev["artist"])
    logger.info("Genres Deezer récupérés.")

    # --- 5. Assemblage final ---
    concerts = [
        {
            "id":           ev["id"],
            "artist":       ev["artist"],
            "date_live":    ev["date_live"],
            "doors_time":   ev.get("doors_time"),
            "location":     MJC_LOCATION,
            "address":      MJC_ADDRESS,
            "genres":       ev.get("genres") or ["Concerts"],
            "status":       "buy_now",   # pas d'info sold_out exposée sur ce site
            "url":          ev["url"],
            "buy_link":     ev.get("buy_link"),
            "image":        ev.get("image"),
            "price":        ev.get("price", "Price Unavailable"),
            "date_created": run_timestamp,
        }
        for ev in raw_events
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
        "source":     LIST_URL,
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
            "Récupère les concerts du Mergener Hof (MJC Trier) "
            "depuis mjctrier.de (The Events Calendar, catégorie Konzert)"
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
        "Démarrage du scraper Mergener Hof Trier "
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

    except (HTTPError, URLError) as exc:
        logger.error("❌ ERREUR RÉSEAU : %s", exc)
        logger.info("Le fichier de sortie précédent n'a pas été modifié.")
        sys.exit(1)
    except ValueError as exc:
        logger.error("❌ ERREUR STRUCTURE : %s", exc)
        logger.info("Le fichier de sortie précédent n'a pas été modifié.")
        sys.exit(1)
    except Exception as exc:
        logger.exception("❌ ERREUR INATTENDUE : %s", exc)
        logger.info("Le fichier de sortie précédent n'a pas été modifié.")
        sys.exit(1)

    logger.info("Fin du scraper")


if __name__ == "__main__":
    main()
