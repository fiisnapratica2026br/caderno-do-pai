"""Painel autenticado pelo Telegram; usa a API da casa já existente."""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import signal
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import tornado.httpserver
import tornado.web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import CommandHandler
import family


def validate_init_data(raw, token, now=None):
    if not isinstance(raw, str) or not raw or len(raw) > 16384:
        raise ValueError("Abra o painel pelo Telegram.")
    pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
    if len(pairs) != len({key for key, _ in pairs}):
        raise ValueError("Acesso inválido.")
    data = dict(pairs)
    received = data.pop("hash", "")
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{key}={data[key]}" for key in sorted(data))
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise ValueError("Acesso inválido.")
    now = time.time() if now is None else now
    age = now - int(data.get("auth_date", "0"))
    if age < -30 or age > 3600:
        raise ValueError("Acesso expirado. Feche e abra o painel pelo Telegram.")
    user = json.loads(data.get("user", "{}"))
    identifier = user.get("id")
    if type(identifier) is not int or not 0 < identifier <= 2**52 - 1:
        raise ValueError("Usuário inválido.")
    return identifier


def month_period(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValueError("Selecione um mês válido.")
    year, month = map(int, value.split("-"))
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1)
    return start.isoformat(), end.isoformat()


def summary(rows):
    paid = Decimal("0")
    pending = Decimal("0")
    categories = {}
    for row in rows:
        amount = Decimal(str(row["amount"]))
        if row.get("payment_status") == "pending":
            pending += amount
        else:
            paid += amount
            category = row["category"]
            categories[category] = categories.get(category, Decimal("0")) + amount
    return {"recorded": str(paid), "pending": str(pending),
            "count": len(rows), "categories": {key: str(value) for key, value in categories.items()}}


def expense_id(value):
    if type(value) is not int or not 0 < value < 2**53:
        raise ValueError("Registro inválido.")
    return value


class BaseHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header("Cache-Control", "no-store")
        self.set_header("X-Content-Type-Options", "nosniff")
        self.set_header("Referrer-Policy", "no-referrer")

    def write_error(self, status_code, **kwargs):
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.finish({"error": "Não foi possível concluir a operação."})


class HealthHandler(BaseHandler):
    def get(self):
        self.finish({"status": "ok", "panel": "telegram-credit-v2"})


class PanelHandler(BaseHandler):
    def get(self):
        self.set_header("Content-Type", "text/html; charset=utf-8")
        self.set_header("Content-Security-Policy",
            "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; "
            "connect-src 'self' https://cadernodopai-bot.onrender.com; img-src 'self' data:; "
            "object-src 'none'; base-uri 'none'")
        self.finish((Path(__file__).parent / "web" / "index.html").read_text())


class WebhookHandler(BaseHandler):
    def initialize(self, telegram_app, secret):
        self.telegram_app, self.secret = telegram_app, secret

    async def post(self):
        supplied = self.request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(self.secret, supplied):
            self.set_status(403)
            self.finish({"error": "Forbidden"})
            return
        try:
            data = json.loads(self.request.body)
            if not isinstance(data, dict) or type(data.get("update_id")) is not int:
                raise ValueError()
            update = Update.de_json(data, self.telegram_app.bot)
        except (ValueError, TypeError, KeyError):
            self.set_status(400)
            self.finish({"error": "Invalid update"})
            return
        await self.telegram_app.update_queue.put(update)
        self.finish({"ok": True})


