"""Auth routes — password login → JWT cookie. Roles: deployment (read) | admin (full)."""
from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from backend.config import settings
from backend.auth.security import verify_password
from backend.auth.jwt_handler import create_token, TOKEN_EXPIRE_HOURS

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str
    role: str = "admin"


@router.post("/login")
async def login(req: LoginRequest, response: Response):
    if not settings.ADMIN_PASSWORD_HASH:
        return JSONResponse({"ok": False, "detail": "No admin password set. Run `python setup.py`."},
                            status_code=503)
    if not verify_password(req.password, settings.ADMIN_PASSWORD_HASH):
        return JSONResponse({"ok": False, "detail": "Invalid password"}, status_code=401)
    role = "admin" if req.role == "admin" else "deployment"
    token = create_token({"sub": "owner", "role": role})
    response.set_cookie("access_token", token, httponly=True, samesite="strict",
                        max_age=TOKEN_EXPIRE_HOURS * 3600)
    return {"ok": True, "access_token": token, "role": role}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("access_token")
    return {"ok": True}
