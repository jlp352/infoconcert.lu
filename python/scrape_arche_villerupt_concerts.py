#!/usr/bin/env python3
"""
Scraper des concerts et spectacles disponibles sur https://l-arche.art/events
(L'Arche — Villerupt, France)

Catégories scrapées :
  - thematicAgenda=2  → Concert
  - thematicAgenda=6  → Spectacle

Le site utilise WinterCMS avec un endpoint AJAX (X-WINTER-REQUEST-HANDLER: onRefreshData)
qui retourne un JSON contenant le HTML de la liste des événements à venir.
Les deux catégories sont récupérées séparément puis fusionnées (dédoublonnage par ID).

Les fichiers sont générés dans des sous-dossiers relatifs à l'emplacement du script :
    ./JSON/scrape_arche_villerupt_concerts.json
    ./CSV/scrape_arche_villerupt_concerts.csv
    ./Log/scrape_arche_villerupt_concerts.log

Usage:
    python scrape_arche_villerupt_concerts.py                       # JSON (défaut)
    python scrape_arche_villerupt_concerts.py -f csv                # CSV
    python scrape_arche_villerupt_concerts.py -f csv -s "sold_out"  # Exclure des statuts
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
from urllib.parse import quote as _url_quote, urlencode
from urllib.request import Request, urlopen, build_opener, HTTPCookieProcessor
from http.cookiejar import CookieJar

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT_NAME = Path(__file__).stem
DIR_JSON = SCRIPT_DIR / "JSON"
DIR_CSV = SCRIPT_DIR / "CSV"
DIR_LOG = SCRIPT_DIR / "Log"

EVENTS_BASE_URL = "https://l-arche.art/events"
BASE_URL = "https://l-arche.art"

# Catégories à scraper : (thematicAgenda, label lisible)
THEMATIC_AGENDAS = [
    ("2", "Concert"),
    ("6", "Spectacle"),
]
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
MAX_RETRIES = 3
RETRY_DELAY = 5  # secondes entre chaque retry

ARCHE_LOCATION = "L'Arche - Villerupt"
ARCHE_ADDRESS = "Esplanade Nino Rota, 54190 Villerupt, France"

# Mapping mois français → numéro
FRENCH_MONTHS = {
    "janvier": 1, "février": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
}

CSV_COLUMNS = [
    "id", "artist", "date_live", "doors_time", "location",
    "address", "genres", "status", "url", "buy_link", "image",
    "price", "date_created",
]

logger = logging.getLogger("arche_villerupt_scraper")


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

def _build_opener() -> object:
    """Crée un opener urllib avec gestion des cookies (session WinterCMS)."""
    jar = CookieJar()
    return build_opener(HTTPCookieProcessor(jar))


def _get_with_session(opener, url: str, retries: int = MAX_RETRIES) -> str:
    """
    GET avec gestion des cookies et retry.
    Retourne le corps de la réponse décodé en UTF-8.
    """
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with opener.open(req, timeout=30) as resp:
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
    raise last_exc


# Lien de réservation billetterie sur les pages détail
_BUY_LINK_RE = re.compile(
    r'href="(https://billetterie\.l-arche\.art/agenda/[^"]+)"',
    re.IGNORECASE,
)


def _fetch_buy_link(opener, event_url: str) -> str | None:
    """
    Récupère le lien de réservation billetterie depuis la page détail d'un événement.
    Cherche la première URL de la forme :
        https://billetterie.l-arche.art/agenda/...
    Retourne None si absent (événement gratuit sans réservation, ou erreur).
    """
    try:
        html = _get_with_session(opener, event_url)
        m = _BUY_LINK_RE.search(html)
        return m.group(1) if m else None
    except Exception as exc:
        logger.debug("buy_link non récupéré pour %s : %s", event_url, exc)
        return None


def _post_ajax(opener, url: str, form_data: dict, retries: int = MAX_RETRIES) -> dict:
    """
    POST WinterCMS AJAX avec header X-WINTER-REQUEST-HANDLER.
    Retourne le JSON parsé.
    """
    last_exc = None
    data = urlencode(form_data).encode("utf-8")
    for attempt in range(1, retries + 1):
        try:
            req = Request(
                url,
                data=data,
                headers={
                    "User-Agent": USER_AGENT,
                    "X-WINTER-REQUEST-HANDLER": "onRefreshData",
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Accept": "application/json",
                    "Referer": url,
                },
            )
            with opener.open(req, timeout=30) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return json.loads(body)
        except (HTTPError, URLError, TimeoutError, ConnectionError) as exc:
            last_exc = exc
            if attempt < retries:
                logger.warning(
                    "POST tentative %d/%d échouée pour %s : %s — retry dans %ds",
                    attempt, retries, url, exc, RETRY_DELAY,
                )
                time.sleep(RETRY_DELAY)
            else:
                logger.error("POST échec définitif pour %s : %s", url, exc)
    raise last_exc


# ---------------------------------------------------------------------------
# Parsing du HTML d'événement
# ---------------------------------------------------------------------------

# Regex pour l'image en background-image CSS
_BG_IMG_RE = re.compile(r'background-image:\s*url\(([^)]+)\)', re.IGNORECASE)

# Date au format DD.MM.YY
_DATE_RE = re.compile(r'(\d{2})\.(\d{2})\.(\d{2})')

# Heure au format HH:MM
_TIME_RE = re.compile(r'\b(\d{2}:\d{2})\b')

# Prix : ex "15 euros", "12 €", "Prix libre", "gratuit"
_PRICE_RE = re.compile(r'(\d+(?:[,.]\d+)?)\s*(?:euros?|€)', re.IGNORECASE)
_FREE_RE = re.compile(r'\bgratuit\b', re.IGNORECASE)
_SOLD_OUT_RE = re.compile(r'complet|sold[\s-]?out', re.IGNORECASE)


def _strip_html(text: str) -> str:
    """Supprime les balises HTML et nettoie les entités."""
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', unescape(text))
    return text.strip()


def _parse_date(raw: str) -> str | None:
    """
    Parse une date au format DD.MM.YY → YYYY-MM-DD.
    Ex: '22.03.26' → '2026-03-22'
    """
    m = _DATE_RE.search(raw)
    if not m:
        return None
    day, month, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    year = 2000 + yy
    try:
        datetime(year, month, day)
        return f"{year}-{month:02d}-{day:02d}"
    except ValueError:
        return None


def _parse_price(price_text: str) -> tuple[str, str | None]:
    """
    Extrait le prix et détermine le statut depuis le texte brut.

    Retourne (price_str, status) où :
      price_str : ex '15.00 EUR', 'Free' ou 'Price Unavailable'
      status    : 'buy_now', 'free', 'sold_out' ou None
    """
    if _SOLD_OUT_RE.search(price_text):
        return "Price Unavailable", "sold_out"

    if _FREE_RE.search(price_text):
        return "Free", "free"

    m = _PRICE_RE.search(price_text)
    if m:
        val = float(m.group(1).replace(",", "."))
        if val == 0:
            return "Free", "free"
        return f"{val:.2f} EUR", "buy_now"

    return "Price Unavailable", "buy_now"


def _parse_event_card(card_html: str) -> dict | None:
    """
    Parse un bloc HTML d'une carte événement.
    Retourne un dict avec les champs bruts, ou None si parsing impossible.
    """
    # --- URL ---
    url_match = re.search(r'href="(https://l-arche\.art/event/[^"]+)"', card_html)
    if not url_match:
        return None
    url = url_match.group(1)

    # --- ID (dernier segment de l'URL) ---
    id_match = re.search(r'/(\d+)$', url)
    event_id = id_match.group(1) if id_match else url.rsplit("/", 1)[-1]

    # --- Image ---
    img_match = _BG_IMG_RE.search(card_html)
    image = img_match.group(1).strip() if img_match else None

    # --- Date et heure (dans le bloc de texte date/heure) ---
    # Structure : <span>dim.</span><br>\n22.03.26<br>\n16:00
    date_block_match = re.search(
        r'class="[^"]*z-\[2\][^"]*text-center[^"]*">(.*?)</div>',
        card_html,
        re.DOTALL,
    )
    date_str = None
    doors_time = None
    if date_block_match:
        raw_block = date_block_match.group(1)
        date_str = _parse_date(raw_block)
        time_match = _TIME_RE.search(_strip_html(raw_block))
        if time_match:
            doors_time = time_match.group(1)
    else:
        # Fallback : chercher directement dans tout le HTML
        date_str = _parse_date(card_html)

    if not date_str:
        logger.debug("Carte ignorée (date introuvable) : %s", url)
        return None

    # --- Titre (dans <h2>) ---
    h2_match = re.search(r'<h2[^>]*>(.*?)</h2>', card_html, re.DOTALL)
    title = _strip_html(h2_match.group(1)) if h2_match else ""

    # --- Catégorie ---
    cat_match = re.search(r'class="[^"]*thematic[^"]*">(.*?)</p>', card_html, re.DOTALL)
    category = _strip_html(cat_match.group(1)) if cat_match else ""

    # --- Prix et statut ---
    price_match = re.search(r'class="[^"]*prices[^"]*">(.*?)</p>', card_html, re.DOTALL)
    price_text = _strip_html(price_match.group(1)) if price_match else ""
    price_str, status = _parse_price(price_text)

    return {
        "id": event_id,
        "artist": title,
        "date_live": date_str,
        "doors_time": doors_time,
        "category": category,
        "url": url,
        "image": image,
        "price": price_str,
        "status": status,
    }


def _parse_events_html(html: str) -> list[dict]:
    """
    Parse le HTML retourné par le endpoint AJAX et retourne
    la liste des événements bruts.
    """
    # Chaque événement est une balise <a class="block event-item">
    # On découpe par les occurrences de class="block event-item"
    events = []
    pattern = re.compile(r'<a\s+href="https://l-arche\.art/event/[^"]*"\s+class="[^"]*event-item[^"]*"')
    positions = [m.start() for m in pattern.finditer(html)]

    for i, pos in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(html)
        card_html = html[pos:end]
        # Limiter au premier </a>
        close_tag = card_html.find("</a>")
        if close_tag >= 0:
            card_html = card_html[:close_tag + 4]

        event = _parse_event_card(card_html)
        if event:
            events.append(event)

    return events


# ---------------------------------------------------------------------------
# Enrichissement genres via API Deezer
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
    clean_name = re.sub(r'\s*\([A-Z]{2,3}\)\s*$', '', artist_name).strip()
    key = clean_name.lower()

    if key in _genre_cache:
        return _genre_cache[key]

    fallback = ["Concerts"]
    deezer_headers = {"Accept-Language": "en"}

    # Opener séparé pour Deezer (pas de cookies)
    dz_opener = build_opener()

    def _dz_get(url: str) -> dict:
        req = Request(url, headers={"User-Agent": USER_AGENT, **deezer_headers})
        with dz_opener.open(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))

    try:
        url1 = f"https://api.deezer.com/search/artist?q={_url_quote(clean_name)}&limit=1"
        data1 = _dz_get(url1)
        artists = data1.get("data", [])
        if not artists:
            _genre_cache[key] = fallback
            return fallback
        artist_id = artists[0]["id"]

        url2 = f"https://api.deezer.com/artist/{artist_id}/top?limit=1"
        data2 = _dz_get(url2)
        tracks = data2.get("data", [])
        if not tracks:
            _genre_cache[key] = fallback
            return fallback
        album_id = tracks[0].get("album", {}).get("id")
        if not album_id:
            _genre_cache[key] = fallback
            return fallback

        url3 = f"https://api.deezer.com/album/{album_id}"
        data3 = _dz_get(url3)
        genres = [g["name"] for g in data3.get("genres", {}).get("data", []) if g.get("name")]
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
    Récupère la liste des concerts et spectacles à venir à L'Arche Villerupt.

    Étapes :
      1. GET pour initialiser la session WinterCMS (cookie)
      2. Pour chaque catégorie (Concert, Spectacle) :
         POST AJAX → JSON avec HTML de la liste des événements
         Parsing HTML → événements bruts
      3. Fusion et dédoublonnage par ID
      4. Enrichissement genres via Deezer
    """
    run_timestamp = datetime.now(timezone.utc).isoformat()
    opener = _build_opener()

    # --- 1. GET pour récupérer le cookie de session ---
    logger.info("Initialisation de la session L'Arche Villerupt…")
    _get_with_session(opener, EVENTS_BASE_URL)

    # --- 2. Collecte par catégorie (Concert + Spectacle) ---
    seen_ids: set[str] = set()
    events_raw: list[dict] = []

    for agenda_id, agenda_label in THEMATIC_AGENDAS:
        logger.info(
            "Récupération des événements AJAX (thematicAgenda=%s = %s)…",
            agenda_id, agenda_label,
        )
        resp = _post_ajax(
            opener, EVENTS_BASE_URL,
            {"production": "", "thematicAgenda": agenda_id},
        )
        html = resp.get("#events-list", "")
        if not html:
            logger.warning(
                "La réponse AJAX ne contient pas '#events-list' pour thematicAgenda=%s — "
                "vérifier si le handler WinterCMS a changé",
                agenda_id,
            )
            continue

        batch = _parse_events_html(html)
        new_count = 0
        for ev in batch:
            if ev["id"] not in seen_ids:
                seen_ids.add(ev["id"])
                ev["category_label"] = agenda_label
                events_raw.append(ev)
                new_count += 1
        logger.info("  → %d événement(s) ajouté(s) depuis '%s'", new_count, agenda_label)

    if not events_raw:
        logger.warning(
            "Aucun événement trouvé — le site est peut-être en maintenance "
            "ou la structure HTML a changé"
        )
        return {
            "scraped_at": run_timestamp,
            "source": EVENTS_BASE_URL,
            "total": 0,
            "concerts": [],
        }

    logger.info("%d événement(s) au total (après dédoublonnage)", len(events_raw))

    # --- 4. Récupération des liens de réservation (page détail) ---
    logger.info("Récupération des liens de réservation pour %d événements…", len(events_raw))
    for ev in events_raw:
        ev["buy_link"] = _fetch_buy_link(opener, ev["url"])
        logger.debug("buy_link %s → %s", ev["url"], ev["buy_link"])
    logger.info("Liens de réservation récupérés.")

    # --- 5. Enrichissement Deezer ---
    logger.info("Récupération des genres via Deezer pour %d artistes…", len(events_raw))
    for ev in events_raw:
        ev["deezer_genres"] = _fetch_deezer_genres(ev.get("artist") or "")
    logger.info("Genres Deezer récupérés.")

    # --- Assemblage final ---
    concerts = []
    for ev in events_raw:
        concert = {
            "id": ev["id"],
            "artist": ev.get("artist") or "",
            "date_live": ev.get("date_live"),
            "doors_time": ev.get("doors_time"),
            "location": ARCHE_LOCATION,
            "address": ARCHE_ADDRESS,
            "genres": ev.get("deezer_genres") or ["Concerts"],
            "status": ev.get("status"),
            "url": ev.get("url"),
            "buy_link": ev.get("buy_link"),  # URL billetterie.l-arche.art/agenda/...
            "image": ev.get("image"),
            "price": ev.get("price", "Price Unavailable"),
            "date_created": run_timestamp,
            # champ interne non exporté — utile pour debug
            "_category": ev.get("category_label", ""),
        }
        concerts.append(concert)

    # --- Filtres ---
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
        "source": EVENTS_BASE_URL,
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
        description="Récupère les concerts et spectacles depuis l-arche.art (L'Arche Villerupt)"
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
        help='Genres à exclure, séparés par des points-virgules',
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
        "Démarrage du scraper L'Arche - Villerupt "
        "(format=%s, catégories=Concert+Spectacle, exclude_genres=%s, exclude_statuses=%s)",
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
    except (ValueError, KeyError) as exc:
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
