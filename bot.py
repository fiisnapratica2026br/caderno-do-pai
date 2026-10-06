import os
import io
import re
import unicodedata
from datetime import datetime
from decimal import Decimal
import asyncio
import hashlib
import logging
import family
import requests
from telegram import Update
from telegram.error import TimedOut
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
        'isTable': True,
        'scale': True,
        'detectOrientation': True,
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


def extrair_conta(texto):
    """Contas de consumo: não confundir subtotal, tributo ou vencimento."""
    def norm(value):
        return ''.join(c for c in unicodedata.normalize('NFKD', value.lower())
                       if not unicodedata.combining(c))
    normal = norm(texto)
    providers = [('sabesp', 'Sabesp', 'Água'), ('sanepar', 'Sanepar', 'Água'),
                 ('copasa', 'Copasa', 'Água'), ('casan', 'Casan', 'Água'),
                 ('cedae', 'Cedae', 'Água'), ('cpfl', 'CPFL', 'Energia elétrica'), ('celesc', 'Celesc', 'Energia elétrica'),
                 ('cemig', 'Cemig', 'Energia elétrica'), ('copel', 'Copel', 'Energia elétrica'),
                 ('enel', 'Enel', 'Energia elétrica'), ('energisa', 'Energisa', 'Energia elétrica')]
    provider = next(((name, cat) for term, name, cat in providers
                     if re.search(r'\b' + term + r'\b', normal)), None)
    if not provider and re.search(r'agua\s+e\s+[ef]sgoto', normal):
        provider = ('Água e esgoto', 'Água')
    if not provider:
        return None
    name, category = provider
    linhas = [l.strip() for l in texto.splitlines() if l.strip()]
    moeda = re.compile(r'(?<![\d.,])(?:\d{1,3}(?:\.\d{3})+|\d+)[,.]\d{2}(?![\d.,])')
    candidates = []
    dates, due = [], []
    def date_values(line):
        # Reparar apenas datas de oito dígitos ou DDMM/AAAA junto a rótulos.
        line = re.sub(r'\b(\d{2})(\d{2})/(\d{4})\b', r'\1/\2/\3', line)
        values = []
        for day, month, year in re.findall(r'\b(\d{2})[/-](\d{2})[/-](\d{4})\b', line):
            try:
                values.append(datetime(int(year), int(month), int(day)).strftime('%d/%m/%Y'))
            except ValueError:
                pass
        return values
    for i, line in enumerate(linhas):
        label = norm(line)
        priority = 0
        if re.search(r'\b(?:total\s*\(\s*r\s*\$\s*\)|total\s+a\s+pagar|valor\s+(?:total\s+da\s+fatura|da\s+fatura|a\s+pagar))', label):
            priority = 4
        elif re.search(r'\btotal\b', label) and not re.search(r'subtotal|tribut|agua|esgoto|[ef]sgoto', label):
            priority = 3
        if priority:
            # Na mesma linha podem aparecer água e esgoto antes do total.
            total_label = re.search(r'\b(?:total\s*\(\s*r\s*\$\s*\)|total\s+a\s+pagar|valor\s+(?:total\s+da\s+fatura|da\s+fatura|a\s+pagar)|total\b)', label)
            values = moeda.findall(line[total_label.end():]) if total_label else []
            if not values:
                for following in linhas[i+1:i+3]:
                    # Linhas de tabela podem conter código e duas datas.
                    if re.search(r'[a-zA-Z]{3,}', following):
                        break
                    values = moeda.findall(following)
                    if values:
                        break
            if len(values) == 1:
                number = values[0].replace('.', '').replace(',', '.') if ',' in values[0] else values[0]
                candidates.append((priority, Decimal(number)))
        if 'emissao' in label or 'apresentacao' in label:
            values = date_values(line)
            if not values:
                for following in linhas[i+1:i+3]:
                    if re.search(r'[a-zA-Z]{3,}', following):
                        break
                    values = date_values(following)
                    if values:
                        break
            if values:
                dates.append((2 if 'emissao' in label else 1, values[0],
                              'emissão' if 'emissao' in label else 'apresentação'))
                if 'vencimento' in label and len(values) == 2:
                    due.append(values[1])
        elif 'vencimento' in label:
            values = date_values(line)
            if not values and i+1 < len(linhas):
                values = date_values(linhas[i+1])
            due.extend(values)
    amount = ''
    if candidates:
        rank = max(p for p, value in candidates)
        values = {value for p, value in candidates if p == rank}
        if len(values) == 1:
            amount = family.money(values.pop())
    document_date, date_label = '', 'Data do documento'
    if dates:
        rank = max(p for p, value, kind in dates)
        values = {(value, kind) for p, value, kind in dates if p == rank}
        if len(values) == 1:
            document_date, kind = values.pop()
            date_label = 'Data de ' + kind
    return {'local': name + (' — Conta de água' if category == 'Água' else ' — Conta de energia'),
            'valor': amount, 'data': document_date, 'texto_completo': texto,
            'document_type': 'bill', 'category': category, 'date_label': date_label,
            'due_date': next(iter(set(due))) if len(set(due)) == 1 else ''}

