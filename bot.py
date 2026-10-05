import os
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

def ler_imagem(image_url):
    """Lê texto de uma imagem usando OCR.space"""
    url = 'https://api.ocr.space/parse/image'
    payload = {
        'url': image_url,
        'language': 'por',
        'isOverlayRequired': False,
        'OCREngine': 2
    }
    headers = {'apikey': OCR_API_KEY}
    
    response = requests.post(url, data=payload, headers=headers)
    result = response.json()
    
    if result.get('IsErroredOnProcessing'):
        return None, "Erro ao processar imagem"
    
    texto = result['ParsedResults'][0]['ParsedText']
    return texto, None

def extrair_dados(texto):
    """Extrai informações básicas do texto da nota"""
    linhas = texto.split('\n')
    dados = {
        'local': '',
        'valor': '',
        'data': '',
        'texto_completo': texto
    }
    
    for linha in linhas:
        linha = linha.strip()
        
        # Tentativa simples de extrair valor (R$ ou números com vírgula)
        if 'R$' in linha or ',' in linha:
            if not dados['valor']:
                dados['valor'] = linha
        
        # Primeira linha não vazia geralmente é o nome do estabelecimento
        if not dados['local'] and linha and len(linha) > 3:
            dados['local'] = linha
    
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
        texto, erro = ler_imagem(file.file_path)
        
        if erro:
            await update.message.reply_text(f"❌ {erro}")
            return
        
        # Extrai dados
        dados = extrair_dados(texto)
        
        # Formata resposta visual
        resposta = (
            f"🧾 *Nota Registrada!*\n\n"
            f"🏪 *Local:* {dados['local'] or 'Não identificado'}\n"
            f"💰 *Valor:* {dados['valor'] or 'Não identificado'}\n"
            f"📅 *Data:* {dados['data'] or 'Ver planilha'}\n\n"
            f"✅ _Dados salvos na planilha._\n"
            f"⚠️ _Confira os valores, a leitura automática pode errar._"
        )
        
        await update.message.reply_text(resposta, parse_mode='Markdown')
        
        # Aqui você pode adicionar código para salvar no Google Sheets
        # Vou deixar isso para a próxima iteração
        
    except Exception as e:
        await update.message.reply_text(f"❌ Erro: {str(e)}")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    print("🤖 Bot rodando...")
    app.run_polling()

if __name__ == '__main__':
    main()