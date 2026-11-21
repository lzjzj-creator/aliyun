-- v3 user-level memory: owner root, long-term facts, extraction watermarks,
-- per-role resume documents. Apply manually after review; never self-schedules.
-- Depends on 20261117_v2_memory.sql (chat_sessions FK + v2_touch_updated_at).
begin;

-- Owner: root of cross-session facts/documents. Only the token hash is stored;
-- anon_id is a provenance hint and is NEVER an authorization key.
create table public.v3_owner (
  id uuid primary key default gen_random_uuid(),
  owner_key_hash text not null unique check (length(owner_key_hash) = 64),
  recovery_hash text check (recovery_hash is null or length(recovery_hash) = 64),
  anon_id text,
  memory_enabled boolean not null default false,
  consent_at timestamptz,
  created_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  deleted_at timestamptz
);

-- Fact: one row = one atomic value; value must be a verbatim substring of the
-- source quote (server-side validation, see v3 extractor). status machine:
-- proposed -> confirmed | rejected; confirmed replaced -> superseded.
create table public.v3_facts (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references public.v3_owner(id) on delete cascade,
  kind text not null check (kind in
    ('identity','goal','education','experience','skill','award','metric','constraint')),
  slot_path text not null check (length(slot_path) between 1 and 120),
  value jsonb not null,
  value_hash text not null check (length(value_hash) = 64),
  status text not null default 'proposed'
    check (status in ('proposed','confirmed','rejected','superseded')),
  confidence numeric(3,2) not null default 0 check (confidence between 0 and 1),
  source_session_id uuid references public.chat_sessions(id) on delete set null,
  source_message_id bigint,
  evidence jsonb not null default '[]' check (jsonb_typeof(evidence) = 'array'),
  superseded_by uuid references public.v3_facts(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz
);
create unique index v3_facts_dedup_idx
  on public.v3_facts(owner_id, value_hash) where deleted_at is null;
create index v3_facts_slot_idx
  on public.v3_facts(owner_id, kind, slot_path) where deleted_at is null;

-- Incremental extraction watermark: a (owner, session) pair extracts each
-- message at most once; failed runs never block chat.
create table public.v3_extraction_state (
  owner_id uuid not null references public.v3_owner(id) on delete cascade,
  session_id uuid not null references public.chat_sessions(id) on delete cascade,
  last_message_id bigint not null default 0,
  attempts integer not null default 0,
  last_error_code text,
  extracted_at timestamptz,
  primary key (owner_id, session_id)
);

-- Resume document: fields + evidence + fact snapshot; the snapshot lets
-- "facts changed since generation" be detected per field (stale_sources).
create table public.v3_resume_documents (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references public.v3_owner(id) on delete cascade,
  title text not null check (length(title) between 1 and 80),
  template_version text not null,
  target_role text,
  jd_digest text,
  fields jsonb not null default '{}' check (jsonb_typeof(fields) = 'object'),
  evidence_map jsonb not null default '{}' check (jsonb_typeof(evidence_map) = 'object'),
  gaps jsonb not null default '[]' check (jsonb_typeof(gaps) = 'array'),
  fact_snapshot jsonb not null default '[]' check (jsonb_typeof(fact_snapshot) = 'array'),
  status text not null default 'draft' check (status in ('draft','ready','archived')),
  version bigint not null default 0 check (version >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz
);
create index v3_resume_owner_idx
  on public.v3_resume_documents(owner_id, updated_at desc) where deleted_at is null;

-- Reuse the v2 trigger function (20261117_v2_memory.sql) instead of duplicating it.
create trigger v3_facts_touch before update on public.v3_facts
for each row execute function public.v2_touch_updated_at();
create trigger v3_resume_touch before update on public.v3_resume_documents
for each row execute function public.v2_touch_updated_at();

-- Service key only: no browser/anonymous policies, no PUBLIC grants.
alter table public.v3_owner enable row level security;
alter table public.v3_facts enable row level security;
alter table public.v3_extraction_state enable row level security;
alter table public.v3_resume_documents enable row level security;
revoke all on public.v3_owner, public.v3_facts, public.v3_extraction_state,
  public.v3_resume_documents from public, anon, authenticated;
grant select, insert, update, delete on public.v3_owner, public.v3_facts,
  public.v3_extraction_state, public.v3_resume_documents to service_role;
commit;
