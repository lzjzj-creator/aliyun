-- 知脉知识库 Supabase 建表脚本
-- 在 Supabase SQL Editor 里执行一次即可

create extension if not exists vector;

-- 文档表：一篇笔记一行
create table if not exists documents (
  slug        text primary key,          -- Quartz slug，也是来源路径
  title       text not null,
  dir         text,                      -- 一级目录原文，如 0001大一核心信息差
  kb_scope    text,                      -- freshman|sophomore_junior|senior|cases|major|video|meta
  tags        text[] default '{}',
  source_url  text,                      -- 可点击的来源链接
  char_count  int,
  created_at  timestamptz default now()
);

-- 分段表：检索的基本单位
create table if not exists chunks (
  id          bigint generated always as identity primary key,
  doc_slug    text references documents(slug) on delete cascade,
  seq         int not null,              -- 段落在原文中的序号
  text        text not null,
  embedding   vector(1024),              -- text-embedding-v4 / bge-m3 均为 1024 维
  kb_scope    text,
  visibility  text default 'public',     -- public|private，检索只查 public
  status      text default 'ready',      -- ready|pending|error
  source_url  text
);

create index if not exists chunks_embedding_idx
  on chunks using ivfflat (embedding vector_cosine_ops) with (lists = 100);
create index if not exists chunks_scope_idx on chunks (kb_scope);
create index if not exists chunks_doc_idx   on chunks (doc_slug);

-- 混合检索：向量相似度 0.7 + 关键词命中 0.3，Supabase RPC 调用
create or replace function match_chunks(
  qvec  vector(1024),
  qtext text,
  scope text default 'all',
  k     int  default 8
) returns table (
  doc_slug   text,
  title      text,
  seq        int,
  text       text,
  source_url text,
  score      float
) as $$
  select c.doc_slug, d.title, c.seq, c.text, c.source_url,
         (0.7 * (1 - (c.embedding <=> qvec))
        + 0.3 * case when c.text ilike '%' || qtext || '%' then 1 else 0 end) as score
  from chunks c
  join documents d on d.slug = c.doc_slug
  where c.visibility = 'public' and c.status = 'ready'
    and (scope = 'all' or c.kb_scope = scope)
  order by score desc
  limit k;
$$ language sql stable;
