import json
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import httpx

from sc2scala.client_credentials import normalized_server_url
from sc2scala.errors import ServerError

_TIMEOUT_SECONDS = 30
_UPLOAD_TIMEOUT_SECONDS = 600
"""How long reading or writing may pause, which an upload of a large bot, and the server's checking it, may take."""


class ScalaClient:
    """Calls a Scala server's API with a user's token.

    Raises `ServerError` for a server that cannot be reached, refuses the token, or answers with an error.
    """

    def __init__(self, server_url: str, token: str, *, http: httpx.Client | None = None) -> None:
        self.server_url = normalized_server_url(server_url)
        self._token = token
        self._http = http or httpx.Client(
            timeout=httpx.Timeout(_TIMEOUT_SECONDS, read=_UPLOAD_TIMEOUT_SECONDS, write=_UPLOAD_TIMEOUT_SECONDS)
        )

    def me(self) -> str:
        """The login of the user whose token this is."""
        return str(self._request("GET", "/api/me")["login"])

    def upload_bot_version(self, name: str, archive: Path, *, race: str | None) -> dict[str, Any]:
        """Uploads the zip `archive` as a version of the bot `name`, which is made at its first upload."""
        with archive.open("rb") as archive_file:
            return self._request(
                "POST",
                f"/api/bots/{name}/versions",
                files={"archive": (archive.name, archive_file, "application/zip")},
                data={"race": race} if race else None,
            )

    def my_bots(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/bots")

    def bot(self, name: str) -> dict[str, Any]:
        return self._request("GET", f"/api/bots/{name}")

    def create_ladder(
        self, name: str, *, map_files: Sequence[Path], password: str | None, limits: dict[str, Any]
    ) -> dict[str, Any]:
        with ExitStack() as stack:
            files = [
                ("maps", (map_file.name, stack.enter_context(map_file.open("rb")), "application/octet-stream"))
                for map_file in map_files
            ]
            data = {"name": name, "limits": json.dumps(limits)} | ({"password": password} if password else {})
            return self._request("POST", "/api/ladders", files=files, data=data)

    def ladders(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/ladders")

    def ladder(self, name: str) -> dict[str, Any]:
        return self._request("GET", f"/api/ladders/{name}")

    def change_ladder(
        self, name: str, *, new_password: str | None, remove_password: bool, limits: dict[str, Any]
    ) -> dict[str, Any]:
        changes = {"new_password": new_password, "remove_password": remove_password, "limits": limits}
        return self._request("PATCH", f"/api/ladders/{name}", json=changes)

    def add_map(self, ladder: str, map_file: Path) -> dict[str, Any]:
        with map_file.open("rb") as opened:
            return self._request(
                "POST",
                f"/api/ladders/{ladder}/maps",
                files={"map_file": (map_file.name, opened, "application/octet-stream")},
            )

    def remove_map(self, ladder: str, map_name: str) -> dict[str, Any]:
        return self._request("DELETE", f"/api/ladders/{ladder}/maps/{map_name}")

    def join_ladder(self, name: str, *, password: str) -> dict[str, Any]:
        return self._request("POST", f"/api/ladders/{name}/join", json={"password": password})

    def enter_bot(self, ladder: str, bot: str) -> dict[str, Any]:
        return self._request("POST", f"/api/ladders/{ladder}/entries", json={"bot": bot})

    def withdraw_bot(self, ladder: str, bot: str) -> dict[str, Any]:
        return self._request("DELETE", f"/api/ladders/{ladder}/entries/{bot}")

    def pause_entry(self, ladder: str, bot: str) -> dict[str, Any]:
        return self._request("POST", f"/api/ladders/{ladder}/entries/{bot}/pause")

    def resume_entry(self, ladder: str, bot: str) -> dict[str, Any]:
        return self._request("POST", f"/api/ladders/{ladder}/entries/{bot}/resume")

    def _request(self, method: str, path: str, **content: Any) -> Any:
        try:
            response = self._http.request(
                method,
                self.server_url + path,
                headers={"Authorization": f"Bearer {self._token}"},
                **content,
            )
        except httpx.HTTPError as error:
            raise ServerError(f"{self.server_url} could not be reached: {error}") from error
        if response.status_code == 401:
            raise ServerError(
                f"{self.server_url} refused the token: make one on {self.server_url}/account", status_code=401
            )
        if response.is_error:
            raise ServerError(
                f"{self.server_url} answered {response.status_code}: {_detail_of(response)}",
                status_code=response.status_code,
            )
        return response.json()


def _detail_of(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        return response.text
    if isinstance(detail, list):
        return "; ".join(f"{'.'.join(map(str, error.get('loc', [])))}: {error.get('msg')}" for error in detail)
    return str(detail)
