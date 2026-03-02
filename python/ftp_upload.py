import argparse
import ftplib
import logging
import sys
from pathlib import Path


def setup_logger():
    script_path = Path(__file__)
    log_dir = script_path.parent / "Log"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / (script_path.stem + ".log")

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    handler_file    = logging.FileHandler(log_file, encoding="utf-8")
    handler_console = logging.StreamHandler(sys.stdout)

    for h in (handler_file, handler_console):
        h.setFormatter(fmt)

    logger = logging.getLogger("ftp_upload")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler_file)
    logger.addHandler(handler_console)
    return logger


def parse_args():
    parser = argparse.ArgumentParser(description="Upload un fichier vers un serveur FTP")
    parser.add_argument("local_file",               help="Chemin du fichier local à envoyer")
    parser.add_argument("--host",     required=True, help="Hôte FTP")
    parser.add_argument("--user",     required=True, help="Utilisateur FTP")
    parser.add_argument("--password", required=True, help="Mot de passe FTP")
    parser.add_argument("--port",     type=int, default=21, help="Port FTP (défaut: 21)")
    parser.add_argument("--remote-dir", default="/",  help="Répertoire distant cible (défaut: /)")
    parser.add_argument("--retries",  type=int, default=3, help="Nombre de tentatives (défaut: 3)")
    return parser.parse_args()


def upload(logger, local_file, host, port, user, password, remote_dir, max_retries):
    local_path = Path(local_file)

    if not local_path.exists():
        logger.error(f"Fichier introuvable : {local_path}")
        return False

    for attempt in range(1, max_retries + 1):
        logger.info(f"Tentative {attempt}/{max_retries} — upload de '{local_path.name}'")
        try:
            with ftplib.FTP() as ftp:
                ftp.connect(host, port, timeout=30)
                ftp.login(user, password)
                logger.info(f"Connecté à {host} en tant que '{user}'")

                ftp.cwd(remote_dir)
                logger.info(f"Répertoire distant : {remote_dir}")

                with open(local_path, "rb") as f:
                    ftp.storbinary(f"STOR {local_path.name}", f)

                logger.info(f"Fichier '{local_path.name}' uploadé avec succès")
                return True

        except ftplib.all_errors as e:
            logger.warning(f"Échec tentative {attempt} : {e}")
            if attempt == max_retries:
                logger.error(f"Upload abandonné après {max_retries} tentatives")

    return False


def main():
    args = parse_args()
    logger = setup_logger()
    logger.info("=" * 60)
    logger.info("Démarrage de l'upload FTP")

    success = upload(
        logger,
        local_file=args.local_file,
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        remote_dir=args.remote_dir,
        max_retries=args.retries,
    )

    if success:
        logger.info("Terminé avec succès")
    else:
        logger.error("Terminé en erreur")
        sys.exit(1)


if __name__ == "__main__":
    main()
