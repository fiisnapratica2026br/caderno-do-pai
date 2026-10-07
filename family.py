"""Fluxo da casa: rascunhos persistentes, confirmação e exportação."""
import asyncio
import csv
import io
import os
import re
import uuid
from collections import defaultdict
from datetime import datetime, date
from decimal import Decimal
from zoneinfo import ZoneInfo
import requests
from categorization import suggest_category
from credit import first_due, installments
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import CommandHandler, MessageHandler, CallbackQueryHandler, filters

TZ = ZoneInfo("America/Sao_Paulo")
MENU = ReplyKeyboardMarkup([
    ["➕ Adicionar gasto", "📸 Enviar nota"],
    ["🧾 Adicionar conta a pagar"],
    ["📊 Resumo do mês", "📋 Histórico"],
    ["📥 Exportar planilha", "📄 Relatório PDF"],
    ["📆 Contas a pagar", "🏠 Minha casa"],
    ["💳 Meus cartões", "❓ Como usar"],
], resize_keyboard=True)

def expense_text(row):
    if row.get('credit_group'):
        return (f"💳 {row['card_name']} · Parcela {row['installment_number']}/{row['installment_count']} · #{row['id']}\n"
                f"{row['description']}\nParcela: {money(row['amount'])} · Compra total: {money(row['purchase_total'])}\n"
                f"Compra: {purchase_date(row['expense_date'])}\nVencimento: {purchase_date(row['due_date'])}\n"
                f"Situação: {'Paga' if row['payment_status']=='paid' else 'A pagar'}\n"
                f"Pagamento: {purchase_date(row.get('payment_date')) if row.get('payment_date') else 'Não informado'}\n📂 {row['category']}")
    if row.get("document_type") == "bill":
        return (f"🧾 Conta #{row['id']}\n\n🏪 {row['description']}\n💰 {money(row['amount'])}\n"
                f"📅 Documento: {purchase_date(row.get('document_date')) if row.get('document_date') else 'Não informada'}\n"
                f"📆 Vencimento: {purchase_date(row.get('due_date'))}\n"
                f"Situação: {'Paga' if row.get('payment_status') == 'paid' else 'A pagar'}\n"
                f"Pagamento: {purchase_date(row.get('payment_date')) if row.get('payment_date') else 'Não informado'}\n"
                f"📂 {row['category']}\n🕒 Lançado: {registration_date(row['created_at'])}")
    return (f"🧾 Gasto #{row['id']}\n\n"
            f"🏪 {row['description']}\n💰 {money(row['amount'])}\n"
            f"📅 Compra: {purchase_date(row['expense_date'])}\n"
            f"🕒 Lançado: {registration_date(row['created_at'])}\n"
            f"📂 {row['category']}")

def expense_buttons(identifier, row=None):
    if row and row.get('credit_group'):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton('Já paguei esta parcela', callback_data=f'billpay:{identifier}')],
            [InlineKeyboardButton('Corrigir vencimento desta parcela',callback_data=f'billdue:{identifier}')],
            [InlineKeyboardButton('Voltar para a pagar',callback_data=f'billpending:{identifier}')],
            [InlineKeyboardButton('Categoria',callback_data=f'expcategory:{identifier}')]])
    if row and row.get('document_type') == 'bill':
        return InlineKeyboardMarkup([
            [InlineKeyboardButton('Marcar como paga / corrigir pagamento', callback_data=f'billpay:{identifier}')],
            [InlineKeyboardButton('Vencimento', callback_data=f'billdue:{identifier}'), InlineKeyboardButton('Emissão / documento', callback_data=f'billdoc:{identifier}')],
            [InlineKeyboardButton('Voltar para a pagar', callback_data=f'billpending:{identifier}')],
            [InlineKeyboardButton('Valor', callback_data=f'expamount:{identifier}'),InlineKeyboardButton('Categoria',callback_data=f'expcategory:{identifier}')],
            [InlineKeyboardButton('Descrição',callback_data=f'expdescription:{identifier}')]])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Valor", callback_data=f"expamount:{identifier}"),
         InlineKeyboardButton("Data da compra", callback_data=f"expdate:{identifier}")],
        [InlineKeyboardButton("Descrição", callback_data=f"expdescription:{identifier}"),
         InlineKeyboardButton("Categoria", callback_data=f"expcategory:{identifier}")],
        [InlineKeyboardButton("Transformar em conta a pagar", callback_data=f"expbill:{identifier}")],
        [InlineKeyboardButton("Concluir edição", callback_data=f"expdone:{identifier}")],
    ])

async def show_expense(message, user, identifier):
    row = await api(user, "get_expense", {"expense_id": int(identifier)})
    if row.get("missing"):
        await message.reply_text("Gasto não encontrado nesta casa.")
        return
    await message.reply_text(expense_text(row), reply_markup=expense_buttons(identifier, row))


CATEGORIES = [
    "Água", "Energia elétrica", "Supermercado", "Combustível", "Moradia",
    "Internet e telefone", "Saúde", "Educação", "Transporte",
    "Casa e manutenção", "Impostos e taxas", "Lazer", "Vestuário", "Pets", "Outros", "Alimentação fora de casa", "Compras na internet",
]


def purchase_date(value):
    return date.fromisoformat(value).strftime("%d/%m/%Y") if value else "Corrija a data"


def registration_date(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TZ).strftime("%d/%m/%Y %H:%M")


def category_buttons(draft_id):
    choices = [InlineKeyboardButton(name, callback_data=f"catpick:{draft_id}:{i}")
               for i, name in enumerate(CATEGORIES)]
    return InlineKeyboardMarkup([choices[i:i+2] for i in range(0, len(choices), 2)])




def api_sync(user, action, data):
    response = requests.post(os.environ["HOUSEHOLD_API_URL"],
        headers={"x-bot-key": os.environ["HOUSEHOLD_API_KEY"]},
        json={"user": user, "action": action, "data": data}, timeout=30)
    response.raise_for_status()
    return response.json()


async def api(user, action, data=None):
    return await asyncio.to_thread(api_sync, user, action, data or {})


