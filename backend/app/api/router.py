"""Aggregated API router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routers import graph, meta, query

api_router = APIRouter()
api_router.include_router(meta.router)
api_router.include_router(graph.router)
api_router.include_router(query.router)
