"""Exportações locais, sem serviços pagos ou compartilhamento adicional."""
import io
import re
import zipfile
import base64
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, date
from decimal import Decimal
from zoneinfo import ZoneInfo
from html import escape

TZ = ZoneInfo('America/Sao_Paulo')
NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
ET.register_namespace('', NS)
def tag(name): return '{' + NS + '}' + name
def brl(value):
    return 'R$ ' + format(Decimal(str(value)), ',.2f').replace(',', '_').replace('.', ',').replace('_', '.')
def stats(rows):
    cats = defaultdict(Decimal)
    for row in rows: cats[row['category']] += Decimal(str(row['amount']))
    return sum(cats.values(), Decimal('0')), sorted(cats.items(), key=lambda x: (-x[1], x[0]))
def clean(value):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))
def cell(root, address, value, numeric=False):
    data = root.find(tag('sheetData'))
    number = re.search(r'\d+', address)[0]
    row = next((r for r in data if r.get('r') == number), None)
    if row is None: row = ET.SubElement(data, tag('row'), {'r': number})
    c = next((c for c in row if c.get('r') == address), None)
    if c is None: c = ET.SubElement(row, tag('c'), {'r': address})
    for child in list(c): c.remove(child)
    c.set('t', 'n' if numeric else 'inlineStr')
    if numeric: ET.SubElement(c, tag('v')).text = str(value)
    else:
        t = ET.SubElement(ET.SubElement(c, tag('is')), tag('t'))
        t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        t.text = clean(value)
def excel(rows, house, month):
    from report_template import TEMPLATE
    source = zipfile.ZipFile(io.BytesIO(base64.b64decode(TEMPLATE)))
    summary = ET.fromstring(source.read('xl/worksheets/sheet1.xml'))
    detail = ET.fromstring(source.read('xl/worksheets/sheet2.xml'))
    cell(summary, 'A2', f'{house} | {month:%m/%Y}')
    if len(rows) >= 2000:
        cell(summary, 'A30', 'Limite de 2.000 registros atingido. Este relatório pode estar incompleto.')
    total, categories = stats(rows)
    pending = sum((Decimal(str(r['amount'])) for r in rows if r.get('payment_status') == 'pending'),Decimal('0'))
    paid = sum((Decimal(str(r['amount'])) for r in rows if r.get('payment_status') == 'paid'),Decimal('0'))
    cached = {'B4': total, 'B5': len(rows), 'B6': total / len(rows) if rows else 0,'B33':total-pending-paid,'B34':paid,'B35':pending}
    for c in summary.iter(tag('c')):
        address = c.get('r')
        if c.find(tag('f')) is None: continue
        if address not in cached:
            n = int(re.search(r'\d+', address)[0]); a = summary.find(f".//{tag('c')}[@r='A{n}']")
            name = ''.join(a.itertext())
            if a.get('t') == 's':
                strings = ET.fromstring(source.read('xl/sharedStrings.xml'))
                name = ''.join(list(strings)[int(name)].itertext())
            value = dict(categories).get(name, Decimal('0'))
            cached[address] = value / total if address.startswith('C') and total else (0 if address.startswith('C') else value)
        old = c.find(tag('v'))
        if old is None: old = ET.SubElement(c, tag('v'))
        old.text = str(cached[address]); c.set('t', 'n')
    for n, row in enumerate(rows, 5):
        created = datetime.fromisoformat(row['created_at'].replace('Z','+00:00')).astimezone(TZ)
        def day(key): return date.fromisoformat(row[key]).strftime('%d/%m/%Y') if row.get(key) else ''
        values = [row['id'], day('expense_date') if row.get('document_type') != 'bill' else '', created.strftime('%d/%m/%Y %H:%M'), row['description'], row['category'], Decimal(str(row['amount'])),day('document_date'),day('due_date'),{'pending':'A pagar','paid':'Paga'}.get(row.get('payment_status'),'Compra registrada'),day('payment_date'),'Conta' if row.get('document_type')=='bill' else 'Compra']
        for col, value in zip('ABCDEFGHIJK', values): cell(detail, f'{col}{n}', value, col in 'AF')
    data = detail.find(tag('sheetData'))
    for r in list(data):
        if int(r.get('r')) > max(4, len(rows)+4): data.remove(r)
    filt = ET.Element(tag('autoFilter'), {'ref': f'A4:K{max(4,len(rows)+4)}'})
    detail.insert(list(detail).index(data)+1, filt)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as dest:
        for entry in source.infolist():
            payload = source.read(entry.filename)
            if entry.filename == 'xl/worksheets/sheet1.xml': payload = ET.tostring(summary,encoding='utf-8',xml_declaration=True)
            if entry.filename == 'xl/worksheets/sheet2.xml': payload = ET.tostring(detail,encoding='utf-8',xml_declaration=True)
            dest.writestr(entry.filename,payload)
    out.seek(0); out.name = f'gastos-da-casa-{month:%Y-%m}.xlsx'; return out

