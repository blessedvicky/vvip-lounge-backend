-- VVIP Lounge Estate Ledger — schema v3
-- Blocks stored internally as A/B/C (stable identifiers); displayed to
-- humans as J/T/R via the frontend's BLOCK_META prefix mapping — no need
-- to touch the database again if the display names change later.
--   A -> "J Block" (J1..J6), 6 houses
--   B -> "T Block" (T1..T6), 6 houses
--   C -> "R Block" (R1..R3), 3 houses
-- Flat rent: KSh 30,000/semester for every house.
--
-- NOTE: this replaces any earlier schema. Run this in the SQL editor to
-- drop and recreate everything fresh — any test registrations made so far
-- will be wiped.

drop table if exists renewals;
drop table if exists complaints;
drop table if exists tenants;
drop table if exists houses;

create table houses (
  id serial primary key,
  block text not null check (block in ('A','B','C')),
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
  approved boolean default false,  -- landlady confirms payment before this flips true
  agreed_terms boolean default false,
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
  payment_confirmed boolean default false,
  created_at timestamptz default now()
);

-- Seed the real 15 houses across the 3 blocks — flat KSh 30,000 rent.
insert into houses (block, house_num, rent, condition_notes) values
  ('A', 1, 30000, ARRAY['Repainted 2025']),
  ('A', 2, 30000, '{}'),
  ('A', 3, 30000, '{}'),
  ('A', 4, 30000, ARRAY['Ceiling patch needed']),
  ('A', 5, 30000, '{}'),
  ('A', 6, 30000, '{}'),
  ('B', 1, 30000, ARRAY['Newly built — 2024']),
  ('B', 2, 30000, ARRAY['Leaking tap reported']),
  ('B', 3, 30000, '{}'),
  ('B', 4, 30000, '{}'),
  ('B', 5, 30000, '{}'),
  ('B', 6, 30000, '{}'),
  ('C', 1, 30000, '{}'),
  ('C', 2, 30000, '{}'),
  ('C', 3, 30000, ARRAY['Newly built — 2024']);

-- Landlord-editable settings (estate name, theme colors, rent per block).
create table if not exists settings (
  id int primary key default 1,
  estate_name text not null default 'VVIP Lounge',
  color_primary text not null default '#1E6B47',
  color_accent text not null default '#D19A3D',
  rent_a int not null default 30000,
  rent_b int not null default 30000,
  rent_c int not null default 30000,
  check (id = 1)
);
insert into settings (id) values (1) on conflict (id) do nothing;
