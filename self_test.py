"""Testes rápidos do núcleo do RIFTWALKER Release Candidate 0.10.

Pode ser executado mesmo sem abrir o jogo:
    python self_test.py

Se pygame estiver instalado, também tenta um smoke test da camada jogável
usando vídeo dummy, sem abrir janela real.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from runtime_diagnostics import RuntimeDiagnostics, trim_oldest, write_crash_report
from core_systems import CLASS_REGISTRY, Gender, SaveManager, create_new_character
from content_systems import (
    CLASS_UNLOCK_RULES,
    EQUIPMENT_REGISTRY,
    EQUIPMENT_SETS,
    content_summary,
    equip_item,
    equipment_modifiers,
    evaluate_class_unlocks,
    make_inventory_item,
    roll_equipment_drop,
)

from narrative_systems import (
    CampaignDirector,
    CampaignEvent,
    CONTRACTS,
    DYNAMIC_EVENTS,
    NPCS,
    QUESTS,
    REGIONS,
    chapter_title,
    ensure_campaign_state,
    narrative_summary,
    select_npc_dialogue,
    validate_narrative_content,
)

from hub_systems import (
    ExpeditionPlan,
    FacilityId,
    add_gold,
    add_echoes,
    add_material,
    add_consumable,
    craft,
    ensure_hub_state,
    expedition_supply_limit,
    facility_level,
    finish_expedition,
    hub_summary,
    launch_prepared_expedition,
    prepare_expedition,
    purchase_offer,
    refresh_vendor_stock,
    salvage_equipment,
    upgrade_facility,
    validate_hub_content,
)


from combat_status_systems import (
    Element, StatusController, StatusId, status_summary, validate_status_content,
)
from build_synergy_systems import (
    active_synergies, available_evolutions, build_summary, evolve_skill, synergy_modifiers,
)
from world_generation_systems import (
    RoomType, begin_generated_expedition, complete_generated_expedition,
    generate_expedition_map, load_active_map, save_active_map, serialize_map, worldgen_summary,
)
from boss_systems import BossDirector, BOSSES, PATTERNS, boss_summary
from encounter_director_systems import EncounterDirector, PerformanceSnapshot, director_summary
from faction_systems import FACTIONS, faction_summary, ensure_faction_state, make_faction_choice, reputation
from challenge_systems import (
    ACHIEVEMENTS, TRIALS, add_metric, challenge_summary, collect_pending_echo_rewards,
    ensure_challenge_state, progress_trial_room, start_trial,
)
from legacy_systems import (
    NODES, legacy_modifiers, legacy_summary, unlock_node, ensure_legacy_state,
)

from class_kit_systems import CLASS_KITS, SKILL_BEHAVIORS, class_kit_summary
from endgame_systems import (
    ENDINGS, FINAL_BOSS_ID, ECHO_BOSS_ID, eligible_endings, endgame_summary,
    ensure_endgame_state, record_final_boss_defeat, choose_ending, start_ng_plus,
    boss_rush_roster, ng_plus_modifiers,
)
from player_experience_systems import (
    DIFFICULTIES, CODEX, TUTORIAL_STEPS, GameSettings, SettingsManager,
    experience_summary, mark_tutorial_seen, tutorial_for_trigger, unlock_codex_by_trigger,
)
from audio_systems import AudioRouter, MusicDirector, MUSIC, audio_summary
from profile_systems import ProfileManager, profile_summary
from production_systems import run_content_audit, content_metrics
from leaderboard_systems import LocalLeaderboard, make_run_result
from daily_challenge_systems import daily_challenge
from room_interaction_systems import generate_room_features

from creature_systems import (
    CREATURES,
    MUTATIONS,
    CreatureTier,
    KnowledgeRank,
    add_research_points,
    apply_traits_to_enemy,
    bestiary_completion,
    bestiary_entry_view,
    can_research,
    complete_research,
    creature_summary,
    ensure_archive_state,
    knowledge_rank,
    record_encounter,
    record_kill,
    record_superior_history,
    roll_spawn_traits,
    validate_creature_content,
)



def core_tests() -> None:
    summary = content_summary()
    assert len(CLASS_REGISTRY) == 10, len(CLASS_REGISTRY)
    assert summary["equipment"] >= 28, summary
    assert summary["sets"] >= 6, summary
    assert len(CLASS_UNLOCK_RULES) == 7

    narrative = narrative_summary()
    assert narrative["regions"] >= 6, narrative
    assert narrative["npcs"] >= 4, narrative
    assert narrative["quests"] >= 6, narrative
    assert narrative["contracts"] >= 4, narrative
    assert narrative["dynamic_events"] >= 5, narrative
    assert not validate_narrative_content(), validate_narrative_content()

    hubinfo = hub_summary()
    assert hubinfo["materials"] >= 8, hubinfo
    assert hubinfo["consumables"] >= 8, hubinfo
    assert hubinfo["facilities"] >= 5, hubinfo
    assert hubinfo["vendors"] >= 4, hubinfo
    assert hubinfo["recipes"] >= 8, hubinfo
    assert not validate_hub_content(), validate_hub_content()

    player = create_new_character("Tester", Gender.MASCULINO, "guerreiro")
    player.inventory.append(make_inventory_item("espada_sentinela", "self_test"))
    ok, _ = equip_item(player, "espada_sentinela")
    assert ok
    assert equipment_modifiers(player).damage_flat >= 6

    player.progression.bosses_derrotados.add("guardiao_primeira_ruptura")
    evaluate_class_unlocks(player)
    assert "ceifador" in player.progression.classes_desbloqueadas
    # O teste acima injeta o boss diretamente só para validar unlock de classe.
    # Limpa o fato antes do teste narrativo para simular uma campanha nova real.
    player.progression.bosses_derrotados.discard("guardiao_primeira_ruptura")

    # Todas as classes devem ser construíveis; a UI controla quais estão liberadas.
    for class_id in CLASS_REGISTRY:
        created = create_new_character("Tester", Gender.FEMININO, class_id)
        assert created.identity.class_id == class_id

    # Boss garante tentativa de loot e deve retornar item elegível.
    for _ in range(8):
        assert roll_equipment_drop(player, boss=True, luck=0.5) is not None

    # Campanha: missão inicial -> boss -> capítulo 2.
    director = CampaignDirector(player)
    campaign = ensure_campaign_state(player)
    assert "primeiro_passo" in campaign["quests"]
    assert campaign["quests"]["primeiro_passo"]["status"] == "active"

    director.record(CampaignEvent.ROOM_CLEARED, amount=3)
    assert campaign["quests"]["primeiro_passo"]["status"] == "complete"
    claimed = director.claim_ready_quests()
    assert "primeiro_passo" in claimed
    assert "first_expedition_complete" in campaign["flags"]
    assert campaign["quests"]["guardiao_da_fenda"]["status"] == "active"

    director.record(CampaignEvent.BOSS_DEFEATED, target_id="guardiao_primeira_ruptura")
    assert campaign["quests"]["guardiao_da_fenda"]["status"] == "complete"
    director.claim_ready_quests()
    assert campaign["chapter"] >= 2
    assert "bosque_sussurrante" in campaign["unlocked_regions"]
    assert "CAPÍTULO II" in chapter_title(campaign["chapter"])

    # Regressão 0.9.9: missão liberada depois de um fato persistente deve
    # reconhecer o histórico sem obrigar o jogador a repetir o boss.
    player.progression.bosses_derrotados.update({
        "guardiao_primeira_ruptura", "matriarca_sussurrante",
        "primeiro_rasgado", "cronarca_fraturado"
    })
    campaign["flags"].extend(["observatorio_unlocked", "abismo_unlocked"])
    campaign["flags"] = sorted(set(campaign["flags"]))
    from narrative_systems import refresh_quest_availability
    refresh_quest_availability(player)
    scars = campaign["quests"]["quatro_cicatrizes"]
    assert scars["status"] == "complete", scars

    # Diálogo contextual precisa marcar encontro e liberar conteúdo dependente.
    line = select_npc_dialogue(player, "nox")
    assert line is not None
    assert "met_nox" in campaign["flags"]

    # Eventos dinâmicos devem ser selecionáveis e entrar no histórico.
    player.stats.nivel = 8
    event = director.choose_dynamic_event()
    assert event is not None
    assert event.id in campaign["dynamic_event_history"]
    assert director.available_contracts(), "nenhum contrato disponível"

    # Refúgio/economia: recursos, loja, crafting, upgrade e desmontagem.
    hub = ensure_hub_state(player)
    add_gold(player, 10000, source="self_test")
    for material_id in hub["materials"]:
        add_material(player, material_id, 100)

    mira_stock = refresh_vendor_stock(player, "mira")
    frasco_offer = next(x for x in mira_stock if x["offer_id"] == "con:frasco_vital")
    bought, _ = purchase_offer(player, "mira", frasco_offer["offer_id"])
    assert bought

    crafted, _ = craft(player, "craft_tonico_fluxo")
    assert crafted

    upgraded, _ = upgrade_facility(player, FacilityId.FORJA.value)
    assert upgraded
    assert facility_level(player, FacilityId.FORJA.value) == 2

    # Duas cópias permitem desmontar uma sem arrancar a equipada.
    extra = make_inventory_item("espada_sentinela", "self_test_duplicate")
    player.inventory.append(extra)
    salvaged, _, salvage_result = salvage_equipment(player, extra["instance_id"])
    assert salvaged and salvage_result

    # Preparação de expedição deve respeitar suprimentos e entrar na região.
    add_consumable(player, "frasco_vital", 2)
    plan = ExpeditionPlan(
        region_id="campos_primeira_fenda",
        consumables={"frasco_vital": 1},
    )
    assert plan.total_supply_slots() <= expedition_supply_limit(player)
    prepared, _ = prepare_expedition(player, plan)
    assert prepared
    launched, _, launched_plan = launch_prepared_expedition(player)
    assert launched and launched_plan is not None
    assert ensure_campaign_state(player)["current_region"] == "campos_primeira_fenda"

    cycle_before = ensure_hub_state(player)["market_cycle"]
    run_record = finish_expedition(
        player,
        survived=True,
        rooms=4,
        bosses=1,
        active_rifts=2,
        gold_reward=200,
        echo_reward=3,
    )
    assert run_record["gold_reward"] >= 200
    assert ensure_campaign_state(player)["current_region"] == "refugio_ultima_luz"
    assert ensure_hub_state(player)["market_cycle"] == cycle_before + 1

    # Criaturas / Bestiário / mutações / Superiores.
    cinfo = creature_summary()
    assert cinfo["creatures"] >= 10, cinfo
    assert cinfo["mutations"] >= 15, cinfo
    assert cinfo["research_projects"] >= 4, cinfo
    assert not validate_creature_content(), validate_creature_content()

    # Spawn superior determinístico: deve gerar traits válidos e aplicáveis sem Pygame.
    import random
    superior_traits = roll_spawn_traits(
        "cacador_ecos",
        wave=12,
        active_rift_ids=("temporal", "vazio"),
        force_superior=True,
        rng=random.Random(77),
    )
    assert superior_traits.tier == CreatureTier.SUPERIOR
    assert superior_traits.superior_name
    assert 1 <= len(superior_traits.mutation_ids) <= 3
    assert superior_traits.hp_mult > 1.0
    assert superior_traits.xp_mult > 1.0

    class DummyEnemy:
        max_hp = 100
        hp = 100
        damage = 10
        speed = 100.0
        xp = 10
        radius = 15

    dummy = DummyEnemy()
    apply_traits_to_enemy(dummy, superior_traits)
    assert dummy.hp == dummy.max_hp
    assert dummy.max_hp > 100
    assert dummy.xp > 10
    assert dummy.creature_id == "cacador_ecos"

    # Encontrar e matar deve subir o conhecimento e registrar mutações.
    before_rank = knowledge_rank(player, "cacador_ecos")
    record_encounter(player, "cacador_ecos", superior_traits.mutation_ids)
    record_kill(
        player,
        "cacador_ecos",
        superior_traits.mutation_ids,
        superior=True,
        superior_name=superior_traits.superior_name,
    )
    after_rank = knowledge_rank(player, "cacador_ecos")
    assert after_rank != KnowledgeRank.DESCONHECIDO
    assert list(player.progression.bestiario["cacador_ecos"]["seen_mutations"])
    view = bestiary_entry_view(player, "cacador_ecos")
    assert view["nome"] == CREATURES["cacador_ecos"].nome

    # Superior alimenta o Arquivo do Véu e projetos obedecem dependências/custos.
    record_superior_history(player, superior_traits, defeated=True)
    archive = ensure_archive_state(player)
    assert archive["lifetime_superiors"] >= 1
    assert archive["superior_history"]
    add_research_points(player, 100)
    ok, _ = can_research(player, "taxonomia_fendas")
    assert ok
    researched, _ = complete_research(player, "taxonomia_fendas")
    assert researched
    assert "taxonomia_fendas" in ensure_archive_state(player)["completed_research"]

    completion = bestiary_completion(player)
    assert completion["discovered"] >= 1


    # ========================================================
    # BUILD 0.7 — SALTO MONSTRO: 8 SISTEMAS
    # ========================================================

    # 1) Status + reações elementais.
    sinfo = status_summary()
    assert sinfo["statuses"] >= 12, sinfo
    assert sinfo["reactions"] >= 10, sinfo
    assert not validate_status_content(), validate_status_content()
    effects = StatusController()
    effects.apply(StatusId.CONGELADO)
    hit = effects.resolve_hit(100, Element.FOGO)
    assert "choque_termico" in hit.reaction_ids
    assert hit.final_damage > 100
    assert not effects.has(StatusId.CONGELADO)

    # 2) Sinergias de build + evoluções de habilidade.
    binfo = build_summary()
    assert binfo["synergies"] >= 12, binfo
    assert binfo["evolutions"] >= 10, binfo
    mage = create_new_character("Synergy", Gender.FEMININO, "mago")
    mage.learn_skill("prisao_glacial")
    mage.learn_skill("singularidade_arcana")
    synergy_ids = {x.id for x in active_synergies(mage)}
    assert "tempestade_arcana" in synergy_ids
    assert synergy_modifiers(mage)["damage_mult"] > 1.0

    chrono = create_new_character("Chrono", Gender.MASCULINO, "cronista")
    chrono.learn_skill("passo_rebobinado")
    chrono.learn_skill("instante_imovel")
    for _ in range(500):
        chrono.register_skill_use("passo_rebobinado", mastery_xp=6)
    assert chrono.masteries["passo_rebobinado"].nivel == 5
    evo_ids = {x.id for x in available_evolutions(chrono)}
    assert "segundo_roubado" in evo_ids
    evolved, _ = evolve_skill(chrono, "segundo_roubado")
    assert evolved

    # 3) Mapa procedural determinístico e persistente.
    winfo = worldgen_summary()
    assert winfo["room_types"] >= 10, winfo
    map_a = generate_expedition_map("campos_primeira_fenda", seed=2468, difficulty=2, main_length=9)
    map_b = generate_expedition_map("campos_primeira_fenda", seed=2468, difficulty=2, main_length=9)
    assert serialize_map(map_a) == serialize_map(map_b)
    assert map_a.nodes[map_a.boss_id].room_type == RoomType.BOSS
    generated = begin_generated_expedition(player, "campos_primeira_fenda", seed=999, difficulty=2)
    assert load_active_map(player) is not None
    first_next = generated.next_nodes(include_hidden=True)[0]
    assert generated.move_to(first_next.id)
    save_active_map(player, generated)
    assert load_active_map(player).current_id == first_next.id

    # 4) Bosses com fases, decks anti-repetição e Memória do Véu.
    bossinfo = boss_summary()
    assert bossinfo["bosses"] >= 4, bossinfo
    assert bossinfo["patterns"] >= 12, bossinfo
    memory = player.veil_memory.get("guardiao_primeira_ruptura")
    memory.encontros = 3
    memory.ultimo_estilo_detectado = "kite_ranged"
    boss_director = BossDirector("guardiao_primeira_ruptura", player, seed=12)
    sequence = [boss_director.choose_pattern(0.48).id for _ in range(6)]
    assert len(set(sequence)) >= 2
    assert boss_director.intro_line()

    # 5) Diretor de encontro justo: ameaça/pacing variam, mas ajuste é limitado.
    dinfo = director_summary()
    assert dinfo["unit_archetypes"] >= 5, dinfo
    enc_director = EncounterDirector(seed=77)
    struggling = PerformanceSnapshot(hp_ratio=0.20, recent_deaths=2, active_rifts=0)
    dominating = PerformanceSnapshot(hp_ratio=0.95, rooms_cleared_fast=3, active_rifts=2)
    assert abs(enc_director.fair_adjustment(struggling)) <= 0.12
    assert abs(enc_director.fair_adjustment(dominating)) <= 0.12
    plan = enc_director.generate("bastilha_carmesim", depth=5, performance=dominating)
    assert plan.units and plan.spawn_groups and plan.threat_budget > 0

    # 6) Facções + escolhas com consequência persistente.
    finfo = faction_summary()
    assert finfo["factions"] >= 4, finfo
    before_vigilia = reputation(player, "vigilia")
    faction_ok, _ = make_faction_choice(player, "entregar_fragmento_vigilia")
    assert faction_ok
    assert reputation(player, "vigilia") > before_vigilia
    assert reputation(player, "filhos_fenda") < 0

    # 7) Conquistas e Provações.
    chinfo = challenge_summary()
    assert chinfo["achievements"] >= 15, chinfo
    assert chinfo["trials"] >= 6, chinfo
    unlocked = add_metric(player, "kills", 100)
    assert "cem_inimigos" in unlocked
    trial_ok, _ = start_trial(player, "vidro")
    assert trial_ok
    finished = False
    for _ in range(TRIALS["vidro"].target_rooms):
        finished, modifiers = progress_trial_room(player)
    assert finished and "player_hp_60" in modifiers
    assert collect_pending_echo_rewards(player) > 0

    # 8) Legado de Ecos — meta progressão moderada.
    linfo = legacy_summary()
    assert linfo["nodes"] >= 20, linfo
    assert linfo["branches"] >= 5, linfo
    add_echoes(player, 20)
    legacy_ok, _ = unlock_node(player, "vigor_1")
    assert legacy_ok
    assert "vigor_1" in ensure_legacy_state(player)["unlocked_nodes"]
    assert legacy_modifiers(player)["max_hp_mult"] > 1.0

    # 9) Kits 0.10 RC: as 10 classes devem ter identidade completa e pelo menos 6 skills recomendadas.
    kitinfo = class_kit_summary()
    assert kitinfo["kits"] == len(CLASS_REGISTRY) == 10, kitinfo
    assert kitinfo["skills_total"] >= 60, kitinfo
    assert len(SKILL_BEHAVIORS) >= 30, len(SKILL_BEHAVIORS)
    for class_id, kit in CLASS_KITS.items():
        assert len(kit.recommended_skills) >= 6, (class_id, kit.recommended_skills)
        for sid in kit.recommended_skills:
            assert sid in __import__("core_systems").SKILL_REGISTRY, sid

    # 10) Campanha final + múltiplos finais + NG+ / boss rush.
    einfo = endgame_summary()
    assert einfo["bosses_total"] >= 6, einfo
    assert einfo["endings"] >= 5, einfo
    assert FINAL_BOSS_ID in BOSSES and ECHO_BOSS_ID in BOSSES
    # Reputação mínima para garantir mais de uma escolha de epílogo.
    from faction_systems import change_reputation
    change_reputation(player, "arquivistas", 30)
    change_reputation(player, "cartografos", 30)
    record_final_boss_defeat(player)
    options = eligible_endings(player)
    assert len(options) >= 2, options
    chosen = choose_ending(player, options[0].id)
    assert ensure_endgame_state(player)["story_complete"]
    cycle = start_ng_plus(player)
    assert cycle == 1
    assert ng_plus_modifiers(cycle)["enemy_hp"] > 1.0
    assert ECHO_BOSS_ID in boss_rush_roster(player)

    # 11) Configurações, acessibilidade, tutorial e códice.
    xinfo = experience_summary()
    assert xinfo["difficulties"] >= 4, xinfo
    assert xinfo["tutorial_steps"] >= 10, xinfo
    assert xinfo["codex_entries"] >= 10, xinfo
    step = tutorial_for_trigger(player, "first_rift_seen")
    assert step is not None
    mark_tutorial_seen(player, step.id)
    assert tutorial_for_trigger(player, "first_rift_seen") is None
    unlocked_codex = unlock_codex_by_trigger(player, "first_rift_seen")
    assert "rupturas" in unlocked_codex
    with tempfile.TemporaryDirectory() as td_settings:
        settings_path = Path(td_settings) / "settings.json"
        sm = SettingsManager(str(settings_path))
        settings = GameSettings(difficulty="riftwalker")
        settings.accessibility.reduced_screen_shake = True
        settings.audio.music = 61
        sm.save(settings)
        loaded_settings = sm.load()
        assert loaded_settings.difficulty == "riftwalker"
        assert loaded_settings.accessibility.reduced_screen_shake
        assert loaded_settings.audio.music == 61

    # 12) Áudio é dirigido por eventos; ausência de asset gera fallback, não crash.
    ainfo = audio_summary()
    assert ainfo["cues"] >= 20, ainfo
    assert ainfo["music_states"] >= 8, ainfo
    router = AudioRouter(seed=12)
    plan_audio = router.resolve("parry", asset_root="/diretorio/que_nao_existe")
    assert plan_audio is not None and plan_audio.fallback_only
    music = MusicDirector()
    desired = music.desired_state(boss_present=True)
    assert desired.id == "boss"
    assert music.transition_plan(desired) is not None

    # 13) Save slots e recuperação.
    pinfo = profile_summary()
    assert pinfo["default_slots"] >= 3
    with tempfile.TemporaryDirectory() as td_profile:
        pm = ProfileManager(td_profile, slots=3)
        meta = pm.save(1, player, playtime_seconds=1234)
        assert meta.exists and meta.character_name == player.identity.nome
        assert pm.verify(1)["load_ok"]
        slots = pm.all_slots()
        assert len(slots) == 3 and slots[0].exists and not slots[1].exists
        pm.delete(1, keep_recovery=True)
        assert not pm.slot_meta(1).exists
        assert pm.restore_deleted(1)
        assert pm.slot_meta(1).exists

    # 14) Auditoria de produção: sem bloqueadores de referência.
    metrics = content_metrics()
    assert metrics["skills"] >= 60, metrics
    assert metrics["bosses"] >= 6, metrics
    audit = run_content_audit(require_audio_assets=False)
    assert not audit.blockers, [(x.code, x.message) for x in audit.blockers]

    # Save round-trip incluindo equipamento, progressão, campanha e Refúgio.
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "save.json"
        manager = SaveManager(str(path))
        manager.save(player)
        loaded = manager.load()
        assert loaded.equipment.arma == "espada_sentinela"
        assert "ceifador" in loaded.progression.classes_desbloqueadas
        assert loaded.inventory[0]["equipment_id"] == "espada_sentinela"
        loaded_campaign = ensure_campaign_state(loaded)
        assert loaded_campaign["chapter"] >= 2
        assert "met_nox" in loaded_campaign["flags"]
        assert "bosque_sussurrante" in loaded_campaign["unlocked_regions"]
        loaded_hub = ensure_hub_state(loaded)
        assert loaded_hub["facilities"][FacilityId.FORJA.value] == 2
        assert loaded_hub["market_cycle"] >= 1
        assert loaded_hub["expedition_history"], "histórico de expedição não persistiu"
        assert "cacador_ecos" in loaded.progression.bestiario
        loaded_archive = ensure_archive_state(loaded)
        assert loaded_archive["lifetime_superiors"] >= 1
        assert "taxonomia_fendas" in loaded_archive["completed_research"]
        assert "vigor_1" in ensure_legacy_state(loaded)["unlocked_nodes"]
        assert reputation(loaded, "vigilia") > 0
        assert "cem_inimigos" in ensure_challenge_state(loaded)["unlocked_achievements"]
        assert ensure_faction_state(loaded)["choices"]
        assert load_active_map(loaded) is not None


    # 15) Ranking local funciona offline e mantém ordenação determinística.
    with tempfile.TemporaryDirectory() as td_rank:
        lb = LocalLeaderboard(Path(td_rank) / "leaderboard.json")
        a = make_run_result(player_name="A", class_id="guerreiro", rooms=5, kills=30, bosses=1, superiors=2, active_rifts=1, difficulty="viajante", difficulty_reward_mult=1.0, ng_plus_cycle=0, trial_id=None, result="death", duration_seconds=420, build_version="test", nonce="a")
        b = make_run_result(player_name="B", class_id="mago", rooms=8, kills=50, bosses=2, superiors=4, active_rifts=2, difficulty="riftwalker", difficulty_reward_mult=1.15, ng_plus_cycle=1, trial_id="vidro", result="victory", duration_seconds=510, build_version="test", nonce="b")
        assert lb.submit(a) == 1
        assert lb.submit(b) == 1
        top = lb.top(2)
        assert [x.player_name for x in top] == ["B", "A"]

    # 16) Insanity Pass: desafio diário e salas interativas são determinísticos.
    daily_a = daily_challenge("2026-09-25")
    daily_b = daily_challenge("2026-09-25")
    daily_next = daily_challenge("2026-09-26")
    assert daily_a == daily_b
    assert (daily_a.seed, daily_a.modifier_id) != (daily_next.seed, daily_next.modifier_id)
    specs_a = generate_room_features(base_seed=daily_a.seed, node_id="room_05_main", room_type="elite", region_id=daily_a.region_id, depth=5)
    specs_b = generate_room_features(base_seed=daily_a.seed, node_id="room_05_main", room_type="elite", region_id=daily_a.region_id, depth=5)
    assert specs_a == specs_b and len(specs_a) == 4
    with tempfile.TemporaryDirectory() as td_daily:
        lb = LocalLeaderboard(Path(td_daily) / "leaderboard.json")
        for i in range(3):
            entry = make_run_result(player_name=f"D{i}", class_id="mago", rooms=7+i, kills=20+i, bosses=1, superiors=2, active_rifts=1, difficulty="riftwalker", difficulty_reward_mult=1.2, ng_plus_cycle=0, trial_id=daily_a.trial_id if i < 2 else None, result="victory", duration_seconds=500+i*20, build_version="0.11-test", nonce=f"daily-{i}")
            lb.submit(entry)
        assert len(lb.top_trial(f"daily:{daily_a.date_key}:", 10)) == 2

    # 17) Diagnóstico/performance guardrails e relatório de crash.
    diag = RuntimeDiagnostics()
    diag.record_frame(1/60, 1, {"particles": 12, "projectiles": 3})
    diag.record_frame(1/30, 2, {"particles": 20, "projectiles": 5})
    assert diag.average_frame_ms > 0
    assert diag.peak_counts["particles"] == 20
    items = list(range(10))
    assert trim_oldest(items, 4) == 6 and items == [6, 7, 8, 9]
    with tempfile.TemporaryDirectory() as td_diag:
        try:
            raise RuntimeError("diagnostic-test")
        except RuntimeError as exc:
            report = write_crash_report(td_diag, exc, {"wave": 9})
        assert report.exists() and "diagnostic-test" in report.read_text(encoding="utf-8")

    # 18) Save corrompido deve cair no backup e informar a origem.
    with tempfile.TemporaryDirectory() as td_recovery:
        save_path = Path(td_recovery) / "save.json"
        recover = SaveManager(str(save_path))
        recover.save(player)
        # segundo save cria backup válido do primeiro
        recover.save(player)
        save_path.write_text("{corrompido", encoding="utf-8")
        recovered = recover.load()
        assert recover.last_load_source == "backup", recover.last_load_source
        assert recover.last_error and "primary" in recover.last_error
        assert recovered.identity.nome == player.identity.nome


def enhancement_tests() -> None:
    expedition = generate_expedition_map("bosque_sussurrante", seed=2026, difficulty=2, main_length=9)
    assert len(expedition.nodes) >= 16, expedition.nodes
    assert any(node.room_type == RoomType.ELITE for node in expedition.nodes.values())
    assert any(len(node.connections) > 1 for node in expedition.nodes.values())

    from pygame_audio_runtime import audio_mix_profile
    profile = audio_mix_profile()
    assert 0.8 <= profile["master_boost"] <= 1.25
    assert 0.0 <= profile["ambience_lift"] <= 0.4
    assert profile["sfx_focus"] >= 1.0


def optional_pygame_smoke_test() -> str:
    try:
        import pygame  # noqa: F401
    except Exception as exc:
        return f"SKIP pygame: {exc}"

    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

    # main inicializa pygame ao importar; em dummy não precisa janela física.
    try:
        import pygame
        import main
        state = create_new_character("Smoke", Gender.MASCULINO, "guerreiro")
        game = main.Game(state)
        for _ in range(15):
            game.fixed_update(1 / 60)
        game.player.velocity.update(20, 0)
        game.player.attack_cooldown = 0.20
        game.player.attack_visual_timer = 0
        game.player.update(1 / 60, game)
        assert game.player.visual_state == "walk"

        # Render one complete frame and each interactive-room prop so visual
        # systems are exercised, not only the simulation/update loop.
        game.draw()
        world_position = game.player.pos.copy()
        game.enter_hub_room()
        assert game.hub_room_open
        assert len(set((round(p.x), round(p.y)) for p in game.hub_npc_positions.values())) == len(main.NPCS)
        assert game._hub_position_is_walkable(game.player.pos)
        assert not game._hub_position_is_walkable(game.hub_npc_positions["aris"])
        game.fixed_update(1 / 60)
        game.player.pos = game.hub_npc_positions["aris"].copy()
        game.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_e))
        assert game.hub_dialogue_npc_id == "aris" and game.meta_message.startswith("Aris:")
        game.draw()
        game.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_TAB))
        assert game.meta_panel == "hub" and not game.hub_room_open
        game.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE))
        assert game.hub_room_open and game.meta_panel is None
        game.leave_hub_room()
        assert not game.hub_room_open
        assert game.player.pos.distance_to(world_position) < 0.01
        game.offer_mode = None
        game.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_h))
        assert game.hub_room_open
        game.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_h))
        assert not game.hub_room_open

        environment = main.EnvironmentRenderer()
        for region_id in REGIONS:
            environment.draw_base(
                main.screen, main.ROOM, region_id, 1, 0, False, 1.25, pygame.Vector2()
            )
            environment.draw_foreground(
                main.screen, main.ROOM, region_id, 1.25, pygame.Vector2(), game.player.pos
            )
        for index, kind in enumerate(
            ("volatile_crystal", "veil_spike", "echo_cache", "healing_well", "rift_pylon")
        ):
            prop = main.RoomInteractive(
                kind, pygame.Vector2(120 + index * 80, 180), 24, 60, 60, 20, 2.0
            )
            prop.draw(main.screen, pygame.Vector2())
        main.SlashVisual(pygame.Vector2(300, 260), pygame.Vector2(1, 0), main.CYAN, 90).draw(
            main.screen, pygame.Vector2()
        )
        bolt = main.Projectile(
            pygame.Vector2(420, 260), pygame.Vector2(520, 0), 20, main.YELLOW,
            "player", skill_id="arrow",
        )
        bolt.trail_points = [pygame.Vector2(380, 260), pygame.Vector2(400, 260)]
        bolt.draw(main.screen, pygame.Vector2())

        enemy = main.Enemy(
            pygame.Vector2(600, 360), "crawler", 1.0, 0,
            main.choose_squad_doctrine(), "vanguard",
        )
        enemy.visual_state = "attack"
        enemy.attack_visual_timer = 0.2
        enemy.attack_visual_max = 0.4
        enemy.draw(main.screen, pygame.Vector2())

        animation_states = ("idle", "walk", "run", "attack", "hurt")
        for class_id in CLASS_REGISTRY:
            for gender in (Gender.MASCULINO, Gender.FEMININO):
                assert main.get_player_skin(class_id, gender) is not None, (class_id, gender)
                for animation_state in animation_states:
                    assert main.get_player_animation_frame(
                        main.BASE_DIR, class_id, gender, animation_state, 0.2
                    ) is not None, (class_id, gender, animation_state)
        enemy_skin_root = main.BASE_DIR / "assets" / "sprites" / "enemies"
        for enemy_skin in enemy_skin_root.glob("*.png"):
            skin_id = enemy_skin.stem
            assert main.get_enemy_skin(skin_id) is not None, skin_id
            for animation_state in animation_states:
                assert main.get_enemy_animation_frame(
                    main.BASE_DIR, skin_id, animation_state, 0.2
                ) is not None, (skin_id, animation_state)

        archer_sustain = (
            main.BASIC_ATTACK_RESOURCE_COSTS["arqueiro"] / 0.205
        )
        mage_sustain = (
            main.BASIC_ATTACK_RESOURCE_COSTS["mago"] / 0.26
        )
        assert archer_sustain <= main.BASE_RESOURCE_REGEN_PER_SECOND * 1.06
        assert mage_sustain >= main.BASE_RESOURCE_REGEN_PER_SECOND * 1.70

        mage_state = create_new_character("Mana", Gender.FEMININO, "mago")
        mage_game = main.Game(mage_state)
        mage_player = mage_game.player
        assert "bola_de_fogo" in mage_state.owned_skills
        mage_player.use_skill("bola_de_fogo", mage_game)
        assert mage_player.resource < mage_player.resource_max
        mage_player.pending_cast = None
        mage_game.skill_cooldowns.clear()
        mage_player.resource = mage_player.resource_max
        for _ in range(30 * 60):
            if mage_player.attack_cooldown <= 0:
                mage_player.basic_attack(mage_game)
            mage_player.update(1 / 60, mage_game)
        assert mage_player.resource < 5.0, mage_player.resource

        spell_state = create_new_character("SpellMana", Gender.FEMININO, "mago")
        spell_game = main.Game(spell_state)
        spell_player = spell_game.player
        for _ in range(30 * 60):
            for skill_id in tuple(spell_game.skill_cooldowns):
                spell_game.skill_cooldowns[skill_id] = max(
                    0.0, spell_game.skill_cooldowns[skill_id] - 1 / 60
                )
            if (
                spell_player.pending_cast is None
                and spell_game.skill_cooldowns.get("bola_de_fogo", 0.0) <= 0
            ):
                spell_player.use_skill("bola_de_fogo", spell_game)
            spell_player.update(1 / 60, spell_game)
        assert spell_player.resource < 15.0, spell_player.resource

        duelist_peak_cooldown = max(
            main.DUELIST_BASIC_COOLDOWN_FLOOR,
            main.DUELIST_BASIC_COOLDOWN_BASE
            - main.DUELIST_COMBO_MAX * main.DUELIST_BASIC_COOLDOWN_REDUCTION_PER_COMBO,
        )
        duelist_peak_dps = (
            31
            * (
                main.DUELIST_BASIC_DAMAGE_BASE
                + main.DUELIST_COMBO_MAX * main.DUELIST_BASIC_DAMAGE_PER_COMBO
            )
            / duelist_peak_cooldown
        )
        assert duelist_peak_dps <= (34 / 0.34) * 1.7
        return "pygame smoke test OK; locomotion animation and mage mana drain checked"
    except Exception as exc:
        raise AssertionError(f"pygame smoke test falhou: {exc}") from exc


if __name__ == "__main__":
    core_tests()
    enhancement_tests()
    print("CORE/CONTENT TESTS: OK")
    print("ENHANCEMENT TESTS: OK")
    print(optional_pygame_smoke_test())
