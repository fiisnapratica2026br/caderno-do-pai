"""Datas de fatura e divisão em centavos, sem presumir pagamento."""
from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

def add_month(value, offset, day=None):
    index=value.year*12+value.month-1+offset
    year,month=divmod(index,12);month+=1
    return date(year,month,min(day or value.day,monthrange(year,month)[1]))

def first_due(purchase, closing, due):
    if not 1 <= closing <= 31 or not 1 <= due <= 31:raise ValueError('Dia inválido')
    close=date(purchase.year,purchase.month,min(closing,monthrange(purchase.year,purchase.month)[1]))
    if purchase >= close: close=add_month(close,1,closing)
    candidate=date(close.year,close.month,min(due,monthrange(close.year,close.month)[1]))
    if candidate <= close:candidate=add_month(candidate,1,due)
    return candidate

def installments(total,count):
    cents=int((Decimal(str(total))*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    if not 1 <= count <= 36 or cents<count:raise ValueError('Parcelas inválidas')
    base,remainder=divmod(cents,count)
    return [Decimal(base+(i<remainder))/100 for i in range(count)]
