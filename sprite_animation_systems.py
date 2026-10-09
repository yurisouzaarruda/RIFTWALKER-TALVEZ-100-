from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import pygame

_FRAME_CACHE: Dict[Tuple[str, ...], List[pygame.Surface]] = {}

PLAYER_FPS = {
    'idle': 4.5,
    'walk': 9.0,
    'run': 12.0,
    'attack': 18.0,
    'hurt': 14.0,
}
ENEMY_FPS = {
    'idle': 4.0,
    'walk': 8.0,
    'run': 11.0,
    'attack': 16.0,
    'hurt': 14.0,
}


def _gender_key(gender) -> str:
    raw = str(getattr(gender, 'value', gender)).lower()
    return 'feminino' if ('fem' in raw or raw.startswith('f')) else 'masculino'


def _load_folder(folder: Path, key: Tuple[str, ...]) -> List[pygame.Surface]:
    cached = _FRAME_CACHE.get(key)
    if cached is not None:
        return cached
    frames: List[pygame.Surface] = []
    if folder.exists():
        for path in sorted(folder.glob('*.png')):
            try:
                frames.append(pygame.image.load(str(path)).convert_alpha())
            except Exception:
                continue
    _FRAME_CACHE[key] = frames
    return frames


def _frame(frames: List[pygame.Surface], state: str, time_s: float, fps_map, progress: Optional[float] = None) -> Optional[pygame.Surface]:
    if not frames:
        return None
    if progress is not None:
        p = max(0.0, min(0.9999, progress))
        return frames[min(len(frames)-1, int(p * len(frames)))]
    fps = fps_map.get(state, 8.0)
    return frames[int(max(0.0, time_s) * fps) % len(frames)]


def get_player_animation_frame(base_dir: Path, class_id: str, gender, state: str, time_s: float, progress: Optional[float] = None) -> Optional[pygame.Surface]:
    state = state if state in PLAYER_FPS else ('run' if state == 'dash' else 'idle')
    g = _gender_key(gender)
    folder = base_dir / 'assets' / 'sprites_animated' / 'player' / class_id / g / state
    frames = _load_folder(folder, ('player', class_id, g, state))
    return _frame(frames, state, time_s, PLAYER_FPS, progress)


def get_enemy_animation_frame(base_dir: Path, skin_key: str, state: str, time_s: float, progress: Optional[float] = None) -> Optional[pygame.Surface]:
    state = state if state in ENEMY_FPS else 'idle'
    folder = base_dir / 'assets' / 'sprites_animated' / 'enemies' / skin_key / state
    frames = _load_folder(folder, ('enemy', skin_key, state))
    return _frame(frames, state, time_s, ENEMY_FPS, progress)


def clear_animation_cache() -> None:
    _FRAME_CACHE.clear()
