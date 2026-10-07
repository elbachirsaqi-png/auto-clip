import asyncio
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from .config import settings
from .pipeline import Pipeline
from .procutil import LOG_FILE, PID_FILE, running_pid


def main() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.StreamHandler(),
            # Lu par l'application AutoClip (onglet Journal et coût du jour).
            RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8"),
        ],
    )
    # httpx logge chaque URL en INFO, y compris les secrets passés en paramètres : on le fait taire.
    for noisy in ("httpx", "httpcore", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    other = running_pid()
    if other is not None and other != os.getpid():
        logging.getLogger("autoclip").error("Le pipeline tourne déjà (PID %d) : arrête-le avant d'en lancer un autre", other)
        sys.exit(1)
    PID_FILE.write_text(str(os.getpid()))
    try:
        asyncio.run(Pipeline(settings).run())
    except KeyboardInterrupt:
        pass
    except Exception:
        # Lancé par l'application, la sortie d'erreur n'est lue par personne : on la journalise.
        logging.getLogger("autoclip").exception("Le pipeline s'est arrêté sur une erreur")
        raise
    finally:
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
