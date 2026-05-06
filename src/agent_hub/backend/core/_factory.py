from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import ProgrammingError
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from ..._metadata import api_prefix, app_name, dist_dir
from .api_version import APIVersionMiddleware
from ._base import LifespanDependency
from ._config import AppConfig, logger
from .logging_config import configure_logging
from .rate_limit import RateLimitConfig, RateLimitMiddleware
from .request_tracing import RequestTracingMiddleware
from .security_headers import SecurityHeadersMiddleware
from .timeout import TimeoutMiddleware
from ..services.base import (
    NotFoundError,
    ConflictError,
    ForbiddenError,
    ValidationError,
    ExternalServiceError,
)


@asynccontextmanager
async def _chain_dep_lifespans(
    deps: list[LifespanDependency],
    app: FastAPI,
) -> AsyncIterator[None]:
    if not deps:
        yield
        return

    head, *tail = deps

    async with head.lifespan(app):
        async with _chain_dep_lifespans(tail, app):
            yield


def create_app(
    *,
    routers: list[APIRouter] | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Dependencies are discovered automatically from the LifespanDependency registry.
    """
    configure_logging()
    all_deps: list[LifespanDependency] = []
    for dep in LifespanDependency._registry:
        try:
            all_deps.append(dep())
        except Exception as e:
            logger.error(f"Failed to instantiate dependency {dep.__name__}: {e}")
            raise e

    @asynccontextmanager
    async def _composed_lifespan(app: FastAPI):
        async with _chain_dep_lifespans(all_deps, app):
            yield

    app = FastAPI(title=app_name, lifespan=_composed_lifespan)

    config = AppConfig()
    app.add_middleware(APIVersionMiddleware)
    app.add_middleware(RequestTracingMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware, config=RateLimitConfig())
    app.add_middleware(TimeoutMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_allowed_origins,
        allow_credentials=config.cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
        max_age=config.cors_max_age,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    @app.exception_handler(NotFoundError)
    async def _not_found(request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def _conflict(request: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ForbiddenError)
    async def _forbidden(request: Request, exc: ForbiddenError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(ValidationError)
    async def _validation(request: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(ExternalServiceError)
    async def _external_error(request: Request, exc: ExternalServiceError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(ProgrammingError)
    async def _db_table_missing(request: Request, exc: ProgrammingError) -> JSONResponse:
        if "UndefinedTable" in str(exc.orig) or "does not exist" in str(exc):
            return JSONResponse(
                status_code=503,
                content={"detail": "Database migrations in progress. Please retry in a moment."},
            )
        return JSONResponse(status_code=500, content={"detail": "Database error"})

    api_router: APIRouter = create_router()
    for dep in all_deps:
        for r in dep.get_routers():
            api_router.include_router(r)
    app.include_router(api_router)

    for router in routers or []:
        if router is not api_router:
            app.include_router(router)

    if dist_dir.exists():
        from ._static import CachedStaticFiles, add_not_found_handler

        app.mount("/", CachedStaticFiles(directory=dist_dir, html=True))
        add_not_found_handler(app)

    return app


@lru_cache(maxsize=1)
def create_router() -> APIRouter:
    """Return the singleton APIRouter with the application's API prefix."""
    return APIRouter(prefix=api_prefix)
