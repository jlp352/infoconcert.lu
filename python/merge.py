"""
Concatène tous les fichiers JSON ou CSV du dossier source en un seul fichier.
Les doublons sont détectés uniquement sur (artist, date_live).

Usage :
    python merge.py -f json   → fusionne JSON/  → OUT/concerts.json
    python merge.py -f csv    → fusionne CSV/   → OUT/concerts.csv

Logs : Log/merge.log  (append)
"""

import argparse
import csv
import json
import logging
import os
import shutil
from datetime import datetime

import requests


# ──────────────────────────────────────────────
# Deezer
# ──────────────────────────────────────────────

_deezer_cache: dict[str, tuple[str, str]] = {}


def load_cache_from_bak(bak_path: str, fmt: str, log: logging.Logger) -> None:
    """Pré-alimente _deezer_cache depuis le fichier .bak (JSON ou CSV).
    Seuls les artistes avec preview_url non vide sont mis en cache
    (les autres seront retentés via l'API Deezer).
    """
    if not os.path.exists(bak_path):
        return

    loaded = 0
    try:
        if fmt == "json":
            with open(bak_path, encoding="utf-8") as f:
                data = json.load(f)
            concerts = data.get("concerts", [])
        else:
            with open(bak_path, encoding="utf-8", newline="") as f:
                concerts = list(csv.DictReader(f))

        for concert in concerts:
            artist      = (concert.get("artist") or "").strip()
            preview_url  = (concert.get("preview_url")  or "").strip()
            preview_url1 = (concert.get("preview_url1") or "").strip()
            if artist and preview_url:          # seulement si preview non vide
                cache_key = artist.lower()
                if cache_key not in _deezer_cache:
                    _deezer_cache[cache_key] = (preview_url, preview_url1)
                    loaded += 1

        log.info(f"Cache Deezer pré-chargé depuis .bak : {loaded} artiste(s)")

    except Exception as e:
        log.warning(f"Impossible de lire le .bak pour le cache Deezer : {e}")


def get_top2_previews(artist_name: str) -> tuple[str, str]:
    """Retourne (preview_url, preview_url1) pour l'artiste via l'API Deezer.
    Résultats mis en cache par nom d'artiste. Retourne ("", "") si introuvable.
    """
    key = artist_name.strip().lower()
    if key in _deezer_cache:
        return _deezer_cache[key]

    try:
        r = requests.get(
            "https://api.deezer.com/search/artist",
            params={"q": artist_name},
            timeout=10,
        )
        data = r.json().get("data", [])
        if not data:
            _deezer_cache[key] = ("", "")
            return ("", "")

        artist_id = data[0]["id"]

        r2 = requests.get(
            f"https://api.deezer.com/artist/{artist_id}/top",
            params={"limit": 50},
            timeout=10,
        )
        tracks = r2.json().get("data", [])
        tracks_sorted = sorted(tracks, key=lambda x: x.get("rank", 0), reverse=True)

        previews = [t.get("preview", "") for t in tracks_sorted[:2]]
        while len(previews) < 2:
            previews.append("")

        result = (previews[0], previews[1])
        _deezer_cache[key] = result
        return result

    except Exception:
        _deezer_cache[key] = ("", "")
        return ("", "")

BASE_DIR = os.path.dirname(__file__)
OUT_DIR  = os.path.join(BASE_DIR, "OUT")
LOG_DIR  = os.path.join(BASE_DIR, "Log")


# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────

def setup_logger(fmt: str) -> logging.Logger:
    """Configure le logger : fichier Log/merge.log (append) + console."""
    os.makedirs(LOG_DIR, exist_ok=True)

    log_file = os.path.join(LOG_DIR, "merge.log")

    fmt_str  = "%(asctime)s [%(levelname)s] %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S,%f"[:-3]   # millisecondes à 3 chiffres

    logger = logging.getLogger("merge")
    logger.setLevel(logging.DEBUG)

    # Handler fichier — mode append pour conserver l'historique
    fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(fmt_str, datefmt=date_fmt))

    # Handler console
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(fmt_str, datefmt=date_fmt))

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def dedup_key(concert: dict) -> tuple:
    """Clé de déduplication : (artist normalisé, date_live)."""
    artist    = (concert.get("artist") or "").strip().lower()
    date_live = (concert.get("date_live") or "").strip()
    return (artist, date_live)


def backup(path: str, log: logging.Logger) -> tuple[bool, str]:
    """Sauvegarde le fichier existant. Retourne (has_backup, backup_path)."""
    bak = path + ".bak"
    if os.path.exists(path):
        shutil.copy2(path, bak)
        os.remove(path)
        log.info(f"Ancien fichier sauvegardé : {bak}")
        return True, bak
    return False, bak


def restore(has_backup: bool, bak: str, path: str, log: logging.Logger) -> None:
    """Restaure le backup en cas d'erreur."""
    if has_backup and os.path.exists(bak):
        shutil.copy2(bak, path)
        os.remove(bak)
        log.info(f"Ancien fichier restauré   : {path}")
    elif os.path.exists(path):
        os.remove(path)


def cleanup_backup(has_backup: bool, bak: str, log: logging.Logger) -> None:
    """Supprime le backup après un succès."""
    if has_backup and os.path.exists(bak):
        os.remove(bak)
        log.debug(f"Backup supprimé : {bak}")


# ──────────────────────────────────────────────
# JSON
# ──────────────────────────────────────────────

