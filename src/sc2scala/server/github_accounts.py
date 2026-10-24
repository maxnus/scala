from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GitHubUser:
    """A GitHub account, by its id, which never changes, and its login, which may."""

    id: int
    login: str


class GitHubAccounts(Protocol):
    """GitHub's sign-in, by OAuth: where to send someone to sign in, and whose account the code they return with is."""

    def authorize_url(self, *, state: str, redirect_uri: str) -> str:
        """The page on GitHub that asks the person to sign in, and returns them to `redirect_uri` with `state`."""
        ...

    def user_for_code(self, code: str, *, redirect_uri: str) -> GitHubUser:
        """The account that signed in and returned with `code`; raises `SignInError` if GitHub refuses it."""
        ...
