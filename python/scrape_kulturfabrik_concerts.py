#!/usr/bin/env python3
"""
Scraper des concerts disponibles sur https://www.kulturfabrik.lu/
Scraping direct de la page HTML (pas d'API REST).
Filtre appliqué : category=musique

Les fichiers sont générés automatiquement dans des sous-dossiers
relatifs à l'emplacement du script :
    ./JSON/scrape_kulturfabrik_concerts.json
    ./CSV/scrape_kulturfabrik_concerts.csv
    ./Log/scrape_kulturfabrik_concerts.log

Usage:
    python scrape_kulturfabrik_concerts.py                          # JSON (défaut)
    python scrape_kulturfabrik_concerts.py -f csv                   # CSV
    python scrape_kulturfabrik_concerts.py -f csv -s "sold_out"     # Exclure des statuts
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
SCRIPT_NAME = Path(__file__).stem  # "scrape_kulturfabrik_concerts"
DIR_JSON = SCRIPT_DIR / "JSON"
DIR_CSV = SCRIPT_DIR / "CSV"
DIR_LOG = SCRIPT_DIR / "Log"

EVENTS_URL = "https://www.kulturfabrik.lu/events?category=musique"
BASE_URL = "https://www.kulturfabrik.lu"
USER_AGENT = "KulturfabrikConcertScraper/1.0"
MAX_WORKERS = 10
MAX_RETRIES = 3
RETRY_DELAY = 5  # secondes entre chaque retry

# Adresse fixe de la Kulturfabrik (Esch-sur-Alzette)
KULTURFABRIK_ADDRESS = "116, rue de Luxembourg, L-4221 Esch-sur-Alzette"

# Mois → numéro (2 chiffres)
_FR_MONTHS = {
    "janvier": "01", "février": "02", "mars": "03", "avril": "04",
    "mai": "05", "juin": "06", "juillet": "07", "août": "08",
    "septembre": "09", "octobre": "10", "novembre": "11", "décembre": "12",
}
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

logger = logging.getLogger("kulturfabrik_scraper")

# Cache artiste → genres (insensible à la casse)
_genre_cache: dict[str, list[str]] = {}


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
    Parse la page liste de la Kulturfabrik (?category=musique).

    Structure HTML observée (Bunker Palace CMS) :
        Date/heure texte : "jeu. 26.02.26 — 19h30" (avant le lien événement)
        <a href="/event/[slug]">
          <img src="[url]" alt="[titre]">
          <div>[titre artiste]</div>
          <div>[artistes support / sous-titre]</div>
        </a>

    Le parser collecte :
      - L'URL de chaque événement (href)
      - Le titre (premier texte significatif dans le lien)
      - Le sous-titre/support (deuxième texte significatif)
      - L'image (balise <img> dans le lien)
      - La date/heure (texte avant le lien, format DD.MM.YY — HHhMM)
    """

    def __init__(self):
        super().__init__()
        self._in_event_link = False
        self._text_count = 0        # nombre de textes non-vides collectés dans le lien
        self._current: dict = {}
        self._pending_date: str | None = None  # date collectée avant le lien
        self._pending_time: str | None = None  # heure collectée avant le lien
        self.events: list[dict] = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)

        if tag == "a":
            href = attrs_dict.get("href", "")
            # Liens de la forme /event/slug ou URL complète kulturfabrik.lu/event/slug
            if re.search(r"/event/[^/?#]+", href):
                self._in_event_link = True
                full_url = href if href.startswith("http") else BASE_URL + href
                self._current = {
                    "title": None,
                    "subtitle": None,
                    "url": full_url,
                    "image": None,
                    "date_str": self._pending_date,
                    "time_str": self._pending_time,
                }
                self._text_count = 0
                self._pending_date = None
                self._pending_time = None
            return

        if not self._in_event_link:
            return

        if tag == "img":
            src = (
                attrs_dict.get("data-src")
                or attrs_dict.get("data-lazy-src")
                or attrs_dict.get("data-original")
                or attrs_dict.get("src")
            )
            if src and not src.startswith("data:"):
                self._current["image"] = src if src.startswith("http") else BASE_URL + src

    def handle_endtag(self, tag):
        if tag == "a" and self._in_event_link:
            self._in_event_link = False
            if self._current.get("url"):
                self.events.append(dict(self._current))
            self._current = {}

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return

        if self._in_event_link:
            # Ignorer les fragments date/heure purs qui peuvent apparaître dans la card
            if re.match(r"^\d{2}\.\d{2}\.\d{2,4}$", text):
                if not self._current.get("date_str"):
                    self._current["date_str"] = text
                return
            if re.match(r"^\d{1,2}[h:]\d{2}$", text):
                return

            self._text_count += 1
            if self._text_count == 1:
                self._current["title"] = unescape(text)
            elif self._text_count == 2 and text != self._current.get("title"):
                self._current["subtitle"] = unescape(text)
        else:
            # Capturer date et heure avant le prochain lien événement
            # Format liste : "jeu. 26.02.26 — 19h30" ou variations
            m_date = re.search(r"\b(\d{2}\.\d{2}\.\d{2,4})\b", text)
            if m_date:
                self._pending_date = m_date.group(1)
            m_time = re.search(r"\b(\d{1,2}[h:]\d{2})\b", text)
            if m_time:
                self._pending_time = m_time.group(1)


