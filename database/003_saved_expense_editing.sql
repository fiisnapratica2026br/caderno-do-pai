create or replace function public.household_bot(p_user bigint,p_action text,p_data jsonb default '{}')
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
 elsif p_action in ('get_expense','update_expense') then
  select to_jsonb(t) into result from public.expenses t
   where id=(p_data->>'expense_id')::bigint and household_id=h for update;
  if not found then return '{"missing":true}'; end if;
  if p_action='get_expense' then return result; end if;
  if jsonb_typeof(p_data->'patch') <> 'object'
    or not ((p_data->'patch') - array['amount','description','category','date']) = '{}'::jsonb
    then raise exception 'Invalid patch'; end if;
  update public.expenses set
   amount=case when p_data->'patch' ? 'amount' then (p_data->'patch'->>'amount')::numeric else amount end,
   description=case when p_data->'patch' ? 'description' then p_data->'patch'->>'description' else description end,
   category=case when p_data->'patch' ? 'category' then p_data->'patch'->>'category' else category end,
   expense_date=case when p_data->'patch' ? 'date' then (p_data->'patch'->>'date')::date else expense_date end
   where id=(p_data->>'expense_id')::bigint and household_id=h
   returning to_jsonb(expenses) into result;
  return result;
 elsif p_action='list' then
  select coalesce(jsonb_agg(to_jsonb(t) order by t.expense_date desc,t.id desc),'[]') into result
  from (select id,amount,description,category,expense_date,created_at from public.expenses
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
