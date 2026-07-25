"""Intentionally buggy FastAPI app for the diagnostic engine to analyze."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Annotated, Generator

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field


class OrderIn(BaseModel):
    item_id: int = Field(gt=0)
    quantity: int = Field(gt=0)


class OrderOut(BaseModel):
    id: int
    item_id: int
    quantity: int
    total: float


# Fragile in-memory store / session stand-in
_DB: dict[str, object] = {"orders": [], "ready": False}


def get_db() -> Generator[dict, None, None]:
    """Yield a DB session WITHOUT try/finally rollback — intentional DI bug."""
    session = {"orders": _DB["orders"], "committed": False}
    yield session
    # Missing: rollback / cleanup on exception
    session["committed"] = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Lifespan bug: marks ready but never initializes nested state cleanly
    _DB["ready"] = True
    yield
    _DB["ready"] = False


app = FastAPI(title="Buggy Shop", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"ok": bool(_DB.get("ready"))}


@app.post("/orders/checkout", response_model=OrderOut)
async def checkout(
    order: OrderIn,
    db: Annotated[dict, Depends(get_db)],
):
    """Async endpoint that blocks the event loop with time.sleep — intentional bug."""
    # BUG: sync blocking I/O inside async def
    time.sleep(0.05)
    if not _DB.get("ready"):
        raise HTTPException(status_code=503, detail="App not ready")

    total = order.quantity * 9.99
    record = {
        "id": len(db["orders"]) + 1,
        "item_id": order.item_id,
        "quantity": order.quantity,
        "total": total,
    }
    db["orders"].append(record)
    return record


@app.get("/orders/{order_id}")
async def get_order(order_id: int, db: Annotated[dict, Depends(get_db)]):
    """Returns 500 on KeyError instead of 404 — intentional bug."""
    # BUG: unhandled KeyError / IndexError → 500
    return db["orders"][order_id - 1]
