"""RIFTWALKER — DAILY RIFT CHALLENGES 0.11.
Deterministic daily single-player challenges that compare run scores locally/online.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Dict, Optional, Tuple
from core_systems import PlayerState

DAILY_SCHEMA_VERSION = 1
DAILY_SALT = "RIFTWALKER_DAILY_RIFT_S1"
DAILY_REGIONS: Tuple[str, ...] = (
    "campos_primeira_fenda", "bosque_sussurrante", "bastilha_carmesim",
    "observatorio_fraturado", "abismo_sem_nome",
)

@dataclass(frozen=True)
class DailyChallenge:
    date_key: str
    seed: int
    region_id: str
    modifier_id: str
    title: str
    description: str
    enemy_hp_mult: float = 1.0
    enemy_damage_mult: float = 1.0
    enemy_speed_mult: float = 1.0
    player_damage_mult: float = 1.0
    score_mult: float = 1.0
    superior_bonus: float = 0.0
    starting_rifts: int = 0
    @property
    def trial_id(self) -> str:
        return f"daily:{self.date_key}:{self.modifier_id}"

MODIFIERS = (
    dict(modifier_id="riftstorm", title="TEMPESTADE DO VÉU", description="Duas Rupturas começam ativas. Caos alto e pontuação maior.", enemy_hp_mult=1.06, enemy_damage_mult=1.08, enemy_speed_mult=1.04, player_damage_mult=1.00, score_mult=1.22, superior_bonus=0.05, starting_rifts=2),
    dict(modifier_id="black_glass", title="VIDRO NEGRO", description="Você e os monstros causam muito mais dano. Erros ficam caros.", enemy_hp_mult=0.92, enemy_damage_mult=1.36, enemy_speed_mult=1.06, player_damage_mult=1.34, score_mult=1.24, superior_bonus=0.03, starting_rifts=0),
    dict(modifier_id="superior_hunt", title="CAÇADA SUPERIOR", description="Superiores aparecem com frequência brutal e a rota vale mais pontos.", enemy_hp_mult=1.12, enemy_damage_mult=1.12, enemy_speed_mult=1.08, player_damage_mult=1.05, score_mult=1.25, superior_bonus=0.20, starting_rifts=0),
    dict(modifier_id="chronoclash", title="CRONOCISMA", description="Tudo acelera. Seu dano sobe, mas o ritmo vira sobrevivência.", enemy_hp_mult=1.03, enemy_damage_mult=1.12, enemy_speed_mult=1.22, player_damage_mult=1.16, score_mult=1.23, superior_bonus=0.08, starting_rifts=1),
    dict(modifier_id="iron_memory", title="MEMÓRIA DE FERRO", description="Ameaças muito resistentes e mais Superiores. Run longa e tensa.", enemy_hp_mult=1.32, enemy_damage_mult=1.10, enemy_speed_mult=0.98, player_damage_mult=1.04, score_mult=1.28, superior_bonus=0.12, starting_rifts=0),
)

def _date_key(date_key: Optional[str] = None) -> str:
    return str(date_key) if date_key else datetime.now(timezone.utc).date().isoformat()

def daily_challenge(date_key: Optional[str] = None) -> DailyChallenge:
    key = _date_key(date_key)
    digest = sha256(f"{DAILY_SALT}|{key}".encode("utf-8")).digest()
    seed = int.from_bytes(digest[:8], "big") % 2_000_000_000 + 1
    region = DAILY_REGIONS[digest[8] % len(DAILY_REGIONS)]
    modifier = MODIFIERS[digest[9] % len(MODIFIERS)]
    return DailyChallenge(date_key=key, seed=seed, region_id=region, **modifier)

def ensure_daily_state(state: PlayerState) -> Dict[str, object]:
    root = state.campaign.setdefault("daily_challenge", {})
    root.setdefault("schema_version", DAILY_SCHEMA_VERSION)
    root.setdefault("active", False)
    root.setdefault("active_challenge", None)
    root.setdefault("previous_region", None)
    root.setdefault("completed", [])
    root.setdefault("best_scores", {})
    return root

def start_daily(state: PlayerState, challenge: DailyChallenge) -> None:
    root = ensure_daily_state(state)
    root["active"] = True
    root["active_challenge"] = asdict(challenge)
    root["previous_region"] = state.campaign.get("current_region", "refugio_ultima_luz")

def load_active_daily(state: PlayerState) -> Optional[DailyChallenge]:
    root = ensure_daily_state(state)
    raw = root.get("active_challenge")
    if not root.get("active") or not isinstance(raw, dict):
        return None
    try:
        return DailyChallenge(**raw)
    except Exception:
        root["active"] = False; root["active_challenge"] = None
        return None

def finish_daily(state: PlayerState, score: int = 0) -> None:
    root = ensure_daily_state(state)
    raw = root.get("active_challenge")
    if isinstance(raw, dict) and raw.get("date_key"):
        key = str(raw["date_key"])
        done = list(root.get("completed", []))
        if key not in done: done.append(key)
        root["completed"] = done[-60:]
        best = dict(root.get("best_scores", {}))
        best[key] = max(int(best.get(key, 0)), max(0, int(score)))
        root["best_scores"] = best
    previous = root.get("previous_region")
    if previous: state.campaign["current_region"] = previous
    root["active"] = False; root["active_challenge"] = None; root["previous_region"] = None

def daily_summary(date_key: Optional[str] = None) -> Dict[str, object]:
    c = daily_challenge(date_key)
    return {"date_key": c.date_key, "seed": c.seed, "region_id": c.region_id, "modifier_id": c.modifier_id, "title": c.title, "score_mult": c.score_mult}
