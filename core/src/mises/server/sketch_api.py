"""Sketch routes (admin token; the app's Sketch page is gone, Plan 7 U2b): `POST /sketches` stores a sketch (strokes in mm, typed
dimensions, notes, optional photo and canvas render) and returns its id; `GET /sketches/{id}`
returns the same summary. A run gets the sketch with `POST /runs {..., "sketch_id"}`."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import APIRouter

from ..sketch import SketchUpload, load_summary, save_sketch

if TYPE_CHECKING:
    from ..runtime import Runtime


def build_sketch_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()

    @router.post("/sketches")
    async def post_sketch(body: SketchUpload):
        # image decoding (and HEIC via sips) is blocking work
        return await asyncio.to_thread(save_sketch, runtime.home, body)

    @router.get("/sketches/{sketch_id}")
    async def get_sketch(sketch_id: str):
        return load_summary(runtime.home, sketch_id)

    return router
