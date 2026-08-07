from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_DETAIL_FMT = "%(asctime)s.%(msecs)03d [%(levelname)-8s] [%(name)s] %(message)s"
_CONSOLE_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    log_dir: Path | None = None,
    log_level: int = logging.DEBUG,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB per file
    backup_count: int = 5,
) -> Path:
    """Configure the ``kronika`` logger hierarchy.

    Attaches two handlers to the root ``kronika`` logger:
    - A rotating file handler (DEBUG+) writing to ``<log_dir>/kronika.log``.
    - A stderr console handler (WARNING+) so critical messages surface in the
      terminal without requiring the operator to tail the log file.

    Returns the resolved path of the main log *file* (not directory), so
    callers can display it to the user or pass it to an external log viewer.

    Safe to call multiple times — existing handlers are cleared before
    re-registration to prevent duplicated output lines.
    """
    target_dir = (log_dir or Path("logs")).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    main_log_file = target_dir / "kronika.log"

    file_formatter = logging.Formatter(fmt=_DETAIL_FMT, datefmt=_DATE_FMT)
    console_formatter = logging.Formatter(fmt=_CONSOLE_FMT, datefmt=_DATE_FMT)

    file_handler = RotatingFileHandler(
        filename=str(main_log_file),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)

    # Surface WARNING+ on stderr so operators see critical events immediately.
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(console_formatter)

    kronika_logger = logging.getLogger("kronika")
    kronika_logger.setLevel(log_level)
    # Clear existing handlers to prevent duplicate output on re-import.
    kronika_logger.handlers.clear()
    kronika_logger.addHandler(file_handler)
    kronika_logger.addHandler(console_handler)
    # Do not let records propagate to the root Python logger (avoids double
    # printing when external libraries also configure the root logger).
    kronika_logger.propagate = False

    kronika_logger.info(
        "Logging initialized — file=%s level=%s",
        main_log_file,
        logging.getLevelName(log_level),
    )

    return main_log_file
