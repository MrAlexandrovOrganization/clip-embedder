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
