# -*- coding: utf-8 -*-
"""Logging configuration for Dragonpaw Bot.

Call configure_logging() once at startup, before any other imports log.
All modules then use ``structlog.get_logger()`` directly.
"""

import io
import logging
import sys
from typing import Any

import structlog

_RESET = "\033[0m"


def _fg(r: int, g: int, b: int) -> str:
    return f"\033[38;2;{r};{g};{b}m"


def _bg(r: int, g: int, b: int) -> str:
    return f"\033[48;2;{r};{g};{b}m"


# Gruvbox dark palette
_LEVEL_STYLES: dict[str, tuple[str, str]] = {
    # level: (badge_color, event_color)
    "debug": (_fg(0x92, 0x83, 0x74), _fg(0xA8, 0x99, 0x84)),
    "info": (_fg(0x8E, 0xC0, 0x7C), _fg(0xB8, 0xBB, 0x26)),
    "warning": (_fg(0xFA, 0xBD, 0x2F), _fg(0xD7, 0x99, 0x21)),
    "error": (_fg(0xFB, 0x49, 0x34), _fg(0xCC, 0x24, 0x1D)),
    "critical": (_fg(0xFE, 0x80, 0x19), _bg(0xCC, 0x24, 0x1D) + _fg(0xFF, 0xFF, 0xFF)),
}

_TIMESTAMP_COLOR = _fg(0x66, 0x5C, 0x54)
_LOGGER_COLOR = _fg(0x83, 0xA5, 0x98)
_KV_KEY_COLOR = _fg(0xA8, 0x99, 0x84)
_KV_VALUE_COLOR = _fg(0xEB, 0xDB, 0xB2)

_EVENT_WIDTH = 40


class GruvboxRenderer:
    """Gruvbox dark-themed structlog renderer."""

    def __call__(
        self,
        logger: Any,
        name: str,
        event_dict: dict[str, Any],
    ) -> str:
        timestamp = event_dict.pop("timestamp", "")
        level = event_dict.pop("level", "info")
        logger_name = event_dict.pop("logger", "")
        event = event_dict.pop("event", "")
        stack = event_dict.pop("stack", None)
        exception = event_dict.pop("exception", None)
        event_dict.pop("exc_info", None)

        badge_color, event_color = _LEVEL_STYLES.get(level, _LEVEL_STYLES["info"])

        parts: list[str] = []

        # Timestamp
        if timestamp:
            parts.append(f"{_TIMESTAMP_COLOR}{timestamp}{_RESET}")

        # Level badge (padded to 8 chars)
        parts.append(f"[{badge_color}{level:<8s}{_RESET}]")

        # Event message (padded for alignment)
        padded_event = (
            f"{event:<{_EVENT_WIDTH}s}" if len(event) < _EVENT_WIDTH else event
        )
        parts.append(f"{event_color}{padded_event}{_RESET}")

        # Logger name as a KV pair
        if logger_name:
            parts.append(f"{_LOGGER_COLOR}logger={_RESET}{logger_name}")

        # Remaining KV pairs
        for key, value in event_dict.items():
            parts.append(
                f"{_KV_KEY_COLOR}{key}={_RESET}{_KV_VALUE_COLOR}{value}{_RESET}"
            )

        line = " ".join(parts)

        # Append stack/exception info
        if stack is not None:
            sio = io.StringIO()
            sio.write("\n" + stack)
            if exception is not None:
                sio.write("\n\n" + exception)
            line += sio.getvalue()
        elif exception is not None:
            line += "\n" + exception

        return line


def configure_logging() -> None:
    """Configure structlog with colorized dev console output and stdlib bridge."""

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[  # type: ignore[arg-type]  # final renderer returns str
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            GruvboxRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    logging.getLogger("dragonpaw_bot").setLevel(logging.DEBUG)