def extrair_dados(texto):
    """Procura o total explicitamente; nunca usa quantidade como valor."""
    conta = extrair_conta(texto)
    if conta:
        return conta
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
        if re.search(r'\b(?:valor\s+(?:total|a\s+pagar)|total\s+(?:a\s+pagar|da\s+nota|da\s+compra|geral))\b', rotulo):
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

def extrair_itens(texto):
    """Reconhece linhas comuns de cupom; conserva itens ausentes como dúvida."""
    linhas = [l.strip() for l in texto.splitlines() if l.strip()]
    numero = r'(?:\d{1,3}(?:\.\d{3})+|\d+)[,.]\d{2}'
    padrao = re.compile(
        r'(?P<qtd>\d+(?:[,.]\d{1,3})?)\s*'
        r'(?P<un>UN|UND|UNID|PC|PÇ|KG|G|LT|L|MT|M|CX|PCT)'
        r'\s*(?:X|×)?\s*(?:R\$\s*)?'
        r'(?P<unit>' + numero + r')\s+(?:R\$\s*)?'
        r'(?P<total>' + numero + r')(?![\d.,])', re.I)
    def decimal(valor):
        return Decimal(valor.replace('.', '').replace(',', '.')) if ',' in valor else Decimal(valor)
    itens = []
    for i, linha in enumerate(linhas):
        encontrado = padrao.search(linha)
        if not encontrado:
            # Une apenas fragmentos numéricos/unidades contíguos; nunca
            # atravessa a descrição de outro produto nem o total da nota.
            fragmentos = []
            for fragmento in linhas[i:i + 5]:
                if not re.fullmatch(r'[\d\s.,R$×xX]+|(?:UN|UND|UNID|PC|PÇ|KG|G|LT|L|MT|M|CX|PCT)\s*[xX×]?', fragmento, re.I):
                    break
                fragmentos.append(fragmento)
                encontrado = padrao.search(" ".join(fragmentos))
                if encontrado:
                    linha = " ".join(fragmentos)
                    break
        if not encontrado:
            continue
        descricao = linha[:encontrado.start()].strip()
        if not descricao and i:
            descricao = linhas[i - 1]
        descricao = re.sub(r'^\d{3,14}\s+', '', descricao).strip()
        if not descricao or any(p in descricao.lower() for p in
                               ('total', 'tribut', 'pagamento', 'troco')):
            continue
        qtd = Decimal(encontrado['qtd'].replace(',', '.'))
        unitario = decimal(encontrado['unit'])
        subtotal = decimal(encontrado['total'])
        itens.append({'descricao': descricao, 'quantidade': qtd,
                      'unidade': encontrado['un'].upper(), 'unitario': unitario,
                      'subtotal': subtotal,
                      'conferido': abs(qtd * unitario - subtotal) <= Decimal('0.02')})
    return itens


def formatar_itens(texto, valor_total):
    itens = extrair_itens(texto)
    if not itens:
        return "\n🛒 Não consegui identificar os produtos com segurança.\n"
    def dinheiro(valor):
        return 'R$ ' + format(valor, ',.2f').replace(',', '_').replace('.', ',').replace('_', '.')
    partes = ["\n🛒 Produtos identificados:"]
    for item in itens[:20]:
        qtd = format(item['quantidade'], 'f').rstrip('0').rstrip('.') if '.' in str(item['quantidade']) else str(item['quantidade'])
        partes.append(
            f"• {item['descricao'][:100]}\n"
            f"  {qtd.replace('.', ',')} {item['unidade']} × {dinheiro(item['unitario'])}"
            f" = {dinheiro(item['subtotal'])}"
            + ("" if item['conferido'] else " ⚠️ conferir"))
    if len(itens) > 20:
        partes.append(f"Mais {len(itens) - 20} produtos identificados.")
    soma = sum((item['subtotal'] for item in itens), Decimal('0'))
    partes.append(f"Soma dos itens identificados: {dinheiro(soma)}")
    if valor_total:
        total = Decimal(valor_total.replace('R$ ', '').replace('.', '').replace(',', '.'))
        if soma == total and all(item['conferido'] for item in itens):
            partes.append("✓ A soma dos itens bate com o total lido.")
        else:
            partes.append("⚠️ A soma ou os preços precisam de revisão; pode haver itens ausentes, descontos ou erro de leitura.")
    return "\n".join(partes) + "\n"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧾 *Bem-vindo ao Caderno do Pai - Controle de Gastos*\n\n"
        "Envie a foto de uma nota fiscal ou cupom e eu organizo os dados para você.\n\n"
        "_Dica: Fotos nítidas funcionam melhor._",
        parse_mode='Markdown'
    )

