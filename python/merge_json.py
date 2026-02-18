"""
Concatène tous les fichiers JSON du dossier JSON/ en un seul fichier.
Les doublons sont détectés uniquement sur (artist, date_live).
Le fichier de sortie est OUT/concerts.json.
En cas d'erreur, l'ancien fichier est restauré.
"""

import json
import os
import shutil
from datetime import datetime

INPUT_DIR = os.path.join(os.path.dirname(__file__), "JSON")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "OUT")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "concerts.json")


def load_json_file(filepath: str) -> dict:
    with open(filepath, encoding="utf-8") as f:
        return json.load(f)


def merge_json_files(input_dir: str, output_file: str) -> None:
    # Création du dossier OUT si nécessaire
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    json_files = [
        f for f in os.listdir(input_dir)
        if f.endswith(".json")
    ]

    if not json_files:
        print("Aucun fichier JSON trouvé dans", input_dir)
        return

    # Sauvegarde de l'ancien fichier pour rollback en cas d'erreur
    backup_file = output_file + ".bak"
    has_backup = False
    if os.path.exists(output_file):
        shutil.copy2(output_file, backup_file)
        has_backup = True
        os.remove(output_file)
        print(f"Ancien fichier sauvegardé : {backup_file}")

    try:
        # clé de déduplication : (artist normalisé, date_live)
        merged_concerts = {}   # (artist, date_live) -> concert
        merged_genres = {}     # id -> genre dict
        merged_venues = {}     # id -> venue dict
        sources = []
        duplicates = 0

        for filename in sorted(json_files):
            filepath = os.path.join(input_dir, filename)
            print(f"Traitement : {filename}")
            data = load_json_file(filepath)

            source = data.get("source", filename)
            sources.append(source)

            for concert in data.get("concerts", []):
                artist = (concert.get("artist") or "").strip().lower()
                date_live = (concert.get("date_live") or "").strip()
                key = (artist, date_live)

                if key in merged_concerts:
                    duplicates += 1
                    print(
                        f"  Doublon ignoré : {concert.get('artist', '?')} "
                        f"le {date_live}"
                    )
                else:
                    merged_concerts[key] = concert

            for genre in data.get("genres", []):
                genre_id = genre.get("id") if isinstance(genre, dict) else genre
                if genre_id not in merged_genres:
                    merged_genres[genre_id] = genre

            for venue in data.get("venues", []):
                venue_id = venue.get("id") if isinstance(venue, dict) else venue
                if venue_id not in merged_venues:
                    merged_venues[venue_id] = venue

        concerts_list = list(merged_concerts.values())
        concerts_list.sort(key=lambda c: (c.get("date_live", ""), c.get("artist", "")))

        genres_list = sorted(
            merged_genres.values(),
            key=lambda g: g.get("name", "") if isinstance(g, dict) else g
        )
        venues_list = sorted(
            merged_venues.values(),
            key=lambda v: v.get("name", "") if isinstance(v, dict) else v
        )

        output = {
            "scraped_at": datetime.now().isoformat(),
            "sources": sources,
            "total": len(concerts_list),
            "duplicates_removed": duplicates,
            "concerts": concerts_list,
            "genres": genres_list,
            "venues": venues_list,
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        # Succès : suppression du backup
        if has_backup and os.path.exists(backup_file):
            os.remove(backup_file)

        print(f"\nRésultat : {len(concerts_list)} concerts ({duplicates} doublons supprimés)")
        print(f"Fichier généré : {output_file}")

    except Exception as e:
        print(f"\nERREUR : {e}")
        # Rollback : restauration de l'ancien fichier
        if has_backup and os.path.exists(backup_file):
            shutil.copy2(backup_file, output_file)
            os.remove(backup_file)
            print(f"Ancien fichier restauré : {output_file}")
        elif os.path.exists(output_file):
            os.remove(output_file)
        raise


if __name__ == "__main__":
    merge_json_files(INPUT_DIR, OUTPUT_FILE)
