"""RIFTWALKER — LOCAL + OPTIONAL ONLINE LEADERBOARDS 0.10 RC.

O jogo continua 100% single-player. O ranking compara resultados de runs; ele não
altera combate, save ou progressão. O ranking local funciona sem internet. O
ranking online é opcional e usa a REST API do Supabase quando configurado em
leaderboard_config.json (ou por variáveis de ambiente).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import threading
import urllib.parse
import urllib.request
from typing import Dict, Iterable, List, Optional, Tuple


LEADERBOARD_SCHEMA_VERSION = 1
DEFAULT_LOCAL_LIMIT = 40
DEFAULT_ONLINE_LIMIT = 50


@dataclass(frozen=True)
class RunResult:
    run_id: str
    player_name: str
    class_id: str
    score: int
    rooms: int
    kills: int
    bosses: int
    superiors: int
    active_rifts: int
    difficulty: str
    ng_plus_cycle: int
    trial_id: str
    result: str
    duration_seconds: int
    build_version: str
    created_at: str
    fingerprint: str

    @classmethod
    def from_dict(cls, raw: Dict[str, object]) -> "RunResult":
        fields = cls.__dataclass_fields__
        data = {k: raw.get(k) for k in fields}
        data.update({
            "run_id": str(data.get("run_id") or ""),
            "player_name": str(data.get("player_name") or "Riftwalker")[:24],
            "class_id": str(data.get("class_id") or "guerreiro")[:40],
            "score": max(0, int(data.get("score") or 0)),
            "rooms": max(0, int(data.get("rooms") or 0)),
            "kills": max(0, int(data.get("kills") or 0)),
            "bosses": max(0, int(data.get("bosses") or 0)),
            "superiors": max(0, int(data.get("superiors") or 0)),
            "active_rifts": max(0, int(data.get("active_rifts") or 0)),
            "difficulty": str(data.get("difficulty") or "viajante")[:32],
            "ng_plus_cycle": max(0, int(data.get("ng_plus_cycle") or 0)),
            "trial_id": str(data.get("trial_id") or "")[:48],
            "result": str(data.get("result") or "unknown")[:24],
            "duration_seconds": max(0, int(data.get("duration_seconds") or 0)),
            "build_version": str(data.get("build_version") or "unknown")[:32],
            "created_at": str(data.get("created_at") or ""),
            "fingerprint": str(data.get("fingerprint") or "")[:64],
        })
        return cls(**data)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def calculate_run_score(
    *,
    rooms: int,
    kills: int,
    bosses: int,
    superiors: int,
    active_rifts: int,
    difficulty_reward_mult: float,
    ng_plus_cycle: int,
    trial_active: bool,
    result: str,
    duration_seconds: int,
) -> int:
    """Pontuação competitiva determinística, baseada só em progresso da run.

    Tempo dá bônus pequeno e limitado; o sistema não força speedrun e não pune
    quem explora. Abandonar preserva a pontuação, mas com redução de 10%.
    """
    rooms = max(0, int(rooms)); kills = max(0, int(kills)); bosses = max(0, int(bosses))
    superiors = max(0, int(superiors)); active_rifts = max(0, int(active_rifts))
    ng_plus_cycle = max(0, int(ng_plus_cycle)); duration_seconds = max(0, int(duration_seconds))

    base = rooms * 1000 + kills * 70 + bosses * 3200 + superiors * 550 + active_rifts * 650
    difficulty_mult = max(0.75, min(2.0, float(difficulty_reward_mult)))
    ng_mult = 1.0 + min(10, ng_plus_cycle) * 0.08
    trial_mult = 1.12 if trial_active else 1.0
    result_mult = {"victory": 1.25, "death": 1.0, "abandoned": 0.90}.get(result, 1.0)

    # Até +12% por eficiência, saturando a partir de 8 min. Runs longas não perdem pontos.
    time_bonus = 1.0
    if duration_seconds > 0:
        target = max(120, rooms * 55 + bosses * 75)
        if duration_seconds < target:
            time_bonus += min(0.12, (target - duration_seconds) / max(1, target) * 0.12)

    return max(0, int(round(base * difficulty_mult * ng_mult * trial_mult * result_mult * time_bonus)))


def make_run_result(
    *,
    player_name: str,
    class_id: str,
    rooms: int,
    kills: int,
    bosses: int,
    superiors: int,
    active_rifts: int,
    difficulty: str,
    difficulty_reward_mult: float,
    ng_plus_cycle: int,
    trial_id: Optional[str],
    result: str,
    duration_seconds: int,
    build_version: str,
    nonce: str,
) -> RunResult:
    score = calculate_run_score(
        rooms=rooms, kills=kills, bosses=bosses, superiors=superiors,
        active_rifts=active_rifts, difficulty_reward_mult=difficulty_reward_mult,
        ng_plus_cycle=ng_plus_cycle, trial_active=bool(trial_id), result=result,
        duration_seconds=duration_seconds,
    )
    created = _utc_now()
    material = "|".join(map(str, (
        player_name, class_id, rooms, kills, bosses, superiors, active_rifts,
        difficulty, ng_plus_cycle, trial_id or "", result, duration_seconds,
        build_version, nonce, created, score,
    )))
    digest = sha256(material.encode("utf-8")).hexdigest()
    return RunResult(
        run_id=digest[:24], player_name=player_name.strip()[:24] or "Riftwalker",
        class_id=class_id, score=score, rooms=max(0, int(rooms)), kills=max(0, int(kills)),
        bosses=max(0, int(bosses)), superiors=max(0, int(superiors)),
        active_rifts=max(0, int(active_rifts)), difficulty=difficulty,
        ng_plus_cycle=max(0, int(ng_plus_cycle)), trial_id=trial_id or "",
        result=result, duration_seconds=max(0, int(duration_seconds)),
        build_version=build_version, created_at=created, fingerprint=digest,
    )


class LocalLeaderboard:
    def __init__(self, path: str | Path, limit: int = DEFAULT_LOCAL_LIMIT):
        self.path = Path(path)
        self.limit = max(10, int(limit))
        self._lock = threading.Lock()

    def _read(self) -> List[RunResult]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            entries = raw.get("entries", []) if isinstance(raw, dict) else []
            return [RunResult.from_dict(x) for x in entries if isinstance(x, dict)]
        except Exception:
            return []

    def _write(self, entries: Iterable[RunResult]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ordered = sorted(entries, key=lambda x: (-x.score, x.created_at))[: self.limit]
        payload = {"schema_version": LEADERBOARD_SCHEMA_VERSION, "entries": [asdict(x) for x in ordered]}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def submit(self, result: RunResult) -> int:
        with self._lock:
            entries = self._read()
            if result.run_id not in {x.run_id for x in entries}:
                entries.append(result)
                self._write(entries)
            ordered = sorted(entries, key=lambda x: (-x.score, x.created_at))
            return next((i + 1 for i, x in enumerate(ordered) if x.run_id == result.run_id), len(ordered))

    def top(self, limit: int = 10) -> List[RunResult]:
        with self._lock:
            return sorted(self._read(), key=lambda x: (-x.score, x.created_at))[: max(1, int(limit))]

    def top_trial(self, trial_prefix: str, limit: int = 10) -> List[RunResult]:
        prefix = str(trial_prefix)
        with self._lock:
            rows = [x for x in self._read() if x.trial_id.startswith(prefix)]
            return sorted(rows, key=lambda x: (-x.score, x.created_at))[: max(1, int(limit))]

    def best_for_player(self, player_name: str) -> Optional[RunResult]:
        key = player_name.strip().casefold()
        matches = [x for x in self.top(self.limit) if x.player_name.casefold() == key]
        return matches[0] if matches else None


class OnlineLeaderboardClient:
    """REST Supabase opcional com cache e chamadas em thread.

    A chave publishable/anon não é segredo. O schema SQL fornecido restringe as
    operações a SELECT/INSERT e valida limites básicos no servidor. Como o jogo é
    single-player, o placar online deve ser tratado como competitivo/comunitário,
    não como anti-cheat inviolável.
    """
    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        self.enabled = False
        self.project_url = ""
        self.publishable_key = ""
        self.table = "riftwalker_leaderboard"
        self.timeout = 3.0
        self.status = "ONLINE OFF"
        self.error = ""
        self.cached_top: List[RunResult] = []
        self.cached_daily: Dict[str, List[RunResult]] = {}
        self.daily_status: Dict[str, str] = {}
        self._busy = False
        self._lock = threading.Lock()
        self.reload_config()

    def reload_config(self) -> None:
        data: Dict[str, object] = {}
        if self.config_path.exists():
            try:
                raw = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict): data = raw
            except Exception:
                data = {}
        self.project_url = str(os.environ.get("RIFTWALKER_SUPABASE_URL") or data.get("project_url") or "").rstrip("/")
        self.publishable_key = str(os.environ.get("RIFTWALKER_SUPABASE_KEY") or data.get("publishable_key") or "")
        self.table = str(data.get("table") or "riftwalker_leaderboard")
        self.enabled = bool(data.get("enabled", False) or (self.project_url and self.publishable_key)) and bool(self.project_url and self.publishable_key)
        self.status = "ONLINE PRONTO" if self.enabled else "ONLINE OFF — configure leaderboard_config.json"

    def _headers(self, prefer: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "apikey": self.publishable_key,
            "Content-Type": "application/json",
        }
        # Chaves publishable modernas (sb_publishable_*) não são JWTs de usuário.
        # Já a chave anon legada é um JWT e pode ser usada como Bearer.
        if not self.publishable_key.startswith("sb_publishable_"):
            headers["Authorization"] = f"Bearer {self.publishable_key}"
        if prefer: headers["Prefer"] = prefer
        return headers

    def _request(self, request: urllib.request.Request) -> bytes:
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read()

    def fetch_top(self, limit: int = DEFAULT_ONLINE_LIMIT) -> List[RunResult]:
        if not self.enabled:
            return []
        limit = max(1, min(100, int(limit)))
        query = urllib.parse.urlencode({
            "select": "run_id,player_name,class_id,score,rooms,kills,bosses,superiors,active_rifts,difficulty,ng_plus_cycle,trial_id,result,duration_seconds,build_version,created_at,fingerprint",
            "order": "score.desc,created_at.asc",
            "limit": str(limit),
        })
        url = f"{self.project_url}/rest/v1/{self.table}?{query}"
        raw = self._request(urllib.request.Request(url, headers=self._headers()))
        data = json.loads(raw.decode("utf-8"))
        return [RunResult.from_dict(x) for x in data if isinstance(x, dict)]

    def fetch_trial(self, trial_prefix: str, limit: int = DEFAULT_ONLINE_LIMIT) -> List[RunResult]:
        if not self.enabled:
            return []
        limit = max(1, min(100, int(limit)))
        query = urllib.parse.urlencode({
            "select": "run_id,player_name,class_id,score,rooms,kills,bosses,superiors,active_rifts,difficulty,ng_plus_cycle,trial_id,result,duration_seconds,build_version,created_at,fingerprint",
            "trial_id": f"like.{trial_prefix}%",
            "order": "score.desc,created_at.asc",
            "limit": str(limit),
        })
        url = f"{self.project_url}/rest/v1/{self.table}?{query}"
        raw = self._request(urllib.request.Request(url, headers=self._headers()))
        data = json.loads(raw.decode("utf-8"))
        return [RunResult.from_dict(x) for x in data if isinstance(x, dict)]

    def refresh_trial_async(self, trial_prefix: str, limit: int = DEFAULT_ONLINE_LIMIT) -> bool:
        if not self.enabled:
            return False
        prefix = str(trial_prefix)
        self.daily_status[prefix] = "ATUALIZANDO DESAFIO..."
        def worker():
            try:
                data = self.fetch_trial(prefix, limit)
                with self._lock:
                    self.cached_daily[prefix] = data
                self.daily_status[prefix] = f"DESAFIO ONLINE OK — {len(data)} resultados"
            except Exception as exc:
                self.daily_status[prefix] = f"DESAFIO ONLINE INDISPONÍVEL — {type(exc).__name__}"
        threading.Thread(target=worker, daemon=True).start()
        return True

    def top_trial_cached(self, trial_prefix: str, limit: int = 10) -> List[RunResult]:
        prefix = str(trial_prefix)
        with self._lock:
            return list(self.cached_daily.get(prefix, [])[: max(1, int(limit))])

    def submit(self, result: RunResult) -> None:
        if not self.enabled:
            return
        url = f"{self.project_url}/rest/v1/{self.table}"
        body = json.dumps(asdict(result), ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=self._headers("return=minimal,resolution=ignore-duplicates"), method="POST")
        self._request(request)

    def refresh_async(self, limit: int = DEFAULT_ONLINE_LIMIT) -> bool:
        if not self.enabled or self._busy:
            return False
        self._busy = True
        self.status = "ATUALIZANDO ONLINE..."
        def worker():
            try:
                data = self.fetch_top(limit)
                with self._lock:
                    self.cached_top = data
                self.error = ""
                self.status = f"ONLINE OK — {len(data)} resultados"
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"
                self.status = "ONLINE INDISPONÍVEL — ranking local continua"
            finally:
                self._busy = False
        threading.Thread(target=worker, daemon=True).start()
        return True

    def submit_async(self, result: RunResult) -> bool:
        if not self.enabled:
            return False
        def worker():
            try:
                self.submit(result)
                self.error = ""
                self.status = "RESULTADO ENVIADO — atualizando top..."
                self.refresh_async()
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"
                self.status = "FALHA AO ENVIAR — resultado ficou no ranking local"
        threading.Thread(target=worker, daemon=True).start()
        return True

    def top_cached(self, limit: int = 10) -> List[RunResult]:
        with self._lock:
            return list(self.cached_top[: max(1, int(limit))])


class LeaderboardManager:
    def __init__(self, data_dir: str | Path):
        root = Path(data_dir)
        self.local = LocalLeaderboard(root / "leaderboard_local.json")
        self.online = OnlineLeaderboardClient(root / "leaderboard_config.json")
        self.last_result: Optional[RunResult] = None
        self.last_local_rank: Optional[int] = None

    def submit(self, result: RunResult) -> int:
        self.last_result = result
        self.last_local_rank = self.local.submit(result)
        self.online.submit_async(result)
        return self.last_local_rank

    def top_daily_local(self, date_key: str, limit: int = 10) -> List[RunResult]:
        return self.local.top_trial(f"daily:{date_key}:", limit)

    def top_daily_online_cached(self, date_key: str, limit: int = 10) -> List[RunResult]:
        return self.online.top_trial_cached(f"daily:{date_key}:", limit)

    def refresh_daily_online(self, date_key: str, limit: int = DEFAULT_ONLINE_LIMIT) -> bool:
        return self.online.refresh_trial_async(f"daily:{date_key}:", limit)