class ApiHandler(BaseHandler):
    def initialize(self, token, backend):
        self.token, self.backend = token, backend

    def allowed_origin(self):
        origin = self.request.headers.get("Origin")
        configured = os.environ.get("PANEL_ORIGIN", "").rstrip("/")
        own = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
        if origin and origin not in {configured, own}:
            raise tornado.web.HTTPError(403)
        if origin and origin == configured:
            self.set_header("Access-Control-Allow-Origin", origin)
            self.set_header("Vary", "Origin")

    def options(self):
        self.allowed_origin()
        self.set_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type")
        self.set_status(204)
        self.finish()

    async def post(self):
        self.allowed_origin()
        try:
            payload = json.loads(self.request.body)
            if not isinstance(payload, dict):
                raise ValueError("Pedido inválido.")
            try:
                user = validate_init_data(payload.get("initData"), self.token)
            except (ValueError, TypeError, AttributeError) as error:
                self.set_status(401)
                self.finish({"error": str(error) if isinstance(error, ValueError) else "Acesso inválido."})
                return
            action = payload.get("action", "snapshot")
            if action == "snapshot":
                start, end = month_period(payload.get("month"))
                home, rows, bills = await asyncio.gather(
                    self.backend(user, "home"),
                    self.backend(user, "list", {"from": start, "until": end}),
                    self.backend(user, "bills"))
                # Não enviar metadados internos de notas/rascunhos ao navegador.
                fields = {"id", "amount", "description", "category", "expense_date",
                          "document_type", "document_date", "due_date", "payment_date", "payment_status",
                          "credit_group", "card_name", "installment_number", "installment_count", "purchase_total"}
                clean = lambda records: [{key: value for key, value in row.items() if key in fields} for row in records]
                self.finish({"home": home.get("name", "Minha casa"), "month": payload["month"],
                    "rows": clean(rows), "bills": clean(bills), "summary": summary(rows),
                    "limited": len(rows) >= 2000 or len(bills) >= 100})
            elif action == "delete":
                result = await self.backend(user, "delete", {"expense_id": expense_id(payload.get("id"))})
                if not result.get("deleted"):
                    self.set_status(404)
                    self.finish({"error": "Registro não encontrado nesta casa."})
                else:
                    self.finish({"ok": True})
            elif action in ("pay", "pay_invoice"):
                value = payload.get("date")
                if not isinstance(value, str):
                    raise ValueError("Informe a data do pagamento.")
                payment = date.fromisoformat(value)
                if payment > datetime.now(family.TZ).date():
                    raise ValueError("O pagamento não pode estar no futuro.")
                if action=='pay_invoice':
                    due=date.fromisoformat(payload.get('due_date','')).isoformat()
                    card=payload.get('card_name')
                    if not isinstance(card,str) or not 1<=len(card)<=40:raise ValueError()
                    result=await self.backend(user,'pay_invoice',{'card_name':card,'due_date':due,'payment_date':payment.isoformat()})
                    self.finish({'ok':True,'paid':result.get('paid',0)})
                    return
                result = await self.backend(user, "update_bill", {"expense_id": expense_id(payload.get("id")),
                    "patch": {"payment_status": "paid", "payment_date": payment.isoformat()}})
                if result.get("missing"):
                    self.set_status(404)
                    self.finish({"error": "Conta não encontrada nesta casa."})
                else:
                    self.finish({"ok": True})
            else:
                raise ValueError("Operação inválida.")
        except (ValueError, TypeError, KeyError):
            self.set_status(400)
            self.finish({"error": "Confira os campos e tente novamente."})
        except Exception as error:
            logging.warning("Falha no painel: %s", type(error).__name__)
            self.set_status(502)
            self.finish({"error": "Não consegui acessar os registros. Tente novamente em instantes."})


def create_http_app(telegram_app, token, backend=family.api):
    secret = hashlib.sha256(("webhook:" + token).encode()).hexdigest()
    web = str(Path(__file__).parent / "web")
    return tornado.web.Application([
        (r"/health", HealthHandler),
        (r"/telegram", WebhookHandler, {"telegram_app": telegram_app, "secret": secret}),
        (r"/api/panel", ApiHandler, {"token": token, "backend": backend}),
        (r"/(?:painel|painel/|)", PanelHandler),
        (r"/(panel\.js|panel\.css)", tornado.web.StaticFileHandler, {"path": web}),
    ], debug=False)


async def open_panel(update, context):
    if not await family.private(update):
        return
    base = os.environ.get("PANEL_URL") or os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/") + "/painel"
    if urlparse(base).scheme != "https":
        await update.effective_message.reply_text("O painel ainda não está disponível neste ambiente.")
        return
    await update.effective_message.reply_text("Abra o painel da sua casa. Os registros são os mesmos do bot.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Abrir meu painel", web_app=WebAppInfo(base))]]))


def register(app):
    app.add_handler(CommandHandler("painel", open_panel))


async def serve(app, public_url, token):
    # Ciclo de vida público do python-telegram-bot, com servidor HTTP próprio.
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopped.set)
    secret = hashlib.sha256(("webhook:" + token).encode()).hexdigest()
    server = tornado.httpserver.HTTPServer(create_http_app(app, token), max_buffer_size=1024 * 1024)
    async with app:
        server.listen(int(os.environ.get("PORT", "10000")), address="0.0.0.0")
        await app.start()
        try:
            await app.bot.set_webhook(url=public_url.rstrip("/") + "/telegram",
                secret_token=secret, allowed_updates=["message", "callback_query"])
            print("Bot e painel iniciados no Render.", flush=True)
            await stopped.wait()
        finally:
            server.stop()
            await server.close_all_connections()
            await app.stop()
