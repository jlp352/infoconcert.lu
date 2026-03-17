#!/usr/bin/env python3
"""
Scraper des concerts et spectacles disponibles sur https://www.le-galaxie.com/evenements/
(Le Galaxie — Amnéville, France)

Le site est un WordPress avec pagination (/evenements/page/{n}).
Les données sont extraites via :
  1. Pages de liste   → URLs des événements + statut (on_sale / sold_out)
  2. Page détail      → JSON-LD schema.org/Event (date, heure, buy_link, image)
  3. API Deezer       → enrichissement des genres musicaux

Fichiers générés dans des sous-dossiers relatifs à l'emplacement du script :
    ./JSON/scrape_galaxie_amneville_concerts.json
    ./CSV/scrape_galaxie_amneville_concerts.csv
    ./Log/scrape_galaxie_amneville_concerts.log

Usage:
    python scrape_galaxie_amneville_concerts.py                       # JSON (défaut)
    python scrape_galaxie_amneville_concerts.py -f csv                # CSV
    python scrape_galaxie_amneville_concerts.py -f csv -s "sold_out"  # Exclure des statuts
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
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote as _url_quote
from urllib.request import Request, build_opener, urlopen

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT_NAME = Path(__file__).stem
DIR_JSON = SCRIPT_DIR / "JSON"
DIR_CSV  = SCRIPT_DIR / "CSV"
DIR_LOG  = SCRIPT_DIR / "Log"

BASE_URL   = "https://www.le-galaxie.com"
EVENTS_URL = f"{BASE_URL}/evenements/"
PAGE_URL   = f"{BASE_URL}/evenements/page/{{n}}/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
MAX_RETRIES  = 3
RETRY_DELAY  = 5   # secondes entre chaque retry
POLITE_DELAY = 0.4 # délai entre requêtes détail (politesse)

VENUE_LOCATION = "Le Galaxie - Amnéville"
VENUE_ADDRESS  = "Rue des Artistes, 57360 Amnéville, France"

CSV_COLUMNS = [
    "id", "artist", "date_live", "doors_time", "location",
    "address", "genres", "status", "url", "buy_link", "image",
    "price", "date_created",
]

logger = logging.getLogger("galaxie_amneville_scraper")


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

def _http_get(url: str, retries: int = MAX_RETRIES) -> str:
    """GET avec retry. Retourne le corps décodé en UTF-8."""
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = Request(
                url,
                headers={
                    "User-Agent":      USER_AGENT,
                    "Accept-Language": "fr-FR,fr;q=0.9",
                    "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
                },
            )
            with urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError, ConnectionError) as exc:
            last_exc = exc
            if attempt < retries:
                logger.warning(
                    "GET tentative %d/%d échouée pour %s : %s — retry dans %ds",
                    attempt, retries, url, exc, RETRY_DELAY,
                )
                time.sleep(RETRY_DELAY)
            else:
                logger.error("GET échec définitif pour %s : %s", url, exc)
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Parsing — pages de liste
# ---------------------------------------------------------------------------

# Numéros de pages dans la pagination (pas de slash final requis)
_PAGE_NUM_RE = re.compile(r'/evenements/page/(\d+)')

# Carte événement : <div class="card-event status-on_sale" data-title="..." data-category="...">
_CARD_HEAD_RE = re.compile(
    r'<div\s+class="card-event\s+([^"]+)"\s+data-title="([^"]*)"\s+data-category="([^"]*)"'
)

# URL de la page détail dans le bloc d'une carte
_CARD_URL_RE = re.compile(
    r'href="(https://www\.le-galaxie\.com/evenement/[^"]+)"'
)

# Image de la carte (src ou data-src, hébergée sur le domaine)
_CARD_IMG_RE = re.compile(
    r'(?:data-src|src)="(https://www\.le-galaxie\.com/wp-content/[^"]+)"'
)


def _get_max_page(html: str) -> int:
    """Retourne le numéro de la dernière page de pagination (1 si aucune)."""
    nums = [int(m.group(1)) for m in _PAGE_NUM_RE.finditer(html)]
    return max(nums, default=1)


def _parse_status_from_class(css_class: str) -> str:
    lc = css_class.lower()
    if "sold_out" in lc or "soldout" in lc:
        return "sold_out"
    if "free" in lc:
        return "free"
    return "buy_now"


def _parse_cards(html: str) -> list[dict]:
    """
    Parse les cartes événements d'une page de liste.

    Stratégie : on localise chaque tête de carte (<div class="card-event …">)
    puis on découpe le HTML entre deux têtes consécutives pour extraire URL
    et image depuis le bloc propre à chaque carte.
    """
    matches = list(_CARD_HEAD_RE.finditer(html))
    events: list[dict] = []
    seen_urls: set[str] = set()

    for i, m in enumerate(matches):
        css_class = m.group(1)
        title     = unescape(m.group(2)).strip()
        category  = unescape(m.group(3)).strip()

        # Bloc HTML de cette carte (jusqu'à la suivante ou fin)
        start = m.start()
        end   = matches[i + 1].start() if i + 1 < len(matches) else len(html)
        block = html[start:end]

        url_m = _CARD_URL_RE.search(block)
        if not url_m:
            logger.debug("Carte ignorée (URL introuvable) : titre=%s", title)
            continue

        url = url_m.group(1).rstrip("/") + "/"
        if url in seen_urls:
            continue
        seen_urls.add(url)

        img_m = _CARD_IMG_RE.search(block)
        image = img_m.group(1) if img_m else None

        slug = url.rstrip("/").rsplit("/", 1)[-1]

        events.append({
            "id":       slug,
            "artist":   title,
            "category": category,
            "status":   _parse_status_from_class(css_class),
            "url":      url,
            "image":    image,
        })

    return events


# ---------------------------------------------------------------------------
# Parsing — page détail (JSON-LD schema.org/Event)
# ---------------------------------------------------------------------------

_JSONLD_RE = re.compile(
    r'<script\s[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def _extract_event_schema(html: str) -> dict | None:
    """
    Extrait le nœud schema.org/Event depuis les blocs JSON-LD de la page.
    Gère les deux formes : objet direct {"@type":"Event"} ou @graph contenant un Event.
    """
    for m in _JSONLD_RE.finditer(html):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue

        if isinstance(data, dict):
            if data.get("@type") == "Event":
                return data
            for node in data.get("@graph", []):
                if isinstance(node, dict) and node.get("@type") == "Event":
                    return node

    return None


def _parse_iso_datetime(iso: str) -> tuple[str | None, str | None]:
    """
    Décompose une date ISO 8601 (ex: "2026-03-13T20:00:00.000000Z")
    en (date_live "YYYY-MM-DD", doors_time "HH:MM").

    Le Galaxie stocke l'heure locale en UTC (pratique WP courante) :
    pas de conversion nécessaire.
    """
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})", iso or "")
    if not m:
        return None, None
    y, mo, d, h, mi = (int(g) for g in m.groups())
    date_live  = f"{y}-{mo:02d}-{d:02d}"
    doors_time = f"{h:02d}:{mi:02d}" if (h or mi) else None
    return date_live, doors_time


def _parse_availability(url: str | None) -> str:
    """
    Convertit l'URL schema.org/availability en statut interne.
    InStock → buy_now  |  SoldOut → sold_out  |  autres → buy_now
    """
    if not url:
        return "buy_now"
    lc = url.lower()
    if "soldout" in lc or "sold_out" in lc:
        return "sold_out"
    return "buy_now"


def _enrich_from_detail(event: dict) -> None:
    """
    Récupère la page détail et complète le dictionnaire `event` en place.
    Champs enrichis : date_live, doors_time, status, buy_link, image, price.
    """
    try:
        html = _http_get(event["url"])
    except Exception as exc:
        logger.warning("Page détail inaccessible pour %s : %s", event["url"], exc)
        return

    schema = _extract_event_schema(html)
    if not schema:
        logger.debug("Pas de JSON-LD Event pour %s", event["url"])
        return

    # Date et heure
    date_live, doors_time = _parse_iso_datetime(schema.get("startDate") or "")
    if date_live:
        event["date_live"]  = date_live
    if doors_time:
        event["doors_time"] = doors_time

    # Offres (statut, buy_link, prix)
    offers = schema.get("offers") or {}
    availability = offers.get("availability", "")
    # Priorité au statut de la page de liste (plus fiable car visible),
    # sauf si la page de liste a dit buy_now mais le JSON-LD dit SoldOut.
    if _parse_availability(availability) == "sold_out":
        event["status"] = "sold_out"

    event["buy_link"] = offers.get("url") or None

    # Image (la fiche détail propose souvent une image plus grande)
    images = schema.get("image") or []
    if isinstance(images, str):
        images = [images]
    if images:
        event["image"] = images[0]


# Prix depuis la page billetterie (billetterie.le-galaxie.com)
# Deux formats Drupal/Hubber :
#   /product/   → <span class="price-amount">65 €</span>
#   /manifestation/ → <div class="manifestation-price ...">dès 44 €</div>
# L'espace avant € est un espace insécable (\xa0).
_PRICE_AMOUNT_RE    = re.compile(r'class="price-amount"[^>]*>([\d\s\xa0,\.]+)(?:€|EUR)', re.IGNORECASE)
# Pour /manifestation/ : prix dans <em class="placeholder">44 €</em> à l'intérieur du bloc
_PRICE_MANIF_RE     = re.compile(r'manifestation-price[^>]*>.*?<em[^>]*>([\d\s\xa0,\.]+)(?:€|EUR)', re.IGNORECASE | re.DOTALL)


def _fetch_billetterie_price(buy_link: str) -> str:
    """
    Récupère le prix plancher (minimum) depuis la page billetterie.le-galaxie.com.

    Gère deux types d'URL :
      - /fr/product/{id}/...       → prix par catégorie (.price-amount)
      - /fr/manifestation/{id}/... → prix "dès X €" (.manifestation-price)

    Retourne "XX.XX EUR" ou "Price Unavailable" en cas d'échec.
    """
    try:
        html = _http_get(buy_link)
    except Exception as exc:
        logger.debug("Billetterie inaccessible (%s) : %s", buy_link, exc)
        return "Price Unavailable"

    # Essayer les deux patterns selon le type de page
    raw_prices = _PRICE_AMOUNT_RE.findall(html) or _PRICE_MANIF_RE.findall(html)

    if not raw_prices:
        logger.debug("Aucun prix trouvé sur %s", buy_link)
        return "Price Unavailable"

    values: list[float] = []
    for raw in raw_prices:
        cleaned = raw.replace("\xa0", "").replace("\u00a0", "").replace(" ", "").replace(",", ".").strip()
        try:
            val = float(cleaned)
            if val > 0:
                values.append(val)
        except (ValueError, TypeError):
            continue

    if not values:
        return "Price Unavailable"

    min_price = min(values)
    logger.debug("Prix billetterie %s → à partir de %.2f EUR", buy_link, min_price)
    return f"{min_price:.2f} EUR"


# ---------------------------------------------------------------------------
# Enrichissement genres via API Deezer
# ---------------------------------------------------------------------------

_genre_cache: dict[str, list[str]] = {}


def _fetch_deezer_genres(artist_name: str) -> list[str]:
    """
    Récupère les genres musicaux d'un artiste via l'API Deezer.
    Chaîne : search/artist → top track → album → genres.
    Résultats mis en cache. Retourne ["Concerts"] en cas d'échec.
    """
    clean = re.sub(r'\s*\([A-Z]{2,3}\)\s*$', '', artist_name).strip()
    key   = clean.lower()

    if key in _genre_cache:
        return _genre_cache[key]

    fallback  = ["Concerts"]
    dz_opener = build_opener()

    def _dz_get(url: str) -> dict:
        req = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
        with dz_opener.open(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))

    try:
        d1      = _dz_get(f"https://api.deezer.com/search/artist?q={_url_quote(clean)}&limit=1")
        artists = d1.get("data", [])
        if not artists:
            _genre_cache[key] = fallback
            return fallback

        artist_id = artists[0]["id"]
        d2        = _dz_get(f"https://api.deezer.com/artist/{artist_id}/top?limit=1")
        tracks    = d2.get("data", [])
        if not tracks:
            _genre_cache[key] = fallback
            return fallback

        album_id = tracks[0].get("album", {}).get("id")
        if not album_id:
            _genre_cache[key] = fallback
            return fallback

        d3     = _dz_get(f"https://api.deezer.com/album/{album_id}")
        genres = [g["name"] for g in d3.get("genres", {}).get("data", []) if g.get("name")]
        result = genres or fallback
        _genre_cache[key] = result
        logger.debug("Genres Deezer pour '%s' : %s", clean, result)
        return result

    except Exception as exc:
        logger.debug("Genres Deezer non récupérés pour '%s' : %s", clean, exc)
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
    exclude_genres:   str | None = None,
    exclude_statuses: str | None = None,
) -> dict:
    """
    Collecte l'ensemble des événements du Galaxie Amnéville.

    Étapes :
      1. GET page 1 → déterminer le nombre total de pages
      2. Pour chaque page : GET + parse des cartes (.card-event)
      3. Pour chaque événement : GET page détail + parse JSON-LD Event
      4. Enrichissement genres via Deezer
      5. Filtres optionnels et assemblage
    """
    run_timestamp = datetime.now(timezone.utc).isoformat()

    # ── 1. Page 1 ─────────────────────────────────────────────────────────
    logger.info("Récupération page 1 : %s", EVENTS_URL)
    html_p1  = _http_get(EVENTS_URL)
    max_page = _get_max_page(html_p1)
    logger.info("%d page(s) de liste détectée(s)", max_page)

    # ── 2. Toutes les pages de liste ───────────────────────────────────────
    all_events: list[dict] = []
    seen_ids:   set[str]  = set()

    def _add_batch(batch: list[dict]) -> int:
        added = 0
        for ev in batch:
            if ev["id"] not in seen_ids:
                seen_ids.add(ev["id"])
                all_events.append(ev)
                added += 1
        return added

    added = _add_batch(_parse_cards(html_p1))
    logger.info("  Page 1 : %d événement(s)", added)

    for page_num in range(2, max_page + 1):
        url = PAGE_URL.format(n=page_num)
        logger.info("Récupération page %d/%d : %s", page_num, max_page, url)
        try:
            html  = _http_get(url)
            added = _add_batch(_parse_cards(html))
            logger.info("  Page %d : %d événement(s)", page_num, added)
        except Exception as exc:
            logger.error("Erreur page %d : %s — ignorée", page_num, exc)

    total_found = len(all_events)
    logger.info("%d événement(s) unique(s) trouvé(s) au total", total_found)

    if not all_events:
        logger.warning(
            "Aucun événement trouvé — vérifier si la structure HTML du site a changé"
        )
        return {
            "scraped_at": run_timestamp,
            "source":     EVENTS_URL,
            "total":      0,
            "concerts":   [],
        }

    # ── 3. Enrichissement depuis les pages détail ─────────────────────────
    logger.info("Récupération des %d pages détail…", total_found)
    for i, ev in enumerate(all_events, 1):
        logger.debug("[%d/%d] %s", i, total_found, ev["url"])
        _enrich_from_detail(ev)
        time.sleep(POLITE_DELAY)

    # ── 3b. Prix depuis la billetterie ────────────────────────────────────
    logger.info("Récupération des prix depuis la billetterie…")
    for i, ev in enumerate(all_events, 1):
        buy_link = ev.get("buy_link")
        if buy_link and "billetterie.le-galaxie.com" in buy_link and "/fr/" in buy_link:
            logger.debug("[%d/%d] Prix : %s", i, total_found, buy_link)
            ev["price"] = _fetch_billetterie_price(buy_link)
            time.sleep(POLITE_DELAY)
        else:
            ev["price"] = "Price Unavailable"
    logger.info("Prix récupérés.")

    # ── 4. Genres Deezer ──────────────────────────────────────────────────
    logger.info("Enrichissement des genres via Deezer pour %d artistes…", total_found)
    for ev in all_events:
        ev["genres"] = _fetch_deezer_genres(ev.get("artist") or "")
    logger.info("Genres Deezer récupérés.")

    # ── 5. Assemblage ─────────────────────────────────────────────────────
    concerts: list[dict] = []
    for ev in all_events:
        concerts.append({
            "id":           ev["id"],
            "artist":       ev.get("artist", ""),
            "date_live":    ev.get("date_live"),
            "doors_time":   ev.get("doors_time"),
            "location":     VENUE_LOCATION,
            "address":      VENUE_ADDRESS,
            "genres":       ev.get("genres", ["Concerts"]),
            "status":       ev.get("status", "buy_now"),
            "url":          ev.get("url", ""),
            "buy_link":     ev.get("buy_link"),
            "image":        ev.get("image"),
            "price":        ev.get("price", "Price Unavailable"),
            "date_created": run_timestamp,
        })

    # ── 6. Filtres ────────────────────────────────────────────────────────
    # Exclure les événements passés (date_live < aujourd'hui)
    today = datetime.now(timezone.utc).date()
    before_date = len(concerts)
    concerts = [
        c for c in concerts
        if c.get("date_live") and datetime.strptime(c["date_live"], "%Y-%m-%d").date() >= today
    ]
    if len(concerts) < before_date:
        logger.info(
            "Filtre dates passées : %d → %d (supprimés : %d)",
            before_date, len(concerts), before_date - len(concerts),
        )

    excluded_genres = _parse_exclusion_list(exclude_genres)
    if excluded_genres:
        before   = len(concerts)
        concerts = [
            c for c in concerts
            if not any(g.lower() in excluded_genres for g in (c.get("genres") or []))
        ]
        logger.info("Filtre genres %s : %d → %d", excluded_genres, before, len(concerts))

    excluded_statuses = _parse_exclusion_list(exclude_statuses)
    if excluded_statuses:
        before   = len(concerts)
        concerts = [
            c for c in concerts
            if (c.get("status") or "").lower() not in excluded_statuses
        ]
        logger.info("Filtre statuts %s : %d → %d", excluded_statuses, before, len(concerts))

    return {
        "scraped_at": run_timestamp,
        "source":     EVENTS_URL,
        "total":      len(concerts),
        "concerts":   concerts,
    }


# ---------------------------------------------------------------------------
# Écriture atomique
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
        description="Récupère les concerts et spectacles depuis le-galaxie.com (Amnéville)"
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
        help='Genres à exclure, séparés par des points-virgules (ex: "Classical Music")',
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
        "Démarrage du scraper Le Galaxie - Amnéville "
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

        logger.info("✅ %d événement(s) sauvegardé(s) → %s", data["total"], out_file)

    except (HTTPError, URLError) as exc:
        logger.error("❌ ERREUR RÉSEAU — site indisponible ou URL modifiée : %s", exc)
        logger.info("Le fichier de sortie précédent n'a pas été modifié")
        sys.exit(1)
    except (ValueError, KeyError) as exc:
        logger.error("❌ ERREUR STRUCTURE — structure du site modifiée : %s", exc)
        logger.info("Le fichier de sortie précédent n'a pas été modifié")
        sys.exit(1)
    except Exception as exc:
        logger.exception("❌ ERREUR INATTENDUE : %s", exc)
        logger.info("Le fichier de sortie précédent n'a pas été modifié")
        sys.exit(1)

    logger.info("Fin du scraper")


if __name__ == "__main__":
    main()
