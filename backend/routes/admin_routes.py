"""Admin API — agent weight overrides, persisted to a JSON file so they survive restarts.
In DEV_MODE this is open; put it behind auth before any public deployment."""
import json
import os
from fastapi import APIRouter
from pydantic import BaseModel
from backend.config import settings

router = APIRouter(prefix="/api/admin", tags=["admin"])

WEIGHTS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "agent_weights.json")


def load_weights() -> dict:
    try:
        with open(WEIGHTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_weights(weights: dict):
    with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump(weights, f, indent=2)


class WeightUpdate(BaseModel):
    agent_id: int
    weight: float


@router.get("/weights")
async def get_weights():
    return {"weights": load_weights(), "dev_mode": settings.DEV_MODE}


@router.post("/weights")
async def set_weight(update: WeightUpdate):
    from backend.agents.orchestrator import apply_weight_override
    weights = load_weights()
    weights[str(update.agent_id)] = update.weight
    save_weights(weights)
    apply_weight_override(update.agent_id, update.weight)
    return {"ok": True, "weights": weights}
