#!/usr/bin/env bash
# =============================================================================
# install.sh — Installation de l'environnement virtuel pour infoconcert.lu
# =============================================================================
# Usage :
#   chmod +x install.sh
#   ./install.sh
#
# Ce script :
#   1. Vérifie que Python 3.10+ est disponible
#   2. Crée un environnement virtuel .venv dans le dossier courant
#   3. Active l'environnement virtuel
#   4. Installe les dépendances depuis requirements.txt
#   5. Crée les sous-dossiers nécessaires (JSON, CSV, Log, OUT)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
REQUIREMENTS="$SCRIPT_DIR/requirements.txt"

# -----------------------------------------------------------------------------
# 1. Vérification de Python
# -----------------------------------------------------------------------------
echo ">>> Vérification de Python..."

PYTHON_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        version=$("$candidate" -c "import sys; print(sys.version_info[:2])")
        major=$("$candidate" -c "import sys; print(sys.version_info.major)")
        minor=$("$candidate" -c "import sys; print(sys.version_info.minor)")
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON_BIN="$candidate"
            echo "    Python $major.$minor trouvé : $(command -v $candidate)"
            break
        else
            echo "    $candidate $major.$minor ignoré (Python 3.10+ requis)"
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "ERREUR : Python 3.10 ou supérieur introuvable."
    echo "         Installez Python depuis https://www.python.org/downloads/"
    exit 1
fi

# -----------------------------------------------------------------------------
# 2. Création de l'environnement virtuel
# -----------------------------------------------------------------------------
if [ -d "$VENV_DIR" ]; then
    echo ">>> Environnement virtuel existant trouvé : $VENV_DIR"
    echo "    Pour le recréer, supprimez le dossier .venv puis relancez ce script."
else
    echo ">>> Création de l'environnement virtuel dans .venv ..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    echo "    Environnement virtuel créé."
fi

# -----------------------------------------------------------------------------
# 3. Activation de l'environnement virtuel
# -----------------------------------------------------------------------------
echo ">>> Activation de l'environnement virtuel..."
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# -----------------------------------------------------------------------------
# 4. Installation des dépendances
# -----------------------------------------------------------------------------
echo ">>> Mise à jour de pip..."
pip install --upgrade pip --quiet

if [ -f "$REQUIREMENTS" ]; then
    echo ">>> Installation des dépendances depuis requirements.txt ..."
    pip install -r "$REQUIREMENTS"
    echo "    Dépendances installées."
else
    echo "AVERTISSEMENT : requirements.txt introuvable — aucune dépendance installée."
fi

# -----------------------------------------------------------------------------
# 5. Création des sous-dossiers de travail
# -----------------------------------------------------------------------------
echo ">>> Création des sous-dossiers de travail..."
for dir in JSON CSV Log OUT; do
    mkdir -p "$SCRIPT_DIR/$dir"
    echo "    $dir/"
done

# -----------------------------------------------------------------------------
# Résumé
# -----------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "  Installation terminée."
echo "============================================================"
echo ""
echo "  Pour activer l'environnement virtuel dans votre shell :"
echo "    source .venv/bin/activate"
echo ""
echo "  Exemples d'utilisation :"
echo "    python scrape_atelier_concerts.py"
echo "    python scrape_rockhal_concerts.py"
echo "    python scrape_casino2000_concerts.py"
echo "    python scrape_kulturfabrik_concerts.py"
echo "    python scrape_philharmonie_concerts.py"
echo "    python scrape_echo_lu_concerts.py"
echo "    python scrape_entrepot_concerts.py"
echo "    python scrape_arche_villerupt_concerts.py"
echo "    python scrape_citemusicale_metz_concerts.py"
echo "    python scrape_galaxie_amneville_concerts.py"
echo "    python scrape_mergener_hof_trier_concerts.py"
echo "    python scrape_forum_trier_concerts.py"
echo "    python scrape_gueulard_nilvange_concerts.py"
echo "    python scrape_lenox_concerts.py"
echo "    python merge.py -f json"
echo "    python ftp_upload.py <fichier> --host <host> --user <user> --password <password>"
echo "    python check_logs.py --email-from alertes@example.com --email-to admin@example.com --smtp-host smtp.example.com --test"
echo ""
echo "  Pour désactiver l'environnement virtuel :"
echo "    deactivate"
echo "============================================================"
