#!/usr/bin/env python3
"""
Scraper des concerts disponibles sur https://www.philharmonie.lu/
Scraping direct des pages HTML (pas d'API REST).
Pagination : ?eventtype=concert&favorite=false&page=N (≈16 concerts/page)

Les fichiers sont générés automatiquement dans des sous-dossiers
relatifs à l'emplacement du script :
    ./JSON/scrape_philharmonie_concerts.json
    ./CSV/scrape_philharmonie_concerts.csv
    ./Log/scrape_philharmonie_concerts.log

Usage:
    python scrape_philharmonie_concerts.py                          # JSON (défaut)
    python scrape_philharmonie_concerts.py -f csv                   # CSV
    python scrape_philharmonie_concerts.py -s "sold_out"            # Exclure des statuts
    python scrape_philharmonie_concerts.py -g "Classical"           # Exclure des genres
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
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT_NAME = Path(__file__).stem  # "scrape_philharmonie_concerts"
DIR_JSON = SCRIPT_DIR / "JSON"
DIR_CSV = SCRIPT_DIR / "CSV"
DIR_LOG = SCRIPT_DIR / "Log"

BASE_URL = "https://www.philharmonie.lu"
LISTING_URL_TMPL = (
    BASE_URL + "/en/programme?eventtype=concert&favorite=false&page={page}"
)
USER_AGENT = "PhilharmonieConcertScraper/1.0"

MAX_WORKERS = 10
MAX_RETRIES = 3
RETRY_DELAY = 5   # secondes entre chaque retry
MAX_PAGES = 50    # garde-fou anti-boucle infinie

# Adresse fixe de la Philharmonie Luxembourg
PHILHARMONIE_LOCATION = "Philharmonie"
PHILHARMONIE_ADDRESS = "Place de l'Europe, L-1499 Luxembourg"
DEFAULT_IMAGE = "https://infoconcert.lu/image/location/philharmonie.png"

# Genres disponibles sur le site (slug URL → libellé lisible).
# Vérifiés empiriquement via ?genre=[slug]&eventtype=concert.
GENRE_SLUGS: dict[str, str] = {
    "chamber":    "Chamber Music",
    "orchestral": "Orchestral",
    "world":      "World Music",
    "crossover":  "Crossover",
    "film":       "Film Music",
    "electronic": "Electronic",
}
GENRE_URL_TMPL = (
    BASE_URL + "/en/programme?eventtype=concert&favorite=false&genre={slug}&page={page}"
)

# Salles connues de la Philharmonie (pour extraction depuis la page détail)
PHILHARMONIE_HALLS = [
    "Grand Auditorium",
    "Salle de Musique de Chambre",
    "Espace Découverte",
    "Rotonde",
    "Foyer",
]

CSV_COLUMNS = [
    "id", "artist", "date_live", "doors_time", "location",
    "address", "genres", "status", "url", "buy_link", "image",
    "price", "date_created",
]

logger = logging.getLogger("philharmonie_scraper")


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

def _request(
    url: str,
    *,
    as_json: bool = False,
    retries: int = MAX_RETRIES,
    extra_headers: dict | None = None,
):
    """
    GET avec retry automatique.
    Retourne le contenu décodé (str) ou le JSON parsé selon as_json.
    Lève une exception si toutes les tentatives échouent.
    """
    last_exc = None
    headers = {"User-Agent": USER_AGENT, **(extra_headers or {})}
    for attempt in range(1, retries + 1):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
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
# Parsing de la page listing
# ---------------------------------------------------------------------------

_EVENT_HREF_RE = re.compile(r"^/(?:fr|en)/programme/\d{4}-\d{2}/.+")


class _ListingParser(HTMLParser):
    """
    Parse une page de listing Philharmonie.

    Structure cible par événement (bloc <a> incluant le titre) :
        <a href="/en/programme/2025-26/[slug]">
            ...
            <h5>[Titre du concert]</h5>
            ...
        </a>
    """

    def __init__(self):
        super().__init__()
        self._in_event_link = False
        self._in_h5 = False
        self._current_href: str | None = None
        self._current_title: str | None = None
        self.events: list[dict] = []

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)

        if tag == "a":
            href = attrs_d.get("href", "")
            if _EVENT_HREF_RE.match(href):
                self._in_event_link = True
                self._current_href = BASE_URL + href
                self._current_title = None
            return

        if self._in_event_link and tag == "h5":
            self._in_h5 = True

    def handle_endtag(self, tag):
        if tag == "h5":
            self._in_h5 = False

        if tag == "a" and self._in_event_link:
            self._in_event_link = False
            if self._current_href and self._current_title:
                # Dédupliquer par URL (certains layouts répètent les cartes)
                if not any(e["url"] == self._current_href for e in self.events):
                    self.events.append({
                        "url": self._current_href,
                        "title": self._current_title,
                    })
            self._current_href = None
            self._current_title = None

    def handle_data(self, data):
        if self._in_h5 and self._current_title is None:
            text = unescape(data.strip())
            if text:
                self._current_title = text


def _parse_listing_page(html: str) -> list[dict]:
    """Parse une page listing et retourne les événements concerts bruts."""
    parser = _ListingParser()
    parser.feed(html)
    return parser.events


# ---------------------------------------------------------------------------
# Parsing de la page détail — artistes
# ---------------------------------------------------------------------------

class _PerformersParser(HTMLParser):
    """
    Extrait les noms des artistes depuis la section "Les artistes".

    Structure cible :
        <h2>Les artistes</h2>
        <ul>
            <li><strong>Mark Steinberg</strong> violon</li>
            <li><strong>Serena Canin</strong> violon</li>
            ...
        </ul>
        <h2>[Autre section]</h2>   ← fin des artistes
    """

    def __init__(self):
        super().__init__()
        self._in_h2 = False
        self._in_artists_section = False
        self._in_li = False
        self._in_strong = False
        self.performers: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "h2":
            self._in_h2 = True
            return
        if self._in_artists_section:
            if tag == "li":
                self._in_li = True
            elif tag == "strong" and self._in_li:
                self._in_strong = True

    def handle_endtag(self, tag):
        if tag == "h2":
            self._in_h2 = False
        if tag == "li":
            self._in_li = False
            self._in_strong = False
        if tag == "strong":
            self._in_strong = False

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return

        if self._in_h2:
            if "artiste" in text.lower():
                self._in_artists_section = True
            elif self._in_artists_section:
                # Nouvelle section h2 → on arrête la collecte
                self._in_artists_section = False
            return

        if self._in_strong and self._in_artists_section and self._in_li:
            name = unescape(text)
            if name and name not in self.performers:
                self.performers.append(name)


# ---------------------------------------------------------------------------
# Parsing de la page détail — scraping complet
# ---------------------------------------------------------------------------

def _fetch_detail(url: str) -> dict:
    """
    Scrape une page de concert individuelle.

    Extrait via regex :
        - Date           : "10.03.2026"
        - Heure          : "19:30" (après abréviation jour : "mar. 19:30")
        - Lien ticket    : https://ticket.philharmonie.lu/phoenix/webticket/shop?event=XXXX
        - ID événement   : XXXX extrait de l'URL ticket
        - Prix           : "48 €", "36 €" → liste de floats
        - Tags           : /programme?tag=[Tag]
        - Image          : data-srcset dans class="full-image__image" → /media/[hash]/[file]
        - Statut         : "buy_now" / "sold_out"
        - Salle          : parmi la liste PHILHARMONIE_HALLS

    Extrait via HTMLParser :
        - Performers : <li><strong>Name</strong> role</li>
    """
    result: dict = {
        "date_str": None,
        "time_str": None,
        "hall": None,
        "performers": [],
        "prices": [],
        "buy_link": None,
        "event_id": None,
        "tags": [],
        "image": None,
        "status": "buy_now",
    }
    try:
        html = _request(url, retries=2)

        # --- Date : "10.03.2026" ---
        m_date = re.search(r"\b(\d{2}\.\d{2}\.\d{4})\b", html)
        if m_date:
            result["date_str"] = m_date.group(1)

        # --- Heure de début : "mar. 19:30", "lun. 20:00", etc. ---
        m_time = re.search(
            r"(?:lun|mar|mer|jeu|ven|sam|dim)\.\s+(\d{2}:\d{2})",
            html,
            re.IGNORECASE,
        )
        if m_time:
            result["time_str"] = m_time.group(1)
        else:
            # Fallback : première occurrence HH:MM dans la page
            m_time2 = re.search(r"\b(\d{2}:\d{2})\b", html)
            if m_time2:
                result["time_str"] = m_time2.group(1)

        # --- Lien ticket + ID événement ---
        m_buy = re.search(
            r'href=["\']('
            r'https://ticket\.philharmonie\.lu/phoenix/webticket/shop\?event=(\d+)[^"\']*'
            r')["\']',
            html,
        )
        if m_buy:
            result["buy_link"] = m_buy.group(1)
            result["event_id"] = m_buy.group(2)

        # --- Statut ---
        if result["buy_link"]:
            result["status"] = "buy_now"
        elif re.search(r"\bcomplet\b", html, re.IGNORECASE):
            result["status"] = "sold_out"
        else:
            result["status"] = "buy_now"

        # --- Prix : "Cat. I 48 €", "Cat. II 36 €", "Toutes catégories 28 €" ---
        raw_prices = re.findall(r"(\d+(?:[.,]\d+)?)\s*€", html)
        for p_str in raw_prices:
            try:
                result["prices"].append(float(p_str.replace(",", ".")))
            except ValueError:
                pass
        # Cas "Gratuit"
        if re.search(r"\bgratuit\b", html, re.IGNORECASE) and not result["prices"]:
            result["prices"].append(0.0)

        # --- Tags / catégories ---
        raw_tags = re.findall(r"/programme\?tag=([^\"&\s<>]+)", html)
        # Dédupliquer en préservant l'ordre
        seen: set[str] = set()
        for t in raw_tags:
            decoded = unescape(t)
            if decoded not in seen:
                seen.add(decoded)
                result["tags"].append(decoded)

        # --- Image : section "full-image__image" du hero ---
        # Le site Philharmonie n'expose pas de og:image.
        # L'image principale du concert est dans un <picture> à l'intérieur de
        #   class="full-image__image"
        # avec un <source data-srcset="/media/[hash]/[fichier].[ext]?...">
        hero_start = html.find("full-image__image")
        if hero_start != -1:
            hero_section = html[hero_start : hero_start + 2000]
            m_src = re.search(
                r'data-srcset="(/media/[a-z0-9]+/[^?& "\']+\.[a-zA-Z]+)',
                hero_section,
            )
            if m_src:
                result["image"] = BASE_URL + m_src.group(1) + "?width=1440&quality=80"
        if not result["image"]:
            result["image"] = DEFAULT_IMAGE

        # --- Salle ---
        for hall in PHILHARMONIE_HALLS:
            if hall in html:
                result["hall"] = hall
                break

        # --- Artistes (HTMLParser) ---
        perf_parser = _PerformersParser()
        perf_parser.feed(html)
        result["performers"] = perf_parser.performers

        return result

    except Exception as exc:
        logger.warning("Impossible de scraper la page détail %s : %s", url, exc)
        return result


# ---------------------------------------------------------------------------
# Helpers date et prix
# ---------------------------------------------------------------------------

def _parse_date(raw: str) -> str | None:
    """Convertit 'DD.MM.YYYY' → 'YYYY-MM-DD'."""
    try:
        return datetime.strptime(raw.strip(), "%d.%m.%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _parse_price(prices: list[float]) -> str:
    """Retourne le prix minimum en format 'XX.XX EUR'."""
    if not prices:
        return "Price Unavailable"
    return f"{min(prices):.2f} EUR"


# ---------------------------------------------------------------------------
# Construction de la map genre → concerts (source : filtres Philharmonie)
# ---------------------------------------------------------------------------

def _build_genre_map() -> dict[str, list[str]]:
    """
    Construit une map {concert_url → [genre_labels]} en paginant chaque
    slug de genre disponible sur le site Philharmonie.

    Un concert peut appartenir à plusieurs genres (ex : chamber + crossover).
    Les concerts sans genre resteront absents de la map → fallback ["Classical"].
    """
    genre_map: dict[str, list[str]] = {}

    for slug, label in GENRE_SLUGS.items():
        concert_count = 0
        for page in range(1, MAX_PAGES + 1):
            url = GENRE_URL_TMPL.format(slug=slug, page=page)
            try:
                html = _request(url, retries=2)
            except Exception as exc:
                logger.warning(
                    "Erreur genre '%s' page %d : %s", slug, page, exc
                )
                break

            events = _parse_listing_page(html)
            if not events:
                break

            for ev in events:
                concert_url = ev["url"]
                if concert_url not in genre_map:
                    genre_map[concert_url] = []
                if label not in genre_map[concert_url]:
                    genre_map[concert_url].append(label)
                concert_count += 1

            if len(events) < 16:
                break

        logger.debug("Genre '%s' : %d concerts taggés", label, concert_count)

    logger.info(
        "Genre map construite : %d concerts taggés (%d genres)",
        len(genre_map), len(GENRE_SLUGS),
    )
    return genre_map


# ---------------------------------------------------------------------------
# Fonction principale de collecte
# ---------------------------------------------------------------------------

def _parse_exclusion_list(raw: str | None) -> set[str]:
    """Convertit 'sold_out; buy_now' en ensemble normalisé {'sold_out', 'buy_now'}."""
    if not raw:
        return set()
    return {v.strip().lower() for v in raw.split(";") if v.strip()}


def fetch_concerts(
    exclude_genres: str | None = None,
    exclude_statuses: str | None = None,
) -> dict:
    """
    Récupère la liste complète des concerts avec toutes les métadonnées.

    Étapes :
      1. Parcours paginé du listing (?page=N) → collecte des URLs et titres
      2. Scraping parallèle des pages détail → date, heure, artistes, prix, image, tags
      3. Construction de la map genre → concerts (filtres Philharmonie)
      4. Assemblage final + filtres
    """

    run_timestamp = datetime.now(timezone.utc).isoformat()

    # --- Phase 1 : listing paginé ---
    logger.info("Phase 1 : collecte des URLs depuis le listing paginé…")
    all_events_raw: list[dict] = []

    for page in range(1, MAX_PAGES + 1):
        listing_url = LISTING_URL_TMPL.format(page=page)
        logger.debug("Fetch page %d : %s", page, listing_url)
        try:
            html = _request(listing_url)
        except Exception as exc:
            logger.error("Erreur lors du chargement de la page %d : %s", page, exc)
            break

        events = _parse_listing_page(html)

        if not events:
            logger.info("Page %d : aucun événement — fin de la pagination", page)
            break

        # Dédupliquer par URL avant d'ajouter
        new_events = [
            e for e in events
            if not any(x["url"] == e["url"] for x in all_events_raw)
        ]
        all_events_raw.extend(new_events)
        logger.info(
            "Page %d : %d nouveaux concerts (total : %d)",
            page, len(new_events), len(all_events_raw),
        )

        # Moins de 16 résultats sur la page → dernière page
        if len(events) < 16:
            logger.debug("Page %d partielle (%d événements) — fin de pagination", page, len(events))
            break

    if not all_events_raw:
        logger.warning(
            "Aucun concert trouvé — vérifier si le site est en maintenance "
            "ou si la structure HTML a changé"
        )
        return {
            "scraped_at": run_timestamp,
            "source": LISTING_URL_TMPL.format(page=1),
            "total": 0,
            "concerts": [],
        }

    logger.info("Phase 1 terminée : %d concerts collectés", len(all_events_raw))

    # --- Phase 2 : scraping des pages détail (parallélisé) ---
    logger.info("Phase 2 : scraping de %d pages détail…", len(all_events_raw))

    details: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_url = {
            executor.submit(_fetch_detail, ev["url"]): ev["url"]
            for ev in all_events_raw
        }
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            details[url] = future.result()

    logger.info("Phase 2 terminée : %d pages traitées", len(details))

    # --- Phase 3 : construction de la map genre → concerts ---
    logger.info("Phase 3 : construction de la map genres Philharmonie…")
    genre_map = _build_genre_map()
    logger.info("Phase 3 terminée.")

    # --- Assemblage final ---
    concerts = []
    for ev in all_events_raw:
        url = ev["url"]
        det = details.get(url, {})

        # Artiste : noms des performers (max 5) ou titre de l'événement
        performers = det.get("performers", [])
        artist = (
            ", ".join(performers[:5])
            if performers
            else unescape(ev.get("title") or "")
        )

        # Date
        date_live = _parse_date(det.get("date_str") or "")
        if date_live is None:
            logger.debug(
                "Date non parsable pour '%s' : '%s'",
                ev.get("title"), det.get("date_str"),
            )

        # ID : event ID Ticketmatic, sinon slug extrait de l'URL
        event_id = det.get("event_id")
        if not event_id:
            slug_m = re.search(r"/programme/[^/]+/([^/?#]+)", url)
            event_id = slug_m.group(1) if slug_m else url

        # Genres : map officielle Philharmonie, sinon ["Classical"]
        genres = genre_map.get(url) or ["Classical"]

        concert = {
            "id": event_id,
            "artist": artist,
            "date_live": date_live,
            "doors_time": det.get("time_str"),
            "location": PHILHARMONIE_LOCATION,
            "address": PHILHARMONIE_ADDRESS,
            "genres": genres,
            "status": det.get("status", "buy_now"),
            "url": url,
            "buy_link": det.get("buy_link"),
            "image": det.get("image"),
            "price": _parse_price(det.get("prices") or []),
            "date_created": run_timestamp,
        }
        concerts.append(concert)

    # --- Filtres optionnels ---
    excluded_genres = _parse_exclusion_list(exclude_genres)
    if excluded_genres:
        before = len(concerts)
        concerts = [
            c for c in concerts
            if not any(g.lower() in excluded_genres for g in (c.get("genres") or []))
        ]
        logger.info(
            "Filtre genres exclus %s : %d → %d concerts",
            excluded_genres, before, len(concerts),
        )

    excluded_statuses = _parse_exclusion_list(exclude_statuses)
    if excluded_statuses:
        before = len(concerts)
        concerts = [
            c for c in concerts
            if (c.get("status") or "").lower() not in excluded_statuses
        ]
        logger.info(
            "Filtre statuts exclus %s : %d → %d concerts",
            excluded_statuses, before, len(concerts),
        )

    return {
        "scraped_at": run_timestamp,
        "source": LISTING_URL_TMPL.format(page=1),
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
    """Convertit la liste de concerts en chaîne CSV."""
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
        description="Récupère la liste des concerts depuis philharmonie.lu"
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
        help='Genres à exclure, séparés par des points-virgules (ex: "Classical")',
    )
    parser.add_argument(
        "-s", "--exclude-statuses",
        metavar="STATUSES",
        help='Statuts à exclure, séparés par des points-virgules (ex: "sold_out")',
    )
    args = parser.parse_args()

    # --- Logging ---
    _setup_logging()
    logger.info("=" * 60)
    logger.info(
        "Démarrage du scraper Philharmonie "
        "(format=%s, exclude_genres=%s, exclude_statuses=%s)",
        args.format, args.exclude_genres, args.exclude_statuses,
    )

    try:
        data = fetch_concerts(
            exclude_genres=args.exclude_genres,
            exclude_statuses=args.exclude_statuses,
        )

        # Déterminer le fichier de sortie
        out_file = (
            DIR_CSV / f"{SCRIPT_NAME}.csv"
            if args.format == "csv"
            else DIR_JSON / f"{SCRIPT_NAME}.json"
        )

        # Écriture sécurisée
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

    logger.info("Fin du scraper Philharmonie")


if __name__ == "__main__":
    main()
