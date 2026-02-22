# merge.py

Fusionne les fichiers JSON ou CSV générés par les scrapers en un seul fichier de sortie, en supprimant les doublons et en enrichissant chaque concert avec les IDs de pistes Deezer.

## Fonctionnement

### Fusion et déduplication

Le script lit tous les fichiers `.json` (depuis `JSON/`) ou `.csv` (depuis `CSV/`) et les concatène. La déduplication est effectuée sur la paire **(artiste normalisé, date_live)** : si un même concert apparaît dans plusieurs sources (ex: concert co-organisé Rockhal + Atelier), seule la première occurrence est conservée.

### Enrichissement Deezer

Pour chaque concert unique, l'API Deezer est interrogée afin de récupérer les **deux meilleures pistes** de l'artiste (triées par rang de popularité) :

- `track_id` — ID de la piste la plus populaire
- `track_id1` — ID de la deuxième piste

Les résultats sont mis en **cache en mémoire** par nom d'artiste pour éviter les appels redondants. Si un artiste était déjà connu dans le fichier de sortie précédent (`.bak`), ses IDs sont directement réutilisés sans appel API.

### Préservation de `date_created`

Pour les concerts qui existaient déjà dans le fichier précédent, la valeur originale de `date_created` (horodatage du premier scan) est restaurée depuis le backup, garantissant la traçabilité de l'historique.

### Sauvegarde et restauration

Avant toute écriture, le fichier de sortie existant est copié en `.bak`. En cas d'erreur pendant la fusion, le backup est automatiquement restauré. En cas de succès, le backup est supprimé.

## Sorties

| Répertoire | Fichier           | Format |
|-----------|-------------------|--------|
| `OUT/`    | `concerts.json`   | JSON   |
| `OUT/`    | `concerts.csv`    | CSV    |
| `Log/`    | `merge.log`       | Log    |

### Structure du JSON de sortie

```json
{
  "scraped_at": "2026-02-22T10:00:00",
  "sources": ["https://...", "https://..."],
  "total": 42,
  "duplicates_removed": 3,
  "concerts": [ ... ],
  "genres": [ ... ],
  "venues": [ ... ]
}
```

### Champs ajoutés par le merge

| Champ        | Description                                   |
|--------------|-----------------------------------------------|
| `track_id`   | ID Deezer de la piste la plus populaire       |
| `track_id1`  | ID Deezer de la deuxième piste populaire      |

Tous les champs originaux des scrapers sont conservés (voir README des scrapers).

## Usage

```bash
# Fusionner les fichiers JSON
python merge.py -f json

# Fusionner les fichiers CSV
python merge.py -f csv
```

### Options CLI

| Option          | Description                                   | Requis |
|-----------------|-----------------------------------------------|--------|
| `-f`, `--format` | Format des fichiers à fusionner : `json` ou `csv` | Oui    |

## Dépendances

Ce script nécessite la librairie externe **`requests`**.

```
requests>=2.28.0
```

Installer via pip :

```bash
pip install requests
```

Ou via le fichier `requirements.txt` :

```bash
pip install -r requirements.txt
```

- Python 3.10+

## Installation

Voir [`install.sh`](install.sh) pour la mise en place d'un environnement virtuel.

## Workflow recommandé

```bash
# 1. Scraper les deux sources
python scrape_atelier_concerts.py -f json
python scrape_rockhal_concerts.py -f json

# 2. Fusionner en un seul fichier
python merge.py -f json
# → OUT/concerts.json contient tous les concerts dédupliqués + track_ids Deezer
```
