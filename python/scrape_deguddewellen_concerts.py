#!/usr/bin/env python3
"""
Scraper des concerts disponibles sur https://deguddewellen.lu/agenda
Scraping direct de la page HTML (Webflow CMS — pas d'API REST).
Filtres appliqués :
  - Catégories : Concert, Clubbing uniquement (exclu : Other)
  - Lieu : "De Gudde Wëllen" uniquement (exclu : Buvette, mikrokosmos)
  - Dates futures uniquement
  - Titres préfixés "CANCELLED" exclus

Les fichiers sont générés automatiquement dans des sous-dossiers
relatifs à l'emplacement du script :
    ./JSON/scrape_deguddewellen_concerts.json
    ./CSV/scrape_deguddewellen_concerts.csv
    ./Log/scrape_deguddewellen_concerts.log

Usage:
    python scrape_deguddewellen_concerts.py                          # JSON (défaut)
    python scrape_deguddewellen_concerts.py -f csv                   # CSV
    python scrape_deguddewellen_concerts.py -f csv -s "sold_out"     # Exclure des statuts
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote as _url_quote
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT_NAME = Path(__file__).stem
DIR_JSON = SCRIPT_DIR / "JSON"
DIR_CSV  = SCRIPT_DIR / "CSV"
DIR_LOG  = SCRIPT_DIR / "Log"

AGENDA_URL = "https://deguddewellen.lu/agenda"
BASE_URL   = "https://deguddewellen.lu"
USER_AGENT = "DGWConcertScraper/1.0"
MAX_WORKERS  = 8
MAX_RETRIES  = 3
RETRY_DELAY  = 5

DGW_ADDRESS = "17, rue du St. Esprit, L-1475 Luxembourg"

# Catégories music à conserver (case-insensitive)
_KEEP_CATEGORIES = {"concert", "clubbing"}

# Lieux à conserver (case-insensitive, correspondance exacte)
_KEEP_VENUES = {"de gudde wëllen", "de gudde wellen"}

_EN_MONTHS = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}

CSV_COLUMNS = [
    "id", "artist", "date_live", "doors_time", "location",
    "address", "genres", "status", "url", "buy_link", "image",
    "price", "date_created",
]

logger = logging.getLogger("dgw_scraper")

_genre_cache: dict[str, list[str]] = {}


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
# Helpers réseau
# ---------------------------------------------------------------------------

def _request(url: str, *, retries: int = MAX_RETRIES,
             extra_headers: dict | None = None) -> str:
    """GET avec retry automatique. Retourne le HTML décodé."""
    last_exc = None
    headers = {"User-Agent": USER_AGENT, **(extra_headers or {})}
    for attempt in range(1, retries + 1):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=30) as resp:
                raw = resp.read()
                # Détecter l'encodage depuis Content-Type ou BOM, fallback UTF-8
                ct = resp.headers.get("Content-Type", "")
                enc_m = re.search(r"charset=([^\s;]+)", ct)
                enc = enc_m.group(1) if enc_m else "utf-8"
                return raw.decode(enc, errors="replace")
        except (HTTPError, URLError, TimeoutError, ConnectionError) as exc:
            last_exc = exc
            if attempt < retries:
                logger.warning(
                    "Tentative %d/%d échouée pour %s : %s — retry dans %ds",
                    attempt, retries, url, exc, RETRY_DELAY,
                )
                time.sleep(RETRY_DELAY)
            else:
                logger.error("Échec définitif après %d tentatives pour %s : %s",
                             retries, url, exc)
    raise last_exc  # type: ignore[misc]


def _request_json(url: str, *, retries: int = MAX_RETRIES,
                  extra_headers: dict | None = None) -> dict:
    return json.loads(_request(url, retries=retries, extra_headers=extra_headers))


# ---------------------------------------------------------------------------
# Parsing de la page agenda (passe 1)
# ---------------------------------------------------------------------------

def _inner_text(html_fragment: str) -> str:
    """Extrait le texte brut d'un fragment HTML."""
    return unescape(re.sub(r"<[^>]+>", " ", html_fragment)).strip()


def _div_content(html: str, class_name: str) -> str | None:
    """
    Extrait le contenu intérieur du premier <div class="...{class_name}..."> trouvé.
    Gère les attributs class avec plusieurs valeurs.
    """
    pat = re.compile(
        r'<div[^>]+class=["\'][^"\']*' + re.escape(class_name) + r'[^"\']*["\'][^>]*>(.*?)</div>',
        re.DOTALL | re.IGNORECASE,
    )
    m = pat.search(html)
    return m.group(1) if m else None


