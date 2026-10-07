import unittest
from unittest.mock import AsyncMock,patch
from types import SimpleNamespace as NS
import family

class DateFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_bill_starts_pending_without_inventing_emission(self):
        update=NS(effective_chat=NS(type='private'),effective_user=NS(id=42),effective_message=NS(reply_text=AsyncMock()))
        backend=AsyncMock(side_effect=[{},{}])
        with patch.object(family,'api',backend):
            await family.propose(update,NS(user_data={}),{'document_type':'bill','date':'2026-10-06','description':'internet','amount':'59.90','category':'Internet e telefone'},'text:42:1')
        data=backend.call_args_list[0].args[2]['data']
        self.assertIsNone(data['document_date'])
        self.assertEqual(data['payment_status'],'pending')
        self.assertIsNone(data['payment_date'])
    async def test_saved_conversion_asks_due_date_before_writing(self):
        q=NS(data='expbill:31',answer=AsyncMock(),message=NS(reply_text=AsyncMock()))
        update=NS(callback_query=q,effective_chat=NS(type='private'),effective_user=NS(id=42))
        context=NS(user_data={})
        backend=AsyncMock(return_value={'document_type':'receipt'})
        with patch.object(family,'api',backend):await family.callback(update,context)
        backend.assert_awaited_once_with(42,'get_expense',{'expense_id':31})
        self.assertEqual(context.user_data['editing'],('convert_due','expense:31'))
    async def test_conversion_uses_same_id_and_authenticated_user(self):
        msg=NS(text='10/10/2026',reply_text=AsyncMock())
        update=NS(message=msg,effective_chat=NS(type='private'),effective_user=NS(id=42))
        context=NS(user_data={'editing':('convert_due','expense:31')})
        backend=AsyncMock(return_value={'missing':True})
        with patch.object(family,'api',backend):await family.handle_text(update,context)
        backend.assert_awaited_once_with(42,'convert_bill',{'expense_id':31,'patch':{'due_date':'2026-10-10'}})
    def test_both_types_can_be_selected_in_preview(self):
        receipt=[b.callback_data for row in family.buttons('draft',{}).inline_keyboard for b in row]
        bill=[b.callback_data for row in family.buttons('draft',{'document_type':'bill'}).inline_keyboard for b in row]
        self.assertIn('asbill:draft',receipt)
        self.assertIn('asreceipt:draft',bill)
        self.assertIn('due_date:draft',bill)
        self.assertIn('document_date:draft',bill)
