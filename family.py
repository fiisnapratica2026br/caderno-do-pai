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
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import CommandHandler, MessageHandler, CallbackQueryHandler, filters

TZ = ZoneInfo("America/Sao_Paulo")
MENU = ReplyKeyboardMarkup([
    ["➕ Adicionar gasto", "📸 Enviar nota"],
    ["📊 Resumo do mês", "📋 Histórico"],
    ["📥 Exportar planilha", "🏠 Minha casa"],
], resize_keyboard=True)

def expense_text(row):
    return (f"🧾 Gasto #{row['id']}\n\n"
            f"🏪 {row['description']}\n💰 {money(row['amount'])}\n"
            f"📅 Compra: {purchase_date(row['expense_date'])}\n"
            f"🕒 Lançado: {registration_date(row['created_at'])}\n"
            f"📂 {row['category']}")

def expense_buttons(identifier):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Valor", callback_data=f"expamount:{identifier}"),
         InlineKeyboardButton("Data da compra", callback_data=f"expdate:{identifier}")],
        [InlineKeyboardButton("Descrição", callback_data=f"expdescription:{identifier}"),
         InlineKeyboardButton("Categoria", callback_data=f"expcategory:{identifier}")],
        [InlineKeyboardButton("Concluir edição", callback_data=f"expdone:{identifier}")],
    ])

async def show_expense(message, user, identifier):
    row = await api(user, "get_expense", {"expense_id": int(identifier)})
    if row.get("missing"):
        await message.reply_text("Gasto não encontrado nesta casa.")
        return
    await message.reply_text(expense_text(row), reply_markup=expense_buttons(identifier))


CATEGORIES = [
    "Água", "Energia", "Supermercado", "Combustível", "Moradia",
    "Internet e telefone", "Saúde", "Educação", "Transporte",
    "Casa e manutenção", "Impostos e taxas", "Lazer", "Vestuário", "Pets", "Outros",
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


def buttons(draft_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Salvar gasto", callback_data="save:"+draft_id),
         InlineKeyboardButton("Cancelar", callback_data="cancel:"+draft_id)],
        [InlineKeyboardButton("Corrigir valor", callback_data="amount:"+draft_id),
         InlineKeyboardButton("Data da compra", callback_data="date:"+draft_id)],
        [InlineKeyboardButton("Editar descrição", callback_data="description:"+draft_id),
         InlineKeyboardButton("Categoria", callback_data="category:"+draft_id)],
    ])


async def private(update):
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text("Use este bot em uma conversa privada para proteger os gastos da casa.")
        return False
    return True


def draft_text(data):
    return (
        "🧾 Confira antes de salvar\n\n"
        f"🏪 {data.get('description') or 'Descrição não identificada'}\n"
        f"💰 {money(data['amount']) if data.get('amount') else 'Corrija o valor'}\n"
        f"📅 Data da compra: {purchase_date(data.get('date'))}\n"
        f"📂 {data.get('category', 'Outros')}\n\n"
        "Os preços dos produtos podem conter erros de leitura. "
        "Este registro salva o total da compra para a casa."
    )


async def propose(update, context, data, source_key, details=None):
    if not await private(update):
        return
    draft_id = str(uuid.uuid4())
    await api(update.effective_user.id, "draft",
        {"id": draft_id, "source_key": source_key, "data": data})
    await update.effective_message.reply_text(draft_text(data), reply_markup=buttons(draft_id))
    if details:
        # Respeita o limite de caracteres das mensagens do Telegram.
        for offset in range(0, len(details), 3500):
            await update.effective_message.reply_text(details[offset:offset+3500])


