"""Точка входа: `python -m memory_desk` запускает кабинет на 127.0.0.1."""
from __future__ import annotations

import uvicorn

from . import config


def run() -> None:
    from .app import create_app
    uvicorn.run(create_app(), host="127.0.0.1", port=config.PORT, log_level="info")


if __name__ == "__main__":
    run()