def money(value):
    return "R$ " + format(Decimal(str(value)), ",.2f").replace(",", "_").replace(".", ",").replace("_", ".")


def parse_amount(value):
    value = value.strip().replace("R$", "").replace(" ", "")
    if not re.fullmatch(r"(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,2})?|\d+\.\d{1,2}", value):
        raise ValueError("Valor inválido")
    amount = Decimal(value.replace(".", "").replace(",", ".")) if "," in value else Decimal(value)
    if not Decimal("0") < amount <= Decimal("9999999999.99"):
        raise ValueError("Valor inválido")
    return format(amount.quantize(Decimal("0.01")), "f")


def installment_buttons(draft_id):
    choices=[InlineKeyboardButton(f'{n}x' + (' (à vista)' if n==1 else ''),callback_data=f'creditqty:{draft_id}:{n}') for n in (1,2,3,4,5,6,10,12)]
    return InlineKeyboardMarkup([choices[i:i+3] for i in range(0,len(choices),3)]+[[InlineKeyboardButton('Outra quantidade',callback_data='installments:'+draft_id)]])


def parse_installment_count(value):
    match=re.fullmatch(r'\s*(\d{1,2})\s*(?:x|parcelas?)?\s*',value,re.I)
    if not match or not 1<=int(match[1])<=36:raise ValueError('Parcelas inválidas')
    return int(match[1])


def buttons(draft_id, data=None):
    if data and data.get('payment_method')=='credit':
        return InlineKeyboardMarkup([
            [InlineKeyboardButton('Salvar parcelas',callback_data='save:'+draft_id),InlineKeyboardButton('Cancelar',callback_data='cancel:'+draft_id)],
            [InlineKeyboardButton('Valor total',callback_data='amount:'+draft_id),InlineKeyboardButton('Data da compra',callback_data='date:'+draft_id)],
            [InlineKeyboardButton('Quantidade de parcelas',callback_data='installments:'+draft_id),InlineKeyboardButton('Primeiro vencimento',callback_data='first_due:'+draft_id)],
            [InlineKeyboardButton('Escolher cartão',callback_data='credit:'+draft_id),InlineKeyboardButton('Categoria',callback_data='category:'+draft_id)],
            [InlineKeyboardButton('Não foi no crédito',callback_data='nocredit:'+draft_id)]])
    if data and data.get('document_type') == 'bill':
        return InlineKeyboardMarkup([
            [InlineKeyboardButton('Salvar conta',callback_data='save:'+draft_id),InlineKeyboardButton('Cancelar',callback_data='cancel:'+draft_id)],
            [InlineKeyboardButton('Valor',callback_data='amount:'+draft_id),InlineKeyboardButton('Vencimento',callback_data='due_date:'+draft_id)],
            [InlineKeyboardButton('Emissão / documento',callback_data='document_date:'+draft_id),InlineKeyboardButton('Categoria',callback_data='category:'+draft_id)],
            [InlineKeyboardButton('Já paguei / data do pagamento',callback_data='payment_date:'+draft_id)],
            [InlineKeyboardButton('Ainda está a pagar',callback_data='unpaid:'+draft_id)],
            [InlineKeyboardButton('Descrição',callback_data='description:'+draft_id)],
            [InlineKeyboardButton('É uma compra',callback_data='asreceipt:'+draft_id)]])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Salvar gasto", callback_data="save:"+draft_id),
         InlineKeyboardButton("Cancelar", callback_data="cancel:"+draft_id)],
        [InlineKeyboardButton("Corrigir valor", callback_data="amount:"+draft_id),
         InlineKeyboardButton("Data da compra", callback_data="date:"+draft_id)],
        [InlineKeyboardButton("Editar descrição", callback_data="description:"+draft_id),
         InlineKeyboardButton("Categoria", callback_data="category:"+draft_id)],
        [InlineKeyboardButton("É uma conta a pagar", callback_data="asbill:"+draft_id)],
        [InlineKeyboardButton("💳 Compra no cartão de crédito",callback_data="credit:"+draft_id)],
    ])


async def private(update):
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text("Use este bot em uma conversa privada para proteger os gastos da casa.")
        return False
    return True


def draft_text(data):
    if data.get('payment_method')=='credit':
        count=data.get('installments',1)
        values=installments(data['amount'],count) if data.get('amount') else []
        schedule=(f"{count}x · primeira {money(values[0])}"+(f" · última {money(values[-1])}" if values and values[-1]!=values[0] else '')) if values else 'Corrija o valor total'
        return (f"💳 Confira a compra no crédito\n\n{data.get('description')}\n"
                f"Total da compra: {money(data['amount']) if data.get('amount') else 'Não informado'}\n"
                f"Cartão: {data.get('card_name')}\nCompra: {purchase_date(data.get('date'))}\n"
                f"{schedule}\nPrimeiro vencimento: {purchase_date(data.get('first_due'))}\n"
                f"📂 {data.get('category','Outros')}\n\nTodas as parcelas começam a pagar. Confira a primeira fatura: compras no dia do fechamento podem entrar no próximo mês. Valor total deve incluir eventuais juros. A compra não será somada novamente às parcelas.")
    if data.get('document_type') == 'bill':
        return ("🧾 Confira a conta antes de salvar\n\n"
            f"🏪 {data.get('description')}\n💰 {money(data['amount']) if data.get('amount') else 'Corrija o valor'}\n"
            f"📅 Emissão / documento: {purchase_date(data.get('document_date')) if data.get('document_date') else 'Não identificada'}\n"
            f"📆 Vencimento: {purchase_date(data.get('due_date'))}\n"
            f"Situação: {'Paga' if data.get('payment_status') == 'paid' else 'A pagar'}\n"
            f"Pagamento: {purchase_date(data.get('payment_date')) if data.get('payment_date') else 'Não informado'}\n"
            f"📂 {data.get('category', 'Outros')}\n\n"
            "Confira o vencimento. A conta só fica paga quando você informar a data do pagamento.")
    return (
        "🧾 Confira antes de salvar\n\n"
        f"🏪 {data.get('description') or 'Descrição não identificada'}\n"
        f"💰 {money(data['amount']) if data.get('amount') else 'Corrija o valor'}\n"
        f"📅 {data.get('date_label', 'Data da compra')}: {purchase_date(data.get('date'))}\n"
        f"📂 {data.get('category', 'Outros')}"
        + (" (sugerida — confira)" if data.get("category_auto") and data.get("category") != "Outros" else "")
        + ("\n📆 Vencimento: " + data["due_date"] if data.get("due_date") else "")
        + "\n\n"
        + ("Confira o total da fatura e a data do documento. Vencimento não significa pagamento."
           if data.get("document_type") == "bill" else
           "Os preços dos produtos podem conter erros de leitura. Este registro salva o total da compra para a casa.")
    )


