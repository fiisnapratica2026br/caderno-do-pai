
create table public.households (
 id uuid primary key default gen_random_uuid(),
 owner_telegram_id bigint not null unique check(owner_telegram_id > 0),
 name text not null default 'Minha casa' check(length(name) between 1 and 80),
 created_at timestamptz not null default now()
);
create table public.expenses (
 id bigint generated always as identity primary key,
 household_id uuid not null references public.households(id),
 source_key text not null,
 amount numeric(12,2) not null check(amount > 0),
 description text not null check(length(description) between 1 and 250),
 category text not null default 'Outros',
 expense_date date not null,
 items jsonb not null default '[]',
 created_at timestamptz not null default now(),
 unique(household_id, source_key)
);
create table public.receipt_drafts (
 id uuid primary key,
 household_id uuid not null references public.households(id),
 source_key text not null,
 data jsonb not null,
 saved_expense_id bigint references public.expenses(id),
 cancelled boolean not null default false,
 created_at timestamptz not null default now()
);
create index expenses_family_date on public.expenses(household_id,expense_date);
create index drafts_family on public.receipt_drafts(household_id);
alter table public.households enable row level security;
alter table public.expenses enable row level security;
alter table public.receipt_drafts enable row level security;
revoke all on public.households,public.expenses,public.receipt_drafts from anon,authenticated;
grant all on public.households,public.expenses,public.receipt_drafts to service_role;
grant usage,select on sequence public.expenses_id_seq to service_role;

create function public.household_bot(p_user bigint,p_action text,p_data jsonb default '{}')
returns jsonb language plpgsql security invoker set search_path=public,pg_temp as $$
declare h uuid; d public.receipt_drafts; e bigint; result jsonb; a numeric; dt date;
begin
 if p_user <= 0 then raise exception 'Invalid user'; end if;
 insert into public.households(owner_telegram_id) values(p_user)
 on conflict(owner_telegram_id) do nothing;
 select id into h from public.households where owner_telegram_id=p_user;
 if p_action='home' then
  if p_data ? 'name' then
   update public.households set name=trim(p_data->>'name') where id=h;
  end if;
  return (select to_jsonb(t) from public.households t where id=h);
 elsif p_action='draft' then
  insert into public.receipt_drafts(id,household_id,source_key,data)
  values((p_data->>'id')::uuid,h,p_data->>'source_key',p_data->'data')
  on conflict(id) do nothing;
  return jsonb_build_object('id',p_data->>'id');
 elsif p_action in ('get_draft','edit','confirm','cancel') then
  select * into d from public.receipt_drafts
   where id=(p_data->>'id')::uuid and household_id=h for update;
  if not found then raise exception 'Draft not found'; end if;
  if p_action='get_draft' then return to_jsonb(d); end if;
  if d.saved_expense_id is not null then
   return jsonb_build_object('saved',d.saved_expense_id);
  end if;
  if d.cancelled then raise exception 'Draft cancelled'; end if;
  if p_action='cancel' then
   update public.receipt_drafts set cancelled=true where id=d.id;
   return '{"cancelled":true}';
  elsif p_action='edit' then
   update public.receipt_drafts set data=data||(p_data->'patch') where id=d.id
   returning data into result;
   return result;
  else
   a=(d.data->>'amount')::numeric; dt=(d.data->>'date')::date;
   if a is null or a <= 0 or dt is null then raise exception 'Amount/date required'; end if;
   insert into public.expenses(household_id,source_key,amount,description,category,expense_date,items)
   values(h,d.source_key,a,d.data->>'description',coalesce(d.data->>'category','Outros'),dt,coalesce(d.data->'items','[]'))
   on conflict(household_id,source_key) do update set source_key=excluded.source_key
   returning id into e;
   update public.receipt_drafts set saved_expense_id=e where id=d.id;
   return jsonb_build_object('saved',e);
  end if;
 elsif p_action='list' then
  select coalesce(jsonb_agg(to_jsonb(t) order by t.expense_date desc,t.id desc),'[]') into result
  from (select id,amount,description,category,expense_date from public.expenses
   where household_id=h and expense_date >= (p_data->>'from')::date
    and expense_date < (p_data->>'until')::date order by expense_date desc,id desc limit 2000) t;
  return result;
 elsif p_action='delete' then
  update public.receipt_drafts set saved_expense_id=null,cancelled=true
   where household_id=h and saved_expense_id=(p_data->>'expense_id')::bigint;
  delete from public.expenses where household_id=h and id=(p_data->>'expense_id')::bigint returning id into e;
  return jsonb_build_object('deleted',e);
 end if;
 raise exception 'Unknown action';
end $$;
revoke all on function public.household_bot(bigint,text,jsonb) from public,anon,authenticated;
grant execute on function public.household_bot(bigint,text,jsonb) to service_role;
