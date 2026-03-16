#!/usr/bin/env python3
"""
Scraper des concerts disponibles sur https://legueulard.fr/web/
Scraping via l'API WordPress REST (JSON) — catégorie "concert" (ID 2).

Le champ `date` WordPress correspond à la date/heure de l'événement.
Les images sont récupérées via l'option _embed de l'API.

Les fichiers sont générés automatiquement dans des sous-dossiers
relatifs à l'emplacement du script :
    ./JSON/scrape_gueulard_nilvange_concerts.json
    ./CSV/scrape_gueulard_nilvange_concerts.csv
    ./Log/scrape_gueulard_nilvange_concerts.log

Usage:
    python scrape_gueulard_nilvange_concerts.py                          # JSON (défaut)
    python scrape_gueulard_nilvange_concerts.py -f csv                   # CSV
    python scrape_gueulard_nilvange_concerts.py -f csv -s "sold_out"     # Exclure des statuts
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
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT_NAME = Path(__file__).stem
DIR_JSON = SCRIPT_DIR / "JSON"
DIR_CSV = SCRIPT_DIR / "CSV"
DIR_LOG = SCRIPT_DIR / "Log"

BASE_URL = "https://legueulard.fr/web"
API_BASE = f"{BASE_URL}/wp-json/wp/v2"
# Catégorie "concert" sur legueulard.fr (vérifiable via /wp-json/wp/v2/categories)
CONCERT_CATEGORY_ID = 2

USER_AGENT = "GueulardNilvangeConcertScraper/1.0"
MAX_RETRIES = 3
RETRY_DELAY = 5  # secondes entre chaque retry

GUEULARD_LOCATION = "Le Gueulard - Nilvange"
GUEULARD_ADDRESS = "14 rue Clémenceau, 57240 Nilvange, France"

CSV_COLUMNS = [
    "id", "artist", "date_live", "doors_time", "location",
    "address", "genres", "status", "url", "buy_link", "image",
    "price", "date_created",
]

logger = logging.getLogger("gueulard_scraper")


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
    """
    GET avec retry automatique.
    Retourne (body_str, response_headers).
    """
    last_exc = None
    headers = {"User-Agent": USER_AGENT, **(extra_headers or {})}
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
# Récupération des posts via l'API WordPress REST
# ---------------------------------------------------------------------------

def _fetch_all_concert_posts() -> list[dict]:
    """
    Récupère tous les posts de la catégorie concert via l'API WordPress REST.
    Gère la pagination via le header X-WP-TotalPages.
    Utilise _embed=1 pour obtenir les images en une seule requête.
    """
    posts = []
    page = 1

    while True:
        url = (
            f"{API_BASE}/posts"
            f"?categories={CONCERT_CATEGORY_ID}"
            f"&per_page=100"
            f"&page={page}"
            f"&orderby=date&order=asc"
            f"&_embed=1"
        )
        logger.debug("Fetching page %d : %s", page, url)

        try:
            data, headers = _request_json(url)
        except HTTPError as exc:
            if exc.code == 400 and page > 1:
                # Page hors limites → fin de pagination
                break
            raise

        if not isinstance(data, list) or not data:
            break

        posts.extend(data)
        logger.debug("Page %d : %d posts (total cumulé : %d)", page, len(data), len(posts))

        total_pages_str = headers.get("x-wp-totalpages")
        if total_pages_str:
            if page >= int(total_pages_str):
                break
        else:
            # Pas de header → on suppose une seule page
            break

        page += 1

    return posts


# ---------------------------------------------------------------------------
# Parsing des métadonnées de chaque post
# ---------------------------------------------------------------------------

def _strip_html(text: str) -> str:
    """Supprime les balises HTML et normalise les espaces."""
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', unescape(text))
    return text.strip()


# Span de prix après l'icône icon-money dans la page HTML du concert
# Structure : <i class="icon-money"></i>\n...<span>TARIF PLEIN : 10€ | RÉDUIT : 5€</span>
_MONEY_SPAN_RE = re.compile(
    r'icon-money[^>]*>.*?<span[^>]*>(.*?)</span>',
    re.DOTALL | re.IGNORECASE,
)
# Prix numérique : "10€", "12,50 €", "10 EUR"
_PRICE_RE = re.compile(r'(\d+(?:[.,]\d+)?)\s*(?:€|EUR)', re.IGNORECASE)
# Événement gratuit
_FREE_RE = re.compile(
    r'(entr[ée]e?\s+libre|gratuit|accès\s+libre|free\s+entry)',
    re.IGNORECASE,
)
# Événement complet — \b évite de matcher "complete" dans le JavaScript
_SOLD_OUT_RE = re.compile(r'(\bcomplet\b|sold[\s-]out|guichet\s+ferm)', re.IGNORECASE)
# Plateformes de billetterie (France + international)
_TICKET_URL_RE = re.compile(
    r'https?://(?:www\.)?(?:'
    r'helloasso\.com|billetweb\.fr|weezevent\.com|'
    r'shotgun\.live|digitick\.com|fnacspectacles\.com|'
    r'ticketmaster\.fr|yurplan\.com|madate\.app'
    r')[^\s"\'<>]+',
    re.IGNORECASE,
)


def _fetch_concert_details(url: str) -> tuple[str, str | None, str | None]:
    """
    Récupère la page HTML du concert et extrait (price_str, status, buy_link)
    depuis le bloc <div itemprop="summary">.

    Le prix est dans <i class="icon-money"></i><span>TARIF PLEIN : 10€ | RÉDUIT : 5€</span>
    et n'est pas disponible via l'API WordPress REST.

    Retourne ("Price Unavailable", None, None) en cas d'échec ou d'absence de données.
    """
    try:
        html, _ = _request(url)
    except Exception as exc:
        logger.debug("Impossible de récupérer la page %s : %s", url, exc)
        return "Price Unavailable", None, None

    # Lien billetterie (cherché sur l'ensemble de la page)
    m_ticket = _TICKET_URL_RE.search(html)
    buy_link = m_ticket.group(0) if m_ticket else None

    # Texte du span de prix (après icon-money) — cherché sur la page entière
    # Note : ne pas extraire le <div itemprop="summary"> car il contient des
    # divs imbriqués qui trompent un regex non-greedy.
    m_span = _MONEY_SPAN_RE.search(html)
    span_text = _strip_html(m_span.group(1)) if m_span else ""
    logger.debug("Span prix pour %s : %r", url, span_text)

    if _SOLD_OUT_RE.search(span_text or html):
        return "Price Unavailable", "sold_out", buy_link

    if _FREE_RE.search(span_text or html):
        return "Free", "free", buy_link

    # Prix dans le span icon-money (ex : "TARIF PLEIN : 10€ | RÉDUIT : 5€")
    m_price = _PRICE_RE.search(span_text) if span_text else None
    if m_price:
        raw = m_price.group(1).replace(",", ".")
        amount = float(raw)
        if amount == 0.0:
            return "Free", "free", buy_link
        return f"{amount:.2f} EUR", "buy_now", buy_link

    # Fallback : prix dans le corps du post HTML (ex : "30 €" dans la description)
    # Cherche uniquement dans les noeuds texte (entre > et <) pour éviter le JS
    for m in re.finditer(r'>([^<]{0,80})<', html):
        node_text = m.group(1)
        m_p = _PRICE_RE.search(node_text)
        if m_p:
            raw = m_p.group(1).replace(",", ".")
            amount = float(raw)
            if amount > 0:
                logger.debug("Prix trouvé dans le corps pour %s : %s €", url, raw)
                return f"{amount:.2f} EUR", "buy_now", buy_link

    # Indication de réservation sans prix explicite → buy_now sans montant
    if span_text and re.search(r'r[ée]servation', span_text, re.IGNORECASE):
        return "Price Unavailable", "buy_now", buy_link

    return "Price Unavailable", None, buy_link


def _parse_post(post: dict) -> dict | None:
    """
    Convertit un post WordPress brut en dict concert normalisé.
    Retourne None si les données essentielles sont manquantes.
    Le prix est récupéré via une requête HTML supplémentaire sur la page du concert.
    """
    # --- Date/heure de l'événement ---
    date_raw = post.get("date")  # ex: "2026-05-22T20:30:00"
    if not date_raw:
        return None

    try:
        dt = datetime.fromisoformat(date_raw)
    except ValueError:
        logger.debug("Date invalide pour post id=%s : %s", post.get("id"), date_raw)
        return None

    date_live = dt.strftime("%Y-%m-%d")
    # Heure à 00:00 → probablement non renseignée → ne pas l'exporter
    doors_time = dt.strftime("%H:%M") if (dt.hour, dt.minute) != (0, 0) else None

    # --- Artiste / titre ---
    artist = _strip_html(post.get("title", {}).get("rendered", ""))

    # --- URL ---
    url = post.get("link", "")

    # --- Image (via _embedded) ---
    image = None
    embedded = post.get("_embedded", {})
    media_list = embedded.get("wp:featuredmedia", [])
    if media_list and isinstance(media_list, list):
        first_media = media_list[0]
        if isinstance(first_media, dict):
            image = first_media.get("source_url")

    return {
        "id": str(post.get("id", "")),
        "artist": artist,
        "date_str": date_live,
        "doors_time": doors_time,
        "image": image,
        "url": url,
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
    clean_name = re.sub(r'\s*\([A-Z]{2,3}\)\s*$', '', artist_name).strip()
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
    Récupère la liste complète des concerts à venir avec toutes les métadonnées.

    Étapes :
      1. Appels API WordPress REST → liste des posts catégorie "concert"
      2. Filtrage : suppression des événements passés
      3. Enrichissement des genres via l'API Deezer (séquentiel, avec cache)
    """
    run_timestamp = datetime.now(timezone.utc).isoformat()
    today = datetime.now().strftime("%Y-%m-%d")

    # --- 1. Récupération des posts ---
    logger.info("Récupération des concerts via l'API WordPress de Le Gueulard…")
    raw_posts = _fetch_all_concert_posts()
    logger.info("%d posts récupérés depuis l'API", len(raw_posts))

    # --- 2. Parsing + filtre événements passés ---
    events_raw = []
    for post in raw_posts:
        ev = _parse_post(post)
        if ev is None:
            continue
        if ev["date_str"] < today:
            logger.debug("Événement passé ignoré : %s (%s)", ev["artist"], ev["date_str"])
            continue
        events_raw.append(ev)

    logger.info("%d concerts à venir après filtrage des événements passés", len(events_raw))

    # --- 3. Récupération du prix via la page HTML de chaque concert ---
    # Le prix (TARIF PLEIN) est dans un custom field WordPress non exposé par l'API REST.
    # On ne fetch que les concerts à venir, pas les 500+ posts historiques.
    logger.info("Récupération des prix depuis les pages HTML (%d concerts)…", len(events_raw))
    for ev in events_raw:
        price, status, buy_link = _fetch_concert_details(ev["url"])
        ev["price"] = price
        ev["status"] = status
        ev["buy_link"] = buy_link or ev["url"]
    logger.info("Prix récupérés.")

    if not events_raw:
        logger.warning(
            "Aucun concert à venir trouvé — vérifier la catégorie (ID=%d) "
            "ou la structure de l'API", CONCERT_CATEGORY_ID
        )
        return {
            "scraped_at": run_timestamp,
            "source": BASE_URL,
            "total": 0,
            "concerts": [],
        }

    # --- 4. Enrichissement genres via Deezer ---
    logger.info("Récupération des genres Deezer pour %d artistes…", len(events_raw))
    for ev in events_raw:
        ev["deezer_genres"] = _fetch_deezer_genres(ev.get("artist") or "")
    logger.info("Genres Deezer récupérés.")

    # --- 5. Assemblage final ---
    concerts = []
    for ev in events_raw:
        concerts.append({
            "id": ev["id"],
            "artist": ev.get("artist") or "",
            "date_live": ev.get("date_str"),
            "doors_time": ev.get("doors_time"),
            "location": GUEULARD_LOCATION,
            "address": GUEULARD_ADDRESS,
            "genres": ev.get("deezer_genres") or ["Concerts"],
            "status": ev.get("status"),
            "url": ev.get("url"),
            "buy_link": ev.get("buy_link"),
            "image": ev.get("image"),
            "price": ev.get("price", "Price Unavailable"),
            "date_created": run_timestamp,
        })

    # --- 6. Filtres optionnels ---
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
        "source": BASE_URL,
        "total": len(concerts),
        "concerts": concerts,
    }


# ---------------------------------------------------------------------------
# Écriture sécurisée (atomic write)
# ---------------------------------------------------------------------------

def _safe_write(target: Path, content: str) -> None:
    """
    Écrit dans un fichier temporaire puis renomme vers la cible.
    Protège le fichier précédent en cas de crash pendant l'écriture.
    """
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
        description="Récupère la liste des concerts depuis legueulard.fr (Le Gueulard, Nilvange)"
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
        "Démarrage du scraper Le Gueulard (format=%s, exclude_genres=%s, exclude_statuses=%s)",
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

    logger.info("Fin du scraper")


if __name__ == "__main__":
    main()
