"""Authentication endpoints: POST /auth/login and GET /auth/me."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from src import auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login")
def login(request: LoginRequest, response: Response):
    response.headers["Cache-Control"] = "no-store"

    try:
        auth.get_secret()  # fail before touching any account state
        user = auth.authenticate(request.username.strip(), request.password)
        token = auth.issue_token(user["id"])
    except auth.InvalidCredentials:
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (auth.AuthConfigError, auth.AuthUnavailable) as error:
        logger.warning("Login unavailable: %s", type(error).__name__)
        raise HTTPException(status_code=503, detail="Authentication is not available.")

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": int(auth.TOKEN_TTL.total_seconds()),
        "user": user,
    }


@router.get("/me")
def me(user=Depends(auth.current_user)):
    return {"user": user, "auth_mode": auth.auth_mode()}