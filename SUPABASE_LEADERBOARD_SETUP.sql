-- RIFTWALKER 0.10 — leaderboard online comunitário (single-player)
-- Execute em um projeto Supabase próprio. O cliente usa apenas chave publishable/anon.

create table if not exists public.riftwalker_leaderboard (
  run_id text primary key check (char_length(run_id) between 12 and 40),
  player_name text not null check (char_length(player_name) between 1 and 24),
  class_id text not null check (char_length(class_id) between 1 and 40),
  score bigint not null check (score between 0 and 2000000000),
  rooms integer not null check (rooms between 0 and 10000),
  kills integer not null check (kills between 0 and 1000000),
  bosses integer not null check (bosses between 0 and 10000),
  superiors integer not null check (superiors between 0 and 100000),
  active_rifts integer not null check (active_rifts between 0 and 5),
  difficulty text not null check (char_length(difficulty) between 1 and 32),
  ng_plus_cycle integer not null check (ng_plus_cycle between 0 and 100),
  trial_id text not null default '' check (char_length(trial_id) <= 48),
  result text not null check (result in ('victory','death','abandoned')),
  duration_seconds integer not null check (duration_seconds between 0 and 604800),
  build_version text not null check (char_length(build_version) between 1 and 32),
  created_at timestamptz not null default now(),
  fingerprint text not null check (char_length(fingerprint) between 32 and 64)
);

create index if not exists riftwalker_leaderboard_score_idx
  on public.riftwalker_leaderboard (score desc, created_at asc);

alter table public.riftwalker_leaderboard enable row level security;

-- Data API: grants e RLS trabalham juntos. Concedemos somente leitura/inserção.
revoke all on table public.riftwalker_leaderboard from anon, authenticated;
grant select, insert on table public.riftwalker_leaderboard to anon, authenticated;

drop policy if exists "leaderboard public read" on public.riftwalker_leaderboard;
create policy "leaderboard public read"
  on public.riftwalker_leaderboard for select
  to anon, authenticated
  using (true);

drop policy if exists "leaderboard public insert" on public.riftwalker_leaderboard;
create policy "leaderboard public insert"
  on public.riftwalker_leaderboard for insert
  to anon, authenticated
  with check (
    score >= 0 and rooms >= 0 and kills >= 0 and bosses >= 0 and superiors >= 0
    and active_rifts between 0 and 5
    and duration_seconds between 0 and 604800
  );

-- Não há UPDATE/DELETE público. Uma run enviada vira registro imutável.
