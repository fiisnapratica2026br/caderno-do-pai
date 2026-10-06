CREATE OR REPLACE FUNCTION public.household_bot(p_user bigint, p_action text, p_data jsonb DEFAULT '{}'::jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare h uuid; result jsonb; d public.receipt_drafts; bill boolean; due date; paid date;
begin
 if p_user is null or p_user<=0 then raise exception 'Invalid user'; end if;
 perform public.household_bot_base(p_user,'home','{}');
 select id into h from public.households where owner_telegram_id=p_user for update;
 if p_action in ('confirm','check_duplicate') then
  select * into d from public.receipt_drafts where id=(p_data->>'id')::uuid and household_id=h for update;
  if not found then raise exception 'Draft not found'; end if;
  bill=coalesce(d.data->>'document_type','receipt')='bill';
  if bill and d.saved_expense_id is null and not d.cancelled then
   due=nullif(d.data->>'due_date','')::date;
   paid=nullif(d.data->>'payment_date','')::date;
   if p_action='confirm' and due is null then raise exception 'Due date required'; end if;
   if p_action='confirm' and d.data->>'payment_status'='paid' and (paid is null or paid>(now() at time zone 'America/Sao_Paulo')::date) then raise exception 'Payment date required'; end if;
   -- A emissão pode estar ausente, mas nunca é substituída pelo vencimento no campo documento.
   update public.receipt_drafts set data=data||jsonb_build_object('date',coalesce(nullif(data->>'document_date',''),due::text)) where id=d.id;
  end if;
  result=public.household_bot_base(p_user,p_action,p_data);
  if bill and d.saved_expense_id is null and result ? 'saved' then
   update public.expenses set document_type='bill',document_date=nullif(d.data->>'document_date','')::date,due_date=due,
    payment_status=case when d.data->>'payment_status'='paid' then 'paid' else 'pending' end,
    payment_date=case when d.data->>'payment_status'='paid' then paid else null end
    where id=(result->>'saved')::bigint and household_id=h;
  end if;
  return result;
 elsif p_action='list' then
  select coalesce(jsonb_agg(to_jsonb(t) order by t.expense_date desc,t.id desc),'[]') into result
  from(select * from public.expenses where household_id=h and
   (case when document_type='bill' then case when payment_status='paid' then payment_date else due_date end else expense_date end)>=(p_data->>'from')::date and
   (case when document_type='bill' then case when payment_status='paid' then payment_date else due_date end else expense_date end)<(p_data->>'until')::date
   order by expense_date desc,id desc limit 2000)t;
  return result;
 elsif p_action='bills' then
  select coalesce(jsonb_agg(to_jsonb(t) order by t.due_date,t.id),'[]') into result from
   (select * from public.expenses where household_id=h and payment_status='pending' order by due_date,id limit 100)t;
  return result;
 elsif p_action='convert_bill' then
  due=nullif(p_data->'patch'->>'due_date','')::date;
  if due is null then raise exception 'Due date required'; end if;
  update public.expenses set document_type='bill',document_date=null,due_date=due,
   payment_status='pending',payment_date=null
   where id=(p_data->>'expense_id')::bigint and household_id=h and document_type='receipt'
   returning to_jsonb(expenses) into result;
  return coalesce(result,'{"missing":true}');
 elsif p_action='update_bill' then
  if jsonb_typeof(p_data->'patch')<>'object' or (p_data->'patch')-array['document_date','due_date','payment_date','payment_status']<>'{}'::jsonb then raise exception 'Invalid patch'; end if;
  paid=nullif(p_data->'patch'->>'payment_date','')::date;
  if paid>(now() at time zone 'America/Sao_Paulo')::date then raise exception 'Future payment'; end if;
  update public.expenses set
   document_date=case when p_data->'patch' ? 'document_date' then nullif(p_data->'patch'->>'document_date','')::date else document_date end,
   due_date=case when p_data->'patch' ? 'due_date' then (p_data->'patch'->>'due_date')::date else due_date end,
   payment_date=case when p_data->'patch'->>'payment_status'='pending' then null when p_data->'patch' ? 'payment_date' then paid else payment_date end,
   payment_status=coalesce(p_data->'patch'->>'payment_status',payment_status)
   where id=(p_data->>'expense_id')::bigint and household_id=h and document_type='bill'
   returning to_jsonb(expenses) into result;
  return coalesce(result,'{"missing":true}');
 end if;
 return public.household_bot_base(p_user,p_action,p_data);
end $function$
