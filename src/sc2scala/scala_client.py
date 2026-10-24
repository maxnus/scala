from typing import Any

import httpx

from sc2scala.client_credentials import normalized_server_url
from sc2scala.errors import ServerError

_TIMEOUT_SECONDS = 30


class ScalaClient:
    """Calls a Scala server's API with a user's token.

    Raises `ServerError` for a server that cannot be reached, refuses the token, or answers with an error.
    """

    def __init__(self, server_url: str, token: str, *, http: httpx.Client | None = None) -> None:
        self.server_url = normalized_server_url(server_url)
        self._token = token
        self._http = http or httpx.Client(timeout=_TIMEOUT_SECONDS)

    def me(self) -> str:
        """The login of the user whose token this is."""
        return str(self._get("/api/me")["login"])

    def _get(self, path: str) -> Any:
        try:
            response = self._http.get(self.server_url + path, headers={"Authorization": f"Bearer {self._token}"})
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
        return str(response.json().get("detail", response.text))
    except ValueError:
        return response.text
