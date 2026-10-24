from contextlib import asynccontextmanager

import hmac

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import db, pipeline
from .config import get_settings
from .schemas import CreateSummary, Summary


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    db.fail_interrupted()
    yield


_s = get_settings()


def require_token(authorization: str = Header(default="")) -> None:
    if not _s.api_token:
        return
    scheme, _, given = authorization.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(given.encode(), _s.api_token.encode()):
        raise HTTPException(401, "Falta o es incorrecta la clave de acceso.")


app = FastAPI(title="TubeRezume API", lifespan=lifespan)
auth = [Depends(require_token)]
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


@app.get("/health")
def health() -> dict:
    return {"ok": True, "auth": bool(_s.api_token)}


@app.post("/summaries", response_model=Summary, response_model_by_alias=True, status_code=201, dependencies=auth)
def create_summary(body: CreateSummary):
    try:
        return pipeline.create(body.url, body.web_search)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/summaries", response_model=list[Summary], response_model_by_alias=True, dependencies=auth)
def list_summaries():
    return db.list_all()


@app.get("/summaries/{summary_id}", response_model=Summary, response_model_by_alias=True, dependencies=auth)
def get_summary(summary_id: str):
    s = db.get(summary_id)
    if s is None:
        raise HTTPException(404, "Resumen no encontrado.")
    return s


@app.delete("/summaries/{summary_id}", status_code=204, dependencies=auth)
def delete_summary(summary_id: str):
    if not db.delete(summary_id):
        raise HTTPException(404, "Resumen no encontrado.")
    return Response(status_code=204)
