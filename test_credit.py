import unittest
from datetime import date
from decimal import Decimal
from credit import first_due,add_month,installments
import family

class CreditTests(unittest.TestCase):
 def test_before_close(self):self.assertEqual(first_due(date(2026,10,6),22,28),date(2026,10,28))
 def test_after_close(self):self.assertEqual(first_due(date(2026,10,23),22,28),date(2026,11,28))
 def test_closing_day_is_conservative(self):self.assertEqual(first_due(date(2026,10,22),22,28),date(2026,11,28))
 def test_due_before_closing(self):self.assertEqual(first_due(date(2026,10,6),22,5),date(2026,11,5))
 def test_year_rollover(self):self.assertEqual(first_due(date(2026,12,23),22,28),date(2027,1,28))
 def test_short_month(self):self.assertEqual(first_due(date(2027,2,1),22,31),date(2027,2,28))
 def test_exact_cents(self):self.assertEqual(installments('133.25',3),[Decimal('44.42'),Decimal('44.42'),Decimal('44.41')])
 def test_one_installment(self):self.assertEqual(installments('133.25',1),[Decimal('133.25')])
 def test_too_many_installments(self):
  with self.assertRaises(ValueError):installments('0.02',3)
 def test_preview_has_due_not_paid(self):
  data={'payment_method':'credit','amount':'133.25','installments':3,'card_name':'Meu cartão','date':'2026-10-06','first_due':'2026-10-28','description':'Drogal'}
  text=family.draft_text(data)
  self.assertIn('44,42',text);self.assertIn('44,41',text);self.assertIn('28/10/2026',text)
  self.assertIn('a pagar',text)
 def test_help_fits_telegram(self):self.assertLess(len(family.HELP_TEXT),4096)

from unittest.mock import AsyncMock, patch
import panel_server
from telegram.error import RetryAfter
class StartupRetryTests(unittest.IsolatedAsyncioTestCase):
 async def test_telegram_wait_is_respected(self):
  operation=AsyncMock(side_effect=[RetryAfter(5),'ok'])
  with patch.object(panel_server.asyncio,'sleep',new_callable=AsyncMock) as sleep:
   self.assertEqual(await panel_server.startup_retry(operation),'ok')
   sleep.assert_awaited_once_with(6)
  self.assertEqual(operation.await_count,2)
 async def test_permanent_error_not_hidden(self):
  with self.assertRaises(ValueError):await panel_server.startup_retry(AsyncMock(side_effect=ValueError('invalid')))

from types import SimpleNamespace as NS
class InstallmentConversationTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.data={'description':'Drogal','amount':'133.25','date':'2026-10-06','document_type':'receipt','category':'Saúde'}
  self.cards=[{'name':'Meu cartão inter','closing_day':22,'due_day':28}]
  self.calls=[]
  self.context=NS(user_data={})
 async def backend(self,user,action,data=None):
  self.calls.append((user,action,data))
  if action=='get_draft':return {'data':dict(self.data)}
  if action=='cards':return self.cards
  if action=='edit':
   self.data.update(data['patch']);return dict(self.data)
  raise AssertionError('Unexpected action: '+action)
 def callback_update(self,value):
  message=NS(reply_text=AsyncMock())
  return NS(callback_query=NS(data=value,answer=AsyncMock(),message=message,edit_message_text=AsyncMock()),effective_chat=NS(type='private'),effective_user=NS(id=42))
 def text_update(self,value):
  message=NS(text=value,reply_text=AsyncMock())
  return NS(message=message,effective_chat=NS(type='private'),effective_user=NS(id=42))
 async def test_screenshot_sequence_preserves_original_purchase(self):
  with patch.object(family,'api',self.backend):
   await family.callback(self.callback_update('credit:draft'),self.context)
   await family.handle_text(self.text_update('3 parcelas'),self.context)
   self.assertFalse(any(a=='draft' for _,a,_ in self.calls))
   await family.callback(self.callback_update('pickcard:draft:0'),self.context)
  self.assertEqual(self.data['amount'],'133.25')
  self.assertEqual(self.data['description'],'Drogal')
  self.assertEqual(self.data['installments'],3)
  self.assertEqual(self.data['first_due'],'2026-10-28')
 async def test_quantity_text_after_card_is_accepted(self):
  self.data.update(payment_method='credit',card_name='Meu cartão inter',installments=1,first_due='2026-10-28')
  self.context.user_data['editing']=('installments','draft')
  with patch.object(family,'api',self.backend):await family.handle_text(self.text_update('3 parcelas'),self.context)
  self.assertEqual(self.data['amount'],'133.25');self.assertEqual(self.data['installments'],3)
 async def test_quantity_without_context_never_creates_purchase(self):
  backend=AsyncMock()
  with patch.object(family,'api',backend):await family.handle_text(self.text_update('3 parcelas'),self.context)
  backend.assert_not_awaited()
 async def test_inline_quantity_updates_same_draft(self):
  self.data.update(payment_method='credit',card_name='Meu cartão inter',installments=1,first_due='2026-10-28')
  with patch.object(family,'api',self.backend):await family.callback(self.callback_update('creditqty:draft:3'),self.context)
  self.assertEqual(self.data['amount'],'133.25');self.assertEqual(self.data['installments'],3)
  edits=[d for _,a,d in self.calls if a=='edit']
  self.assertEqual(edits,[{'id':'draft','patch':{'installments':3}}])
