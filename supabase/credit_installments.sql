-- Mantém o fluxo anterior e acrescenta crédito sem criar um gasto total adicional.
alter table public.households add column if not exists credit_cards jsonb not null default '[]';
alter table public.expenses add column if not exists credit_group uuid;
alter table public.expenses add column if not exists card_name text;
alter table public.expenses add column if not exists installment_number integer;
alter table public.expenses add column if not exists installment_count integer;
alter table public.expenses add column if not exists purchase_total numeric(12,2);
create index if not exists expenses_credit_group_idx on public.expenses(household_id,credit_group);
alter function public.household_bot(bigint,text,jsonb) rename to household_bot_before_credit;
create function public.household_bot(p_user bigint,p_action text,p_data jsonb default '{}') returns jsonb
language plpgsql set search_path=public,pg_temp as $$
declare h uuid; d public.receipt_drafts; r jsonb; x public.expenses; n integer; i integer; cents bigint; part numeric;
 firstdate date; target date; g uuid; cname text; cfg jsonb; closing integer; due integer; paydate date;
begin
 if p_user is null or p_user<=0 then raise exception 'Invalid user'; end if;
 perform public.household_bot_before_credit(p_user,'home','{}');
 select id into h from public.households where owner_telegram_id=p_user for update;
 if p_action='cards' then
  return (select credit_cards from public.households where id=h);
 elsif p_action='set_card' then
  cname=trim(p_data->>'name');closing=(p_data->>'closing_day')::int;due=(p_data->>'due_day')::int;
  if cname is null or length(cname) not between 1 and 40 or closing not between 1 and 31 or due not between 1 and 31 then raise exception 'Invalid card';end if;
  select credit_cards into cfg from public.households where id=h;
  if jsonb_array_length(cfg)>=10 and not exists(select from jsonb_array_elements(cfg) c where c->>'name'=cname) then raise exception 'Card limit';end if;
  select coalesce(jsonb_agg(c),'[]') into cfg from jsonb_array_elements(cfg)c where c->>'name'<>cname;
  update public.households set credit_cards=cfg||jsonb_build_array(jsonb_build_object('name',cname,'closing_day',closing,'due_day',due)) where id=h;
  return jsonb_build_object('ok',true);
 elsif p_action='pay_invoice' then
  paydate=(p_data->>'payment_date')::date;firstdate=(p_data->>'due_date')::date;cname=p_data->>'card_name';
  if paydate is null or paydate>(now() at time zone 'America/Sao_Paulo')::date then raise exception 'Invalid payment';end if;
  update public.expenses set payment_status='paid',payment_date=paydate where household_id=h and card_name=cname and due_date=firstdate and payment_status='pending' and credit_group is not null;
  get diagnostics n=row_count;
  return jsonb_build_object('paid',n);
 elsif p_action='confirm' then
  select * into d from public.receipt_drafts where id=(p_data->>'id')::uuid and household_id=h for update;
  if not found then raise exception 'Draft not found';end if;
  if d.saved_expense_id is null and d.data->>'payment_method'='credit' then
   n=(d.data->>'installments')::integer;firstdate=(d.data->>'first_due')::date;cname=d.data->>'card_name';
   if n is null or n not between 1 and 36 or firstdate is null or nullif(d.data->>'date','') is null or firstdate<(d.data->>'date')::date then raise exception 'Invalid installment dates';end if;
   if not exists(select from public.households hh,jsonb_array_elements(hh.credit_cards)c where hh.id=h and c->>'name'=cname) then raise exception 'Card not found';end if;
   cents=round((d.data->>'amount')::numeric*100);
   if cents is null or cents<n then raise exception 'Amount too small';end if;
   if coalesce(d.data->>'document_type','receipt')<>'receipt' then raise exception 'Credit purchase must be receipt';end if;
   r=public.household_bot_before_credit(p_user,p_action,p_data);
   if not r ? 'saved' then return r;end if;
   select * into x from public.expenses where id=(r->>'saved')::bigint and household_id=h for update;
   if x.credit_group is not null then return r;end if;
   g=gen_random_uuid();
   for i in 1..n loop
    part=(cents/n + case when i<=cents%n then 1 else 0 end)::numeric/100;
    target=(date_trunc('month',firstdate)+(i-1)*interval '1 month')::date;
    target=target+(least(extract(day from firstdate)::int,extract(day from (target+interval '1 month - 1 day'))::int)-1);
    if i=1 then
     update public.expenses set amount=part,document_type='bill',document_date=x.expense_date,due_date=target,payment_status='pending',payment_date=null,
      credit_group=g,card_name=cname,installment_number=i,installment_count=n,purchase_total=x.amount where id=x.id;
    else
     insert into public.expenses(household_id,source_key,amount,description,category,expense_date,items,document_type,document_date,due_date,payment_status,credit_group,card_name,installment_number,installment_count,purchase_total)
     values(h,x.source_key||':installment:'||i,part,x.description,x.category,x.expense_date,'[]','bill',x.expense_date,target,'pending',g,cname,i,n,x.amount);
    end if;
   end loop;
   return r||jsonb_build_object('installments',n);
  end if;
 elsif p_action='update_expense' then
  if exists(select from public.expenses where household_id=h and id=(p_data->>'expense_id')::bigint and credit_group is not null) and (p_data->'patch' ? 'amount' or p_data->'patch' ? 'date') then raise exception 'Installment amount and purchase date locked';end if;
 end if;
 return public.household_bot_before_credit(p_user,p_action,p_data);
end $$;
revoke all on function public.household_bot(bigint,text,jsonb) from public,anon,authenticated;
grant execute on function public.household_bot(bigint,text,jsonb) to service_role;
