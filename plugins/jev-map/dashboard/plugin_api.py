"""Jev map backend: the route-map facts the desktop side pane renders.

Mounted at ``/api/plugins/jev-map/`` once ``jev-map`` is in ``plugins.enabled``. Read-only.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jev_map import map_data  # noqa: E402

router = APIRouter()


@router.get("/map")
def get_map() -> dict:
    return map_data()


if __name__ == "__main__":
    data = map_data()
    assert data["ok"] is True, data
    assert data["mode"] in {"off", "shadow", "auto", "notify", "confirm"}, data["mode"]
    assert "routes" in data and "kindModels" in data and "updatedAt" in data
    print("jev-map api self-check ok:", data["mode"], data["modeSource"])
