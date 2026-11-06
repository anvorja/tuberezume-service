from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import db, pipeline, security
from .config import get_settings
from .schemas import (
    CreateSummary,
    LoginIn,
    NotesIn,
    Session,
    Summary,
    TagsIn,
    UserCreate,
    UserOut,
    UserPatch,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if len(get_settings().jwt_secret) < 32:
        raise RuntimeError("Falta JWT_SECRET (mínimo 32 caracteres) en el .env.")
    db.init_db()
    # Solo quien procesa puede saber que un trabajo quedó a medias; el servidor de consulta no debe tocarlos.
    if get_settings().can_process:
        db.mark_interrupted()
    yield


_s = get_settings()
app = FastAPI(title="TubeRezume API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_s.origins,
    allow_origin_regex=_s.cors_origin_regex or None,
    allow_methods=["*"],
    allow_headers=["*"],
)


# El frontend lee `message` del JSON en caso de error.
@app.exception_handler(HTTPException)
async def http_error(_, exc: HTTPException):
    return JSONResponse({"message": exc.detail}, status_code=exc.status_code)


def _out(u: db.User) -> UserOut:
    return UserOut(id=u.id, username=u.username, role=u.role, active=u.active, created_at=u.created_at)


def current_user(authorization: str = Header(default="")) -> db.User:
    scheme, _, token = authorization.partition(" ")
    uid = security.read_token(token) if scheme.lower() == "bearer" else None
    user = db.get_user(uid) if uid else None
    if user is None or not user.active:
        raise HTTPException(401, "Sesión no válida o caducada. Inicia sesión de nuevo.")
    return user


def member(user: db.User = Depends(current_user)) -> db.User:
    """Usuarios normales: dueños de sus resúmenes. El administrador no tiene resúmenes."""
    if user.role != "user":
        raise HTTPException(403, "El administrador solo gestiona usuarios.")
    return user


def admin(user: db.User = Depends(current_user)) -> db.User:
    if user.role != "admin":
        raise HTTPException(403, "Solo el administrador puede hacer esto.")
    return user


@app.get("/health")
def health() -> dict:
    return {"ok": True, "auth": True, "canProcess": get_settings().can_process}


# --- Sesión ------------------------------------------------------------------


@app.post("/auth/login", response_model=Session, response_model_by_alias=True)
def login(body: LoginIn, request: Request):
    key = f"{body.username.lower()}|{request.client.host if request.client else ''}"
    if security.locked(key):
        raise HTTPException(429, "Demasiados intentos. Espera un minuto e inténtalo de nuevo.")
    user = db.get_user_by_name(body.username.strip())
    if user is None:
        security.burn_time(body.password)
    if user is None or not user.active or not security.verify_password(body.password, user.password_hash):
        security.register_fail(key)
        raise HTTPException(401, "Usuario o contraseña incorrectos.")
    security.clear_fails(key)
    return Session(token=security.create_token(user.id), user=_out(user), can_process=get_settings().can_process)


@app.get("/auth/me", response_model=Session, response_model_by_alias=True)
def me(user: db.User = Depends(current_user), authorization: str = Header(default="")):
    return Session(token=authorization.partition(" ")[2], user=_out(user), can_process=get_settings().can_process)


# --- Resúmenes (usuario normal) ----------------------------------------------


@app.post("/summaries", response_model=Summary, response_model_by_alias=True, status_code=201)
def create_summary(body: CreateSummary, user: db.User = Depends(member)):
    if not get_settings().can_process:
        raise HTTPException(
            403, "Este servidor es solo de consulta. Procesa los videos desde el equipo con ngrok."
        )
    try:
        return pipeline.create(body.url, body.web_search, user.id)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/summaries/{summary_id}/resume", response_model=Summary, response_model_by_alias=True)
def resume_summary(summary_id: str, user: db.User = Depends(member)):
    if not get_settings().can_process:
        raise HTTPException(
            403, "Este servidor es solo de consulta. Reanuda el resumen desde el equipo con ngrok."
        )
    s = db.get_for_user(summary_id, user.id)
    if s is None:
        raise HTTPException(404, "No encontramos este resumen.")
    if s.status != "interrupted":
        raise HTTPException(409, "Solo se pueden reanudar los resúmenes interrumpidos.")
    return pipeline.resume(s)


@app.get("/summaries", response_model=list[Summary], response_model_by_alias=True)
def list_summaries(user: db.User = Depends(member)):
    return db.list_for_user(user.id)


@app.get("/summaries/{summary_id}", response_model=Summary, response_model_by_alias=True)
def get_summary(summary_id: str, user: db.User = Depends(member)):
    s = db.get_for_user(summary_id, user.id)
    if s is None:
        raise HTTPException(404, "Resumen no encontrado.")
    return s


@app.patch("/summaries/{summary_id}/notes", response_model=Summary, response_model_by_alias=True)
def save_notes(summary_id: str, body: NotesIn, user: db.User = Depends(member)):
    s = db.get_for_user(summary_id, user.id)
    if s is None:
        raise HTTPException(404, "Resumen no encontrado.")
    if s.status not in ("ready", "failed"):
        raise HTTPException(409, "Espera a que termine el resumen para editar las notas.")
    s.notes = body.notes
    db.save(s)
    return s


@app.patch("/summaries/{summary_id}/tags", response_model=Summary, response_model_by_alias=True)
def save_tags(summary_id: str, body: TagsIn, user: db.User = Depends(member)):
    s = db.get_for_user(summary_id, user.id)
    if s is None:
        raise HTTPException(404, "Resumen no encontrado.")
    if s.status not in ("ready", "failed"):
        raise HTTPException(409, "Espera a que termine el resumen para etiquetarlo.")
    s.tags = body.tags
    db.save(s)
    return s


@app.delete("/summaries/{summary_id}", status_code=204)
def delete_summary(summary_id: str, user: db.User = Depends(member)):
    if not db.delete_for_user(summary_id, user.id):
        raise HTTPException(404, "Resumen no encontrado.")
    return Response(status_code=204)


# --- Gestión de usuarios (administrador) -------------------------------------


@app.get("/admin/users", response_model=list[UserOut], response_model_by_alias=True)
def list_users(_: db.User = Depends(admin)):
    return [_out(u) for u in db.list_users()]


@app.post("/admin/users", response_model=UserOut, response_model_by_alias=True, status_code=201)
def create_user(body: UserCreate, _: db.User = Depends(admin)):
    if db.get_user_by_name(body.username):
        raise HTTPException(409, "Ya existe un usuario con ese nombre.")
    return _out(db.create_user(body.username, security.hash_password(body.password), body.role))


@app.patch("/admin/users/{user_id}", response_model=UserOut, response_model_by_alias=True)
def patch_user(user_id: str, body: UserPatch, me: db.User = Depends(admin)):
    target = db.get_user(user_id)
    if target is None:
        raise HTTPException(404, "Usuario no encontrado.")
    if body.active is False and target.id == me.id:
        raise HTTPException(409, "No puedes desactivar tu propia cuenta.")
    db.update_user(
        user_id, security.hash_password(body.password) if body.password else None, body.active
    )
    return _out(db.get_user(user_id))


@app.delete("/admin/users/{user_id}", status_code=204)
def remove_user(user_id: str, me: db.User = Depends(admin)):
    if user_id == me.id:
        raise HTTPException(409, "No puedes eliminar tu propia cuenta.")
    if not db.delete_user(user_id):
        raise HTTPException(404, "Usuario no encontrado.")
    return Response(status_code=204)