async def baixar_foto(bot, file_id):
    """Repetir apenas leituras, sem repetir gravações nem mensagens."""
    for attempt in range(2):
        try:
            file = await bot.get_file(file_id, read_timeout=60, connect_timeout=20, pool_timeout=20)
            return bytes(await file.download_as_bytearray(
                read_timeout=60, connect_timeout=20, pool_timeout=20))
        except TimedOut:
            if attempt == 1:
                raise
            await asyncio.sleep(1)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await family.private(update):
        return
    stage = "aviso inicial"
    try:
        await update.message.reply_text("📸 Processando sua nota...")
        stage = "download da foto"
        # Pega a foto
        photo = update.message.photo[-1]
        image_bytes = await baixar_foto(context.bot, photo.file_id)
        stage = "OCR"
        texto, erro = await asyncio.to_thread(ler_imagem, image_bytes)
        
        if erro:
            await update.message.reply_text(f"❌ {erro}")
            return
        
        # Extrai dados
        stage = "extração dos dados"
        dados = extrair_dados(texto)
        
        is_bill = dados.get("document_type") == "bill"
        detalhes = None if is_bill else formatar_itens(texto, dados["valor"])

        amount = family.parse_amount(dados["valor"]) if dados["valor"] else None
        expense_date = datetime.strptime(dados["data"], "%d/%m/%Y").date().isoformat() if dados["data"] else None
        items = [{key: str(value) if isinstance(value, Decimal) else value
                  for key, value in item.items()} for item in ([] if is_bill else extrair_itens(texto))]
        stage = "rascunho e confirmação"
        await family.propose(update, context, {
            "description": (dados["local"] or "Compra sem descrição")[:250],
            "amount": amount, "date": expense_date, "category": dados.get("category", "Outros"), "items": items,
            "photo_hash": hashlib.sha256(image_bytes).hexdigest(),
            "photo_unique_id": photo.file_unique_id,
            "document_type": dados.get("document_type", "receipt"),
            "date_label": dados.get("date_label", "Data da compra"), "due_date": dados.get("due_date", "")
        }, f"photo:{update.effective_chat.id}:{update.message.message_id}", detalhes)

        stage = "envio do diagnóstico"
        if (not amount or not expense_date or (not is_bill and not items)) and update.effective_chat.type == "private":
            # A leitura vai apenas para a conversa que enviou a nota.
            # Não gravar texto de notas em logs públicos ou no repositório.
            diagnostico = io.BytesIO(texto.encode("utf-8"))
            diagnostico.name = "leitura_da_nota.txt"
            await update.message.reply_document(
                document=diagnostico,
                caption="Leitura bruta para diagnóstico dos produtos. "
                        "Se estiver recebendo suporte, envie este arquivo ao suporte. "
                        "Ele pode conter dados da sua nota."
            )
        
        
    except Exception as e:
        logging.error("Falha ao processar nota na etapa %s: %s", stage, type(e).__name__)
        text = ("⏳ A comunicação com o Telegram demorou demais. "
                "Nenhum gasto é salvo automaticamente. Tente enviar a foto novamente."
                if isinstance(e, TimedOut) else
                "❌ Não consegui processar esta nota. Tente novamente em instantes.")
        try:
            await update.message.reply_text(text)
        except TimedOut:
            logging.error("Tempo limite ao enviar aviso de falha")

def main():
    app = (Application.builder().token(TELEGRAM_TOKEN)
           .connect_timeout(20).read_timeout(60).write_timeout(60).pool_timeout(20)
           .build())
    
    family.register(app)
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
            allowed_updates=["message", "callback_query"],
        )
    else:
        print("Bot iniciando localmente.", flush=True)
        app.run_polling()

if __name__ == '__main__':
    main()