def duplicate_text(rows):
    lines = [
        f"#{r['id']} · {r['description'][:80]} · {money(r['amount'])} · {purchase_date(r['expense_date'])}"
        for r in rows
    ]
    return ("⚠️ Possível gasto duplicado\n\n"
            "Encontrei um gasto que pode corresponder à mesma nota ou compra:\n"
            + "\n".join(lines)
            + "\n\nSe for o mesmo gasto, cancele. Se for outra compra, confirme abaixo.")


async def propose(update, context, data, source_key, details=None):
    if not await private(update):
        return
    context.user_data.pop('waiting_credit',None)
    data = dict(data)
    if data.get('document_type') == 'bill':
        data['document_date'] = None if source_key.startswith('text:') else data.get('date')
        if data.get('due_date'):
            data['due_date'] = datetime.strptime(data['due_date'], '%d/%m/%Y').date().isoformat()
        data['payment_status'] = 'pending'
        data['payment_date'] = None
    if data.get("category", "Outros") == "Outros":
        data["category"] = suggest_category(data.get("description", ""), data.get("items"), data.get("amount"))
        data["category_auto"] = True
    draft_id = str(uuid.uuid4())
    await api(update.effective_user.id, "draft",
        {"id": draft_id, "source_key": source_key, "data": data})
    check = await api(update.effective_user.id, "check_duplicate", {"id": draft_id})
    preview = draft_text(data)
    if check.get("duplicates"):
        preview += "\n\n" + duplicate_text(check["duplicates"])
    await update.effective_message.reply_text(preview, reply_markup=buttons(draft_id, data))
    if details:
        # Respeita o limite de caracteres das mensagens do Telegram.
        for offset in range(0, len(details), 3500):
            await update.effective_message.reply_text(details[offset:offset+3500])


HELP_TEXT = """❓ Como usar o controle da casa

🛒 Registrar uma compra
Toque em “Adicionar gasto” ou escreva: mercado 149,95.
Também pode enviar uma foto da nota. Confira valor, descrição, categoria e data da compra antes de salvar. A leitura da foto pode errar.

🧾 Registrar uma conta a pagar
Toque em “Adicionar conta a pagar” e escreva: internet 59,90.
Informe o vencimento e, se souber, a emissão. Salve como “A pagar”. No rascunho, também pode trocar uma compra para conta usando “É uma conta a pagar”.

📅 Entenda as datas
Compra: dia em que comprou.
Emissão: dia em que a conta foi emitida; pode ficar sem informação.
Vencimento: prazo para pagar.
Pagamento: dia em que realmente pagou.
O sistema guarda automaticamente quando você fez o lançamento.

⏰ Conta atrasada
Uma conta vencida em 02/10 e lançada em 06/10 fica “A pagar” e “Atrasada”. O vencimento continua 02/10. Se pagar em 06/10, informe essa data como pagamento.

✅ Pagar uma conta
Abra /contas ou /painel, escolha a conta e toque em “Já paguei” ou “Marcar como paga”. Informe o dia real do pagamento. Não use data futura.

✏️ Corrigir um lançamento
Use /historico para encontrar o número do registro. Exemplo: /editar 123.
Você pode corrigir valor, descrição, categoria e datas. Se salvou uma conta como compra, toque em “Transformar em conta a pagar” e informe o vencimento. Isso altera o mesmo registro, sem duplicar.

🗑 Excluir
Exemplo: /excluir 123. Confira e confirme a exclusão. Ela também remove o registro do painel.
Use /cancelar para sair de uma edição antes de enviar a alteração.

📊 Consultar e exportar
/painel abre o painel dentro do Telegram.
/contas mostra pendências, inclusive de outros meses.
/resumo e /historico consultam o mês atual.
Para outro mês: /resumo 09/2026.
/planilha, /csv e /relatorio exportam os registros.
No resumo, compras entram pela data da compra; contas pagas pelo pagamento; pendentes pelo vencimento.

🏠 Uso da família
Nesta versão, uma conta do Telegram centraliza os lançamentos da casa. Os registros ficam no banco do serviço, não só no celular.
💳 Cartão de crédito
Cadastre: /cartao Meu cartão | 22 | 28 (fechamento e vencimento).
No rascunho da compra, toque em “Compra no cartão de crédito”, escolha o cartão e informe as parcelas (1 para crédito à vista). Confira o primeiro vencimento na sua fatura. Compras no dia do fechamento são sugeridas para o próximo ciclo.
As parcelas entram como contas a pagar. No painel, “Paguei esta fatura” quita apenas as parcelas cadastradas daquele cartão e vencimento. O app não consulta o banco. Não lance a mesma fatura novamente como outro gasto.
Contas mensais precisam ser lançadas a cada mês. Ainda não há repetição automática nem lembretes de vencimento.
"""

async def help_user(update, context):
    if not await private(update):
        return
    await update.effective_message.reply_text(HELP_TEXT, reply_markup=MENU)


