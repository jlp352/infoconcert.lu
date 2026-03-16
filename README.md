# infoconcert.lu

**infoConcert.lu** est un agrégateur de concerts au Luxembourg. Le projet collecte automatiquement les événements musicaux publiés par les principales salles du Luxembourg et les restitue dans une interface web simple, moderne et multilingue.

Le projet se compose de deux parties indépendantes :

- **Backend Python** : des scrapers interrogent les APIs REST et les pages HTML des salles pour extraire les informations de chaque concert (artiste, date, salle, prix, statut de billetterie, genres musicaux, image). Les données des sources sont ensuite fusionnées, dédupliquées et enrichies avec les identifiants de pistes [Deezer](https://www.deezer.com/) pour permettre la lecture d'extraits audio.

- **Frontend Web** : une page HTML/CSS/JS sans framework charge le fichier JSON produit par les scrapers et propose une expérience de navigation complète : filtres par mois, genre et salle, recherche d'artiste en temps réel, lecteur audio intégré, notifications de nouveaux concerts, et support FR / EN / DE.

## Structure du projet

```
infoconcert.lu/
├── python/                                        # Scripts de scraping et de traitement
│   ├── scrape_atelier_concerts.py                 # Scraper Atelier.lu
│   ├── scrape_rockhal_concerts.py                 # Scraper Rockhal.lu
│   ├── scrape_casino2000_concerts.py              # Scraper Casino 2000
│   ├── scrape_kulturfabrik_concerts.py            # Scraper Kulturfabrik
│   ├── scrape_philharmonie_concerts.py            # Scraper Philharmonie Luxembourg
│   ├── scrape_echo_lu_concerts.py                 # 9 centres culturels (API Firestore)
│   ├── scrape_entrepot_concerts.py                # Scraper L'Entrepôt Arlon (BE)
│   ├── scrape_arche_villerupt_concerts.py         # Scraper L'Arche Villerupt (FR)
│   ├── scrape_citemusicale_metz_concerts.py       # Scraper Cité Musicale Metz (FR)
│   ├── scrape_galaxie_amneville_concerts.py       # Scraper Galaxie Amnéville (FR)
│   ├── scrape_mergener_hof_trier_concerts.py      # Scraper Mergener Hof Trier (DE)
│   ├── scrape_forum_trier_concerts.py             # Scraper Forum Trier (DE)
│   ├── scrape_gueulard_nilvange_concerts.py       # Scraper Le Gueulard Nilvange (FR)
│   ├── scrape_lenox_concerts.py                   # Scraper Lenox Club Luxembourg
│   ├── merge.py                                   # Fusion + enrichissement Deezer
│   ├── purgelog.py                                # Nettoyage des fichiers de log
│   ├── requirements.txt                           # Dépendances Python
│   ├── install.sh                                 # Script d'installation
│   ├── JSON/                                      # Sorties JSON des scrapers
│   ├── CSV/                                       # Sorties CSV des scrapers
│   ├── OUT/                                       # Fichiers fusionnés (json ou csv)
│   └── Log/                                       # Fichiers de log
└── Web/                                           # Interface web
│   ├── index.html                 # Page principale
│   ├── contact.html               # Page Contact
│   ├── legalnotice.html           # Page légale pour un site Web
│   ├── venues.html                # Page des salles de concert au Luxembourg
│   ├── image/                     # Images necessaires au site (Logo + Location)
│   ├── IN/                        # Fichier fusionné utilisé (concerts.json)
```

## Installation

### Prérequis

- [Git](https://git-scm.com/)
- [Python 3.10+](https://www.python.org/downloads/)
- `bash` (Linux, macOS, ou WSL sous Windows)

### 1. Cloner le dépôt

```bash
git clone https://github.com/jlp352/infoconcert.lu.git
cd infoconcert.lu
```

### 2. Lancer le script d'installation

```bash
cd python
chmod +x install.sh
./install.sh
```

Le script effectue automatiquement :

1. Vérifie que Python 3.10+ est disponible
2. Crée un environnement virtuel `.venv`
3. Active l'environnement virtuel
4. Installe les dépendances (`requests`)
5. Crée les sous-dossiers de travail (`JSON/`, `CSV/`, `Log/`, `OUT/`)

### 3. Activer l'environnement virtuel

```bash
source .venv/bin/activate
```

## Utilisation

### Scraper les concerts

```bash
# Atelier.lu
python scrape_atelier_concerts.py

# Rockhal.lu
python scrape_rockhal_concerts.py

# Casino 2000
python scrape_casino2000_concerts.py

# Kulturfabrik
python scrape_kulturfabrik_concerts.py

# Philharmonie Luxembourg
python scrape_philharmonie_concerts.py

# 9 centres culturels via Portail echo.lu
#     Aalt Stadhaus     Differdange
#     Cube 521          Marnach
#     CAPE              Ettelbruck
#     Kinneksbond       Mamer
#     Mierscher Theater Mersch
#     opderschmelz      Dudelange
#     Artikuss          Soleuvre
#     Prabbeli          Wiltz
#     Maacher           Grevenmacher
python scrape_echo_lu_concerts.py

# L'Entrepôt Arlon (Belgique)
python scrape_entrepot_concerts.py

# L'Arche Villerupt (France)
python scrape_arche_villerupt_concerts.py

# Cité Musicale Metz (France)
python scrape_citemusicale_metz_concerts.py

# Galaxie Amnéville (France)
python scrape_galaxie_amneville_concerts.py

# Mergener Hof Trier (Allemagne)
python scrape_mergener_hof_trier_concerts.py

# Forum Trier (Allemagne)
python scrape_forum_trier_concerts.py

# Le Gueulard Nilvange (France)
python scrape_gueulard_nilvange_concerts.py

# Lenox Club Luxembourg
python scrape_lenox_concerts.py
```

### Fusionner les données

```bash
python merge.py -f json
```

Le fichier `OUT/concerts.json` contiendra tous les concerts dédupliqués, enrichis avec les IDs de pistes Deezer.

### Workflow complet recommandé

```bash
python scrape_atelier_concerts.py
python scrape_rockhal_concerts.py
python scrape_casino2000_concerts.py
python scrape_kulturfabrik_concerts.py
python scrape_philharmonie_concerts.py
python scrape_echo_lu_concerts.py
python scrape_entrepot_concerts.py
python scrape_arche_villerupt_concerts.py
python scrape_citemusicale_metz_concerts.py
python scrape_galaxie_amneville_concerts.py
python scrape_mergener_hof_trier_concerts.py
python scrape_forum_trier_concerts.py
python scrape_gueulard_nilvange_concerts.py
python scrape_lenox_concerts.py
python merge.py -f json
```

## Scripts Python

| Script | Description | Doc |
|---|---|---|
| `scrape_atelier_concerts.py` | Scrape les concerts de Atelier.lu (API + pages HTML) | [README](python/README_scrape_atelier_concerts.md) |
| `scrape_rockhal_concerts.py` | Scrape les concerts de Rockhal.lu (API + pages HTML) | [README](python/README_scrape_rockhal_concerts.md) |
| `scrape_casino2000_concerts.py` | Scrape les concerts de Casino 2000 (pages HTML) | [README](python/README_scrape_casino2000_concerts.md) |
| `scrape_kulturfabrik_concerts.py` | Scrape les concerts de la Kulturfabrik (pages HTML) | [README](python/README_scrape_kulturfabrik_concerts.md) |
| `scrape_philharmonie_concerts.py` | Scrape les concerts de la Philharmonie Luxembourg (pages HTML) | [README](python/README_scrape_philharmonie_concerts.md) |
| `scrape_echo_lu_concerts.py` | Scrape 9 centres culturels via echo.lu (API Firestore) | [README](python/README_scrape_echo_lu_concerts.md) |
| `scrape_entrepot_concerts.py` | Scrape les concerts de L'Entrepôt Arlon — BE (pages HTML) | [README](python/README_scrape_entrepot_concerts.md) |
| `scrape_arche_villerupt_concerts.py` | Scrape les concerts de L'Arche Villerupt — FR (pages HTML) | [README](python/README_scrape_arche_villerupt_concerts.md) |
| `scrape_citemusicale_metz_concerts.py` | Scrape les concerts de la Cité Musicale Metz — FR (pages HTML) | [README](python/README_scrape_citemusicale_metz_concerts.md) |
| `scrape_galaxie_amneville_concerts.py` | Scrape les concerts de Galaxie Amnéville — FR (pages HTML) | [README](python/README_scrape_galaxie_amneville_concerts.md) |
| `scrape_mergener_hof_trier_concerts.py` | Scrape les concerts du Mergener Hof Trier — DE (pages HTML) | [README](python/README_scrape_mergener_hof_trier_concerts.md) |
| `scrape_forum_trier_concerts.py` | Scrape les concerts du Forum Trier — DE (pages HTML) | [README](python/README_scrape_forum_trier_concerts.md) |
| `scrape_gueulard_nilvange_concerts.py` | Scrape les concerts du Gueulard Nilvange — FR (API WordPress) | [README](python/README_scrape_gueulard_nilvange_concerts.md) |
| `scrape_lenox_concerts.py` | Scrape les concerts du Lenox Club Luxembourg (RSC/xceed.me) | [README](python/README_scrape_lenox_concerts.md) |
| `merge.py` | Fusionne les sorties des scrapers et enrichit avec Deezer | [README](python/README_merge.md) |
| `purgelog.py` | Nettoie les fichiers de log anciens | — |
| `check_logs.py` | Analyse les logs et envoie une alerte ntfy si nouvelles erreurs | [README](python/README_check_logs.md) |

## Site Web

Le site web est une application statique (HTML/CSS/JS) ne nécessitant aucun framework ni serveur applicatif. Il suffit de servir le dossier `Web/` via un serveur HTTP.

### Prérequis

Le fichier `Web/IN/concerts.json` doit être présent avant de lancer le serveur. Il est généré par le workflow Python (voir section [Workflow complet recommandé](#workflow-complet-recommandé)).

### Lancer le serveur web en local

Depuis la racine du projet :

```bash
cd Web
python3 -m http.server 8000
```

Le site est alors accessible à l'adresse : [http://localhost:8000](http://localhost:8000)

Pour écouter sur toutes les interfaces réseau (accès depuis d'autres machines du réseau local) :

```bash
python3 -m http.server 8000 --bind 0.0.0.0
```

### Lancer le serveur sur un port différent

```bash
python3 -m http.server 8080
```

### Pages disponibles

| Page | URL | Description |
|---|---|---|
| Accueil | `/index.html` | Liste de tous les concerts à venir |
| Salles | `/venues.html` | Présentation des salles de concert |
| Contact | `/contact.html` | Formulaire de contact |
| Mentions légales | `/legalnotice.html` | Cookies, CGU, RGPD |

> Pour la documentation complète du site web, voir [website.md](website.md).

## Automatisation (CRON)

Pour automatiser l'exécution sur un serveur Linux, configurer les tâches suivantes via `crontab -e` :

```cron
# Purge des logs au démarrage
@reboot /usr/bin/python3 /home/user/infoconcert.lu/python/purgelog.py

# Scraping + fusion toutes les heures
0 * * * * /home/user/infoconcert.lu/run_scripts.sh

# Serveur web au démarrage
@reboot cd /home/user/infoconcert.lu/Web && /usr/bin/python3 -m http.server 8000 --bind 0.0.0.0
```

Appliquer les permissions d'exécution au préalable :

```bash
chmod +x $HOME/infoconcert.lu/run_scripts.sh
```

### Contenu de `run_scripts.sh`

Script à placer à la racine du projet (`/home/user/infoconcert.lu/run_scripts.sh`) :

```bash
#!/bin/bash
# Lancer les scripts Python pour le scraping et la fusion en séquence

# Chemin vers le dossier contenant les scripts Python
SCRIPTS_PATH="$HOME/infoconcert.lu/python"

# Chemin vers le dossier d'entrée du site Web
SCRIPTS_PATH_WEB="$HOME/infoconcert.lu/Web/IN"

# Chemin vers Python (environnement virtuel)
PYTHON="$HOME/infoconcert.lu/python/.venv/bin/python3"

# Atelier.lu — exclure les genres Party/Film et les concerts annulés
$PYTHON "$SCRIPTS_PATH/scrape_atelier_concerts.py" -g "Party; Film" -s "cancelled"

# Rockhal.lu — exclure les concerts jeune public et les concerts annulés
$PYTHON "$SCRIPTS_PATH/scrape_rockhal_concerts.py" -g "Kids/Young Audience" -s "cancelled"

# Casino 2000
$PYTHON "$SCRIPTS_PATH/scrape_casino2000_concerts.py"

# Kulturfabrik
$PYTHON "$SCRIPTS_PATH/scrape_kulturfabrik_concerts.py"

# Philharmonie Luxembourg
$PYTHON "$SCRIPTS_PATH/scrape_philharmonie_concerts.py"

# echo.lu — 9 centres culturels via Firestore
$PYTHON "$SCRIPTS_PATH/scrape_echo_lu_concerts.py"

# L'Entrepôt Arlon (Belgique)
$PYTHON "$SCRIPTS_PATH/scrape_entrepot_concerts.py"

# L'Arche Villerupt (France)
$PYTHON "$SCRIPTS_PATH/scrape_arche_villerupt_concerts.py"

# Cité Musicale Metz (France)
$PYTHON "$SCRIPTS_PATH/scrape_citemusicale_metz_concerts.py"

# Galaxie Amnéville (France)
$PYTHON "$SCRIPTS_PATH/scrape_galaxie_amneville_concerts.py"

# Mergener Hof Trier (Allemagne)
$PYTHON "$SCRIPTS_PATH/scrape_mergener_hof_trier_concerts.py"

# Forum Trier (Allemagne)
$PYTHON "$SCRIPTS_PATH/scrape_forum_trier_concerts.py"

# Le Gueulard Nilvange (France)
$PYTHON "$SCRIPTS_PATH/scrape_gueulard_nilvange_concerts.py"

# Lenox Club Luxembourg
$PYTHON "$SCRIPTS_PATH/scrape_lenox_concerts.py"

# Fusion de toutes les sources
$PYTHON "$SCRIPTS_PATH/merge.py" -f json

# Copie du fichier fusionné vers le dossier du site Web
cp "$SCRIPTS_PATH/OUT/concerts.json" "$SCRIPTS_PATH_WEB/concerts.json"

# Vérification des logs et synchronisation JSON — alerte ntfy si problème
$PYTHON "$SCRIPTS_PATH/check_logs.py" \
  --ntfy-url https://ntfy.exemple.com/infoconcert \
  --ntfy-token tk_abc123xyz \
  --web-json-url https://infoconcert.lu/IN/concerts.json
```

## Dépendances

- Python 3.10+
- `requests >= 2.28.0` (pour `merge.py` uniquement)

Les scrapers utilisent uniquement la bibliothèque standard Python.