def _parse_event_list(html: str) -> list[dict]:
    """Parse la page liste et retourne les événements bruts."""
    parser = _EventListParser()
    parser.feed(html)
    return parser.events


# ---------------------------------------------------------------------------
# Parsing de la date
# ---------------------------------------------------------------------------

def _parse_date(raw: str) -> str | None:
    """
    Convertit diverses représentations de date vers 'YYYY-MM-DD'.

    Formats supportés :
      - DD.MM.YYYY   → 26.02.2026
      - DD.MM.YY     → 26.02.26  (2-digit year → 20YY)
      - YYYY-MM-DD   → ISO
      - D(D) mois_fr YYYY → 28 février 2026
      - D(D) mois_en YYYY → 28 February 2026
      - mois_en D(D), YYYY → February 28, 2026
    """
    if not raw:
        return None
    raw = raw.strip()

    # ISO
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw

    # DD.MM.YYYY
    m = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", raw)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

    # DD.MM.YY (2-digit year → 20YY)
    m = re.match(r"^(\d{2})\.(\d{2})\.(\d{2})$", raw)
    if m:
        return f"20{m.group(3)}-{m.group(2)}-{m.group(1)}"

    raw_lower = raw.lower()

    # Forme française : "(jour) DD mois YYYY" → "28 février 2026"
    fr_pat = r"(\d{1,2})\s+(" + "|".join(_FR_MONTHS.keys()) + r")\s+(\d{4})"
    m = re.search(fr_pat, raw_lower)
    if m:
        return f"{m.group(3)}-{_FR_MONTHS[m.group(2)]}-{int(m.group(1)):02d}"

    # Forme anglaise DD Month YYYY : "28 February 2026"
    en_pat1 = r"(\d{1,2})\s+(" + "|".join(_EN_MONTHS.keys()) + r")\s*,?\s*(\d{4})"
    m = re.search(en_pat1, raw_lower)
    if m:
        return f"{m.group(3)}-{_EN_MONTHS[m.group(2)]}-{int(m.group(1)):02d}"

    # Forme anglaise Month DD, YYYY : "February 28, 2026"
    en_pat2 = r"(" + "|".join(_EN_MONTHS.keys()) + r")\s+(\d{1,2}),?\s+(\d{4})"
    m = re.search(en_pat2, raw_lower)
    if m:
        return f"{m.group(3)}-{_EN_MONTHS[m.group(1)]}-{int(m.group(2)):02d}"

    return None


# ---------------------------------------------------------------------------
# Enrichissement des genres via l'API Deezer
# ---------------------------------------------------------------------------

def _fetch_deezer_genres(artist_name: str) -> list[str]:
    """
    Récupère les genres musicaux d'un artiste via l'API Deezer.

    Chaîne d'appels :
      1. GET /search/artist?q=...     → artist_id
      2. GET /artist/{id}/top?limit=1 → album_id
      3. GET /album/{album_id}        → genres.data[].name

    Retourne ["Concerts"] en cas d'échec ou d'absence de données.
    Résultats mis en cache par nom d'artiste.
    """
    key = artist_name.strip().lower()
    if key in _genre_cache:
        return _genre_cache[key]

    fallback = ["Concerts"]
    deezer_headers = {"Accept-Language": "en"}
    try:
        url1 = f"https://api.deezer.com/search/artist?q={_url_quote(artist_name)}&limit=1"
        data1 = _request(url1, as_json=True, extra_headers=deezer_headers)
        artists = data1.get("data", [])
        if not artists:
            _genre_cache[key] = fallback
            return fallback
        artist_id = artists[0]["id"]

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
# Récupération du prix via Ticketmatic Kulturfabrik
# ---------------------------------------------------------------------------

