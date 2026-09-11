-- VVIP Lounge Estate Ledger — schema
-- Run this once in the Supabase SQL editor after creating the project.

create table houses (
  id serial primary key,
  block text not null check (block in ('old','new')),
  house_num int not null,
  rent int not null,
  condition_notes text[] default '{}',
  profile_complete boolean default false,
  unique(block, house_num)
);

create table tenants (
  id serial primary key,
  house_id int references houses(id) unique,  -- unique = one active tenant per house
  name text not null,
  course text,
  phone text,
  emergency_name text,
  emergency_phone text,
  roommates jsonb default '[]',
  photo_url text,
  paid boolean default false,
  created_at timestamptz default now()
);

create table complaints (
  id serial primary key,
  tenant_id int references tenants(id) on delete cascade,
  type text,
  message text,
  status text default 'open' check (status in ('open','resolved')),
  created_at timestamptz default now()
);

create table renewals (
  id serial primary key,
  tenant_id int references tenants(id) on delete cascade,
  block text,
  house_num int,
  tenant_name text,
  created_at timestamptz default now()
);

-- Seed the real 15 houses: old block (6, KSh 25,000/semester),
-- new block (9, KSh 30,000/semester). Numbering starts from the
-- house nearest the gate.
insert into houses (block, house_num, rent, condition_notes) values
  ('old', 1, 25000, ARRAY['Repainted 2025']),
  ('old', 2, 25000, '{}'),
  ('old', 3, 25000, '{}'),
  ('old', 4, 25000, ARRAY['Ceiling patch needed']),
  ('old', 5, 25000, '{}'),
  ('old', 6, 25000, '{}'),
  ('new', 1, 30000, ARRAY['Newly built — 2024']),
  ('new', 2, 30000, ARRAY['Leaking tap reported']),
  ('new', 3, 30000, '{}'),
  ('new', 4, 30000, '{}'),
  ('new', 5, 30000, '{}'),
  ('new', 6, 30000, '{}'),
  ('new', 7, 30000, '{}'),
  ('new', 8, 30000, '{}'),
  ('new', 9, 30000, ARRAY['Newly built — 2024']);