def _parse_agenda_items(html: str) -> list[dict]:
    """
    Parse la page /agenda et retourne la liste brute des événements.

    Structure Webflow CMS (div, pas li) :
        <div data-month="03" data-year="2026" role="listitem" class="w-dyn-item">
          <div class="agenda_event-grid">
            <div class="agenda_event-date">March 28, 2026</div>
            <div class="agenda_event-venue">De Gudde Wëllen</div>
            <a href="/events/kian" class="..."><div class="agenda_event-name">KIAN</div></a>
            <div class="agenda_event-genre">Clubbing</div>
          </div>
        </div>

    Stratégie : on repère la position de chaque "agenda_event-grid" dans le HTML,
    puis on travaille sur une fenêtre de ~1 200 caractères — suffisant pour
    couvrir les 4 champs plats (date, venue, name, genre) sans risquer de
    déborder sur l'item suivant.
    """
    events: list[dict] = []

    # Regex pour localiser le début de chaque bloc agenda_event-grid
    grid_opener = re.compile(
        r'<div[^>]+class=["\'][^"\']*agenda_event-grid[^"\']*["\'][^>]*>',
        re.IGNORECASE,
    )

    for m_grid in grid_opener.finditer(html):
        window = html[m_grid.start(): m_grid.start() + 1200]

        date_raw  = _inner_text(_div_content(window, "agenda_event-date")  or "")
        venue_raw = _inner_text(_div_content(window, "agenda_event-venue") or "")
        title_raw = _inner_text(_div_content(window, "agenda_event-name")  or "")
        cat_raw   = _inner_text(_div_content(window, "agenda_event-genre") or "")

        # Lien vers la page détail
        href_m = re.search(r'href=["\'](/events/[^"\'?#]+)["\']', window)
        if not href_m:
            continue
        slug = href_m.group(1)
        url  = BASE_URL + slug

        if not date_raw or not title_raw:
            continue

        events.append({
            "date_raw":  date_raw,
            "venue":     unescape(venue_raw),
            "title":     unescape(title_raw),
            "category":  unescape(cat_raw),
            "url":       url,
            "slug":      slug.lstrip("/events/"),
        })

    return events


# ---------------------------------------------------------------------------
# Parsing de la date
# ---------------------------------------------------------------------------

def _parse_date(raw: str) -> str | None:
    """
    Convertit une date texte vers 'YYYY-MM-DD'.
    Format attendu : 'Month DD, YYYY'  (ex: 'March 28, 2026')
    """
    if not raw:
        return None
    raw_lower = raw.strip().lower()

    # "Month DD, YYYY" ou "Month DD YYYY"
    pat = r"(" + "|".join(_EN_MONTHS.keys()) + r")\s+(\d{1,2}),?\s+(\d{4})"
    m = re.search(pat, raw_lower)
    if m:
        return f"{m.group(3)}-{_EN_MONTHS[m.group(1)]}-{int(m.group(2)):02d}"

    # ISO YYYY-MM-DD (fallback)
    m2 = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    if m2:
        return m2.group(1)

    return None


def _is_future(date_str: str | None) -> bool:
    """Retourne True si date_str (YYYY-MM-DD) est aujourd'hui ou dans le futur."""
    if not date_str:
        return False
    try:
        return date.fromisoformat(date_str) >= date.today()
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Parsing de la page détail (passe 2)
# ---------------------------------------------------------------------------

def _first_match(html: str, class_name: str) -> str | None:
    """Extrait le texte du premier élément HTML portant class_name."""
    pat = re.compile(
        r'<[^>]+class=["\'][^"\']*' + re.escape(class_name) + r'[^"\']*["\'][^>]*>(.*?)</[^>]+>',
        re.DOTALL | re.IGNORECASE,
    )
    m = pat.search(html)
    return _inner_text(m.group(1)) if m else None


