import asyncio
import hashlib
import hmac
import json
import os
import time
import unittest
from types import SimpleNamespace
from urllib.parse import urlencode
from unittest.mock import patch

from tornado.testing import AsyncHTTPTestCase
import panel_server as panel

TOKEN = "123456:test-token-for-unit-tests"

def signed(user=42, auth_date=None, **extra):
    fields = {"auth_date": str(int(time.time()) if auth_date is None else auth_date),
              "user": json.dumps({"id": user, "first_name": "Teste"}), **extra}
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)

class AuthTests(unittest.TestCase):
    def test_valid_signature_with_telegram_signature_field(self):
        self.assertEqual(panel.validate_init_data(signed(signature="extra-field"), TOKEN), 42)
    def test_forged_user_rejected(self):
        raw = signed().replace("%3A+42", "%3A+99")
        self.assertNotEqual(raw, signed())
        with self.assertRaises(ValueError): panel.validate_init_data(raw, TOKEN)
    def test_other_bot_rejected(self):
        with self.assertRaises(ValueError): panel.validate_init_data(signed(), "other-token")
    def test_expired_rejected(self):
        with self.assertRaises(ValueError): panel.validate_init_data(signed(auth_date=int(time.time())-3601), TOKEN)
    def test_future_rejected(self):
        with self.assertRaises(ValueError): panel.validate_init_data(signed(auth_date=int(time.time())+120), TOKEN)
    def test_duplicate_fields_rejected(self):
        with self.assertRaises(ValueError): panel.validate_init_data(signed()+"&user=%7B%7D", TOKEN)
    def test_bool_user_rejected(self):
        with self.assertRaises(ValueError): panel.validate_init_data(signed(user=True), TOKEN)
    def test_february_and_year_rollover(self):
        self.assertEqual(panel.month_period("2028-02"), ("2028-02-01", "2028-03-01"))
        self.assertEqual(panel.month_period("2026-12"), ("2026-12-01", "2027-01-01"))
    def test_summary_excludes_pending_from_recorded(self):
        rows=[{"amount":"12.15","category":"Mercado","payment_status":"recorded"},
              {"amount":"20.25","category":"Energia","payment_status":"paid"},
              {"amount":"45.80","category":"Energia","payment_status":"pending"}]
        self.assertEqual(panel.summary(rows), {"recorded":"32.40","pending":"45.80","count":3,
            "categories":{"Mercado":"12.15","Energia":"20.25"}})

class HttpTests(AsyncHTTPTestCase):
    def get_app(self):
        self.calls=[]
        self.queue=asyncio.Queue()
        self.telegram=SimpleNamespace(bot=None,update_queue=self.queue)
        async def backend(user,action,data=None):
            self.calls.append((user,action,data))
            if action=="home":return {"name":"Casa teste","owner_telegram_id":user}
            if action in ("list","bills"):return []
            if action=="delete":return {"deleted":None}
            if action=="update_bill":return {"missing":True}
        return panel.create_http_app(self.telegram,TOKEN,backend)
    def request(self,payload,headers=None):
        return self.fetch('/api/panel',method="POST",body=json.dumps(payload),headers=headers or {'Content-Type':'application/json'})
    def test_missing_auth_does_not_touch_backend(self):
        self.assertEqual(self.request({"month":"2026-10"}).code,401)
        self.assertEqual(self.calls,[])
    def test_snapshot_uses_signed_identity_not_claimed_id(self):
        response=self.request({"initData":signed(42),"month":"2026-10","user_id":99})
        self.assertEqual(response.code,200)
        self.assertTrue(all(call[0]==42 for call in self.calls))
        self.assertNotIn("owner_telegram_id", response.body.decode())
    def test_delete_scoped_to_signed_user(self):
        response=self.request({"initData":signed(42),"action":"delete","id":123,"user_id":99})
        self.assertEqual(response.code,404)
        self.assertEqual(self.calls,[(42,"delete",{"expense_id":123})])
    def test_unknown_action_not_forwarded(self):
        self.assertEqual(self.request({"initData":signed(),"action":"arbitrary_rpc"}).code,400)
        self.assertEqual(self.calls,[])
    def test_future_payment_not_forwarded(self):
        self.assertEqual(self.request({"initData":signed(),"action":"pay","id":123,"date":"2999-01-01"}).code,400)
        self.assertEqual(self.calls,[])
    def test_untrusted_origin_rejected(self):
        self.assertEqual(self.request({"initData":signed(),"month":"2026-10"}, {'Origin':'https://evil.example'}).code,403)
        self.assertEqual(self.calls,[])
    def test_configured_origin_preflight(self):
        with patch.dict(os.environ,{"PANEL_ORIGIN":"https://panel.example"}):
            response=self.fetch('/api/panel',method='OPTIONS',headers={'Origin':'https://panel.example'})
        self.assertEqual(response.code,204)
        self.assertEqual(response.headers['Access-Control-Allow-Origin'],'https://panel.example')
    def test_webhook_requires_secret(self):
        response=self.fetch('/telegram',method='POST',body='{"update_id":1}')
        self.assertEqual(response.code,403)
        self.assertTrue(self.queue.empty())
    def test_verified_webhook_queues_update(self):
        secret=hashlib.sha256(('webhook:'+TOKEN).encode()).hexdigest()
        response=self.fetch('/telegram',method='POST',body='{"update_id":1}',headers={'X-Telegram-Bot-Api-Secret-Token':secret})
        self.assertEqual(response.code,200)
        self.assertEqual(self.queue.get_nowait().update_id,1)
    def test_panel_and_static_assets_public_but_no_data(self):
        response=self.fetch('/painel')
        self.assertEqual(response.code,200)
        self.assertIn('no-store',response.headers['Cache-Control'])
        self.assertEqual(self.fetch('/panel.js').code,200)
        self.assertEqual(self.fetch('/panel.css').code,200)
        self.assertNotIn('test-token',response.body.decode())

if __name__ == '__main__': unittest.main()
