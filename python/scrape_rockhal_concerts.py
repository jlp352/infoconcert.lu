#!/usr/bin/env python3
"""
Scraper des concerts disponibles sur https://rockhal.lu/
Utilise l'API REST WordPress + scraping des pages individuelles.

Les fichiers sont générés automatiquement dans des sous-dossiers
relatifs à l'emplacement du script :
    ./JSON/scrape_rockhal_concerts.json
    ./CSV/scrape_rockhal_concerts.csv
    ./Log/scrape_rockhal_concerts.log

Un champ "new" indique "New" si le concert n'existait pas lors du
scan précédent (comparaison par id).

Usage:
    python scrape_rockhal_concerts.py                  # JSON (défaut)
    python scrape_rockhal_concerts.py -f csv           # CSV
    python scrape_rockhal_concerts.py -f csv --available-only
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
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# Répertoire racine = dossier contenant le script
SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT_NAME = Path(__file__).stem  # "scrape_rockhal_concerts"
DIR_JSON = SCRIPT_DIR / "JSON"
DIR_CSV = SCRIPT_DIR / "CSV"
DIR_LOG = SCRIPT_DIR / "Log"

API_SHOWS = "https://rockhal.lu/wp-json/rockhal/shows"
USER_AGENT = "RockhalConcertScraper/1.0"
MAX_WORKERS = 10
MAX_RETRIES = 3
RETRY_DELAY = 5  # secondes entre chaque retry

# Adresse fixe de la Rockhal
ROCKHAL_ADDRESS = "5, avenue du Rock, L-4361 Esch-sur-Alzette"

CSV_COLUMNS = [
    "id", "artist", "date_live", "doors_time", "location",
    "address", "genres", "status", "new", "url", "buy_link", "image",
    "date_created",
]

logger = logging.getLogger("rockhal_scraper")


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

def _request(url: str, *, as_json: bool = False, retries: int = MAX_RETRIES):
    """
    GET avec retry automatique.
    Retourne le contenu décodé (str) ou le JSON parsé selon as_json.
    Lève une exception si toutes les tentatives échouent.
    """
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
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
# Parsing de la section .show-detail__practical
# ---------------------------------------------------------------------------

class _PracticalInfoParser(HTMLParser):
    """
    Extrait les infos pratiques de la section .show-detail__practical.

    Structure HTML de Rockhal :
        <div class="show-detail__practical">
          <div class="uppercase">
            <span>Venue:</span> Rockhal Main Hall<br>
            <span>Doors:</span> 19:00<br>
          </div>
        </div>

    Le parser collecte les paires label/valeur en suivant les <span>.
    """

    def __init__(self):
        super().__init__()
        self._in_practical = False
        self._in_span = False
        self._current_label = ""
        self._current_value = ""
        self._collecting_value = False
        self.items: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class", "")
        if "show-detail__practical" in cls:
            self._in_practical = True
        if self._in_practical and tag == "span":
            # Fin de la valeur précédente
            if self._collecting_value and self._current_label:
                self.items[self._current_label] = self._current_value.strip()
            self._in_span = True
            self._current_label = ""
            self._current_value = ""
            self._collecting_value = False
        # Un <br> marque la fin de la valeur courante (si pas de nouveau span)
        if self._in_practical and tag == "br" and self._collecting_value:
            if self._current_label:
                self.items[self._current_label] = self._current_value.strip()
                self._collecting_value = False

    def handle_endtag(self, tag):
        if self._in_span and tag == "span":
            self._in_span = False
            self._collecting_value = True

    def handle_data(self, data):
        if self._in_span:
            self._current_label += data.strip()
        elif self._collecting_value and self._in_practical:
            self._current_value += data

    def close(self):
        # Capturer la dernière paire si elle existe
        if self._collecting_value and self._current_label:
            self.items[self._current_label] = self._current_value.strip()
        super().close()


def _parse_practical_info(html: str) -> dict:
    """Renvoie {doors_time} depuis le HTML d'une page de show Rockhal."""
    parser = _PracticalInfoParser()
    parser.feed(html)
    parser.close()

    result = {"doors_time": None}
    for label, value in parser.items.items():
        label_clean = re.sub(r"\s+", " ", label).strip().rstrip(":")
        if label_clean.lower() == "doors":
            result["doors_time"] = value.strip()
            break
    return result


# ---------------------------------------------------------------------------
# Récupération des détails par page de concert
# ---------------------------------------------------------------------------

def _fetch_show_details(url: str) -> dict:
    """Scrape une page de concert individuelle (2 tentatives pour les détails)."""
    try:
        html = _request(url, retries=2)
        info = _parse_practical_info(html)
        if info["doors_time"] is None:
            logger.warning("Structure inattendue (show-detail__practical absent ou Doors manquant) : %s", url)
        return info
    except Exception as exc:
        logger.warning("Impossible de scraper %s : %s", url, exc)
        return {"doors_time": None}


# ---------------------------------------------------------------------------
# Parsing de la date
# ---------------------------------------------------------------------------

