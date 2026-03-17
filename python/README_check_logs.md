# check_logs.py — Alerte email en cas d'erreur

Analyse les fichiers de log après chaque exécution des scrapers et envoie une alerte email si de nouvelles lignes `[ERROR]` sont détectées.

## Fonctionnement

Le script effectue deux vérifications indépendantes à chaque exécution :

**1. Analyse des logs**
- Lit `Log/.alert_state.json` pour connaître la position de la dernière lecture dans chaque fichier `.log`
- Parcourt les nouvelles lignes uniquement (depuis l'offset mémorisé)
- Filtre les lignes `[ERROR]`
- Envoie un email si de nouvelles erreurs sont trouvées
- Met à jour l'état — les erreurs déjà alertées ne seront jamais renvoyées
- Si un fichier de log a été purgé entre deux exécutions, repart automatiquement depuis le début

**2. Vérification de synchronisation du JSON (optionnel)**
- Calcule le MD5 du fichier local `OUT/concerts.json`
- Télécharge et calcule le MD5 du fichier servi par le site (`/IN/concerts.json`)
- Envoie une alerte si les deux fichiers diffèrent (problème de copie ou de déploiement)

## Usage

```bash
python check_logs.py \
  --email-from alertes@example.com \
  --email-to admin@example.com \
  --smtp-host smtp.example.com \
  --smtp-user alertes@example.com \
  --smtp-password secret \
  --web-json-url https://infoconcert.lu/IN/concerts.json
```

### Arguments

| Argument | Obligatoire | Défaut | Description |
|----------|-------------|--------|-------------|
| `--email-from` | Oui | — | Adresse expéditeur |
| `--email-to` | Oui | — | Adresse destinataire (répétable pour plusieurs) |
| `--smtp-host` | Oui | — | Serveur SMTP (ex: `smtp.example.com`) |
| `--smtp-port` | Non | `587` | Port SMTP |
| `--smtp-user` | Non | — | Login SMTP |
| `--smtp-password` | Non | — | Mot de passe SMTP |
| `--smtp-ssl` | Non | — | Utiliser SSL direct (port 465) au lieu de STARTTLS |
| `--web-json-url` | Non | — | URL du `concerts.json` servi par le site web à comparer avec `OUT/concerts.json` |
| `--test` | Non | — | Envoie un email de test et quitte (vérifie que les paramètres SMTP sont corrects) |

### Vérifier la configuration email

```bash
python check_logs.py \
  --email-from alertes@example.com \
  --email-to admin@example.com \
  --smtp-host smtp.example.com \
  --smtp-user alertes@example.com \
  --smtp-password secret \
  --test
```

Envoie un email de test immédiatement et quitte. Le corps de l'email récapitule tous les paramètres SMTP utilisés. Aucune analyse de log n'est effectuée.

### Plusieurs destinataires

Répéter `--email-to` autant de fois que nécessaire :

```bash
python check_logs.py \
  --email-from alertes@example.com \
  --email-to admin@example.com \
  --email-to autre@example.com \
  --smtp-host smtp.example.com
```

### SSL direct (port 465)

```bash
python check_logs.py \
  --email-from alertes@example.com \
  --email-to admin@example.com \
  --smtp-host smtp.example.com \
  --smtp-port 465 \
  --smtp-ssl
```

## Fichier d'état

`Log/.alert_state.json` — créé automatiquement à la première exécution.

```json
{
  "scrape_atelier_concerts.log": 4096,
  "scrape_rockhal_concerts.log": 2048,
  "merge.log": 512
}
```

Chaque valeur est la position en octets jusqu'où le fichier a été lu. Ne pas supprimer ce fichier manuellement sauf pour forcer une ré-analyse complète.

---

## Intégration dans run_scripts.sh

Ajouter à la fin du script, après la copie du fichier JSON :

```bash
# Vérification des logs et alerte email si nouvelles erreurs
$PYTHON "$SCRIPTS_PATH/check_logs.py" \
  --email-from alertes@example.com \
  --email-to admin@example.com \
  --smtp-host smtp.example.com \
  --smtp-user alertes@example.com \
  --smtp-password secret \
  --web-json-url https://infoconcert.lu/IN/concerts.json
```

---

## Exemples d'emails reçus

**Erreurs de scraping :**
```
Sujet : ⚠️ infoconcert.lu — 2 erreur(s) — 2026-03-16 15:00

▸ scrape_rockhal_concerts
  [ERROR] HTTP 503 après 3 tentatives — https://rockhal.lu/events

▸ merge
  [ERROR] Fichier JSON manquant : scrape_lenox_concerts.json
```

**JSON différent entre Serveur et Web :**
```
Sujet : 🔴 infoconcert.lu — JSON différent entre Serveur et Web — 2026-03-16 15:00

JSON différent entre Serveur et Web
  Local  (1243581 octets) : a3f2c1d4e5b6...
  Web                     : 9f8e7d6c5b4a...
  URL : https://infoconcert.lu/IN/concerts.json
```

---

## Dépendances

Aucune dépendance externe. Utilise uniquement la bibliothèque standard Python :
- `smtplib`, `email.mime.text` — envoi email
- `urllib.request` — vérification MD5 du JSON web
- `json`, `os`, `glob`, `argparse`, `datetime`
