import datetime
import json
import logging
import sys


class Formatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {
                "time": datetime.datetime.fromtimestamp(
                    record.created, datetime.timezone.utc
                ).isoformat(),
                "level": record.levelname,
                "msg": record.getMessage(),
                "service": record.name,
            }
        )


def setup():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(Formatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    # Download libraries can log signed URLs or remote error bodies. Keep only
    # the worker's own bounded lifecycle messages in application logs.
    for name in ("httpx", "httpcore", "huggingface_hub", "open_clip"):
        logging.getLogger(name).disabled = True
    logging.getLogger().setLevel(logging.WARNING)
    logging.getLogger("clip-worker").setLevel(logging.INFO)
    logging.getLogger("clip-coordinator").setLevel(logging.INFO)