def _fetch_kulturfabrik_price(buy_link: str) -> str:
    """
    Récupère le prix minimum depuis le widget Ticketmatic Kulturfabrik.
    URL attendue : apps.ticketmatic.com/widgets/kulturfabrik/addtickets?...&event=[ID]
    """
    try:
        m_id = re.search(r"[?&]event=(\d+)", buy_link)
        if not m_id:
            return "Price Unavailable"
        event_id = m_id.group(1)

        m_acc = re.search(r"apps\.ticketmatic\.com/widgets/([^/]+)/", buy_link)
        account = m_acc.group(1) if m_acc else "kulturfabrik"

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
        logger.debug("Prix Kulturfabrik non récupéré pour %s : %s", buy_link, exc)
        return "Price Unavailable"


# ---------------------------------------------------------------------------
# Récupération des détails par page de concert
# ---------------------------------------------------------------------------

def _fetch_show_details(url: str) -> dict:
    """
    Scrape une page individuelle de concert Kulturfabrik.
    Extrait : titre (og:title), image (og:image), date, doors_time,
              buy_link (Ticketmatic), status et price.
    """
    result: dict = {
        "title": None,
        "image": None,
        "date_str": None,
        "doors_time": None,
        "start_time": None,
        "buy_link": None,
        "status": None,
        "price": "Price Unavailable",
    }
    try:
        html = _request(url, retries=2)

        # --- Image og:image ---
        m_img = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html
        )
        if not m_img:
            m_img = re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html
            )
        if m_img:
            result["image"] = m_img.group(1)

        # --- Titre og:title ---
        m_title = re.search(
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html
        )
        if not m_title:
            m_title = re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', html
            )
        if m_title:
            result["title"] = unescape(m_title.group(1).strip())

        # --- Lien Ticketmatic (buy link) ---
        # Format : https://apps.ticketmatic.com/widgets/kulturfabrik/addtickets?...event=ID...
        m_buy = re.search(
            r'https://apps\.ticketmatic\.com/widgets/kulturfabrik/[^\s"\'<>#]+', html
        )
        if m_buy:
            # Retirer le fragment éventuel (#!/addtickets)
            result["buy_link"] = m_buy.group(0).split("#!/")[0].rstrip("?&")

        # --- Date ---
        # Priorité 1 : balise <time datetime="YYYY-MM-DD">
        m_time_tag = re.search(r'<time[^>]+datetime=["\'](\d{4}-\d{2}-\d{2})["\']', html)
        if m_time_tag:
            result["date_str"] = m_time_tag.group(1)
        else:
            # Priorité 2 : DD.MM.YYYY dans le texte (hors attributs HTML)
            m_date = re.search(r'\b(\d{2}\.\d{2}\.\d{4})\b', html)
            if m_date:
                result["date_str"] = m_date.group(1)
            else:
                # Priorité 3 : forme française "28 février 2026"
                fr_pat = (
                    r'\b(\d{1,2})\s+('
                    + "|".join(_FR_MONTHS.keys())
                    + r')\s+(\d{4})\b'
                )
                m_fr = re.search(fr_pat, html, re.IGNORECASE)
                if m_fr:
                    result["date_str"] = (
                        f"{m_fr.group(1)} {m_fr.group(2).lower()} {m_fr.group(3)}"
                    )
                else:
                    # Priorité 4 : forme anglaise "February 28, 2026" ou "28 February 2026"
                    en_pat = (
                        r'\b('
                        + "|".join(_EN_MONTHS.keys())
                        + r')\s+(\d{1,2}),?\s+(\d{4})\b'
                    )
                    m_en = re.search(en_pat, html, re.IGNORECASE)
                    if m_en:
                        result["date_str"] = (
                            f"{m_en.group(2)} {m_en.group(1).lower()} {m_en.group(3)}"
                        )

        # --- Heure des portes ---
        for pattern in [
            r"[Pp]ortes?\s*[:\-–—]\s*(\d{1,2}[h:]\d{2})",
            r"[Oo]uverture\s+des\s+portes[^0-9]*(\d{1,2}[h:]\d{2})",
            r"[Dd]oors?\s*[:\-–—]\s*(\d{1,2}[h:]\d{2})",
        ]:
            m = re.search(pattern, html)
            if m:
                result["doors_time"] = m.group(1)
                break

        # --- Heure de début (fallback si portes non trouvées) ---
        for pattern in [
            r"[Dd]ébut\s*[:\-–—]\s*(\d{1,2}[h:]\d{2})",
            r"[Ss]tart\s*[:\-–—]\s*(\d{1,2}[h:]\d{2})",
            r"[Ss]how\s*[:\-–—]\s*(\d{1,2}[h:]\d{2})",
        ]:
            m = re.search(pattern, html)
            if m:
                result["start_time"] = m.group(1)
                break

        # --- Statut (sold out) ---
        # On vérifie la présence de classes CSS spécifiques plutôt que du texte brut
        # pour éviter les faux positifs (ex : "sold out" dans une description d'artiste)
        if re.search(r'class=["\'][^"\']*sold.?out[^"\']*["\']', html, re.IGNORECASE):
            result["status"] = "sold_out"
        elif re.search(r'class=["\'][^"\']*complet[^"\']*["\']', html, re.IGNORECASE):
            result["status"] = "sold_out"
        else:
            result["status"] = "buy_now"

        # --- Prix via Ticketmatic ---
        if result["buy_link"]:
            result["price"] = _fetch_kulturfabrik_price(result["buy_link"])

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
      1. Scraping de la page ?category=musique → liste des événements
      2. Scraping des pages individuelles → date, horaires, image, prix (parallélisé)
      3. Enrichissement des genres via l'API Deezer (séquentiel, avec cache)
    """
    run_timestamp = datetime.now(timezone.utc).isoformat()

    # --- 1. Page liste ---
    logger.info("Récupération de la liste des concerts Kulturfabrik…")
    html = _request(EVENTS_URL)
    events_raw = _parse_event_list(html)

    if not events_raw:
        logger.warning(
            "Aucun concert trouvé — vérifier si le site est en maintenance "
            "ou si la structure HTML a changé"
        )
        return {
            "scraped_at": run_timestamp,
            "source": EVENTS_URL,
            "total": 0,
            "concerts": [],
        }

    logger.info("%d concerts trouvés sur la page liste", len(events_raw))

    # --- 2. Scraping des pages individuelles (parallélisé) ---
    logger.info("Scraping de %d pages pour horaires, images et prix…", len(events_raw))

    show_details: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_url = {
            executor.submit(_fetch_show_details, ev["url"]): ev["url"]
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
            if result.get("date_str") is None and result.get("doors_time") is None:
                fail_count += 1

    if fail_count:
        logger.warning(
            "Détails incomplets pour %d/%d concerts (date ou horaire manquants)",
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

        # Titre : og:title de la page individuelle souvent plus propre (sans caractères parasites)
        title = details.get("title") or ev.get("title") or ""

        # Date : préférer la page individuelle (plus fiable), sinon la liste
        raw_date = details.get("date_str") or ev.get("date_str") or ""
        date_live = _parse_date(raw_date)
        if date_live is None:
            logger.debug("Date non parsable pour '%s' : '%s'", title, raw_date)

        # Heure : portes → start_time de la page individuelle → time_str de la liste
        doors_time = (
            details.get("doors_time")
            or details.get("start_time")
            or ev.get("time_str")
        )

        # Image : og:image (page individuelle) prioritaire sur l'image de la liste
        image = details.get("image") or ev.get("image")

        # ID : slug extrait de l'URL de l'événement
        slug_match = re.search(r"/event/([^/?#]+)", url)
        concert_id = slug_match.group(1) if slug_match else url

        concert = {
            "id": concert_id,
            "artist": title,
            "date_live": date_live,
            "doors_time": doors_time,
            "location": "Kulturfabrik",
            "address": KULTURFABRIK_ADDRESS,
            "genres": ev.get("deezer_genres") or ["Concerts"],
            "status": details.get("status") or "buy_now",
            "url": url,
            "buy_link": details.get("buy_link"),
            "image": image,
            "price": details.get("price", "Price Unavailable"),
            "date_created": run_timestamp,
        }
        concerts.append(concert)

    # Filtre genres
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

    # Filtre statuts
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
        "source": EVENTS_URL,
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
        description="Récupère la liste des concerts depuis kulturfabrik.lu"
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
        help='Statuts à exclure, séparés par des points-virgules (ex: "sold_out")',
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