async def cards_user(update, context):
    if not await private(update):return
    context.user_data.pop('editing',None)
    cards=await api(update.effective_user.id,'cards')
    args=' '.join(context.args or [])
    if args:
        try:
            name,closing,due=[x.strip() for x in args.split('|')]
            closing,due=int(closing),int(due)
            if not name or len(name)>40 or not 1<=closing<=31 or not 1<=due<=31:raise ValueError()
        except ValueError:
            await update.effective_message.reply_text('Use /cartao Nome | fechamento | vencimento\nExemplo: /cartao Meu cartão | 22 | 28\nNão envie número, senha ou código do cartão.');return
        await api(update.effective_user.id,'set_card',{'name':name,'closing_day':closing,'due_day':due})
        cards=await api(update.effective_user.id,'cards')
    lines=[f"💳 {c['name']} · fecha dia {c['closing_day']} · vence dia {c['due_day']}" for c in cards]
    await update.effective_message.reply_text(('\n'.join(lines) if lines else 'Nenhum cartão cadastrado.')+'\n\nCadastre ou atualize: /cartao Meu cartão | 22 | 28\nUse apenas um apelido; não envie dados do cartão. Alterar o cadastro não muda parcelas já salvas.',reply_markup=MENU)


async def home(update, context):
    context.user_data.pop('waiting_credit',None)
    context.user_data.pop('editing',None)
    if not await private(update):
        return
    name = " ".join(context.args).strip()
    result = await api(update.effective_user.id, "home", {"name": name} if name else {})
    await update.message.reply_text(
        f"🏠 {result['name']}\n\n"
        "Controle gratuito dos gastos da casa.\n"
        "Envie foto da nota ou escreva: mercado 149,95.\n"
        "Confira e toque em Salvar gasto.\n\n"
        "/casa Família do Anderson — nome da casa\n"
        "/resumo — total e categorias do mês\n"
        "/historico — últimos gastos do mês\n"
        "/planilha — baixar Excel com resumo e gastos\n"
        "/relatorio — relatório do mês em PDF\n"
        "/csv — exportação simples\n"
        "/contas — contas a pagar, inclusive atrasadas\n"
        "/painel — abrir o painel visual da casa\n"
        "/ajuda — guia de uso com exemplos\n"
        "/cartao — cadastrar fechamento e vencimento\n"
        "/editar 123 — corrigir um gasto salvo\n"
        "/excluir 123 — excluir um gasto pelo número\n"
        "Para outro mês: /resumo 09/2026 ou /planilha 09/2026.\n\n"
        "Uma conta do Telegram centraliza os gastos da família nesta versão. "
        "Não pedimos acesso ao banco. As fotos são enviadas ao serviço de OCR para leitura.",
        reply_markup=MENU
    )


