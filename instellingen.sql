-- Nieuwe tabel: JOUW blijvende standaard-aantal-uren per groep,
-- los van de dagelijkse planning (die blijft per dag/uitzondering).
create table if not exists instellingen (
  groep      text primary key,
  n          int2 not null default 4,
  updated_at timestamptz not null default now()
);

-- vertrekpunt: dezelfde 4 als vandaag, jij past dit later zelf aan
insert into instellingen (groep, n) values
  ('circulatie', 4),
  ('wp', 4)
on conflict (groep) do nothing;

alter table instellingen enable row level security;
create policy "i_select" on instellingen for select using (true);
create policy "i_update" on instellingen for update using (true) with check (true);
create policy "i_insert" on instellingen for insert with check (true);
