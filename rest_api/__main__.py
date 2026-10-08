"""Entry point for `python -m rest_api`."""

import sys

import uvicorn

from br_fixed_income.logger import logger, setup_logging

from .app import create_app
from .config import ConfigError, Settings


def main() -> int:
    setup_logging()
    try:
        settings = Settings.from_env()
    except ConfigError as e:
        logger.error("%s", e)
        return 2
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
