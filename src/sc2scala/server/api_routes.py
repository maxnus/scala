"""The API the CLI and the runners call, each request bearing a user's token."""

from typing import Annotated

from fastapi import APIRouter, Depends

from sc2scala.server.models import User
from sc2scala.server.web import token_user

router = APIRouter(prefix="/api")


@router.get("/me")
def me(user: Annotated[User, Depends(token_user)]) -> dict[str, str]:
    """Whose token the request bears."""
    return {"login": user.login}
