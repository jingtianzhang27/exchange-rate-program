import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import ssl
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from backend.core import TZ, connect, history, init_db, parse_boc, record_quote, summarize, units, validate_rule, window_day
from backend.server import Application, handler
from backend.integrations import dispatch, analyze, quote_failure, WechatSender


class RulesTest(unittest.TestCase):
    def test_exchange_rate_template_payload(self):
        mapping={'character_string1':'price','thing5':'reason','time2':'time','thing3':'bank','thing4':'currency'}
        with patch.dict('os.environ',{'WX_APP_ID':'wx-test','WX_APP_SECRET':'test-only','WX_TEMPLATE_ID':'test-template',
                                     'WX_TEMPLATE_MAPPING':json.dumps(mapping)}), \
             patch('backend.integrations.request_json',side_effect=[{'access_token':'test-token','expires_in':7200},{'errcode':0}]) as request:
            WechatSender().send('test-user',{'price':6660600,'stamp':'2026-10-28T15:38:18+08:00','side':'up'})
            payload=request.call_args.args[1]
            self.assertEqual(payload['data'],{'character_string1':{'value':'6.6606'},'thing5':{'value':'涨幅达标'},
                                             'time2':{'value':'2026-10-28 15:38:18'},'thing3':{'value':'中国银行'},
                                             'thing4':{'value':'美元兑人民币'}})

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name)/'test.db')
        init_db(self.path)
        with connect(self.path) as db:
            db.execute("INSERT INTO users VALUES('u','openid',0)")

    def tearDown(self):
        self.tmp.cleanup()

    def add_rule(self, kind='movement', amount='0.01', direction='above', start='08:00', end='17:00'):
        with connect(self.path) as db:
            db.execute('INSERT INTO rules VALUES(?,?,?,?,?,?,?,?,?)', ('r','u',kind,start,end,units(amount),direction,1,'2026-10-21T00:00:00+08:00'))

    def quote(self, price, stamp):
        return record_quote(self.path,units(price),stamp,datetime.fromisoformat(stamp))

    def test_parser_uses_cashless_not_cash_or_selling_and_divides_100(self):
        html = '''<table><tr><th>货币名称</th><th>现汇买入价</th><th>现钞买入价</th><th>现汇卖出价</th><th>现钞卖出价</th><th>中行折算价</th><th>发布时间</th></tr>
          <tr><td>美元</td><td><span>670.12</span></td><td>660.00</td><td>680.00</td><td>680.00</td><td>675.00</td><td>2026.10.22 09:00:00</td></tr></table>'''
        price, stamp = parse_boc(html)
        self.assertEqual(price,6701200)
        self.assertEqual(stamp,'2026-10-22T09:00:00+08:00')
        with self.assertRaises(ValueError):
            parse_boc(html.replace('现汇买入价','未知栏目'))

    def test_boc_slash_date_in_date_cell_and_repeated_time_cell(self):
        # Current BOC layout has a full slash-separated datetime in 发布日期,
        # followed by a separate 发布时间 cell. Cashless/cash values differ here
        # deliberately so a wrong price-column selection cannot pass.
        template = '''<table><tr><th>货币名称</th><th>现汇买入价</th><th>现钞买入价</th><th>现汇卖出价</th><th>现钞卖出价</th><th>中行折算价</th><th>发布日期</th><th>发布时间</th></tr>
          <tr data-currency="美元"><td>美元</td><td>666.06</td><td>665.00</td><td>668.86</td><td>668.86</td><td>671.8</td><td class="pjrq">DATE 15:38:18</td><td>15:38:18</td></tr></table>'''
        for date in ('2026/10/23','2026-10-23','2026.10.23'):
            with self.subTest(date=date):
                price, stamp = parse_boc(template.replace('DATE',date))
                self.assertEqual(price,6660600)
                self.assertEqual(stamp,'2026-10-23T15:38:18+08:00')

    def test_quote_diagnostics_classify_without_exposing_secrets(self):
        secret_url='https://user:do-not-print@example.test/?token=private'
        http_error=urllib.error.HTTPError(secret_url,403,'private message',{},None)
        self.assertIn('HTTP 403',quote_failure(http_error))
        cert_error=urllib.error.URLError(ssl.SSLCertVerificationError('private message'))
        self.assertIn('证书验证失败',quote_failure(cert_error))
        timeout=urllib.error.URLError(TimeoutError('private message'))
        self.assertIn('超时',quote_failure(timeout))
        for error in (http_error,cert_error,timeout,ValueError('private message'),RuntimeError(secret_url)):
            message=quote_failure(error)
            self.assertNotIn('private',message)
            self.assertNotIn('do-not-print',message)

    def test_threshold_exact_and_no_duplicates_both_directions(self):
        self.add_rule()
        self.assertEqual(self.quote('6.70','2026-10-22T08:00:00+08:00'),0)
        self.assertEqual(self.quote('6.7099','2026-10-22T08:01:00+08:00'),0)
        self.assertEqual(self.quote('6.71','2026-10-22T08:02:00+08:00'),1)
        self.assertEqual(self.quote('6.72','2026-10-22T08:03:00+08:00'),0)
        self.assertEqual(self.quote('6.69','2026-10-22T08:04:00+08:00'),1)
        self.assertEqual(self.quote('6.68','2026-10-22T08:04:00+08:00'),0)

    def test_daily_baseline_reset_and_outside_window(self):
        self.add_rule()
        self.assertEqual(self.quote('6.5','2026-10-22T07:59:00+08:00'),0)
        self.assertEqual(self.quote('6.70','2026-10-22T08:00:00+08:00'),0)
        self.assertEqual(self.quote('6.71','2026-10-22T17:00:00+08:00'),1)
        self.assertEqual(self.quote('6.5','2026-10-22T17:01:00+08:00'),0)
        self.assertEqual(self.quote('6.80','2026-10-23T08:00:00+08:00'),0)
        self.assertEqual(self.quote('6.81','2026-10-23T08:01:00+08:00'),1)

    def test_cross_midnight_uses_previous_day(self):
        self.add_rule(start='22:00',end='06:00')
        self.quote('6.7','2026-10-22T22:00:00+08:00')
        self.assertEqual(self.quote('6.71','2026-10-23T01:00:00+08:00'),1)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT day FROM alerts').fetchone()[0],'2026-10-22')
        self.assertIsNone(window_day({'start':'22:00','end':'06:00'},datetime(2026,10,23,12,tzinfo=TZ)))

    def test_target_crosses_without_exact_equality(self):
        self.add_rule(kind='target',amount='6.7')
        self.assertEqual(self.quote('6.69','2026-10-22T09:00:00+08:00'),0)
        self.assertEqual(self.quote('6.701','2026-10-22T09:01:00+08:00'),1)
        self.assertEqual(self.quote('6.72','2026-10-22T09:02:00+08:00'),0)

    def test_target_below(self):
        self.add_rule(kind='target',amount='6.7',direction='below')
        self.assertEqual(self.quote('6.71','2026-10-22T09:00:00+08:00'),0)
        self.assertEqual(self.quote('6.699','2026-10-22T09:01:00+08:00'),1)

    def test_stale_and_out_of_order_stored_but_not_alerted(self):
        self.add_rule(kind='target',amount='6.7')
        stamp = '2026-10-22T08:00:00+08:00'
        self.assertEqual(record_quote(self.path,units('6.8'),stamp,datetime(2026,10,22,10,tzinfo=TZ)),0)
        self.assertEqual(self.quote('6.8','2026-10-22T07:59:00+08:00'),0)
        self.assertEqual(len(history(self.path,'2026-10-22T00:00:00+08:00','2026-10-23T00:00:00+08:00')),2)

    def test_invalid_rules_and_nan(self):
        for value in ('NaN','Infinity','0','-0.01','100',None):
            with self.assertRaises((ValueError,TypeError)):
                units(value)
        for start,end in (('25:00','17:00'),('08:00','08:00')):
            with self.assertRaises(ValueError):
                validate_rule({'kind':'movement','start':start,'end':end,'amount':'.01'})

    def test_no_subscription_never_calls_sender(self):
        from backend.core import now
        self.add_rule(kind='target',amount='6.7',start='00:00',end='23:59')
        self.quote('6.8',now().isoformat())
        class Sender:
            def configured(self): return True
            def send(self,*args): raise AssertionError('must not send')
        dispatch(self.path,Sender())
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT status FROM alerts').fetchone()[0],'needs_subscription')

    def test_notification_sent_once_with_credit(self):
        from backend.core import now
        self.add_rule(kind='target',amount='6.7',start='00:00',end='23:59')
        self.quote('6.8',now().isoformat())
        with connect(self.path) as db:
            db.execute("UPDATE users SET credits=1 WHERE id='u'")
        class Sender:
            calls = 0
            def configured(self): return True
            def send(self,*args): self.calls += 1
        sender = Sender()
        dispatch(self.path,sender)
        dispatch(self.path,sender)
        self.assertEqual(sender.calls,1)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT status FROM alerts').fetchone()[0],'sent')
            self.assertEqual(db.execute('SELECT credits FROM users').fetchone()[0],0)

    def test_stats_fallback_not_labeled_ai(self):
        stats = summarize([{'price':6.7,'stamp':'a'},{'price':6.71,'stamp':'b'}])
        self.assertEqual(stats['change'],.01)
        with patch.dict('os.environ',{'AI_API_KEY':'','AI_MODEL':''}):
            self.assertEqual(analyze(stats)['mode'],'statistics')
        with self.assertRaises(ValueError): summarize([])


class HttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = Application(str(Path(self.tmp.name)/'api.db'),demo=True)
        self.server = ThreadingHTTPServer(('127.0.0.1',0),handler(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.tmp.cleanup()

    def call(self,path,method='GET',data=None,token=None):
        headers = {'Content-Type':'application/json'}
        if token: headers['Authorization'] = 'Bearer '+token
        request = urllib.request.Request(self.base+path,method=method,data=json.dumps(data).encode() if data is not None else None,headers=headers)
        try:
            with urllib.request.urlopen(request) as r: return r.status,json.load(r)
        except urllib.error.HTTPError as e: return e.code,json.load(e)

    def test_full_workflow_and_user_isolation(self):
        self.assertEqual(self.call('/api/rules')[0],401)
        token = self.call('/api/login','POST',{'code':'first-user'})[1]['token']
        other = self.call('/api/login','POST',{'code':'second-user'})[1]['token']
        status, rule = self.call('/api/rules','POST',{'kind':'target','start':'00:00','end':'23:59','amount':'6.7','direction':'above'},token)
        self.assertEqual(status,200)
        self.assertEqual(len(self.call('/api/rules',token=token)[1]['rules']),1)
        self.assertEqual(self.call('/api/rules',token=other)[1]['rules'],[])
        self.assertEqual(self.call('/api/rules/'+rule['id'],'DELETE',token=other)[0],404)
        from backend.core import now
        record_quote(self.app.path,units('6.8'),(now()+timedelta(seconds=1)).isoformat(),now())
        self.assertEqual(len(self.call('/api/alerts',token=token)[1]['alerts']),1)
        self.assertEqual(self.call('/api/alerts',token=other)[1]['alerts'],[])
        self.assertTrue(self.call('/api/status',token=token)[1]['demo'])
        self.assertEqual(self.call('/api/subscriptions','POST',{'accepted':True},token)[0],400)
        self.assertEqual(self.call('/api/rules/'+rule['id'],'DELETE',token=token)[0],200)
        self.assertEqual(self.call('/api/rules',token=token)[1]['rules'],[])

    def test_real_login_returns_safe_actionable_errors(self):
        self.app.demo=False
        with patch.dict('os.environ',{'WX_APP_ID':'','WX_APP_SECRET':''}):
            status, body=self.call('/api/login','POST',{'code':'new-code'})
        self.assertEqual(status,503)
        self.assertEqual(body['code'],'wechat_config_missing')
        with patch.dict('os.environ',{'WX_APP_ID':'wx-test','WX_APP_SECRET':'not-a-real-key'}):
            for errcode in (40013,40125,40029,40163):
                with self.subTest(errcode=errcode), patch('backend.integrations.request_json',return_value={
                        'errcode':errcode,'errmsg':'sensitive token, secret and URL must never be returned'}):
                    status,body=self.call('/api/login','POST',{'code':'private-code'})
                    self.assertEqual(status,400)
                    self.assertEqual(body['wechat_errcode'],errcode)
                    self.assertNotIn('sensitive',json.dumps(body))
                    self.assertNotIn('not-a-real-key',json.dumps(body))
                    self.assertNotIn('private-code',json.dumps(body))

    def test_real_login_transport_failure_and_success(self):
        self.app.demo=False
        with patch.dict('os.environ',{'WX_APP_ID':'wx-test','WX_APP_SECRET':'not-a-real-key'}):
            with patch('backend.integrations.request_json',side_effect=urllib.error.URLError('private URL and secret')):
                status, body=self.call('/api/login','POST',{'code':'new-code'})
                self.assertEqual(status,502)
                self.assertEqual(body['code'],'wechat_upstream_error')
                self.assertNotIn('private',json.dumps(body))
            cert_error=ssl.SSLCertVerificationError('private secret-bearing request URL')
            cert_error.verify_code=20
            with patch('backend.integrations.request_json',side_effect=urllib.error.URLError(cert_error)):
                status,body=self.call('/api/login','POST',{'code':'new-code'})
                self.assertEqual(status,502)
                self.assertEqual(body['code'],'wechat_tls_error')
                self.assertIn('verify_code=20',body['error'])
                self.assertNotIn('private',json.dumps(body))
            with patch('backend.integrations.request_json',return_value={'openid':'real-user-test','session_key':'never-return-this'}):
                status,body=self.call('/api/login','POST',{'code':'new-code'})
                self.assertEqual(status,200)
                self.assertFalse(body['demo'])
                self.assertIn('token',body)
                self.assertNotIn('never-return-this',json.dumps(body))
                self.assertEqual(self.call('/api/status',token=body['token'])[0],200)


if __name__ == '__main__':
    unittest.main()