def merge_json(input_dir: str, output_file: str, log: logging.Logger) -> None:
    json_files = sorted(
        f for f in os.listdir(input_dir) if f.endswith(".json")
    )
    if not json_files:
        log.warning(f"Aucun fichier JSON trouvé dans {input_dir}")
        return

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    has_backup, bak = backup(output_file, log)
    load_cache_from_bak(bak, "json", log)

    try:
        merged_concerts = {}   # dedup_key -> concert
        merged_genres   = {}   # id         -> genre dict
        merged_venues   = {}   # id         -> venue dict
        sources    = []
        duplicates = 0

        for filename in json_files:
            filepath = os.path.join(input_dir, filename)
            log.info(f"Traitement : {filename}")
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)

            sources.append(data.get("source", filename))

            for concert in data.get("concerts", []):
                key = dedup_key(concert)
                if key in merged_concerts:
                    duplicates += 1
                    log.debug(f"Doublon ignoré : {concert.get('artist','?')} le {key[1]}")
                else:
                    artist = concert.get("artist", "")
                    preview_url, preview_url1 = get_top2_previews(artist)
                    log.debug(f"Deezer preview : {artist} → {preview_url or 'introuvable'}")
                    concert["preview_url"]  = preview_url
                    concert["preview_url1"] = preview_url1
                    merged_concerts[key] = concert

            for genre in data.get("genres", []):
                gid = genre.get("id") if isinstance(genre, dict) else genre
                merged_genres.setdefault(gid, genre)

            for venue in data.get("venues", []):
                vid = venue.get("id") if isinstance(venue, dict) else venue
                merged_venues.setdefault(vid, venue)

        concerts_list = sorted(
            merged_concerts.values(),
            key=lambda c: (c.get("date_live", ""), c.get("artist", ""))
        )
        genres_list = sorted(
            merged_genres.values(),
            key=lambda g: g.get("name", "") if isinstance(g, dict) else g
        )
        venues_list = sorted(
            merged_venues.values(),
            key=lambda v: v.get("name", "") if isinstance(v, dict) else v
        )

        output = {
            "scraped_at":         datetime.now().isoformat(),
            "sources":            sources,
            "total":              len(concerts_list),
            "duplicates_removed": duplicates,
            "concerts":           concerts_list,
            "genres":             genres_list,
            "venues":             venues_list,
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        cleanup_backup(has_backup, bak, log)
        log.info(f"Résultat : {len(concerts_list)} concerts ({duplicates} doublons supprimés)")
        log.info(f"Fichier généré : {output_file}")

    except Exception as e:
        log.error(f"Erreur lors de la fusion JSON : {e}", exc_info=True)
        restore(has_backup, bak, output_file, log)
        raise


# ──────────────────────────────────────────────
# CSV
# ──────────────────────────────────────────────

def merge_csv(input_dir: str, output_file: str, log: logging.Logger) -> None:
    csv_files = sorted(
        f for f in os.listdir(input_dir) if f.endswith(".csv")
    )
    if not csv_files:
        log.warning(f"Aucun fichier CSV trouvé dans {input_dir}")
        return

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    has_backup, bak = backup(output_file, log)
    load_cache_from_bak(bak, "csv", log)

    try:
        merged_concerts = {}   # dedup_key -> row dict
        fieldnames      = None
        duplicates      = 0

        for filename in csv_files:
            filepath = os.path.join(input_dir, filename)
            log.info(f"Traitement : {filename}")
            with open(filepath, encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                if fieldnames is None:
                    fieldnames = list(reader.fieldnames or [])
                    for extra in ("preview_url", "preview_url1"):
                        if extra not in fieldnames:
                            fieldnames.append(extra)

                for row in reader:
                    key = dedup_key(row)
                    if key in merged_concerts:
                        duplicates += 1
                        log.debug(f"Doublon ignoré : {row.get('artist','?')} le {key[1]}")
                    else:
                        artist = row.get("artist", "")
                        preview_url, preview_url1 = get_top2_previews(artist)
                        log.debug(f"Deezer preview : {artist} → {preview_url or 'introuvable'}")
                        row["preview_url"]  = preview_url
                        row["preview_url1"] = preview_url1
                        merged_concerts[key] = row

        concerts_list = sorted(
            merged_concerts.values(),
            key=lambda r: (r.get("date_live", ""), r.get("artist", ""))
        )

        with open(output_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(concerts_list)

        cleanup_backup(has_backup, bak, log)
        log.info(f"Résultat : {len(concerts_list)} concerts ({duplicates} doublons supprimés)")
        log.info(f"Fichier généré : {output_file}")

    except Exception as e:
        log.error(f"Erreur lors de la fusion CSV : {e}", exc_info=True)
        restore(has_backup, bak, output_file, log)
        raise


# ──────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fusionne les fichiers de concerts JSON ou CSV."
    )
    parser.add_argument(
        "-f", "--format",
        choices=["json", "csv"],
        required=True,
        help="Format des fichiers à fusionner : json ou csv"
    )
    args = parser.parse_args()

    log = setup_logger(args.format)
    log.info("=" * 60)
    log.info(f"Démarrage du merge (format={args.format})")

    try:
        if args.format == "json":
            merge_json(
                input_dir   = os.path.join(BASE_DIR, "JSON"),
                output_file = os.path.join(OUT_DIR, "concerts.json"),
                log         = log,
            )
        else:
            merge_csv(
                input_dir   = os.path.join(BASE_DIR, "CSV"),
                output_file = os.path.join(OUT_DIR, "concerts.csv"),
                log         = log,
            )
        log.info("Merge terminé avec succès")

    except Exception as e:
        log.critical(f"Merge échoué : {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
