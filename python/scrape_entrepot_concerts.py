#!/usr/bin/env python3
"""
Scraper des concerts disponibles sur https://www.entrepotarlon.be/
Scraping de la page HTML agenda.php (pas d'API, flux RSS limité à 10 items).

Les fichiers sont générés automatiquement dans des sous-dossiers
relatifs à l'emplacement du script :
    ./JSON/scrape_entrepot_concerts.json
    ./CSV/scrape_entrepot_concerts.csv
    ./Log/scrape_entrepot_concerts.log

Usage:
    python scrape_entrepot_concerts.py                          # JSON (défaut)
    python scrape_entrepot_concerts.py -f csv                   # CSV
    python scrape_entrepot_concerts.py -f csv -s "sold_out"     # Exclure des statuts
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

AGENDA_URL = "https://www.entrepotarlon.be/agenda.php"
BASE_URL = "https://www.entrepotarlon.be"
# Encodage de la page (iso-8859-15 déclaré dans le HTML)
PAGE_ENCODING = "iso-8859-15"
USER_AGENT = "EntrepotArlonConcertScraper/1.0"
MAX_RETRIES = 3
RETRY_DELAY = 5  # secondes entre chaque retry

# Adresse fixe de L'Entrepôt Arlon (Belgique)
# Coordonnées GPS : 49.68019, 5.80322
ENTREPOT_ADDRESS = "6700 Arlon, Belgique"

CSV_COLUMNS = [
    "id", "artist", "date_live", "doors_time", "location",
    "address", "genres", "status", "url", "buy_link", "image",
    "price", "date_created",
]

logger = logging.getLogger("entrepot_scraper")


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

def _request(url: str, *, as_json: bool = False, retries: int = MAX_RETRIES,
             encoding: str = "utf-8", extra_headers: dict | None = None) -> str:
    """
    GET avec retry automatique.
    Retourne le contenu décodé (str) ou le JSON parsé selon as_json.
    """
    last_exc = None
    headers = {"User-Agent": USER_AGENT, **(extra_headers or {})}
    for attempt in range(1, retries + 1):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=30) as resp:
                body = resp.read().decode(encoding)
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
# Parsing de la page agenda
# ---------------------------------------------------------------------------

# Regex pour les ancres de section mensuelle : <a name="month202603">
_MONTH_ANCHOR_RE = re.compile(r'<a\s+name="month(\d{4})(\d{2})"')

# Séparateur entre concerts
_SEP_RE = re.compile(r'<div class="agendasep"')

# Bloc de concert
_AGENDA_BLOCK_START = re.compile(r'<div class="agenda"')

# ID concert depuis l'agendaflyer
_ID_RE = re.compile(r'<div class="agendaflyer">.*?href="concert\.php\?id=(\d+)"', re.DOTALL)

# Image miniature (125w) dans l'agendaflyer
_IMG_RE = re.compile(r'<div class="agendaflyer">.*?<img src="([^"]+)"', re.DOTALL)

# Date concert (événement sur un jour) — classe "showdate"
_SINGLE_DATE_RE = re.compile(
    r'class="showdate[^"]*"[^>]*>.*?(\d{2}/\d{2})'
    r'.*?<sup class="showhour">([^<]+)</sup>',
    re.DOTALL,
)

# Date concert (festival multi-jours) — classe "gothic24sh colorthis"
_MULTI_DATE_RE = re.compile(
    r'class="gothic24sh colorthis"[^>]*>.*?(\d{2}/\d{2})',
    re.DOTALL,
)

# Artiste principal (headliner)
_HEADLINER_RE = re.compile(
    r'<p class="agendafirstshow">(.*?)</p>',
    re.DOTALL,
)

# Prix (ex: "20&euro; / 20&euro;" ou "20 &euro; / 20 &euro;")
_PRICE_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(?:&euro;|€)\s*/\s*(\d+(?:\.\d+)?)\s*(?:&euro;|€)')

# Concert gratuit
_FREE_RE = re.compile(r'CONCERT GRATUIT', re.IGNORECASE)

# Lien utick (billetterie)
_UTICK_RE = re.compile(r'href="(https://shop\.utick\.net/[^"]+)"')


def _strip_html(text: str) -> str:
    """Supprime toutes les balises HTML et nettoie les entités."""
    # Remplacer <sup>...</sup> : si le contenu a déjà des parenthèses, garder tel quel
    def _sup_replace(m: re.Match) -> str:
        content = m.group(1).strip()
        return f" {content}" if content.startswith("(") else f" ({content})"
    text = re.sub(r'<sup[^>]*>(.*?)</sup>', _sup_replace, text)
    # Supprimer les balises restantes
    text = re.sub(r'<[^>]+>', '', text)
    # Nettoyer les espaces multiples
    text = re.sub(r'  +', ' ', unescape(text))
    return text.strip()


def _parse_time(raw: str) -> str | None:
    """
    Convertit l'heure de la balise showhour.
    Formats attendus : '20H30', '20H', '19H30', '18H', '13H'
    Retourne 'HH:MM' ou None.
    """
    raw = raw.strip().upper()
    m = re.match(r'(\d{1,2})H(\d{2})?', raw)
    if not m:
        return None
    h = int(m.group(1))
    mn = int(m.group(2)) if m.group(2) else 0
    return f"{h:02d}:{mn:02d}"


def _make_image_url(thumb_url: str) -> str:
    """
    Remplace le préfixe de taille '125w' par '474x474' pour obtenir
    une image de meilleure résolution.
    Exemples :
      http://img.losange.net/13397/125w/foo.jpg
      → http://img.losange.net/13397/474x474/foo.jpg
    """
    return re.sub(r'/(\d+w|125w)/', '/474x474/', thumb_url)


def _parse_price(block_html: str) -> tuple[str, str]:
    """
    Extrait le prix et détermine le statut depuis un bloc HTML de concert.

    Retourne (price_str, status) où :
      price_str : '20.00 EUR', 'Free' ou 'Price Unavailable'
      status    : 'buy_now', 'free' ou None
    """
    # Concert gratuit
    if _FREE_RE.search(block_html):
        return "Free", "free"

    # Prix payant
    m = _PRICE_RE.search(block_html)
    if m:
        presale = float(m.group(1))
        if presale == 0:
            return "Free", "free"
        return f"{presale:.2f} EUR", "buy_now"

    return "Price Unavailable", None


def _parse_concerts_from_html(html: str) -> list[dict]:
    """
    Parse la page agenda.php et retourne la liste des concerts bruts.

    Stratégie :
      1. Identifier les sections mensuelles via les ancres <a name="month YYYYMM">
         pour associer le bon couple (année, mois) à chaque concert.
      2. Découper le HTML en blocs <div class="agenda"> ... <div class="agendasep">
      3. Extraire les champs de chaque bloc.
    """
    concerts = []
    current_year = 0
    current_month = 0

    # On parcourt le HTML segment par segment, délimité par les ancres mensuelles
    # et les séparateurs de concerts.
    # On repère d'abord toutes les positions des ancres mensuelles.
    month_positions = [
        (m.start(), int(m.group(1)), int(m.group(2)))
        for m in _MONTH_ANCHOR_RE.finditer(html)
    ]

    # On décompose en sections : de chaque ancre mensuelle jusqu'à la suivante
    sections = []
    for i, (pos, year, month) in enumerate(month_positions):
        end = month_positions[i + 1][0] if i + 1 < len(month_positions) else len(html)
        sections.append((year, month, html[pos:end]))

    for year, month, section_html in sections:
        # Découper la section en blocs de concerts (séparés par agendasep)
        blocks = _SEP_RE.split(section_html)
        for block in blocks:
            if '<div class="agenda"' not in block:
                continue
            concert = _parse_single_block(block, year, month)
            if concert:
                concerts.append(concert)

    return concerts


def _parse_single_block(block: str, year: int, month: int) -> dict | None:
    """
    Parse un bloc HTML correspondant à un seul concert.
    Retourne un dict avec les champs bruts, ou None si parsing impossible.
    """
    # --- ID ---
    m_id = _ID_RE.search(block)
    if not m_id:
        return None
    concert_id = m_id.group(1)

    # --- URL ---
    url = f"{BASE_URL}/concert.php?id={concert_id}"

    # --- Image (miniature → haute résolution) ---
    m_img = _IMG_RE.search(block)
    image = _make_image_url(m_img.group(1)) if m_img else None

    # --- Date et heure ---
    date_str = None
    doors_time = None

    m_date = _SINGLE_DATE_RE.search(block)
    if m_date:
        # Ex: "13/03" → day=13, month=03
        raw_date = m_date.group(1)  # "DD/MM"
        day, _ = raw_date.split("/")
        date_str = f"{year}-{month:02d}-{int(day):02d}"
        doors_time = _parse_time(m_date.group(2))
    else:
        # Événement multi-jours : prendre la première date
        m_multi = _MULTI_DATE_RE.search(block)
        if m_multi:
            raw_date = m_multi.group(1)  # "DD/MM"
            day, _ = raw_date.split("/")
            date_str = f"{year}-{month:02d}-{int(day):02d}"
            doors_time = None  # pas d'heure unique pour les festivals multi-jours

    if date_str is None:
        logger.debug("Bloc ignoré (date introuvable) pour concert id=%s", concert_id)
        return None

    # --- Artiste principal (headliner) ---
    m_hl = _HEADLINER_RE.search(block)
    artist = _strip_html(m_hl.group(1)) if m_hl else ""

    # --- Prix et statut ---
    price, status = _parse_price(block)

    # Affiner le statut : si présence d'un lien utick → buy_now confirmé
    m_utick = _UTICK_RE.search(block)
    buy_link = unescape(m_utick.group(1)) if m_utick else None

    if status is None and buy_link:
        status = "buy_now"

    return {
        "id": concert_id,
        "artist": artist,
        "date_str": date_str,
        "doors_time": doors_time,
        "image": image,
        "price": price,
        "status": status,
        "url": url,
        "buy_link": buy_link,
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
    # Nettoyer le nom : enlever le code pays entre parenthèses ex: "(FR)"
    clean_name = re.sub(r'\s*\([A-Z]{2,3}\)\s*$', '', artist_name).strip()
    key = clean_name.lower()

    if key in _genre_cache:
        return _genre_cache[key]

    fallback = ["Concerts"]
    deezer_headers = {"Accept-Language": "en"}

    try:
        url1 = f"https://api.deezer.com/search/artist?q={_url_quote(clean_name)}&limit=1"
        data1 = _request(url1, as_json=True, encoding="utf-8")
        artists = data1.get("data", [])
        if not artists:
            _genre_cache[key] = fallback
            return fallback
        artist_id = artists[0]["id"]

        url2 = f"https://api.deezer.com/artist/{artist_id}/top?limit=1"
        data2 = _request(url2, as_json=True, encoding="utf-8")
        tracks = data2.get("data", [])
        if not tracks:
            _genre_cache[key] = fallback
            return fallback
        album_id = tracks[0].get("album", {}).get("id")
        if not album_id:
            _genre_cache[key] = fallback
            return fallback

        url3 = f"https://api.deezer.com/album/{album_id}"
        data3 = _request(url3, as_json=True, encoding="utf-8", extra_headers=deezer_headers)
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
    Récupère la liste complète des concerts avec toutes les métadonnées.

    Étapes :
      1. Scraping de la page agenda.php → liste de tous les concerts à venir
      2. Enrichissement des genres via l'API Deezer (séquentiel, avec cache)
    """
    run_timestamp = datetime.now(timezone.utc).isoformat()

    # --- 1. Page agenda ---
    logger.info("Récupération de la page agenda L'Entrepôt Arlon…")
    html = _request(AGENDA_URL, encoding=PAGE_ENCODING)
    events_raw = _parse_concerts_from_html(html)

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

    logger.info("%d concerts trouvés sur la page agenda", len(events_raw))

    # --- 2. Enrichissement des genres via Deezer (séquentiel + cache) ---
    logger.info("Récupération des genres musicaux via Deezer pour %d artistes…", len(events_raw))
    for ev in events_raw:
        ev["deezer_genres"] = _fetch_deezer_genres(ev.get("artist") or "")
    logger.info("Genres Deezer récupérés.")

    # --- Assemblage final ---
    concerts = []
    for ev in events_raw:
        concert = {
            "id": ev["id"],
            "artist": ev.get("artist") or "",
            "date_live": ev.get("date_str"),
            "doors_time": ev.get("doors_time"),
            "location": "L'Entrepôt",
            "address": ENTREPOT_ADDRESS,
            "genres": ev.get("deezer_genres") or ["Concerts"],
            "status": ev.get("status"),
            "url": ev.get("url"),
            "buy_link": ev.get("buy_link"),
            "image": ev.get("image"),
            "price": ev.get("price", "Price Unavailable"),
            "date_created": run_timestamp,
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
        description="Récupère la liste des concerts depuis entrepotarlon.be"
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
        "Démarrage du scraper L'Entrepôt (format=%s, exclude_genres=%s, exclude_statuses=%s)",
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
