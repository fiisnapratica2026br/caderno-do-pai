import os
import re
import unicodedata
from datetime import datetime
from decimal import Decimal
import asyncio
import hashlib
import logging
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Configurações
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', 'COLE_SEU_TOKEN_AQUI')
OCR_API_KEY = os.environ.get('OCR_API_KEY', 'COLE_SUA_CHAVE_OCR_AQUI')
GOOGLE_SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', 'COLE_ID_DA_PLANILHA_AQUI')

# Configuração Google Sheets
scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = None  # Vamos configurar isso depois

def ler_imagem(image_bytes):
    """Lê texto de uma imagem usando OCR.space"""
    url = 'https://api.ocr.space/parse/image'
    payload = {
        'language': 'por',
        'isOverlayRequired': False,
        'OCREngine': 2
    }
    headers = {'apikey': OCR_API_KEY}
    
    response = requests.post(url, data=payload, headers=headers,
                             files={'file': ('nota.jpg', image_bytes, 'image/jpeg')},
                             timeout=45)
    response.raise_for_status()
    result = response.json()
    
    if result.get('IsErroredOnProcessing'):
        return None, "Erro ao processar imagem"
    
    parsed = result.get('ParsedResults') or []
    if not parsed or not parsed[0].get('ParsedText', '').strip():
        return None, 'Não consegui ler a nota. Tente uma foto mais nítida.'
    texto = parsed[0]['ParsedText']
    return texto, None

def extrair_dados(texto):
    """Procura o total explicitamente; nunca usa quantidade como valor."""
    linhas = [linha.strip() for linha in texto.splitlines() if linha.strip()]
    dados = {'local': '', 'valor': '', 'data': '', 'texto_completo': texto}
    if linhas:
        dados['local'] = linhas[0]
    def normalizar(linha):
        return ''.join(c for c in unicodedata.normalize('NFKD', linha.lower())
                       if not unicodedata.combining(c))
    # Duas casas decimais obrigatórias. Rejeita quantidades como 4,000.
    moeda = re.compile(r'(?<![\d.,])(?:\d{1,3}(?:\.\d{3})+|\d+)[,.]\d{2}(?![\d.,])')
    candidatos = []
    for i, linha in enumerate(linhas):
        rotulo = normalizar(linha)
        if any(x in rotulo for x in ('subtotal', 'sub total', 'tribut', 'imposto',
                                     'desconto', 'troco', 'quantidade', 'qtde')):
            continue
        prioridade = None
        if re.search(r'\b(?:valor\s+total|total\s+(?:a\s+pagar|da\s+nota|da\s+compra|geral))\b', rotulo):
            prioridade = 3
        elif re.search(r'\btotal\b', rotulo):
            prioridade = 2
        elif re.search(r'\bvalor\s+pago\b', rotulo):
            prioridade = 1
        if prioridade is None:
            continue
        valores = moeda.findall(linha)
        if not valores:
            # OCR às vezes coloca o valor numa linha separada do rótulo.
            # Aceita apenas uma linha contendo exclusivamente dinheiro.
            for seguinte in linhas[i + 1:i + 3]:
                if re.fullmatch(r'(?:R\$\s*)?' + moeda.pattern, seguinte):
                    valores = moeda.findall(seguinte)
                    break
                if re.search(r'[A-Za-z]', seguinte):
                    break
        if len(valores) == 1:
            candidatos.append((prioridade, valores[0].replace('.', '').replace(',', '.')
                               if ',' in valores[0] else valores[0]))
    if candidatos:
        melhor = max(p for p, _ in candidatos)
        totais = {v for p, v in candidatos if p == melhor}
        # Totais conflitantes exigem revisão, sem escolher arbitrariamente.
        if len(totais) == 1:
            dados['valor'] = 'R$ ' + format(Decimal(totais.pop()), ',.2f').replace(
                ',', '_').replace('.', ',').replace('_', '.')
    datas = []
    for linha in linhas:
        for dia, mes, ano in re.findall(r'\b(\d{2})[/-](\d{2})[/-](\d{4})\b', linha):
            try:
                data = datetime(int(ano), int(mes), int(dia))
            except ValueError:
                continue
            rotulo = normalizar(linha)
            prioridade = 2 if any(x in rotulo for x in ('emissao', 'autorizacao')) else 1
            datas.append((prioridade, data.strftime('%d/%m/%Y')))
    if datas:
        melhor = max(p for p, _ in datas)
        opcoes = {d for p, d in datas if p == melhor}
        if len(opcoes) == 1:
            dados['data'] = opcoes.pop()
    return dados

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧾 *Bem-vindo ao Caderno do Pai - Controle de Gastos*\n\n"
        "Envie a foto de uma nota fiscal ou cupom e eu organizo os dados para você.\n\n"
        "_Dica: Fotos nítidas funcionam melhor._",
        parse_mode='Markdown'
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📸 Processando sua nota...")
    
    try:
        # Pega a foto
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        
        # Envia para OCR
        image_bytes = bytes(await file.download_as_bytearray())
        texto, erro = await asyncio.to_thread(ler_imagem, image_bytes)
        
        if erro:
            await update.message.reply_text(f"❌ {erro}")
            return
        
        # Extrai dados
        dados = extrair_dados(texto)
        
        # Formata resposta visual
        resposta = (
            f"🧾 Nota lida (teste)\n\n"
            f"🏪 Local: {dados['local'] or 'Não identificado'}\n"
            f"💰 Valor: {dados['valor'] or 'Não identificado'}\n"
            f"📅 Data: {dados['data'] or 'Não identificada'}\n\n"
            f"ℹ️ A gravação na planilha ainda não está configurada.\n"
            f"⚠️ Confira os valores, a leitura automática pode errar."
        )
        
        await update.message.reply_text(resposta)
        
        # Aqui você pode adicionar código para salvar no Google Sheets
        # Vou deixar isso para a próxima iteração
        
    except Exception as e:
        logging.error("Falha ao processar nota: %s", type(e).__name__)
        await update.message.reply_text("❌ Não consegui processar esta nota. Tente novamente em instantes.")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    logging.basicConfig(level=logging.WARNING)
    # Não registrar URLs de requisição: URLs do Telegram contêm o token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    public_url = os.environ.get("RENDER_EXTERNAL_URL")
    if public_url:
        secret = hashlib.sha256(("webhook:" + TELEGRAM_TOKEN).encode()).hexdigest()
        print("Bot iniciando com webhook no Render.", flush=True)
        app.run_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get("PORT", "10000")),
            url_path="telegram",
            webhook_url=public_url.rstrip("/") + "/telegram",
            secret_token=secret,
            allowed_updates=["message"],
        )
    else:
        print("Bot iniciando localmente.", flush=True)
        app.run_polling()

if __name__ == '__main__':
    main()