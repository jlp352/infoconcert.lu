#!/usr/bin/env python3
"""
Scraper des concerts disponibles sur https://casino2000.lu/
Scraping direct de la page HTML (pas d'API REST).

Les fichiers sont générés automatiquement dans des sous-dossiers
relatifs à l'emplacement du script :
    ./JSON/scrape_casino2000_concerts.json
    ./CSV/scrape_casino2000_concerts.csv
    ./Log/scrape_casino2000_concerts.log

Usage:
    python scrape_casino2000_concerts.py                          # JSON (défaut)
    python scrape_casino2000_concerts.py -f csv                   # CSV
    python scrape_casino2000_concerts.py -f csv -s "sold_out"     # Exclure des statuts
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
from urllib.parse import quote as _url_quote
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# Répertoire racine = dossier contenant le script
SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT_NAME = Path(__file__).stem  # "scrape_casino2000_concerts"
DIR_JSON = SCRIPT_DIR / "JSON"
DIR_CSV = SCRIPT_DIR / "CSV"
DIR_LOG = SCRIPT_DIR / "Log"

AGENDA_URL = "https://casino2000.lu/fr/agenda-du-casino-2000/?type=concerts"
BASE_URL = "https://casino2000.lu"
USER_AGENT = "Casino2000ConcertScraper/1.0"
MAX_WORKERS = 10
MAX_RETRIES = 3
RETRY_DELAY = 5  # secondes entre chaque retry

# Adresse fixe du Casino 2000 (Mondorf-les-Bains)
CASINO2000_ADDRESS = "Route de Mondorf, L-5618 Mondorf-les-Bains"

CSV_COLUMNS = [
    "id", "artist", "date_live", "doors_time", "location",
    "address", "genres", "status", "url", "buy_link", "image",
    "price", "date_created",
]

logger = logging.getLogger("casino2000_scraper")


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

def _request(url: str, *, as_json: bool = False, retries: int = MAX_RETRIES,
             extra_headers: dict | None = None):
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
# Parsing de la page liste des événements
# ---------------------------------------------------------------------------

class _EventListParser(HTMLParser):
    """
    Parse la page liste de l'agenda Casino 2000 (?type=concerts).

    Structure HTML des cards :
        <article class="event-item visible all concerts">
          <h5 class="event-title">
            <a aria-label="[Titre]" href="https://casino2000.lu/fr/events/[slug]/">[Titre]</a>
          </h5>
          <div class="taxo-style">Concerts</div>
          <div class="date-event-next">DD.MM.YYYY</div>
          <img class="lazyload" data-src="[image-url]">
          <a class="btn [listing-agenda]" href="[booking-url]">Réserver</a>
        </article>

    Pour les événements gratuits :
        <a class="btn" href="[event-url]">Gratuit</a>
    """

    def __init__(self):
        super().__init__()
        self._in_article = False
        self._in_h5 = False
        self._in_title_a = False
        self._in_date = False
        self._in_btn = False
        self._current: dict = {}
        self.events: list[dict] = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")

        # Détecter <article class="event-item ... concerts">
        if tag == "article" and "event-item" in cls and "concerts" in cls:
            self._in_article = True
            self._current = {
                "title": None,
                "url": None,
                "date_str": None,
                "image": None,
                "buy_link": None,
                "status": None,
            }
            return

        if not self._in_article:
            return

        # Titre : <h5 class="event-title">
        if tag == "h5" and "event-title" in cls:
            self._in_h5 = True
            return

        # Lien du titre (à l'intérieur du h5)
        if tag == "a" and self._in_h5:
            href = attrs_dict.get("href", "")
            if href:
                self._current["url"] = href if href.startswith("http") else BASE_URL + href
            self._in_title_a = True
            return

        # Date : <div class="date-event-next">
        if tag == "div" and "date-event-next" in cls:
            self._in_date = True
            return

        # Image lazy-load
        if tag == "img":
            data_src = (
                attrs_dict.get("data-src")
                or attrs_dict.get("data-lazy-src")
                or attrs_dict.get("data-original")
            )
            if data_src and not data_src.startswith("data:"):
                self._current["image"] = data_src
            else:
                src = attrs_dict.get("src", "")
                if src and not src.startswith("data:"):
                    self._current["image"] = src
            return

        # Bouton réservation / gratuit (hors h5, avec class "btn")
        if tag == "a" and not self._in_h5 and "btn" in cls.split():
            href = attrs_dict.get("href", "")
            self._current["buy_link"] = href if href else None
            self._in_btn = True

    def handle_endtag(self, tag):
        if tag == "article" and self._in_article:
            self._in_article = False
            self._in_h5 = False
            self._in_title_a = False
            self._in_date = False
            self._in_btn = False
            if self._current.get("title") and self._current.get("url"):
                self.events.append(self._current)
            self._current = {}
            return

        if tag == "h5":
            self._in_h5 = False
        if tag == "a":
            self._in_title_a = False
            self._in_btn = False
        if tag == "div":
            self._in_date = False

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return

        # Titre du concert
        if self._in_title_a and self._current.get("title") is None:
            self._current["title"] = text

        # Date (format DD.MM.YYYY, prendre la première date si plage)
        if self._in_date and self._current.get("date_str") is None:
            m = re.match(r"(\d{2}\.\d{2}\.\d{4})", text)
            if m:
                self._current["date_str"] = m.group(1)

        # Statut depuis le texte du bouton
        # Valeurs alignées sur le contrat du frontend (normalizeStatus dans index.html) :
        #   "buy_now"  → available → "Billets Disponibles"
        #   "sold_out" → full      → "Complet"
        if self._in_btn and self._current.get("status") is None:
            text_lower = text.lower()
            if "réserver" in text_lower or "reserver" in text_lower or "book" in text_lower:
                self._current["status"] = "buy_now"
            elif "gratuit" in text_lower or "free" in text_lower:
                self._current["status"] = "buy_now"   # entrée libre = places disponibles
            elif "complet" in text_lower or "sold out" in text_lower:
                self._current["status"] = "sold_out"
            elif text_lower:
                self._current["status"] = text_lower


def _parse_event_list(html: str) -> list[dict]:
    """Parse la page liste et retourne les événements concerts bruts."""
    parser = _EventListParser()
    parser.feed(html)
    return parser.events


# ---------------------------------------------------------------------------
# Enrichissement des genres via l'API Deezer
# ---------------------------------------------------------------------------

# Cache artiste → genres (insensible à la casse)
_genre_cache: dict[str, list[str]] = {}


def _fetch_deezer_genres(artist_name: str) -> list[str]:
    """
    Récupère les genres musicaux d'un artiste via l'API Deezer.

    Chaîne d'appels (le genre n'est pas fourni directement par /search/artist) :
      1. GET /search/artist?q=...        → artist_id
      2. GET /artist/{id}/top?limit=1    → album_id du premier titre
      3. GET /album/{album_id}           → genres.data[].name

    Retourne ["Concerts"] en cas d'échec ou d'absence de données.
    Résultats mis en cache par nom d'artiste.
    """
    key = artist_name.strip().lower()
    if key in _genre_cache:
        return _genre_cache[key]

    fallback = ["Concerts"]
    deezer_headers = {"Accept-Language": "en"}
    try:
        # Étape 1 : recherche de l'artiste
        url1 = f"https://api.deezer.com/search/artist?q={_url_quote(artist_name)}&limit=1"
        data1 = _request(url1, as_json=True, extra_headers=deezer_headers)
        artists = data1.get("data", [])
        if not artists:
            _genre_cache[key] = fallback
            return fallback
        artist_id = artists[0]["id"]

        # Étape 2 : top tracks → récupérer un album_id
        url2 = f"https://api.deezer.com/artist/{artist_id}/top?limit=1"
        data2 = _request(url2, as_json=True, extra_headers=deezer_headers)
        tracks = data2.get("data", [])
        if not tracks:
            _genre_cache[key] = fallback
            return fallback
        album_id = tracks[0].get("album", {}).get("id")
        if not album_id:
            _genre_cache[key] = fallback
            return fallback

        # Étape 3 : genres de l'album (Accept-Language: en pour noms en anglais)
        url3 = f"https://api.deezer.com/album/{album_id}"
        data3 = _request(url3, as_json=True, extra_headers=deezer_headers)
        genres = [
            g["name"]
            for g in data3.get("genres", {}).get("data", [])
            if g.get("name")
        ]

        result = genres if genres else fallback
        _genre_cache[key] = result
        logger.debug("Genres Deezer pour '%s' : %s", artist_name, result)
        return result

    except Exception as exc:
        logger.debug("Genres Deezer non récupérés pour '%s' : %s", artist_name, exc)
        _genre_cache[key] = fallback
        return fallback


# ---------------------------------------------------------------------------
# Parsing de la date
# ---------------------------------------------------------------------------

def _parse_date(raw: str) -> str | None:
    """Convertit 'DD.MM.YYYY' → 'YYYY-MM-DD'."""
    raw = raw.strip()
    for fmt in ("%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Récupération du prix via Ticketmatic Casino 2000
# ---------------------------------------------------------------------------

def _fetch_casino2000_price(buy_link: str) -> str:
    """
    Récupère le prix minimum depuis le widget Ticketmatic Casino 2000.
    URLs attendues :
      - apps.ticketmatic.com/widgets/casino2000/flow/new?event=...
      - apps.ticketmatic.com/widgets/casino2000/flow/tickets?event=...

    Casino 2000 n'expose pas de route /flow/web ; on utilise le endpoint
    /addtickets directement en extrayant l'event ID.
    """
    try:
        # Extraire l'identifiant d'événement depuis le paramètre event=
        m_id = re.search(r"[?&]event=(\d+)", buy_link)
        if not m_id:
            return "Price Unavailable"
        event_id = m_id.group(1)

        # Extraire le nom du compte Ticketmatic (ex : "casino2000")
        m_acc = re.search(r"apps\.ticketmatic\.com/widgets/([^/]+)/", buy_link)
        account = m_acc.group(1) if m_acc else "casino2000"

        url = f"https://apps.ticketmatic.com/widgets/{account}/addtickets?event={event_id}&l=en"
        html = _request(url, retries=2)
        m = re.search(r'constant\("TM",\s*(\{.+?\})\s*\);', html, re.DOTALL)
        if not m:
            return "Price Unavailable"
        tm = json.loads(m.group(1))
        events = tm.get("configs", {}).get("addtickets", {}).get("events", [])
        prices = []
        for ev in events:
            for cont in ev.get("prices", {}).get("contingents", []):
                for pt in cont.get("pricetypes", []):
                    p = pt.get("price")
                    if p is None or p <= 0:
                        continue
                    # Ignorer les tarifs conditionnels (ex : Kulturpass)
                    has_conditions = any(
                        sc.get("conditions") for sc in pt.get("saleschannels", [])
                    )
                    if not has_conditions:
                        prices.append(p)
        if not prices:
            return "Price Unavailable"
        return f"{min(prices):.2f} EUR"
    except Exception as exc:
        logger.debug("Prix Casino 2000 non récupéré pour %s : %s", buy_link, exc)
        return "Price Unavailable"


# ---------------------------------------------------------------------------
# Récupération des détails par page de concert
# ---------------------------------------------------------------------------

def _fetch_show_details(url: str, buy_link: str | None = None) -> dict:
    """Scrape une page de concert individuelle pour récupérer l'horaire, l'image et le prix."""
    result: dict = {"doors_time": None, "image": None, "price": "Price Unavailable"}
    try:
        html = _request(url, retries=2)

        # Image : balise Open Graph (fiable sur les sites WordPress)
        m_img = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html)
        if not m_img:
            # Ordre alternatif des attributs
            m_img = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html)
        if m_img:
            result["image"] = m_img.group(1)

        # Heure d'ouverture des portes
        # Patterns attendus :
        #   "Ouverture des portes à 19h00."
        #   "Ouverture des portes : 19h00"
        #   "Doors: 19:00"
        m = re.search(
            r"[Oo]uverture des portes[^0-9]*(\d{1,2}[h:]\d{2})",
            html,
        )
        if m:
            result["doors_time"] = m.group(1).strip()
        else:
            m2 = re.search(r"[Dd]oors[^0-9]*(\d{1,2}[h:]\d{2})", html)
            if m2:
                result["doors_time"] = m2.group(1).strip()

        if result["doors_time"] is None:
            logger.debug("Heure d'ouverture non trouvée pour : %s", url)

        # Prix via le widget Ticketmatic Casino 2000
        if buy_link and "ticketmatic" in buy_link:
            result["price"] = _fetch_casino2000_price(buy_link)
        # TheMis ne permet pas l'extraction automatique du prix
        # → "Price Unavailable" (déjà valeur par défaut)

        return result
    except Exception as exc:
        logger.warning("Impossible de scraper %s : %s", url, exc)
        return result


# ---------------------------------------------------------------------------
# Fonction principale de collecte
# ---------------------------------------------------------------------------

def _parse_exclusion_list(raw: str | None) -> set[str]:
    """Convertit une chaîne 'sold_out; free' en ensemble normalisé {'sold_out', 'free'}."""
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
      1. Scraping de la page agenda filtrée (?type=concerts) → liste des concerts
      2. Scraping des pages individuelles → heure d'ouverture + prix (parallélisé)
      3. Enrichissement des genres via l'API Deezer (séquentiel, avec cache)
    """

    run_timestamp = datetime.now(timezone.utc).isoformat()

    # --- 1. Page liste ---
    logger.info("Récupération de la liste des concerts Casino 2000…")
    html = _request(AGENDA_URL)
    events_raw = _parse_event_list(html)

    if not events_raw:
        logger.warning(
            "Aucun concert trouvé — vérifier si le site est en maintenance "
            "ou si la structure HTML a changé"
        )
        return {
            "scraped_at": run_timestamp,
            "source": AGENDA_URL,
            "total": 0,
            "concerts": [],
        }

    logger.info("%d concerts trouvés sur la page liste", len(events_raw))

    # --- 2. Scraping des pages individuelles (parallélisé) ---
    logger.info("Scraping de %d pages pour horaires et prix…", len(events_raw))

    show_details: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_url = {
            executor.submit(_fetch_show_details, ev["url"], ev.get("buy_link")): ev["url"]
            for ev in events_raw
            if ev.get("url")
        }
        done_count = 0
        fail_count = 0
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            result = future.result()
            show_details[url] = result
            done_count += 1
            if result["doors_time"] is None:
                fail_count += 1

    if fail_count:
        logger.warning(
            "Horaire manquant pour %d/%d concerts",
            fail_count, done_count,
        )
    logger.info("Scraping terminé : %d/%d pages OK", done_count - fail_count, done_count)

    # --- 3. Enrichissement des genres via Deezer (séquentiel + cache) ---
    logger.info("Récupération des genres musicaux via Deezer pour %d artistes…", len(events_raw))
    for ev in events_raw:
        ev["deezer_genres"] = _fetch_deezer_genres(ev.get("title") or "")
    logger.info("Genres Deezer récupérés.")

    # --- Assemblage final ---
    concerts = []
    for ev in events_raw:
        url = ev.get("url", "")
        details = show_details.get(url, {})

        # Identifiant = slug extrait de l'URL de l'événement
        slug_match = re.search(r"/events/([^/]+)/?$", url)
        concert_id = slug_match.group(1) if slug_match else url

        date_live = _parse_date(ev.get("date_str") or "")
        if date_live is None:
            logger.debug(
                "Date non parsable pour '%s' : '%s'",
                ev.get("title"), ev.get("date_str"),
            )

        concert = {
            "id": concert_id,
            "artist": unescape(ev.get("title") or ""),
            "date_live": date_live,
            "doors_time": details.get("doors_time"),
            "location": "Casino 2000",
            "address": CASINO2000_ADDRESS,
            "genres": ev.get("deezer_genres") or ["Concerts"],
            "status": ev.get("status"),
            "url": url,
            "buy_link": ev.get("buy_link"),
            "image": details.get("image") or ev.get("image"),
            "price": details.get("price", "Price Unavailable"),
            "date_created": run_timestamp,
        }
        concerts.append(concert)

    # Filtre genres : les genres sont maintenant enrichis via Deezer.
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
        "source": AGENDA_URL,
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
        description="Récupère la liste des concerts depuis casino2000.lu"
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

    # --- Logging ---
    _setup_logging()
    logger.info("=" * 60)
    logger.info(
        "Démarrage du scraper (format=%s, exclude_genres=%s, exclude_statuses=%s)",
        args.format, args.exclude_genres, args.exclude_statuses,
    )

    try:
        data = fetch_concerts(
            exclude_genres=args.exclude_genres,
            exclude_statuses=args.exclude_statuses,
        )

        # --- Déterminer le fichier de sortie ---
        if args.format == "csv":
            out_file = DIR_CSV / f"{SCRIPT_NAME}.csv"
        else:
            out_file = DIR_JSON / f"{SCRIPT_NAME}.json"

        # --- Écriture sécurisée du résultat ---
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
