(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const tg = window.Telegram?.WebApp;
  const initData = tg?.initData || '';
  // Este endereço aponta para o servidor existente; não contém chave secreta.
  const apiBase = location.hostname.endsWith('.vercel.app') ? 'https://cadernodopai-bot.onrender.com' : '';
  const currency = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
  const localToday = () => new Intl.DateTimeFormat('sv-SE', { timeZone: 'America/Sao_Paulo', year:'numeric',month:'2-digit',day:'2-digit' }).format(new Date());
  const dateLabel = date => date ? date.split('-').reverse().join('/') : 'Não informada';
  let state = null, tab = 'month', busy = false, payId = null, invoiceTarget = null, revision = 0;
  function node(tag, text, cls) { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; }
  function status(text, error = false) { $('status').textContent = text; $('status').className = error ? 'error' : ''; }
  async function api(action, extra = {}) {
    const response = await fetch(apiBase + '/api/panel', { method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({initData, action, ...extra}) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Não consegui acessar os registros.');
    return result;
  }
  function setBusy(value) { busy=value; $('refresh').disabled=value; $('month').disabled=value; document.querySelectorAll('.delete,.pay').forEach(b => b.disabled=value); }
  function render() {
    $('home').textContent=state.home;
    $('paid').textContent=currency.format(Number(state.summary.recorded));
    $('pending').textContent=currency.format(Number(state.summary.pending));
    $('count').textContent=state.summary.count;
    $('limit').hidden=!state.limited;
    $('categories').replaceChildren();
    const cats=Object.entries(state.summary.categories).sort((a,b)=>Number(b[1])-Number(a[1]));
    for (const [name, amount] of cats) {
      const wrap=node('div'); const label=node('div',null,'category-label');
      label.append(node('span',name),node('span',currency.format(Number(amount))));
      const track=node('div',null,'track'),fill=node('div',null,'fill');
      fill.style.width=(Number(amount)/Math.max(Number(state.summary.recorded),.01)*100)+'%';
      track.append(fill);wrap.append(label,track);$('categories').append(wrap);
    }
    if (!cats.length) $('categories').append(node('p','Nenhuma compra ou conta paga neste mês.','empty'));
    renderRows();
  }
  function renderRows() {
    $('rows').replaceChildren();
    if (!state) return;
    const query=$('search').value.toLocaleLowerCase('pt-BR');
    const rows=(tab==='month'?state.rows:state.bills).filter(r => (r.description+' '+r.category).toLocaleLowerCase('pt-BR').includes(query));
    const invoices=new Set();
    for (const row of rows) {
      const wrap=node('article',null,'row'),info=node('div',null,'row-info'),actions=node('div',null,'row-actions');
      info.append(node('h3',row.description));
      if(row.credit_group){
        info.append(node('p',row.card_name+' · Parcela '+row.installment_number+'/'+row.installment_count+' · Compra total: '+currency.format(Number(row.purchase_total))));
        info.append(node('p','Compra: '+dateLabel(row.expense_date)));
      }
      const bill=row.document_type==='bill',pending=row.payment_status==='pending';
      const effective=bill?(pending?row.due_date:row.payment_date):row.expense_date;
      info.append(node('p', (bill?(pending?'Vencimento: ':'Pagamento: '):'Compra: ')+dateLabel(effective)+' · #'+row.id));
      info.append(node('span',row.category,'badge'));
      if (bill) {
        info.append(node('p','Emissão: '+dateLabel(row.document_date)+' · Vencimento: '+dateLabel(row.due_date)));
        const overdue=pending && row.due_date < localToday();
        info.append(node('span',pending?(overdue?'Atrasada':row.due_date===localToday()?'Vence hoje':'A pagar'):'Paga','badge '+(overdue?'overdue':pending?'pending':'')));
      }
      actions.append(node('strong',currency.format(Number(row.amount))));
      if (pending) {
        const button=node('button','Já paguei','pay');button.type='button';button.disabled=busy;
        button.addEventListener('click',()=>{payId=row.id;invoiceTarget=null;$('payment-description').textContent=row.description;$('payment-date').max=localToday();$('payment-date').value=localToday();$('payment-error').textContent='';$('payment-dialog').showModal();});actions.append(button);
      }
      if(pending && row.credit_group && !state.limited){
        const key=JSON.stringify([row.card_name,row.due_date]);
        if(!invoices.has(key)){
          invoices.add(key);
          const invoice=node('button','Paguei esta fatura','pay');invoice.type='button';invoice.disabled=busy;
          invoice.addEventListener('click',()=>{
            const entries=state.bills.filter(r=>r.credit_group && r.card_name===row.card_name && r.due_date===row.due_date);
            if(!confirm('Marcar como pagas todas as parcelas cadastradas de '+row.card_name+' com vencimento em '+dateLabel(row.due_date)+'?\n'+entries.length+' registros · '+currency.format(entries.reduce((a,r)=>a+Number(r.amount),0))+'\nSó inclui compras registradas aqui. Confira na fatura real.'))return;
            invoiceTarget={card_name:row.card_name,due_date:row.due_date};payId=null;
            $('payment-description').textContent='Fatura '+row.card_name+' · '+dateLabel(row.due_date);
            $('payment-date').max=localToday();$('payment-date').value=localToday();$('payment-error').textContent='';$('payment-dialog').showModal();
          });actions.append(invoice);
        }
      }
      const remove=node('button' ,'Excluir','delete');remove.type='button';remove.disabled=busy;remove.setAttribute('aria-label','Excluir '+row.description);
      remove.addEventListener('click',async()=>{
        if(busy || !confirm((row.credit_group?'Excluir apenas esta parcela? As demais permanecem.\n':'Excluir este registro?\n')+row.description+' · '+currency.format(Number(row.amount))+'\nEle também será removido do histórico do bot.'))return;
        setBusy(true);
        try { await api('delete',{id:row.id});await load(); } catch(error){status(error.message,true);}finally{setBusy(false);}
      });actions.append(remove);wrap.append(info,actions);$('rows').append(wrap);
    }
    if (!rows.length) $('rows').append(node('p',query?'Nenhum registro corresponde à busca.':tab==='bills'?'Nenhuma conta a pagar.':'Nenhum registro neste mês.','empty'));
  }
  async function load() {
    const request=++revision;status('Carregando os registros da sua casa…');$('content').hidden=true;setBusy(true);
    try {
      const result=await api('snapshot',{month:$('month').value});if(request!==revision)return;
      state=result;render();$('content').hidden=false;status('');
    } catch(error) { if(request===revision)status(error.message,true); }
    finally { if(request===revision)setBusy(false); }
  }
  $('payment-form').addEventListener('submit',async event=>{
    event.preventDefault();if(busy)return;setBusy(true);$('payment-submit').disabled=true;$('payment-cancel').disabled=true;
    try{await api(invoiceTarget?'pay_invoice':'pay',invoiceTarget?{...invoiceTarget,date:$('payment-date').value}:{id:payId,date:$('payment-date').value});$('payment-dialog').close();await load();}
    catch(error){$('payment-error').textContent=error.message;}
    finally{setBusy(false);$('payment-submit').disabled=false;$('payment-cancel').disabled=false;}
  });
  $('payment-cancel').addEventListener('click',()=>$('payment-dialog').close());
  $('refresh').addEventListener('click',load);$('month').addEventListener('change',load);$('search').addEventListener('input',renderRows);
  for(const name of ['month','bills'])$('tab-'+name).addEventListener('click',()=>{tab=name;for(const value of ['month','bills'])$('tab-'+value).classList.toggle('active',value===name);renderRows();});
  $('back').addEventListener('click',()=>tg?.close());
  $('month').value=localToday().slice(0,7);
  if(!initData){$('welcome').hidden=false;$('refresh').hidden=true;return;}
  tg.ready();tg.expand();$('dashboard').hidden=false;load();
})();
