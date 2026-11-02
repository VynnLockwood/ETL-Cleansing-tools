import secrets
from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

_basic = HTTPBasic(auto_error=False)
ADMIN_USERNAME = "admin"


def admin_auth(password: str) -> Callable[..., None]:
    """Build a FastAPI dependency enforcing HTTP Basic auth as `admin`."""

    def dependency(credentials: HTTPBasicCredentials | None = Depends(_basic)) -> None:
        # Always compare both fields so timing does not reveal which was wrong.
        user_ok = secrets.compare_digest(
            (credentials.username if credentials else "").encode(), ADMIN_USERNAME.encode()
        )
        pass_ok = secrets.compare_digest(
            (credentials.password if credentials else "").encode(), password.encode()
        )
        if credentials is None or not (user_ok and pass_ok):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized",
                headers={"WWW-Authenticate": "Basic"},
            )

    return dependency
