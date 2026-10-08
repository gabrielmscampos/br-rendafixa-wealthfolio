"""Package-wide logger: every module imports `logger` from here."""

import logging


logger = logging.getLogger("br_fixed_income")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        format="[%(levelname)s] %(message)s", level=logging.WARNING
    )
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logging.getLogger("httpx").setLevel(
        logging.INFO if verbose else logging.WARNING
    )
