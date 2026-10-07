import unittest
from bot import extrair_dados
from categorization import suggest_category

class ReceiptTotalTests(unittest.TestCase):
    def test_discounted_total_is_amount_due(self):
        text='Drogal\nValor Total R$:\t172,51\nDesconto R$\t39,20\nValor a Pagar R$:\t133,25\nFORMA PAGAMENTO:\tValor Pago R$.\nCartão de Credito\t133.25'
        self.assertEqual(extrair_dados(text)['valor'],'R$ 133,25')
    def test_conflicting_final_totals_still_require_review(self):
        self.assertEqual(extrair_dados('Valor a Pagar R$: 133,25\nTotal a pagar 134,25')['valor'],'')
    def test_gross_total_is_fallback(self):
        self.assertEqual(extrair_dados('Valor Total R$: 172,51')['valor'],'R$ 172,51')
    def test_drogal_category(self):
        self.assertEqual(suggest_category('Drogal DROGAL FARMACEUTICALIDA'),'Saúde')
