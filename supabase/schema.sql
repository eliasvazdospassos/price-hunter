-- Price Hunter — Schema Supabase (PostgreSQL + Row Level Security)
-- Execute este script no SQL Editor do Supabase caso deseje migrar
-- a autenticação e persistência do SQLite local para o Supabase.

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  name text not null default '',
  preferred_cep text not null default '',
  preferences jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.favorites (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  offer_id text not null,
  product_key text not null,
  product_title text not null,
  brand text default '',
  model text default '',
  store text not null,
  observed_price numeric(12,2) not null,
  shipping_price numeric(12,2),
  final_price numeric(12,2),
  original_url text not null,
  image_url text default '',
  condition text not null default 'new',
  cep_used text default '',
  opportunity_score integer,
  created_at timestamptz not null default now(),
  unique (user_id, original_url)
);

create table if not exists public.price_history (
  id bigint generated always as identity primary key,
  product_key text not null,
  offer_url text not null,
  store text not null,
  condition text not null,
  price numeric(12,2) not null,
  shipping_price numeric(12,2),
  final_price numeric(12,2),
  cep_prefix text default '',
  observed_at timestamptz not null default now()
);

create index if not exists idx_ph_product_key on public.price_history(product_key, observed_at desc);
create index if not exists idx_ph_offer_url on public.price_history(offer_url, observed_at desc);

-- Segurança: Row Level Security (RLS)
alter table public.profiles enable row level security;
alter table public.favorites enable row level security;
alter table public.price_history enable row level security;

create policy "Usuário acessa apenas o próprio perfil"
  on public.profiles for all
  using (auth.uid() = id)
  with check (auth.uid() = id);

create policy "Usuário gerencia apenas os próprios favoritos"
  on public.favorites for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create policy "Leitura pública de histórico de preços"
  on public.price_history for select
  using (true);
