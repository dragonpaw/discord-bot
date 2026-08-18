from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dragonpaw_bot.utils import ModalHandler

MODAL_HANDLERS: dict[str, ModalHandler] = {}