async def handle_text(update, context):
    if not await private(update):
        return
    value = update.message.text.strip()
    if value == "💳 Meus cartões":
        context.args=[]
        await cards_user(update,context)
        return
    if value == "❓ Como usar":
        await help_user(update, context)
        return
    if value in ("📊 Resumo do mês", "📋 Histórico", "📥 Exportar planilha", "📄 Relatório PDF"):
        context.user_data.pop("editing", None)
        context.args = []
        await report(update, context)
        return
    if value == '📆 Contas a pagar':
        await bills(update, context)
        return
    if value == "🏠 Minha casa":
        context.user_data.pop("editing", None)
        context.args = []
        await home(update, context)
        return
    if value in ("➕ Adicionar gasto", "📸 Enviar nota", "🧾 Adicionar conta a pagar"):
        context.user_data.pop("waiting_credit",None)
        context.user_data["new_bill"] = value == "🧾 Adicionar conta a pagar"
        context.user_data.pop("editing", None)
        await update.message.reply_text(
            "Escreva a descrição e o valor. Exemplo: cachorro quente do Bidjula 15,00. Depois escolha compra ou conta a pagar e confira as datas e a categoria antes de salvar."
            if value in ("➕ Adicionar gasto", "🧾 Adicionar conta a pagar") else
            "Envie uma foto nítida da nota inteira, com boa iluminação. Depois confira os dados antes de salvar.",
            reply_markup=MENU)
        return
    quantity_text=re.fullmatch(r'\s*\d{1,2}\s*(?:x|parcelas?)\s*',value,re.I)
    waiting=context.user_data.get('waiting_credit')
    if waiting and re.fullmatch(r'\s*\d{1,2}\s*(?:x|parcelas?)?\s*',value,re.I):
        try:waiting['count']=parse_installment_count(value)
        except ValueError:
            await update.message.reply_text('Escolha de 1 a 36 parcelas.');return
        cards=await api(update.effective_user.id,'cards')
        await update.message.reply_text(f"Entendi: {waiting['count']} parcelas. Toque no nome do cartão abaixo para aplicar à compra original; nenhum gasto novo foi criado.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(c['name'],callback_data=f"pickcard:{waiting['id']}:{i}")] for i,c in enumerate(cards)]))
        return
    edit = context.user_data.get("editing")
    if quantity_text and (not edit or edit[0]!='installments'):
        await update.message.reply_text('Essa mensagem indica parcelas, não um gasto novo. Volte à nota original, toque em Compra no cartão de crédito e depois no nome do cartão.');return
    if edit:
        field, draft_id = edit
        try:
            if field == "installments":
                parsed=parse_installment_count(value)
            elif field == "amount":
                parsed = parse_amount(value)
            elif field in ("date", "due_date", "document_date", "payment_date", "convert_due", "first_due"):
                parsed_date = datetime.strptime(value, "%d/%m/%Y").date()
                if field == 'payment_date' and parsed_date > datetime.now(TZ).date():
                    raise ValueError('Pagamento futuro')
                parsed = parsed_date.isoformat()
            else:
                maximum = 250 if field == "description" else 60
                if not 1 <= len(value) <= maximum:
                    raise ValueError("Texto muito longo")
                parsed = value
        except ValueError:
            await update.message.reply_text("Formato inválido. Use 149,95 para valor ou 03/10/2026 para data.")
            return
        if draft_id.startswith("expense:"):
            identifier = draft_id.split(":", 1)[1]
            patch = {field: parsed}
            action = 'convert_bill' if field == 'convert_due' else ('update_bill' if field in ('document_date','due_date','payment_date') else 'update_expense')
            if field == 'convert_due': patch = {'due_date': parsed}
            if field == 'payment_date': patch['payment_status'] = 'paid'
            result = await api(update.effective_user.id, action,
                {'expense_id': int(identifier), 'patch': patch})
            context.user_data.pop("editing", None)
            if result.get("missing"):
                await update.message.reply_text("Gasto não encontrado nesta casa.")
            else:
                await update.message.reply_text("✅ Alteração salva.\n\n" + expense_text(result),
                    reply_markup=expense_buttons(identifier, result))
            return
        patch = {field: parsed}
        if field in ('date','first_due','installments','amount'):
            current=await api(update.effective_user.id,'get_draft',{'id':draft_id})
            current=current.get('data',{})
            if current.get('payment_method')=='credit':
                count=parsed if field=='installments' else current.get('installments',1)
                amount=parsed if field=='amount' else current.get('amount')
                try:
                    if amount:installments(amount,count)
                    if field=='first_due' and parsed<current.get('date',''):raise ValueError()
                except ValueError:
                    await update.message.reply_text('Confira valor, quantidade de parcelas e vencimento após a compra.');return
                if field=='date':
                    cards=await api(update.effective_user.id,'cards')
                    card=next((c for c in cards if c['name']==current.get('card_name')),None)
                    if card:patch['first_due']=first_due(date.fromisoformat(parsed),card['closing_day'],card['due_day']).isoformat()
        if field == 'payment_date': patch['payment_status'] = 'paid'
        if field == "description":
            draft = await api(update.effective_user.id, "get_draft", {"id": draft_id})
            data = draft.get("data", {})
            if data.get("category_auto", data.get("category", "Outros") == "Outros"):
                patch["category"] = suggest_category(parsed, data.get("items"), data.get("amount"))
                patch["category_auto"] = True
        result = await api(update.effective_user.id, "edit",
            {"id": draft_id, "patch": patch})
        context.user_data.pop("editing", None)
        if "saved" in result:
            await update.message.reply_text("Esse gasto já foi salvo; crie um novo registro para outra compra.")
        else:
            await update.message.reply_text(draft_text(result), reply_markup=buttons(draft_id, result))
        return
    match = re.fullmatch(r"(.+?)\s+(\d[\d.,]*)", value)
    if not match:
        match2 = re.fullmatch(r"(?:gastei\s+)?(\d[\d.,]*)\s+(?:em\s+|no\s+|na\s+)?(.+)", value, re.I)
        if match2:
            description, amount = match2[2], match2[1]
        else:
            await update.message.reply_text("Envie uma foto ou escreva: mercado 149,95. Use /start para ver as opções.")
            return
    else:
        description, amount = match[1], match[2]
    try:
        parsed = parse_amount(amount)
    except ValueError:
        await update.message.reply_text("Não reconheci o valor. Exemplo: mercado 149,95")
        return
    if len(description) > 250:
        await update.message.reply_text("Use uma descrição de até 250 caracteres.")
        return
    await propose(update, context, {"description": description, "amount": parsed,
        "document_type": "bill" if context.user_data.pop("new_bill", False) else "receipt",
        "date": datetime.now(TZ).date().isoformat(), "category": "Outros", "items": []},
        f"text:{update.effective_chat.id}:{update.message.message_id}")


async def callback(update, context):
    q = update.callback_query
    await q.answer()
    if not await private(update):
        return
    action, identifier = q.data.split(":", 1)
    user = update.effective_user.id
    if action == 'creditqty':
        identifier,value=identifier.rsplit(':',1)
        try:count=parse_installment_count(value)
        except ValueError:return
        draft=await api(user,'get_draft',{'id':identifier})
        if draft.get('saved_expense_id') or draft.get('cancelled'):
            await q.message.reply_text('Esse registro já foi concluído.');return
        data=draft['data']
        if data.get('payment_method')!='credit':
            await q.message.reply_text('Escolha primeiro o cartão desta compra.');return
        try:
            if data.get('amount'):installments(data['amount'],count)
        except ValueError:
            await q.message.reply_text('O valor total é pequeno para essa quantidade de parcelas.');return
        result=await api(user,'edit',{'id':identifier,'patch':{'installments':count}})
        context.user_data.pop('editing',None)
        context.user_data.pop('waiting_credit',None)
        if 'saved' in result:await q.message.reply_text('Esse registro já foi salvo.')
        else:await q.edit_message_text(draft_text(result),reply_markup=buttons(identifier,result))
        return
    if action == 'credit':
        draft=await api(user,'get_draft',{'id':identifier})
        if draft.get('saved_expense_id') or draft.get('cancelled'):
            await q.message.reply_text('Esse registro já foi concluído.');return
        if draft['data'].get('document_type')=='bill':
            await q.message.reply_text('Use crédito para uma compra. Uma fatura de cartão não deve ser cadastrada junto com as compras que ela já contém.');return
        cards=await api(user,'cards')
        if not cards:
            await q.message.reply_text('Cadastre primeiro: /cartao Meu cartão | 22 | 28\nDepois toque novamente em Compra no cartão de crédito. Não envie dados do cartão.');return
        context.user_data.pop('editing',None)
        context.user_data['waiting_credit']={'id':identifier}
        await q.message.reply_text('Toque no nome do cartão abaixo. Depois escolha as parcelas:',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(c['name'],callback_data=f'pickcard:{identifier}:{i}')] for i,c in enumerate(cards)]));return
    if action == 'pickcard':
        identifier,index=identifier.rsplit(':',1)
        draft=await api(user,'get_draft',{'id':identifier})
        if draft.get('saved_expense_id') or draft.get('cancelled'):
            await q.message.reply_text('Esse registro já foi concluído.');return
        cards=await api(user,'cards')
        if not index.isdigit() or int(index)>=len(cards):return
        card=cards[int(index)];data=draft['data']
        if not data.get('date'):
            await q.message.reply_text('Informe primeiro a data da compra no rascunho.');return
        waiting=context.user_data.pop('waiting_credit',{})
        count=waiting.get('count',1) if waiting.get('id')==identifier else 1
        try:
            if data.get('amount'):installments(data['amount'],count)
        except ValueError:
            await q.message.reply_text('Confira o valor total e a quantidade de parcelas.');return
        result=await api(user,'edit',{'id':identifier,'patch':{'payment_method':'credit','card_name':card['name'],'installments':count,
            'first_due':first_due(date.fromisoformat(data['date']),card['closing_day'],card['due_day']).isoformat()}})
        context.user_data['editing']=('installments',identifier)
        await q.message.reply_text(draft_text(result),reply_markup=buttons(identifier,result))
        await q.message.reply_text('Em quantas parcelas? Toque em uma opção abaixo ou escreva, por exemplo, 3 parcelas. Depois confira e toque em Salvar parcelas.',reply_markup=installment_buttons(identifier));return
    if action == 'nocredit':
        context.user_data.pop('waiting_credit',None)
        result=await api(user,'edit',{'id':identifier,'patch':{'payment_method':None,'card_name':None,'installments':None,'first_due':None}})
        context.user_data.pop('editing',None)
        if 'saved' in result:await q.message.reply_text('Esse registro já foi salvo.')
        else:await q.edit_message_text(draft_text(result),reply_markup=buttons(identifier,result))
        return
    if action == 'expbill':
        row = await api(user, 'get_expense', {'expense_id': int(identifier)})
        if row.get('missing') or row.get('document_type') == 'bill':
            await q.message.reply_text('Use a edição da conta existente.'); return
        context.user_data['editing'] = ('convert_due', 'expense:' + identifier)
        await q.message.reply_text('Qual é o vencimento? Digite DD/MM/AAAA. Ao enviar, este mesmo registro vira conta a pagar, sem duplicar. A antiga data da compra não será tratada como emissão. /cancelar para desistir.'); return
    if action in ('asbill', 'asreceipt'):
        draft = await api(user, 'get_draft', {'id': identifier})
        if draft.get('saved_expense_id') or draft.get('cancelled'):
            await q.message.reply_text('Esse registro já foi concluído.'); return
        data = draft['data']
        patch = {'payment_method': None, 'document_type': 'bill', 'document_date': None, 'due_date': None,
                 'payment_status': 'pending', 'payment_date': None} if action == 'asbill' else {
                 'payment_method': None, 'document_type': 'receipt', 'document_date': None, 'due_date': None,
                 'payment_status': 'recorded', 'payment_date': None}
        result = await api(user, 'edit', {'id': identifier, 'patch': patch})
        context.user_data.pop('editing', None)
        await q.edit_message_text(draft_text(result), reply_markup=buttons(identifier, result))
        return
    if action in ('billpay','billdue','billdoc','billpending'):
        row = await api(user, 'get_expense', {'expense_id':int(identifier)})
        if row.get('missing') or row.get('document_type') != 'bill':
            await q.message.reply_text('Conta não encontrada nesta casa.'); return
        if action == 'billpending':
            context.user_data.pop('editing',None)
            result = await api(user,'update_bill',{'expense_id':int(identifier),'patch':{'payment_status':'pending'}})
            await q.message.reply_text(expense_text(result),reply_markup=expense_buttons(identifier,result)); return
        field = {'billpay':'payment_date','billdue':'due_date','billdoc':'document_date'}[action]
        context.user_data['editing']=(field,'expense:'+identifier)
        await q.message.reply_text('Digite a data em DD/MM/AAAA. Para pagamento, informe o dia em que pagou. A alteração será salva ao enviar. /cancelar para desistir.');return
    if action == "expense":
        context.user_data.pop("editing", None)
        await show_expense(q.message, user, identifier)
        return
    if action == "expdone":
        context.user_data.pop("editing", None)
        await q.edit_message_reply_markup(reply_markup=None)
        await q.message.reply_text("Edição concluída. As alterações foram salvas.", reply_markup=MENU)
        return
    if action == "expcat":
        identifier, index = identifier.rsplit(":", 1)
        if not index.isdigit() or not 0 <= int(index) < len(CATEGORIES):
            return
        result = await api(user, "update_expense", {"expense_id": int(identifier),
            "patch": {"category": CATEGORIES[int(index)]}})
        context.user_data.pop("editing", None)
        await q.edit_message_text("Gasto não encontrado nesta casa." if result.get("missing")
            else "✅ Categoria salva.\n\n" + expense_text(result),
            reply_markup=None if result.get("missing") else expense_buttons(identifier, result))
        return
    if action in ("expamount", "expdate", "expdescription", "expcategory"):
        row = await api(user, "get_expense", {"expense_id": int(identifier)})
        if row.get("missing"):
            await q.message.reply_text("Gasto não encontrado nesta casa.")
            return
        field = action[3:]
        if field == "category":
            context.user_data.pop("editing", None)
            choices = [InlineKeyboardButton(name, callback_data=f"expcat:{identifier}:{i}")
                       for i, name in enumerate(CATEGORIES)]
            await q.message.reply_text("Escolha a categoria. Ela será salva ao tocar:",
                reply_markup=InlineKeyboardMarkup([choices[i:i+2] for i in range(0, len(choices), 2)]))
            return
        context.user_data["editing"] = (field, "expense:" + identifier)
        prompts = {"amount": "Digite o novo valor. Exemplo: 149,95",
                   "date": "Digite a data real da compra. Exemplo: 03/05/2026",
                   "description": "Digite a nova descrição, com até 250 caracteres."}
        await q.message.reply_text(prompts[field] + "\nA alteração será salva ao enviar. Use /cancelar para desistir.")
        return
    if action == "catpick":
        draft_id, index = identifier.rsplit(":", 1)
        if not index.isdigit() or not 0 <= int(index) < len(CATEGORIES):
            return
        result = await api(user, "edit", {"id": draft_id,
            "patch": {"category": CATEGORIES[int(index)], "category_auto": False}})
        context.user_data.pop("editing", None)
        if "saved" in result:
            await q.message.reply_text("Esse gasto já foi salvo.")
        else:
            await q.edit_message_text(draft_text(result), reply_markup=buttons(draft_id, result))
        return
    if action == "askdelete":
        await q.message.reply_text(f"Excluir o gasto #{identifier} desta casa?",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Excluir", callback_data="delete:"+identifier),
                InlineKeyboardButton("Manter", callback_data="keep:"+identifier)]]))
        return
    if action == "delete":
        result = await api(user, "delete", {"expense_id": int(identifier)})
        await q.edit_message_text("Gasto excluído." if result.get("deleted") else "Gasto não encontrado nesta casa.")
        return
    if action == "keep":
        await q.edit_message_text("Exclusão cancelada.")
        return
    if action == "back":
        draft = await api(user, "get_draft", {"id": identifier})
        context.user_data.pop("editing", None)
        if draft.get("saved_expense_id") or draft.get("cancelled"):
            await q.message.reply_text("Esse registro já foi concluído.")
        else:
            await q.edit_message_text(draft_text(draft["data"]), reply_markup=buttons(identifier, draft["data"]))
        return
    if action in ("save", "cancel", "force"):
        payload = {"id": identifier}
        if action == "force":
            identifier, token = identifier.rsplit(":", 1)
            payload = {"id": identifier, "duplicate_token": token}
        if action != 'cancel':
            draft = await api(user,'get_draft',{'id':identifier})
            data = draft.get('data',{})
            if context.user_data.get('waiting_credit',{}).get('id')==identifier:
                await q.message.reply_text('Toque no nome do cartão para concluir a escolha antes de salvar esta compra.');return
            if data.get('payment_method')=='credit' and not draft.get('saved_expense_id'):
                try:
                    installments(data['amount'],data.get('installments',1))
                    if not data.get('date') or not data.get('first_due') or data['first_due']<data['date']:raise ValueError()
                except (ValueError,KeyError,TypeError):
                    await q.message.reply_text('Confira o valor total, a data da compra, as parcelas e o primeiro vencimento antes de salvar.');return
            if data.get('document_type') == 'bill' and not draft.get('saved_expense_id'):
                if not data.get('amount') or not data.get('due_date'):
                    await q.message.reply_text('Informe o valor e o vencimento antes de salvar a conta.');return
        result = await api(user, "cancel" if action == "cancel" else "confirm", payload)
        if result.get("duplicates"):
            await q.edit_message_text(duplicate_text(result["duplicates"]),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Cancelar este lançamento", callback_data="cancel:"+identifier)],
                    [InlineKeyboardButton("É outra compra, salvar", callback_data=f"force:{identifier}:{result['duplicate_token']}")],
                    [InlineKeyboardButton("Voltar e corrigir", callback_data="back:"+identifier)],
                ]))
            context.user_data.pop("editing", None)
            return
        context.user_data.pop("editing", None)
        await q.edit_message_text(
            f"✅ Registro #{result['saved']} salvo para a casa." + (f"\n{result['installments']} parcela(s) criada(s) a pagar. Confira /contas ou /painel." if result.get("installments") else "\nUse o menu para consultar os gastos e contas.")
            if result.get("saved") else "Registro cancelado.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Editar gasto",
                callback_data=f"expense:{result['saved']}")]]) if result.get("saved") else None)
        return
    if action == 'unpaid':
        result=await api(user,'edit',{'id':identifier,'patch':{'payment_status':'pending','payment_date':None}})
        if 'saved' in result: await q.message.reply_text('Esse registro já foi salvo.')
        else: await q.edit_message_text(draft_text(result),reply_markup=buttons(identifier,result))
        return
    if action not in ("amount", "date", "description", "category", "due_date", "document_date", "payment_date", "installments", "first_due"):
        return
    draft = await api(user, "get_draft", {"id": identifier})
    if draft.get("saved_expense_id") or draft.get("cancelled"):
        await q.message.reply_text("Esse registro já foi concluído.")
        return
    if action == "category":
        context.user_data.pop("editing", None)
        await q.message.reply_text("Escolha a categoria deste gasto:", reply_markup=category_buttons(identifier))
        return
    context.user_data["editing"] = (action, identifier)
    if action=='installments':
        await q.message.reply_text('Escolha as parcelas abaixo ou escreva 3 parcelas, por exemplo.',reply_markup=installment_buttons(identifier));return
    prompts = {'installments':'Em quantas parcelas? Envie de 1 a 36. Crédito à vista = 1.', 'first_due':'Digite o primeiro vencimento em DD/MM/AAAA. Confira na fatura do cartão.', 'due_date':'Digite o vencimento: DD/MM/AAAA.', 'document_date':'Digite a emissão / data do documento: DD/MM/AAAA.', 'payment_date':'Digite o dia em que pagou: DD/MM/AAAA. Não use uma data futura.', "amount": "Digite o valor correto. Exemplo: 149,95",
        "date": "Digite a data real da compra com ano. Exemplo: 03/05/2026. A data do lançamento será automática.",
        "description": "Digite o nome da loja ou a descrição do gasto.",
        "category": "Digite a categoria: Mercado, Casa, Transporte, Saúde, Educação, Lazer ou Outros."}
    await q.message.reply_text(prompts[action])


