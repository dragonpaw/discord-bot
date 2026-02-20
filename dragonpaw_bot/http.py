from http import HTTPStatus
from typing import Any

import aiohttp

# ---------------------------------------------------------------------------- #
#                            HTTP utility functions                            #
# ---------------------------------------------------------------------------- #


async def get_json(url: str) -> Any:
    async with aiohttp.ClientSession(raise_for_status=True) as session:
        async with session.get(url) as r:
            if r.status == HTTPStatus.OK:
                return await r.json()


async def get_text(url) -> str:
    async with aiohttp.ClientSession(raise_for_status=True) as session:
        async with session.get(url) as r:
            return await r.text()


async def get_gist(url) -> str:
    # e.x.: https://gist.github.com/dragonpaw/ed69fa12e38de27199d21bd7dde4768e
    gist_id = url.split("/")[-1]
    async with aiohttp.ClientSession(
        raise_for_status=True,
        headers={
            "Accept": "application/vnd.github.v3+json",
        },
    ) as session:
        async with session.get(f"https://api.github.com/gists/{gist_id}") as r:
            data = await r.json()

        # Try to find a specific TOML file is there is one.
        for file in data["files"].values():
            if (
                file["filename"].lower().endswith(".toml")
                or file["language"].lower() == "toml"
            ):
                return file["content"]

        # Ok, then whatever the first file is.
        return list(data["files"].values())[0]["content"]