def _parse_date(raw: str) -> str | None:
    """Convertit 'Thu 19 Feb 2026' → '2026-02-19'."""
    raw = raw.strip()
    for fmt in ("%a %d %B %Y", "%a %d %b %Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Validation de la réponse API
# ---------------------------------------------------------------------------

def _validate_api_response(data: dict) -> list[dict]:
    """
    Vérifie que la réponse de l'API a la structure attendue.
    Retourne la liste des shows ou lève une ValueError.
    """
    if not isinstance(data, dict):
        raise ValueError(f"Réponse API inattendue : type={type(data).__name__}, attendu=dict")

    if "shows" not in data:
        raise ValueError(
            f"Clé 'shows' absente de la réponse API. Clés reçues : {list(data.keys())}. "
            "La structure du site a peut-être changé."
        )

    shows = data["shows"]
    if not isinstance(shows, list):
        raise ValueError(f"'shows' n'est pas une liste : type={type(shows).__name__}")

    if len(shows) == 0:
        logger.warning("L'API a retourné 0 concerts — vérifier si le site est en maintenance")
        return shows

    # Vérifier que les champs attendus sont présents dans le premier show
    required_fields = {"id", "title", "start_date", "show_month", "show_year"}
    sample = shows[0]
    missing = required_fields - set(sample.keys())
    if missing:
        raise ValueError(
            f"Champs manquants dans les données : {missing}. "
            "La structure de l'API a peut-être changé."
        )

    return shows


# ---------------------------------------------------------------------------
# Fonction principale de collecte
# ---------------------------------------------------------------------------

def fetch_concerts(available_only: bool = False) -> dict:
    """
    Récupère la liste complète des concerts avec toutes les métadonnées.

    Étapes :
      1. /wp-json/rockhal/shows    → données principales (avec retry)
      2. Scraping pages individuelles → heure d'ouverture des portes
    """

    run_timestamp = datetime.now(timezone.utc).isoformat()

    # --- 1. Données principales ---
    logger.info("Récupération de la liste des concerts…")
    raw = _request(API_SHOWS, as_json=True)
    shows = _validate_api_response(raw)
    genres_list = raw.get("genres", [])
    logger.info("%d concerts trouvés via l'API", len(shows))

    # --- 2. Scraping des pages individuelles (parallélisé) ---
    logger.info("Scraping de %d pages pour horaires…", len(shows))

    show_details: dict[str, dict] = {}
    urls = {s["permalink"]: s["permalink"] for s in shows if s.get("permalink")}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_url = {
            executor.submit(_fetch_show_details, url): url
            for url in urls.values()
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
            "Détails incomplets pour %d/%d concerts (horaire manquant)",
            fail_count, done_count,
        )
    logger.info("Scraping terminé : %d/%d pages OK", done_count - fail_count, done_count)

    # --- Assemblage final ---
    concerts = []
    for show in shows:
        raw_date = show.get("start_date", "")
        permalink = show.get("permalink", "")
        details = show_details.get(permalink, {})

        date_live = _parse_date(raw_date)
        if date_live is None:
            logger.debug("Date non parsable pour '%s' : '%s'", show.get("title"), raw_date)

        concert = {
            "id": show.get("id"),
            "artist": show.get("title"),
            "date_live": date_live,
            "doors_time": details.get("doors_time"),
            "location": show.get("location"),
            "address": ROCKHAL_ADDRESS,
            "genres": [g.get("name") for g in show.get("genres", [])],
            "status": show.get("status_string"),
            "url": permalink,
            "buy_link": show.get("custom_event_link"),
            "image": show.get("image_url"),
            "date_created": run_timestamp,
        }
        concerts.append(concert)

    if available_only:
        before = len(concerts)
        concerts = [c for c in concerts if c["status"] not in ("sold-out", "cancelled")]
        logger.info("Filtre available_only : %d → %d concerts", before, len(concerts))

    return {
        "scraped_at": run_timestamp,
        "source": API_SHOWS,
        "total": len(concerts),
        "concerts": concerts,
        "genres": genres_list,
    }


# ---------------------------------------------------------------------------
# Détection des nouveaux concerts
# ---------------------------------------------------------------------------

def _load_previous_ids(out_file: Path, fmt: str) -> set[int]:
    """Charge les IDs du fichier de sortie précédent (JSON ou CSV)."""
    if not out_file.exists():
        return set()

    try:
        text = out_file.read_text(encoding="utf-8")
        if fmt == "json":
            data = json.loads(text)
            return {c["id"] for c in data.get("concerts", []) if c.get("id")}
        else:
            reader = csv.DictReader(io.StringIO(text))
            return {int(row["id"]) for row in reader if row.get("id")}
    except Exception as exc:
        logger.warning("Impossible de lire le fichier précédent %s : %s", out_file, exc)
        return set()


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
        description="Récupère la liste des concerts depuis rockhal.lu"
    )
    parser.add_argument(
        "-f", "--format",
        choices=["json", "csv"],
        default="json",
        help="Format de sortie : json (défaut) ou csv",
    )
    parser.add_argument(
        "--available-only",
        action="store_true",
        help="Exclure les concerts complets (sold-out) et annulés (cancelled)",
    )
    args = parser.parse_args()

    # --- Logging ---
    _setup_logging()
    logger.info("=" * 60)
    logger.info("Démarrage du scraper (format=%s, available_only=%s)", args.format, args.available_only)

    try:
        data = fetch_concerts(available_only=args.available_only)

        # --- Déterminer le fichier de sortie ---
        if args.format == "csv":
            out_file = DIR_CSV / f"{SCRIPT_NAME}.csv"
        else:
            out_file = DIR_JSON / f"{SCRIPT_NAME}.json"

        # --- Charger les IDs du scan précédent ---
        previous_ids = _load_previous_ids(out_file, args.format)
        new_count = 0
        for concert in data["concerts"]:
            if previous_ids and concert["id"] not in previous_ids:
                concert["new"] = "New"
                new_count += 1
            else:
                concert["new"] = ""

        if previous_ids:
            logger.info("%d nouveau(x) concert(s) détecté(s)", new_count)
        else:
            logger.info("Premier scan, pas de comparaison possible")

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
