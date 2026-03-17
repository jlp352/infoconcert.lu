#!/usr/bin/env python3
"""
check_logs.py — Analyse les fichiers de log et envoie une alerte email si
de nouvelles lignes [ERROR] sont détectées depuis la dernière exécution.
Vérifie également que le fichier concerts.json du site web est identique
au fichier local OUT/concerts.json.

Usage :
    python check_logs.py \
        --email-from alertes@example.com \
        --email-to admin@example.com \
        --smtp-host smtp.example.com \
        --smtp-user alertes@example.com \
        --smtp-password secret \
        --web-json-url https://infoconcert.lu/IN/concerts.json

Fichier d'état : Log/.alert_state.json
  Stocke la position en octets (offset) de la dernière lecture pour chaque
  fichier .log. Seules les nouvelles lignes sont analysées à chaque exécution.
"""

import argparse
import glob
import hashlib
import json
import os
import smtplib
import sys
import urllib.request
import urllib.error
from datetime import datetime
from email.mime.text import MIMEText

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Log")
STATE_FILE = os.path.join(LOG_DIR, ".alert_state.json")
OUT_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "OUT", "concerts.json")
ERROR_LEVEL = "[ERROR]"


# ---------------------------------------------------------------------------
# État persistant
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_state(state: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


# ---------------------------------------------------------------------------
# Analyse des logs
# ---------------------------------------------------------------------------

def scan_log(path: str, offset: int) -> tuple[list[str], int]:
    """
    Lit le fichier depuis `offset` octets, retourne les nouvelles lignes
    [ERROR] et le nouvel offset.
    Si le fichier est plus petit que l'offset (ex: purgé/rotaté), repart de 0.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], offset

    # Fichier purgé ou rotaté
    if size < offset:
        offset = 0

    errors = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            for line in f:
                if ERROR_LEVEL in line:
                    errors.append(line.rstrip())
            new_offset = f.tell()
    except OSError:
        return [], offset

    return errors, new_offset


def collect_errors(state: dict) -> tuple[dict, dict]:
    """
    Parcourt tous les .log du dossier Log/.
    Retourne :
      - errors_by_file : {nom_fichier: [lignes erreur]}
      - new_state      : offsets mis à jour
    """
    log_files = sorted(glob.glob(os.path.join(LOG_DIR, "*.log")))
    errors_by_file = {}
    new_state = {}

    for path in log_files:
        name = os.path.basename(path)
        offset = state.get(name, 0)
        errors, new_offset = scan_log(path, offset)
        new_state[name] = new_offset
        if errors:
            errors_by_file[name] = errors

    return errors_by_file, new_state


# ---------------------------------------------------------------------------
# Vérification de synchronisation du fichier JSON web
# ---------------------------------------------------------------------------

def md5_of_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def md5_of_url(url: str) -> str | None:
    """Télécharge le contenu de l'URL et retourne son MD5."""
    req = urllib.request.Request(url, headers={"User-Agent": "infoconcert-check/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            h = hashlib.md5()
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                h.update(chunk)
            return h.hexdigest()
    except urllib.error.HTTPError as e:
        print(f"[check_logs] JSON web — Erreur HTTP {e.code} : {url}", file=sys.stderr)
    except urllib.error.URLError as e:
        print(f"[check_logs] JSON web — URL inaccessible : {e.reason}", file=sys.stderr)
    except Exception as e:
        print(f"[check_logs] JSON web — Erreur inattendue : {e}", file=sys.stderr)
    return None


def check_json_sync(web_url: str) -> str | None:
    """
    Compare le MD5 du fichier local OUT/concerts.json avec celui servi par le site.
    Retourne un message d'alerte si les fichiers diffèrent, None si identiques.
    """
    if not os.path.exists(OUT_JSON):
        return f"Fichier local introuvable : {OUT_JSON}"

    local_md5 = md5_of_file(OUT_JSON)
    print(f"[check_logs] JSON local  MD5 : {local_md5}")

    web_md5 = md5_of_url(web_url)
    if web_md5 is None:
        return f"Impossible de récupérer le JSON depuis {web_url}"

    print(f"[check_logs] JSON web    MD5 : {web_md5}")

    if local_md5 != web_md5:
        local_size = os.path.getsize(OUT_JSON)
        return (
            f"JSON différent entre Serveur et Web\n"
            f"  Local  ({local_size} octets) : {local_md5}\n"
            f"  Web                        : {web_md5}\n"
            f"  URL : {web_url}"
        )

    print("[check_logs] JSON web identique au fichier local.")
    return None


# ---------------------------------------------------------------------------
# Notification email
# ---------------------------------------------------------------------------

def build_message(errors_by_file: dict) -> str:
    lines = []
    for filename, errs in errors_by_file.items():
        label = filename.replace(".log", "")
        lines.append(f"▸ {label}")
        for err in errs:
            idx = err.find(ERROR_LEVEL)
            lines.append(f"  {err[idx:].strip()}")
    return "\n".join(lines)


def send_email(smtp_host: str, smtp_port: int, smtp_user: str, smtp_password: str,
               from_addr: str, to_addrs: list[str], subject: str, body: str,
               use_ssl: bool = False) -> bool:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)

    try:
        if use_ssl:
            smtp = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10)
        else:
            smtp = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
            smtp.starttls()

        if smtp_user:
            smtp.login(smtp_user, smtp_password)

        smtp.sendmail(from_addr, to_addrs, msg.as_string())
        smtp.quit()
        return True
    except smtplib.SMTPException as e:
        print(f"[check_logs] Erreur SMTP : {e}", file=sys.stderr)
    except OSError as e:
        print(f"[check_logs] Impossible de joindre le serveur SMTP : {e}", file=sys.stderr)
    return False


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Analyse les logs et alerte par email si erreurs.")
    parser.add_argument("--email-from", required=True,
                        help="Adresse expéditeur, ex: alertes@example.com")
    parser.add_argument("--email-to", required=True, action="append", dest="email_to",
                        help="Adresse destinataire (répétable pour plusieurs)")
    parser.add_argument("--smtp-host", required=True,
                        help="Serveur SMTP, ex: smtp.example.com")
    parser.add_argument("--smtp-port", type=int, default=587,
                        help="Port SMTP (défaut : 587)")
    parser.add_argument("--smtp-user", default="",
                        help="Login SMTP (optionnel)")
    parser.add_argument("--smtp-password", default="",
                        help="Mot de passe SMTP (optionnel)")
    parser.add_argument("--smtp-ssl", action="store_true",
                        help="Utiliser SSL direct (port 465) au lieu de STARTTLS")
    parser.add_argument("--web-json-url", default="",
                        help="URL du concerts.json servi par le site, ex: https://infoconcert.lu/IN/concerts.json")
    parser.add_argument("--test", action="store_true",
                        help="Envoie un email de test et quitte (vérifie que les paramètres SMTP sont corrects)")
    return parser.parse_args()


def main():
    args = parse_args()
    exit_code = 0

    if args.test:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        subject = f"[TEST] infoconcert.lu — check_logs — {now}"
        body = (
            f"Ceci est un email de test envoyé par check_logs.py.\n\n"
            f"Paramètres utilisés :\n"
            f"  SMTP host    : {args.smtp_host}\n"
            f"  SMTP port    : {args.smtp_port}\n"
            f"  SMTP user    : {args.smtp_user or '(aucun)'}\n"
            f"  SSL          : {'oui' if args.smtp_ssl else 'non (STARTTLS)'}\n"
            f"  Expéditeur   : {args.email_from}\n"
            f"  Destinataire : {', '.join(args.email_to)}\n"
        )
        print("[check_logs] Envoi email de test…")
        if send_email(args.smtp_host, args.smtp_port, args.smtp_user, args.smtp_password,
                      args.email_from, args.email_to, subject, body, args.smtp_ssl):
            print("[check_logs] Email de test envoyé avec succès.")
            sys.exit(0)
        else:
            print("[check_logs] Échec de l'envoi de l'email de test.", file=sys.stderr)
            sys.exit(1)

    if not os.path.isdir(LOG_DIR):
        print(f"[check_logs] Dossier Log/ introuvable : {LOG_DIR}", file=sys.stderr)
        sys.exit(1)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # --- 1. Vérification des logs ---
    state = load_state()
    errors_by_file, new_state = collect_errors(state)
    save_state(new_state)

    if errors_by_file:
        total = sum(len(v) for v in errors_by_file.values())
        subject = f"⚠️ infoconcert.lu — {total} erreur(s) — {now}"
        message = build_message(errors_by_file)
        print(f"[check_logs] {total} nouvelle(s) erreur(s) détectée(s), envoi alerte email…")
        if not send_email(args.smtp_host, args.smtp_port, args.smtp_user, args.smtp_password,
                          args.email_from, args.email_to, subject, message, args.smtp_ssl):
            exit_code = 1
        else:
            print("[check_logs] Alerte erreurs envoyée.")
    else:
        print("[check_logs] Aucune nouvelle erreur détectée.")

    # --- 2. Vérification synchronisation JSON web ---
    if args.web_json_url:
        sync_error = check_json_sync(args.web_json_url)
        if sync_error:
            subject = f"🔴 infoconcert.lu — JSON différent entre Serveur et Web — {now}"
            print(f"[check_logs] JSON différent entre Serveur et Web, envoi alerte email…")
            if not send_email(args.smtp_host, args.smtp_port, args.smtp_user, args.smtp_password,
                              args.email_from, args.email_to, subject, sync_error, args.smtp_ssl):
                exit_code = 1
            else:
                print("[check_logs] Alerte JSON envoyée.")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