def _fetch_event_details(url: str) -> dict:
    """
    Scrape une page individuelle d'événement DGW.

    Extrait :
      - title         (.event-title-wrapper)
      - date          (.event-date)
      - venue         (.event-venue)
      - doors_time    (.event-door)
      - show_time     (.event-show)
      - buy_link      (href de .ticket-link ou .ticket-link-wrapper)
      - image         (src de l'img dans .event-image-wrapper)
      - genres        (texte des .tag-chip)
      - price         (texte "PRESALE:" ou "FREE" dans la page)
      - status        (sold_out / buy_now / free)
    """
    result: dict = {
        "title":      None,
        "date_raw":   None,
        "venue":      None,
        "doors_time": None,
        "show_time":  None,
        "buy_link":   None,
        "image":      None,
        "genres":     [],
        "price":      "Price Unavailable",
        "status":     None,
    }
    try:
        html = _request(url, retries=2)

        # Titre
        result["title"] = _first_match(html, "event-title-wrapper")

        # Date et lieu
        result["date_raw"] = _first_match(html, "event-date")
        result["venue"]    = _first_match(html, "event-venue")

        # Horaires
        result["doors_time"] = _first_match(html, "event-door")
        result["show_time"]  = _first_match(html, "event-show")

        # Lien billetterie (.ticket-link ou .ticket-link-wrapper)
        ticket_pat = re.compile(
            r'<a[^>]+class=["\'][^"\']*ticket-link[^"\']*["\'][^>]+href=["\']([^"\']+)["\']'
            r'|href=["\']([^"\']+)["\'][^>]+class=["\'][^"\']*ticket-link[^"\']*["\']',
            re.IGNORECASE,
        )
        tm = ticket_pat.search(html)
        if tm:
            link = tm.group(1) or tm.group(2)
            if link and link.strip() not in ("#", "", "/"):
                result["buy_link"] = link

        # Image dans .event-image-wrapper
        img_block_pat = re.compile(
            r'<div[^>]+class=["\'][^"\']*event-image-wrapper[^"\']*["\'][^>]*>(.*?)</div>',
            re.DOTALL | re.IGNORECASE,
        )
        img_m = img_block_pat.search(html)
        if img_m:
            src_m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', img_m.group(1), re.IGNORECASE)
            if src_m:
                result["image"] = unescape(src_m.group(1))

        # Tags genres (.tag-chip)
        tag_pat = re.compile(
            r'<a[^>]+class=["\'][^"\']*tag-chip[^"\']*["\'][^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )
        genres = []
        seen = set()
        for tm2 in tag_pat.finditer(html):
            g = _inner_text(tm2.group(1)).strip()
            if g and g.lower() not in seen:
                genres.append(g)
                seen.add(g.lower())
        result["genres"] = genres

        # Prix — priorité 1 : og:title de loveyourartist.com ("from €X.XX")
        if result["buy_link"] and "loveyourartist.com" in result["buy_link"]:
            try:
                lya_html = _request(result["buy_link"], retries=1)
                og_m = re.search(
                    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']'
                    r'|<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
                    lya_html, re.IGNORECASE,
                )
                if og_m:
                    og = unescape(og_m.group(1) or og_m.group(2))
                    pm = re.search(r'from\s*\D{0,3}(\d+[.,]\d+)', og)
                    if pm:
                        result["price"] = f"{pm.group(1)} EUR"
            except Exception as exc:
                logger.debug("Prix LYA non récupéré pour %s : %s", result["buy_link"], exc)

        # Prix — priorité 2 : texte de la page DGW ("PRESALE:", "FREE ENTRY", etc.)
        if result["price"] == "Price Unavailable":
            price_m = re.search(
                r"(PRESALE\s*:?\s*[\d€£$][^<]{0,80}"
                r"|PRESALE[^<]{0,80}\d[^<]{0,40}"
                r"|FREE\s+ENTRY[^<]{0,30}"
                r"|ENTRÉE\s+LIBRE[^<]{0,30}"
                r"|GRATUIT[^<]{0,30})",
                html, re.IGNORECASE,
            )
            if price_m:
                result["price"] = unescape(price_m.group(1).strip())

        # Statut
        title_upper = (result["title"] or "").upper()
        if "SOLD OUT" in title_upper:
            result["status"] = "sold_out"
        elif re.search(
            r"\bFREE\s+ENTRY\b|\bENTRÉE\s+LIBRE\b|\bGRATUIT\b|\bFREI\s+EINTRITT\b",
            html, re.IGNORECASE,
        ):
            result["status"] = "free"
        elif result["buy_link"]:
            result["status"] = "buy_now"
        else:
            result["status"] = "buy_now"

    except Exception as exc:
        logger.warning("Impossible de scraper %s : %s", url, exc)

    return result


# ---------------------------------------------------------------------------
# Enrichissement genres via Deezer
# ---------------------------------------------------------------------------

def _fetch_deezer_genres(artist_name: str) -> list[str]:
    """
    Récupère les genres musicaux d'un artiste via l'API Deezer.
    Chaîne : search/artist → top track → album → genres.
    Résultats mis en cache.
    """
    key = artist_name.strip().lower()
    if key in _genre_cache:
        return _genre_cache[key]

    fallback = ["Concerts"]
    hdrs = {"Accept-Language": "en"}
    try:
        d1 = _request_json(
            f"https://api.deezer.com/search/artist?q={_url_quote(artist_name)}&limit=1",
            extra_headers=hdrs,
        )
        artists = d1.get("data", [])
        if not artists:
            _genre_cache[key] = fallback
            return fallback

        artist_id = artists[0]["id"]
        d2 = _request_json(
            f"https://api.deezer.com/artist/{artist_id}/top?limit=1",
            extra_headers=hdrs,
        )
        tracks = d2.get("data", [])
        if not tracks:
            _genre_cache[key] = fallback
            return fallback

        album_id = tracks[0].get("album", {}).get("id")
        if not album_id:
            _genre_cache[key] = fallback
            return fallback

        d3 = _request_json(f"https://api.deezer.com/album/{album_id}", extra_headers=hdrs)
        genres = [g["name"] for g in d3.get("genres", {}).get("data", []) if g.get("name")]
        result = genres if genres else fallback
        _genre_cache[key] = result
        logger.debug("Genres Deezer pour '%s' : %s", artist_name, result)
        return result

    except Exception as exc:
        logger.debug("Genres Deezer non récupérés pour '%s' : %s", artist_name, exc)
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
    Récupère les concerts de De Gudde Wëllen.

    Étapes :
      1. GET /agenda → parse tous les <li class="w-dyn-item">
      2. Filtres : catégorie (Concert/Clubbing), lieu (DGW), date future, pas CANCELLED
      3. GET pages détail en parallèle → horaires, image, genres DGW, buy_link, prix
      4. Enrichissement genres via Deezer si aucun tag trouvé sur la page
    """
    run_ts = datetime.now(timezone.utc).isoformat()

    # --- 1. Page agenda ---
    logger.info("Récupération de l'agenda DGW…")
    html = _request(AGENDA_URL)
    all_events = _parse_agenda_items(html)
    logger.info("%d événements trouvés au total", len(all_events))

    if not all_events:
        logger.warning(
            "Aucun événement trouvé — vérifier si le site est en maintenance "
            "ou si la structure HTML a changé"
        )
        return {"scraped_at": run_ts, "source": AGENDA_URL, "total": 0, "concerts": []}

    # --- 2. Filtres ---
    filtered = []
    for ev in all_events:
        # Catégorie : Concert ou Clubbing uniquement
        if ev["category"].strip().lower() not in _KEEP_CATEGORIES:
            continue
        # Lieu : De Gudde Wëllen uniquement
        venue_norm = ev["venue"].strip().lower()
        if not any(v in venue_norm for v in _KEEP_VENUES):
            continue
        # Titre CANCELLED → ignorer
        if re.search(r"\bCANCELLED\b", ev["title"], re.IGNORECASE):
            logger.debug("Événement annulé ignoré : %s", ev["title"])
            continue
        # Date future
        date_str = _parse_date(ev["date_raw"])
        if not _is_future(date_str):
            continue
        ev["date_live"] = date_str
        filtered.append(ev)

    logger.info(
        "Filtrage : %d/%d événements conservés (Concert/Clubbing, DGW, futurs)",
        len(filtered), len(all_events),
    )

    if not filtered:
        return {"scraped_at": run_ts, "source": AGENDA_URL, "total": 0, "concerts": []}

    # --- 3. Pages détail (parallélisé) ---
    logger.info("Scraping de %d pages détail…", len(filtered))
    details_map: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_url = {
            executor.submit(_fetch_event_details, ev["url"]): ev["url"]
            for ev in filtered
        }
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            details_map[url] = future.result()

    logger.info("Pages détail récupérées.")

    # --- 4. Genres Deezer (si pas de tags sur la page) ---
    logger.info("Enrichissement genres Deezer…")
    for ev in filtered:
        det = details_map.get(ev["url"], {})
        if not det.get("genres"):
            # Nettoyer le titre pour la recherche Deezer
            artist = re.sub(
                r"\b(SOLD OUT|RELEASE PARTY|EP RELEASE|ALBUM RELEASE|SUPPORT:.*)\b.*",
                "", ev["title"], flags=re.IGNORECASE,
            ).strip(" -–—")
            det["deezer_genres"] = _fetch_deezer_genres(artist)
        else:
            det["deezer_genres"] = None  # utiliser les tags de la page

    # --- Assemblage final ---
    concerts = []
    for ev in filtered:
        url  = ev["url"]
        det  = details_map.get(url, {})

        # Titre depuis la page détail si disponible (souvent plus propre)
        title = det.get("title") or ev["title"]
        # Nettoyage préfixe "SOLD OUT - "
        title = re.sub(r"^SOLD\s*OUT\s*[-–—]\s*", "", title, flags=re.IGNORECASE).strip()

        # Genres : tags de la page détail > Deezer > fallback
        page_genres = det.get("genres") or []
        genres = page_genres if page_genres else (det.get("deezer_genres") or ["Concerts"])

        # Heure : SHOW > DOORS comme fallback
        show_time  = det.get("show_time")
        doors_time = det.get("doors_time")

        # ID : slug de l'URL
        slug_m = re.search(r"/events/([^/?#]+)", url)
        concert_id = slug_m.group(1) if slug_m else url

        concert = {
            "id":         concert_id,
            "artist":     title,
            "date_live":  ev["date_live"],
            "doors_time": doors_time,
            "location":   "De Gudde Wëllen",
            "address":    DGW_ADDRESS,
            "genres":     genres,
            "status":     det.get("status") or "buy_now",
            "url":        url,
            "buy_link":   det.get("buy_link") or url,
            "image":      det.get("image"),
            "price":      det.get("price", "Price Unavailable"),
            "date_created": run_ts,
        }
        # Ajouter show_time au date_live si disponible
        if show_time and concert["date_live"]:
            concert["date_live"] = f"{concert['date_live']} {show_time}"

        concerts.append(concert)

    # Tri chronologique
    concerts.sort(key=lambda c: c["date_live"] or "")

    # --- Filtres optionnels ---
    excluded_genres = _parse_exclusion_list(exclude_genres)
    if excluded_genres:
        before = len(concerts)
        concerts = [
            c for c in concerts
            if not any(g.lower() in excluded_genres for g in (c.get("genres") or []))
        ]
        logger.info("Filtre genres %s : %d → %d", excluded_genres, before, len(concerts))

    excluded_statuses = _parse_exclusion_list(exclude_statuses)
    if excluded_statuses:
        before = len(concerts)
        concerts = [
            c for c in concerts
            if (c.get("status") or "").lower() not in excluded_statuses
        ]
        logger.info("Filtre statuts %s : %d → %d", excluded_statuses, before, len(concerts))

    return {
        "scraped_at": run_ts,
        "source":     AGENDA_URL,
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
        buf, fieldnames=CSV_COLUMNS, extrasaction="ignore", quoting=csv.QUOTE_MINIMAL,
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
        description="Récupère les concerts depuis deguddewellen.lu"
    )
    parser.add_argument("-f", "--format", choices=["json", "csv"], default="json",
                        help="Format de sortie : json (défaut) ou csv")
    parser.add_argument("-g", "--exclude-genres", metavar="GENRES",
                        help='Genres à exclure, séparés par ";"')
    parser.add_argument("-s", "--exclude-statuses", metavar="STATUSES",
                        help='Statuts à exclure, séparés par ";"')
    args = parser.parse_args()

    _setup_logging()
    logger.info("=" * 60)
    logger.info(
        "Démarrage du scraper DGW (format=%s, exclude_genres=%s, exclude_statuses=%s)",
        args.format, args.exclude_genres, args.exclude_statuses,
    )

    try:
        data = fetch_concerts(
            exclude_genres=args.exclude_genres,
            exclude_statuses=args.exclude_statuses,
        )

        out_file = (DIR_CSV / f"{SCRIPT_NAME}.csv") if args.format == "csv" \
                   else (DIR_JSON / f"{SCRIPT_NAME}.json")

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

    logger.info("Fin du scraper DGW")


if __name__ == "__main__":
    main()
