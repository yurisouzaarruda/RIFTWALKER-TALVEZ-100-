"""RIFTWALKER — DETERMINISTIC INTERACTIVE ROOM FEATURES 0.11."""
from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
import random
from typing import List

@dataclass(frozen=True)
class RoomFeatureSpec:
    kind: str
    x_norm: float
    y_norm: float
    radius: float
    hp: float
    power: float
    cooldown: float

COMBAT_FEATURES = ("volatile_crystal", "veil_spike", "echo_cache", "healing_well", "rift_pylon")

def _seed(base_seed: int, node_id: str, region_id: str) -> int:
    return int.from_bytes(sha256(f"{base_seed}|{node_id}|{region_id}|interactive-v1".encode()).digest()[:8], "big")

def generate_room_features(*, base_seed: int, node_id: str, room_type: str, region_id: str, depth: int) -> List[RoomFeatureSpec]:
    if room_type not in {"combat", "elite", "boss"}: return []
    rng = random.Random(_seed(base_seed, node_id, region_id))
    count = 3 if room_type == "boss" else 4 if room_type == "elite" else 2 + (1 if depth >= 5 and rng.random() < 0.65 else 0)
    pool = list(COMBAT_FEATURES)
    if room_type == "boss": pool = ["volatile_crystal", "veil_spike", "rift_pylon", "echo_cache"]
    elif room_type == "elite": pool += ["volatile_crystal", "veil_spike", "rift_pylon"]
    result, positions = [], []
    for _ in range(count):
        for _attempt in range(40):
            x, y = rng.uniform(0.17,0.83), rng.uniform(0.18,0.82)
            if all((x-px)**2+(y-py)**2 >= 0.07**2 for px,py in positions): positions.append((x,y)); break
        else: x,y = rng.uniform(0.2,0.8), rng.uniform(0.2,0.8)
        kind = rng.choice(pool)
        if kind == "volatile_crystal": spec = RoomFeatureSpec(kind,x,y,22,52+depth*4,56+depth*3,0)
        elif kind == "veil_spike": spec = RoomFeatureSpec(kind,x,y,28,9999,14+depth*1.2,rng.uniform(1.7,2.4))
        elif kind == "echo_cache": spec = RoomFeatureSpec(kind,x,y,24,44+depth*3,35+depth*4,0)
        elif kind == "healing_well": spec = RoomFeatureSpec(kind,x,y,30,9999,0.16,0)
        else: spec = RoomFeatureSpec(kind,x,y,25,72+depth*5,9+depth*1.5,rng.uniform(2.3,3.2))
        result.append(spec)
    return result
