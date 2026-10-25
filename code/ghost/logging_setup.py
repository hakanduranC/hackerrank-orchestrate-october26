"""One logger for the package, configured by the entry points.

Library code logs through ``logging.getLogger("ghost")``; the CLIs decide where it goes
(stdout, plain messages, DEBUG with ``--verbose``). Nothing in the package prints.
"""

from __future__ import annotations

import logging
import sys

LOGGER_NAME = "ghost"


def get_logger() -> logging.Logger:
    """The package logger."""
    return logging.getLogger(LOGGER_NAME)


def configure_logging(verbose: bool = False) -> None:
    """Send package logs to stdout as plain lines; idempotent."""
    logger = get_logger()
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