def period(args):
    today = datetime.now(TZ).date()
    start = datetime.strptime(args[0], "%m/%Y").date().replace(day=1) if args else today.replace(day=1)
    end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start, end


async def report(update, context):
    if not await private(update):
        return
    try:
        start, end = period(context.args)
    except ValueError:
        await update.message.reply_text("Use mês/ano: /resumo 10/2026")
        return
    rows = await api(update.effective_user.id, "list", {"from": start.isoformat(), "until": end.isoformat()})
    command = {"📊 Resumo do mês": "/resumo", "📋 Histórico": "/historico",
               "📥 Exportar planilha": "/planilha", "📄 Relatório PDF": "/relatorio"}.get(update.message.text,
                   update.message.text.split()[0].split("@")[0])
    if command in ("/planilha", "/relatorio"):
        if not rows:
            await update.message.reply_text("Nenhum gasto registrado neste mês.", reply_markup=MENU)
            return
        from reports import excel, pdf
        household = await api(update.effective_user.id, "home")
        builder = excel if command == "/planilha" else pdf
        file = await asyncio.to_thread(builder, rows, household["name"], start)
        await update.message.reply_document(file,
            caption=f"Controle de {start:%m/%Y}. Compras pela data da compra; contas pelo pagamento ou vencimento.")
    elif command == "/csv":
        out = io.StringIO()
        writer = csv.writer(out, delimiter=";")
        writer.writerow(["Registro", "Data da compra", "Lançado em (Brasília)", "Descrição", "Categoria", "Valor (R$)", "Documento", "Vencimento", "Situação", "Pagamento"])
        def safe_cell(value):
            value = str(value)
            return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
        for row in rows:
            writer.writerow([row["id"], purchase_date(row["expense_date"]) if row.get("document_type")!="bill" else "", registration_date(row["created_at"]), safe_cell(row["description"]),
                safe_cell(row["category"]), format(Decimal(str(row["amount"])), ".2f").replace(".", ","), row.get("document_date") or "", row.get("due_date") or "", {"pending":"A pagar","paid":"Paga","recorded":"Compra registrada"}.get(row.get("payment_status"),"Compra registrada"),row.get("payment_date") or ""])
        file = io.BytesIO(out.getvalue().encode("utf-8-sig"))
        file.name = f"gastos-da-casa-{start:%Y-%m}.csv"
        await update.message.reply_document(file, caption="Planilha do mês. Abre no Excel ou Google Planilhas.")
    elif command == "/historico":
        lines = [f"#{r['id']} · {r['description'][:60]} · {money(r['amount'])}\n"
                 f"Data: {purchase_date(r.get('document_date') if r.get('document_type')=='bill' else r['expense_date'])} · Lançado: {registration_date(r['created_at'])}\n"
                 f"Categoria: {r['category']}\n"
                 + (f"Conta: {'Paga' if r.get('payment_status')=='paid' else 'A pagar'} | Vencimento: {purchase_date(r.get('due_date'))}" if r.get('document_type')=='bill' else '') for r in rows[:20]]
        text = "🏠 Gastos do mês\n\n" + ("\n\n".join(lines) or "Nenhum gasto registrado.")
        if not rows:
            await update.message.reply_text(text)
        for row, line in zip(rows[:20], lines):
            await update.message.reply_text(line, reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Editar", callback_data=f"expense:{row['id']}"),
                InlineKeyboardButton("Excluir", callback_data=f"askdelete:{row['id']}")]]))
    else:
        categories = defaultdict(Decimal)
        total = Decimal("0")
        for row in rows:
            total += Decimal(str(row["amount"]))
            categories[row["category"]] += Decimal(str(row["amount"]))
        lines = [f"• {cat}: {money(amount)}" for cat, amount in sorted(categories.items(), key=lambda x: -x[1])]
        pending=sum((Decimal(str(r['amount'])) for r in rows if r.get('payment_status')=='pending'),Decimal('0'))
        paid=sum((Decimal(str(r['amount'])) for r in rows if r.get('payment_status')=='paid'),Decimal('0'))
        purchases=total-pending-paid
        text = (f"🏠 Resumo da casa · {start:%m/%Y}\n\nCompras registradas: {money(purchases)}\nContas pagas: {money(paid)}\nContas a pagar: {money(pending)}\nTotal de compromissos: {money(total)}\nRegistros: {len(rows)}\n\nCompras pela data da compra; contas pagas pelo pagamento; pendentes pelo vencimento.\n\n" + "\n".join(lines))
        for offset in range(0, len(text), 3500):
            await update.message.reply_text(text[offset:offset+3500])
    if len(rows) == 2000:
        await update.message.reply_text("Este relatório atingiu o limite de 2.000 registros; pode estar incompleto.")


