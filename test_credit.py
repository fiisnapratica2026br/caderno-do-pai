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
