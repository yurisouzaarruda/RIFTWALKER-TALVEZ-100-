from __future__ import annotations

import math
import os
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pygame

from core_systems import (
    ActiveRiftState,
    AutosaveController,
    AutosaveReason,
    CLASS_REGISTRY,
    CharacterIdentity,
    CombatTelemetry,
    DamageType,
    FixedTimestep,
    Gender,
    MemoryRank,
    PlayerState,
    RARITY_INFO,
    RIFT_REGISTRY,
    Rarity,
    ReactionController,
    ReactionEvent,
    SaveManager,
    SKILL_REGISTRY,
    SkillCategory,
    SkillDefinition,
    SquadDoctrine,
    SQUAD_DOCTRINES,
    choose_squad_doctrine,
    create_new_character,
    generate_skill_offers,
)

from content_systems import (
    EQUIPMENT_REGISTRY,
    EquipmentSlot,
    active_set_bonuses,
    active_unique_effects,
    equip_item,
    equipment_modifiers,
    evaluate_class_unlocks,
    inventory_equipment_ids,
    make_inventory_item,
    roll_equipment_drop,
    rule_for_class,
    transfer_meta_progression,
)

from narrative_systems import (
    CampaignDirector,
    CampaignEvent,
    QUESTS,
    REGIONS,
    NPCS,
    select_npc_dialogue,
    chapter_title,
)

from hub_systems import (
    ensure_hub_state,
    reward_boss_materials,
    reward_rift_materials,
    FACILITIES, MATERIALS, RECIPES, VENDORS, CONSUMABLES,
    upgrade_facility, facility_level, facility_effects,
    material_count, gold, echoes, add_echoes,
    craft, salvage_equipment, refresh_vendor_stock, purchase_offer, sell_equipment_instance,
    consumable_count, expedition_supply_limit,
    ExpeditionPlan, prepare_expedition, launch_prepared_expedition, finish_expedition,
)

from creature_systems import (
    CREATURES,
    CreatureTier,
    apply_traits_to_enemy,
    creature_id_for_archetype,
    knowledge_rank,
    mutation_display,
    record_encounter,
    record_kill,
    record_superior_history,
    roll_spawn_traits,
    superior_memory_id,
    record_death_to,
    RESEARCH_PROJECTS, ensure_archive_state, complete_research,
    bestiary_entry_view, bestiary_completion,
)

# 0.9 RC: estes módulos registram os kits e o conteúdo final no motor.
from class_kit_systems import behavior_for, CLASS_KITS
from boss_systems import BOSSES, BossDirector
from endgame_systems import (
    FINAL_BOSS_ID,
    ECHO_BOSS_ID,
    final_boss_available,
    record_final_boss_defeat,
    record_echo_boss_defeat,
    eligible_endings,
    choose_ending,
    ensure_endgame_state, postgame_available, boss_rush_roster,
    start_ng_plus, ng_plus_modifiers,
)
from combat_status_systems import Element, StatusController, StatusId
from build_synergy_systems import synergy_modifiers, available_evolutions, evolve_skill
from challenge_systems import (
    ACHIEVEMENTS, TRIALS, add_metric, progress_trial_room,
    ensure_challenge_state, start_trial, collect_pending_echo_rewards,
)
from legacy_systems import NODES, legacy_modifiers, unlock_node, ensure_legacy_state
from encounter_director_systems import EncounterDirector, PerformanceSnapshot
from world_generation_systems import (
    RoomType, begin_generated_expedition, load_active_map, save_active_map, complete_generated_expedition,
)
from profile_systems import ProfileManager
from player_experience_systems import (
    SettingsManager, DIFFICULTIES, CODEX, DEFAULT_BINDINGS,
    tutorial_for_trigger, mark_tutorial_seen, unlock_codex_by_trigger, ensure_tutorial_state,
)
from pygame_audio_runtime import PygameAudioRuntime
from runtime_diagnostics import RuntimeDiagnostics, trim_oldest, write_crash_report
from environment_systems import EnvironmentRenderer
from sprite_animation_systems import get_player_animation_frame, get_enemy_animation_frame
from faction_systems import (
    FACTIONS, CHOICES, reputation, reputation_rank, make_faction_choice, ensure_faction_state,
)
from leaderboard_systems import LeaderboardManager, make_run_result
from daily_challenge_systems import daily_challenge, start_daily, load_active_daily, finish_daily
from room_interaction_systems import generate_room_features

# ============================================================
# RIFTWALKER — RELEASE CANDIDATE 0.10
# ============================================================
#
# Este arquivo liga o motor de sistemas (core_systems.py) ao
# Pygame e transforma a arquitetura em uma versão jogável.
#
# CONTROLES
# WASD       mover
# Mouse      mirar
# Clique 1   ataque básico
# Clique 2   habilidade contextual / especial da classe
# Q/E/R/F    habilidades equipadas (1..4 também funcionam)
# SHIFT      defesa/parry quando disponível
# Mouse/setas direcionais = mira
# SPACE      dash
# ESC        pausa/voltar
# F3         debug FPS
#
# IMPORTANTE:
# - lógica de jogo usa fixed timestep em 60 Hz;
# - render pode rodar em FPS maior sem alterar gameplay;
# - autosave acontece em pontos seguros, não por frame.
# ============================================================

pygame.mixer.pre_init(44100, -16, 2, 512)
pygame.init()

BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))

# Em execução .py, saves/settings ficam ao lado do projeto para facilitar a escola e o debug.
# Em um .exe PyInstaller, usamos uma pasta gravável do usuário em vez do diretório temporário _MEIPASS.
if getattr(sys, "frozen", False):
    _appdata = os.environ.get("APPDATA")
    data_dir = (Path(_appdata) if _appdata else Path.home()) / "RIFTWALKER"
else:
    data_dir = BASE_DIR
DATA_DIR = data_dir
DATA_DIR.mkdir(parents=True, exist_ok=True)

BUILD_VERSION = "0.11.0"
# leaderboard_config.json é criado/copied para a pasta gravável para o jogador poder
# ativar o ranking online sem editar arquivos internos do EXE.
LEADERBOARD_CONFIG_PATH = DATA_DIR / "leaderboard_config.json"
if not LEADERBOARD_CONFIG_PATH.exists():
    packaged_config = BASE_DIR / "leaderboard_config.json"
    if packaged_config.exists():
        try:
            LEADERBOARD_CONFIG_PATH.write_text(packaged_config.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass
LEADERBOARD = LeaderboardManager(DATA_DIR)

SETTINGS_MANAGER = SettingsManager(str(DATA_DIR / "settings.json"))
GAME_SETTINGS = SETTINGS_MANAGER.load()
WIDTH, HEIGHT = GAME_SETTINGS.video.resolution
RENDER_FPS_CAP = GAME_SETTINGS.video.fps_cap
LOGIC_UPS = 60

_video_flags = pygame.FULLSCREEN if GAME_SETTINGS.video.fullscreen else 0
try:
    screen = pygame.display.set_mode((WIDTH, HEIGHT), _video_flags, vsync=1 if GAME_SETTINGS.video.vsync else 0)
except TypeError:
    screen = pygame.display.set_mode((WIDTH, HEIGHT), _video_flags)
pygame.display.set_caption("RIFTWALKER — 0.11 INSANITY PASS")
clock = pygame.time.Clock()
stepper = FixedTimestep(updates_per_second=LOGIC_UPS)
AUDIO = PygameAudioRuntime(GAME_SETTINGS, str(BASE_DIR))

# ============================================================
# FONTES / CORES
# ============================================================

_UI_SCALE = 1.14 if GAME_SETTINGS.accessibility.larger_ui else 1.0
def _fs(size: int) -> int:
    return max(10, int(size * _UI_SCALE))
FONT_TINY = pygame.font.SysFont("consolas", _fs(13))
FONT_SMALL = pygame.font.SysFont("consolas", _fs(16))
FONT = pygame.font.SysFont("consolas", _fs(20))
FONT_MEDIUM = pygame.font.SysFont("consolas", _fs(28), bold=True)
FONT_BIG = pygame.font.SysFont("consolas", _fs(48), bold=True)
FONT_TITLE = pygame.font.SysFont("consolas", _fs(76), bold=True)

WHITE = (238, 241, 248)
BLACK = (7, 8, 13)
BG = (12, 14, 23)
BG_2 = (18, 21, 33)
PANEL = (29, 33, 48)
PANEL_HOVER = (42, 48, 69)
GRID = (37, 42, 59)
GRAY = (144, 151, 169)
DARK_GRAY = (73, 80, 96)
RED = (232, 72, 78)
GREEN = (73, 214, 127)
BLUE = (82, 151, 245)
CYAN = (69, 225, 235)
PURPLE = (173, 93, 236)
YELLOW = (246, 216, 83)
ORANGE = (246, 140, 52)
PINK = (236, 91, 178)

RARITY_COLORS = {
    Rarity.COMUM: (210, 215, 225),
    Rarity.INCOMUM: (92, 220, 126),
    Rarity.RARA: (80, 152, 245),
    Rarity.EPICA: (175, 92, 238),
    Rarity.LENDARIA: (246, 190, 63),
    Rarity.MITICA: (75, 236, 235),
    Rarity.CORROMPIDA: (235, 62, 95),
}

CLASS_COLORS = {
    "guerreiro": RED,
    "mago": CYAN,
    "arqueiro": YELLOW,
    "ceifador": (177, 74, 164),
    "rasgado": (212, 54, 87),
    "cronista": (79, 188, 210),
    "artifice": ORANGE,
    "duelista": (236, 226, 205),
    "oraculo": PURPLE,
    "guardiao_veu": BLUE,
}

BASIC_ATTACK_RESOURCE_COSTS = {
    "mago": 4.5,
    "arqueiro": 2.0,
}
BASE_RESOURCE_REGEN_PER_SECOND = 9.25
MAGE_RESOURCE_REGEN_PER_SECOND = 4.5
BASIC_ATTACK_VISUAL_DURATION = 0.14
DUELIST_COMBO_MAX = 12
DUELIST_BASIC_DAMAGE_BASE = 0.84
DUELIST_BASIC_DAMAGE_PER_COMBO = 0.011
DUELIST_BASIC_COOLDOWN_BASE = 0.22
DUELIST_BASIC_COOLDOWN_REDUCTION_PER_COMBO = 0.0025
DUELIST_BASIC_COOLDOWN_FLOOR = 0.19

DAMAGE_ELEMENT_MAP = {
    DamageType.FISICO: Element.FISICO,
    DamageType.FOGO: Element.FOGO,
    DamageType.GELO: Element.GELO,
    DamageType.RAIO: Element.RAIO,
    DamageType.SOMBRA: Element.SOMBRA,
    DamageType.VAZIO: Element.VAZIO,
    DamageType.TEMPORAL: Element.TEMPORAL,
    DamageType.ARCANO: Element.ARCANO,
    DamageType.SANGRAMENTO: Element.FISICO,
    DamageType.PURO: Element.ARCANO,
}

def element_color(element: Optional[Element]) -> Tuple[int, int, int]:
    if element is None:
        return WHITE
    return {
        Element.FOGO: ORANGE,
        Element.GELO: CYAN,
        Element.RAIO: (110, 220, 255),
        Element.SOMBRA: PURPLE,
        Element.VAZIO: (208, 68, 230),
        Element.TEMPORAL: (78, 205, 230),
        Element.ARCANO: (185, 92, 255),
        Element.FISICO: WHITE,
    }.get(element, WHITE)

# ============================================================
# SKINS / ANIMACAO VISUAL DO JOGADOR
# ============================================================

PLAYER_SKIN_CACHE: Dict[Tuple[str, str], pygame.Surface] = {}

def _gender_asset_key(gender: Gender) -> str:
    raw_gender = str(getattr(gender, "value", gender)).lower()
    return "feminino" if raw_gender.startswith("f") else "masculino"

def get_player_skin(class_id: str, gender: Gender) -> Optional[pygame.Surface]:
    gender_key = _gender_asset_key(gender)
    key = (class_id, gender_key)
    if key in PLAYER_SKIN_CACHE:
        return PLAYER_SKIN_CACHE[key]
    path = BASE_DIR / "assets" / "sprites" / "player" / class_id / f"{gender_key}.png"
    if not path.exists():
        PLAYER_SKIN_CACHE[key] = None
        return None
    try:
        img = pygame.image.load(str(path)).convert_alpha()
        # Failsafe para assets cujo RGB está íntegro, mas o recorte alpha veio
        # quase todo transparente. Evita personagem invisível no runtime.
        area = max(1, img.get_width() * img.get_height())
        coverage = pygame.mask.from_surface(img, 10).count() / area
        if coverage < 0.08:
            opaque = pygame.image.load(str(path)).convert()
            recovered = pygame.Surface(opaque.get_size(), pygame.SRCALPHA)
            recovered.blit(opaque, (0, 0))
            vignette = pygame.Surface(opaque.get_size(), pygame.SRCALPHA)
            inset_x = max(2, int(opaque.get_width() * 0.025))
            inset_y = max(2, int(opaque.get_height() * 0.015))
            rect = pygame.Rect(inset_x, inset_y, opaque.get_width() - inset_x * 2, opaque.get_height() - inset_y * 2)
            pygame.draw.rect(vignette, (255, 255, 255, 255), rect, border_radius=max(10, min(rect.width, rect.height) // 7))
            recovered.blit(vignette, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            img = recovered
    except Exception:
        PLAYER_SKIN_CACHE[key] = None
        return None
    PLAYER_SKIN_CACHE[key] = img
    return img

def draw_tinted(surface: pygame.Surface, image: pygame.Surface, pos, tint=(255,255,255), alpha=255):
    frame = image.copy()
    if tint != (255,255,255):
        frame.fill((*tint, 255), special_flags=pygame.BLEND_RGBA_MULT)
    frame.set_alpha(alpha)
    surface.blit(frame, pos)


# ============================================================
# SPRITES / ANIMACAO VISUAL DOS INIMIGOS
# ============================================================

ENEMY_SKIN_CACHE: Dict[str, Optional[pygame.Surface]] = {}

def get_enemy_skin(kind: str, boss_id: Optional[str] = None) -> Optional[pygame.Surface]:
    # Bosses têm arte própria; inimigos normais usam o arquétipo.
    key = boss_id if boss_id else kind
    if key == "guardian" or not key:
        key = "guardiao_primeira_ruptura"
    if key in ENEMY_SKIN_CACHE:
        return ENEMY_SKIN_CACHE[key]
    path = BASE_DIR / "assets" / "sprites" / "enemies" / f"{key}.png"
    if not path.exists():
        ENEMY_SKIN_CACHE[key] = None
        return None
    try:
        image = pygame.image.load(str(path)).convert_alpha()
    except Exception:
        ENEMY_SKIN_CACHE[key] = None
        return None
    ENEMY_SKIN_CACHE[key] = image
    return image


def enemy_tier_color(tier: str) -> Tuple[int, int, int]:
    raw = str(tier).lower()
    if "super" in raw:
        return (244, 77, 205)
    if "elite" in raw:
        return (246, 190, 63)
    if "boss" in raw:
        return (246, 90, 64)
    return (110, 118, 140)


# Cache geométrico: evita smoothscale/flip/rotate repetidos para os mesmos estados.
# Dimensões e ângulos são quantizados de propósito para limitar memória e manter
# a animação visual suave sem criar milhares de Surfaces diferentes.
SPRITE_TRANSFORM_CACHE: Dict[Tuple[int, int, int, bool, int], pygame.Surface] = {}
SPRITE_TRANSFORM_CACHE_LIMIT = 640

def cached_sprite_transform(image: pygame.Surface, width: int, height: int, flip_x: bool, angle: float) -> pygame.Surface:
    qw = max(2, int(round(width / 2.0) * 2))
    qh = max(2, int(round(height / 2.0) * 2))
    qa = int(round(angle / 2.0) * 2)
    key = (id(image), qw, qh, bool(flip_x), qa)
    cached = SPRITE_TRANSFORM_CACHE.get(key)
    if cached is not None:
        return cached
    frame = pygame.transform.smoothscale(image, (qw, qh))
    if flip_x:
        frame = pygame.transform.flip(frame, True, False)
    if qa:
        frame = pygame.transform.rotate(frame, qa)
    if len(SPRITE_TRANSFORM_CACHE) >= SPRITE_TRANSFORM_CACHE_LIMIT:
        # Limpeza simples e previsível; melhor um pico raro de rebuild do que cache sem limite.
        SPRITE_TRANSFORM_CACHE.clear()
    SPRITE_TRANSFORM_CACHE[key] = frame
    return frame



def elemental_payload_for_skill(skill_id: Optional[str]):
    skill = SKILL_REGISTRY.get(skill_id or "")
    if skill is None:
        return Element.FISICO, ()
    element = DAMAGE_ELEMENT_MAP.get(skill.damage_type, Element.FISICO)
    tags = set(skill.tags)
    applied = []
    if element == Element.FOGO:
        applied.append((StatusId.QUEIMANDO, 1))
    elif element == Element.GELO:
        applied.append((StatusId.RESFRIADO, 1))
        if "control" in tags:
            applied.append((StatusId.RESFRIADO, 1))
    elif element == Element.RAIO:
        applied.append((StatusId.ELETRIZADO, 1))
    elif element == Element.SOMBRA:
        applied.append((StatusId.AMALDICOADO, 1))
    elif element == Element.VAZIO:
        applied.append((StatusId.CORROMPIDO, 1))
    elif element == Element.TEMPORAL:
        applied.append((StatusId.LENTO, 1))
    if "bleed" in tags:
        applied.append((StatusId.SANGRANDO, 1))
    if "mark" in tags:
        applied.append((StatusId.MARCADO, 1))
    return element, tuple(applied)

ROOM = pygame.Rect(55, 78, WIDTH - 110, HEIGHT - 145)

HUB_NPC_PRESENTATIONS = {
    "aris": ("guardiao_veu", Gender.MASCULINO, ORANGE, "PORTAL"),
    "maelis": ("mago", Gender.FEMININO, CYAN, "ARQUIVO"),
    "nox": ("artifice", Gender.MASCULINO, YELLOW, "FORJA"),
    "ilyra": ("cronista", Gender.FEMININO, PURPLE, "HOLOGRAMA"),
}

SAVE_DIR = DATA_DIR / "saves"
SAVE_DIR.mkdir(parents=True, exist_ok=True)
SAVE_PATH = SAVE_DIR / "save_riftwalker.json"

# ============================================================
# HELPERS
# ============================================================

def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize(v: pygame.Vector2) -> pygame.Vector2:
    if v.length_squared() <= 0.00001:
        return pygame.Vector2(1, 0)
    return v.normalize()


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def draw_text(
    surface: pygame.Surface,
    text: str,
    pos: Tuple[float, float] | pygame.Vector2,
    color=WHITE,
    font=FONT,
    center: bool = False,
):
    img = font.render(str(text), True, color)
    rect = img.get_rect()
    if center:
        rect.center = (int(pos[0]), int(pos[1]))
    else:
        rect.topleft = (int(pos[0]), int(pos[1]))
    surface.blit(img, rect)
    return rect


def draw_wrapped_text(
    surface: pygame.Surface,
    text: str,
    rect: pygame.Rect,
    color=WHITE,
    font=FONT_SMALL,
    line_spacing: int = 3,
):
    words = text.split()
    lines: List[str] = []
    current = ""

    for word in words:
        test = word if not current else current + " " + word
        if font.size(test)[0] <= rect.width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    y = rect.top
    for line in lines:
        if y + font.get_height() > rect.bottom:
            break
        draw_text(surface, line, (rect.left, y), color, font)
        y += font.get_height() + line_spacing


def draw_bar(
    surface: pygame.Surface,
    rect: pygame.Rect,
    value: float,
    maximum: float,
    color,
    border_color=WHITE,
):
    pygame.draw.rect(surface, (6, 8, 14), rect.move(0, 3), border_radius=5)
    pygame.draw.rect(surface, (45, 50, 65), rect, border_radius=5)
    ratio = 0.0 if maximum <= 0 else clamp(value / maximum, 0.0, 1.0)
    fill = rect.copy()
    fill.width = int(rect.width * ratio)
    if fill.width > 0:
        pygame.draw.rect(surface, color, fill, border_radius=5)
        pygame.draw.line(surface, tuple(min(255, c + 55) for c in color), (fill.x + 5, fill.y + 2), (max(fill.x + 5, fill.right - 5), fill.y + 2), 1)
    pygame.draw.rect(surface, border_color, rect, 1, border_radius=5)


def draw_ui_panel(surface, rect, accent=CYAN, fill=PANEL, radius=10, glow=False):
    shadow = rect.move(0, 5)
    pygame.draw.rect(surface, (5, 6, 12), shadow, border_radius=radius)
    pygame.draw.rect(surface, fill, rect, border_radius=radius)
    if glow:
        glow_rect = rect.inflate(6, 6)
        pygame.draw.rect(surface, (*accent, 34), glow_rect, 2, border_radius=radius + 2)
    pygame.draw.rect(surface, accent, rect, 2, border_radius=radius)
    pygame.draw.line(surface, (*accent, 150), (rect.x + 14, rect.y + 2), (rect.right - 14, rect.y + 2), 2)


def point_in_circle(a: pygame.Vector2, b: pygame.Vector2, radius: float) -> bool:
    return a.distance_squared_to(b) <= radius * radius


def circle_collision(
    a_pos: pygame.Vector2,
    a_radius: float,
    b_pos: pygame.Vector2,
    b_radius: float,
) -> bool:
    r = a_radius + b_radius
    return a_pos.distance_squared_to(b_pos) <= r * r


def active_trial_modifiers_for_state(state: PlayerState) -> set[str]:
    root = ensure_challenge_state(state)
    trial_id = root.get("active_trial")
    if not trial_id or trial_id not in TRIALS:
        return set()
    return set(TRIALS[trial_id].modifiers)


def faction_reward_flags(state: PlayerState) -> set[str]:
    root = ensure_faction_state(state)
    return {str(x) for x in root.get("unlocked_flags", [])}


def soften_rift_penalty(multiplier: float, penalty_mult: float, harmful_when_above_one: bool = False) -> float:
    """Move only the harmful part of a Rift modifier toward neutral (1.0)."""
    m = float(multiplier); p = max(0.0, min(1.0, float(penalty_mult)))
    if harmful_when_above_one and m > 1.0:
        return 1.0 + (m - 1.0) * p
    if not harmful_when_above_one and m < 1.0:
        return 1.0 - (1.0 - m) * p
    return m


def binding_name(action: str) -> str:
    return str(GAME_SETTINGS.bindings.get(action, DEFAULT_BINDINGS.get(action, ""))).upper()


def binding_key(action: str, fallback: int) -> int:
    name = binding_name(action)
    aliases = {
        "SPACE": pygame.K_SPACE, "ESC": pygame.K_ESCAPE, "ESCAPE": pygame.K_ESCAPE,
        "SHIFT": pygame.K_LSHIFT, "LSHIFT": pygame.K_LSHIFT, "RSHIFT": pygame.K_RSHIFT,
        "UP": pygame.K_UP, "DOWN": pygame.K_DOWN, "LEFT": pygame.K_LEFT, "RIGHT": pygame.K_RIGHT,
        "ENTER": pygame.K_RETURN, "RETURN": pygame.K_RETURN, "TAB": pygame.K_TAB,
        "BACKSPACE": pygame.K_BACKSPACE,
    }
    if name in aliases:
        return aliases[name]
    if len(name) == 1:
        try:
            return pygame.key.key_code(name.lower())
        except Exception:
            return fallback
    try:
        return pygame.key.key_code(name.lower())
    except Exception:
        return fallback


def binding_mouse_button(action: str) -> Optional[int]:
    name = binding_name(action)
    return {"MOUSE1": 1, "MOUSE2": 3, "MOUSE3": 2}.get(name)


def difficulty_label(value: int) -> str:
    return {
        1: "Fácil",
        2: "Média",
        3: "Difícil",
        4: "Alta",
        5: "Extrema",
    }.get(value, "?")


# ============================================================
# VISUAIS PROCEDURAIS
# ============================================================

@dataclass
class Particle:
    pos: pygame.Vector2
    vel: pygame.Vector2
    color: Tuple[int, int, int]
    life: float
    max_life: float
    radius: float
    drag: float = 0.92
    dead: bool = False

    @classmethod
    def burst(
        cls,
        pos: pygame.Vector2,
        color,
        speed_min=70.0,
        speed_max=240.0,
        life=0.42,
        radius=5.0,
    ) -> "Particle":
        angle = random.uniform(0, math.tau)
        speed = random.uniform(speed_min, speed_max)
        vel = pygame.Vector2(math.cos(angle), math.sin(angle)) * speed
        actual_life = life * random.uniform(0.7, 1.2)
        return cls(
            pos=pygame.Vector2(pos),
            vel=vel,
            color=color,
            life=actual_life,
            max_life=actual_life,
            radius=random.uniform(max(1.0, radius * 0.45), radius),
        )

    def update(self, dt: float):
        self.pos += self.vel * dt
        self.vel *= self.drag
        self.life -= dt
        if self.life <= 0:
            self.dead = True

    def draw(self, surface, offset):
        ratio = clamp(self.life / self.max_life, 0.0, 1.0)
        radius = max(1, int(self.radius * ratio))
        p = self.pos + offset
        alpha = int(220 * ratio)
        glow_r = max(radius + 4, int(radius * 2.8))
        layer = pygame.Surface((glow_r * 2 + 4, glow_r * 2 + 4), pygame.SRCALPHA)
        c = (glow_r + 2, glow_r + 2)
        pygame.draw.circle(layer, (*self.color, max(8, alpha // 5)), c, glow_r)
        pygame.draw.circle(layer, (*self.color, max(18, alpha // 2)), c, max(1, radius + 2))
        pygame.draw.circle(layer, (255, 255, 255, min(235, alpha)), c, max(1, radius // 2))
        surface.blit(layer, (int(p.x - c[0]), int(p.y - c[1])))
        if self.vel.length_squared() > 900:
            d = normalize(self.vel)
            tail = p - d * min(18, 5 + self.vel.length() * 0.035)
            pygame.draw.line(surface, self.color, tail, p, max(1, radius // 2))


@dataclass
class FloatingText:
    pos: pygame.Vector2
    text: str
    color: Tuple[int, int, int]
    life: float = 0.72
    max_life: float = 0.72
    dead: bool = False

    def update(self, dt: float):
        self.pos.y -= 42 * dt
        self.life -= dt
        if self.life <= 0:
            self.dead = True

    def draw(self, surface, offset):
        draw_text(surface, self.text, self.pos + offset, self.color, FONT_SMALL, True)


@dataclass
class SlashVisual:
    pos: pygame.Vector2
    direction: pygame.Vector2
    color: Tuple[int, int, int]
    radius: float
    life: float = 0.13
    max_life: float = 0.13
    width: int = 10
    dead: bool = False

    def update(self, dt):
        self.life -= dt
        if self.life <= 0:
            self.dead = True

    def draw(self, surface, offset):
        progress = 1.0 - clamp(self.life / self.max_life, 0.0, 1.0)
        angle = math.atan2(self.direction.y, self.direction.x)
        envelope = min(1.0, progress * 5.0, (1.0 - progress) * 3.0)
        sweep = 0.72 + 0.30 * math.sin(progress * math.pi)
        start = angle - sweep + 0.48 * progress
        end = angle + sweep + 0.48 * progress
        pad = int(self.width * 2.5)
        size = int(self.radius * 2 + pad * 2)
        layer = pygame.Surface((size, size), pygame.SRCALPHA)
        local_rect = pygame.Rect(pad, pad, int(self.radius * 2), int(self.radius * 2))
        # Layered, tapered ribbons read as a blade swing rather than a complete ring.
        pygame.draw.arc(layer, (*self.color, int(48 * envelope)), local_rect.inflate(18, 18), start, end, self.width + 16)
        pygame.draw.arc(layer, (*self.color, int(105 * envelope)), local_rect.inflate(8, 8), start, end, self.width + 8)
        pygame.draw.arc(layer, (*self.color, int(225 * envelope)), local_rect, start, end, self.width)
        pygame.draw.arc(layer, (255, 255, 255, int(195 * envelope)), local_rect.inflate(-3, -3), start, end, max(2, self.width // 3))
        center = pygame.Vector2(size / 2, size / 2)
        for frac, alpha, thick in ((0.82, 145, 4), (0.95, 92, 3), (1.06, 52, 2)):
            tip = center + pygame.Vector2(math.cos(end), math.sin(end)) * (self.radius * frac)
            tail = tip - normalize(self.direction).rotate(90) * (8 + 20 * progress)
            pygame.draw.line(layer, (*self.color, int(alpha * envelope)), tail, tip, thick)
        dest = (int(self.pos.x + offset.x - size/2), int(self.pos.y + offset.y - size/2))
        surface.blit(layer, dest)


@dataclass
class ImpactRing:
    pos: pygame.Vector2
    color: Tuple[int, int, int]
    radius_start: float = 10.0
    radius_end: float = 54.0
    life: float = 0.24
    max_life: float = 0.24
    thickness: int = 4
    dead: bool = False

    def update(self, dt: float):
        self.life -= dt
        if self.life <= 0:
            self.dead = True

    def draw(self, surface, offset):
        ratio = clamp(self.life / self.max_life, 0.0, 1.0)
        progress = 1.0 - ratio
        radius = lerp(self.radius_start, self.radius_end, progress)
        alpha = int(205 * ratio)
        p = self.pos + offset
        size = int(radius * 2 + 24)
        layer = pygame.Surface((size, size), pygame.SRCALPHA)
        c = (size // 2, size // 2)
        pygame.draw.circle(layer, (*self.color, max(8, alpha // 4)), c, int(radius + 7), max(2, self.thickness + 6))
        pygame.draw.circle(layer, (*self.color, alpha), c, int(radius), max(1, self.thickness))
        pygame.draw.circle(layer, (255,255,255,min(180, alpha)), c, max(2, int(radius * 0.34)), 1)
        for i in range(6):
            ang = i * math.tau / 6 + progress * 0.45
            a = pygame.Vector2(math.cos(ang), math.sin(ang))
            q1 = pygame.Vector2(c) + a * (radius * 0.72)
            q2 = pygame.Vector2(c) + a * (radius * 1.10)
            pygame.draw.line(layer, (*self.color, int(alpha * 0.7)), q1, q2, 2)
        surface.blit(layer, (int(p.x - c[0]), int(p.y - c[1])))


@dataclass
class RoomInteractive:
    kind: str
    pos: pygame.Vector2
    radius: float
    hp: float
    max_hp: float
    power: float
    cooldown: float = 0.0
    timer: float = 0.0
    pulse: float = 0.0
    flash: float = 0.0
    dead: bool = False
    used: bool = False

    @property
    def destructible(self) -> bool:
        return self.kind in {"volatile_crystal", "echo_cache", "rift_pylon"}

    def damage(self, amount: float, game: "Game") -> bool:
        if self.dead or not self.destructible or amount <= 0:
            return False
        self.hp -= amount
        self.flash = 0.12
        color = PURPLE if self.kind == "rift_pylon" else CYAN
        game.impact_rings.append(ImpactRing(self.pos.copy(), color, 8, self.radius + 28, 0.18, 0.18, 3))
        for _ in range(7):
            game.particles.append(Particle.burst(self.pos, color, 45, 155, 0.26, 4))
        if self.hp <= 0:
            self.destroy(game)
        return True

    def destroy(self, game: "Game") -> None:
        if self.dead:
            return
        self.dead = True
        if self.kind == "volatile_crystal":
            color = (255, 88, 118); blast = 135.0
            game.impact_rings.append(ImpactRing(self.pos.copy(), color, 18, blast, 0.34, 0.34, 6))
            for _ in range(34): game.particles.append(Particle.burst(self.pos, color, 100, 380, 0.52, 8))
            for enemy in game.enemies:
                if not enemy.dead and enemy.pos.distance_to(self.pos) <= blast + enemy.radius:
                    enemy.take_damage(self.power, game, knockback=normalize(enemy.pos - self.pos) * 42, critical=True)
            if game.player.pos.distance_to(self.pos) <= blast * 0.72 + game.player.radius:
                game.player.take_damage(max(4.0, self.power * 0.22), game)
            game.add_shake(15, 0.20, direction=normalize(game.player.pos - self.pos))
        elif self.kind == "echo_cache":
            gain = max(18, int(self.power))
            game.player_state.stats.ouro += gain; game.score += gain * 2
            game.floating_texts.append(FloatingText(self.pos.copy(), f"+{gain} OURO", YELLOW))
            if random.random() < 0.35: game.heals.append(self.pos.copy())
            for _ in range(18): game.particles.append(Particle.burst(self.pos, YELLOW, 55, 210, 0.45, 5))
        elif self.kind == "rift_pylon":
            blast = 118.0
            for enemy in game.enemies:
                if not enemy.dead and enemy.pos.distance_to(self.pos) <= blast + enemy.radius:
                    enemy.take_damage(self.power * 1.8, game, knockback=normalize(enemy.pos - self.pos) * 25)
            game.impact_rings.append(ImpactRing(self.pos.copy(), PURPLE, 16, blast, 0.30, 0.30, 5))
            game.score += 90

    def update(self, dt: float, game: "Game") -> None:
        if self.dead: return
        self.timer=max(0.0,self.timer-dt); self.pulse+=dt; self.flash=max(0.0,self.flash-dt)
        if self.kind == "healing_well" and not self.used:
            if game.player.hp < game.player.max_hp and game.player.pos.distance_to(self.pos) <= self.radius + game.player.radius + 8:
                self.used=True; game.player.heal(game.player.max_hp*self.power); game.audio.play("heal")
                game.impact_rings.append(ImpactRing(self.pos.copy(), GREEN, 12, 76, 0.40, 0.40, 4))
                game.floating_texts.append(FloatingText(self.pos.copy(), "FONTE RESTAURADA", GREEN)); self.dead=True
            return
        if self.kind == "veil_spike" and self.timer <= 0:
            blast=self.radius+58
            if game.player.pos.distance_to(self.pos) <= blast + game.player.radius: game.player.take_damage(self.power,game)
            for enemy in game.enemies:
                if not enemy.dead and enemy.pos.distance_to(self.pos) <= blast + enemy.radius:
                    enemy.take_damage(self.power*0.72,game,knockback=normalize(enemy.pos-self.pos)*18)
            game.impact_rings.append(ImpactRing(self.pos.copy(),RED,12,blast,0.24,0.24,4)); self.timer=max(1.1,self.cooldown)
            return
        if self.kind == "rift_pylon" and self.timer <= 0:
            blast=self.radius+78
            for enemy in game.enemies:
                if not enemy.dead and enemy.pos.distance_to(self.pos) <= blast + enemy.radius:
                    enemy.attack_cd=max(0.0,enemy.attack_cd-0.18)
            if game.player.pos.distance_to(self.pos) <= blast + game.player.radius:
                game.player.resource=max(0.0,game.player.resource-self.power*0.20)
            game.impact_rings.append(ImpactRing(self.pos.copy(),PURPLE,10,blast,0.28,0.28,3)); self.timer=max(1.4,self.cooldown)

    def draw(self, surface: pygame.Surface, offset: pygame.Vector2) -> None:
        if self.dead: return
        p=self.pos+offset; t=self.pulse
        pulse = 0.5 + 0.5 * math.sin(t * 3.1)
        pygame.draw.ellipse(
            surface, (6, 8, 13),
            (int(p.x - self.radius * 1.05), int(p.y + self.radius * 0.48),
             int(self.radius * 2.1), max(7, int(self.radius * 0.55))),
        )
        if self.kind == "volatile_crystal":
            c=WHITE if self.flash>0 else (238,74,125)
            base_radius = self.radius + pulse * 1.5
            ground = [p + pygame.Vector2(1, 0).rotate(a) * base_radius for a in (0, 72, 144, 216, 288)]
            pygame.draw.polygon(surface, (65, 16, 39), ground)
            pygame.draw.polygon(surface, (190, 43, 100), ground, 2)
            pts=[p+pygame.Vector2(0,-self.radius-pulse*2),p+pygame.Vector2(self.radius*.72,-3),p+pygame.Vector2(8,self.radius),p+pygame.Vector2(-self.radius*.68,4)]
            pygame.draw.polygon(surface,(76,20,42),pts); pygame.draw.polygon(surface,c,pts,3)
            pygame.draw.line(surface,(255,205,230),pts[0],pts[2],2)
            pygame.draw.line(surface,(255,130,190),pts[1],p,1)
            pygame.draw.circle(surface,(255,188,220),p,3)
        elif self.kind == "veil_spike":
            warn=self.timer<0.58; c=(255,88,88) if warn else (111,71,154)
            pygame.draw.circle(surface,(33,21,48),p,int(self.radius+5),0)
            pygame.draw.circle(surface,c,p,int(self.radius+3+math.sin(t*4)*2),2)
            for i in range(6):
                v=pygame.Vector2(1,0).rotate(i*60+t*18)
                pygame.draw.polygon(surface,(42,28,65),[p+v*(self.radius+4),p+v.rotate(18)*12,p+v.rotate(-18)*12])
                pygame.draw.polygon(surface,c,[p+v*(self.radius+4),p+v.rotate(18)*12,p+v.rotate(-18)*12],2)
            if warn:
                warning_radius=int(self.radius+58)
                pygame.draw.circle(surface,(62,22,37),p,warning_radius,1)
                pygame.draw.circle(surface,RED,p,warning_radius,2)
                for i in range(8):
                    v=pygame.Vector2(1,0).rotate(i*45+t*24)
                    pygame.draw.line(surface,(255,142,116),p+v*(warning_radius-6),p+v*warning_radius,2)
        elif self.kind == "echo_cache":
            rect=pygame.Rect(int(p.x-20),int(p.y-13-pulse),40,28)
            pygame.draw.rect(surface,(42,32,28),rect.move(0,4),border_radius=5)
            pygame.draw.rect(surface,(96,65,35),rect,border_radius=5)
            pygame.draw.rect(surface,YELLOW if self.flash<=0 else WHITE,rect,2,border_radius=5)
            pygame.draw.line(surface,(235,177,80),(rect.left+3,rect.top+7),(rect.right-3,rect.top+7),2)
            pygame.draw.rect(surface,(67,45,28),(rect.left+15,rect.top+5,10,rect.height-8),1,border_radius=2)
            pygame.draw.circle(surface,CYAN,(rect.centerx,rect.centery+1),4)
            pygame.draw.circle(surface,WHITE,(rect.centerx,rect.centery+1),2)
        elif self.kind == "healing_well":
            r=int(self.radius+math.sin(t*3.2)*3)
            pygame.draw.circle(surface,(18,39,44),p,r+5)
            pygame.draw.circle(surface,(40,89,75),p,r)
            pygame.draw.circle(surface,GREEN,p,r,3)
            pygame.draw.circle(surface,CYAN,p,max(6,r//3),2)
            pygame.draw.circle(surface,(185,255,221),p,max(2,int(4+pulse*2)))
            for angle in (0,90,180,270):
                v=pygame.Vector2(1,0).rotate(angle+t*12)
                pygame.draw.line(surface,(132,255,201),p+v*(r+3),p+v*(r+9),2)
            pygame.draw.line(surface,WHITE,p+(-7,0),p+(7,0),2)
            pygame.draw.line(surface,WHITE,p+(0,-7),p+(0,7),2)
        else:
            c=WHITE if self.flash>0 else PURPLE
            pygame.draw.circle(surface,(52,24,75),p,int(self.radius+6+pulse*2),2)
            pts=[p+(0,-self.radius-10),p+(self.radius,10),p+(0,self.radius+12),p+(-self.radius,10)]
            pygame.draw.polygon(surface,(24,15,38),pts)
            pygame.draw.polygon(surface,c,pts,3)
            pygame.draw.polygon(surface,(116,65,172),[p+(0,-self.radius+1),p+(self.radius*.52,10),p+(0,self.radius+1),p+(-self.radius*.52,10)],1)
            pygame.draw.circle(surface,c,p,7,2)
            pygame.draw.circle(surface,(235,204,255),p,2)
            for i in range(3):
                q=p+pygame.Vector2(1,0).rotate(t*45+i*120)*(self.radius+8); pygame.draw.circle(surface,c,q,3)
        if self.destructible and self.hp < self.max_hp:
            draw_bar(surface,pygame.Rect(int(p.x-24),int(p.y-self.radius-20),48,5),self.hp,self.max_hp,CYAN if self.kind!="rift_pylon" else PURPLE)


@dataclass
class SpeechBubble:
    text: str
    speaker: str
    color: Tuple[int, int, int]
    life: float
    max_life: float
    priority: int = 1
    dead: bool = False

    def update(self, dt):
        self.life -= dt
        if self.life <= 0:
            self.dead = True

    def draw(self, surface):
        alpha = int(220 * clamp(self.life / self.max_life, 0.0, 1.0))
        box = pygame.Surface((760, 68), pygame.SRCALPHA)
        box.fill((9, 11, 18, alpha))
        pygame.draw.rect(box, (*self.color, alpha), box.get_rect(), 2, border_radius=8)
        draw_text(box, self.speaker, (14, 8), self.color, FONT_SMALL)
        draw_text(box, self.text, (14, 31), WHITE, FONT)
        surface.blit(box, (WIDTH // 2 - 380, HEIGHT - 132))


@dataclass
class AreaEffect:
    kind: str
    pos: pygame.Vector2
    radius: float
    duration: float
    owner: str
    damage: float = 0.0
    tick_interval: float = 0.25
    tick_timer: float = 0.0
    delay: float = 0.0
    color: Tuple[int, int, int] = CYAN
    status: Optional[str] = None
    dead: bool = False
    triggered: bool = False

    def update(self, dt, game: "Game"):
        if self.delay > 0:
            self.delay -= dt
            if self.delay <= 0:
                self.triggered = True
                game.add_shake(10, 0.15)
                for _ in range(28):
                    game.particles.append(Particle.burst(self.pos, self.color, 100, 320, 0.5, 7))
            return

        self.triggered = True
        self.duration -= dt
        self.tick_timer -= dt

        if self.tick_timer <= 0:
            self.tick_timer = self.tick_interval

            if self.owner == "player":
                if self.status == "repair_field" and point_in_circle(game.player.pos, self.pos, self.radius + game.player.radius):
                    game.player.heal(max(1.0, game.player.max_hp * 0.018))
                for enemy in game.enemies:
                    if enemy.dead:
                        continue
                    if point_in_circle(enemy.pos, self.pos, self.radius + enemy.radius):
                        if self.kind == "ice_prison":
                            enemy.slow_timer = max(enemy.slow_timer, 0.55)
                            if self.status == "temporal_slow":
                                enemy.statuses.apply(StatusId.LENTO, 1, duration=0.8, source="skill_area")
                            elif self.status == "fragile_seal":
                                enemy.statuses.apply(StatusId.VULNERAVEL, 1, duration=1.2, source="skill_area")
                            elif self.status == "veil_taunt":
                                enemy.slow_timer = max(enemy.slow_timer, 0.28)
                                enemy.weaken_timer = max(enemy.weaken_timer, 1.1)
                            enemy.take_damage(self.damage, game, critical=False)
                        elif self.kind == "arrow_rain":
                            enemy.take_damage(self.damage, game, critical=False)
                        elif self.kind == "singularity":
                            direction = normalize(self.pos - enemy.pos)
                            enemy.pos += direction * 18
                            enemy.take_damage(self.damage, game, critical=False)
                        elif self.status == "repair_field":
                            # Campo de reparo é suporte; não causa dano em inimigos.
                            pass

        if self.duration <= 0:
            if self.kind == "singularity":
                for enemy in game.enemies:
                    if enemy.dead:
                        continue
                    if point_in_circle(enemy.pos, self.pos, self.radius + enemy.radius):
                        enemy.take_damage(self.damage * 3.4, game, critical=True)
                game.add_shake(16, 0.23)
                game.hit_stop = max(game.hit_stop, 0.08)
            self.dead = True

    def draw(self, surface, offset):
        p = self.pos + offset
        now = pygame.time.get_ticks() * 0.001

        # Telegraph continua legível, mas deixa de ser um círculo vazio genérico.
        if self.delay > 0:
            pulse = 2 + int(abs(math.sin(now * 9.0)) * 3)
            pygame.draw.circle(surface, self.color, p, int(self.radius), pulse)
            inner = max(12, int(self.radius * (0.45 + 0.06 * math.sin(now * 7.0))))
            pygame.draw.circle(surface, (*self.color,), p, inner, 2)
            for a in range(0, 360, 90):
                v1 = pygame.Vector2(1, 0).rotate(a + now * 55) * (self.radius * 0.62)
                v2 = pygame.Vector2(1, 0).rotate(a + now * 55) * (self.radius * 0.86)
                pygame.draw.line(surface, self.color, p + v1, p + v2, 3)
            return

        if self.kind == "singularity":
            # Núcleo negro com anéis em rotação e pequenos braços de distorção.
            pygame.draw.circle(surface, (8, 5, 18), p, int(self.radius))
            for scale, width in ((0.92, 3), (0.68, 2), (0.42, 2)):
                rr = max(8, int(self.radius * scale))
                rect = pygame.Rect(p.x - rr, p.y - rr * 0.55, rr * 2, rr * 1.1)
                pygame.draw.arc(surface, self.color, rect, now * 1.7, now * 1.7 + math.pi * 1.35, width)
            pygame.draw.circle(surface, WHITE, p, 7)
            pygame.draw.circle(surface, self.color, p, 14, 3)
        elif self.kind == "ice_prison":
            # Prisão formada por cristais, não apenas um círculo.
            pygame.draw.circle(surface, self.color, p, int(self.radius), 2)
            for angle in range(0, 360, 45):
                d = pygame.Vector2(1, 0).rotate(angle)
                side = d.rotate(90)
                tip = p + d * self.radius
                base = p + d * (self.radius * 0.58)
                crystal = [tip, base + side * 8, base - side * 8]
                pygame.draw.polygon(surface, self.color, crystal)
                pygame.draw.line(surface, WHITE, base, tip, 2)
        elif self.kind == "arrow_rain":
            # Setas visíveis caindo sobre a área.
            pygame.draw.circle(surface, self.color, p, int(self.radius), 2)
            for i in range(7):
                angle = (i * 137.5 + now * 23.0) % 360
                dist = self.radius * (0.25 + 0.65 * ((i * 0.31 + now * 0.17) % 1.0))
                q = p + pygame.Vector2(1, 0).rotate(angle) * dist
                pygame.draw.line(surface, WHITE, q + (-5, -16), q + (3, 7), 3)
                pygame.draw.line(surface, self.color, q + (3, 7), q + (-3, 1), 2)
                pygame.draw.line(surface, self.color, q + (3, 7), q + (6, 0), 2)
        elif self.kind == "magic_burst":
            # Selo rúnico de explosão.
            pygame.draw.circle(surface, self.color, p, int(self.radius), 3)
            pygame.draw.circle(surface, WHITE, p, max(10, int(self.radius * 0.24)), 2)
            pts = []
            for i in range(6):
                pts.append(p + pygame.Vector2(1, 0).rotate(i * 60 + now * 35) * self.radius * 0.58)
            pygame.draw.polygon(surface, self.color, pts, 2)
            for q in pts[::2]:
                pygame.draw.line(surface, self.color, p, q, 2)
        else:
            # Área genérica com runas rotativas em vez de aro vazio.
            pygame.draw.circle(surface, self.color, p, int(self.radius), 2)
            for a in (0, 120, 240):
                q = p + pygame.Vector2(1, 0).rotate(a + now * 40) * self.radius * 0.72
                pygame.draw.circle(surface, self.color, q, 5, 2)


# ============================================================
# PROJÉTEIS
# ============================================================

@dataclass
class Projectile:
    pos: pygame.Vector2
    vel: pygame.Vector2
    damage: float
    color: Tuple[int, int, int]
    owner: str
    radius: float = 6.0
    life: float = 2.0
    pierce: int = 0
    explosion_radius: float = 0.0
    skill_id: Optional[str] = None
    bleed_damage: float = 0.0
    dead: bool = False
    hit_ids: set = field(default_factory=set)
    trail_points: List[pygame.Vector2] = field(default_factory=list)
    trail_timer: float = 0.0

    def update(self, dt: float, game: "Game"):
        self.pos += self.vel * dt
        self.life -= dt
        self.trail_timer += dt
        while self.trail_timer >= 0.025:
            self.trail_timer -= 0.025
            self.trail_points.append(self.pos.copy())
            if len(self.trail_points) > 6:
                self.trail_points.pop(0)

        if self.life <= 0:
            self.dead = True

        if not ROOM.inflate(150, 150).collidepoint(self.pos):
            self.dead = True

    def explode(self, game: "Game"):
        if self.explosion_radius <= 0:
            return

        game.add_shake(8, 0.13)
        for _ in range(22):
            game.particles.append(
                Particle.burst(self.pos, self.color, 80, 290, 0.45, 7)
            )

        for enemy in game.enemies:
            if enemy.dead:
                continue
            if point_in_circle(enemy.pos, self.pos, self.explosion_radius + enemy.radius):
                enemy.take_damage(self.damage * 0.68, game, critical=False)

    def draw(self, surface, offset):
        p = self.pos + offset
        direction = normalize(self.vel)
        side = direction.rotate(90)
        speed = self.vel.length()
        sid = self.skill_id or ""
        skill = SKILL_REGISTRY.get(sid)
        dtype = skill.damage_type if skill is not None else None
        tags = set(skill.tags) if skill is not None else set()
        now = pygame.time.get_ticks() * 0.001

        trail = self.trail_points
        if trail:
            points = trail + [p]
            for index, (start, end) in enumerate(zip(points, points[1:])):
                strength = (index + 1) / len(points)
                trail_color = tuple(int(channel * (0.20 + strength * 0.62)) for channel in self.color)
                pygame.draw.line(surface, trail_color, start + offset, end + offset, max(1, int(self.radius * strength * 0.8)))

        def line(a, b, color, width):
            pygame.draw.line(surface, color, a, b, max(1, int(width)))

        def arrow(length=34, width=8, color=None):
            c = color or self.color
            tail = p - direction * length * 0.58
            tip = p + direction * length * 0.55
            line(tail, tip, c, max(2, width // 3))
            line(tail, tip, WHITE, 1)
            head = [tip, tip - direction * width * 1.5 + side * width * 0.75, tip - direction * width * 1.5 - side * width * 0.75]
            pygame.draw.polygon(surface, c, head)
            # penas
            line(tail, tail + direction * width + side * width * 0.65, c, 2)
            line(tail, tail + direction * width - side * width * 0.65, c, 2)

        # Flechas e disparos perfurantes: silhueta longa e reconhecível.
        if sid in {"arrow", "burst_arrow", "ricochet", "horizonte_partido"} or "arrow" in tags:
            if sid == "horizonte_partido":
                for k, w in ((52, 10), (38, 5), (25, 2)):
                    line(p - direction * k, p + direction * 26, CYAN if w > 2 else WHITE, w)
                pygame.draw.polygon(surface, WHITE, [p + direction*34, p + direction*14 + side*9, p + direction*14 - side*9])
            else:
                arrow(46 if sid == "burst_arrow" else 38, 9 if sid == "burst_arrow" else 7, YELLOW if sid != "ricochet" else CYAN)
            return

        # Fogo: cometa com cauda, núcleo e pequenas labaredas.
        if sid == "bola_de_fogo" or dtype == DamageType.FOGO:
            for dist, rr, c in ((30, 7, RED), (20, 9, ORANGE), (10, 10, YELLOW)):
                pygame.draw.circle(surface, c, p - direction * dist, rr)
            pygame.draw.circle(surface, ORANGE, p, int(self.radius + 5))
            pygame.draw.circle(surface, YELLOW, p, max(5, int(self.radius)))
            pygame.draw.circle(surface, WHITE, p + direction * 2, 3)
            return

        # Gelo: estilhaço cristalino em forma de lança.
        if dtype == DamageType.GELO or "ice" in tags:
            tip = p + direction * 18
            back = p - direction * 18
            pts = [tip, p + side * 8, back, p - side * 8]
            pygame.draw.polygon(surface, CYAN, pts)
            pygame.draw.polygon(surface, WHITE, [tip, p + side*2, back], 2)
            line(back - direction*18, back, (110, 200, 255), 4)
            return

        # Raio: zig-zag, não esfera.
        if dtype == DamageType.RAIO or "lightning" in tags:
            points = [p - direction * 34]
            for i in range(1, 5):
                t = i / 5.0
                jitter = side * (7 if i % 2 else -7) * (0.75 + 0.25 * math.sin(now * 20 + i))
                points.append(p - direction * (34 - 68*t) + jitter)
            points.append(p + direction * 34)
            pygame.draw.lines(surface, CYAN, False, points, 5)
            pygame.draw.lines(surface, WHITE, False, points, 2)
            return

        # Temporal: losango/estilhaço com eco atrás.
        if sid == "temporal_shard" or dtype == DamageType.TEMPORAL:
            pts = [p + direction*19, p + side*7, p - direction*16, p - side*7]
            pygame.draw.polygon(surface, CYAN, pts)
            pygame.draw.polygon(surface, WHITE, pts, 2)
            for i in range(1, 4):
                q = p - direction * (i * 13)
                pygame.draw.polygon(surface, (79,188,210), [q+direction*7, q+side*3, q-direction*6, q-side*3], 1)
            return

        # Ruptura/Vazio/Sombra: lâmina crescente com rastro quebrado.
        if sid in {"rasgado_basic", "arkhon_rift"} or dtype in {DamageType.VAZIO, DamageType.SOMBRA} or "rift" in tags:
            length = 32 if sid != "arkhon_rift" else 48
            a = p - direction * length * 0.45
            b = p + direction * length * 0.55
            line(a, b, PURPLE if sid == "arkhon_rift" else RED, 8 if sid == "arkhon_rift" else 6)
            line(a + side*6, b, WHITE, 2)
            for i in range(3):
                q = a - direction * (8 + i*8) + side * ((-1)**i * 5)
                line(q, q + direction*8, PURPLE, 2)
            return

        # Artífice: munição mecânica/energia compacta com estabilizadores.
        if sid in {"artifice_bolt", "artifice_burst", "torreta_do_veu"} or "device" in tags or "artifice" in sid:
            front = p + direction * 13
            back = p - direction * 13
            body = [front + side*4, front - side*4, back - side*6, back + side*6]
            pygame.draw.polygon(surface, ORANGE, body)
            pygame.draw.polygon(surface, WHITE, body, 1)
            line(back + side*6, back - direction*8 + side*10, ORANGE, 2)
            line(back - side*6, back - direction*8 - side*10, ORANGE, 2)
            return

        # Oráculo: selo em losango com "olho" central.
        if sid in {"oracle_mark_bolt", "marca_do_pressagio"} or "mark" in tags or dtype == DamageType.ARCANO:
            r = max(7, int(self.radius + 2))
            diamond = [p + direction*r*1.5, p + side*r, p - direction*r*1.5, p - side*r]
            pygame.draw.polygon(surface, PURPLE if self.color == PURPLE else self.color, diamond, 3)
            pygame.draw.circle(surface, WHITE, p, max(2, r//3))
            line(p - direction*24, p - direction*10, self.color, 3)
            return

        # Inimigos: Shooter dispara espinhos do Véu; bosses disparam fragmentos maiores.
        if self.owner == "enemy":
            if self.radius >= 9 or self.color == ORANGE:
                tip = p + direction * 19
                base = p - direction * 15
                pygame.draw.polygon(surface, ORANGE, [tip, base + side*8, base - side*8])
                pygame.draw.polygon(surface, WHITE, [tip, base + side*2, base - side*2], 1)
                line(base - direction*18, base, RED, 4)
            else:
                tip = p + direction * 16
                base = p - direction * 13
                pygame.draw.polygon(surface, PURPLE, [tip, base + side*6, base - side*6])
                line(base - direction*14, base, (120, 64, 180), 3)
            return

        # Físico genérico: dardo, nunca uma bolinha.
        if dtype in {DamageType.FISICO, DamageType.SANGRAMENTO} or "projectile" in tags:
            arrow(34, 7, self.color)
            return

        # Último fallback ainda tem silhueta de cometa/runa; nenhum projétil vira ponto.
        tail = p - direction * 22
        line(tail, p, self.color, max(3, int(self.radius * 0.7)))
        pygame.draw.polygon(surface, self.color, [p + direction*10, p + side*6, p - direction*8, p - side*6])
        pygame.draw.circle(surface, WHITE, p, 2)


# ============================================================
# INIMIGOS / SQUADS
# ============================================================

@dataclass
class EnemyDeathVisual:
    pos: pygame.Vector2
    kind: str
    boss_id: Optional[str]
    radius: float
    facing_left: bool
    tier: str
    mutation_ids: Tuple[str, ...]
    life: float = 0.62
    max_life: float = 0.62
    dead: bool = False

    def update(self, dt: float):
        self.life -= dt
        if self.life <= 0:
            self.dead = True

    def draw(self, surface: pygame.Surface, offset: pygame.Vector2):
        skin = get_enemy_skin(self.kind, self.boss_id)
        if skin is None:
            return
        p = self.pos + offset
        progress = 1.0 - clamp(self.life / self.max_life, 0.0, 1.0)
        base_h = 122 if self.kind == "guardian" else max(58, int(self.radius * 3.1))
        ratio = base_h / max(1, skin.get_height())
        w = max(28, int(skin.get_width() * ratio * (1.0 + progress * 0.16)))
        h = max(32, int(skin.get_height() * ratio * (1.0 - progress * 0.34)))
        frame = pygame.transform.smoothscale(skin, (w, h))
        if self.facing_left:
            frame = pygame.transform.flip(frame, True, False)
        frame = pygame.transform.rotate(frame, (-34 if self.facing_left else 34) * progress)
        frame.set_alpha(int(255 * (1.0 - progress) ** 0.7))
        rect = frame.get_rect(midbottom=(int(p.x), int(p.y + self.radius + 7 + progress * 7)))
        surface.blit(frame, rect)


class Enemy:
    next_id = 1

    def __init__(
        self,
        pos: pygame.Vector2,
        kind: str,
        difficulty: float,
        squad_id: int,
        doctrine: SquadDoctrine,
        role: str,
    ):
        self.uid = Enemy.next_id
        Enemy.next_id += 1

        self.pos = pygame.Vector2(pos)
        self.velocity = pygame.Vector2()
        self.kind = kind
        self.squad_id = squad_id
        self.doctrine = doctrine
        self.role = role

        self.dead = False
        self.flash_timer = 0.0
        self.contact_cd = 0.0
        self.attack_cd = random.uniform(0.3, 1.0)
        self.slow_timer = 0.0
        self.weaken_timer = 0.0
        self.bleed_timer = 0.0
        self.bleed_tick = 0.0
        self.bleed_damage = 0.0
        self.strategy_hint = doctrine.id

        # Metadados de criatura/mutação são preenchidos por Game.decorate_enemy.
        self.creature_id = creature_id_for_archetype(kind)
        self.creature_tier = CreatureTier.COMUM.value
        self.mutation_ids: List[str] = []
        self.superior_name: Optional[str] = None
        self.mutation_behavior_hints: List[str] = []
        # Bosses usam o mesmo corpo runtime enquanto arte final ainda não existe,
        # mas possuem identidade/diretor/padrões próprios.
        self.boss_id: Optional[str] = None
        self.boss_director: Optional[BossDirector] = None
        self.last_boss_pattern: Optional[str] = None
        self.last_boss_phase_name: Optional[str] = None
        self.pending_boss_pattern = None
        self.pending_boss_timer = 0.0
        self.pending_boss_direction = pygame.Vector2(1,0)
        self.stagger_meter = 0.0
        self.stagger_timer = 0.0
        self.statuses = StatusController()

        # Estado visual é separado da IA/hitbox: animações nunca mudam colisão.
        self.visual_time = random.uniform(0.0, 4.0)
        self.visual_state = "idle"
        self.visual_move = pygame.Vector2()
        self.facing_left = False
        self.attack_visual_timer = 0.0
        self.attack_visual_max = 0.0
        self.attack_direction = pygame.Vector2(1, 0)
        self.hurt_visual_timer = 0.0
        self.step_timer = random.uniform(0.0, 0.22)
        self.last_pos = self.pos.copy()

        if kind == "crawler":
            self.radius = 18
            self.max_hp = int(52 * difficulty)
            self.speed = 104 + difficulty * 6
            self.damage = int(10 + difficulty * 1.8)
            self.color = RED
            self.xp = 12
        elif kind == "shooter":
            self.radius = 19
            self.max_hp = int(67 * difficulty)
            self.speed = 78 + difficulty * 4
            self.damage = int(9 + difficulty * 1.6)
            self.color = PURPLE
            self.xp = 18
        elif kind == "tank":
            self.radius = 27
            self.max_hp = int(145 * difficulty)
            self.speed = 55 + difficulty * 2.5
            self.damage = int(15 + difficulty * 2.2)
            self.color = BLUE
            self.xp = 30
        elif kind == "hunter":
            self.radius = 17
            self.max_hp = int(76 * difficulty)
            self.speed = 145 + difficulty * 5
            self.damage = int(12 + difficulty * 2.0)
            self.color = PINK
            self.xp = 24
        else:
            self.radius = 46
            self.max_hp = int(760 * difficulty)
            self.speed = 66
            self.damage = int(20 + difficulty * 3.0)
            self.color = ORANGE
            self.xp = 180

        self.hp = self.max_hp

    def take_damage(
        self,
        amount: float,
        game: "Game",
        knockback: Optional[pygame.Vector2] = None,
        critical: bool = False,
        element: Optional[Element] = None,
        applied_statuses: Sequence[Tuple[StatusId, int]] = (),
    ):
        if self.dead:
            return

        # Equipamentos podem alterar crítico e roubo de vida sem acoplar o inimigo a itens específicos.
        gear = game.player.gear_modifiers()
        if not critical and gear.crit_chance > 0 and random.random() < gear.crit_chance:
            critical = True
            amount *= gear.crit_damage_mult

        # Marca do Duelo cresce enquanto o jogador continua batendo no mesmo alvo.
        if game.player.duel_mark_target_uid is not None:
            if self.uid == game.player.duel_mark_target_uid:
                amount *= 1.0 + game.player.duel_mark_bonus
                game.player.duel_mark_bonus = min(0.45, game.player.duel_mark_bonus + 0.06)
            else:
                game.player.duel_mark_target_uid = None
                game.player.duel_mark_bonus = 0.0

        if getattr(game, "crimson_oil_timer", 0.0) > 0:
            amount *= 1.25

        if element is not None:
            resolution = self.statuses.resolve_hit(amount, element, applied_statuses)
            amount = resolution.final_damage
            for reaction_id in resolution.reaction_ids[:2]:
                game.floating_texts.append(
                    FloatingText(self.pos + pygame.Vector2(0, -self.radius - 28),
                                 reaction_id.replace("_", " ").upper(), CYAN)
                )
                game.add_shake(5, 0.08)

        if amount > 0:
            # Aplica o dano exatamente uma vez. A versão 0.9.8 descontava HP
            # aqui e novamente logo abaixo, dobrando todo dano recebido pelo inimigo.
            self.hp -= amount
            bonus_lifesteal = 0.06 if game.player.active_buffs.get("funeral_mantle", 0) > 0 else 0.0
            game.player.heal(amount * (gear.lifesteal + bonus_lifesteal))

        self.flash_timer = 0.025 if GAME_SETTINGS.accessibility.reduced_flashes else 0.08
        self.hurt_visual_timer = max(self.hurt_visual_timer, 0.24)
        if random.random() < 0.42:
            game.audio.play("enemy_hurt")

        if knockback is not None:
            self.pos += knockback

        if GAME_SETTINGS.accessibility.damage_numbers:
            game.floating_texts.append(
                FloatingText(
                    self.pos + pygame.Vector2(0, -self.radius - 10),
                    str(int(amount)),
                    YELLOW if critical else WHITE,
                )
            )

        game.impact_rings.append(
            ImpactRing(
                self.pos.copy(),
                YELLOW if critical else (element_color(element) if element is not None else self.color),
                radius_start=max(8.0, self.radius * 0.38),
                radius_end=max(34.0, self.radius * (2.0 if critical else 1.45)),
                life=0.30 if critical else 0.20,
                max_life=0.30 if critical else 0.20,
                thickness=5 if critical else 3,
            )
        )
        for _ in range(18 if critical else 10):
            game.particles.append(
                Particle.burst(
                    self.pos,
                    YELLOW if critical else self.color,
                    70,
                    240 if critical else 180,
                    0.34,
                    6,
                )
            )

        impact_dir=normalize(self.pos-game.player.pos)
        game.add_shake(7 if critical else 3,0.12 if critical else 0.07,direction=impact_dir)
        game.hit_stop=max(game.hit_stop,0.05 if critical else 0.018)
        game.telemetry.dano_causado += amount
        game.register_combat_hit(amount,critical=critical,target=self)
        ratio=amount/max(1.0,self.max_hp)
        self.stagger_meter += ratio*(150.0 if self.kind!="guardian" else 75.0)+(18.0 if critical else 0.0)
        threshold=58.0 if self.kind!="guardian" else 100.0
        if self.stagger_meter>=threshold and self.hp>0:
            self.stagger_meter=0.0; self.stagger_timer=0.46 if self.kind!="guardian" else 0.32; self.attack_visual_timer=0.0
            if self.kind=="guardian": self.pending_boss_pattern=None; self.pending_boss_timer=0.0
            game.impact_rings.append(ImpactRing(self.pos.copy(),WHITE,self.radius,self.radius+52,0.30,0.30,5))
            game.floating_texts.append(FloatingText(self.pos+pygame.Vector2(0,-self.radius-28),"QUEBRA!",YELLOW)); game.hit_stop=max(game.hit_stop,0.065)

        if self.hp <= 0:
            self.die(game)

    def die(self, game: "Game"):
        if self.dead:
            return
        game.enemy_deaths.append(EnemyDeathVisual(
            pos=self.pos.copy(),
            kind=self.kind,
            boss_id=self.boss_id,
            radius=self.radius,
            facing_left=self.facing_left,
            tier=str(self.creature_tier),
            mutation_ids=tuple(self.mutation_ids),
        ))
        self.dead = True
        if self.kind != "guardian":
            game.audio.play("enemy_death")

        game.award_xp(self.xp)

        for _ in range(20):
            game.particles.append(Particle.burst(self.pos, self.color, 80, 290, 0.45, 7))

        if "sangue_do_veu" in game.player_state.owned_skills:
            game.player.heal(max(2, int(game.player.max_hp * 0.018)))

        if random.random() < 0.14:
            game.heals.append(self.pos.copy())

        game.try_equipment_drop(self)
        game.on_enemy_killed(self)

        if self.kind == "guardian":
            game.on_boss_defeated(self)

    def apply_bleed(self, damage: float, duration: float = 3.0):
        self.bleed_damage = max(self.bleed_damage, damage)
        self.bleed_timer = max(self.bleed_timer, duration)
        self.bleed_tick = min(self.bleed_tick, 0.35)

    def update_status(self, dt: float, game: "Game"):
        self.flash_timer = max(0.0, self.flash_timer - dt)
        self.contact_cd = max(0.0, self.contact_cd - dt)
        self.attack_cd = max(0.0, self.attack_cd - dt)
        self.slow_timer = max(0.0, self.slow_timer - dt)
        self.weaken_timer = max(0.0, self.weaken_timer - dt)

        if self.bleed_timer > 0:
            self.bleed_timer -= dt
            self.bleed_tick -= dt
            if self.bleed_tick <= 0:
                self.bleed_tick = 0.55
                self.take_damage(self.bleed_damage, game, critical=False)

        status_update = self.statuses.update(dt)
        if status_update.periodic_damage > 0:
            self.take_damage(status_update.periodic_damage, game, critical=False)

    def speed_multiplier(self) -> float:
        base = 0.52 if self.slow_timer > 0 else 1.0
        return base * self.statuses.movement_multiplier()

    def doctrine_vector(self, game: "Game", base_direction: pygame.Vector2) -> pygame.Vector2:
        player = game.player
        to_player = player.pos - self.pos
        distance = max(0.001, to_player.length())
        direction = to_player / distance

        if self.doctrine.id == "cerco":
            if self.role.lower() == "flanker":
                side = -1 if self.uid % 2 else 1
                return normalize(direction + direction.rotate(90) * 0.72 * side)
            return direction

        if self.doctrine.id == "fortaleza":
            squad_center = game.squad_center(self.squad_id)
            if self.role.lower() in ("tank", "vanguard"):
                guard_point = pygame.Vector2.lerp(squad_center, player.pos, 0.42)
                return normalize(guard_point - self.pos)
            if distance < 250:
                return -direction
            if distance > 360:
                return direction
            return direction.rotate(90 if self.uid % 2 else -90)

        if self.doctrine.id == "cacada":
            if self.role.lower() == "hunter" and player.dash_cooldown > 0.55:
                return direction
            return normalize(direction + direction.rotate(90) * (0.18 if self.uid % 2 else -0.18))

        if self.doctrine.id == "isca":
            if self.role.lower() == "vanguard":
                return direction
            if distance < 210:
                return -direction
            return direction.rotate(90 if self.uid % 2 else -90)

        return base_direction

    def update(self, dt: float, game: "Game"):
        if self.dead:
            return

        self.last_pos = self.pos.copy()
        self.visual_time += dt
        self.attack_visual_timer = max(0.0, self.attack_visual_timer - dt)
        self.hurt_visual_timer = max(0.0, self.hurt_visual_timer - dt)
        self.step_timer = max(0.0, self.step_timer - dt)

        self.update_status(dt, game)
        self.stagger_timer=max(0.0,self.stagger_timer-dt)
        self.stagger_meter=max(0.0,self.stagger_meter-dt*(14.0 if self.kind!="guardian" else 9.0))
        if self.stagger_timer>0:
            self.visual_state="hurt"; self.visual_move*=max(0.0,1.0-10.0*dt); return

        p = game.player
        delta = p.pos - self.pos
        distance = max(delta.length(), 0.001)
        direction = delta / distance
        move_direction = self.doctrine_vector(game, direction)
        speed = self.speed * self.speed_multiplier()

        # Modificadores de Ruptura são aplicados dinamicamente.
        rmods = game.player_state.active_rifts.cumulative_modifiers()
        legacy = legacy_modifiers(game.player_state)
        speed_rift = soften_rift_penalty(rmods["enemy_speed_multiplier"], legacy["rift_penalty_mult"], harmful_when_above_one=True)
        damage_rift = soften_rift_penalty(rmods["enemy_damage_multiplier"], legacy["rift_penalty_mult"], harmful_when_above_one=True)
        speed *= speed_rift
        effective_damage = self.damage * damage_rift * self.statuses.outgoing_damage_multiplier()
        if self.weaken_timer > 0:
            effective_damage *= 0.72
        desired_velocity = pygame.Vector2()

        if self.kind == "crawler":
            desired_velocity = move_direction * speed

        elif self.kind == "tank":
            desired_velocity = move_direction * speed
            if distance < 100 and self.attack_cd <= 0:
                self.attack_cd = 1.6
                self.attack_visual_timer = 0.38
                self.attack_visual_max = self.attack_visual_timer
                self.attack_direction = direction.copy()
                if random.random() < 0.22:
                    game.speak_enemy("Colosso", "O Véu fecha sobre você.", ORANGE, priority=1, duration=1.4, cooldown_key="enemy_attack_voice")
                game.audio.play("enemy_attack_melee")
                p.take_damage(effective_damage * 1.25, game)
                if p.mirror_curse_target_uid == self.uid: self.take_damage(effective_damage * 0.28, game)
                p.pos += direction * 38
                game.add_shake(9, 0.14)

        elif self.kind == "hunter":
            desired_velocity = move_direction * speed
            if distance < 90 and self.attack_cd <= 0:
                self.attack_cd = 1.05
                self.attack_visual_timer = 0.30
                self.attack_visual_max = self.attack_visual_timer
                self.attack_direction = direction.copy()
                if random.random() < 0.18:
                    game.speak_enemy("Caçador", "Eu conheço seus passos.", PINK, priority=1, duration=1.4, cooldown_key="enemy_attack_voice")
                game.audio.play("enemy_attack_melee")
                if p.try_parry(self, game):
                    self.pos -= direction * 60
                else:
                    p.take_damage(effective_damage, game)
                    if p.mirror_curse_target_uid == self.uid: self.take_damage(effective_damage * 0.28, game)

        elif self.kind == "shooter":
            ideal = 300
            if distance > ideal + 55:
                desired_velocity = move_direction * speed
            elif distance < ideal - 55:
                desired_velocity = -direction * speed
            else:
                desired_velocity = direction.rotate(90 if self.uid % 2 else -90) * speed * 0.46

            if self.attack_cd <= 0 and distance < 570:
                self.attack_cd = random.uniform(1.0, 1.45)
                self.attack_visual_timer = 0.36
                self.attack_visual_max = self.attack_visual_timer
                self.attack_direction = direction.copy()
                if random.random() < 0.16:
                    game.speak_enemy("Atirador", "Você chama isso de controle?", PURPLE, priority=1, duration=1.4, cooldown_key="enemy_attack_voice")
                game.audio.play("enemy_attack_ranged")
                game.projectiles.append(
                    Projectile(
                        pos=self.pos.copy(),
                        vel=direction * 335,
                        damage=effective_damage,
                        color=PURPLE,
                        owner="enemy",
                        radius=8,
                        life=2.3,
                    )
                )

        else:  # guardian
            self.update_guardian(dt, game, direction, distance, speed, effective_damage)

        if self.kind != "guardian":
            response = 10.0 if desired_velocity.length_squared() > 0 else 15.0
            self.velocity = self.velocity.lerp(desired_velocity, min(1.0, response * dt))
            self.pos += self.velocity * dt

        self.pos.x = clamp(self.pos.x, ROOM.left + self.radius, ROOM.right - self.radius)
        self.pos.y = clamp(self.pos.y, ROOM.top + self.radius, ROOM.bottom - self.radius)

        delta_move = self.pos - self.last_pos
        moved = delta_move.length_squared() > 0.02
        if moved:
            self.visual_move = normalize(delta_move)
            if abs(self.visual_move.x) > 0.08:
                self.facing_left = self.visual_move.x < 0
            if self.step_timer <= 0 and self.kind != "guardian":
                if self.kind == "crawler":
                    game.audio.play("enemy_step_crawler")
                    self.step_timer = 0.29
                elif self.kind == "tank":
                    game.audio.play("enemy_step_heavy")
                    self.step_timer = 0.44
                else:
                    game.audio.play("enemy_step_light")
                    self.step_timer = 0.34
        else:
            self.visual_move *= max(0.0, 1.0 - 8.0 * dt)

        if self.hurt_visual_timer > 0:
            self.visual_state = "hurt"
        elif self.attack_visual_timer > 0:
            self.visual_state = "attack"
        elif moved:
            self.visual_state = "run" if self.kind == "hunter" else "walk"
        else:
            self.visual_state = "idle"

        if self.kind in ("crawler",) and distance < self.radius + p.radius + 4:
            if self.contact_cd <= 0:
                self.contact_cd = 0.72
                self.attack_visual_timer = 0.26
                self.attack_visual_max = self.attack_visual_timer
                self.attack_direction = direction.copy()
                if random.random() < 0.12:
                    game.speak_enemy("Rastejante", "Esqueça.", RED, priority=1, duration=1.1, cooldown_key="enemy_attack_voice")
                game.audio.play("enemy_attack_melee")
                if p.try_parry(self, game):
                    self.pos -= direction * 55
                else:
                    p.take_damage(effective_damage, game)
                    self.pos -= direction * 24

    def execute_boss_pattern(self, game: "Game", pattern, direction: pygame.Vector2, effective_damage: float):
        danger=pattern.danger; tags=set(pattern.tags); direction=normalize(direction); distance=game.player.pos.distance_to(self.pos)
        game.impact_rings.append(ImpactRing(self.pos.copy(),ORANGE,self.radius,self.radius+72+danger*5,0.24,0.24,5))
        if "melee" in tags or "dash" in tags or "leap" in tags:
            self.pos += direction*(80+danger*12)
            if distance < 155+danger*4:
                if game.player.try_parry(self,game): self.pos -= direction*76
                else: game.player.take_damage(effective_damage*(0.75+danger*0.06),game)
            for _ in range(22): game.particles.append(Particle.burst(self.pos,ORANGE,90,300,0.36,6))
            game.add_shake(6+danger,0.12,direction=direction); return
        if "line" in tags:
            count=5+max(0,danger-5); spread=34+danger*2
            for i in range(count):
                t=0.5 if count==1 else i/(count-1); d=direction.rotate(-spread/2+spread*t)
                game.projectiles.append(Projectile(self.pos.copy(),d*(335+danger*18),effective_damage*0.86,ORANGE,"enemy",radius=9,life=2.4))
            game.add_shake(6+danger,0.12,direction=direction); return
        count=8+danger*2
        if "ultimate" in tags: count+=4
        offset=math.pi/count if "feint" in tags or "delayed" in tags else 0.0
        for i in range(count):
            angle=math.tau*i/count+offset; v=pygame.Vector2(math.cos(angle),math.sin(angle))
            game.projectiles.append(Projectile(self.pos.copy(),v*(230+danger*19),effective_damage*(0.70 if count>16 else 0.80),ORANGE,"enemy",radius=8 if danger<7 else 10,life=2.8))
        if "ultimate" in tags:
            for _ in range(30): game.particles.append(Particle.burst(self.pos,PURPLE,110,390,0.55,8))
            game.cinematic_flash=max(game.cinematic_flash,0.12)
        if danger>=6: game.add_shake(7+danger,0.15)

    def update_guardian(self, dt: float, game: "Game", direction: pygame.Vector2, distance: float, speed: float, effective_damage: float):
        boss_id=self.boss_id or "guardiao_primeira_ruptura"; memory=game.player_state.veil_memory.get(boss_id); strategy=game.current_boss_strategy; director=self.boss_director
        hp_ratio=self.hp/max(1.0,self.max_hp); phase_speed=1.0; phase_damage=1.0
        if director is not None:
            phase=director.phase_for_hp(hp_ratio)
            if self.last_boss_phase_name is None: self.last_boss_phase_name=phase.nome
            elif phase.nome != self.last_boss_phase_name:
                self.last_boss_phase_name=phase.nome; game.audio.play("boss_phase"); game.start_boss_phase_transition(self,phase.nome)
            phase_speed=phase.speed_mult; phase_damage=phase.damage_mult
        speed*=phase_speed; effective_damage*=phase_damage

        # Telegraph real: direção fica travada e o golpe só acontece depois da janela visual.
        if self.pending_boss_pattern is not None:
            self.pending_boss_timer=max(0.0,self.pending_boss_timer-dt); self.attack_visual_timer=max(self.attack_visual_timer,self.pending_boss_timer); self.attack_visual_max=max(self.attack_visual_max,0.001)
            if self.pending_boss_timer<=0:
                pattern=self.pending_boss_pattern; locked=self.pending_boss_direction.copy(); self.pending_boss_pattern=None
                self.execute_boss_pattern(game,pattern,locked,effective_damage)
            return

        if strategy in ("fechar_distancia","salto_de_aproximacao","pressao_continua"):
            if distance>125: self.pos += direction*speed*1.32*dt
        elif strategy in ("flanquear_ranged","flanco","cortar_rota_fuga"):
            self.pos += normalize(direction+direction.rotate(90)*0.7)*speed*dt
        elif distance>165: self.pos += direction*speed*dt
        if self.attack_cd>0: return
        game.audio.play("enemy_roar")
        if director is not None:
            pattern=director.choose_pattern(hp_ratio); self.last_boss_pattern=pattern.id; self.pending_boss_pattern=pattern; self.pending_boss_direction=direction.copy()
            self.pending_boss_timer=max(0.30,float(pattern.telegraph)); self.attack_cd=max(0.65,self.pending_boss_timer+pattern.duration*0.30)
            self.attack_visual_timer=self.pending_boss_timer; self.attack_visual_max=self.pending_boss_timer; self.attack_direction=direction.copy()
            if pattern.voice_line: game.speak_enemy(BOSSES[boss_id].nome,pattern.voice_line,ORANGE,priority=4,duration=max(1.2,self.pending_boss_timer+0.4))
            return
        self.attack_cd=1.25; self.attack_visual_timer=0.42; self.attack_visual_max=self.attack_visual_timer; self.attack_direction=direction.copy()
        for i in range(12):
            angle=math.tau*i/12; v=pygame.Vector2(math.cos(angle),math.sin(angle)); game.projectiles.append(Projectile(self.pos.copy(),v*275,effective_damage*0.80,ORANGE,"enemy",radius=8,life=2.7))

    def draw(self, surface, offset):
        p = self.pos + offset
        skin_key = self.boss_id if (self.kind == "guardian" and self.boss_id) else self.kind
        static_skin = get_enemy_skin(self.kind, self.boss_id if self.kind == "guardian" else None)
        anim_progress = None
        if self.visual_state == "attack" and self.attack_visual_max > 0:
            anim_progress = 1.0 - clamp(self.attack_visual_timer / self.attack_visual_max, 0.0, 1.0)
        elif self.visual_state == "hurt":
            anim_progress = 1.0 - clamp(self.hurt_visual_timer / 0.24, 0.0, 1.0)
        skin = get_enemy_animation_frame(
            BASE_DIR,
            skin_key or self.kind,
            self.visual_state,
            self.visual_time,
            progress=anim_progress,
        ) or static_skin

        # Sombra ancora o monstro no chão. A hitbox real continua sendo self.radius.
        shadow_w = int(self.radius * (2.0 if self.kind != "guardian" else 2.5))
        pygame.draw.ellipse(surface, (4, 5, 8),
                            (p.x - shadow_w/2, p.y + self.radius*0.55, shadow_w, max(8, self.radius*0.55)))

        if skin is not None:
            state = self.visual_state
            t = self.visual_time
            bob = 0.0
            angle = 0.0
            sx = sy = 1.0
            tint = (255, 255, 255)

            if state == "idle":
                bob = math.sin(t * (2.4 if self.kind == "tank" else 3.8)) * 1.4
                sx = 1.0 + math.sin(t * 2.7) * 0.012
                sy = 1.0 - math.sin(t * 2.7) * 0.010
            elif state == "attack":
                phase = 1.0 - clamp(self.attack_visual_timer / max(0.001, self.attack_visual_max), 0.0, 1.0)
                facing = 1.0 if self.facing_left else -1.0
                if phase < 0.30:
                    windup = phase / 0.30
                    angle = -facing * 8.0 * windup
                    sx = 1.0 - 0.045 * windup
                    sy = 1.0 + 0.035 * windup
                    bob = 2.0 * windup
                elif phase < 0.72:
                    strike = (phase - 0.30) / 0.42
                    impulse = math.sin(strike * math.pi)
                    angle = facing * (8.0 + 8.0 * impulse)
                    sx = 1.0 + impulse * (0.17 if self.kind == "tank" else 0.12)
                    sy = 1.0 - impulse * 0.075
                    bob = -4.0 * impulse
                else:
                    recovery = (phase - 0.72) / 0.28
                    angle = lerp(facing * 8.0, 0.0, recovery)
                    sx = lerp(1.035, 1.0, recovery)
                    sy = lerp(0.98, 1.0, recovery)
                    bob = -2.0 * (1.0 - recovery)
            elif state == "hurt":
                phase = clamp(self.hurt_visual_timer / 0.24, 0.0, 1.0)
                angle = (8.0 if self.facing_left else -8.0) * phase
                sx, sy = 0.96, 1.04
                tint = (255, 150, 150)

            if self.kind == "guardian":
                base_h = 126
            else:
                base_h = max(58, min(94, int(self.radius * 3.2)))
            ratio = base_h / max(1, skin.get_height())
            w = max(28, int(skin.get_width() * ratio * sx))
            h = max(34, int(skin.get_height() * ratio * sy))
            frame = cached_sprite_transform(skin, w, h, self.facing_left, angle)
            if tint != (255,255,255):
                frame = frame.copy()
                frame.fill((*tint,255), special_flags=pygame.BLEND_RGBA_MULT)
            if self.flash_timer > 0:
                frame = frame.copy()
                frame.fill((150,150,150,150), special_flags=pygame.BLEND_RGBA_ADD)

            # Hunter ganha um pequeno afterimage em corrida, reforçando a função de perseguidor.
            if self.kind == "hunter" and state == "run" and self.visual_move.length_squared() > 0:
                ghost = frame.copy(); ghost.set_alpha(68)
                gp = p - self.visual_move * 18
                grect = ghost.get_rect(midbottom=(int(gp.x), int(gp.y + self.radius + 9 + bob)))
                surface.blit(ghost, grect)

            rect = frame.get_rect(midbottom=(int(p.x), int(p.y + self.radius + 9 + bob)))
            surface.blit(frame, rect)
        else:
            # Fallback: se um asset faltar, o inimigo continua jogável.
            color = WHITE if self.flash_timer > 0 else self.color
            pygame.draw.circle(surface, color, p, int(self.radius))
            pygame.draw.circle(surface, BLACK, p, int(self.radius), 3)

        if self.attack_visual_timer > 0 and self.attack_visual_max > 0:
            progress = 1.0 - clamp(self.attack_visual_timer / self.attack_visual_max, 0.0, 1.0)
            direction = normalize(self.attack_direction)
            side = pygame.Vector2(-direction.y, direction.x)
            reach = self.radius + 30 + progress * (34 if self.kind != "guardian" else 58)
            width = max(7, int(9 + progress * 10))
            color = (255, 116, 72) if progress < 0.55 else (255, 220, 112)
            base = p + direction * (self.radius * 0.25)
            tip = p + direction * reach
            pygame.draw.polygon(surface, color, [base + side * width, tip, base - side * width], 2)
            if self.kind == "shooter":
                pygame.draw.line(surface, color, p + direction * self.radius, tip, width)

        if self.kind == "guardian" and self.attack_visual_timer > 0:
            charge = clamp(self.attack_visual_timer / max(0.001, self.attack_visual_max), 0.0, 1.0)
            tele_r = int(self.radius + 36 + (1.0 - charge) * 20)
            pulse_col = (255, 96, 72) if charge > 0.45 else (255, 218, 112)
            pygame.draw.circle(surface, pulse_col, p, tele_r, 3)
            pygame.draw.circle(surface, WHITE, p, max(self.radius + 10, tele_r - 14), 1)
            d = normalize(self.attack_direction)
            tip = p + d * (self.radius + 120)
            self_line_start = p + d * (self.radius + 12)
            pygame.draw.line(surface, pulse_col, self_line_start, tip, 4)
            for i in range(4):
                q = p + pygame.Vector2(1,0).rotate(i*90 + self.visual_time*55) * (tele_r + 8)
                pygame.draw.circle(surface, pulse_col, q, 4, 2)

        if self.kind=="guardian" and self.pending_boss_pattern is not None:
            tags=set(self.pending_boss_pattern.tags); d=normalize(self.pending_boss_direction); side=pygame.Vector2(-d.y,d.x)
            charge=clamp(self.pending_boss_timer/max(0.001,self.attack_visual_max),0.0,1.0); tele=(255,92,74) if charge>0.35 else (255,224,120)
            overlay=pygame.Surface((WIDTH,HEIGHT),pygame.SRCALPHA)
            if "line" in tags:
                start=p+d*(self.radius+10); end=p+d*640; width=18+int((1-charge)*12)
                pygame.draw.line(overlay,(*tele,38),start,end,width); pygame.draw.line(surface,tele,start,end,3)
            elif "melee" in tags or "dash" in tags or "leap" in tags:
                reach=150+self.pending_boss_pattern.danger*8; half=38
                pts=[p,p+d.rotate(-half)*reach,p+d*reach,p+d.rotate(half)*reach]
                pygame.draw.polygon(overlay,(*tele,34),pts); pygame.draw.polygon(surface,tele,pts,2)
            else:
                r=int(110+self.pending_boss_pattern.danger*10)
                pygame.draw.circle(overlay,(*tele,28),p,r); pygame.draw.circle(surface,tele,p,r,3)
            surface.blit(overlay,(0,0))

        # Elite/Superior e mutações deixam marcas visuais legíveis sem aumentar a hitbox.
        tier_color = enemy_tier_color(str(self.creature_tier))
        tier_raw = str(self.creature_tier).lower()
        if "elite" in tier_raw or "super" in tier_raw or self.kind == "guardian":
            pulse = 2 + int(abs(math.sin(self.visual_time * 4.0)) * 3)
            pygame.draw.circle(surface, tier_color, p, int(self.radius + 8 + pulse), 2)
        if self.mutation_ids:
            orbit = self.radius + 13
            for i, _mid in enumerate(self.mutation_ids[:3]):
                ang = self.visual_time * (1.4 + i * 0.2) + i * math.tau / 3
                q = p + pygame.Vector2(math.cos(ang), math.sin(ang)) * orbit
                pygame.draw.circle(surface, tier_color, q, 3 + (i % 2))

        bar = pygame.Rect(int(p.x - 28), int(p.y - self.radius - 22), 56, 7)
        draw_bar(surface, bar, self.hp, self.max_hp, GREEN)

        if self.bleed_timer > 0:
            pygame.draw.circle(surface, RED, p, int(self.radius + 5), 2)

        if self.superior_name and "super" in tier_raw:
            draw_text(surface, self.superior_name[:28], (p.x, p.y - self.radius - 37), tier_color, FONT_TINY, True)


# ============================================================
# PLAYER RUNTIME
# ============================================================

@dataclass
class PendingCast:
    skill_id: str
    timer: float
    target: pygame.Vector2
    aim: pygame.Vector2
    incantation_spoken: bool = False


class PlayerAvatar:
    def __init__(self, state: PlayerState):
        self.state = state
        self.pos = pygame.Vector2(WIDTH / 2, HEIGHT / 2)
        self.prev_pos = self.pos.copy()
        self.radius = 21
        self.velocity = pygame.Vector2()

        self.max_hp = self.effective_max_hp()
        self.hp = float(self.max_hp)
        self.resource_max = float(state.class_definition().recurso_maximo)

        # Guerreiro acumula Fadiga e Rasgado acumula Corrupção.
        if state.identity.class_id in ("guerreiro", "rasgado"):
            self.resource = 0.0
        else:
            self.resource = self.resource_max

        self.attack_cooldown = 0.0
        self.special_cooldown = 0.0
        self.dash_cooldown = 0.0
        self.invuln = 0.0
        self.parry_window = 0.0
        self.parry_cooldown = 0.0
        self.shield_timer = 0.0
        self.attack_counter = 0
        self.pending_cast: Optional[PendingCast] = None
        self.last_dash_direction = pygame.Vector2()
        self.low_hp_pulse_cd = 0.0
        self.position_history: List[pygame.Vector2] = []
        self.position_history_timer = 0.0
        self.duelist_combo = 0

        # Animacao visual: a hitbox continua sendo o circulo logico; a skin e apenas render.
        self.visual_time = 0.0
        self.visual_state = "idle"
        self.visual_move = pygame.Vector2()
        self.dash_visual_timer = 0.0
        self.hurt_visual_timer = 0.0
        self.attack_visual_timer = 0.0
        self.footstep_timer = 0.0
        self.skin = get_player_skin(self.class_id, self.state.identity.genero)

        # Mira independente do movimento. Mouse atualiza normalmente; setas direcionais
        # permitem mirar sem tirar a mão do teclado. Ataques sempre usam esta direção.
        self.aim_dir = pygame.Vector2(1, 0)
        self.aim_target = self.pos + self.aim_dir * 320

        # Buffs genéricos usados pelos kits 0.8. A chave é semântica e o valor é tempo restante.
        self.active_buffs: Dict[str, float] = {}
        self.war_oath_charge = 0.0
        self.duel_mark_target_uid: Optional[int] = None
        self.duel_mark_bonus = 0.0
        self.mirror_curse_target_uid: Optional[int] = None
        self.attack_buffer_timer = 0.0
        self.special_buffer_timer = 0.0
        self.dash_buffer_timer = 0.0
        self.daily_damage_mult = 1.0

        self.active_slots: List[str] = []
        self.refresh_skill_slots()

    @property
    def class_id(self) -> str:
        return self.state.identity.class_id

    @property
    def color(self):
        return CLASS_COLORS.get(self.class_id, WHITE)

    def gear_modifiers(self):
        return equipment_modifiers(self.state)

    def effective_max_hp(self) -> int:
        mods = self.gear_modifiers()
        legacy = legacy_modifiers(self.state)
        base = self.state.class_definition().hp_base + self.state.stats.vida_maxima_bonus
        rift = self.state.active_rifts.cumulative_modifiers()["max_hp_multiplier"]
        rift = soften_rift_penalty(rift, legacy["rift_penalty_mult"], harmful_when_above_one=False)
        hp = (base * rift + mods.hp_flat) * mods.hp_mult * legacy["max_hp_mult"]
        if "player_hp_60" in active_trial_modifiers_for_state(self.state):
            hp *= 0.60
        return max(1, int(hp))

    def combat_damage(self) -> float:
        mods = self.gear_modifiers()
        legacy = legacy_modifiers(self.state)
        base_damage = self.state.class_definition().dano_base + self.state.stats.dano_bonus
        rift_damage = self.state.active_rifts.cumulative_modifiers()["player_damage_multiplier"]
        rift_damage = soften_rift_penalty(rift_damage, legacy["rift_penalty_mult"], harmful_when_above_one=False)
        value = (base_damage * rift_damage + mods.damage_flat) * mods.damage_mult
        value *= float(synergy_modifiers(self.state)["damage_mult"])

        # Rasgado cresce com a própria corrupção acumulada.
        if self.class_id == "rasgado":
            value *= 1.0 + (self.resource / max(1.0, self.resource_max)) * 0.55

        # Buffs de kit 0.8: bônus deliberadamente moderados para manter leitura e balanceamento.
        if self.active_buffs.get("hunter_step", 0) > 0:
            value *= 1.35
        if self.active_buffs.get("future_echo", 0) > 0:
            value *= 1.45
        if self.active_buffs.get("torn_apotheosis", 0) > 0:
            value *= 1.45
        if self.active_buffs.get("war_oath", 0) > 0:
            value *= 1.0 + self.war_oath_charge
        if self.active_buffs.get("final_citadel", 0) > 0:
            value *= 1.08
        if self.active_buffs.get("imminent_vision", 0) > 0:
            value *= 1.10
        if "carne_do_vazio" in self.state.owned_skills and self.hp / max(1, self.max_hp) <= 0.35:
            value *= 1.18
        if "player_damage_135" in active_trial_modifiers_for_state(self.state):
            value *= 1.35

        value *= max(0.50, float(self.daily_damage_mult))
        return value

    def consume_next_attack_buffs(self):
        for buff_id in ("hunter_step", "future_echo", "war_oath"):
            if self.active_buffs.get(buff_id, 0) > 0:
                self.active_buffs.pop(buff_id, None)
        self.war_oath_charge = 0.0

    def refresh_from_state(self):
        old_max = self.max_hp
        self.max_hp = self.effective_max_hp()
        if self.max_hp > old_max:
            self.hp += self.max_hp - old_max
        self.hp = min(self.hp, self.max_hp)
        self.refresh_skill_slots()

    def refresh_skill_slots(self):
        available: List[str] = []
        for skill_id in sorted(self.state.owned_skills):
            definition = SKILL_REGISTRY[skill_id]
            if definition.categoria == SkillCategory.PASSIVA:
                continue
            available.append(skill_id)

        # Preserva a ordem já equipada. Aprender uma habilidade nova não deve
        # embaralhar Q/E/R/F só porque o ID dela vem antes alfabeticamente.
        saved = self.state.campaign.get("skill_loadout", []) if isinstance(self.state.campaign, dict) else []
        preferred = list(self.active_slots) if self.active_slots else list(saved)
        slots = [skill_id for skill_id in preferred if skill_id in available]
        for skill_id in available:
            if skill_id not in slots and len(slots) < 4:
                slots.append(skill_id)
        self.active_slots = slots[:4]
        if isinstance(self.state.campaign, dict):
            self.state.campaign["skill_loadout"] = list(self.active_slots)

    def movement_speed(self) -> float:
        mods = self.gear_modifiers()
        synergy = synergy_modifiers(self.state)
        legacy = legacy_modifiers(self.state)
        base_speed = self.state.class_definition().velocidade_base + self.state.stats.velocidade_bonus
        rift_speed = self.state.active_rifts.cumulative_modifiers()["player_speed_multiplier"]
        rift_speed = soften_rift_penalty(rift_speed, legacy["rift_penalty_mult"], harmful_when_above_one=False)
        return (base_speed * rift_speed + mods.speed_flat) * mods.speed_mult * float(synergy["speed_mult"]) * legacy["speed_mult"]

    def heal(self, amount: float):
        if "healing_disabled" in active_trial_modifiers_for_state(self.state):
            return
        mods = self.gear_modifiers()
        synergy = synergy_modifiers(self.state)
        legacy = legacy_modifiers(self.state)
        healing = amount * mods.healing_mult * float(synergy["healing_mult"]) * legacy["healing_mult"]
        self.hp = min(self.max_hp, self.hp + healing)

    def take_damage(self, amount: float, game: "Game"):
        if self.invuln > 0:
            return

        if getattr(game, "rift_guard_timer", 0.0) > 0 and self.state.active_rifts.active_ids:
            amount *= 0.78
        if getattr(game, "crimson_oil_timer", 0.0) > 0:
            amount *= 1.15
        if self.shield_timer > 0:
            amount *= 0.35
        if self.active_buffs.get("funeral_mantle", 0) > 0:
            amount *= 0.72
        if self.active_buffs.get("protective_chain", 0) > 0:
            amount *= 0.70
        if self.active_buffs.get("final_citadel", 0) > 0:
            amount *= 0.52
        if "carne_do_vazio" in self.state.owned_skills and self.hp / max(1, self.max_hp) <= 0.35:
            amount *= 0.82
        if self.hp / max(1, self.max_hp) <= 0.30:
            amount *= max(0.50, 1.0 - legacy_modifiers(self.state).get("low_hp_resist", 0.0))
        if self.active_buffs.get("war_oath", 0) > 0:
            self.war_oath_charge = min(0.60, self.war_oath_charge + amount / max(1.0, self.max_hp) * 1.6)

        self.hp -= amount
        game.break_combo()
        game.trigger_experience("first_damage_taken")
        self.hurt_visual_timer = 0.28
        game.audio.play("player_hurt")
        if self.class_id == "duelista":
            self.duelist_combo = 0
        self.invuln = 0.52
        game.telemetry.dano_recebido += amount
        source=game.nearest_enemy(self.pos)
        shake_dir=normalize(self.pos-source.pos) if source is not None else pygame.Vector2()
        game.add_shake(11,0.18,direction=shake_dir)
        game.hit_stop = max(game.hit_stop, 0.04)
        if GAME_SETTINGS.accessibility.damage_numbers:
            game.floating_texts.append(
                FloatingText(self.pos + pygame.Vector2(0, -34), f"-{int(amount)}", RED)
            )

        game.impact_rings.append(ImpactRing(self.pos.copy(), RED, 14, 62, 0.28, 0.28, 4))
        for _ in range(18):
            game.particles.append(Particle.burst(self.pos, RED, 70, 300, 0.42, 7))

        # Pulso de Sobrevivência (passiva universal)
        if (
            "pulso_de_sobrevivencia" in self.state.owned_skills
            and self.hp / max(1, self.max_hp) <= 0.25
            and self.low_hp_pulse_cd <= 0
        ):
            self.low_hp_pulse_cd = 18.0
            for enemy in game.enemies:
                dist = enemy.pos.distance_to(self.pos)
                if dist < 150:
                    enemy.pos += normalize(enemy.pos - self.pos) * 55
                    enemy.take_damage(20, game)
            game.speak_player("NÃO AINDA!", priority=2)

    def request_basic_attack(self, game: "Game"):
        if self.attack_cooldown > 0 or self.pending_cast is not None:
            self.attack_buffer_timer=max(self.attack_buffer_timer,0.16); return
        self.basic_attack(game)

    def request_secondary(self, game: "Game"):
        if self.special_cooldown > 0 or self.pending_cast is not None:
            self.special_buffer_timer=max(self.special_buffer_timer,0.18); return
        self.use_secondary(game)

    def request_dash(self, game: "Game"):
        if self.dash_cooldown > 0 or self.pending_cast is not None:
            self.dash_buffer_timer=max(self.dash_buffer_timer,0.15); return
        self.dash(game)

    def start_parry(self, game: "Game"):
        if self.class_id not in ("guerreiro", "duelista"):
            return
        if self.parry_cooldown > 0:
            return

        legacy = legacy_modifiers(self.state)
        self.parry_window = 0.22 * legacy["parry_window_mult"]
        self.parry_cooldown = 1.1
        game.speak_player(random.choice(("VENHA!", "AGORA.")), priority=1, cooldown_key="parry")

    def try_parry(self, enemy: Enemy, game: "Game") -> bool:
        if self.parry_window <= 0:
            return False

        self.parry_window = 0
        self.invuln = 0.24
        game.audio.play("parry")
        game.telemetry.parries += 1
        game.add_shake(15, 0.21)
        game.hit_stop = max(game.hit_stop, 0.075)

        damage = self.combat_damage() * 1.6
        enemy.take_damage(
            damage,
            game,
            knockback=normalize(enemy.pos - self.pos) * 62,
            critical=True,
        )

        game.slashes.append(
            SlashVisual(self.pos.copy(), enemy.pos - self.pos, CYAN, 98, 0.17, 12)
        )
        game.speak_player("MINHA VEZ!", priority=3, cooldown_key="perfect_parry")
        return True

    def dash(self, game: "Game"):
        if self.dash_cooldown > 0 or self.pending_cast is not None:
            return

        keys = pygame.key.get_pressed()
        direction = pygame.Vector2(
            keys[binding_key("move_right", pygame.K_d)] - keys[binding_key("move_left", pygame.K_a)],
            keys[binding_key("move_down", pygame.K_s)] - keys[binding_key("move_up", pygame.K_w)],
        )

        if direction.length_squared() == 0:
            direction = pygame.Vector2(pygame.mouse.get_pos()) - self.pos

        direction = normalize(direction)
        self.last_dash_direction = direction.copy()
        self.dash_visual_timer = 0.24
        self.prev_pos = self.pos.copy()
        game.audio.play("dash")
        self.pos += direction * 122
        self.dash_cooldown = 0.95 * legacy_modifiers(self.state)["dash_cooldown_mult"]
        self.invuln = 0.24

        game.telemetry.dashes_total += 1
        nearest = game.nearest_enemy(self.pos)
        if nearest is not None:
            to_enemy = normalize(nearest.pos - self.pos)
            # Se o dash aponta para longe do inimigo, conta como para trás.
            if direction.dot(to_enemy) < -0.25:
                game.telemetry.dashes_para_tras += 1

        for _ in range(16):
            game.particles.append(Particle.burst(self.prev_pos, self.color, 45, 150, 0.34, 5))

        # Passo Fantasma cria eco puramente visual + pequena desorientação.
        if "passo_fantasma" in self.state.owned_skills:
            for enemy in game.enemies:
                if enemy.pos.distance_to(self.prev_pos) < 125:
                    enemy.attack_cd += 0.25

    def basic_attack(self, game: "Game"):
        if self.attack_cooldown > 0 or self.pending_cast is not None:
            return

        basic_resource_cost = BASIC_ATTACK_RESOURCE_COSTS.get(self.class_id, 0.0)
        if self.class_id == "guerreiro" and self.resource >= 92:
            return
        if self.class_id == "rasgado" and self.resource >= 98:
            return
        if basic_resource_cost and self.resource < basic_resource_cost:
            return

        aim = normalize(self.aim_dir)
        damage = self.combat_damage()
        self.consume_next_attack_buffs()
        self.attack_visual_timer = max(self.attack_visual_timer, BASIC_ATTACK_VISUAL_DURATION)

        if self.class_id == "guerreiro":
            self.resource += 12
            if random.random() < 0.08:
                game.speak_player("ABRAM CAMINHO!", priority=1, cooldown_key="basic_warrior_voice", duration=1.0)
            game.audio.play("sword_swing")
            self.attack_cooldown = 0.34
            self.attack_counter += 1
            critical = self.attack_counter % 10 == 0

            game.telemetry.ataques_melee += 1
            game.slashes.append(
                SlashVisual(
                    self.pos.copy(),
                    aim,
                    YELLOW if critical else WHITE,
                    92 if not critical else 112,
                    0.13 if not critical else 0.17,
                    10 if not critical else 14,
                )
            )

            hit_any = False
            for enemy in game.enemies:
                delta = enemy.pos - self.pos
                if delta.length() <= 98 + enemy.radius:
                    direction = normalize(delta)
                    if direction.dot(aim) > 0.30:
                        enemy.take_damage(
                            damage * (1.9 if critical else 1.0),
                            game,
                            knockback=aim * (34 if critical else 16),
                            critical=critical,
                        )
                        hit_any = True

            if critical:
                game.add_shake(12, 0.16)
                game.hit_stop = max(game.hit_stop, 0.08)
                game.speak_player("DÉCIMO IMPACTO!", priority=3, cooldown_key="tenth_hit")
                if "arkhon_tenth_rift" in active_unique_effects(self.state):
                    game.projectiles.append(Projectile(self.pos + aim * 30, aim * 720, damage * 1.05, PURPLE, "player", radius=10, life=1.0, pierce=2, skill_id="arkhon_rift"))
            elif hit_any:
                game.audio.play("sword_hit")
                game.add_shake(2.5, 0.06)

        elif self.class_id == "mago":
            self.resource -= basic_resource_cost
            if random.random() < 0.06:
                game.speak_player("EXPLODA!", priority=1, cooldown_key="basic_mage_voice", duration=1.0)
            game.audio.play("magic_cast")
            self.attack_cooldown = 0.26
            game.telemetry.ataques_magicos += 1
            game.projectiles.append(
                Projectile(
                    self.pos + aim * 28,
                    aim * 520,
                    damage * 0.78,
                    CYAN,
                    "player",
                    radius=7,
                    life=1.8,
                    pierce=0,
                    skill_id="arcane_bolt",
                )
            )

        elif self.class_id == "arqueiro":
            self.resource -= basic_resource_cost
            if random.random() < 0.06:
                game.speak_player("RAJADA!", priority=1, cooldown_key="basic_archer_voice", duration=1.0)
            game.audio.play("arrow_shot")
            self.attack_cooldown = 0.205
            game.telemetry.ataques_ranged += 1

            bleed = 0.0
            if "flecha_serrilhada" in self.state.owned_skills:
                bleed = 5.0

            effects = active_unique_effects(self.state)
            game.projectiles.append(
                Projectile(
                    self.pos + aim * 28,
                    aim * 690,
                    damage * 0.88,
                    YELLOW,
                    "player",
                    radius=5,
                    life=1.55,
                    pierce=1 if "horizon_pierce" in effects else 0,
                    skill_id="arrow",
                    bleed_damage=bleed,
                )
            )

        elif self.class_id == "ceifador":
            game.audio.play("sword_swing")
            self.attack_cooldown = 0.29
            game.telemetry.ataques_melee += 1
            game.slashes.append(SlashVisual(self.pos.copy(), aim, PINK, 108, 0.15, 12))
            hit = 0
            for enemy in game.enemies:
                delta = enemy.pos - self.pos
                if delta.length() <= 112 + enemy.radius and normalize(delta).dot(aim) > 0.05:
                    enemy.take_damage(damage * 0.96, game, knockback=aim * 18)
                    hit += 1
            if hit:
                self.heal(damage * 0.025 * hit)

        elif self.class_id == "rasgado":
            game.audio.play("magic_cast")
            self.resource = min(100.0, self.resource + 6.0)
            self.attack_cooldown = 0.28
            game.telemetry.ataques_magicos += 1
            game.projectiles.append(
                Projectile(
                    self.pos + aim * 28,
                    aim * 585,
                    damage * 0.90,
                    RED,
                    "player",
                    radius=8,
                    life=1.7,
                    skill_id="rasgado_basic",
                )
            )

        elif self.class_id == "cronista":
            game.audio.play("magic_cast")
            self.attack_cooldown = 0.235
            game.telemetry.ataques_magicos += 1
            game.projectiles.append(
                Projectile(
                    self.pos + aim * 28,
                    aim * 610,
                    damage * 0.78,
                    CYAN,
                    "player",
                    radius=6,
                    life=1.55,
                    skill_id="temporal_shard",
                )
            )

        elif self.class_id == "artifice":
            game.audio.play("arrow_shot")
            self.attack_cooldown = 0.22
            game.telemetry.ataques_ranged += 1
            game.projectiles.append(
                Projectile(
                    self.pos + aim * 28,
                    aim * 650,
                    damage * 0.80,
                    ORANGE,
                    "player",
                    radius=6,
                    life=1.55,
                    skill_id="artifice_bolt",
                )
            )

        elif self.class_id == "duelista":
            game.audio.play("sword_swing")
            self.attack_cooldown = max(
                DUELIST_BASIC_COOLDOWN_FLOOR,
                DUELIST_BASIC_COOLDOWN_BASE
                - min(
                    DUELIST_BASIC_COOLDOWN_BASE - DUELIST_BASIC_COOLDOWN_FLOOR,
                    self.duelist_combo * DUELIST_BASIC_COOLDOWN_REDUCTION_PER_COMBO,
                ),
            )
            self.duelist_combo = min(DUELIST_COMBO_MAX, self.duelist_combo + 1)
            game.telemetry.ataques_melee += 1
            game.slashes.append(SlashVisual(self.pos.copy(), aim, WHITE, 92, 0.11, 8))
            for enemy in game.enemies:
                delta = enemy.pos - self.pos
                if delta.length() <= 94 + enemy.radius and normalize(delta).dot(aim) > 0.18:
                    enemy.take_damage(
                        damage * (
                            DUELIST_BASIC_DAMAGE_BASE
                            + self.duelist_combo * DUELIST_BASIC_DAMAGE_PER_COMBO
                        ),
                        game,
                        knockback=aim * 10,
                    )

        elif self.class_id == "oraculo":
            game.audio.play("magic_cast")
            self.attack_cooldown = 0.30
            game.telemetry.ataques_magicos += 1
            game.projectiles.append(
                Projectile(
                    self.pos + aim * 28,
                    aim * 540,
                    damage * 0.88,
                    PURPLE,
                    "player",
                    radius=7,
                    life=1.9,
                    skill_id="oracle_mark_bolt",
                )
            )

        elif self.class_id == "guardiao_veu":
            game.audio.play("sword_swing")
            self.attack_cooldown = 0.38
            game.telemetry.ataques_melee += 1
            game.slashes.append(SlashVisual(self.pos.copy(), aim, BLUE, 100, 0.15, 13))
            for enemy in game.enemies:
                delta = enemy.pos - self.pos
                if delta.length() <= 105 + enemy.radius and normalize(delta).dot(aim) > 0.10:
                    enemy.take_damage(damage * 0.92, game, knockback=aim * 34)

        else:
            self.attack_cooldown = 0.30
        if self.class_id in {"guerreiro","ceifador","duelista","guardiao_veu"}:
            game.damage_room_objects_arc(self.pos,aim,118.0,damage)

    def use_secondary(self, game: "Game"):
        # Especial base rápido por classe inicial.
        if self.special_cooldown > 0 or self.pending_cast is not None:
            return

        aim = normalize(self.aim_dir)
        damage = self.combat_damage()
        self.consume_next_attack_buffs()

        if self.class_id == "guerreiro":
            self.special_cooldown = 4.2
            game.speak_player("ABRAM CAMINHO!", priority=2, cooldown_key="warrior_spin")
            for enemy in game.enemies:
                if enemy.pos.distance_to(self.pos) < 160:
                    enemy.take_damage(
                        damage * 1.8,
                        game,
                        normalize(enemy.pos - self.pos) * 48,
                        critical=True,
                    )
            game.add_shake(13, 0.19)

        elif self.class_id == "mago":
            if self.resource < 32:
                return
            self.resource -= 32
            self.special_cooldown = 4.8
            game.speak_player("EXPLODA!", priority=2, cooldown_key="mage_burst")
            game.area_effects.append(
                AreaEffect(
                    "magic_burst",
                    pygame.Vector2(self.aim_target),
                    120,
                    duration=0.05,
                    owner="player",
                    damage=damage * 1.9,
                    delay=0.3,
                    color=CYAN,
                )
            )
            # magic_burst usa gatilho manual no update do jogo.

        elif self.class_id == "arqueiro":
            if self.resource < 25:
                return
            self.resource -= 25
            self.special_cooldown = 3.6
            game.speak_player("RAJADA!", priority=2, cooldown_key="archer_burst")
            for angle in (-8, -4, 0, 4, 8):
                d = aim.rotate(angle)
                game.projectiles.append(
                    Projectile(
                        self.pos + d * 28,
                        d * 760,
                        damage * 1.05,
                        YELLOW,
                        "player",
                        radius=5,
                        life=1.5,
                        pierce=1,
                        skill_id="burst_arrow",
                    )
                )

        elif self.class_id == "ceifador":
            if self.resource < 28:
                return
            self.resource -= 28
            self.special_cooldown = 4.4
            game.speak_player("COLHEITA!", priority=2, cooldown_key="reaper_secondary")
            for enemy in game.enemies:
                if enemy.pos.distance_to(self.pos) < 150:
                    dealt = damage * (1.35 if enemy.hp / max(1, enemy.max_hp) > 0.25 else 2.1)
                    enemy.take_damage(dealt, game, knockback=normalize(enemy.pos - self.pos) * 30)
                    self.heal(dealt * 0.025)

        elif self.class_id == "rasgado":
            if self.resource < 32:
                return
            power = 1.0 + self.resource / 100.0
            self.resource = max(0.0, self.resource - 32.0)
            self.special_cooldown = 4.7
            self.hp = max(1.0, self.hp - self.max_hp * 0.05)
            game.speak_player("RASGUE!", priority=3, cooldown_key="torn_secondary")
            for enemy in game.enemies:
                if enemy.pos.distance_to(self.pos) < 185:
                    enemy.take_damage(damage * 1.15 * power, game, critical=power > 1.65)
            game.add_shake(12, 0.18)

        elif self.class_id == "cronista":
            if self.resource < 26:
                return
            self.resource -= 26
            self.special_cooldown = 5.2
            if self.position_history:
                self.pos = self.position_history[0].copy()
            self.invuln = max(self.invuln, 0.28)
            game.speak_player("VOLTE.", priority=2, cooldown_key="chronist_secondary")

        elif self.class_id == "artifice":
            if self.resource < 30:
                return
            self.resource -= 30
            self.special_cooldown = 4.2
            game.speak_player("SISTEMA: SOBRECARGA!", priority=2, cooldown_key="artifice_secondary")
            for angle in (-18, -9, 0, 9, 18):
                d = aim.rotate(angle)
                game.projectiles.append(Projectile(self.pos + d * 25, d * 700, damage * 0.72, ORANGE, "player", radius=5, life=1.3, skill_id="artifice_burst"))

        elif self.class_id == "duelista":
            self.special_cooldown = 3.0
            self.invuln = max(self.invuln, 0.16)
            self.pos += aim * 95
            game.speak_player("MINHA VEZ.", priority=2, cooldown_key="duelist_secondary")
            for enemy in game.enemies:
                if enemy.pos.distance_to(self.pos) < 92:
                    enemy.take_damage(damage * 1.45, game, knockback=aim * 22, critical=self.duelist_combo >= 6)

        elif self.class_id == "oraculo":
            if self.resource < 30:
                return
            self.resource -= 30
            self.special_cooldown = 5.0
            game.speak_player("EU JÁ VI ESTE MOMENTO.", priority=2, cooldown_key="oracle_secondary")
            game.area_effects.append(AreaEffect("magic_burst", pygame.Vector2(self.aim_target), 135, 0.05, "player", damage=damage * 1.65, delay=0.55, color=PURPLE))

        elif self.class_id == "guardiao_veu":
            if self.resource < 24:
                return
            self.resource -= 24
            self.special_cooldown = 4.8
            self.shield_timer = max(self.shield_timer, 3.2)
            self.parry_window = max(self.parry_window, 0.32)
            game.speak_player("ATRÁS DE MIM!", priority=2, cooldown_key="guardian_secondary")

    def use_skill_slot(self, index: int, game: "Game"):
        if index < 0 or index >= len(self.active_slots):
            return
        self.use_skill(self.active_slots[index], game)

    def use_skill(self, skill_id: str, game: "Game"):
        if skill_id not in self.state.owned_skills:
            return
        if self.pending_cast is not None:
            return

        skill = SKILL_REGISTRY[skill_id]
        mastery = self.state.masteries.get(skill_id)
        if mastery is None:
            return

        # Cooldowns por habilidade ficam no Game.
        if game.skill_cooldowns.get(skill_id, 0.0) > 0:
            return

        mods = self.gear_modifiers()
        cost = mastery.effective_resource_cost() * mods.resource_cost_mult
        if self.class_id == "rasgado":
            legacy = legacy_modifiers(self.state)
            cost *= mods.corruption_gain_mult * max(0.50, 1.0 - legacy.get("corruption_resist", 0.0))

        if self.class_id in ("guerreiro", "rasgado"):
            # Fadiga/Corrupção crescem em vez de consumir pool tradicional.
            if self.resource + cost > 100:
                return
            self.resource += cost
        else:
            if self.resource < cost:
                return
            self.resource -= cost

        target = pygame.Vector2(self.aim_target)
        aim = normalize(self.aim_dir)
        base_cast = self.base_cast_time(skill_id)
        cast_time = mastery.effective_cast_time(base_cast)

        incantation = mastery.choose_incantation(self.state.identity.genero)
        if incantation and cast_time > 0.05:
            game.speak_player(
                incantation,
                priority=2,
                cooldown_key=f"incant_{skill_id}",
                duration=max(1.0, cast_time + 0.25),
            )

        if cast_time > 0.05:
            self.pending_cast = PendingCast(skill_id, cast_time, target, aim, bool(incantation))
        else:
            self.execute_skill(skill_id, target, aim, game)

    def base_cast_time(self, skill_id: str) -> float:
        return {
            "bola_de_fogo": 0.75,
            "prisao_glacial": 0.42,
            "singularidade_arcana": 0.85,
            "fim_da_linha_temporal": 1.0,
            "corte_crescente": 0.0,
            "muralha_de_aco": 0.0,
            "ruptura_da_lamina": 0.22,
            "chuva_de_flechas": 0.35,
            "horizonte_partido": 0.75,
        }.get(skill_id, 0.10)

    def update_pending_cast(self, dt: float, game: "Game"):
        if self.pending_cast is None:
            return

        self.pending_cast.timer -= dt
        if self.pending_cast.timer <= 0:
            cast = self.pending_cast
            self.pending_cast = None
            self.execute_skill(cast.skill_id, cast.target, cast.aim, game)

    def execute_skill(
        self,
        skill_id: str,
        target: pygame.Vector2,
        aim: pygame.Vector2,
        game: "Game",
    ):
        game.play_skill_audio(skill_id)
        skill = SKILL_REGISTRY[skill_id]
        mastery = self.state.masteries[skill_id]
        damage = mastery.effective_damage()
        had_attack_buff = any(self.active_buffs.get(x, 0) > 0 for x in ("hunter_step", "future_echo", "war_oath"))
        mods = self.gear_modifiers()
        damage = (damage + mods.damage_flat) * mods.damage_mult
        if damage > 0 and had_attack_buff:
            # Replica/precisão/retaliação afetam exatamente o próximo ataque ofensivo.
            buff_mult = self.combat_damage() / max(0.001, (self.state.damage_base() + mods.damage_flat) * mods.damage_mult)
            damage *= max(1.0, min(1.8, buff_mult))
            self.consume_next_attack_buffs()
        cooldown = mastery.effective_cooldown() * mods.cooldown_mult * float(synergy_modifiers(self.state)["cooldown_mult"])
        if "skill_cooldown_150" in active_trial_modifiers_for_state(self.state):
            cooldown *= 1.50
        game.skill_cooldowns[skill_id] = cooldown

        leveled = self.state.register_skill_use(skill_id, mastery_xp=6)
        if leveled:
            stage = self.state.masteries[skill_id].stage()
            game.campaign.record(
                CampaignEvent.SKILL_MASTERY,
                target_id=skill_id,
                value=stage.nivel,
            )
            game.process_campaign_rewards()
            game.queue_banner(
                f"MAESTRIA {stage.nivel} — {stage.nome}",
                f"{skill.nome} foi aperfeiçoada.",
                RARITY_COLORS[skill.raridade],
            )
            game.trigger_experience("first_mastery_up")
            if stage.nivel >= 5:
                add_metric(self.state, "mastery_5", 1)
            # Evoluções são marcos de build; quando requisitos completos convergem,
            # o primeiro caminho válido evolui automaticamente nesta build sem UI extra.
            matching = [e for e in available_evolutions(self.state) if e.base_skill_id == skill_id]
            if matching:
                ok, evo_name = evolve_skill(self.state, matching[0].id)
                if ok:
                    add_metric(self.state, "skill_evolutions", 1)
                    game.queue_banner("HABILIDADE EVOLUÍDA", evo_name, PURPLE)
            game.autosave.request(AutosaveReason.DESBLOQUEIO_IMPORTANTE)

        line = mastery.choose_voice_line(self.state.identity.genero)
        if line:
            game.speak_player(line, priority=3, cooldown_key=f"skill_voice_{skill_id}")

        if skill_id == "corte_crescente":
            game.telemetry.ataques_melee += 1
            game.slashes.append(SlashVisual(self.pos.copy(), aim, WHITE, 120, 0.17, 14))
            for enemy in game.enemies:
                delta = enemy.pos - self.pos
                if delta.length() <= 125 + enemy.radius and normalize(delta).dot(aim) > 0.10:
                    enemy.take_damage(damage, game, aim * 36)

        elif skill_id == "muralha_de_aco":
            self.shield_timer = 2.6
            self.parry_window = max(self.parry_window, 0.42)
            game.add_shake(3, 0.08)

        elif skill_id == "ruptura_da_lamina":
            game.telemetry.ataques_melee += 1
            game.slashes.append(SlashVisual(self.pos.copy(), aim, PURPLE, 150, 0.20, 16))
            game.projectiles.append(
                Projectile(
                    self.pos + aim * 35,
                    aim * 610,
                    damage,
                    PURPLE,
                    "player",
                    radius=11,
                    life=1.2,
                    pierce=3,
                    skill_id=skill_id,
                )
            )

        elif skill_id == "bola_de_fogo":
            game.telemetry.ataques_magicos += 1
            game.projectiles.append(
                Projectile(
                    self.pos + aim * 32,
                    aim * 535,
                    damage,
                    ORANGE,
                    "player",
                    radius=11,
                    life=2.0,
                    explosion_radius=95,
                    skill_id=skill_id,
                )
            )

        elif skill_id == "prisao_glacial":
            game.telemetry.ataques_magicos += 1
            game.area_effects.append(
                AreaEffect(
                    "ice_prison",
                    target,
                    125,
                    duration=2.2,
                    owner="player",
                    damage=damage * 0.18,
                    tick_interval=0.42,
                    color=CYAN,
                )
            )

        elif skill_id == "singularidade_arcana":
            game.telemetry.ataques_magicos += 1
            game.area_effects.append(
                AreaEffect(
                    "singularity",
                    target,
                    145,
                    duration=2.35,
                    owner="player",
                    damage=damage * 0.12,
                    tick_interval=0.25,
                    color=PURPLE,
                )
            )

        elif skill_id == "fim_da_linha_temporal":
            game.telemetry.ataques_magicos += 1
            for enemy in game.enemies:
                if enemy.pos.distance_to(self.pos) < 390:
                    enemy.slow_timer = max(enemy.slow_timer, 2.5)
                    enemy.take_damage(damage * 0.65, game, critical=True)
            game.add_shake(18, 0.28)
            game.hit_stop = max(game.hit_stop, 0.11)

        elif skill_id == "chuva_de_flechas":
            game.telemetry.ataques_ranged += 1
            game.area_effects.append(
                AreaEffect(
                    "arrow_rain",
                    target,
                    135,
                    duration=2.0,
                    owner="player",
                    damage=damage * 0.20,
                    tick_interval=0.22,
                    delay=0.55,
                    color=YELLOW,
                )
            )

        elif skill_id == "horizonte_partido":
            game.telemetry.ataques_ranged += 1
            game.add_shake(14, 0.20)
            game.projectiles.append(
                Projectile(
                    self.pos + aim * 32,
                    aim * 1050,
                    damage,
                    CYAN,
                    "player",
                    radius=12,
                    life=1.05,
                    pierce=999,
                    skill_id=skill_id,
                )
            )

        elif skill_id == "colheita_sombria":
            hit = 0
            game.slashes.append(SlashVisual(self.pos.copy(), aim, PINK, 132, 0.18, 15))
            for enemy in game.enemies:
                delta = enemy.pos - self.pos
                if delta.length() <= 135 + enemy.radius and normalize(delta).dot(aim) > -0.05:
                    enemy.take_damage(damage, game, knockback=aim * 22)
                    hit += 1
            if hit:
                self.heal(damage * 0.07 * hit)

        elif skill_id == "sentenca_final":
            candidates = [e for e in game.enemies if not e.dead]
            if candidates:
                target_enemy = min(candidates, key=lambda e: e.pos.distance_squared_to(target))
                ratio = target_enemy.hp / max(1, target_enemy.max_hp)
                target_enemy.take_damage(damage * (2.4 if ratio <= 0.22 and target_enemy.kind != "guardian" else 1.0), game, critical=ratio <= 0.22)

        elif skill_id == "fenda_interior":
            self.hp = max(1.0, self.hp - self.max_hp * 0.10)
            self.resource = min(100.0, self.resource + 38.0)
            game.add_shake(9, 0.14)

        elif skill_id == "passo_rebobinado":
            if self.position_history:
                self.pos = self.position_history[0].copy()
            self.invuln = max(self.invuln, 0.30)

        elif skill_id == "instante_imovel":
            game.area_effects.append(AreaEffect("ice_prison", target, 150, 2.4, "player", damage=damage * 0.12, tick_interval=0.32, color=CYAN))

        elif skill_id == "torreta_do_veu":
            # Primeira versão funcional: um núcleo estacionário dispara uma rajada radial.
            for angle in range(0, 360, 30):
                d = pygame.Vector2(1, 0).rotate(angle)
                game.projectiles.append(Projectile(target.copy(), d * 430, damage * 0.50, ORANGE, "player", radius=5, life=1.5, skill_id=skill_id))

        elif skill_id == "mina_de_fenda":
            game.area_effects.append(AreaEffect("magic_burst", target, 125, 0.05, "player", damage=damage, delay=0.80, color=PURPLE))

        elif skill_id == "contra_golpe":
            self.parry_window = max(self.parry_window, 0.48)
            self.invuln = max(self.invuln, 0.12)

        elif skill_id == "danca_de_laminas":
            game.add_shake(10, 0.15)
            for enemy in game.enemies:
                if enemy.pos.distance_to(self.pos) < 170:
                    enemy.take_damage(damage * 0.72, game, critical=True)
                    enemy.take_damage(damage * 0.38, game, critical=False)

        elif skill_id == "marca_do_pressagio":
            game.projectiles.append(Projectile(self.pos + aim * 28, aim * 560, damage, PURPLE, "player", radius=8, life=1.8, skill_id=skill_id))

        elif skill_id == "destino_condenado":
            for enemy in game.enemies:
                if enemy.pos.distance_to(self.pos) < 330:
                    enemy.take_damage(damage * 0.82, game, critical=True)

        elif skill_id == "barreira_do_veu":
            self.shield_timer = max(self.shield_timer, 4.0)
            self.parry_window = max(self.parry_window, 0.36)

        else:
            # Executor genérico 0.8. Novas skills declaram comportamento em
            # class_kit_systems.py; isso evita um bloco gigante por habilidade.
            behavior = behavior_for(skill_id)
            if behavior is None:
                game.projectiles.append(
                    Projectile(self.pos + aim * 28, aim * 550, damage, self.color,
                               "player", radius=8, life=1.7, skill_id=skill_id)
                )
            elif behavior.mode == "projectile":
                speed = behavior.projectile_speed or 600
                game.projectiles.append(
                    Projectile(
                        self.pos + aim * 28, aim * speed, damage * behavior.power_scale,
                        self.color, "player", radius=8, life=max(1.2, behavior.range / max(1.0, speed)),
                        pierce=behavior.pierce, skill_id=skill_id,
                        explosion_radius=behavior.radius if behavior.radius > 0 else 0,
                    )
                )
            elif behavior.mode == "multi_projectile":
                count = max(1, behavior.projectile_count)
                spread = behavior.spread
                for i in range(count):
                    t = 0.0 if count == 1 else i / (count - 1)
                    angle = -spread / 2 + spread * t
                    d = aim.rotate(angle)
                    game.projectiles.append(
                        Projectile(
                            self.pos + d * 28, d * (behavior.projectile_speed or 560),
                            damage * behavior.power_scale, self.color, "player", radius=7,
                            life=1.8, pierce=behavior.pierce, skill_id=skill_id,
                        )
                    )
            elif behavior.mode in ("aoe_self", "cone"):
                radius = behavior.radius or 150
                for enemy in game.enemies:
                    delta = enemy.pos - self.pos
                    if delta.length() > radius + enemy.radius:
                        continue
                    if behavior.mode == "cone" and normalize(delta).dot(aim) < 0.05:
                        continue
                    enemy.take_damage(
                        damage * behavior.power_scale, game,
                        knockback=normalize(delta) * behavior.knockback if behavior.knockback else None,
                    )
                game.add_shake(8 if behavior.radius < 220 else 12, 0.14)
            elif behavior.mode in ("area", "delayed_aoe"):
                kind = "magic_burst" if behavior.mode == "delayed_aoe" else "ice_prison"
                game.area_effects.append(
                    AreaEffect(
                        kind, target, behavior.radius or 135,
                        max(0.12, behavior.duration if behavior.mode == "area" else 0.08),
                        "player", damage=damage * max(0.1, behavior.power_scale),
                        tick_interval=0.35,
                        delay=behavior.duration if behavior.mode == "delayed_aoe" else 0.0,
                        color=self.color,
                        status=behavior.status,
                    )
                )
            elif behavior.mode == "dash_strike":
                self.pos += aim * (behavior.dash_distance or 120)
                self.invuln = max(self.invuln, 0.16)
                for enemy in game.enemies:
                    delta = enemy.pos - self.pos
                    if delta.length() <= (behavior.radius or 80) + enemy.radius:
                        enemy.take_damage(
                            damage * behavior.power_scale, game,
                            knockback=normalize(delta) * behavior.knockback if behavior.knockback else None,
                        )
            elif behavior.mode == "dash_buff":
                near_threat = any(e.pos.distance_to(self.pos) < 125 for e in game.enemies if not e.dead)
                self.pos += aim * (behavior.dash_distance or 110)
                inv = 0.22
                if behavior.status == "shadowless_step" and near_threat:
                    inv = 0.42
                self.invuln = max(self.invuln, inv)
                if behavior.status:
                    self.active_buffs[behavior.status] = max(self.active_buffs.get(behavior.status, 0), behavior.duration or 2.0)
            elif behavior.mode == "self_buff":
                if behavior.status:
                    self.active_buffs[behavior.status] = max(self.active_buffs.get(behavior.status, 0), behavior.duration or 3.0)
                if behavior.status == "soul_feast":
                    self.heal(self.max_hp * 0.18)
                    self.resource = min(self.resource_max, self.resource + 25)
                elif behavior.status in ("protective_chain", "final_citadel"):
                    self.shield_timer = max(self.shield_timer, behavior.duration or 3.0)
                elif behavior.status == "imminent_vision":
                    self.parry_window = max(self.parry_window, 0.55)
                    self.invuln = max(self.invuln, 0.18)
            elif behavior.mode == "passive":
                # Passivas não deveriam ser acionadas manualmente, mas não causam erro
                # caso um save antigo as coloque em slot.
                return
            else:
                game.projectiles.append(
                    Projectile(self.pos + aim * 28, aim * 550, damage, self.color,
                               "player", radius=8, life=1.7, skill_id=skill_id)
                )

    def update(self, dt: float, game: "Game"):
        self.prev_pos = self.pos.copy()

        self.attack_cooldown = max(0.0, self.attack_cooldown - dt)
        self.special_cooldown = max(0.0, self.special_cooldown - dt)
        self.dash_cooldown = max(0.0, self.dash_cooldown - dt)
        self.invuln = max(0.0, self.invuln - dt)
        self.parry_window = max(0.0, self.parry_window - dt)
        self.parry_cooldown = max(0.0, self.parry_cooldown - dt)
        self.shield_timer = max(0.0, self.shield_timer - dt)
        self.low_hp_pulse_cd = max(0.0, self.low_hp_pulse_cd - dt)
        self.dash_visual_timer = max(0.0, self.dash_visual_timer - dt)
        self.hurt_visual_timer = max(0.0, self.hurt_visual_timer - dt)
        self.attack_visual_timer = max(0.0, self.attack_visual_timer - dt)
        self.footstep_timer = max(0.0, self.footstep_timer - dt)
        self.attack_buffer_timer=max(0.0,self.attack_buffer_timer-dt)
        self.special_buffer_timer=max(0.0,self.special_buffer_timer-dt)
        self.dash_buffer_timer=max(0.0,self.dash_buffer_timer-dt)
        self.visual_time += dt

        for buff_id in list(self.active_buffs):
            self.active_buffs[buff_id] = max(0.0, self.active_buffs[buff_id] - dt)
            if self.active_buffs[buff_id] <= 0:
                del self.active_buffs[buff_id]

        # Apoteose Rasgada troca vida por poder enquanto permanece ativa.
        if self.active_buffs.get("torn_apotheosis", 0) > 0:
            self.hp = max(1.0, self.hp - self.max_hp * 0.006 * dt)
        if self.active_buffs.get("final_citadel", 0) > 0:
            self.shield_timer = max(self.shield_timer, 0.15)

        self.position_history_timer -= dt
        if self.position_history_timer <= 0:
            self.position_history_timer = 0.08
            self.position_history.append(self.pos.copy())
            if len(self.position_history) > 20:
                self.position_history.pop(0)

        self.update_pending_cast(dt, game)
        if self.pending_cast is None:
            if self.attack_buffer_timer>0 and self.attack_cooldown<=0: self.attack_buffer_timer=0; self.basic_attack(game)
            if self.special_buffer_timer>0 and self.special_cooldown<=0: self.special_buffer_timer=0; self.use_secondary(game)
            if self.dash_buffer_timer>0 and self.dash_cooldown<=0: self.dash_buffer_timer=0; self.dash(game)

        keys = pygame.key.get_pressed()

        # Mira: mouse por padrão; setas têm prioridade quando pressionadas.
        keyboard_aim = pygame.Vector2(
            keys[pygame.K_RIGHT] - keys[pygame.K_LEFT],
            keys[pygame.K_DOWN] - keys[pygame.K_UP],
        )
        if keyboard_aim.length_squared() > 0:
            self.aim_dir = keyboard_aim.normalize()
            self.aim_target = self.pos + self.aim_dir * 320
        else:
            # Converte mouse de coordenada de tela para coordenada do mundo,
            # compensando o screen shake usado no desenho.
            mouse_target = pygame.Vector2(pygame.mouse.get_pos()) - game.current_camera_offset
            mouse_delta = mouse_target - self.pos
            if mouse_delta.length_squared() > 36:
                self.aim_dir = mouse_delta.normalize()
                self.aim_target = mouse_target

        movement = pygame.Vector2(
            keys[binding_key("move_right", pygame.K_d)] - keys[binding_key("move_left", pygame.K_a)],
            keys[binding_key("move_down", pygame.K_s)] - keys[binding_key("move_up", pygame.K_w)],
        )

        # Durante encantamentos maiores o Mago se move mais devagar, mas sem travar a resposta do controle.
        cast_move_mult = 0.52 if self.pending_cast else 1.0

        target_velocity = pygame.Vector2()
        if movement.length_squared() > 0:
            movement = movement.normalize()
            target_velocity = movement * self.movement_speed() * cast_move_mult
            game.telemetry.tempo_movendo += dt
            if self.footstep_timer <= 0 and self.dash_visual_timer <= 0:
                game.audio.play("footstep")
                self.footstep_timer = 0.31

        # Aceleração curta, frenagem suave e leve preservação de momento deixam o
        # movimento mais esportivo e controlável sem perder precisão em combate.
        response = 18.5 if target_velocity.length_squared() > 0 else 25.0
        self.velocity = self.velocity.lerp(target_velocity, min(1.0, response * dt))
        if movement.length_squared() == 0 and self.velocity.length_squared() < 18.0:
            self.velocity *= max(0.0, 1.0 - 10.0 * dt)
        self.pos += self.velocity * dt
        if self.velocity.length_squared() > 4:
            self.visual_move = self.velocity.normalize()
        else:
            self.visual_move *= max(0.0, 1.0 - 12.0 * dt)

        if self.hurt_visual_timer > 0:
            self.visual_state = "hurt"
        elif self.dash_visual_timer > 0:
            self.visual_state = "run"
        elif self.parry_window > 0 or self.shield_timer > 0:
            self.visual_state = "parry"
        elif self.attack_visual_timer > 0:
            self.visual_state = "attack"
        elif self.velocity.length_squared() > 64:
            self.visual_state = "walk"
        else:
            self.visual_state = "idle"

        mods = self.gear_modifiers()
        if self.class_id == "guerreiro":
            self.resource = max(0.0, self.resource - 18.0 * mods.resource_regen_mult * dt)
        elif self.class_id == "rasgado":
            self.resource = max(0.0, self.resource - 4.2 * dt)
        else:
            regen_rate = (
                MAGE_RESOURCE_REGEN_PER_SECOND
                if self.class_id == "mago"
                else BASE_RESOURCE_REGEN_PER_SECOND
            )
            self.resource = min(
                self.resource_max,
                self.resource + regen_rate * mods.resource_regen_mult * dt,
            )

        self.pos.x = clamp(self.pos.x, ROOM.left + self.radius, ROOM.right - self.radius)
        self.pos.y = clamp(self.pos.y, ROOM.top + self.radius, ROOM.bottom - self.radius)

        if self.hp / max(1, self.max_hp) <= 0.25:
            game.telemetry.tempo_baixa_vida += dt

    def draw(self, surface, offset):
        p = self.pos + offset
        blink = self.invuln > 0 and int(self.invuln * 22) % 2 == 0
        color = WHITE if blink else self.color

        # A hitbox continua circular e invisivel. A sombra ancora os pes no chao.
        pygame.draw.ellipse(surface, (5, 6, 10), (p.x - 24, p.y + 18, 48, 13))

        aim_world = normalize(self.aim_dir)
        facing_left = aim_world.x < 0

        animated_skin = get_player_animation_frame(
            BASE_DIR,
            self.class_id,
            self.state.identity.genero,
            self.visual_state,
            self.visual_time,
            progress=(
                1.0 - clamp(self.attack_visual_timer / BASIC_ATTACK_VISUAL_DURATION, 0.0, 1.0)
                if self.visual_state == "attack" and self.attack_visual_timer > 0
                else (
                    1.0 - clamp(self.hurt_visual_timer / 0.28, 0.0, 1.0)
                    if self.visual_state == "hurt" and self.hurt_visual_timer > 0
                    else None
                )
            ),
        )
        render_skin = animated_skin or self.skin

        if render_skin is not None:
            state = self.visual_state
            t = self.visual_time
            bob = 0.0
            angle = 0.0
            sx = 1.0
            sy = 1.0
            alpha = 255
            tint = (255, 255, 255)

            if state == "idle":
                bob = math.sin(t * 3.2) * 1.6
                sx = 1.0 + math.sin(t * 3.2) * 0.012
                sy = 1.0 - math.sin(t * 3.2) * 0.008
            elif state == "attack":
                phase = 1.0 - clamp(
                    self.attack_visual_timer / BASIC_ATTACK_VISUAL_DURATION, 0.0, 1.0
                )
                facing = -1 if facing_left else 1
                if phase < 0.22:
                    windup = phase / 0.22
                    angle = -11.0 * windup * facing
                    sx = 1.0 - 0.035 * windup
                    sy = 1.0 + 0.025 * windup
                    bob = 2.0 * windup
                elif phase < 0.68:
                    swing = (phase - 0.22) / 0.46
                    angle = lerp(-11.0, 15.0, swing) * facing
                    impulse = math.sin(swing * math.pi)
                    sx = 1.0 + impulse * 0.095
                    sy = 1.0 - impulse * 0.045
                    bob = -3.0 * impulse
                else:
                    recovery = (phase - 0.68) / 0.32
                    angle = lerp(15.0, 0.0, recovery) * facing
                    sx = lerp(1.04, 1.0, recovery)
                    sy = lerp(0.98, 1.0, recovery)
                    bob = -2.0 * (1.0 - recovery)
            elif state == "parry":
                sy = 0.95
                sx = 1.045
                angle = -3.0 if facing_left else 3.0
                bob = 2.0
            elif state == "hurt":
                phase = clamp(self.hurt_visual_timer / 0.28, 0.0, 1.0)
                angle = (7.0 if facing_left else -7.0) * phase
                tint = (255, 155, 155)
                bob = 2.0

            # Normaliza toda skin para uma altura de gameplay legivel sem mudar a hitbox.
            base_h = 78
            ratio = base_h / max(1, render_skin.get_height())
            w = max(24, int(render_skin.get_width() * ratio * sx))
            h = max(38, int(render_skin.get_height() * ratio * sy))
            frame = cached_sprite_transform(render_skin, w, h, facing_left, angle)

            if blink:
                flash = frame.copy()
                flash.fill((255,255,255,150), special_flags=pygame.BLEND_RGBA_ADD)
                frame = flash
            elif tint != (255,255,255):
                frame = frame.copy()
                frame.fill((*tint,255), special_flags=pygame.BLEND_RGBA_MULT)

            # Dash: duas imagens fantasmas deixam o movimento claramente animado.
            if self.dash_visual_timer > 0 and self.last_dash_direction.length_squared() > 0:
                for dist, a in ((24, 70), (13, 120)):
                    ghost = frame.copy(); ghost.set_alpha(a)
                    gpos = p - self.last_dash_direction * dist
                    grect = ghost.get_rect(midbottom=(int(gpos.x), int(gpos.y + 31 + bob)))
                    surface.blit(ghost, grect)

            rect = frame.get_rect(midbottom=(int(p.x), int(p.y + 31 + bob)))
            surface.blit(frame, rect)
        else:
            # Fallback seguro se um asset faltar: nunca quebra a partida.
            pygame.draw.circle(surface, color, p, self.radius)
            pygame.draw.circle(surface, BLACK, p, self.radius, 3)
            pygame.draw.line(surface, WHITE, p, p + aim_world * 32, 4)

        if self.parry_window > 0:
            pygame.draw.circle(surface, CYAN, p, self.radius + 13, 4)
        if self.shield_timer > 0:
            pygame.draw.circle(surface, BLUE, p, self.radius + 18, 3)

        if self.pending_cast:
            ratio = clamp(self.pending_cast.timer / max(0.001, self.base_cast_time(self.pending_cast.skill_id)), 0, 1)
            progress = 1.0 - ratio
            pulse = 1.0 + 0.08 * math.sin(self.visual_time * 13.0)
            rr = int((self.radius + 27) * pulse)
            pygame.draw.circle(surface, PURPLE, p, rr, 3)
            pygame.draw.arc(
                surface,
                WHITE,
                pygame.Rect(p.x - 34, p.y - 34, 68, 68),
                -math.pi / 2,
                -math.pi / 2 + math.tau * progress,
                5,
            )
            for i in range(6):
                ang = self.visual_time * 2.7 + i * math.tau / 6
                d = pygame.Vector2(math.cos(ang), math.sin(ang))
                q = p + d * (self.radius + 36)
                tip = q + d * 6
                side = d.rotate(90) * 4
                pygame.draw.polygon(surface, CYAN, [tip, q - d*4 + side, q - d*4 - side], 1)
            aim_tip = p + normalize(self.aim_dir) * 56
            pygame.draw.line(surface, CYAN, p + normalize(self.aim_dir) * 24, aim_tip, 3)
            pygame.draw.circle(surface, WHITE, (int(aim_tip.x), int(aim_tip.y)), 3)


# ============================================================
# UI: BOTÕES / CARDS
# ============================================================

class Button:
    def __init__(self, rect: pygame.Rect, text: str, accent=WHITE):
        self.rect = rect
        self.text = text
        self.accent = accent

    def draw(self, surface, mouse):
        hover = self.rect.collidepoint(mouse)
        draw_ui_panel(
            surface,
            self.rect,
            self.accent,
            PANEL_HOVER if hover else PANEL,
            radius=9,
            glow=hover,
        )
        marker_x = self.rect.x + 18
        pygame.draw.circle(surface, self.accent, (marker_x, self.rect.centery), 4 if hover else 3)
        draw_text(surface, self.text, (self.rect.centerx + 8, self.rect.centery), self.accent if hover else WHITE, FONT, True)

    def clicked(self, event) -> bool:
        return (
            event.type == pygame.MOUSEBUTTONDOWN
            and event.button == 1
            and self.rect.collidepoint(event.pos)
        )


# ============================================================
# MENU / CRIAÇÃO
# ============================================================

class Frontend:
    def __init__(self, profile_manager: ProfileManager):
        self.profile_manager = profile_manager
        self.selected_slot = 1
        self.save_manager = SaveManager(str(self.profile_manager.save_path(self.selected_slot)))
        self.mode = "title"
        self.name_input = ""
        self.gender = Gender.MASCULINO
        self.class_id = "guerreiro"
        self.message = ""
        self.scroll = 0
        self.meta_state: Optional[PlayerState] = None
        self.unlocked_classes = {"guerreiro", "mago", "arqueiro"}
        self.delete_confirm = False
        self.refresh_slot_context()

    def refresh_slot_context(self):
        self.save_manager = SaveManager(str(self.profile_manager.save_path(self.selected_slot)))
        self.meta_state = None
        self.unlocked_classes = {"guerreiro", "mago", "arqueiro"}
        if self.profile_manager.save_path(self.selected_slot).exists():
            self.meta_state = self.save_manager.load()
            self.unlocked_classes = set(self.meta_state.progression.classes_desbloqueadas)

    def select_slot(self, slot: int):
        if 1 <= slot <= self.profile_manager.slots:
            self.selected_slot = slot
            self.refresh_slot_context()
            self.message = ""
            self.delete_confirm = False

    def slot_rects(self):
        width = 205
        gap = 18
        total = width * self.profile_manager.slots + gap * (self.profile_manager.slots - 1)
        start = WIDTH // 2 - total // 2
        return [pygame.Rect(start + i * (width + gap), 292, width, 76) for i in range(self.profile_manager.slots)]

    def run(self) -> PlayerState:
        AUDIO.set_music("menu", force=True)
        while True:
            dt = clock.tick(RENDER_FPS_CAP) / 1000.0
            AUDIO.router.update(dt)
            mouse = pygame.mouse.get_pos()

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    raise SystemExit

                if self.mode == "title":
                    result = self.handle_title_event(event)
                    if result is not None:
                        return result
                elif self.mode == "create":
                    result = self.handle_create_event(event)
                    if result is not None:
                        return result
                elif self.mode == "settings":
                    self.handle_front_settings_event(event)
                elif self.mode == "credits":
                    self.handle_credits_event(event)

            if self.mode == "title":
                self.draw_title(mouse)
            elif self.mode == "create":
                self.draw_create(mouse)
            elif self.mode == "settings":
                self.draw_front_settings()
            else:
                self.draw_credits()

            pygame.display.flip()

    def handle_title_event(self, event):
        new_btn = Button(pygame.Rect(WIDTH // 2 - 170, 382, 340, 46), "NOVO JOGO", PURPLE)
        continue_btn = Button(pygame.Rect(WIDTH // 2 - 170, 434, 340, 46), "CONTINUAR", CYAN)
        settings_btn = Button(pygame.Rect(WIDTH // 2 - 170, 486, 340, 46), "CONFIGURAÇÕES", WHITE)
        credits_btn = Button(pygame.Rect(WIDTH // 2 - 170, 538, 340, 46), "CRÉDITOS", YELLOW)
        quit_btn = Button(pygame.Rect(WIDTH // 2 - 170, 590, 340, 46), "SAIR", RED)

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for i, rect in enumerate(self.slot_rects(), start=1):
                if rect.collidepoint(event.pos):
                    AUDIO.play("ui_confirm")
                    self.select_slot(i)
                    return None

        if new_btn.clicked(event):
            AUDIO.play("ui_confirm")
            self.mode = "create"
            return None

        if continue_btn.clicked(event):
            if self.profile_manager.save_path(self.selected_slot).exists():
                AUDIO.play("ui_confirm")
                return self.save_manager.load()
            self.message = f"Slot {self.selected_slot} está vazio."

        if settings_btn.clicked(event):
            AUDIO.play("ui_confirm")
            self.mode = "settings"
            return None

        if credits_btn.clicked(event):
            AUDIO.play("ui_confirm")
            self.mode = "credits"
            return None

        if quit_btn.clicked(event):
            AUDIO.play("ui_back")
            pygame.quit()
            raise SystemExit

        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_1, pygame.K_2, pygame.K_3):
                self.select_slot(event.key - pygame.K_1 + 1)
            elif event.key == pygame.K_n:
                self.mode = "create"
            elif event.key == pygame.K_c and self.profile_manager.save_path(self.selected_slot).exists():
                return self.save_manager.load()
            elif event.key == pygame.K_o:
                self.mode = "settings"
            elif event.key == pygame.K_F1:
                self.mode = "credits"
            elif event.key == pygame.K_DELETE:
                if self.profile_manager.save_path(self.selected_slot).exists():
                    if not self.delete_confirm:
                        self.delete_confirm = True
                        self.message = f"DELETE novamente apaga o Slot {self.selected_slot} (cópia de recuperação será mantida)."
                    else:
                        self.profile_manager.delete(self.selected_slot, keep_recovery=True)
                        self.refresh_slot_context()
                        self.delete_confirm = False
                        self.message = f"Slot {self.selected_slot} apagado. R restaura a última cópia."
                else:
                    self.message = "Esse slot já está vazio. R tenta restaurar uma cópia apagada."
            elif event.key == pygame.K_r and not self.profile_manager.save_path(self.selected_slot).exists():
                if self.profile_manager.restore_deleted(self.selected_slot):
                    self.refresh_slot_context()
                    self.message = f"Slot {self.selected_slot} restaurado."
                    AUDIO.play("ui_confirm")
                else:
                    self.message = "Nenhuma cópia apagada disponível para este slot."

        return None

    def handle_create_event(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                AUDIO.play("ui_back")
                self.mode = "title"
                return None
            if event.key == pygame.K_BACKSPACE:
                self.name_input = self.name_input[:-1]
            elif event.key == pygame.K_RETURN:
                return self.try_create()
            elif event.unicode and event.unicode.isprintable() and len(self.name_input) < 24:
                self.name_input += event.unicode

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            male_rect = pygame.Rect(120, 205, 180, 46)
            female_rect = pygame.Rect(315, 205, 180, 46)
            if male_rect.collidepoint(event.pos):
                AUDIO.play("ui_confirm")
                self.gender = Gender.MASCULINO
            elif female_rect.collidepoint(event.pos):
                AUDIO.play("ui_confirm")
                self.gender = Gender.FEMININO

            cards = self.class_card_rects()
            for class_id, rect in cards:
                if rect.collidepoint(event.pos):
                    if class_id in self.unlocked_classes:
                        AUDIO.play("ui_confirm")
                        self.class_id = class_id
                        self.message = ""
                    else:
                        rule = rule_for_class(class_id)
                        self.message = rule.descricao if rule else f"{CLASS_REGISTRY[class_id].nome} está bloqueada."

            start_rect = pygame.Rect(WIDTH - 305, HEIGHT - 72, 245, 48)
            if start_rect.collidepoint(event.pos):
                AUDIO.play("ui_confirm")
                return self.try_create()

        return None

    def try_create(self):
        name = self.name_input.strip() or "Viajante"
        state = create_new_character(name, self.gender, self.class_id)
        if self.meta_state is not None:
            state = transfer_meta_progression(self.meta_state, state)
        self.profile_manager.save(self.selected_slot, state, playtime_seconds=0)
        self.refresh_slot_context()
        return state

    def draw_title(self, mouse):
        screen.fill(BG)
        self.draw_rift_background()

        veil = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        veil.fill((5, 6, 13, 92))
        screen.blit(veil, (0, 0))

        title_panel = pygame.Rect(WIDTH // 2 - 340, 55, 680, 215)
        draw_ui_panel(screen, title_panel, PURPLE, (16, 17, 30), radius=18, glow=True)
        pygame.draw.circle(screen, (255, 255, 255, 22), (WIDTH // 2, 132), 108, 2)

        draw_text(screen, "RIFTWALKER", (WIDTH // 2, 100), PURPLE, FONT_TITLE, True)
        draw_text(screen, f"RELEASE CANDIDATE {BUILD_VERSION}", (WIDTH // 2, 168), GRAY, FONT, True)
        draw_text(screen, "O Véu se lembra.", (WIDTH // 2, 215), WHITE, FONT_MEDIUM, True)
        draw_text(screen, "Escolha um slot", (WIDTH // 2, 262), GRAY, FONT_SMALL, True)

        for slot, rect in enumerate(self.slot_rects(), start=1):
            selected = slot == self.selected_slot
            meta = self.profile_manager.slot_meta(slot)
            draw_ui_panel(screen, rect, CYAN if selected else DARK_GRAY, PANEL_HOVER if selected else PANEL, radius=9, glow=selected)
            draw_text(screen, f"SLOT {slot}", (rect.centerx, rect.y + 16), CYAN if selected else WHITE, FONT_SMALL, True)
            if meta.exists:
                cls = CLASS_REGISTRY.get(meta.class_id)
                label = f"{meta.character_name} • Nv.{meta.level}"
                draw_text(screen, label, (rect.centerx, rect.y + 40), WHITE, FONT_TINY, True)
                draw_text(screen, cls.nome if cls else meta.class_id, (rect.centerx, rect.y + 58), GRAY, FONT_TINY, True)
            else:
                draw_text(screen, "VAZIO", (rect.centerx, rect.y + 48), DARK_GRAY, FONT_TINY, True)

        buttons = [
            Button(pygame.Rect(WIDTH // 2 - 170, 382, 340, 46), "NOVO JOGO", PURPLE),
            Button(pygame.Rect(WIDTH // 2 - 170, 434, 340, 46), "CONTINUAR", CYAN),
            Button(pygame.Rect(WIDTH // 2 - 170, 486, 340, 46), "CONFIGURAÇÕES", WHITE),
            Button(pygame.Rect(WIDTH // 2 - 170, 538, 340, 46), "CRÉDITOS", YELLOW),
            Button(pygame.Rect(WIDTH // 2 - 170, 590, 340, 46), "SAIR", RED),
        ]
        for btn in buttons:
            btn.draw(screen, mouse)

        if not self.profile_manager.save_path(self.selected_slot).exists():
            draw_text(screen, "Slot selecionado sem save", (WIDTH // 2, 590), DARK_GRAY, FONT_TINY, True)
        if self.message:
            draw_text(screen, self.message, (WIDTH // 2, 615), YELLOW, FONT_SMALL, True)

        draw_text(screen, "1/2/3 slot • N novo • C continuar • DEL apagar • R restaurar • O opções • F1 créditos", (WIDTH // 2, HEIGHT - 38), GRAY, FONT_TINY, True)
        draw_text(screen, "Python + Pygame | lógica 60 Hz independente do FPS", (WIDTH // 2, HEIGHT - 19), DARK_GRAY, FONT_TINY, True)

    def handle_front_settings_event(self, event):
        if event.type != pygame.KEYDOWN:
            return
        if event.key in (pygame.K_ESCAPE, pygame.K_o):
            AUDIO.play("ui_back")
            self.mode = "title"
            return
        changed = False
        if event.key == pygame.K_1:
            GAME_SETTINGS.accessibility.subtitles = not GAME_SETTINGS.accessibility.subtitles; changed = True
        elif event.key == pygame.K_2:
            GAME_SETTINGS.accessibility.reduced_screen_shake = not GAME_SETTINGS.accessibility.reduced_screen_shake; changed = True
        elif event.key == pygame.K_3:
            GAME_SETTINGS.accessibility.reduced_flashes = not GAME_SETTINGS.accessibility.reduced_flashes; changed = True
        elif event.key == pygame.K_4:
            GAME_SETTINGS.accessibility.damage_numbers = not GAME_SETTINGS.accessibility.damage_numbers; changed = True
        elif event.key == pygame.K_5:
            ids = list(DIFFICULTIES.keys())
            cur = ids.index(GAME_SETTINGS.difficulty) if GAME_SETTINGS.difficulty in ids else 0
            GAME_SETTINGS.difficulty = ids[(cur + 1) % len(ids)]; changed = True
        elif event.key == pygame.K_6:
            GAME_SETTINGS.audio.music = max(0, GAME_SETTINGS.audio.music - 10); changed = True
        elif event.key == pygame.K_7:
            GAME_SETTINGS.audio.music = min(100, GAME_SETTINGS.audio.music + 10); changed = True
        elif event.key == pygame.K_8:
            GAME_SETTINGS.audio.sfx = max(0, GAME_SETTINGS.audio.sfx - 10); changed = True
        elif event.key == pygame.K_9:
            GAME_SETTINGS.audio.sfx = min(100, GAME_SETTINGS.audio.sfx + 10); changed = True
        if changed:
            SETTINGS_MANAGER.save(GAME_SETTINGS)
            AUDIO.refresh_volumes()
            AUDIO.play("ui_confirm")

    def draw_front_settings(self):
        screen.fill(BG)
        draw_text(screen, "CONFIGURAÇÕES", (WIDTH // 2, 75), WHITE, FONT_BIG, True)
        draw_text(screen, "1–9 altera • O/ESC volta", (WIDTH // 2, 122), GRAY, FONT_SMALL, True)
        a = GAME_SETTINGS.accessibility
        au = GAME_SETTINGS.audio
        diff = DIFFICULTIES[GAME_SETTINGS.difficulty]
        rows = [
            ("1", "Legendas", "ON" if a.subtitles else "OFF"),
            ("2", "Screen shake reduzido", "ON" if a.reduced_screen_shake else "OFF"),
            ("3", "Flashes reduzidos", "ON" if a.reduced_flashes else "OFF"),
            ("4", "Números de dano", "ON" if a.damage_numbers else "OFF"),
            ("5", "Dificuldade", diff.nome),
            ("6", "Música -10", f"{au.music}%"),
            ("7", "Música +10", f"{au.music}%"),
            ("8", "SFX -10", f"{au.sfx}%"),
            ("9", "SFX +10", f"{au.sfx}%"),
        ]
        y=175
        for key,label,value in rows:
            rect=pygame.Rect(WIDTH//2-380,y,760,44)
            draw_ui_panel(screen, rect, CYAN if key == "5" else DARK_GRAY, PANEL, radius=7, glow=False)
            draw_text(screen,f"{key}. {label}",(rect.x+18,rect.y+11),WHITE,FONT_SMALL)
            draw_text(screen,value,(rect.right-160,rect.y+11),CYAN,FONT_SMALL)
            y+=51
        draw_wrapped_text(screen,diff.descricao,pygame.Rect(WIDTH//2-380,y+10,760,70),GRAY,FONT_SMALL)

    def handle_credits_event(self, event):
        if event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_F1):
            AUDIO.play("ui_back")
            self.mode = "title"

    def draw_credits(self):
        screen.fill(BG)
        self.draw_rift_background()
        veil = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        veil.fill((5, 6, 13, 128))
        screen.blit(veil, (0, 0))
        draw_ui_panel(screen, pygame.Rect(90, 42, WIDTH - 180, HEIGHT - 105), PURPLE, (14, 16, 27), radius=16, glow=True)
        draw_text(screen,"RIFTWALKER",(WIDTH//2,72),PURPLE,FONT_TITLE,True)
        draw_text(screen,"CRÉDITOS DA RELEASE CANDIDATE",(WIDTH//2,142),WHITE,FONT_MEDIUM,True)
        lines=[
            "Projeto: RIFTWALKER — Python + Pygame",
            "Sistemas: classes, Rupturas, IA, Memória do Véu, campanha, Refúgio e progressão",
            "Áudio/músicas: geração procedural própria desta RC",
            "Voz provisória: síntese local com eSpeak + pós-processamento FFmpeg",
            "Ícone: geração procedural do projeto",
            "Assets externos incorporados nesta RC: nenhum",
            "",
            "As vozes sintéticas são provisórias e podem ser substituídas por dublagem final.",
            "O arquivo CREDITS.txt mantém as informações de produção/licença.",
        ]
        y=230
        for line in lines:
            draw_text(screen,line,(WIDTH//2,y),GRAY if line else WHITE,FONT_SMALL,True)
            y+=38
        draw_text(screen,"F1 ou ESC para voltar",(WIDTH//2,HEIGHT-60),YELLOW,FONT_SMALL,True)

    def draw_rift_background(self):
        center = pygame.Vector2(WIDTH // 2, HEIGHT // 2)
        ticks = pygame.time.get_ticks() * 0.001
        for i in range(18):
            angle = i / 18 * math.tau + ticks * 0.06
            length = 260 + math.sin(ticks * 1.5 + i) * 28
            start = center + pygame.Vector2(math.cos(angle), math.sin(angle)) * 115
            end = center + pygame.Vector2(math.cos(angle), math.sin(angle)) * length
            pygame.draw.line(screen, (40, 24, 65), start, end, 2)

    def class_card_rects(self):
        ids = list(CLASS_REGISTRY.keys())
        rects = []
        columns = 5
        card_w = 215
        card_h = 140
        gap = 14
        start_x = 70
        start_y = 305
        for i, class_id in enumerate(ids):
            col = i % columns
            row = i // columns
            rect = pygame.Rect(
                start_x + col * (card_w + gap),
                start_y + row * (card_h + gap),
                card_w,
                card_h,
            )
            rects.append((class_id, rect))
        return rects

    def draw_create(self, mouse):
        screen.fill(BG)
        self.draw_rift_background()
        veil = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        veil.fill((7, 8, 15, 118))
        screen.blit(veil, (0, 0))
        draw_text(screen, "CRIAR RIFTWALKER", (55, 38), WHITE, FONT_BIG)
        pygame.draw.line(screen, PURPLE, (55, 92), (WIDTH - 55, 92), 2)

        draw_text(screen, "Nome", (70, 115), GRAY, FONT_SMALL)
        name_rect = pygame.Rect(70, 140, 430, 48)
        draw_ui_panel(screen, name_rect, PURPLE, PANEL, radius=8, glow=True)
        name_display = self.name_input + ("|" if pygame.time.get_ticks() // 450 % 2 == 0 else "")
        draw_text(screen, name_display or "", (84, 152), WHITE, FONT)

        draw_text(screen, "Gênero / voz", (120, 183), GRAY, FONT_TINY)
        male_rect = pygame.Rect(120, 205, 180, 46)
        female_rect = pygame.Rect(315, 205, 180, 46)
        for rect, gender, label in (
            (male_rect, Gender.MASCULINO, "HOMEM"),
            (female_rect, Gender.FEMININO, "MULHER"),
        ):
            selected = self.gender == gender
            draw_ui_panel(screen, rect, CYAN if selected else DARK_GRAY, PANEL_HOVER if selected else PANEL, radius=7, glow=selected)
            draw_text(screen, label, rect.center, WHITE, FONT_SMALL, True)

        # Preview vivo da skin selecionada: usa exatamente class_id + genero.
        selected_skin = get_player_skin(self.class_id, self.gender)
        if selected_skin is not None:
            target_h = 145
            ratio = target_h / max(1, selected_skin.get_height())
            preview = pygame.transform.smoothscale(selected_skin, (max(45, int(selected_skin.get_width()*ratio)), target_h))
            bob = math.sin(pygame.time.get_ticks() * 0.0032) * 2
            prect = preview.get_rect(midbottom=(WIDTH - 180, 272 + int(bob)))
            screen.blit(preview, prect)
            draw_text(screen, CLASS_REGISTRY[self.class_id].nome, (WIDTH - 180, 278), CLASS_COLORS.get(self.class_id, WHITE), FONT_SMALL, True)

        draw_text(screen, "Classes", (70, 272), WHITE, FONT_MEDIUM)
        draw_text(
            screen,
            "As 3 primeiras estão disponíveis. As outras existem no sistema e serão desbloqueadas.",
            (225, 280),
            GRAY,
            FONT_SMALL,
        )

        for class_id, rect in self.class_card_rects():
            definition = CLASS_REGISTRY[class_id]
            unlocked = class_id in self.unlocked_classes
            selected = class_id == self.class_id
            accent = CLASS_COLORS.get(class_id, WHITE)

            hovered = rect.collidepoint(mouse)
            draw_ui_panel(
                screen,
                rect,
                accent if unlocked else DARK_GRAY,
                PANEL_HOVER if (hovered or selected) else PANEL,
                radius=10,
                glow=selected,
            )
            pygame.draw.circle(screen, accent if unlocked else DARK_GRAY, (rect.right - 17, rect.y + 17), 4)

            draw_text(screen, definition.nome, (rect.x + 12, rect.y + 10), accent if unlocked else GRAY, FONT_SMALL)
            draw_text(
                screen,
                f"Dificuldade: {difficulty_label(definition.dificuldade)}",
                (rect.x + 12, rect.y + 34),
                GRAY,
                FONT_TINY,
            )
            draw_wrapped_text(
                screen,
                definition.identidade,
                pygame.Rect(rect.x + 12, rect.y + 56, rect.width - 24, 58),
                WHITE if unlocked else DARK_GRAY,
                FONT_TINY,
            )
            if not unlocked:
                draw_text(screen, "BLOQUEADA", (rect.centerx, rect.bottom - 14), RED, FONT_TINY, True)
            elif class_id not in ("guerreiro", "mago", "arqueiro"):
                draw_text(screen, "DESBLOQUEADA", (rect.centerx, rect.bottom - 14), GREEN, FONT_TINY, True)

        start_rect = pygame.Rect(WIDTH - 305, HEIGHT - 72, 245, 48)
        draw_ui_panel(screen, start_rect, GREEN, PANEL_HOVER if start_rect.collidepoint(mouse) else PANEL, radius=8, glow=start_rect.collidepoint(mouse))
        draw_text(screen, "ENTRAR NO VÉU", start_rect.center, GREEN, FONT_SMALL, True)

        if self.message:
            draw_text(screen, self.message, (70, HEIGHT - 55), YELLOW, FONT_SMALL)


# ============================================================
# JOGO PRINCIPAL
# ============================================================

class Game:
    def __init__(self, player_state: PlayerState, save_path: Path = SAVE_PATH):
        self.player_state = player_state
        self.player = PlayerAvatar(player_state)
        self.audio = AUDIO
        self.difficulty = DIFFICULTIES.get(GAME_SETTINGS.difficulty, DIFFICULTIES["viajante"])
        self.meta_panel: Optional[str] = None
        self.meta_message = ""
        self.hub_room_open = False
        self.hub_menu_from_room = False
        self.hub_dialogue_npc_id: Optional[str] = None
        self.hub_return_position: Optional[pygame.Vector2] = None
        self.hub_npc_positions = {
            "aris": pygame.Vector2(ROOM.left + 142, ROOM.top + 132),
            "maelis": pygame.Vector2(ROOM.right - 142, ROOM.top + 132),
            "nox": pygame.Vector2(ROOM.left + 142, ROOM.bottom - 155),
            "ilyra": pygame.Vector2(ROOM.right - 142, ROOM.bottom - 155),
        }

        # Campanha roda separada da renderização e reage a eventos reais do jogo.
        self.campaign = CampaignDirector(self.player_state)
        self.hub = ensure_hub_state(self.player_state)
        if self.campaign.current_region().id == "refugio_ultima_luz":
            self.campaign.enter_region("campos_primeira_fenda")

        self.encounter_director = EncounterDirector(seed=random.randint(1, 2_000_000_000))
        self.environment = EnvironmentRenderer()
        self.diagnostics = RuntimeDiagnostics()
        self.transition_alpha = 170.0

        self.save_path = Path(save_path)
        self.save_manager = SaveManager(str(self.save_path))
        self.autosave = AutosaveController(self.save_manager, minimum_interval=1.5)

        self.enemies: List[Enemy] = []
        self.enemy_deaths: List[EnemyDeathVisual] = []
        self.projectiles: List[Projectile] = []
        self.particles: List[Particle] = []
        self.floating_texts: List[FloatingText] = []
        self.slashes: List[SlashVisual] = []
        self.impact_rings: List[ImpactRing] = []
        self.area_effects: List[AreaEffect] = []
        self.heals: List[pygame.Vector2] = []
        self.speech: Optional[SpeechBubble] = None
        self.reaction_controller = ReactionController()

        self.wave = 0
        self.rooms_cleared = 0
        self.score = 0
        self.paused = False
        self.pause_selection = 0
        self.abandon_confirm = False
        self.return_to_menu_requested = False
        self.game_over = False
        self.debug = False
        self.inventory_open = False
        self.inventory_message = ""
        self.inventory_page = 0
        self.hit_stop = 0.0
        self.shake_power = 0.0
        self.shake_timer = 0.0
        self.camera_shake_offset=pygame.Vector2(); self.current_camera_offset=pygame.Vector2(); self.shake_direction=pygame.Vector2(); self.logic_updates_last_frame=0
        self.combo_count=0; self.combo_timer=0.0; self.combo_best=0; self.combo_damage=0.0
        self.room_objects: List[RoomInteractive]=[]
        self.boss_cinematic_timer=0.0; self.boss_cinematic_max=0.0; self.boss_cinematic_title=""; self.boss_cinematic_subtitle=""
        self.boss_phase_timer=0.0; self.boss_phase_name=""; self.cinematic_flash=0.0
        self.last_run_result=None; self.last_run_gap_text=""; self.ranking_daily_view=False

        self.skill_cooldowns: Dict[str, float] = {}
        self.telemetry = CombatTelemetry()

        self.offer_mode: Optional[str] = None
        self.skill_offers: List[SkillDefinition] = []
        self.rift_offers: List[str] = []
        self.ending_offers = []
        self.banner_title = ""
        self.banner_subtitle = ""
        self.banner_color = WHITE
        self.banner_timer = 0.0
        self.banner_queue: List[Tuple[str, str, Tuple[int, int, int]]] = []

        self.current_boss_strategy = "variacao_tatica"
        self.boss_spawn_line_shown = False
        self.magic_burst_triggered_ids = set()

        self.pending_level_ups = 0
        self.xp_needed = self.calculate_xp_needed(self.player_state.stats.nivel)

        # 0.10: sistemas antes apenas de backend agora têm estado de runtime/UI.
        ensure_faction_state(self.player_state)
        ensure_challenge_state(self.player_state)
        ensure_legacy_state(self.player_state)
        ensure_endgame_state(self.player_state)
        ensure_tutorial_state(self.player_state)
        self.hub_tab = "main"
        self.hub_vendor_id: Optional[str] = None
        self.hub_page = 0
        self.expedition_contract_index = 0
        self.route_offers: List[str] = []
        self.expedition_map = None
        self.awaiting_expedition = False
        self.boss_rush_active = False
        self.boss_rush_queue: List[str] = []
        self.run_kills = 0
        self.run_bosses = 0
        self.run_superiors = 0
        self.run_elapsed = 0.0
        self.run_submitted = False
        self.run_nonce = f"{random.getrandbits(96):024x}"
        self.run_trial_id=str(ensure_challenge_state(self.player_state).get("active_trial") or "")
        self.daily_challenge=load_active_daily(self.player_state); self.last_daily_challenge=self.daily_challenge
        if self.daily_challenge is not None:
            self.player_state.campaign["current_region"]=self.daily_challenge.region_id; self.player.daily_damage_mult=self.daily_challenge.player_damage_mult; self.run_trial_id=self.daily_challenge.trial_id
        self.ranking_online_view=False
        self.rebind_mode = False
        self.rebind_index = 0
        self.rebind_waiting = False
        self.codex_research_mode = False
        self.vendor_sell_mode = False
        self.expedition_supply_loadout: Dict[str, int] = {}
        self.expedition_protected_reward = False
        self.run_supplies: Dict[str, int] = {}
        self.run_protected_reward = False
        self.rift_guard_timer = 0.0
        self.tonic_timer = 0.0
        self.crimson_oil_timer = 0.0
        self.echo_signal_timer = 0.0

        self.trigger_experience("game_started")
        self.ensure_expedition_map()
        self.spawn_next_room()

    # --------------------------------------------------------
    # PERFORMANCE / GUARDRAILS
    # --------------------------------------------------------

    def enforce_runtime_budgets(self):
        # Limites altos o bastante para não amputar a fantasia visual, mas impedem
        # uma combinação extrema de Rupturas/bosses de crescer sem controle.
        budgets = (
            ("particles", self.particles, 520),
            ("floating_texts", self.floating_texts, 140),
            ("slashes", self.slashes, 140),
            ("projectiles", self.projectiles, 420),
            ("area_effects", self.area_effects, 120),
            ("enemy_deaths", self.enemy_deaths, 80),
        )
        for name, collection, limit in budgets:
            dropped = trim_oldest(collection, limit)
            self.diagnostics.note_drop(name, dropped)

    def diagnostic_counts(self):
        return {
            "enemies": len(self.enemies),
            "projectiles": len(self.projectiles),
            "particles": len(self.particles),
            "areas": len(self.area_effects),
            "texts": len(self.floating_texts),
        }

    # --------------------------------------------------------
    # PROGRESSÃO
    # --------------------------------------------------------

    @staticmethod
    def calculate_xp_needed(level: int) -> int:
        return int(70 * (1.25 ** max(0, level - 1)))

    def process_campaign_rewards(self):
        """Claim completed campaign quests and announce their rewards."""
        claimed = self.campaign.claim_ready_quests()
        for quest_id in claimed:
            quest = QUESTS[quest_id]
            self.queue_banner(
                "MISSÃO CONCLUÍDA",
                f"{quest.nome} — recompensas recebidas",
                GREEN,
            )
            self.autosave.request(AutosaveReason.EVENTO_HISTORIA)

    def award_xp(self, amount: int):
        mult = self.player_state.active_rifts.cumulative_modifiers()["xp_multiplier"]
        gained = max(1, int(amount * mult * self.difficulty.reward_mult))
        self.player_state.stats.xp += gained
        self.score += gained

        while self.player_state.stats.xp >= self.xp_needed:
            self.player_state.stats.xp -= self.xp_needed
            self.player_state.stats.nivel += 1
            self.pending_level_ups += 1
            self.xp_needed = self.calculate_xp_needed(self.player_state.stats.nivel)

    def open_skill_offer(self):
        faction_flags = faction_reward_flags(self.player_state)
        offer_luck = min(0.65, self.player_state.stats.nivel * 0.025 + (0.12 if "corrupted_offer_bonus" in faction_flags else 0.0))
        self.skill_offers = generate_skill_offers(
            class_id=self.player_state.identity.class_id,
            owned_skill_ids=self.player_state.owned_skills,
            amount=3,
            luck=offer_luck,
            allow_corrupted=self.rooms_cleared >= (1 if "corrupted_offer_bonus" in faction_flags else 2),
        )
        if self.skill_offers:
            if any(skill.corrupted for skill in self.skill_offers):
                self.trigger_experience("first_corrupted_offer")
            self.offer_mode = "skill"
        else:
            self.pending_level_ups = max(0, self.pending_level_ups - 1)

    def choose_skill_offer(self, index: int):
        if self.offer_mode != "skill" or index >= len(self.skill_offers):
            return

        skill = self.skill_offers[index]
        learned = self.player_state.learn_skill(skill.id)
        self.player.refresh_skill_slots()

        if learned:
            if skill.raridade in (Rarity.LENDARIA, Rarity.MITICA, Rarity.CORROMPIDA):
                self.audio.play("ui_legendary_drop")
            elif skill.raridade in (Rarity.RARA, Rarity.EPICA):
                self.audio.play("ui_rare_drop")
            else:
                self.audio.play("ui_confirm")
            self.queue_banner(
                skill.raridade.value.upper(),
                f"{skill.nome} aprendida",
                RARITY_COLORS[skill.raridade],
            )
            self.campaign.record(CampaignEvent.SKILL_LEARNED, target_id=skill.id)
            self.trigger_experience("first_skill_learned")
            self.process_campaign_rewards()
        else:
            # Se no futuro permitirmos duplicatas, aqui pode evoluir a habilidade.
            self.player_state.register_skill_use(skill.id, mastery_xp=30)

        self.pending_level_ups = max(0, self.pending_level_ups - 1)
        self.offer_mode = None
        self.autosave.request(AutosaveReason.DESBLOQUEIO_IMPORTANTE)
        self.evaluate_unlocks()

        if self.pending_level_ups > 0:
            self.open_skill_offer()
        elif not self.enemies and not self.game_over:
            if self.boss_rush_active:
                self.spawn_next_room()
            elif self.expedition_map is not None and not self.awaiting_expedition:
                self.open_route_offer()

    def open_rift_offer(self):
        # Uma Ruptura representa uma condição ativa, não uma pilha infinita da
        # mesma condição. Evita duplicatas e encerra a oferta quando as cinco
        # Rupturas disponíveis já estão ativas nesta run.
        active = set(self.player_state.active_rifts.active_ids)
        ids = [rift_id for rift_id in RIFT_REGISTRY if rift_id not in active]
        if not ids:
            self.rift_offers = []
            self.offer_mode = None
            self.complete_current_room()
            return
        self.audio.play("rift_open")
        extra = int(round(legacy_modifiers(self.player_state).get("rift_offer_bonus", 0.0)))
        self.rift_offers = random.sample(ids, min(3 + max(0, extra), len(ids)))
        self.offer_mode = "rift"

    def choose_rift_offer(self, index: int):
        if self.offer_mode != "rift" or index >= len(self.rift_offers):
            return

        rift_id = self.rift_offers[index]
        self.audio.play("rift_accept")
        rift = RIFT_REGISTRY[rift_id]
        self.player_state.active_rifts.add(rift_id)
        self.player_state.progression.rupturas_descobertas.add(rift_id)
        self.campaign.record(CampaignEvent.RIFT_DISCOVERED, target_id=rift_id)
        add_metric(self.player_state, "max_active_rifts", len(self.player_state.active_rifts.active_ids), absolute_max=True)
        self.process_campaign_rewards()
        self.player.refresh_from_state()
        self.trigger_experience("first_rift_seen")

        self.queue_banner(rift.nome.upper(), rift.descricao, PURPLE)
        self.offer_mode = None
        self.autosave.request(AutosaveReason.RUPTURA_CONCLUIDA)
        self.evaluate_unlocks()
        self.complete_current_room()

    def choose_ending_offer(self, index: int):
        if self.offer_mode != "ending" or index < 0 or index >= len(self.ending_offers):
            return
        ending = choose_ending(self.player_state, self.ending_offers[index].id)
        self.offer_mode = None
        self.ending_offers = []
        self.queue_banner(ending.nome.upper(), ending.epilogo, CYAN if ending.tone != "corrupted" else RED)
        self.trigger_experience("postgame_unlocked")
        self.submit_run_result("victory")
        self.autosave.save_now(self.player_state, AutosaveReason.EVENTO_HISTORIA)

    # --------------------------------------------------------
    # 0.10 — EXPERIÊNCIA / RANK / MAPA PROCEDURAL / META
    # --------------------------------------------------------

    def trigger_experience(self, trigger: str):
        unlocked = unlock_codex_by_trigger(self.player_state, trigger)
        step = tutorial_for_trigger(self.player_state, trigger)
        if step is not None:
            self.queue_banner(step.title.upper(), step.text, CYAN)
            mark_tutorial_seen(self.player_state, step.id)
        if unlocked:
            self.autosave.request(AutosaveReason.DESBLOQUEIO_IMPORTANTE)

    def active_trial_modifiers(self) -> set[str]:
        return active_trial_modifiers_for_state(self.player_state)

    def submit_run_result(self, result: str):
        if self.run_submitted or self.rooms_cleared<=0: return None
        endgame=ensure_endgame_state(self.player_state); challenge_state=ensure_challenge_state(self.player_state); active_daily=self.daily_challenge
        trial_id=active_daily.trial_id if active_daily is not None else (self.run_trial_id or str(challenge_state.get("active_trial") or "")); daily_mult=active_daily.score_mult if active_daily is not None else 1.0
        entry=make_run_result(player_name=self.player_state.identity.nome,class_id=self.player_state.identity.class_id,rooms=self.rooms_cleared,kills=self.run_kills,bosses=self.run_bosses,superiors=self.run_superiors,active_rifts=len(self.player_state.active_rifts.active_ids),difficulty=GAME_SETTINGS.difficulty,difficulty_reward_mult=self.difficulty.reward_mult*daily_mult,ng_plus_cycle=int(endgame.get("ng_plus_cycle",0)),trial_id=trial_id or None,result=result,duration_seconds=int(self.run_elapsed),build_version=BUILD_VERSION,nonce=self.run_nonce)
        global_rank=LEADERBOARD.submit(entry); self.last_run_result=entry; self.run_submitted=True
        if active_daily is not None:
            rows=LEADERBOARD.top_daily_local(active_daily.date_key,40)
            local_rank=next((i+1 for i,x in enumerate(rows) if x.run_id==entry.run_id),global_rank)
        else:
            rows=LEADERBOARD.local.top(40); local_rank=global_rank
        if local_rank<=1: self.last_run_gap_text="#1 DESTE PLACAR — agora alguém precisa passar você."
        elif local_rank<=len(rows):
            ahead=rows[local_rank-2]; gap=max(0,ahead.score-entry.score+1); self.last_run_gap_text=f"Faltaram {gap:,} pts para passar {ahead.player_name} (#{local_rank-1})."
        else: self.last_run_gap_text=f"Posição local #{local_rank}."
        self.queue_banner("RANK DIÁRIO" if active_daily is not None else "RANK LOCAL",f"{entry.score:,} pts — posição #{local_rank}",YELLOW)
        if active_daily is not None:
            self.last_daily_challenge=active_daily; finish_daily(self.player_state,entry.score); self.daily_challenge=None; self.player.daily_damage_mult=1.0; self.run_trial_id=""
        return entry

    def ensure_expedition_map(self, reset: bool = False, region_id: Optional[str] = None):
        if reset:
            complete_generated_expedition(self.player_state)
        exp_map = None if reset else load_active_map(self.player_state)
        target_region = region_id or self.campaign.current_region().id
        if target_region == "refugio_ultima_luz" or target_region not in REGIONS:
            target_region = "campos_primeira_fenda"
            self.campaign.enter_region(target_region)
        if exp_map is None or exp_map.region_id != target_region:
            seed = random.randint(1, 2_000_000_000)
            danger = max(1, REGIONS[target_region].danger + 1)
            exp_map = begin_generated_expedition(
                self.player_state, target_region, seed,
                difficulty=danger, main_length=8 + min(4, self.player_state.stats.nivel // 4),
            )
        self.expedition_map = exp_map
        # START é um nó técnico: entra automaticamente na primeira sala jogável.
        if self.expedition_map.current_id == self.expedition_map.start_id:
            next_nodes = self.expedition_map.next_nodes()
            if next_nodes:
                self.expedition_map.move_to(next_nodes[0].id)
                save_active_map(self.player_state, self.expedition_map)
        self.awaiting_expedition = False
        return self.expedition_map

    def current_room_node(self):
        if self.expedition_map is None:
            return None
        return self.expedition_map.nodes.get(self.expedition_map.current_id)

    def spawn_room_interactives(self):
        self.room_objects.clear(); node=self.current_room_node()
        if self.expedition_map is None or node is None: return
        specs=generate_room_features(base_seed=self.expedition_map.seed,node_id=node.id,room_type=node.room_type.value,region_id=self.expedition_map.region_id,depth=node.depth)
        for spec in specs:
            pos=pygame.Vector2(ROOM.left+ROOM.width*spec.x_norm,ROOM.top+ROOM.height*spec.y_norm)
            self.room_objects.append(RoomInteractive(spec.kind,pos,spec.radius,spec.hp,spec.hp,spec.power,spec.cooldown,spec.cooldown*.62 if spec.cooldown>0 else 0.0))

    def damage_room_objects_arc(self, origin: pygame.Vector2, direction: pygame.Vector2, reach: float, amount: float):
        direction=normalize(direction)
        for obj in self.room_objects:
            if obj.dead or not obj.destructible: continue
            delta=obj.pos-origin
            if delta.length()<=reach+obj.radius and normalize(delta).dot(direction)>0.12: obj.damage(amount*.85,self)

    def resolve_room_object_projectile(self, projectile: Projectile) -> bool:
        if projectile.dead or projectile.owner!="player": return False
        for obj in self.room_objects:
            if obj.dead or not obj.destructible: continue
            if circle_collision(projectile.pos,projectile.radius,obj.pos,obj.radius):
                obj.damage(projectile.damage*.90,self)
                if projectile.explosion_radius>0: projectile.explode(self)
                if projectile.pierce>0: projectile.pierce-=1
                else: projectile.dead=True
                return True
        return False

    def start_daily_challenge(self):
        challenge=daily_challenge(); complete_generated_expedition(self.player_state); start_daily(self.player_state,challenge)
        self.daily_challenge=challenge; self.last_daily_challenge=challenge; self.run_trial_id=challenge.trial_id; self.player_state.campaign["current_region"]=challenge.region_id
        self.player_state.active_rifts=ActiveRiftState(); rng=random.Random(challenge.seed ^ 0xBADC0DE); rifts=list(RIFT_REGISTRY.keys())
        for rid in rng.sample(rifts,min(challenge.starting_rifts,len(rifts))): self.player_state.active_rifts.add(rid)
        self.player=PlayerAvatar(self.player_state); self.player.daily_damage_mult=challenge.player_damage_mult
        self.enemies.clear(); self.projectiles.clear(); self.area_effects.clear(); self.room_objects.clear(); self.heals.clear(); self.particles.clear(); self.floating_texts.clear(); self.slashes.clear(); self.impact_rings.clear()
        self.wave=0; self.rooms_cleared=0; self.score=0; self.run_kills=0; self.run_bosses=0; self.run_superiors=0; self.run_elapsed=0.0; self.run_submitted=False; self.run_nonce=f"{random.getrandbits(96):024x}"
        self.combo_count=0; self.combo_timer=0.0; self.combo_best=0; self.combo_damage=0.0; self.offer_mode=None; self.game_over=False; self.awaiting_expedition=False
        danger=max(2,REGIONS[challenge.region_id].danger+2); self.expedition_map=begin_generated_expedition(self.player_state,challenge.region_id,challenge.seed,difficulty=danger,main_length=10)
        next_nodes=self.expedition_map.next_nodes()
        if next_nodes: self.expedition_map.move_to(next_nodes[0].id); save_active_map(self.player_state,self.expedition_map)
        LEADERBOARD.refresh_daily_online(challenge.date_key); self.meta_panel=None
        self.queue_banner(challenge.title,f"Seed {challenge.seed} • score x{challenge.score_mult:.2f}",PURPLE); self.spawn_next_room()

    def _secret_revealed(self, node_id: str) -> bool:
        node = self.expedition_map.nodes[node_id]
        if not node.hidden:
            return True
        legacy = legacy_modifiers(self.player_state)
        carto = max(0, reputation(self.player_state, "cartografos"))
        ff = faction_reward_flags(self.player_state)
        faction_bonus = (0.10 if "map_secret_hint" in ff else 0.0) + (0.12 if "rare_expedition_routes" in ff else 0.0)
        signal_bonus = 0.16 if self.echo_signal_timer > 0 else 0.0
        chance = min(0.88, 0.16 + legacy.get("secret_hint", 0.0) + carto / 400.0 + faction_bonus + signal_bonus)
        token = f"{self.expedition_map.seed}|{self.expedition_map.current_id}|{node_id}|{self.player_state.identity.nome}"
        roll = int.from_bytes(token.encode("utf-8"), "little", signed=False) % 10000 / 10000.0
        return roll < chance

    def open_route_offer(self):
        if self.expedition_map is None:
            self.awaiting_expedition = True
            self.meta_panel = "hub"
            self.hub_tab = "expedition"
            return
        candidates = [n for n in self.expedition_map.next_nodes(include_hidden=True) if self._secret_revealed(n.id)]
        if not candidates:
            complete_generated_expedition(self.player_state)
            self.expedition_map = None
            self.awaiting_expedition = True
            self.meta_panel = "hub"
            self.hub_tab = "expedition"
            self.queue_banner("EXPEDIÇÃO CONCLUÍDA", "Escolha a próxima rota no Refúgio.", GREEN)
            return
        if len(candidates) == 1:
            self.expedition_map.move_to(candidates[0].id)
            save_active_map(self.player_state, self.expedition_map)
            self.spawn_next_room()
            return
        max_choices = 3 if "extra_branch_choice" in faction_reward_flags(self.player_state) else 2
        self.route_offers = [n.id for n in candidates[:max_choices]]
        self.offer_mode = "route"

    def choose_route_offer(self, index: int):
        if self.offer_mode != "route" or index < 0 or index >= len(self.route_offers):
            return
        node_id = self.route_offers[index]
        if self.expedition_map is None or not self.expedition_map.move_to(node_id):
            return
        node = self.expedition_map.nodes[node_id]
        if node.hidden:
            wg = self.player_state.campaign.setdefault("world_generation", {})
            wg["secrets_found"] = int(wg.get("secrets_found", 0)) + 1
            add_metric(self.player_state, "secrets_found", 1)
        save_active_map(self.player_state, self.expedition_map)
        self.offer_mode = None
        self.route_offers = []
        self.spawn_next_room()

    def _collect_meta_rewards(self):
        pending = collect_pending_echo_rewards(self.player_state)
        if pending > 0:
            add_echoes(self.player_state, pending)
            self.queue_banner("ECOS RECEBIDOS", f"+{pending} Ecos de conquistas/provações", CYAN)

    def complete_current_room(self):
        node = self.current_room_node()
        self.rooms_cleared += 1
        self.campaign.record(CampaignEvent.ROOM_CLEARED, amount=1)
        add_metric(self.player_state, "rooms_cleared", 1)
        trial_finished, _ = progress_trial_room(self.player_state)
        if trial_finished:
            self.queue_banner("PROVAÇÃO CONCLUÍDA", "Recompensa em Ecos liberada.", CYAN)
        self._collect_meta_rewards()
        if self.player_state.active_rifts.active_ids:
            reward_rift_materials(
                self.player_state,
                self.player_state.active_rifts.active_ids[-1],
                elite=bool(node and node.room_type in (RoomType.ELITE, RoomType.BOSS)),
            )
        self.process_campaign_rewards()

        if node and node.room_type == RoomType.BOSS:
            self.submit_run_result("victory")
            rewards = finish_expedition(
                self.player_state, survived=True, rooms=self.rooms_cleared,
                bosses=self.run_bosses, active_rifts=len(self.player_state.active_rifts.active_ids),
                gold_reward=max(80, int(120 * node.reward * legacy_modifiers(self.player_state).get("gold_return_mult", 1.0) * legacy_modifiers(self.player_state).get("rift_reward_mult", 1.0))), echo_reward=1,
            )
            complete_generated_expedition(self.player_state)
            self.expedition_map = None
            self.awaiting_expedition = True
            self.queue_banner("EXPEDIÇÃO CONCLUÍDA", f"Retorno: +{rewards.get('gold_reward', 0)} ouro", GREEN)
            if postgame_available(self.player_state):
                self.trigger_experience("postgame_unlocked")
            # Boss rush controla a próxima luta sem voltar ao Refúgio.
            if self.boss_rush_active and self.boss_rush_queue: self.spawn_next_room(); return
            self.meta_panel="ranking"; self.ranking_daily_view=bool(self.last_run_result and str(self.last_run_result.trial_id).startswith("daily:"))
            if self.ranking_daily_view:
                key=self.last_run_result.trial_id.split(":",2)[1]; LEADERBOARD.refresh_daily_online(key)
            else: LEADERBOARD.online.refresh_async()
            return

        if self.pending_level_ups > 0:
            self.open_skill_offer()
        else:
            self.open_route_offer()

    def resolve_noncombat_room(self, node):
        label = node.room_type.value.upper()
        if node.room_type == RoomType.REST:
            self.player.heal(self.player.max_hp * 0.30)
            self.queue_banner("DESCANSO", "Vida recuperada antes da próxima rota.", GREEN)
        elif node.room_type == RoomType.SHRINE:
            self.award_xp(max(25, int(45 * node.reward)))
            self.queue_banner("SANTUÁRIO", "O Véu devolveu parte do que aprendeu com você.", CYAN)
        elif node.room_type == RoomType.EVENT:
            event = self.campaign.choose_dynamic_event()
            if event:
                self.campaign.record(CampaignEvent.DYNAMIC_EVENT_COMPLETED, target_id=event.id)
                self.player_state.stats.ouro += max(20, int(55 * node.reward))
                self.queue_banner(event.nome.upper(), event.descricao, PURPLE)
            else:
                self.queue_banner("ECO INSTÁVEL", "Nada permaneceu tempo suficiente para ser catalogado.", PURPLE)
        elif node.room_type == RoomType.TREASURE:
            # Força uma rolagem de equipamento de qualidade boss sem precisar de inimigo-fantasma.
            item = roll_equipment_drop(self.player_state, elite=True, boss=True, luck=0.35 + node.reward * 0.08)
            if item:
                self.player_state.inventory.append(make_inventory_item(item.id, source="treasure"))
                self.campaign.record(CampaignEvent.EQUIPMENT_COLLECTED, target_id=item.id)
                self.queue_banner("TESOURO", item.nome, RARITY_COLORS[item.raridade])
            else:
                self.player_state.stats.ouro += 100
                self.queue_banner("TESOURO", "+100 ouro", YELLOW)
        elif node.room_type == RoomType.SECRET:
            add_metric(self.player_state, "secrets_found", 1)
            self.player_state.stats.ouro += max(80, int(130 * node.reward))
            self.queue_banner("SALA SECRETA", "Você encontrou uma rota que não deveria existir.", PURPLE)
        elif node.room_type == RoomType.MERCHANT:
            self.queue_banner("MERCADOR DE CAMPO", "O estoque do Refúgio pode ser acessado agora.", ORANGE)
            self.meta_panel = "hub"
            self.hub_tab = "vendors"
        else:
            self.queue_banner(label, "A rota se estabilizou.", GRAY)
        self.complete_current_room()

    def start_region_expedition(self, region_id: str, contract_id: Optional[str] = None):
        plan = ExpeditionPlan(
            region_id=region_id, contract_id=contract_id,
            consumables={k: int(v) for k, v in self.expedition_supply_loadout.items() if int(v) > 0},
            protected_reward=bool(self.expedition_protected_reward),
        )
        ok, message = prepare_expedition(self.player_state, plan)
        if not ok:
            self.meta_message = message
            return False
        ok, message, launched = launch_prepared_expedition(self.player_state)
        if not ok or launched is None:
            self.meta_message = message
            return False
        self.run_supplies = dict(launched.consumables)
        self.run_protected_reward = bool(launched.protected_reward)
        self.expedition_supply_loadout = {}
        self.expedition_protected_reward = False
        self.player_state.active_rifts = ActiveRiftState()
        self.player = PlayerAvatar(self.player_state)
        self.enemies.clear(); self.projectiles.clear(); self.area_effects.clear(); self.heals.clear()
        self.wave = 0; self.rooms_cleared = 0; self.run_kills = 0; self.run_bosses = 0; self.run_superiors = 0
        self.run_elapsed = 0.0; self.run_submitted = False; self.run_nonce = f"{random.getrandbits(96):024x}"
        self.telemetry = CombatTelemetry(); self.skill_cooldowns.clear(); self.awaiting_expedition = False
        self.ensure_expedition_map(reset=True, region_id=region_id)
        self.meta_panel = None; self.hub_tab = "main"; self.meta_message = message
        self.spawn_next_room()
        return True

    def start_boss_rush(self):
        if not postgame_available(self.player_state):
            self.meta_message = "Boss Rush libera depois de concluir a campanha."
            return False
        self.boss_rush_active = True
        self.boss_rush_queue = list(boss_rush_roster(self.player_state))
        self.enemies.clear(); self.projectiles.clear(); self.area_effects.clear(); self.heals.clear()
        self.rooms_cleared = 0; self.run_kills = 0; self.run_bosses = 0; self.run_superiors = 0
        self.run_elapsed = 0.0; self.run_submitted = False; self.run_nonce = f"{random.getrandbits(96):024x}"
        self.meta_panel = None; self.awaiting_expedition = False
        self.spawn_next_room()
        return True

    def use_run_supply(self, index: int) -> bool:
        ids = list(CONSUMABLES.keys())
        if index < 0 or index >= len(ids):
            return False
        cid = ids[index]
        count = int(self.run_supplies.get(cid, 0))
        if count <= 0:
            self.meta_message = f"Sem {CONSUMABLES[cid].nome} nesta expedição."
            self.audio.play("ui_back")
            return False

        used = True
        if cid == "frasco_vital":
            if "healing_disabled" in self.active_trial_modifiers():
                self.meta_message = "Esta Provação bloqueia cura."; used = False
            else:
                before = self.player.hp
                self.player.heal(self.player.max_hp * 0.35)
                self.telemetry.curas_usadas += 1
                self.meta_message = f"Frasco Vital: +{int(self.player.hp-before)} HP."
        elif cid == "tonico_fluxo":
            gain = self.player.resource_max * 0.45
            if self.player.class_id in ("guerreiro", "rasgado"):
                self.player.resource = max(0.0, self.player.resource - gain)
            else:
                self.player.resource = min(self.player.resource_max, self.player.resource + gain)
            self.tonic_timer = max(self.tonic_timer, 10.0)
            self.meta_message = "Tônico de Fluxo ativado por 10s."
        elif cid == "sal_estabilizador":
            self.rift_guard_timer = max(self.rift_guard_timer, 12.0)
            self.meta_message = "Sal Estabilizador: influência negativa das Rupturas reduzida por 12s."
        elif cid == "sinalizador_eco":
            self.echo_signal_timer = max(self.echo_signal_timer, 120.0)
            self.meta_message = "Sinalizador de Eco: rotas raras ficam mais fáceis de encontrar."
        elif cid == "pedra_retorno":
            self.run_supplies[cid] = count - 1
            self.meta_message = "Pedra de Retorno ativada."
            self.safe_return_to_hub()
            return True
        elif cid == "oleo_carmesim":
            self.crimson_oil_timer = max(self.crimson_oil_timer, 12.0)
            self.meta_message = "Óleo Carmesim: +25% dano e +15% dano recebido por 12s."
        elif cid == "agulha_temporal":
            active = [(sid, cd) for sid, cd in self.skill_cooldowns.items() if cd > 0]
            if active:
                sid, cd = max(active, key=lambda pair: pair[1])
                self.skill_cooldowns[sid] = cd * 0.45
                self.meta_message = f"Agulha Temporal reduziu o maior cooldown em 55%."
            else:
                self.meta_message = "Nenhum cooldown ativo."; used = False
        elif cid == "selo_memoria":
            self.run_protected_reward = True
            self.meta_message = "Selo de Memória protege a recompensa desta expedição."
        else:
            used = False

        if used:
            self.run_supplies[cid] = count - 1
            self.audio.play("heal" if cid == "frasco_vital" else "ui_confirm")
        return used

    def safe_return_to_hub(self):
        self.submit_run_result("abandoned")
        rewards = finish_expedition(
            self.player_state, survived=True, rooms=self.rooms_cleared, bosses=self.run_bosses,
            active_rifts=len(self.player_state.active_rifts.active_ids),
            gold_reward=max(20, self.score // 10), echo_reward=1 if self.rooms_cleared >= 3 else 0,
        )
        complete_generated_expedition(self.player_state)
        self.expedition_map = None; self.awaiting_expedition = True
        self.player_state.active_rifts.active_ids.clear()
        self.enemies.clear(); self.projectiles.clear(); self.area_effects.clear(); self.heals.clear()
        self.meta_panel = "hub"; self.hub_tab = "expedition"
        self.queue_banner("RETORNO SEGURO", f"A Pedra preservou a retirada. +{rewards.get('gold_reward',0)} ouro", CYAN)
        self.autosave.save_now(self.player_state, AutosaveReason.CHECKPOINT)

    # --------------------------------------------------------
    # SPAWN / SQUADS
    # --------------------------------------------------------

    def random_edge_spawn(self):
        side = random.choice(("top", "bottom", "left", "right"))
        if side == "top":
            return pygame.Vector2(random.randint(ROOM.left + 40, ROOM.right - 40), ROOM.top + 35)
        if side == "bottom":
            return pygame.Vector2(random.randint(ROOM.left + 40, ROOM.right - 40), ROOM.bottom - 35)
        if side == "left":
            return pygame.Vector2(ROOM.left + 35, random.randint(ROOM.top + 40, ROOM.bottom - 40))
        return pygame.Vector2(ROOM.right - 35, random.randint(ROOM.top + 40, ROOM.bottom - 40))

    def next_story_boss_id(self) -> str:
        defeated = self.player_state.progression.bosses_derrotados
        story_order = (
            "guardiao_primeira_ruptura",
            "matriarca_sussurrante",
            "primeiro_rasgado",
            "cronarca_fraturado",
        )
        for boss_id in story_order:
            if boss_id not in defeated:
                return boss_id

        # O boss final só entra quando o próprio sistema de endgame considerar
        # a campanha pronta. Isso impede pular a reta final por causa do modo arena.
        if FINAL_BOSS_ID not in defeated:
            return FINAL_BOSS_ID if final_boss_available(self.player_state) else "guardiao_primeira_ruptura"

        # Pós-jogo: o Eco entra somente depois do boss final.
        if ECHO_BOSS_ID not in defeated:
            return ECHO_BOSS_ID
        return "guardiao_primeira_ruptura"

    def spawn_boss_enemy(self, boss_id: str, difficulty: float):
        definition = BOSSES[boss_id]
        memory = self.player_state.veil_memory.get(boss_id)
        self.current_boss_strategy = memory.choose_strategy() if memory.encontros > 0 else "variacao_tatica"
        boss = Enemy(
            self.random_edge_spawn(), "guardian", difficulty,
            squad_id=999, doctrine=choose_squad_doctrine(()), role="Boss",
        )
        boss.boss_id = boss_id
        boss.boss_director = BossDirector(boss_id, self.player_state, seed=self.wave * 997 + self.rooms_cleared)
        self.decorate_enemy(boss, boss=True)
        self.enemies.append(boss)
        self.boss_spawn_line_shown = False
        definition_region = definition.region_id
        campaign_state = self.player_state.campaign
        unlocked = set(campaign_state.setdefault("unlocked_regions", ["refugio_ultima_luz", "campos_primeira_fenda"]))
        unlocked.add(definition_region)
        campaign_state["unlocked_regions"] = sorted(unlocked)
        self.campaign.enter_region(definition_region)
        self.queue_banner(definition.nome.upper(), definition.title, ORANGE)
        intro = boss.boss_director.intro_line()
        if intro: self.speak_enemy(definition.nome,intro,ORANGE,priority=5,duration=2.8)
        self.start_boss_cinematic(boss)

    def spawn_next_room(self):
        self.transition_alpha = max(self.transition_alpha, 155.0)
        self.wave += 1
        ng_cycle = int(ensure_endgame_state(self.player_state).get("ng_plus_cycle", 0))
        ng = ng_plus_modifiers(ng_cycle)
        difficulty = (1.0 + self.wave * 0.075) * float(ng["enemy_hp"])

        # Boss Rush usa a mesma camada de boss real, mas sem mapa entre as lutas.
        if self.boss_rush_active:
            if not self.boss_rush_queue:
                self.boss_rush_active = False
                self.submit_run_result("victory")
                self.queue_banner("BOSS RUSH CONCLUÍDO", "Você atravessou todas as memórias do Véu.", YELLOW)
                self.meta_panel = "ranking"
                return
            boss_id = self.boss_rush_queue.pop(0)
            self.spawn_boss_enemy(boss_id, difficulty * 1.08)
            return

        if self.expedition_map is None:
            if self.awaiting_expedition:
                return
            self.ensure_expedition_map()

        node = self.current_room_node()
        if node is None:
            self.awaiting_expedition = True
            self.meta_panel = "hub"; self.hub_tab = "expedition"
            return

        self.spawn_room_interactives()
        # Salas de escolha/descanso/tesouro/etc. agora existem de verdade no loop.
        if node.room_type == RoomType.RIFT:
            self.trigger_experience("first_rift_seen")
            self.open_rift_offer()
            return
        if node.room_type in (RoomType.REST, RoomType.SHRINE, RoomType.EVENT, RoomType.TREASURE, RoomType.SECRET, RoomType.MERCHANT):
            self.resolve_noncombat_room(node)
            return
        if node.room_type == RoomType.BOSS:
            boss_id = self.next_story_boss_id()
            # Quando a região possui seu boss canônico ainda não derrotado, ele tem prioridade.
            regional = [bid for bid, definition in BOSSES.items() if definition.region_id == self.expedition_map.region_id]
            regional_undefeated = [bid for bid in regional if bid not in self.player_state.progression.bosses_derrotados]
            if regional_undefeated:
                boss_id = regional_undefeated[0]
            self.spawn_boss_enemy(boss_id, difficulty)
            return

        region_id = self.expedition_map.region_id
        performance = PerformanceSnapshot(
            hp_ratio=self.player.hp / max(1, self.player.max_hp),
            recent_deaths=min(3, int(self.player_state.campaign.get("recent_deaths", 0))),
            rooms_cleared_fast=1 if self.rooms_cleared >= 3 and self.player.hp / max(1, self.player.max_hp) > 0.80 else 0,
            healing_used_recently=min(4, self.telemetry.curas_usadas),
            active_rifts=len(self.player_state.active_rifts.active_ids),
        )
        plan = self.encounter_director.generate(region_id, max(1, node.depth), performance)
        doctrine = SQUAD_DOCTRINES.get(plan.doctrine, choose_squad_doctrine(()))
        trial_mods = self.active_trial_modifiers()

        for squad_id, group in enumerate(plan.spawn_groups):
            local_group = list(group)
            if "hunter_weight_up" in trial_mods and local_group:
                local_group[-1] = "hunter"
            for archetype in local_group:
                kind = "shooter" if archetype == "support" else archetype
                role = "Support" if archetype == "support" else archetype.title()
                enemy = Enemy(
                    self.random_edge_spawn(), kind, difficulty,
                    squad_id=squad_id, doctrine=doctrine, role=role,
                )
                bonus = plan.superior_chance_bonus + self.difficulty.superior_chance + float(ng["superior_chance_bonus"])
                if node.room_type == RoomType.ELITE:
                    bonus += 0.22
                if "superior_chance_up" in trial_mods:
                    bonus += 0.18
                if self.echo_signal_timer > 0: bonus += 0.10
                if self.daily_challenge is not None: bonus += self.daily_challenge.superior_bonus
                force_superior=random.random()<max(0.0,min(0.85,bonus))
                self.decorate_enemy(
                    enemy, elite=node.room_type == RoomType.ELITE or kind in ("tank", "hunter"),
                    force_superior=force_superior,
                )
                if "pacing_accelerates" in trial_mods:
                    progress = int(ensure_challenge_state(self.player_state).get("trial_progress", 0))
                    enemy.speed *= 1.0 + min(0.30, progress * 0.035)
                self.enemies.append(enemy)

        if node.room_type == RoomType.ELITE:
            self.queue_banner("SALA DE ELITE", "O Véu concentrou ameaças superiores nesta rota.", ORANGE)
        if plan.recovery_after:
            self.queue_banner("O VÉU HESITA", "A próxima pressão foi reduzida pelo Diretor de Encontros.", CYAN)
        self.trigger_experience("first_enemy_seen")

    def decorate_enemy(self, enemy: Enemy, elite: bool = False, boss: bool = False, force_superior: bool = False):
        creature_id = creature_id_for_archetype(enemy.kind)
        traits = roll_spawn_traits(
            creature_id=creature_id,
            wave=self.wave,
            active_rift_ids=self.player_state.active_rifts.active_ids,
            elite=elite,
            boss=boss,
            force_superior=force_superior,
        )
        apply_traits_to_enemy(enemy, traits)
        daily_hp=self.daily_challenge.enemy_hp_mult if self.daily_challenge is not None else 1.0
        daily_damage=self.daily_challenge.enemy_damage_mult if self.daily_challenge is not None else 1.0
        daily_speed=self.daily_challenge.enemy_speed_mult if self.daily_challenge is not None else 1.0
        enemy.max_hp=max(1,int(enemy.max_hp*self.difficulty.enemy_hp*daily_hp)); enemy.hp=enemy.max_hp
        enemy.damage=max(1,int(enemy.damage*self.difficulty.enemy_damage*daily_damage)); enemy.speed*=self.difficulty.enemy_speed*daily_speed
        enemy.memory_id = superior_memory_id(traits)
        before, after = record_encounter(
            self.player_state,
            creature_id,
            traits.mutation_ids,
        )
        if before != after:
            self.queue_banner(
                "ARQUIVO DO VÉU",
                f"{CREATURES[creature_id].nome}: conhecimento {after.value}.",
                CYAN,
            )
        if traits.tier == CreatureTier.SUPERIOR:
            self.audio.play("superior_spawn")
            subtitle = mutation_display(traits.mutation_ids) or "Sem mutação catalogada"
            if "superior_memory_scan" in faction_reward_flags(self.player_state):
                mem = self.player_state.veil_memory.get(superior_memory_id(traits))
                subtitle += f" • Memória {mem.rank().value}"
            self.queue_banner(
                "INIMIGO SUPERIOR",
                f"{traits.superior_name} — {subtitle}",
                ORANGE,
            )
            self.trigger_experience("first_superior_seen")

    @staticmethod
    def kind_for_role(role: str) -> str:
        role_lower = role.lower()
        if role_lower == "tank":
            return "tank"
        if role_lower == "ranged" or role_lower == "support":
            return "shooter"
        if role_lower in ("hunter", "flanker"):
            return "hunter"
        return "crawler"

    def squad_center(self, squad_id: int) -> pygame.Vector2:
        members = [e for e in self.enemies if e.squad_id == squad_id and not e.dead]
        if not members:
            return self.player.pos.copy()
        total = pygame.Vector2()
        for enemy in members:
            total += enemy.pos
        return total / len(members)

    def nearest_enemy(self, pos: pygame.Vector2) -> Optional[Enemy]:
        live = [e for e in self.enemies if not e.dead]
        if not live:
            return None
        return min(live, key=lambda e: e.pos.distance_squared_to(pos))

    # --------------------------------------------------------
    # BOSS / MEMÓRIA
    # --------------------------------------------------------

    def on_boss_defeated(self,enemy: Enemy):
        self.audio.play("boss_death"); self.hit_stop=max(self.hit_stop,0.13); self.cinematic_flash=max(self.cinematic_flash,0.16)
        self.add_shake(18,0.28,direction=normalize(enemy.pos-self.player.pos)); self.impact_rings.append(ImpactRing(enemy.pos.copy(),WHITE,enemy.radius,enemy.radius+150,0.52,0.52,7))
        boss_id = getattr(enemy, "boss_id", None) or "guardiao_primeira_ruptura"
        memory = self.player_state.veil_memory.get(boss_id)
        memory.register_encounter(self.telemetry, player_died=False)
        self.player_state.progression.bosses_derrotados.add(boss_id)
        reward_boss_materials(self.player_state, boss_id)
        self.campaign.record(CampaignEvent.BOSS_DEFEATED, target_id=boss_id)
        add_metric(self.player_state, "bosses", 1)
        self.run_bosses += 1

        if boss_id == FINAL_BOSS_ID:
            record_final_boss_defeat(self.player_state)
            self.ending_offers = eligible_endings(self.player_state)
            self.offer_mode = "ending" if self.ending_offers else None
            self.queue_banner("O VÉU CEDEU", "O fim agora depende da sua escolha.", PURPLE)
        elif boss_id == ECHO_BOSS_ID:
            record_echo_boss_defeat(self.player_state)
            self.queue_banner("ECO SUPERADO", "O Véu não pode mais imitar tudo que você é.", CYAN)

        self.process_campaign_rewards()
        self.autosave.request(AutosaveReason.BOSS_DERROTADO)
        boss_name = BOSSES.get(boss_id).nome if boss_id in BOSSES else boss_id
        self.queue_banner("MEMÓRIA QUEBRADA", f"{boss_name}: memória {memory.rank().value}", GREEN)
        self.evaluate_unlocks()

    def register_death_memory(self):
        guardians = [e for e in self.enemies if e.kind == "guardian" and not e.dead]
        if guardians:
            boss_enemy = guardians[0]
            boss_id = getattr(boss_enemy, "boss_id", None) or "guardiao_primeira_ruptura"
            memory = self.player_state.veil_memory.get(boss_id)
            memory.register_encounter(self.telemetry, player_died=True)

        superiors = [
            e for e in self.enemies
            if not e.dead and getattr(e, "creature_tier", "") == CreatureTier.SUPERIOR.value
        ]
        if superiors:
            culprit = min(
                superiors,
                key=lambda e: e.pos.distance_squared_to(self.player.pos),
            )
            creature_id = getattr(culprit, "creature_id", creature_id_for_archetype(culprit.kind))
            mutation_ids = list(getattr(culprit, "mutation_ids", []))
            record_death_to(self.player_state, creature_id, mutation_ids)
            memory_id = getattr(culprit, "memory_id", creature_id)
            self.player_state.veil_memory.get(memory_id).register_encounter(
                self.telemetry,
                player_died=True,
            )

        if guardians or superiors:
            self.autosave.request(AutosaveReason.CHECKPOINT)

    # --------------------------------------------------------
    # BESTIÁRIO / SUPERIORES
    # --------------------------------------------------------

    def on_enemy_killed(self, enemy: Enemy):
        creature_id = getattr(enemy, "creature_id", creature_id_for_archetype(enemy.kind))
        mutation_ids = list(getattr(enemy, "mutation_ids", []))
        superior = getattr(enemy, "creature_tier", "") == CreatureTier.SUPERIOR.value
        add_metric(self.player_state, "kills", 1)
        self.run_kills += 1
        if superior:
            add_metric(self.player_state, "superiors", 1)
            self.run_superiors += 1
        before, after = record_kill(
            self.player_state,
            creature_id,
            mutation_ids=mutation_ids,
            superior=superior,
            superior_name=getattr(enemy, "superior_name", None),
        )
        if superior:
            traits = roll_spawn_traits(creature_id, self.wave, force_superior=True)
            traits.mutation_ids = mutation_ids
            traits.superior_name = getattr(enemy, "superior_name", None)
            record_superior_history(self.player_state, traits, defeated=True)
            memory_id = getattr(enemy, "memory_id", superior_memory_id(traits))
            self.player_state.veil_memory.get(memory_id).register_encounter(
                self.telemetry,
                player_died=False,
            )
            self.autosave.request(AutosaveReason.DESBLOQUEIO_IMPORTANTE)
        if before != after:
            self.queue_banner(
                "CONHECIMENTO AMPLIADO",
                f"{CREATURES[creature_id].nome}: {after.value}.",
                CYAN,
            )

    # --------------------------------------------------------
    # EQUIPAMENTOS / DESBLOQUEIOS
    # --------------------------------------------------------

    def try_equipment_drop(self, enemy: Enemy):
        elite = enemy.kind in ("tank", "hunter")
        boss = enemy.kind == "guardian"
        legacy = legacy_modifiers(self.player_state)
        hubfx = facility_effects(self.player_state)
        luck = min(0.90, self.player_state.stats.nivel * 0.018 + len(self.player_state.active_rifts.active_ids) * 0.025 + legacy.get("loot_luck", 0.0) + hubfx.get("loot_luck", 0.0))
        item = roll_equipment_drop(self.player_state, elite=elite, boss=boss, luck=luck)
        if item is None:
            return

        self.player_state.inventory.append(make_inventory_item(item.id, source=enemy.kind))
        if item.raridade in (Rarity.LENDARIA, Rarity.MITICA, Rarity.CORROMPIDA):
            self.audio.play("ui_legendary_drop")
        elif item.raridade in (Rarity.RARA, Rarity.EPICA):
            self.audio.play("ui_rare_drop")
        else:
            self.audio.play("ui_confirm")
        self.campaign.record(CampaignEvent.EQUIPMENT_COLLECTED, target_id=item.id)
        self.process_campaign_rewards()
        self.queue_banner(item.raridade.value.upper(), f"Equipamento obtido: {item.nome}", RARITY_COLORS[item.raridade])
        self.autosave.request(AutosaveReason.ITEM_IMPORTANTE)
        self.evaluate_unlocks()

    def equip_inventory_index(self, index: int):
        ids = inventory_equipment_ids(self.player_state)
        if index < 0 or index >= len(ids):
            self.inventory_message = "Sem item neste espaço."
            return

        item_id = ids[index]
        ok, message = equip_item(self.player_state, item_id)
        self.inventory_message = message
        if ok:
            self.player.refresh_from_state()
            self.autosave.request(AutosaveReason.ITEM_IMPORTANTE)

    def evaluate_unlocks(self):
        unlocked = evaluate_class_unlocks(self.player_state)
        for class_id in unlocked:
            definition = CLASS_REGISTRY[class_id]
            self.campaign.record(CampaignEvent.CLASS_UNLOCKED, target_id=class_id)
            self.process_campaign_rewards()
            self.queue_banner("NOVA CLASSE", f"{definition.nome} desbloqueado para novos personagens.", GREEN)
            self.autosave.request(AutosaveReason.DESBLOQUEIO_IMPORTANTE)

    def play_skill_audio(self, skill_id: str):
        skill = SKILL_REGISTRY.get(skill_id)
        if skill is None:
            self.audio.play("magic_cast")
            return
        tags = set(skill.tags)
        if "arrow" in tags or "ranged" in tags and self.player.class_id in ("arqueiro", "artifice"):
            self.audio.play("arrow_shot")
        elif "melee" in tags or "slash" in tags or "scythe" in tags:
            self.audio.play("sword_swing")
        else:
            self.audio.play("magic_cast")

    # --------------------------------------------------------
    # VOZ / REAÇÕES
    # --------------------------------------------------------

    def speak_player(
        self,
        text: str,
        priority: int = 1,
        cooldown_key: Optional[str] = None,
        duration: float = 1.45,
    ):
        event = ReactionEvent(
            event_type="player_voice",
            priority=priority,
            line=text,
            cooldown_key=cooldown_key,
            cooldown=2.2 if cooldown_key else 0.0,
        )
        if self.reaction_controller.play(event):
            self.audio.play_voice(text, self.player_state.identity.genero)
            if GAME_SETTINGS.accessibility.subtitles:
                self.speech = SpeechBubble(
                    text=text,
                    speaker=self.player_state.identity.nome,
                    color=self.player.color,
                    life=duration,
                    max_life=duration,
                    priority=priority,
                )

    def speak_enemy(
        self,
        speaker: str,
        text: str,
        color=ORANGE,
        priority=3,
        duration=2.2,
        cooldown_key: Optional[str] = None,
    ):
        if self.speech and not self.speech.dead and self.speech.priority > priority:
            return
        event = ReactionEvent(
            event_type="enemy_voice",
            priority=priority,
            line=text,
            cooldown_key=cooldown_key,
            cooldown=4.5 if cooldown_key else 0.0,
        )
        if not self.reaction_controller.play(event):
            return
        self.audio.play_enemy_voice(text)
        if GAME_SETTINGS.accessibility.subtitles:
            self.speech = SpeechBubble(text, speaker, color, duration, duration, priority)

    # --------------------------------------------------------
    # FEEDBACK
    # --------------------------------------------------------

    def queue_banner(self, title: str, subtitle: str, color=WHITE):
        entry = (str(title), str(subtitle), tuple(color))
        if self.banner_timer > 0:
            # Agora é fila de verdade: avisos de boss, missão, loot e conhecimento
            # não se sobrescrevem no mesmo frame. Limite evita spam infinito.
            if len(self.banner_queue) < 12:
                self.banner_queue.append(entry)
            return
        self.banner_title, self.banner_subtitle, self.banner_color = entry
        self.banner_timer = 3.0

    def _advance_banner_queue(self):
        if self.banner_timer > 0 or not self.banner_queue:
            return
        self.banner_title, self.banner_subtitle, self.banner_color = self.banner_queue.pop(0)
        self.banner_timer = 3.0

    def register_combat_hit(self, amount: float, critical: bool=False, target: Optional[Enemy]=None):
        if amount<=0: return
        if self.combo_timer>0: self.combo_count+=1
        else: self.combo_count=1; self.combo_damage=0.0
        self.combo_timer=1.55 if critical else 1.35; self.combo_best=max(self.combo_best,self.combo_count); self.combo_damage+=amount
        self.score += min(22,max(1,int(amount*0.035)))

    def break_combo(self):
        self.combo_count=0; self.combo_timer=0.0; self.combo_damage=0.0

    def combo_grade(self) -> str:
        c=self.combo_count
        return "SSS" if c>=35 else "SS" if c>=24 else "S" if c>=16 else "A" if c>=10 else "B" if c>=6 else "C" if c>=3 else ""

    def start_boss_cinematic(self,boss: Enemy):
        definition=BOSSES.get(getattr(boss,"boss_id",""))
        if definition is None: return
        self.boss_cinematic_timer=1.75; self.boss_cinematic_max=1.75; self.boss_cinematic_title=definition.nome.upper(); self.boss_cinematic_subtitle=definition.title
        self.cinematic_flash=max(self.cinematic_flash,0.18); self.hit_stop=max(self.hit_stop,0.08)

    def start_boss_phase_transition(self,boss: Enemy,phase_name: str):
        self.boss_phase_timer=0.48; self.boss_phase_name=phase_name.upper(); self.cinematic_flash=max(self.cinematic_flash,0.22)
        self.impact_rings.append(ImpactRing(boss.pos.copy(),ORANGE,boss.radius,boss.radius+120,0.46,0.46,6))
        for _ in range(28): self.particles.append(Particle.burst(boss.pos,ORANGE,90,330,0.50,7))
        self.add_shake(14,0.22,direction=normalize(boss.pos-self.player.pos))

    def add_shake(self,power: float,duration: float,direction: Optional[pygame.Vector2]=None):
        if GAME_SETTINGS.accessibility.reduced_screen_shake: power*=0.30; duration*=0.70
        self.shake_power=max(self.shake_power,power); self.shake_timer=max(self.shake_timer,duration)
        if direction is not None and direction.length_squared()>0: self.shake_direction=normalize(direction)

    def camera_offset(self):
        target=pygame.Vector2()
        if self.shake_timer>0:
            rand=pygame.Vector2(random.uniform(-self.shake_power,self.shake_power),random.uniform(-self.shake_power,self.shake_power))
            directional=self.shake_direction*random.uniform(-self.shake_power*.65,self.shake_power*.65)
            target=rand*.70+directional
        self.camera_shake_offset=self.camera_shake_offset.lerp(target,0.36)
        return self.camera_shake_offset.copy()

    # --------------------------------------------------------
    # PAUSA / SAÍDA DA RUN
    # --------------------------------------------------------

    def pause_option_rects(self):
        width = 390
        height = 54
        gap = 12
        x = WIDTH // 2 - width // 2
        y0 = HEIGHT // 2 - 90
        return [pygame.Rect(x, y0 + i * (height + gap), width, height) for i in range(4)]

    def abandon_run_to_hub(self):
        # Abandono é diferente de morte: registra a run, limpa Rupturas ativas e retorna
        # ao Refúgio sem incrementar contador de mortes.
        try:
            self.campaign.append_run_history(
                result="abandoned",
                rooms=self.rooms_cleared,
                region_id=self.campaign.current_region().id,
                boss_defeated=False,
                style=self.telemetry.classify_style(),
                active_rifts=list(self.player_state.active_rifts.active_ids),
            )
        except Exception:
            pass
        self.submit_run_result("abandoned")
        if self.rooms_cleared > 0:
            try:
                finish_expedition(
                    self.player_state, survived=bool(self.run_protected_reward), rooms=self.rooms_cleared,
                    bosses=self.run_bosses, active_rifts=len(self.player_state.active_rifts.active_ids),
                    gold_reward=max(20, self.score // 12), echo_reward=0,
                )
            except Exception:
                pass
        complete_generated_expedition(self.player_state)
        self.expedition_map = None
        self.player_state.active_rifts.active_ids.clear()
        try:
            self.campaign.enter_region("refugio_ultima_luz")
        except Exception:
            self.player_state.campaign["current_region"] = "refugio_ultima_luz"
        self.autosave.save_now(self.player_state, AutosaveReason.CHECKPOINT)
        self.return_to_menu_requested = True

    def activate_pause_option(self, index: int):
        index = max(0, min(3, int(index)))
        if index == 0:
            self.paused = False
            self.abandon_confirm = False
            self.audio.play("ui_back")
        elif index == 1:
            if not self.abandon_confirm:
                self.abandon_confirm = True
                self.audio.play("ui_back")
            else:
                self.audio.play("ui_confirm")
                self.abandon_run_to_hub()
        elif index == 2:
            self.audio.play("ui_confirm")
            if not self.run_submitted and self.rooms_cleared > 0:
                self.submit_run_result("abandoned")
            self.autosave.save_now(self.player_state, AutosaveReason.CHECKPOINT)
            self.return_to_menu_requested = True
        else:
            self.autosave.save_now(self.player_state, AutosaveReason.SAIDA_DO_JOGO)
            self.audio.stop_all()
            pygame.quit()
            raise SystemExit

    # --------------------------------------------------------
    # META UI 0.10 — REFÚGIO / RANK / REBIND
    # --------------------------------------------------------

    def _number_index(self, key: int) -> Optional[int]:
        keys = (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4, pygame.K_5,
                pygame.K_6, pygame.K_7, pygame.K_8, pygame.K_9)
        return keys.index(key) if key in keys else None

    def handle_hub_event(self, event) -> bool:
        key = event.key
        if key == pygame.K_TAB and self.hub_menu_from_room:
            self.meta_panel = None
            self.hub_menu_from_room = False
            self.hub_room_open = True
            self.audio.play("ui_back")
            return True
        if key == pygame.K_BACKSPACE:
            if self.hub_tab == "vendors" and self.hub_vendor_id:
                self.hub_vendor_id = None
            elif self.hub_tab == "supplies":
                self.hub_tab = "expedition"; self.hub_page = 0
            elif self.hub_tab != "main":
                self.hub_tab = "main"; self.hub_page = 0
            return True

        idx = self._number_index(key)
        tabs = ("facilities", "craft", "vendors", "factions", "trials", "legacy", "npcs", "expedition", "postgame")
        if self.hub_tab == "main":
            if idx is not None and idx < len(tabs):
                self.hub_tab = tabs[idx]; self.hub_page = 0; self.hub_vendor_id = None
                self.audio.play("ui_confirm")
            return True

        if key in (pygame.K_PAGEUP, pygame.K_LEFTBRACKET):
            self.hub_page = max(0, self.hub_page - 1); return True
        if key in (pygame.K_PAGEDOWN, pygame.K_RIGHTBRACKET):
            self.hub_page += 1; return True

        if self.hub_tab == "facilities" and idx is not None:
            facility_ids = list(FACILITIES.keys())
            if idx < len(facility_ids):
                ok, msg = upgrade_facility(self.player_state, facility_ids[idx])
                self.meta_message = msg; self.audio.play("ui_confirm" if ok else "ui_back")
                if ok: self.autosave.request(AutosaveReason.DESBLOQUEIO_IMPORTANTE)
            return True

        if self.hub_tab == "craft":
            if key == pygame.K_s:
                self.hub_tab = "salvage"; self.hub_page = 0; return True
            recipes = list(RECIPES.values())
            page = recipes[self.hub_page * 9: self.hub_page * 9 + 9]
            if idx is not None and idx < len(page):
                ok, msg = craft(self.player_state, page[idx].id)
                self.meta_message = msg; self.audio.play("ui_confirm" if ok else "ui_back")
                if ok: self.autosave.request(AutosaveReason.ITEM_IMPORTANTE)
            return True

        if self.hub_tab == "salvage":
            items = [x for x in self.player_state.inventory if isinstance(x, dict) and x.get("instance_id")]
            page = items[self.hub_page * 9: self.hub_page * 9 + 9]
            if idx is not None and idx < len(page):
                ok, msg, gains = salvage_equipment(self.player_state, str(page[idx]["instance_id"]))
                self.meta_message = msg if ok else msg
                self.audio.play("ui_confirm" if ok else "ui_back")
                if ok: self.autosave.request(AutosaveReason.ITEM_IMPORTANTE)
            return True

        if self.hub_tab == "vendors":
            if key == pygame.K_s and self.hub_vendor_id is not None:
                self.vendor_sell_mode = not self.vendor_sell_mode
                self.hub_page = 0
                self.meta_message = "Modo venda" if self.vendor_sell_mode else "Modo compra"
                return True
            if self.hub_vendor_id is None:
                ids = list(VENDORS.keys())
                if idx is not None and idx < len(ids):
                    self.hub_vendor_id = ids[idx]; self.hub_page = 0; self.vendor_sell_mode = False
                return True
            if self.vendor_sell_mode:
                items = [x for x in self.player_state.inventory if isinstance(x, dict) and x.get("instance_id")]
                page = items[self.hub_page * 9:self.hub_page * 9 + 9]
                if idx is not None and idx < len(page):
                    ok, msg, _ = sell_equipment_instance(self.player_state, self.hub_vendor_id, str(page[idx]["instance_id"]))
                    self.meta_message = msg; self.audio.play("ui_confirm" if ok else "ui_back")
                    if ok: self.autosave.request(AutosaveReason.ITEM_IMPORTANTE)
                return True
            offers = refresh_vendor_stock(self.player_state, self.hub_vendor_id)
            page = offers[self.hub_page * 9: self.hub_page * 9 + 9]
            if idx is not None and idx < len(page):
                ok, msg = purchase_offer(self.player_state, self.hub_vendor_id, str(page[idx]["offer_id"]))
                self.meta_message = msg; self.audio.play("ui_confirm" if ok else "ui_back")
                if ok: self.autosave.request(AutosaveReason.ITEM_IMPORTANTE)
            return True

        if self.hub_tab == "factions":
            choices = list(CHOICES.values())
            if idx is not None and idx < len(choices):
                ok, msg = make_faction_choice(self.player_state, choices[idx].id)
                self.meta_message = msg; self.audio.play("ui_confirm" if ok else "ui_back")
                if ok:
                    self.trigger_experience("first_faction_choice")
                    self.autosave.request(AutosaveReason.EVENTO_HISTORIA)
            return True

        if self.hub_tab == "trials":
            if key == pygame.K_c:
                amount = collect_pending_echo_rewards(self.player_state)
                if amount:
                    add_echoes(self.player_state, amount)
                    self.meta_message = f"+{amount} Ecos coletados."
                else: self.meta_message = "Nenhum Eco pendente."
                return True
            trials = list(TRIALS.values())
            if idx is not None and idx < len(trials):
                ok, msg = start_trial(self.player_state, trials[idx].id)
                self.meta_message = msg; self.audio.play("ui_confirm" if ok else "ui_back")
                if ok:
                    self.run_trial_id = trials[idx].id
                    if "start_rifts_3" in trials[idx].modifiers:
                        remaining = [rid for rid in RIFT_REGISTRY if rid not in self.player_state.active_rifts.active_ids]
                        for rid in random.sample(remaining, min(3, len(remaining))):
                            self.player_state.active_rifts.add(rid)
                    self.player.refresh_from_state()
                    self.autosave.request(AutosaveReason.DESBLOQUEIO_IMPORTANTE)
            return True

        if self.hub_tab == "legacy":
            nodes = list(NODES.values())
            page = nodes[self.hub_page * 9: self.hub_page * 9 + 9]
            if idx is not None and idx < len(page):
                ok, msg = unlock_node(self.player_state, page[idx].id)
                self.meta_message = msg; self.audio.play("ui_confirm" if ok else "ui_back")
                if ok:
                    self.player.refresh_from_state(); self.autosave.request(AutosaveReason.DESBLOQUEIO_IMPORTANTE)
            return True

        if self.hub_tab == "npcs":
            npcs = list(NPCS.values())
            if idx is not None and idx < len(npcs):
                npc = npcs[idx]
                dialogue = select_npc_dialogue(self.player_state, npc.id)
                if dialogue:
                    self.meta_message = f"{npc.nome}: {dialogue.text}"
                    self.speak_enemy(npc.nome, dialogue.text, CYAN, priority=2, duration=3.5)
                    self.process_campaign_rewards()
                    self.autosave.request(AutosaveReason.EVENTO_HISTORIA)
                else: self.meta_message = f"{npc.nome} não tem nada novo agora."
            return True

        if self.hub_tab == "supplies":
            ids = list(CONSUMABLES.keys())
            if key == pygame.K_p:
                self.expedition_protected_reward = not self.expedition_protected_reward
                if self.expedition_protected_reward and self.expedition_supply_loadout.get("selo_memoria", 0) <= 0:
                    self.expedition_supply_loadout["selo_memoria"] = 1 if consumable_count(self.player_state, "selo_memoria") > 0 else 0
                return True
            if idx is not None and idx < len(ids):
                cid = ids[idx]
                stock = consumable_count(self.player_state, cid)
                current = int(self.expedition_supply_loadout.get(cid, 0))
                limit = expedition_supply_limit(self.player_state)
                used_without_current = sum(max(0, int(v)) for k,v in self.expedition_supply_loadout.items() if k != cid)
                max_for_item = min(stock, max(0, limit - used_without_current))
                self.expedition_supply_loadout[cid] = 0 if current >= max_for_item else current + 1
                if cid == "selo_memoria" and self.expedition_supply_loadout[cid] <= 0:
                    self.expedition_protected_reward = False
                return True
            return True

        if self.hub_tab == "expedition":
            if key == pygame.K_d: self.start_daily_challenge(); return True
            contracts=[None]+[c.id for c in self.campaign.available_contracts()]
            if key == pygame.K_s:
                self.hub_tab = "supplies"; self.hub_page = 0; return True
            if key == pygame.K_c:
                self.expedition_contract_index = (self.expedition_contract_index + 1) % max(1, len(contracts))
                return True
            regions = [rid for rid in self.player_state.campaign.get("unlocked_regions", []) if rid in REGIONS and rid != "refugio_ultima_luz"]
            if idx is not None and idx < len(regions):
                contract = contracts[self.expedition_contract_index % len(contracts)] if contracts else None
                self.start_region_expedition(regions[idx], contract)
            return True

        if self.hub_tab == "postgame":
            if idx == 0:
                try:
                    cycle = start_ng_plus(self.player_state)
                    self.trigger_experience("ng_plus_started")
                    self.meta_message = f"NG+ {cycle} iniciado. Escolha uma expedição."
                    self.awaiting_expedition = True; self.expedition_map = None
                    complete_generated_expedition(self.player_state)
                    self.autosave.save_now(self.player_state, AutosaveReason.EVENTO_HISTORIA)
                except Exception as exc:
                    self.meta_message = str(exc)
            elif idx == 1:
                self.start_boss_rush()
            elif idx == 2:
                self.meta_panel = "ranking"; LEADERBOARD.online.refresh_async()
            return True
        return True

    def handle_ranking_event(self,event)->bool:
        if event.key==pygame.K_TAB:
            self.ranking_online_view=not self.ranking_online_view
            if self.ranking_online_view:
                LEADERBOARD.refresh_daily_online(daily_challenge().date_key) if self.ranking_daily_view else LEADERBOARD.online.refresh_async()
            return True
        if event.key==pygame.K_d:
            self.ranking_daily_view=not self.ranking_daily_view
            if self.ranking_daily_view and self.ranking_online_view: LEADERBOARD.refresh_daily_online(daily_challenge().date_key)
            return True
        if event.key==pygame.K_r:
            LEADERBOARD.online.reload_config(); LEADERBOARD.refresh_daily_online(daily_challenge().date_key) if self.ranking_daily_view else LEADERBOARD.online.refresh_async(); return True
        return False

    # --------------------------------------------------------
    # INPUT
    # --------------------------------------------------------

    def handle_event(self, event):
        if event.type == pygame.QUIT:
            self.autosave.save_now(self.player_state, AutosaveReason.SAIDA_DO_JOGO)
            self.audio.stop_all()
            pygame.quit()
            raise SystemExit

        if self.hub_room_open:
            self.handle_hub_room_event(event)
            return

        if event.type != pygame.KEYDOWN and event.type != pygame.MOUSEBUTTONDOWN:
            return

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_F3:
                self.debug = not self.debug
                return

            if self.paused:
                if event.key == pygame.K_ESCAPE:
                    self.paused = False
                    self.abandon_confirm = False
                    self.audio.play("ui_back")
                elif event.key in (pygame.K_UP, pygame.K_w):
                    self.pause_selection = (self.pause_selection - 1) % 4
                    self.abandon_confirm = False
                    self.audio.play("ui_move")
                elif event.key in (pygame.K_DOWN, pygame.K_s):
                    self.pause_selection = (self.pause_selection + 1) % 4
                    self.abandon_confirm = False
                    self.audio.play("ui_move")
                elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                    self.activate_pause_option(self.pause_selection)
                return

            if self.meta_panel is not None:
                panel_close_key = {"codex": pygame.K_c, "quests": pygame.K_j, "hub": pygame.K_h, "settings": pygame.K_o, "ranking": pygame.K_l, "map": binding_key("map", pygame.K_m), "supplies": pygame.K_k}.get(self.meta_panel)
                if self.meta_panel == "hub" and event.key == pygame.K_h and not self.hub_menu_from_room:
                    self.enter_hub_room()
                    self.audio.play("ui_confirm")
                    return
                if event.key == pygame.K_ESCAPE or (panel_close_key is not None and event.key == panel_close_key):
                    # Quando uma expedição terminou, H/ESC pode fechar a interface, mas
                    # o jogo continua aguardando uma nova rota sem gerar waves invisíveis.
                    self.audio.play("ui_back")
                    if self.meta_panel == "hub" and self.hub_menu_from_room:
                        self.meta_panel = None
                        self.hub_menu_from_room = False
                        self.hub_room_open = True
                        return
                    self.meta_panel = None
                    self.meta_message = ""
                    return
                if self.meta_panel == "hub":
                    self.handle_hub_event(event)
                    return
                if self.meta_panel == "ranking":
                    self.handle_ranking_event(event)
                    return
                if self.meta_panel == "codex":
                    if event.key == pygame.K_r:
                        self.codex_research_mode = not self.codex_research_mode
                        self.meta_message = "Pesquisa do Arquivo" if self.codex_research_mode else "Bestiário"
                        return
                    if self.codex_research_mode:
                        idx = self._number_index(event.key)
                        projects = list(RESEARCH_PROJECTS.items())
                        if idx is not None and idx < len(projects):
                            project_id, project = projects[idx]
                            ok, msg = complete_research(self.player_state, project_id)
                            self.meta_message = msg
                            self.audio.play("ui_confirm" if ok else "ui_back")
                            if ok: self.autosave.request(AutosaveReason.DESBLOQUEIO_IMPORTANTE)
                        return
                    return
                if self.meta_panel == "supplies":
                    idx = self._number_index(event.key)
                    if idx is not None:
                        self.use_run_supply(idx)
                    return
                if self.meta_panel == "settings":
                    actions = list(DEFAULT_BINDINGS.keys())
                    if self.rebind_waiting:
                        action = actions[self.rebind_index]
                        name = pygame.key.name(event.key).upper()
                        aliases = {"ESCAPE": "ESC", "LEFT SHIFT": "LSHIFT", "RIGHT SHIFT": "RSHIFT", "RETURN": "ENTER", " ": "SPACE"}
                        GAME_SETTINGS.bindings[action] = aliases.get(name, name)
                        SETTINGS_MANAGER.save(GAME_SETTINGS)
                        self.rebind_waiting = False
                        self.meta_message = f"{action} → {GAME_SETTINGS.bindings[action]}"
                        self.audio.play("ui_confirm")
                        return
                    if event.key == pygame.K_b:
                        self.rebind_mode = not self.rebind_mode
                        self.meta_message = "Selecione uma ação e ENTER; depois pressione a nova tecla." if self.rebind_mode else "Rebinding fechado."
                        return
                    if self.rebind_mode:
                        if event.key in (pygame.K_UP, pygame.K_w): self.rebind_index = (self.rebind_index - 1) % len(actions)
                        elif event.key in (pygame.K_DOWN, pygame.K_s): self.rebind_index = (self.rebind_index + 1) % len(actions)
                        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER): self.rebind_waiting = True; self.meta_message = f"Pressione a nova tecla para {actions[self.rebind_index]}"
                        return
                    changed = False
                    if event.key == pygame.K_1:
                        GAME_SETTINGS.accessibility.subtitles = not GAME_SETTINGS.accessibility.subtitles; changed = True
                    elif event.key == pygame.K_2:
                        GAME_SETTINGS.accessibility.reduced_screen_shake = not GAME_SETTINGS.accessibility.reduced_screen_shake; changed = True
                    elif event.key == pygame.K_3:
                        GAME_SETTINGS.accessibility.reduced_flashes = not GAME_SETTINGS.accessibility.reduced_flashes; changed = True
                    elif event.key == pygame.K_4:
                        GAME_SETTINGS.accessibility.damage_numbers = not GAME_SETTINGS.accessibility.damage_numbers; changed = True
                    elif event.key == pygame.K_5:
                        GAME_SETTINGS.accessibility.high_contrast_telegraphs = not GAME_SETTINGS.accessibility.high_contrast_telegraphs; changed = True
                    elif event.key == pygame.K_6:
                        ids = list(DIFFICULTIES.keys())
                        current = ids.index(GAME_SETTINGS.difficulty) if GAME_SETTINGS.difficulty in ids else 0
                        GAME_SETTINGS.difficulty = ids[(current + 1) % len(ids)]
                        self.difficulty = DIFFICULTIES[GAME_SETTINGS.difficulty]
                        changed = True
                    elif event.key == pygame.K_7:
                        GAME_SETTINGS.audio.music = max(0, GAME_SETTINGS.audio.music - 10); changed = True
                    elif event.key == pygame.K_8:
                        GAME_SETTINGS.audio.music = min(100, GAME_SETTINGS.audio.music + 10); changed = True
                    elif event.key == pygame.K_9:
                        GAME_SETTINGS.audio.sfx = 0 if GAME_SETTINGS.audio.sfx > 0 else 85; changed = True
                    if changed:
                        SETTINGS_MANAGER.save(GAME_SETTINGS)
                        self.audio.refresh_volumes()
                        self.audio.play("ui_confirm")
                        self.meta_message = "Configuração salva."
                    return
                return

            if not self.offer_mode and not self.game_over and not self.inventory_open:
                if event.key == binding_key("codex", pygame.K_c):
                    self.meta_panel = "codex"; self.audio.play("ui_confirm"); return
                if event.key == pygame.K_j:
                    self.meta_panel = "quests"; self.audio.play("ui_confirm"); return
                if event.key == binding_key("map", pygame.K_m):
                    self.meta_panel = "map"; self.audio.play("ui_confirm"); return
                if event.key == pygame.K_h:
                    self.enter_hub_room(); self.trigger_experience("first_hub_return"); self.audio.play("ui_confirm"); return
                if event.key == pygame.K_o:
                    self.meta_panel = "settings"; self.audio.play("ui_confirm"); return
                if event.key == pygame.K_l:
                    self.meta_panel = "ranking"; LEADERBOARD.online.refresh_async(); self.audio.play("ui_confirm"); return
                if event.key == pygame.K_k and any(int(v) > 0 for v in self.run_supplies.values()):
                    self.meta_panel = "supplies"; self.audio.play("ui_confirm"); return

            if event.key == binding_key("inventory", pygame.K_i) and not self.offer_mode and not self.game_over:
                self.inventory_open = not self.inventory_open
                self.inventory_message = ""
                return

            if self.inventory_open:
                if event.key == pygame.K_ESCAPE:
                    self.inventory_open = False
                    return
                if event.key in (pygame.K_PAGEUP, pygame.K_LEFTBRACKET):
                    self.inventory_page = max(0, self.inventory_page - 1); return
                if event.key in (pygame.K_PAGEDOWN, pygame.K_RIGHTBRACKET):
                    max_page = max(0, (len(inventory_equipment_ids(self.player_state)) - 1) // 9)
                    self.inventory_page = min(max_page, self.inventory_page + 1); return
                number_keys = (
                    pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4, pygame.K_5,
                    pygame.K_6, pygame.K_7, pygame.K_8, pygame.K_9,
                )
                if event.key in number_keys:
                    index = self.inventory_page * 9 + number_keys.index(event.key)
                    self.equip_inventory_index(index)
                return

            if event.key == binding_key("pause", pygame.K_ESCAPE):
                if self.offer_mode:
                    return
                self.paused = True
                self.pause_selection = 0
                self.abandon_confirm = False
                self.audio.play("ui_confirm")
                return

            if self.game_over:
                if event.key==pygame.K_r: self.restart_run()
                elif event.key==pygame.K_l:
                    self.meta_panel="ranking"; self.ranking_daily_view=bool(self.last_run_result and str(self.last_run_result.trial_id).startswith("daily:"))
                    if self.ranking_daily_view and self.last_run_result:
                        key=self.last_run_result.trial_id.split(":",2)[1]; LEADERBOARD.refresh_daily_online(key)
                    else: LEADERBOARD.online.refresh_async()
                return

            if self.offer_mode == "skill":
                if event.key in (pygame.K_1, pygame.K_2, pygame.K_3):
                    self.choose_skill_offer(event.key - pygame.K_1)
                return

            if self.offer_mode == "rift":
                if event.key in (pygame.K_1, pygame.K_2, pygame.K_3):
                    self.choose_rift_offer(event.key - pygame.K_1)
                return

            if self.offer_mode == "route":
                if event.key in (pygame.K_1, pygame.K_2, pygame.K_3):
                    self.choose_route_offer(event.key - pygame.K_1)
                return

            if self.offer_mode == "ending":
                ending_keys = (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4, pygame.K_5)
                if event.key in ending_keys:
                    self.choose_ending_offer(ending_keys.index(event.key))
                return

            if self.paused:
                return

            basic_mouse = binding_mouse_button("basic_attack")
            secondary_mouse = binding_mouse_button("secondary")
            if basic_mouse is None and event.key == binding_key("basic_attack", pygame.K_UNKNOWN):
                self.player.request_basic_attack(self)
            elif secondary_mouse is None and event.key == binding_key("secondary", pygame.K_UNKNOWN): self.player.request_secondary(self)
            elif event.key == binding_key("dash", pygame.K_SPACE): self.player.request_dash(self)
            elif event.key == binding_key("defense", pygame.K_q) or event.key in (pygame.K_LSHIFT, pygame.K_RSHIFT):
                self.player.start_parry(self)
            elif event.key in (pygame.K_q, pygame.K_e, pygame.K_r, pygame.K_f):
                skill_keys = (pygame.K_q, pygame.K_e, pygame.K_r, pygame.K_f)
                self.player.use_skill_slot(skill_keys.index(event.key), self)
            else:
                skill_bindings = [
                    binding_key("skill_1", pygame.K_1), binding_key("skill_2", pygame.K_2),
                    binding_key("skill_3", pygame.K_3), binding_key("skill_4", pygame.K_4),
                ]
                if event.key in skill_bindings:
                    self.player.use_skill_slot(skill_bindings.index(event.key), self)

        if event.type == pygame.MOUSEBUTTONDOWN:
            if self.paused:
                if event.button == 1:
                    for idx, rect in enumerate(self.pause_option_rects()):
                        if rect.collidepoint(event.pos):
                            self.pause_selection = idx
                            self.activate_pause_option(idx)
                            return
                return
            if self.game_over or self.offer_mode or self.inventory_open or self.meta_panel:
                return
            if event.button == binding_mouse_button("basic_attack"): self.player.request_basic_attack(self)
            elif event.button == binding_mouse_button("secondary"): self.player.request_secondary(self)

    # --------------------------------------------------------
    # UPDATE FIXO
    # --------------------------------------------------------

    def enter_hub_room(self):
        self.meta_panel = None
        self.hub_room_open = True
        self.hub_menu_from_room = False
        self.hub_dialogue_npc_id = None
        self.hub_return_position = self.player.pos.copy()
        self.player.pos.update(WIDTH / 2, HEIGHT / 2 + 35)
        self.player.prev_pos = self.player.pos.copy()
        self.player.velocity.update(0, 0)
        self.player.visual_move.update(0, 0)
        self.player.visual_state = "idle"
        self.meta_message = "O salão está seguro. Aproxime-se de alguém e pressione E para conversar."
        self.autosave.request(AutosaveReason.REFUGIO_ENTRADA)

    def leave_hub_room(self):
        self.hub_room_open = False
        self.player.velocity.update(0, 0)
        if self.hub_return_position is not None:
            self.player.pos = self.hub_return_position
            self.player.prev_pos = self.player.pos.copy()
            self.hub_return_position = None
        self.autosave.request(AutosaveReason.REFUGIO_SAIDA)
        if self.awaiting_expedition:
            self.meta_panel = "hub"
            self.hub_tab = "expedition"
            self.hub_menu_from_room = False
        else:
            self.meta_panel = None

    def interact_hub_npc(self, npc_id: str) -> bool:
        if npc_id not in self.hub_npc_positions or npc_id not in NPCS:
            return False
        npc = NPCS[npc_id]
        if self.player.pos.distance_to(self.hub_npc_positions[npc_id]) > 104:
            self.meta_message = f"Chegue mais perto de {npc.nome} para conversar."
            self.hub_dialogue_npc_id = None
            return False
        dialogue = select_npc_dialogue(self.player_state, npc_id)
        self.hub_dialogue_npc_id = npc_id
        if dialogue:
            self.meta_message = f"{npc.nome}: {dialogue.text}"
            self.process_campaign_rewards()
            self.autosave.request(AutosaveReason.EVENTO_HISTORIA)
        else:
            self.meta_message = f"{npc.nome} não tem nada novo agora."
        self.audio.play("ui_confirm")
        return True

    def handle_hub_room_event(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_ESCAPE, pygame.K_h):
                self.leave_hub_room()
                self.audio.play("ui_back")
            elif event.key == pygame.K_TAB:
                self.hub_room_open = False
                self.hub_menu_from_room = True
                self.meta_panel = "hub"
                self.hub_tab = "main"
                self.hub_page = 0
                self.audio.play("ui_confirm")
            elif event.key == pygame.K_e:
                nearest = min(
                    self.hub_npc_positions,
                    key=lambda npc_id: self.player.pos.distance_to(self.hub_npc_positions[npc_id]),
                )
                self.interact_hub_npc(nearest)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            clicked = [
                npc_id
                for npc_id, position in self.hub_npc_positions.items()
                if position.distance_to(event.pos) <= 62
            ]
            if clicked:
                self.interact_hub_npc(clicked[0])

    def update_hub_room(self, dt: float):
        keys = pygame.key.get_pressed()
        movement = pygame.Vector2(
            float(keys[pygame.K_d] or keys[pygame.K_RIGHT])
            - float(keys[pygame.K_a] or keys[pygame.K_LEFT]),
            float(keys[pygame.K_s] or keys[pygame.K_DOWN])
            - float(keys[pygame.K_w] or keys[pygame.K_UP]),
        )
        if movement.length_squared() > 0:
            movement = movement.normalize()
            self.player.visual_move = movement
            self.player.visual_state = "walk"
            self.player.aim_dir = movement
        else:
            self.player.visual_move.update(0, 0)
            self.player.visual_state = "idle"
        self.player.visual_time += dt
        for axis in ("x", "y"):
            delta = getattr(movement, axis) * 220 * dt
            if not delta:
                continue
            candidate = self.player.pos.copy()
            setattr(candidate, axis, getattr(candidate, axis) + delta)
            candidate.x = clamp(candidate.x, ROOM.left + 34, ROOM.right - 34)
            candidate.y = clamp(candidate.y, ROOM.top + 34, ROOM.bottom - 34)
            if self._hub_position_is_walkable(candidate):
                self.player.pos = candidate
        self.player.prev_pos = self.player.pos.copy()
        self.autosave.update(self.player_state, safe_to_save=True)

    def _hub_position_is_walkable(self, position: pygame.Vector2) -> bool:
        inside_room = (
            ROOM.left + 34 <= position.x <= ROOM.right - 34
            and ROOM.top + 34 <= position.y <= ROOM.bottom - 34
        )
        return inside_room and all(
            position.distance_to(npc_pos) >= 58 for npc_pos in self.hub_npc_positions.values()
        )

    def fixed_update(self, dt: float):
        self.reaction_controller.update(dt)
        self.audio.update_game(
            dt,
            region_id="refugio_ultima_luz" if self.hub_room_open else self.campaign.current_region().id,
            enemies=self.enemies,
            ending=(self.offer_mode == "ending"),
        )

        if self.hub_room_open:
            self.update_hub_room(dt)
            return

        if self.speech:
            self.speech.update(dt)
            if self.speech.dead:
                self.speech = None

        if self.banner_timer > 0:
            self.banner_timer = max(0.0, self.banner_timer - dt)
        self._advance_banner_queue()
        if self.transition_alpha > 0:
            self.transition_alpha = max(0.0, self.transition_alpha - dt * 430.0)
        self.rift_guard_timer = max(0.0, self.rift_guard_timer - dt)
        self.tonic_timer = max(0.0, self.tonic_timer - dt)
        self.crimson_oil_timer = max(0.0, self.crimson_oil_timer - dt)
        self.echo_signal_timer=max(0.0,self.echo_signal_timer-dt)
        self.combo_timer=max(0.0,self.combo_timer-dt)
        if self.combo_timer<=0 and self.combo_count: self.break_combo()
        self.boss_cinematic_timer=max(0.0,self.boss_cinematic_timer-dt); self.boss_phase_timer=max(0.0,self.boss_phase_timer-dt); self.cinematic_flash=max(0.0,self.cinematic_flash-dt)
        if self.tonic_timer > 0:
            # Regeneração extra sem substituir a regeneração própria de cada classe.
            if self.player.class_id not in ("guerreiro", "rasgado"):
                self.player.resource = min(self.player.resource_max, self.player.resource + self.player.resource_max * 0.018 * dt)

        if self.shake_timer > 0:
            self.shake_timer -= dt
            if self.shake_timer <= 0:
                self.shake_power = 0

        for skill_id in list(self.skill_cooldowns.keys()):
            self.skill_cooldowns[skill_id] = max(0.0, self.skill_cooldowns[skill_id] - dt)

        for collection in (self.particles, self.floating_texts, self.slashes):
            for item in collection:
                item.update(dt)

        for ring in self.impact_rings:
            ring.update(dt)
        self.particles = [x for x in self.particles if not x.dead]
        self.floating_texts = [x for x in self.floating_texts if not x.dead]
        self.slashes = [x for x in self.slashes if not x.dead]
        self.impact_rings = [x for x in self.impact_rings if not x.dead]
        self.enforce_runtime_budgets()

        if self.boss_cinematic_timer>0 or self.boss_phase_timer>0:
            self.autosave.update(self.player_state,safe_to_save=True); return
        if not self.game_over and not self.paused: self.run_elapsed += dt

        if self.paused or self.game_over or self.offer_mode or self.inventory_open or self.meta_panel:
            self.autosave.update(self.player_state, safe_to_save=True)
            return

        if self.hit_stop > 0:
            self.hit_stop -= dt
            return

        self.telemetry.tempo_combate += dt
        self.player.update(dt,self)
        for obj in self.room_objects: obj.update(dt,self)
        self.room_objects=[obj for obj in self.room_objects if not obj.dead]

        nearest=self.nearest_enemy(self.player.pos)
        if nearest:
            self.telemetry.register_distance_sample(nearest.pos.distance_to(self.player.pos))

        # Segurar clique esquerdo.
        mouse_buttons = pygame.mouse.get_pressed()
        basic_button = binding_mouse_button("basic_attack")
        button_index = {1: 0, 2: 1, 3: 2}.get(basic_button or -1)
        if button_index is not None and mouse_buttons[button_index]: self.player.request_basic_attack(self)

        # Boss fala ao lembrar do jogador.
        if not self.boss_spawn_line_shown:
            bosses = [e for e in self.enemies if e.kind == "guardian" and not e.dead]
            if bosses:
                boss_enemy = bosses[0]
                boss_id = getattr(boss_enemy, "boss_id", None) or "guardiao_primeira_ruptura"
                memory = self.player_state.veil_memory.get(boss_id)
                line = memory.contextual_line(self.player_state.identity.nome)
                if line:
                    boss_name = BOSSES[boss_id].nome if boss_id in BOSSES else "Guardião"
                    self.speak_enemy(boss_name, line, ORANGE, priority=4, duration=2.8)
                self.boss_spawn_line_shown = True

        for enemy in self.enemies:
            enemy.update(dt, self)

        for projectile in self.projectiles:
            projectile.update(dt, self)
            self.resolve_projectile_collision(projectile)

        for effect in self.area_effects:
            # burst mágico especial é um efeito de detonação única.
            if effect.kind == "magic_burst":
                effect.delay -= dt
                if effect.delay <= 0 and not effect.triggered:
                    effect.triggered = True
                    for enemy in self.enemies:
                        if enemy.pos.distance_to(effect.pos) < effect.radius + enemy.radius:
                            enemy.take_damage(effect.damage, self, critical=True)
                    for _ in range(28):
                        self.particles.append(Particle.burst(effect.pos, CYAN, 100, 340, 0.5, 7))
                    self.add_shake(12, 0.18)
                    effect.dead = True
            else:
                effect.update(dt, self)

        for death_visual in self.enemy_deaths:
            death_visual.update(dt)
        self.enemy_deaths = [d for d in self.enemy_deaths if not d.dead]

        self.projectiles = [p for p in self.projectiles if not p.dead]
        self.area_effects = [e for e in self.area_effects if not e.dead]
        self.enemies = [e for e in self.enemies if not e.dead]
        self.enforce_runtime_budgets()

        self.resolve_heals()

        if self.player.hp <= 0 and not self.game_over:
            self.game_over = True
            self.register_death_memory()
            self.campaign.record(CampaignEvent.PLAYER_DIED)
            add_metric(self.player_state, "deaths", 1)
            self.campaign.append_run_history(
                result="death",
                rooms=self.rooms_cleared,
                region_id=self.campaign.current_region().id,
                boss_defeated="guardiao_primeira_ruptura" in self.player_state.progression.bosses_derrotados,
                style=self.telemetry.classify_style(),
                active_rifts=self.player_state.active_rifts.active_ids,
            )
            self.submit_run_result("death")
            try:
                finish_expedition(
                    self.player_state, survived=False, rooms=self.rooms_cleared,
                    bosses=self.run_bosses, active_rifts=len(self.player_state.active_rifts.active_ids),
                    gold_reward=max(10, self.score // 14), echo_reward=0,
                )
            except Exception:
                pass
            complete_generated_expedition(self.player_state)
            self.expedition_map = None
            self.autosave.save_now(self.player_state, AutosaveReason.CHECKPOINT)

        if not self.enemies and not self.game_over and self.offer_mode is None and not self.meta_panel:
            if self.boss_rush_active:
                self.rooms_cleared += 1
                add_metric(self.player_state, "rooms_cleared", 1)
                self._collect_meta_rewards()
                self.spawn_next_room()
            elif not self.awaiting_expedition:
                self.complete_current_room()

        self.autosave.update(self.player_state, safe_to_save=True)

    def resolve_projectile_collision(self, projectile: Projectile):
        if projectile.dead:
            return

        if projectile.owner=="player":
            if self.resolve_room_object_projectile(projectile) and projectile.dead: return
            for enemy in self.enemies:
                if enemy.dead or enemy.uid in projectile.hit_ids:
                    continue
                if circle_collision(projectile.pos, projectile.radius, enemy.pos, enemy.radius):
                    element, applied_statuses = elemental_payload_for_skill(projectile.skill_id)
                    enemy.take_damage(
                        projectile.damage,
                        self,
                        knockback=normalize(projectile.vel) * 10,
                        critical=False,
                        element=element,
                        applied_statuses=applied_statuses,
                    )
                    if projectile.skill_id in ("arrow", "burst_arrow", "ricochet"):
                        self.audio.play("arrow_hit")
                    elif element == Element.FOGO:
                        self.audio.play("fire_impact")
                    elif element == Element.GELO:
                        self.audio.play("ice_break")
                    elif element == Element.RAIO:
                        self.audio.play("lightning_impact")
                    elif element == Element.VAZIO:
                        self.audio.play("void_impact")
                    elif element in (Element.ARCANO, Element.TEMPORAL, Element.SOMBRA):
                        self.audio.play("arcane_impact")
                    projectile.hit_ids.add(enemy.uid)
                    if projectile.skill_id == "marca_do_duelo":
                        self.player.duel_mark_target_uid = enemy.uid
                        self.player.duel_mark_bonus = 0.08
                    elif projectile.skill_id == "maldicao_espelho":
                        self.player.mirror_curse_target_uid = enemy.uid

                    if projectile.bleed_damage > 0:
                        enemy.apply_bleed(projectile.bleed_damage)

                    # Tiro Ricochete: uma chance de gerar novo disparo para outro alvo.
                    if (
                        projectile.skill_id in ("arrow", "burst_arrow")
                        and "tiro_ricochete" in self.player_state.owned_skills
                        and random.random() < 0.35
                    ):
                        candidates = [
                            e for e in self.enemies
                            if not e.dead and e.uid != enemy.uid and e.uid not in projectile.hit_ids
                            and e.pos.distance_to(enemy.pos) < 260
                        ]
                        if candidates:
                            target = min(candidates, key=lambda e: e.pos.distance_squared_to(enemy.pos))
                            direction = normalize(target.pos - enemy.pos)
                            self.projectiles.append(
                                Projectile(
                                    enemy.pos.copy(),
                                    direction * 620,
                                    projectile.damage * 0.72,
                                    YELLOW,
                                    "player",
                                    radius=5,
                                    life=0.8,
                                    skill_id="ricochet",
                                )
                            )

                    if projectile.explosion_radius > 0:
                        projectile.explode(self)

                    if projectile.pierce > 0:
                        projectile.pierce -= 1
                    else:
                        projectile.dead = True
                        break

        else:
            if circle_collision(projectile.pos, projectile.radius, self.player.pos, self.player.radius):
                if self.player.parry_window > 0 and self.player.class_id in ("guerreiro", "duelista"):
                    self.player.parry_window = 0
                    self.telemetry.parries += 1
                    self.audio.play("parry")
                    projectile.owner = "player"
                    projectile.vel *= -1.32
                    projectile.damage *= 1.55
                    projectile.color = CYAN
                    self.add_shake(12, 0.17)
                    self.hit_stop = max(self.hit_stop, 0.055)
                    self.speak_player("DEVOLVIDO!", priority=3, cooldown_key="reflect")
                else:
                    self.player.take_damage(projectile.damage, self)
                    projectile.dead = True

    def resolve_heals(self):
        for heal in self.heals[:]:
            if self.player.pos.distance_to(heal) < 30:
                self.player.heal(28)
                self.audio.play("heal")
                self.telemetry.curas_usadas += 1
                self.heals.remove(heal)

    def restart_run(self):
        # Mantém progresso persistente e reinicia apenas a expedição.
        self.player_state.active_rifts = ActiveRiftState()
        self.player = PlayerAvatar(self.player_state)
        self.enemies.clear()
        self.projectiles.clear()
        self.area_effects.clear()
        self.heals.clear()
        self.particles.clear()
        self.floating_texts.clear()
        self.slashes.clear(); self.impact_rings.clear(); self.room_objects.clear()
        self.combo_count=0; self.combo_timer=0.0; self.combo_best=0; self.combo_damage=0.0
        self.wave = 0
        self.rooms_cleared = 0
        self.run_kills = 0
        self.run_bosses = 0
        self.run_superiors = 0
        self.run_elapsed = 0.0
        self.run_submitted = False
        self.run_nonce = f"{random.getrandbits(96):024x}"
        self.game_over = False
        self.offer_mode = None
        self.telemetry = CombatTelemetry()
        self.skill_cooldowns.clear()
        self.ensure_expedition_map(reset=True, region_id=self.campaign.current_region().id)
        self.spawn_next_room()

    # --------------------------------------------------------
    # DRAW
    # --------------------------------------------------------

    def draw_hub_room(self, surface):
        surface.fill((8, 11, 23))
        room = ROOM.inflate(18, 18)
        pygame.draw.rect(surface, (17, 22, 39), room, border_radius=18)
        pygame.draw.rect(surface, (89, 105, 140), room, 4, border_radius=18)
        pygame.draw.rect(surface, (30, 38, 61), ROOM, border_radius=12)
        pygame.draw.rect(surface, (103, 111, 139), ROOM, 2, border_radius=12)

        tile = 54
        for y in range(ROOM.top + 9, ROOM.bottom - tile, tile):
            for x in range(ROOM.left + 9, ROOM.right - tile, tile):
                parity = ((x - ROOM.left) // tile + (y - ROOM.top) // tile) % 2
                fill = (34, 42, 65) if parity else (30, 38, 60)
                rect = pygame.Rect(x, y, tile - 3, tile - 3)
                pygame.draw.rect(surface, fill, rect, border_radius=5)
                pygame.draw.line(surface, (51, 61, 86), (x + 5, y + 5), (x + 18, y + 5), 1)

        # Central seal and the gate at the far wall make this read as a place,
        # not another translucent menu over the combat arena.
        center = pygame.Vector2(WIDTH / 2, HEIGHT / 2 + 15)
        pygame.draw.circle(surface, (28, 43, 68), center, 112)
        pygame.draw.circle(surface, (68, 151, 184), center, 104, 2)
        pygame.draw.circle(surface, (69, 103, 143), center, 82, 1)
        pygame.draw.polygon(
            surface,
            (54, 82, 119),
            [center + pygame.Vector2(0, -68), center + pygame.Vector2(60, 0),
             center + pygame.Vector2(0, 68), center + pygame.Vector2(-60, 0)],
            2,
        )
        gate = pygame.Rect(WIDTH // 2 - 83, ROOM.top + 16, 166, 89)
        pygame.draw.rect(surface, (21, 31, 53), gate, border_radius=56)
        pygame.draw.arc(surface, (94, 204, 231), gate, math.pi, math.tau, 4)
        pygame.draw.line(surface, (49, 93, 132), (gate.left + 24, gate.bottom - 9), (gate.right - 24, gate.bottom - 9), 3)
        draw_text(surface, "REFÚGIO DA ÚLTIMA LUZ", (WIDTH // 2, ROOM.top - 23), CYAN, FONT_MEDIUM, True)

        nearest = min(
            self.hub_npc_positions,
            key=lambda npc_id: self.player.pos.distance_to(self.hub_npc_positions[npc_id]),
        )
        pulse = 0.5 + 0.5 * math.sin(pygame.time.get_ticks() / 360.0)
        for npc_index, (npc_id, position) in enumerate(self.hub_npc_positions.items()):
            npc = NPCS[npc_id]
            class_id, gender, accent, station = HUB_NPC_PRESENTATIONS[npc_id]
            selected = npc_id == nearest and self.player.pos.distance_to(position) <= 104
            glow_radius = 46 if selected else 35
            glow = pygame.Surface((glow_radius * 2 + 8, glow_radius * 2 + 8), pygame.SRCALPHA)
            pygame.draw.circle(glow, (*accent, 28 if selected else 15), (glow_radius + 4, glow_radius + 4), glow_radius)
            surface.blit(glow, (int(position.x - glow_radius - 4), int(position.y - glow_radius - 4)))
            pygame.draw.ellipse(surface, (8, 10, 17), (position.x - 34, position.y + 30, 68, 17))
            pygame.draw.circle(surface, accent, position, 39, 2 if selected else 1)

            animated_skin = get_player_animation_frame(
                BASE_DIR,
                class_id,
                gender,
                "idle",
                pygame.time.get_ticks() / 1000.0 + npc_index * 0.37,
            )
            skin = animated_skin or get_player_skin(class_id, gender)
            if skin is not None:
                sprite = cached_sprite_transform(skin, 64, 78, False, 0)
                tint = (192, 218, 255) if npc_id == "ilyra" else (255, 255, 255)
                draw_tinted(surface, sprite, (position.x - 32, position.y - 53), tint, 210 if npc_id == "ilyra" else 255)
            else:
                pygame.draw.circle(surface, accent, (int(position.x), int(position.y - 19)), 16)
                pygame.draw.rect(surface, accent, (position.x - 17, position.y - 5, 34, 42), border_radius=10)

            plaque = pygame.Rect(int(position.x - 116), int(position.y + 42), 232, 48)
            pygame.draw.rect(surface, (12, 17, 30), plaque, border_radius=7)
            pygame.draw.rect(surface, accent, plaque, 1, border_radius=7)
            draw_text(surface, npc.nome, (plaque.centerx, plaque.y + 4), accent, FONT_SMALL, True)
            draw_text(surface, f"{npc.papel} • {station}", (plaque.centerx, plaque.y + 27), GRAY, FONT_TINY, True)
            if selected:
                draw_text(surface, f"E / clique — conversar", (position.x, position.y - 70 - pulse * 3), WHITE, FONT_TINY, True)

        self.player.draw(surface, pygame.Vector2())
        draw_text(surface, "WASD mover • E / clique conversar • TAB serviços • H/ESC sair", (WIDTH // 2, 28), WHITE, FONT_SMALL, True)

        if self.meta_message:
            dialogue_box = pygame.Rect(ROOM.left + 22, HEIGHT - 112, WIDTH - 2 * (ROOM.left + 22), 70)
            pygame.draw.rect(surface, (12, 16, 30), dialogue_box, border_radius=9)
            pygame.draw.rect(surface, CYAN if self.hub_dialogue_npc_id else (80, 93, 120), dialogue_box, 2, border_radius=9)
            draw_wrapped_text(surface, self.meta_message, dialogue_box.inflate(-24, -12), WHITE, FONT_SMALL)

    def draw_world(self, surface, offset=None):
        if offset is None:
            offset = self.current_camera_offset
        region_id = self.campaign.current_region().id
        boss_active = any((enemy.boss_id is not None) or enemy.kind == "guardian" for enemy in self.enemies if not enemy.dead)
        rift_count = len(self.player_state.active_rifts.active_ids)
        now_s = pygame.time.get_ticks() / 1000.0

        # Cenário 0.9.3: cada região possui chão, bordas, props, partículas e
        # arena de boss próprios. A colisão continua separada da arte.
        self.environment.draw_base(
            surface, ROOM, region_id, self.wave, rift_count, boss_active, now_s, offset
        )

        mods = self.player_state.active_rifts.cumulative_modifiers()

        for heal in self.heals:
            p = heal + offset
            pygame.draw.circle(surface, GREEN, p, 11)
            pygame.draw.line(surface, WHITE, p + (-5, 0), p + (5, 0), 3)
            pygame.draw.line(surface, WHITE, p + (0, -5), p + (0, 5), 3)

        for obj in self.room_objects: obj.draw(surface,offset)
        for effect in self.area_effects: effect.draw(surface,offset)
        for projectile in self.projectiles:
            projectile.draw(surface, offset)
        for slash in self.slashes:
            slash.draw(surface, offset)
        for ring in self.impact_rings:
            ring.draw(surface, offset)
        for death_visual in self.enemy_deaths:
            death_visual.draw(surface, offset)
        for enemy in self.enemies:
            enemy.draw(surface, offset)
        self.player.draw(surface, offset)
        for particle in self.particles:
            particle.draw(surface, offset)
        for text in self.floating_texts:
            text.draw(surface, offset)

        self.environment.draw_foreground(surface, ROOM, region_id, now_s, offset, self.player.pos)

        # Névoa do Vazio por cima do mundo, abaixo da UI.
        visibility = soften_rift_penalty(mods["visibility_multiplier"], legacy_modifiers(self.player_state)["rift_penalty_mult"], harmful_when_above_one=False)
        if visibility < 0.95:
            alpha = int((1.0 - visibility) * 155)
            fog = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            fog.fill((6, 3, 12, alpha))
            pygame.draw.circle(fog, (0, 0, 0, 0), self.player.pos + offset, int(310 * visibility + 120))
            surface.blit(fog, (0, 0))

    def _draw_dashed_line(self, surface, color, start: pygame.Vector2, end: pygame.Vector2, dash_len: int = 10, gap_len: int = 7, width: int = 2):
        vec = end - start
        dist = vec.length()
        if dist <= 1:
            return
        direction = vec / dist
        cursor = 0.0
        while cursor < dist:
            seg_start = start + direction * cursor
            seg_end = start + direction * min(dist, cursor + dash_len)
            pygame.draw.line(surface, color, seg_start, seg_end, width)
            cursor += dash_len + gap_len

    def _preview_skill_profile(self) -> tuple[str, float, float, Tuple[int, int, int]]:
        player = self.player
        sid = player.pending_cast.skill_id if player.pending_cast else None
        melee_classes = {"guerreiro", "ceifador", "rasgado", "duelista", "guardiao_veu"}
        melee_skills = {"corte_crescente", "ruptura_da_lamina", "colheita_sombria", "sentenca_final", "contra_golpe", "danca_de_laminas"}
        area_skills = {"prisao_glacial", "singularidade_arcana", "chuva_de_flechas", "torreta_do_veu", "mina_de_fenda", "instante_imovel", "barreira_do_veu"}
        blink_skills = {"passo_rebobinado"}
        line_skills = {"bola_de_fogo", "horizonte_partido", "fenda_interior", "marca_do_pressagio", "destino_condenado", "muralha_de_aco", "marca_do_duelo", "maldicao_espelho"}
        if sid in blink_skills:
            return ("blink", 170.0, 22.0, CYAN)
        if sid in area_skills:
            radius = {
                "prisao_glacial": 82.0,
                "singularidade_arcana": 105.0,
                "chuva_de_flechas": 118.0,
                "torreta_do_veu": 48.0,
                "mina_de_fenda": 40.0,
                "instante_imovel": 125.0,
                "barreira_do_veu": 68.0,
            }.get(sid, 80.0)
            return ("area", 255.0, radius, PURPLE)
        if sid in melee_skills:
            return ("cone", 118.0, 44.0, ORANGE)
        if sid in line_skills:
            return ("line", 255.0, 12.0, CYAN)
        if player.class_id in melee_classes:
            return ("cone", 96.0, 36.0, ORANGE)
        return ("line", 240.0, 10.0, CYAN)

    def draw_combat_aim_overlay(self, surface):
        if self.paused or self.game_over or self.offer_mode:
            return
        player = self.player
        origin_world = player.pos
        target_world = pygame.Vector2(player.aim_target)
        origin = origin_world + self.current_camera_offset
        target = target_world + self.current_camera_offset
        guide_color = (88, 214, 255)

        # Linha-guia: mostra exatamente a direção do golpe/disparo.
        self._draw_dashed_line(surface, (50, 180, 235), origin, target, dash_len=9, gap_len=8, width=2)
        pygame.draw.circle(surface, (70, 210, 255), origin, max(18, player.radius + 3), 1)

        mode, reach, aux, accent = self._preview_skill_profile()
        aim = normalize(player.aim_dir)
        side = pygame.Vector2(-aim.y, aim.x)
        preview_center = origin_world + aim * reach
        preview_screen = preview_center + self.current_camera_offset

        overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        if mode == "cone":
            half_angle = math.radians(aux)
            left = aim.rotate_rad(-half_angle) * reach
            right = aim.rotate_rad(half_angle) * reach
            points = [origin, origin + left, origin + aim * (reach * 1.04), origin + right]
            pygame.draw.polygon(overlay, (*accent, 28), points)
            surface.blit(overlay, (0, 0))
            pygame.draw.polygon(surface, accent, points, 2)
            arc_rect = pygame.Rect(origin.x - reach, origin.y - reach, reach * 2, reach * 2)
            start_a = math.atan2(left.y, left.x)
            end_a = math.atan2(right.y, right.x)
            if end_a < start_a:
                start_a, end_a = end_a, start_a
            pygame.draw.arc(surface, accent, arc_rect, start_a, end_a, 2)
        elif mode == "area":
            pygame.draw.circle(overlay, (*accent, 25), (int(target.x), int(target.y)), int(aux))
            surface.blit(overlay, (0, 0))
            pygame.draw.circle(surface, accent, (int(target.x), int(target.y)), int(aux), 2)
            pygame.draw.circle(surface, WHITE, (int(target.x), int(target.y)), 7, 2)
        elif mode == "blink":
            pygame.draw.circle(surface, CYAN, (int(preview_screen.x), int(preview_screen.y)), int(aux), 2)
            pygame.draw.circle(surface, WHITE, (int(preview_screen.x), int(preview_screen.y)), max(6, int(aux * 0.35)), 1)
            self._draw_dashed_line(surface, CYAN, origin, preview_screen, dash_len=7, gap_len=5, width=2)
        else:  # line
            line_end = origin_world + aim * reach + self.current_camera_offset
            self._draw_dashed_line(surface, accent, origin, line_end, dash_len=12, gap_len=6, width=2)
            pygame.draw.circle(surface, accent, (int(line_end.x), int(line_end.y)), int(max(7, aux)), 2)
            for i in range(3):
                q = origin + aim * (70 + i * 60)
                pygame.draw.circle(surface, accent, (int(q.x), int(q.y)), max(2, 4 - i), 1)

        # Crosshair pulsante.
        t = pygame.time.get_ticks() * 0.008
        pulse = 1.0 + 0.18 * math.sin(t)
        r = int(10 * pulse)
        pygame.draw.circle(surface, guide_color, (int(target.x), int(target.y)), r, 2)
        pygame.draw.circle(surface, WHITE, (int(target.x), int(target.y)), 3)
        pygame.draw.line(surface, guide_color, (int(target.x) - 16, int(target.y)), (int(target.x) - 6, int(target.y)), 2)
        pygame.draw.line(surface, guide_color, (int(target.x) + 6, int(target.y)), (int(target.x) + 16, int(target.y)), 2)
        pygame.draw.line(surface, guide_color, (int(target.x), int(target.y) - 16), (int(target.x), int(target.y) - 6), 2)
        pygame.draw.line(surface, guide_color, (int(target.x), int(target.y) + 6), (int(target.x), int(target.y) + 16), 2)

        # Destaca o inimigo mais próximo da linha de mira para leitura imediata.
        nearest = None
        nearest_score = 999999.0
        for enemy in self.enemies:
            if enemy.dead:
                continue
            score = (enemy.pos - target_world).length_squared()
            if score < nearest_score and score <= (max(90.0, enemy.radius + 46.0) ** 2):
                nearest = enemy
                nearest_score = score
        if nearest is not None:
            ep = nearest.pos + self.current_camera_offset
            halo = int(nearest.radius + 14 + abs(math.sin(t * 0.8)) * 4)
            pygame.draw.circle(surface, (255, 120, 90), (int(ep.x), int(ep.y)), halo, 2)
            pygame.draw.circle(surface, WHITE, (int(ep.x), int(ep.y)), max(5, int(nearest.radius * 0.25)), 1)

    def draw_hud(self, surface):
        state = self.player_state
        p = self.player
        class_def = state.class_definition()

        top = pygame.Rect(0, 0, WIDTH, 62)
        pygame.draw.rect(surface, (8, 10, 17), top)
        pygame.draw.line(surface, PURPLE, (18, 60), (WIDTH - 18, 60), 2)
        pygame.draw.line(surface, (50, 55, 78), (18, 61), (WIDTH - 18, 61), 1)

        pygame.draw.polygon(surface, PURPLE, [(22, 11), (29, 20), (22, 29), (15, 20)], 2)
        draw_text(surface, f"RUPTURA {self.wave}", (42, 14), PURPLE, FONT_MEDIUM)
        draw_text(surface, class_def.nome, (210, 20), p.color, FONT)
        draw_text(surface, f"Nv. {state.stats.nivel}", (380, 20), WHITE, FONT)
        draw_text(surface, f"Salas: {self.rooms_cleared}", (475, 20), GRAY, FONT_SMALL)
        draw_text(surface, chapter_title(self.campaign.chapter), (600, 20), DARK_GRAY, FONT_TINY)

        live_bosses = [e for e in self.enemies if e.kind == "guardian" and not e.dead]
        memory_boss_id = (getattr(live_bosses[0], "boss_id", None) if live_bosses else None) or self.next_story_boss_id()
        memory = state.veil_memory.get(memory_boss_id)
        memory_name = BOSSES[memory_boss_id].nome if memory_boss_id in BOSSES else "Boss"
        draw_text(
            surface,
            f"Memória — {memory_name}: {memory.rank().value}",
            (WIDTH - 390, 20),
            ORANGE if memory.rank() != MemoryRank.DESCONHECIDO else GRAY,
            FONT_TINY,
        )

        if self.combo_count>=3:
            grade=self.combo_grade(); combo_color=YELLOW if grade in ("S","SS","SSS") else CYAN if grade in ("A","B") else WHITE
            draw_text(surface,f"{grade}  {self.combo_count} HIT COMBO",(WIDTH-255,48),combo_color,FONT_SMALL,True)
        if self.daily_challenge is not None: draw_text(surface,f"DIÁRIO • {self.daily_challenge.title}",(WIDTH//2,49),PURPLE,FONT_TINY,True)

        gear_ids = [x for x in (state.equipment.arma, state.equipment.armadura) if x]
        gear_label = " | ".join(EQUIPMENT_REGISTRY[x].nome for x in gear_ids if x in EQUIPMENT_REGISTRY)
        draw_text(surface, f"I: Equipamentos{(' | ' + gear_label) if gear_label else ''}", (24, 50), GRAY, FONT_TINY)

        hud_panel = pygame.Rect(14, HEIGHT - 82, WIDTH - 28, 70)
        pygame.draw.rect(surface, (7, 9, 16), hud_panel, border_radius=12)
        pygame.draw.rect(surface, (47, 53, 74), hud_panel, 1, border_radius=12)
        pygame.draw.line(surface, PURPLE, (hud_panel.x + 14, hud_panel.y + 2), (hud_panel.x + 145, hud_panel.y + 2), 2)

        # Vida / recurso / XP
        draw_bar(surface, pygame.Rect(24, HEIGHT - 44, 270, 16), p.hp, p.max_hp, RED)
        draw_text(surface, f"HP {int(p.hp)}/{p.max_hp}", (30, HEIGHT - 65), WHITE, FONT_TINY)

        resource_color = ORANGE if p.class_id == "guerreiro" else (RED if p.class_id == "rasgado" else CYAN)
        draw_bar(
            surface,
            pygame.Rect(315, HEIGHT - 44, 235, 16),
            p.resource,
            p.resource_max,
            resource_color,
        )
        draw_text(
            surface,
            f"{class_def.recurso_nome}: {int(p.resource)}",
            (321, HEIGHT - 65),
            WHITE,
            FONT_TINY,
        )

        draw_bar(
            surface,
            pygame.Rect(570, HEIGHT - 44, 230, 16),
            state.stats.xp,
            self.xp_needed,
            PURPLE,
        )
        draw_text(
            surface,
            f"XP {state.stats.xp}/{self.xp_needed}",
            (576, HEIGHT - 65),
            WHITE,
            FONT_TINY,
        )

        # Skills
        start_x = 820
        for i in range(4):
            rect = pygame.Rect(start_x + i * 105, HEIGHT - 67, 95, 48)
            pygame.draw.rect(surface, PANEL, rect, border_radius=7)
            if i < len(p.active_slots):
                skill_id = p.active_slots[i]
                skill = SKILL_REGISTRY[skill_id]
                color = RARITY_COLORS[skill.raridade]
                pygame.draw.rect(surface, color, rect, 2, border_radius=7)
                skill_key = ("Q", "E", "R", "F")[i]
                draw_text(surface, f"{skill_key}/{i + 1}", (rect.x + 7, rect.y + 5), color, FONT_TINY)
                draw_wrapped_text(
                    surface,
                    skill.nome,
                    pygame.Rect(rect.x + 22, rect.y + 4, rect.width - 26, 29),
                    WHITE,
                    FONT_TINY,
                    0,
                )
                cd = self.skill_cooldowns.get(skill_id, 0.0)
                if cd > 0:
                    overlay = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
                    overlay.fill((0, 0, 0, 135))
                    surface.blit(overlay, rect.topleft)
                    draw_text(surface, f"{cd:.1f}", rect.center, WHITE, FONT_SMALL, True)
            else:
                pygame.draw.rect(surface, DARK_GRAY, rect, 1, border_radius=7)
                draw_text(surface, ("Q", "E", "R", "F")[i], rect.center, DARK_GRAY, FONT_SMALL, True)

        if p.pending_cast:
            skill = SKILL_REGISTRY[p.pending_cast.skill_id]
            draw_text(
                surface,
                f"Conjurando: {skill.nome}",
                (WIDTH // 2, 82),
                RARITY_COLORS[skill.raridade],
                FONT_SMALL,
                True,
            )

        if self.debug:
            fps = clock.get_fps()
            diag = self.diagnostics.summary()
            lines = [
                (f"FPS render: {fps:.1f} | frame médio: {diag['avg_frame_ms']:.2f} ms | pior: {diag['worst_frame_ms']:.2f} ms", GREEN),
                (f"Lógica: {LOGIC_UPS} Hz | updates/frame: {self.logic_updates_last_frame}", GREEN),
                (f"Entidades E/P/VFX: {len(self.enemies)}/{len(self.projectiles)}/{len(self.particles)} | áreas {len(self.area_effects)}", CYAN),
                (f"Região: {self.campaign.current_region().id} | wave {self.wave} | salas {self.rooms_cleared}", CYAN),
                (f"Estilo: {self.telemetry.classify_style()} | Rupturas: {len(self.player_state.active_rifts.active_ids)}", PURPLE),
                (f"Cache sprites: {len(SPRITE_TRANSFORM_CACHE)}/{SPRITE_TRANSFORM_CACHE_LIMIT} | drops VFX: {sum(diag['dropped_effects'].values())}", YELLOW),
            ]
            for i, (text_line, color_line) in enumerate(lines):
                draw_text(surface, text_line, (12, 72 + i * 17), color_line, FONT_TINY)

    def draw_offer(self, surface):
        if self.offer_mode is None:
            return

        overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 210))
        surface.blit(overlay, (0, 0))

        if self.offer_mode == "skill":
            draw_text(surface, "ESCOLHA UMA HABILIDADE", (WIDTH // 2, 95), WHITE, FONT_BIG, True)
            draw_text(
                surface,
                "Raridade muda poder e, em alguns casos, a própria mecânica.",
                (WIDTH // 2, 145),
                GRAY,
                FONT_SMALL,
                True,
            )

            cards = self.offer_card_rects(len(self.skill_offers))
            for i, (skill, rect) in enumerate(zip(self.skill_offers, cards)):
                color = RARITY_COLORS[skill.raridade]
                pygame.draw.rect(surface, PANEL, rect, border_radius=12)
                pygame.draw.rect(surface, color, rect, 3, border_radius=12)
                draw_text(surface, f"{i + 1}", (rect.x + 16, rect.y + 14), color, FONT_MEDIUM)
                draw_text(surface, skill.raridade.value.upper(), (rect.centerx, rect.y + 24), color, FONT_SMALL, True)
                draw_text(surface, skill.nome, (rect.centerx, rect.y + 62), WHITE, FONT_MEDIUM, True)
                draw_wrapped_text(
                    surface,
                    skill.descricao,
                    pygame.Rect(rect.x + 22, rect.y + 105, rect.width - 44, 95),
                    WHITE,
                    FONT_SMALL,
                )
                draw_text(
                    surface,
                    f"Tipo: {skill.categoria.value}",
                    (rect.x + 22, rect.bottom - 60),
                    GRAY,
                    FONT_TINY,
                )
                if skill.corrupted:
                    draw_text(surface, "PODER COM PREÇO", (rect.centerx, rect.bottom - 30), RED, FONT_SMALL, True)
                elif skill.allowed_classes:
                    draw_text(surface, "EXCLUSIVA / RESTRITA", (rect.centerx, rect.bottom - 30), color, FONT_TINY, True)

        elif self.offer_mode == "rift":
            draw_text(surface, "A RUPTURA EXIGE UMA ESCOLHA", (WIDTH // 2, 95), PURPLE, FONT_BIG, True)
            draw_text(surface, "Mais risco. Mais poder.", (WIDTH // 2, 145), GRAY, FONT, True)

            cards = self.offer_card_rects(len(self.rift_offers))
            for i, (rift_id, rect) in enumerate(zip(self.rift_offers, cards)):
                rift = RIFT_REGISTRY[rift_id]
                pygame.draw.rect(surface, PANEL, rect, border_radius=12)
                pygame.draw.rect(surface, PURPLE, rect, 3, border_radius=12)
                draw_text(surface, f"{i + 1}", (rect.x + 16, rect.y + 14), PURPLE, FONT_MEDIUM)
                draw_text(surface, rift.nome, (rect.centerx, rect.y + 58), PURPLE, FONT_MEDIUM, True)
                draw_wrapped_text(
                    surface,
                    rift.descricao,
                    pygame.Rect(rect.x + 22, rect.y + 105, rect.width - 44, 90),
                    WHITE,
                    FONT_SMALL,
                )
                draw_text(
                    surface,
                    f"Perigo: {'◆' * rift.danger}",
                    (rect.centerx, rect.bottom - 70),
                    ORANGE,
                    FONT_SMALL,
                    True,
                )
                draw_text(
                    surface,
                    f"Recompensas x{rift.reward_multiplier:.2f}",
                    (rect.centerx, rect.bottom - 38),
                    GREEN,
                    FONT_SMALL,
                    True,
                )

        elif self.offer_mode == "route":
            draw_text(surface, "ESCOLHA A PRÓXIMA ROTA", (WIDTH // 2, 95), CYAN, FONT_BIG, True)
            draw_text(surface, f"Seed {self.expedition_map.seed if self.expedition_map else 0} — decisões mudam a expedição.",
                      (WIDTH // 2, 145), GRAY, FONT_SMALL, True)
            cards = self.offer_card_rects(len(self.route_offers))
            room_names = {
                RoomType.COMBAT: "COMBATE", RoomType.ELITE: "ELITE", RoomType.EVENT: "EVENTO",
                RoomType.SHRINE: "SANTUÁRIO", RoomType.MERCHANT: "MERCADOR", RoomType.REST: "DESCANSO",
                RoomType.RIFT: "RUPTURA", RoomType.TREASURE: "TESOURO", RoomType.BOSS: "BOSS", RoomType.SECRET: "???",
            }
            for i, (node_id, rect) in enumerate(zip(self.route_offers, cards)):
                node = self.expedition_map.nodes[node_id]
                accent = ORANGE if node.room_type in (RoomType.ELITE, RoomType.BOSS) else PURPLE if node.hidden else CYAN
                pygame.draw.rect(surface, PANEL, rect, border_radius=12)
                pygame.draw.rect(surface, accent, rect, 3, border_radius=12)
                draw_text(surface, f"{i + 1}", (rect.x + 16, rect.y + 14), accent, FONT_MEDIUM)
                draw_text(surface, room_names.get(node.room_type, node.room_type.value.upper()), (rect.centerx, rect.y + 58), accent, FONT_MEDIUM, True)
                draw_text(surface, f"Profundidade {node.depth}", (rect.centerx, rect.y + 105), WHITE, FONT_SMALL, True)
                draw_text(surface, f"Ameaça {node.threat}", (rect.centerx, rect.y + 145), ORANGE, FONT_SMALL, True)
                draw_text(surface, f"Recompensa x{node.reward:.2f}", (rect.centerx, rect.y + 180), GREEN, FONT_SMALL, True)
                if node.hidden:
                    draw_wrapped_text(surface, "Uma rota escondida apareceu no mapa. Pode recompensar exploração — ou cobrar caro por ela.",
                                      pygame.Rect(rect.x + 25, rect.y + 225, rect.width - 50, 100), GRAY, FONT_SMALL)

        elif self.offer_mode == "ending":
            draw_text(surface, "O QUE SERÁ DO VÉU?", (WIDTH // 2, 82), CYAN, FONT_BIG, True)
            draw_text(surface, "Sua campanha, reputação e corrupção determinaram estas possibilidades.",
                      (WIDTH // 2, 132), GRAY, FONT_SMALL, True)
            cards = self.offer_card_rects(len(self.ending_offers))
            for i, (ending, rect) in enumerate(zip(self.ending_offers, cards)):
                accent = RED if ending.tone == "corrupted" else CYAN if ending.tone == "mysterious" else PURPLE
                pygame.draw.rect(surface, PANEL, rect, border_radius=12)
                pygame.draw.rect(surface, accent, rect, 3, border_radius=12)
                draw_text(surface, f"{i + 1}", (rect.x + 16, rect.y + 14), accent, FONT_MEDIUM)
                draw_text(surface, ending.nome, (rect.centerx, rect.y + 58), accent, FONT_MEDIUM, True)
                draw_wrapped_text(surface, ending.descricao,
                                  pygame.Rect(rect.x + 22, rect.y + 105, rect.width - 44, 90),
                                  WHITE, FONT_SMALL)
                draw_text(surface, "FINAL", (rect.centerx, rect.bottom - 38), accent, FONT_SMALL, True)

    @staticmethod
    def offer_card_rects(count: int):
        count = max(1, int(count))
        gap = 18 if count >= 4 else 28
        available = WIDTH - 80
        width = min(330, max(205, (available - gap * (count - 1)) // count))
        height = 390
        total = count * width + (count - 1) * gap
        start = WIDTH // 2 - total // 2
        return [pygame.Rect(start + i * (width + gap), 205, width, height) for i in range(count)]

    def draw_inventory(self, surface):
        if not self.inventory_open:
            return

        overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 215))
        surface.blit(overlay, (0, 0))

        draw_text(surface, "EQUIPAMENTOS", (WIDTH // 2, 62), WHITE, FONT_BIG, True)
        draw_text(surface, "1–9 equipa | PgUp/PgDn páginas | I ou ESC fecha", (WIDTH // 2, 108), GRAY, FONT_SMALL, True)

        eq = self.player_state.equipment
        equipped = [
            ("Arma", eq.arma),
            ("Armadura", eq.armadura),
            ("Acessório 1", eq.acessorio_1),
            ("Acessório 2", eq.acessorio_2),
        ]

        y = 145
        for label, item_id in equipped:
            name = EQUIPMENT_REGISTRY[item_id].nome if item_id in EQUIPMENT_REGISTRY else "—"
            draw_text(surface, f"{label}: {name}", (80, y), CYAN if item_id else GRAY, FONT_SMALL)
            y += 28

        bonuses = active_set_bonuses(self.player_state)
        if bonuses:
            draw_text(surface, "SINERGIAS ATIVAS", (80, y + 10), YELLOW, FONT_SMALL)
            y += 38
            for item_set, bonus in bonuses[:4]:
                draw_text(surface, f"{item_set.nome} ({bonus.pieces}) — {bonus.nome}", (95, y), GREEN, FONT_TINY)
                y += 22

        ids = inventory_equipment_ids(self.player_state)
        start_x = 520
        draw_text(surface, f"INVENTÁRIO ({len(ids)})", (start_x, 145), WHITE, FONT_MEDIUM)

        page_ids = ids[self.inventory_page * 9:self.inventory_page * 9 + 9]
        for i, item_id in enumerate(page_ids):
            item = EQUIPMENT_REGISTRY[item_id]
            color = RARITY_COLORS[item.raridade]
            rect = pygame.Rect(start_x, 195 + i * 48, 675, 40)
            pygame.draw.rect(surface, PANEL, rect, border_radius=6)
            pygame.draw.rect(surface, color, rect, 2, border_radius=6)
            draw_text(surface, f"{i + 1}. {item.nome}", (rect.x + 12, rect.y + 8), color, FONT_SMALL)
            draw_text(surface, item.slot.value.upper(), (rect.right - 135, rect.y + 10), GRAY, FONT_TINY)

        if len(ids) > 9:
            pages = max(1, (len(ids) + 8) // 9)
            draw_text(surface, f"Página {self.inventory_page + 1}/{pages} • {len(ids)} itens", (start_x, 640), GRAY, FONT_TINY)

        if self.inventory_message:
            draw_text(surface, self.inventory_message, (WIDTH // 2, HEIGHT - 42), YELLOW, FONT_SMALL, True)

    def draw_meta_panel(self, surface):
        if self.meta_panel is None:
            return
        overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 222))
        surface.blit(overlay, (0, 0))
        box = pygame.Rect(55, 52, WIDTH - 110, HEIGHT - 104)
        pygame.draw.rect(surface, BG_2, box, border_radius=12)
        pygame.draw.rect(surface, PURPLE, box, 2, border_radius=12)

        if self.meta_panel == "codex":
            draw_text(surface, "CÓDICE / BESTIÁRIO", (85, 76), CYAN, FONT_BIG)
            draw_text(surface, "C ou ESC fecha", (WIDTH - 240, 88), GRAY, FONT_TINY)
            tutorial_root = self.player_state.campaign.get("tutorial", {})
            unlocked = set(tutorial_root.get("codex_unlocked", []))
            y = 145
            for entry in CODEX.values():
                color = WHITE if entry.id in unlocked else DARK_GRAY
                label = entry.title if entry.id in unlocked else "???"
                draw_text(surface, f"[{entry.category}] {label}", (90, y), color, FONT_SMALL)
                if entry.id in unlocked:
                    draw_wrapped_text(surface, entry.text, pygame.Rect(310, y - 2, 430, 38), GRAY, FONT_TINY)
                y += 46
                if y > HEIGHT - 110:
                    break
            bx = 785
            archive = ensure_archive_state(self.player_state)
            if self.codex_research_mode:
                draw_text(surface, "PESQUISA DO ARQUIVO", (bx, 145), YELLOW, FONT_MEDIUM)
                draw_text(surface, f"Pontos: {archive.get('research_points',0)} • R volta ao Bestiário", (bx, 180), CYAN, FONT_TINY)
                by = 220
                completed = set(archive.get("completed_research", []))
                for i,(pid, project) in enumerate(RESEARCH_PROJECTS.items(), 1):
                    color = GREEN if pid in completed else WHITE
                    draw_text(surface, f"{i}. {project['name']} — {project['cost']} pts", (bx, by), color, FONT_TINY)
                    draw_wrapped_text(surface, project['description'], pygame.Rect(bx+15,by+20,390,34), GRAY, FONT_TINY)
                    by += 68
            else:
                summary = bestiary_completion(self.player_state)
                draw_text(surface, "CRIATURAS", (bx, 145), YELLOW, FONT_MEDIUM)
                draw_text(surface, f"{summary['discovered']}/{summary['total']} descobertas • {summary['percent']}% • R pesquisa", (bx, 180), CYAN, FONT_TINY)
                by = 215
                for creature_id, creature in list(CREATURES.items())[:10]:
                    view = bestiary_entry_view(self.player_state, creature_id)
                    rank = view.get("rank", "Desconhecido")
                    draw_text(surface, f"{view.get('nome','???')}: {rank}", (bx, by), WHITE if rank != "Desconhecido" else DARK_GRAY, FONT_TINY)
                    by += 28

        elif self.meta_panel == "quests":
            draw_text(surface, "MISSÕES", (85, 76), GREEN, FONT_BIG)
            draw_text(surface, f"Capítulo {self.campaign.chapter}: {chapter_title(self.campaign.chapter)}", (88, 125), PURPLE, FONT_SMALL)
            y = 175
            active = self.campaign.active_quests()
            if not active:
                draw_text(surface, "Nenhuma missão ativa.", (90, y), GRAY, FONT)
            for quest in active[:5]:
                draw_text(surface, quest.nome, (90, y), YELLOW, FONT_MEDIUM)
                y += 34
                for line in self.campaign.quest_progress_text(quest.id):
                    draw_text(surface, line, (112, y), WHITE, FONT_SMALL)
                    y += 26
                y += 18
            draw_text(surface, "J ou ESC fecha", (WIDTH - 240, 88), GRAY, FONT_TINY)

        elif self.meta_panel == "map":
            draw_text(surface, "MAPA DA EXPEDIÇÃO", (85, 76), CYAN, FONT_BIG)
            draw_text(surface, "M ou ESC fecha", (WIDTH-230, 88), GRAY, FONT_TINY)
            if self.expedition_map is None:
                draw_text(surface, "Nenhuma expedição ativa.", (100, 170), GRAY, FONT)
            else:
                draw_text(surface, f"Região: {REGIONS[self.expedition_map.region_id].nome} | Seed: {self.expedition_map.seed}", (95, 135), WHITE, FONT_SMALL)
                max_depth=max(n.depth for n in self.expedition_map.nodes.values())
                left=110; right=WIDTH-110; top=205; bottom=HEIGHT-120
                positions={}
                groups={}
                for node in self.expedition_map.nodes.values(): groups.setdefault(node.depth,[]).append(node)
                for depth,nodes in groups.items():
                    x=int(left+(right-left)*(depth/max(1,max_depth)))
                    for j,node in enumerate(sorted(nodes,key=lambda n:n.id)):
                        y=int((top+bottom)/2 + (j-(len(nodes)-1)/2)*82)
                        positions[node.id]=(x,y)
                for node_id,node in self.expedition_map.nodes.items():
                    a=positions[node_id]
                    for nxt in node.connections:
                        if nxt in positions: pygame.draw.line(surface,(58,65,85),a,positions[nxt],2)
                visited=set(self.expedition_map.visited)
                for node_id,node in self.expedition_map.nodes.items():
                    if node.hidden and node_id not in visited and not self._secret_revealed(node_id): continue
                    x,y=positions[node_id]
                    current=node_id==self.expedition_map.current_id
                    color=YELLOW if current else GREEN if node_id in visited else ORANGE if node.room_type==RoomType.BOSS else PURPLE if node.hidden else CYAN
                    pygame.draw.circle(surface,color,(x,y),15 if current else 11)
                    pygame.draw.circle(surface,WHITE,(x,y),15 if current else 11,2)
                    label="?" if node.hidden and node_id not in visited else node.room_type.value[:3].upper()
                    draw_text(surface,label,(x,y+27),color,FONT_TINY,True)

        elif self.meta_panel == "hub":
            draw_text(surface, "REFÚGIO DA ÚLTIMA LUZ", (85, 76), ORANGE, FONT_BIG)
            draw_text(surface, f"Ouro: {gold(self.player_state)}   Ecos: {echoes(self.player_state)}", (90, 126), YELLOW, FONT)
            return_hint = "TAB/H/ESC volta ao salão" if self.hub_menu_from_room else "BACKSPACE volta | H/ESC fecha"
            draw_text(surface, return_hint, (WIDTH - 360, 88), GRAY, FONT_TINY)

            if self.hub_tab == "main":
                draw_text(surface, "CENTRO DE OPERAÇÕES", (90, 170), WHITE, FONT_MEDIUM)
                options = [
                    ("1", "Instalações", "Evoluir Forja, Enfermaria, Arquivo, Cartografia e Estabilizador."),
                    ("2", "Crafting / Desmontagem", "Fabricar consumíveis/equipamentos e reciclar loot."),
                    ("3", "Vendedores", "Comprar estoque rotativo do Refúgio."),
                    ("4", "Facções", "Reputação e escolhas com consequências nos finais."),
                    ("5", "Provações", "Desafios opcionais com modificadores e Ecos."),
                    ("6", "Legado de Ecos", "Metaprogressão permanente entre expedições."),
                    ("7", "NPCs", "Conversar com Aris, Maelis, Nox e Ilyra."),
                    ("8", "Expedições", "Escolher região, contrato e gerar rota procedural por seed."),
                    ("9", "Pós-jogo", "NG+, Boss Rush e acesso ao ranking."),
                ]
                y = 215
                for key, title, desc in options:
                    rect = pygame.Rect(90, y, WIDTH - 180, 48)
                    pygame.draw.rect(surface, PANEL, rect, border_radius=7)
                    draw_text(surface, f"{key}. {title}", (112, y + 8), CYAN, FONT_SMALL)
                    draw_text(surface, desc, (330, y + 10), GRAY, FONT_TINY)
                    y += 54

            elif self.hub_tab == "facilities":
                draw_text(surface, "INSTALAÇÕES", (90, 170), CYAN, FONT_MEDIUM)
                y = 215
                for i, (facility_id, facility) in enumerate(FACILITIES.items(), start=1):
                    level = facility_level(self.player_state, facility_id)
                    current = facility.levels[level - 1]
                    next_text = "MÁXIMO" if level >= len(facility.levels) else f"Próx.: {facility.levels[level].gold_cost} ouro"
                    draw_text(surface, f"{i}. {facility.nome} — Nv.{level}", (90, y), CYAN, FONT_SMALL)
                    draw_text(surface, current.description, (115, y + 24), GRAY, FONT_TINY)
                    draw_text(surface, next_text, (720, y + 12), GREEN if next_text != "MÁXIMO" else YELLOW, FONT_TINY)
                    y += 76

            elif self.hub_tab in ("craft", "salvage"):
                title = "CRAFTING" if self.hub_tab == "craft" else "DESMONTAGEM"
                draw_text(surface, title, (90, 170), CYAN, FONT_MEDIUM)
                draw_text(surface, "PgUp/PgDn páginas" + (" | S desmontar loot" if self.hub_tab == "craft" else ""), (760, 176), GRAY, FONT_TINY)
                y = 220
                if self.hub_tab == "craft":
                    rows = list(RECIPES.values())[self.hub_page*9:self.hub_page*9+9]
                    for i, recipe in enumerate(rows, 1):
                        costs = ", ".join(f"{MATERIALS[mid].nome} x{amt}" for mid, amt in recipe.material_costs)
                        draw_text(surface, f"{i}. {recipe.nome} — {recipe.gold_cost} ouro", (95, y), WHITE, FONT_SMALL)
                        draw_text(surface, costs or "Sem materiais", (520, y+2), GRAY, FONT_TINY)
                        y += 48
                else:
                    items = [x for x in self.player_state.inventory if isinstance(x, dict) and x.get("instance_id")]
                    rows = items[self.hub_page*9:self.hub_page*9+9]
                    if not rows: draw_text(surface, "Nenhum equipamento desmontável.", (95, y), GRAY, FONT)
                    for i, entry in enumerate(rows, 1):
                        eid = str(entry.get("equipment_id")); item = EQUIPMENT_REGISTRY.get(eid)
                        draw_text(surface, f"{i}. {item.nome if item else eid}", (95, y), WHITE, FONT_SMALL)
                        draw_text(surface, str(entry.get("source", "loot")), (560, y), GRAY, FONT_TINY)
                        y += 48

            elif self.hub_tab == "vendors":
                draw_text(surface, "VENDEDORES", (90, 170), CYAN, FONT_MEDIUM)
                if self.hub_vendor_id is None:
                    y=225
                    for i,(vid,vendor) in enumerate(VENDORS.items(),1):
                        draw_text(surface, f"{i}. {vendor.nome} — {vendor.tipo.value}", (95,y), WHITE,FONT_SMALL)
                        draw_text(surface, vendor.descricao, (430,y), GRAY,FONT_TINY); y+=58
                else:
                    vendor=VENDORS[self.hub_vendor_id]
                    draw_text(surface, vendor.nome, (95,215), YELLOW,FONT)
                    draw_text(surface, f"S alterna compra/venda • modo: {'VENDA' if self.vendor_sell_mode else 'COMPRA'} • BACKSPACE volta", (520,218),GRAY,FONT_TINY)
                    y=265
                    if self.vendor_sell_mode:
                        items=[x for x in self.player_state.inventory if isinstance(x,dict) and x.get('instance_id')][self.hub_page*9:self.hub_page*9+9]
                        for i,entry in enumerate(items,1):
                            item=EQUIPMENT_REGISTRY.get(str(entry.get('equipment_id')))
                            draw_text(surface,f"{i}. {item.nome if item else entry.get('equipment_id')}",(100,y),WHITE,FONT_SMALL)
                            y+=48
                        if not items: draw_text(surface,"Nada para vender.",(100,y),GRAY,FONT_SMALL)
                    else:
                        offers=refresh_vendor_stock(self.player_state,self.hub_vendor_id)[self.hub_page*9:self.hub_page*9+9]
                        for i,offer in enumerate(offers,1):
                            iid=str(offer["item_id"])
                            label=EQUIPMENT_REGISTRY[iid].nome if offer["type"]=="equipment" else CONSUMABLES[iid].nome
                            draw_text(surface,f"{i}. {label}",(100,y),WHITE,FONT_SMALL)
                            draw_text(surface,f"{offer['price']} ouro",(720,y),GREEN,FONT_SMALL);y+=48

            elif self.hub_tab == "factions":
                draw_text(surface, "FACÇÕES / REPUTAÇÃO", (90, 170), PURPLE, FONT_MEDIUM)
                y=215
                for fid,faction in FACTIONS.items():
                    rep=reputation(self.player_state,fid)
                    draw_text(surface,f"{faction.nome}: {rep:+d} — {reputation_rank(rep)}",(95,y),WHITE,FONT_SMALL)
                    draw_text(surface,faction.descricao,(520,y),GRAY,FONT_TINY);y+=48
                y+=18
                draw_text(surface,"ESCOLHAS (uma vez cada)",(95,y),YELLOW,FONT_SMALL);y+=36
                made=set(ensure_faction_state(self.player_state).get("choices",[]))
                for i,choice in enumerate(CHOICES.values(),1):
                    color=DARK_GRAY if choice.id in made else WHITE
                    draw_text(surface,f"{i}. {choice.description}",(110,y),color,FONT_SMALL);y+=42

            elif self.hub_tab == "trials":
                root=ensure_challenge_state(self.player_state)
                draw_text(surface, "PROVAÇÕES / CONQUISTAS", (90, 170), CYAN, FONT_MEDIUM)
                active=root.get("active_trial")
                draw_text(surface,f"Ativa: {TRIALS[active].nome if active in TRIALS else 'nenhuma'} | progresso {root.get('trial_progress',0)} | C coleta Ecos pendentes: {root.get('pending_echo_rewards',0)}",(95,210),YELLOW,FONT_SMALL)
                y=255
                for i,trial in enumerate(TRIALS.values(),1):
                    done=trial.id in root.get("completed_trials",[])
                    draw_text(surface,f"{i}. {trial.nome} — {trial.target_rooms} salas — +{trial.reward_echoes} Ecos",(100,y),GREEN if done else WHITE,FONT_SMALL)
                    draw_text(surface,trial.description,(700,y),GRAY,FONT_TINY);y+=50
                unlocked=len(root.get("unlocked_achievements",[]))
                draw_text(surface,f"Conquistas: {unlocked}/{len(ACHIEVEMENTS)}",(100,HEIGHT-125),PURPLE,FONT_SMALL)

            elif self.hub_tab == "legacy":
                root=ensure_legacy_state(self.player_state); unlocked=set(root.get("unlocked_nodes",[]))
                draw_text(surface, "LEGADO DE ECOS", (90, 170), CYAN, FONT_MEDIUM)
                draw_text(surface,f"Ecos disponíveis: {echoes(self.player_state)} | PgUp/PgDn páginas",(600,176),YELLOW,FONT_SMALL)
                rows=list(NODES.values())[self.hub_page*9:self.hub_page*9+9]
                y=225
                for i,node in enumerate(rows,1):
                    state="DESBLOQUEADO" if node.id in unlocked else f"{node.cost} Ecos"
                    draw_text(surface,f"{i}. [{node.branch}] {node.nome}",(95,y),GREEN if node.id in unlocked else WHITE,FONT_SMALL)
                    draw_text(surface,state,(700,y),YELLOW,FONT_TINY);y+=50

            elif self.hub_tab == "npcs":
                draw_text(surface, "PERSONAGENS", (90, 170), CYAN, FONT_MEDIUM)
                y=225
                for i,npc in enumerate(NPCS.values(),1):
                    draw_text(surface,f"{i}. {npc.nome} — {npc.papel}",(95,y),WHITE,FONT_SMALL)
                    draw_text(surface,npc.descricao,(420,y),GRAY,FONT_TINY);y+=62
                draw_wrapped_text(surface,self.meta_message,pygame.Rect(95,HEIGHT-165,WIDTH-190,90),YELLOW,FONT_SMALL)

            elif self.hub_tab == "supplies":
                limit = expedition_supply_limit(self.player_state)
                used = sum(max(0,int(v)) for v in self.expedition_supply_loadout.values())
                draw_text(surface, "PREPARAR SUPRIMENTOS", (90, 170), CYAN, FONT_MEDIUM)
                draw_text(surface, f"Slots: {used}/{limit} • 1–8 aumenta/zera item • P proteção por Selo • BACKSPACE volta", (95,210), YELLOW, FONT_SMALL)
                y=255
                for i,(cid,definition) in enumerate(CONSUMABLES.items(),1):
                    stock=consumable_count(self.player_state,cid); packed=int(self.expedition_supply_loadout.get(cid,0))
                    draw_text(surface,f"{i}. {definition.nome} — estoque {stock} — levar {packed}",(100,y),WHITE if stock else DARK_GRAY,FONT_SMALL)
                    draw_text(surface,definition.descricao,(630,y),GRAY,FONT_TINY); y+=50
                draw_text(surface,f"Proteção de recompensa: {'ATIVA' if self.expedition_protected_reward else 'OFF'}",(100,HEIGHT-115),GREEN if self.expedition_protected_reward else GRAY,FONT_SMALL)

            elif self.hub_tab=="expedition":
                draw_text(surface,"EXPEDIÇÕES PROCEDURAIS",(90,170),CYAN,FONT_MEDIUM)
                contracts=[None]+[c.id for c in self.campaign.available_contracts()]; selected=contracts[self.expedition_contract_index%len(contracts)] if contracts else None
                label="Nenhum" if not selected else next((c.nome for c in self.campaign.available_contracts() if c.id==selected),selected); today=daily_challenge()
                draw_text(surface,f"C contrato • S suprimentos • D DESAFIO DIÁRIO — contrato: {label}",(95,210),YELLOW,FONT_SMALL)
                daily_rect=pygame.Rect(95,238,WIDTH-190,68); pygame.draw.rect(surface,(27,22,42),daily_rect,border_radius=9); pygame.draw.rect(surface,PURPLE,daily_rect,2,border_radius=9)
                draw_text(surface,f"HOJE: {today.title} — Seed {today.seed}",(115,250),PURPLE,FONT_SMALL); draw_text(surface,f"{REGIONS[today.region_id].nome} • score x{today.score_mult:.2f} • {today.description}",(115,277),GRAY,FONT_TINY)
                regions=[rid for rid in self.player_state.campaign.get("unlocked_regions",[]) if rid in REGIONS and rid!="refugio_ultima_luz"]; y=325
                for i,rid in enumerate(regions[:9],1):
                    region=REGIONS[rid]
                    draw_text(surface,f"{i}. {region.nome} — perigo {region.danger}",(100,y),WHITE,FONT_SMALL)
                    draw_text(surface,region.descricao,(520,y),GRAY,FONT_TINY);y+=55
                if self.awaiting_expedition:
                    draw_text(surface,"Escolha uma região para continuar.",(100,HEIGHT-115),GREEN,FONT)

            elif self.hub_tab == "postgame":
                root=ensure_endgame_state(self.player_state)
                draw_text(surface, "PÓS-JOGO", (90, 170), CYAN, FONT_MEDIUM)
                draw_text(surface,f"Campanha concluída: {'SIM' if root.get('story_complete') else 'NÃO'} | NG+ {root.get('ng_plus_cycle',0)}",(95,220),WHITE,FONT)
                draw_text(surface,"1. Iniciar próximo ciclo NG+",(100,285),GREEN if root.get('story_complete') else DARK_GRAY,FONT)
                draw_text(surface,"2. Boss Rush",(100,340),ORANGE if root.get('boss_rush_unlocked') else DARK_GRAY,FONT)
                draw_text(surface,"3. Abrir Rankings",(100,395),YELLOW,FONT)
                draw_wrapped_text(surface,"O jogo continua single-player. O ranking só compara resultados de runs locais/online; ninguém entra na sua partida.",pygame.Rect(100,465,WIDTH-200,80),GRAY,FONT_SMALL)

        elif self.meta_panel=="ranking":
            draw_text(surface,"RIFTWALKER RANKINGS",(85,76),YELLOW,FONT_BIG); draw_text(surface,"L/ESC fecha | TAB Local/Online | D Geral/Diário | R atualiza",(WIDTH-590,88),GRAY,FONT_TINY)
            today=daily_challenge(); source="ONLINE" if self.ranking_online_view else "LOCAL"; scope=f"DIÁRIO {today.date_key}" if self.ranking_daily_view else "GERAL"
            draw_text(surface,f"{source} • {scope}",(90,140),CYAN if self.ranking_online_view else GREEN,FONT_MEDIUM)
            if self.ranking_online_view:
                if self.ranking_daily_view:
                    rows=LEADERBOARD.top_daily_online_cached(today.date_key,10); status=LEADERBOARD.online.daily_status.get(f"daily:{today.date_key}:","D para desafio diário"); draw_text(surface,status,(420,145),YELLOW,FONT_SMALL)
                else: rows=LEADERBOARD.online.top_cached(10); draw_text(surface,LEADERBOARD.online.status,(300,145),YELLOW,FONT_SMALL)
                if LEADERBOARD.online.error: draw_wrapped_text(surface,LEADERBOARD.online.error,pygame.Rect(650,135,520,50),RED,FONT_TINY)
            else: rows=LEADERBOARD.top_daily_local(today.date_key,10) if self.ranking_daily_view else LEADERBOARD.local.top(10)
            y=205
            headers=("#","Jogador","Classe","Pontos","Salas","Boss","Dif.")
            xs=(95,145,430,650,790,875,965)
            for h,x in zip(headers,xs): draw_text(surface,h,(x,y),GRAY,FONT_TINY)
            y+=35
            for rank,entry in enumerate(rows,1):
                color=YELLOW if LEADERBOARD.last_result and entry.run_id==LEADERBOARD.last_result.run_id else WHITE
                vals=(str(rank),entry.player_name,entry.class_id,f"{entry.score:,}",str(entry.rooms),str(entry.bosses),entry.difficulty)
                for val,x in zip(vals,xs): draw_text(surface,val,(x,y),color,FONT_SMALL if x in (145,430) else FONT_TINY)
                y+=42
            if not rows: draw_text(surface,"Sem resultados ainda." if not self.ranking_online_view else "Nenhum resultado online carregado.",(100,250),GRAY,FONT)
            if self.last_run_result is not None:
                draw_text(surface,self.last_run_gap_text,(WIDTH//2,HEIGHT-205),CYAN,FONT_SMALL,True)
            if not LEADERBOARD.online.enabled:
                draw_wrapped_text(surface,"Online está opcionalmente desligado. Configure leaderboard_config.json com URL + chave publishable de um projeto Supabase e execute SUPABASE_LEADERBOARD_SETUP.sql. O ranking local já funciona 100% offline.",pygame.Rect(100,HEIGHT-160,WIDTH-200,85),GRAY,FONT_SMALL)

        elif self.meta_panel == "supplies":
            draw_text(surface, "SUPRIMENTOS DA EXPEDIÇÃO", (85, 76), CYAN, FONT_BIG)
            draw_text(surface, "1–8 usa • K/ESC fecha", (WIDTH-300, 88), GRAY, FONT_TINY)
            y = 155
            for i,(cid, definition) in enumerate(CONSUMABLES.items(),1):
                count = int(self.run_supplies.get(cid,0))
                color = WHITE if count > 0 else DARK_GRAY
                draw_text(surface, f"{i}. {definition.nome}  x{count}", (95,y), color, FONT_SMALL)
                draw_wrapped_text(surface, definition.descricao, pygame.Rect(390,y-2,720,36), GRAY, FONT_TINY)
                y += 55
            buffs=[]
            if self.rift_guard_timer>0: buffs.append(f"Sal {self.rift_guard_timer:.0f}s")
            if self.tonic_timer>0: buffs.append(f"Tônico {self.tonic_timer:.0f}s")
            if self.crimson_oil_timer>0: buffs.append(f"Óleo {self.crimson_oil_timer:.0f}s")
            if self.echo_signal_timer>0: buffs.append(f"Sinalizador {self.echo_signal_timer:.0f}s")
            draw_text(surface, "Ativos: " + (" • ".join(buffs) if buffs else "nenhum"), (95, HEIGHT-125), YELLOW, FONT_SMALL)
            if self.meta_message: draw_text(surface,self.meta_message,(95,HEIGHT-90),GREEN,FONT_SMALL)

        elif self.meta_panel == "settings":
            draw_text(surface, "CONFIGURAÇÕES", (85, 76), WHITE, FONT_BIG)
            draw_text(surface, "1–9 altera | B rebinding | O/ESC fecha", (WIDTH - 420, 88), GRAY, FONT_TINY)
            a = GAME_SETTINGS.accessibility
            au = GAME_SETTINGS.audio
            diff = DIFFICULTIES[GAME_SETTINGS.difficulty]
            if self.rebind_mode:
                draw_text(surface, "REBIND DE CONTROLES", (105, 145), CYAN, FONT_MEDIUM)
                draw_text(surface, "W/S seleciona • ENTER escolhe • depois pressione a nova tecla • B volta", (105, 180), GRAY, FONT_TINY)
                actions=list(DEFAULT_BINDINGS.keys())
                for i,action in enumerate(actions):
                    col=i//7; row=i%7
                    x=105+col*520; y=225+row*58
                    selected=i==self.rebind_index
                    rect=pygame.Rect(x,y,470,46)
                    pygame.draw.rect(surface,PANEL_HOVER if selected else PANEL,rect,border_radius=7)
                    pygame.draw.rect(surface,CYAN if selected else DARK_GRAY,rect,2,border_radius=7)
                    draw_text(surface,action.replace('_',' ').title(),(x+18,y+12),WHITE,FONT_SMALL)
                    draw_text(surface,binding_name(action),(x+320,y+12),YELLOW,FONT_SMALL)
                if self.rebind_waiting:
                    draw_text(surface,"AGORA PRESSIONE A NOVA TECLA",(WIDTH//2,HEIGHT-105),ORANGE,FONT,True)
            else:
                rows = [
                    ("1", "Legendas", "ON" if a.subtitles else "OFF"),
                    ("2", "Screen shake reduzido", "ON" if a.reduced_screen_shake else "OFF"),
                    ("3", "Flashes reduzidos", "ON" if a.reduced_flashes else "OFF"),
                    ("4", "Números de dano", "ON" if a.damage_numbers else "OFF"),
                    ("5", "Telegraphs alto contraste", "ON" if a.high_contrast_telegraphs else "OFF"),
                    ("6", "Dificuldade", diff.nome),
                    ("7", "Música -10", f"{au.music}%"),
                    ("8", "Música +10", f"{au.music}%"),
                    ("9", "SFX mute/unmute", f"{au.sfx}%"),
                ]
                y = 155
                for key, label, value in rows:
                    rect = pygame.Rect(105, y, WIDTH - 210, 46)
                    pygame.draw.rect(surface, PANEL, rect, border_radius=7)
                    draw_text(surface, f"{key}. {label}", (130, y + 12), WHITE, FONT_SMALL)
                    draw_text(surface, value, (rect.right - 175, y + 12), CYAN, FONT_SMALL)
                    y += 52
                draw_wrapped_text(surface, diff.descricao, pygame.Rect(105, y + 5, WIDTH - 210, 52), GRAY, FONT_TINY)
                draw_text(surface,"B. Alterar teclas configuráveis",(110,HEIGHT-110),YELLOW,FONT_SMALL)

        if self.meta_message:
            draw_text(surface, self.meta_message, (WIDTH // 2, HEIGHT - 78), YELLOW, FONT_SMALL, True)

    def draw_overlay_states(self, surface):
        if self.cinematic_flash>0 and not GAME_SETTINGS.accessibility.reduced_flashes:
            flash=pygame.Surface((WIDTH,HEIGHT),pygame.SRCALPHA); alpha=int(min(120,self.cinematic_flash*620)); flash.fill((245,238,255,alpha)); surface.blit(flash,(0,0))
        if self.boss_cinematic_timer>0:
            shade=pygame.Surface((WIDTH,HEIGHT),pygame.SRCALPHA); shade.fill((0,0,0,95)); surface.blit(shade,(0,0))
            pygame.draw.rect(surface,BLACK,(0,0,WIDTH,84)); pygame.draw.rect(surface,BLACK,(0,HEIGHT-84,WIDTH,84))
            progress=1.0-self.boss_cinematic_timer/max(0.001,self.boss_cinematic_max)
            draw_text(surface,self.boss_cinematic_title,(WIDTH//2,HEIGHT//2-38),ORANGE,FONT_BIG,True); draw_text(surface,self.boss_cinematic_subtitle,(WIDTH//2,HEIGHT//2+22),WHITE,FONT,True)
            pygame.draw.line(surface,DARK_GRAY,(WIDTH//2-260,HEIGHT//2+62),(WIDTH//2+260,HEIGHT//2+62),3); pygame.draw.line(surface,ORANGE,(WIDTH//2-260,HEIGHT//2+62),(WIDTH//2-260+520*progress,HEIGHT//2+62),4)
        elif self.boss_phase_timer>0:
            phase=pygame.Surface((620,90),pygame.SRCALPHA); phase.fill((8,6,12,205)); pygame.draw.rect(phase,ORANGE,phase.get_rect(),2,border_radius=8); draw_text(phase,"NOVA FASE",(310,22),ORANGE,FONT_SMALL,True); draw_text(phase,self.boss_phase_name,(310,56),WHITE,FONT_MEDIUM,True); surface.blit(phase,(WIDTH//2-310,HEIGHT//2-45))
        if self.banner_timer > 0:
            alpha = int(255 * min(1.0, self.banner_timer / 0.35))
            banner = pygame.Surface((720, 94), pygame.SRCALPHA)
            banner.fill((5, 6, 11, min(220, alpha)))
            pygame.draw.rect(banner, (*self.banner_color, alpha), banner.get_rect(), 2, border_radius=10)
            draw_text(banner, self.banner_title, (360, 24), self.banner_color, FONT_MEDIUM, True)
            draw_text(banner, self.banner_subtitle, (360, 61), WHITE, FONT_SMALL, True)
            surface.blit(banner, (WIDTH // 2 - 360, 95))

        if self.speech:
            self.speech.draw(surface)

        if self.paused:
            overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 205))
            surface.blit(overlay, (0, 0))
            draw_text(surface, "PAUSADO", (WIDTH // 2, HEIGHT // 2 - 160), WHITE, FONT_BIG, True)
            draw_text(surface, "W/S ou ↑/↓ para escolher • ENTER ou clique para confirmar", (WIDTH // 2, HEIGHT // 2 - 116), GRAY, FONT_TINY, True)
            labels = ["CONTINUAR", "ABANDONAR RUN → REFÚGIO", "VOLTAR AO MENU", "SAIR DO JOGO"]
            mouse = pygame.mouse.get_pos()
            for idx, rect in enumerate(self.pause_option_rects()):
                selected = idx == self.pause_selection or rect.collidepoint(mouse)
                pygame.draw.rect(surface, PANEL_HOVER if selected else PANEL, rect, border_radius=9)
                border = CYAN if selected else DARK_GRAY
                pygame.draw.rect(surface, border, rect, 2, border_radius=9)
                text = labels[idx]
                color = RED if idx == 3 else (YELLOW if idx == 1 else WHITE)
                draw_text(surface, text, rect.center, color, FONT_SMALL, True)
            if self.abandon_confirm:
                draw_text(surface, "CONFIRMAR ABANDONO? Selecione ABANDONAR novamente e confirme.", (WIDTH // 2, HEIGHT // 2 + 205), RED, FONT_SMALL, True)
            else:
                draw_text(surface, "ESC retoma imediatamente", (WIDTH // 2, HEIGHT // 2 + 205), GRAY, FONT_TINY, True)

        if self.game_over and self.meta_panel is None:
            overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 215))
            surface.blit(overlay, (0, 0))
            draw_text(surface, "VOCÊ CAIU NA RUPTURA", (WIDTH // 2, 245), RED, FONT_BIG, True)
            style = self.telemetry.classify_style()
            draw_text(
                surface,
                f"O Véu registrou seu estilo: {style}",
                (WIDTH // 2, 320),
                PURPLE,
                FONT,
                True,
            )
            draw_text(
                surface,
                "Alguns inimigos superiores podem se lembrar disso.",
                (WIDTH // 2, 355),
                WHITE,
                FONT_SMALL,
                True,
            )
            if self.last_run_result is not None:
                e=self.last_run_result
                draw_text(surface,f"{e.score:,} PONTOS • {e.rooms} salas • {e.kills} abates • {e.bosses} bosses",(WIDTH//2,405),YELLOW,FONT_SMALL,True)
                draw_text(surface,self.last_run_gap_text,(WIDTH//2,438),CYAN,FONT_SMALL,True)
                draw_text(surface,f"Melhor combo desta run: {self.combo_best} hits • tempo {e.duration_seconds}s",(WIDTH//2,470),GRAY,FONT_TINY,True)
            draw_text(surface,"R = nova expedição   •   L = rankings",(WIDTH//2,515),GREEN,FONT,True)

    def draw(self):
        # Um único offset por frame mantém mundo, névoa e mira perfeitamente alinhados.
        self.current_camera_offset = self.camera_offset()
        if self.hub_room_open:
            self.draw_hub_room(screen)
            return
        self.draw_world(screen, self.current_camera_offset)
        if self.transition_alpha > 0:
            fade = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            fade.fill((7, 8, 13, int(clamp(self.transition_alpha, 0, 210))))
            screen.blit(fade, (0, 0))

        self.draw_combat_aim_overlay(screen)
        self.draw_hud(screen)
        self.draw_offer(screen)
        self.draw_inventory(screen)
        self.draw_meta_panel(screen)
        self.draw_overlay_states(screen)


# ============================================================
# LOOP
# ============================================================

def main():
    profile_manager = ProfileManager(str(SAVE_DIR), slots=3)

    while True:
        frontend = Frontend(profile_manager)
        state = frontend.run()
        active_path = profile_manager.save_path(frontend.selected_slot)
        game = Game(state, save_path=active_path)

        try:
            while True:
                real_dt = min(clock.tick(RENDER_FPS_CAP) / 1000.0, 0.25)

                for event in pygame.event.get():
                    game.handle_event(event)

                if game.return_to_menu_requested:
                    game.audio.stop_all()
                    break

                updates = 0
                for fixed_dt in stepper.consume(real_dt):
                    game.fixed_update(fixed_dt)
                    updates += 1
                game.logic_updates_last_frame = updates
                game.diagnostics.record_frame(real_dt, updates, game.diagnostic_counts())

                game.draw()
                pygame.display.flip()
        except SystemExit:
            raise
        except Exception as exc:
            context = {
                "wave": game.wave,
                "rooms_cleared": game.rooms_cleared,
                "region": game.campaign.current_region().id,
                "class_id": game.player_state.identity.class_id,
                "diagnostics": game.diagnostics.summary(),
            }
            report = write_crash_report(DATA_DIR / "logs", exc, context)
            print(f"\nRIFTWALKER encontrou um erro. Relatório salvo em: {report}\n")
            try:
                game.autosave.save_now(game.player_state, AutosaveReason.CHECKPOINT)
            except Exception:
                pass
            try:
                game.audio.stop_all()
                pygame.quit()
            except Exception:
                pass
            raise


if __name__ == "__main__":
    main()