async def edit_expense(update, context):
    if not await private(update):
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Toque em Editar no histórico ou use /editar 123.")
        return
    context.user_data.pop("editing", None)
    await show_expense(update.message, update.effective_user.id, context.args[0])


async def delete(update, context):
    if not await private(update):
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Use /historico para encontrar o número e /excluir 123.")
        return
    identifier = context.args[0]
    await update.message.reply_text(f"Excluir o gasto #{identifier} desta casa?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Excluir", callback_data="delete:"+identifier),
            InlineKeyboardButton("Manter", callback_data="keep:"+identifier)]]))


async def cancel_edit(update, context):
    context.user_data.pop('waiting_credit',None)
    context.user_data.pop("editing", None)
    await update.message.reply_text("Edição cancelada. Você pode voltar aos botões da nota.")


async def error_handler(update, context):
    import logging
    logging.error("Falha no fluxo da casa: %s", type(context.error).__name__)
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "Não consegui concluir. Seus registros salvos continuam guardados. "
            "Se estiver salvando uma nota, tente o botão novamente.")


async def bills(update,context):
    if not await private(update): return
    context.user_data.pop('editing',None)
    rows=await api(update.effective_user.id,'bills')
    if not rows:
        await update.effective_message.reply_text('Nenhuma conta a pagar.',reply_markup=MENU);return
    today=datetime.now(TZ).date()
    await update.effective_message.reply_text('📆 Contas a pagar (até 100, por vencimento)')
    for row in rows:
        due=date.fromisoformat(row['due_date'])
        label='Atrasada' if due<today else ('Vence hoje' if due==today else 'A vencer')
        await update.effective_message.reply_text(label+'\n'+expense_text(row),reply_markup=expense_buttons(row['id'],row))


def register(app):
    app.add_handler(CommandHandler(["start", "casa", "menu"], home))
    app.add_handler(CommandHandler("cartao", cards_user))
    app.add_handler(CommandHandler(["ajuda", "help"], help_user))
    app.add_handler(CommandHandler("contas", bills))
    app.add_handler(CommandHandler("editar", edit_expense))
    app.add_handler(CommandHandler(["resumo", "historico", "planilha", "relatorio", "csv"], report))
    app.add_handler(CommandHandler("excluir", delete))
    app.add_handler(CommandHandler("cancelar", cancel_edit))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)

