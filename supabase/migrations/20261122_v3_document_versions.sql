-- v3 简历版本历史：每次变更前快照整份可变状态，仅保留最新 10 版（§12-#11）。
begin;

create table public.v3_document_versions (
  id uuid primary key default gen_random_uuid(),
  doc_id uuid not null references public.v3_resume_documents(id)
    on delete cascade,
  owner_id uuid not null references public.v3_owner(id) on delete cascade,
  version integer not null check (version >= 0),
  snapshot jsonb not null check (jsonb_typeof(snapshot) = 'object'),
  created_at timestamptz not null default now(),
  unique (doc_id, version)
);
create index v3_doc_versions_doc_idx
  on public.v3_document_versions(doc_id, version desc);

alter table public.v3_document_versions enable row level security;
-- 无 policies：仅 service_role，与 v3 其余表一致。

commit;