async def home(update, context):
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
        "/planilha — baixar os registros do mês\n"
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
    if value in ("📊 Resumo do mês", "📋 Histórico", "📥 Exportar planilha"):
        context.user_data.pop("editing", None)
        context.args = []
        await report(update, context)
        return
    if value == "🏠 Minha casa":
        context.user_data.pop("editing", None)
        context.args = []
        await home(update, context)
        return
    if value in ("➕ Adicionar gasto", "📸 Enviar nota"):
        context.user_data.pop("editing", None)
        await update.message.reply_text(
            "Escreva a descrição e o valor. Exemplo: cachorro quente do Bidjula 15,00. Depois confira a data e a categoria antes de salvar."
            if value == "➕ Adicionar gasto" else
            "Envie uma foto nítida da nota inteira, com boa iluminação. Depois confira os dados antes de salvar.",
            reply_markup=MENU)
        return
    edit = context.user_data.get("editing")
    if edit:
        field, draft_id = edit
        try:
            if field == "amount":
                parsed = parse_amount(value)
            elif field == "date":
                parsed = datetime.strptime(value, "%d/%m/%Y").date().isoformat()
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
            result = await api(update.effective_user.id, "update_expense",
                {"expense_id": int(identifier), "patch": {field: parsed}})
            context.user_data.pop("editing", None)
            if result.get("missing"):
                await update.message.reply_text("Gasto não encontrado nesta casa.")
            else:
                await update.message.reply_text("✅ Alteração salva.\n\n" + expense_text(result),
                    reply_markup=expense_buttons(identifier))
            return
        result = await api(update.effective_user.id, "edit",
            {"id": draft_id, "patch": {field: parsed}})
        context.user_data.pop("editing", None)
        if "saved" in result:
            await update.message.reply_text("Esse gasto já foi salvo; crie um novo registro para outra compra.")
        else:
            await update.message.reply_text(draft_text(result), reply_markup=buttons(draft_id))
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
        "date": datetime.now(TZ).date().isoformat(), "category": "Outros", "items": []},
        f"text:{update.effective_chat.id}:{update.message.message_id}")


async def callback(update, context):
    q = update.callback_query
    await q.answer()
    if not await private(update):
        return
    action, identifier = q.data.split(":", 1)
    user = update.effective_user.id
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
            reply_markup=None if result.get("missing") else expense_buttons(identifier))
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
            "patch": {"category": CATEGORIES[int(index)]}})
        context.user_data.pop("editing", None)
        if "saved" in result:
            await q.message.reply_text("Esse gasto já foi salvo.")
        else:
            await q.edit_message_text(draft_text(result), reply_markup=buttons(draft_id))
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
    if action in ("save", "cancel"):
        result = await api(user, "confirm" if action == "save" else "cancel", {"id": identifier})
        context.user_data.pop("editing", None)
        await q.edit_message_text(
            f"✅ Gasto salvo para a casa. Registro #{result['saved']}.\nUse o menu para consultar os gastos."
            if result.get("saved") else "Registro cancelado.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Editar gasto",
                callback_data=f"expense:{result['saved']}")]]) if result.get("saved") else None)
        return
    if action not in ("amount", "date", "description", "category"):
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
    prompts = {"amount": "Digite o valor correto. Exemplo: 149,95",
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
               "📥 Exportar planilha": "/planilha"}.get(update.message.text,
                   update.message.text.split()[0].split("@")[0])
    if command == "/planilha":
        out = io.StringIO()
        writer = csv.writer(out, delimiter=";")
        writer.writerow(["Registro", "Data da compra", "Lançado em (Brasília)", "Descrição", "Categoria", "Valor (R$)"])
        def safe_cell(value):
            value = str(value)
            return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
        for row in rows:
            writer.writerow([row["id"], purchase_date(row["expense_date"]), registration_date(row["created_at"]), safe_cell(row["description"]),
                safe_cell(row["category"]), format(Decimal(str(row["amount"])), ".2f").replace(".", ",")])
        file = io.BytesIO(out.getvalue().encode("utf-8-sig"))
        file.name = f"gastos-da-casa-{start:%Y-%m}.csv"
        await update.message.reply_document(file, caption="Planilha do mês. Abre no Excel ou Google Planilhas.")
    elif command == "/historico":
        lines = [f"#{r['id']} · {r['description'][:60]} · {money(r['amount'])}\n"
                 f"Compra: {purchase_date(r['expense_date'])} · Lançado: {registration_date(r['created_at'])}\n"
                 f"Categoria: {r['category']}" for r in rows[:20]]
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
        text = f"🏠 Resumo da casa · {start:%m/%Y}\nPor data da compra\n\nTotal registrado: {money(total)}\nCompras: {len(rows)}\n\n" + "\n".join(lines)
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
    context.user_data.pop("editing", None)
    await update.message.reply_text("Edição cancelada. Você pode voltar aos botões da nota.")


async def error_handler(update, context):
    import logging
    logging.error("Falha no fluxo da casa: %s", type(context.error).__name__)
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "Não consegui concluir. Seus registros salvos continuam guardados. "
            "Se estiver salvando uma nota, tente o botão novamente.")


def register(app):
    app.add_handler(CommandHandler(["start", "casa", "menu"], home))
    app.add_handler(CommandHandler("editar", edit_expense))
    app.add_handler(CommandHandler(["resumo", "historico", "planilha"], report))
    app.add_handler(CommandHandler("excluir", delete))
    app.add_handler(CommandHandler("cancelar", cancel_edit))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)
