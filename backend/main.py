# FastAPI app entry point. Wires up CORS, all routers, and initialises the database.

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .db import init_db
from .routes import router as http_router
from .ws_browse import router as browse_router
from .ws_pipeline import router as pipeline_router

app = FastAPI(title="Riva Strategic Pathfinder")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(http_router)
app.include_router(browse_router)
app.include_router(pipeline_router)

init_db()