def pdf(rows, house, month):
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.graphics.shapes import Drawing, Rect, String
    out = io.BytesIO(); total, categories = stats(rows)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='Brand', fontName='Helvetica-Bold',fontSize=24,leading=29,textColor=colors.HexColor('#143D32'),spaceAfter=10))
    styles.add(ParagraphStyle(name='SmallCell',fontSize=9,leading=13))
    p=lambda text, style='Normal': Paragraph(escape(clean(text)),styles[style])
    story=[p('Caderno do Pai','Brand'),p(f'{house} | Controle de {month:%m/%Y}','Heading2'),p('Compras pela compra; contas pagas pelo pagamento; pendentes pelo vencimento.'),Spacer(1,18)]
    if len(rows)>=2000: story += [p('Atenção: limite de 2.000 registros atingido. Este relatório pode estar incompleto.'),Spacer(1,10)]
    if not rows: story += [p('Nenhum gasto registrado neste mês.')]
    else:
        pending=sum((Decimal(str(r['amount'])) for r in rows if r.get('payment_status')=='pending'),Decimal('0'))
        paid=sum((Decimal(str(r['amount'])) for r in rows if r.get('payment_status')=='paid'),Decimal('0'))
        card=Table([[p('COMPRAS REGISTRADAS'),p('CONTAS PAGAS'),p('CONTAS A PAGAR')],[p(brl(total-pending-paid),'Heading2'),p(brl(paid),'Heading2'),p(brl(pending),'Heading2')]],colWidths=[173,171,171])
        card.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#EAF3EE')),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),12),('TOPPADDING',(0,0),(-1,-1),10),('BOTTOMPADDING',(0,0),(-1,-1),10)]));story += [card,Spacer(1,10),p(f'Total de compromissos: {brl(total)} | Registros: {len(rows)}'),Spacer(1,12),p('Compromissos por categoria','Heading2')]
        height=len(categories)*29+8; graph=Drawing(515,height); maximum=max(v for _,v in categories)
        for i,(name,value) in enumerate(categories):
            y=height-29*(i+1)
            graph.add(String(0,y+8,name,fontName='Helvetica',fontSize=9))
            graph.add(Rect(170,y+4,190*float(value/maximum),13,fillColor=colors.HexColor('#388268'),strokeColor=None))
            graph.add(String(375,y+7,brl(value)+f' ({float(value/total):.1%})',fontSize=9))
        story += [graph,Spacer(1,14),p('Maiores despesas','Heading2')]
        table=[[p('Data / situação','SmallCell'),p('Descrição / categoria','SmallCell'),p('Valor','SmallCell')]]
        for row in sorted(rows,key=lambda r:Decimal(str(r['amount'])),reverse=True)[:5]:
            key = 'payment_date' if row.get('payment_status')=='paid' else ('due_date' if row.get('document_type')=='bill' else 'expense_date')
            label = {'pending':'A pagar','paid':'Paga'}.get(row.get('payment_status'),'Compra')
            table.append([p(date.fromisoformat(row[key]).strftime('%d/%m/%Y')+' / '+label,'SmallCell'),p(row['description']+' | '+row['category'],'SmallCell'),p(brl(row['amount']),'SmallCell')])
        t=Table(table,colWidths=[75,340,100],repeatRows=1);t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#EAF3EE')),('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),9),('LINEBELOW',(0,0),(-1,-1),0.4,colors.HexColor('#D7E3DC'))]));story += [t,Spacer(1,16)]
        others=dict(categories).get('Outros',0)
        if others: story += [p(f'{brl(others)} estão em Outros. Revisar essas categorias deixa o resumo mais útil.'),Spacer(1,10)]
    story += [p('Este relatório inclui somente os gastos lançados no bot. Ele não informa saldo bancário nem confirma pagamento.'),Spacer(1,8),p('Para consultar todos os registros e as duas datas, baixe a planilha Excel.')]
    def footer(canvas,doc):
        canvas.setFont('Helvetica',8);canvas.setFillColor(colors.HexColor('#64756B'));canvas.drawString(40,25,'Caderno do Pai | Controle dos gastos da casa');canvas.drawRightString(A4[0]-40,25,f'Página {doc.page}')
    SimpleDocTemplate(out,pagesize=A4,rightMargin=40,leftMargin=40,topMargin=35,bottomMargin=45,title='Gastos da casa',author='Caderno do Pai').build(story,onFirstPage=footer,onLaterPages=footer)
    out.seek(0);out.name=f'relatorio-da-casa-{month:%Y-%m}.pdf';return out
