"""Run with python3 -m backend.server. No third party runtime dependencies."""
import argparse
import hashlib
import json
import logging
import math
import os
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from backend.core import SCALE, connect, history, init_db, now, record_quote, summarize, validate_rule
from backend.integrations import WechatSender, analyze, dispatch, fetch_quote, quote_failure, wx_login

LOG = logging.getLogger('rates')


class Application:
    def __init__(self, path, demo=False):
        self.path, self.demo = path, demo
        self.last_attempt = None
        self.collect_error = None
        self.sender = WechatSender()
        init_db(path)

    def collect(self):
        self.last_attempt = now().isoformat()
        try:
            if self.demo:
                stamp = now().replace(microsecond=0).isoformat()
                price = round((6.85 + .025 * math.sin(time.time()/900))*SCALE)
            else:
                price, stamp = fetch_quote()
            record_quote(self.path,price,stamp)
            self.collect_error = None
        except Exception as error:
            self.collect_error = quote_failure(error)
            LOG.warning('quote collection failed: %s', self.collect_error)
        if not self.demo:
            dispatch(self.path,self.sender)

    def user(self, token):
        with connect(self.path) as db:
            row = db.execute('SELECT user_id FROM sessions WHERE token=? AND expires>?', (token,now().isoformat())).fetchone()
        return row['user_id'] if row else None

    def api(self, method, path, query, body, token):
        if path == '/health' and method == 'GET':
            return {'ok':True,'demo':self.demo,'last_attempt':self.last_attempt,'collection_error':self.collect_error}
        if path == '/api/login' and method == 'POST':
            code = body.get('code')
            if not isinstance(code,str) or not 1 <= len(code) <= 512:
                raise ValueError('缺少微信登录 code')
            # Demo is opt-in and uses a separate DB; it never sends WeChat notifications.
            openid = 'demo-' + hashlib.sha256(code.encode()).hexdigest() if self.demo else wx_login(code)
            uid, session = uuid.uuid4().hex, secrets.token_urlsafe(32)
            with connect(self.path) as db:
                db.execute('INSERT OR IGNORE INTO users(id,openid) VALUES(?,?)',(uid,openid))
                uid = db.execute('SELECT id FROM users WHERE openid=?',(openid,)).fetchone()[0]
                db.execute('DELETE FROM sessions WHERE expires<=?',(now().isoformat(),))
                db.execute('INSERT INTO sessions VALUES(?,?,?)',(session,uid,(now()+timedelta(days=7)).isoformat()))
            return {'token':session,'demo':self.demo}
        uid = self.user(token)
        if not uid:
            return 401, {'error':'请先登录'}
        if path == '/api/status' and method == 'GET':
            with connect(self.path) as db:
                q = db.execute('SELECT * FROM quotes ORDER BY stamp DESC LIMIT 1').fetchone()
                credits = db.execute('SELECT credits FROM users WHERE id=?',(uid,)).fetchone()[0]
            stale = not q or (now()-datetime.fromisoformat(q['stamp'])).total_seconds() > 600
            return {'demo':self.demo, 'quote': {'price':q['price']/SCALE,'stamp':q['stamp']} if q else None,
                    'stale':stale,'error':self.collect_error,'poll_seconds':60,
                    'notifications_ready':not self.demo and self.sender.configured(),
                    'template_id':os.getenv('WX_TEMPLATE_ID',''),'subscription_credits':credits}
        if path == '/api/subscriptions' and method == 'POST':
            if self.demo or not self.sender.configured():
                raise ValueError('微信订阅消息尚未接入，演示模式不会发送通知')
            if body.get('accepted') is not True:
                raise ValueError('请先授权微信订阅消息')
            with connect(self.path) as db:
                # A local hint only; WeChat ultimately enforces real subscription quota.
                db.execute('UPDATE users SET credits=min(credits+1,50) WHERE id=?',(uid,))
            return {'ok':True}
        if path == '/api/rules':
            if method == 'GET':
                with connect(self.path) as db:
                    rows = [dict(r) for r in db.execute('SELECT * FROM rules WHERE user_id=? ORDER BY created DESC',(uid,))]
                for row in rows:
                    row['amount'] /= SCALE
                    row.pop('user_id')
                return {'rules':rows}
            if method == 'POST':
                values = validate_rule(body)
                rid = uuid.uuid4().hex
                with connect(self.path) as db:
                    if db.execute('SELECT count(*) FROM rules WHERE user_id=?',(uid,)).fetchone()[0] >= 20:
                        raise ValueError('最多保留 20 条监测规则')
                    db.execute('INSERT INTO rules(id,user_id,kind,start,end,amount,direction,created) VALUES(?,?,?,?,?,?,?,?)',
                               (rid,uid,*values,now().isoformat()))
                return {'id':rid}
        if path.startswith('/api/rules/') and method == 'DELETE':
            with connect(self.path) as db:
                deleted = db.execute('DELETE FROM rules WHERE id=? AND user_id=?',(path.rsplit('/',1)[-1],uid)).rowcount
            return (200 if deleted else 404), {'ok':bool(deleted)}
        if path == '/api/alerts' and method == 'GET':
            with connect(self.path) as db:
                rows = [dict(r) for r in db.execute('SELECT id,side,price,stamp,status,detail FROM alerts WHERE user_id=? ORDER BY id DESC LIMIT 50',(uid,))]
            for row in rows:
                row['price'] /= SCALE
            return {'alerts':rows}
        if path in ('/api/history','/api/analysis') and method == 'GET':
            points = history(self.path,query.get('start',[''])[0],query.get('end',[''])[0])
            if path.endswith('analysis'):
                return analyze(summarize(points))
            # Min/max buckets preserve local extrema; limit transport size, not analysis data.
            sampled = points
            if len(points) > 1000:
                sampled = [points[0]]
                size = math.ceil(len(points)/450)
                for i in range(1,len(points)-1,size):
                    bucket = points[i:min(i+size,len(points)-1)]
                    if bucket:
                        pair = {p['stamp']:p for p in (min(bucket,key=lambda p:p['price']),max(bucket,key=lambda p:p['price']))}
                        sampled.extend(sorted(pair.values(),key=lambda p:p['stamp']))
                sampled.append(points[-1])
            return {'points':sampled, 'total':len(points), 'stats':summarize(points) if len(points)>1 else None}
        return 404, {'error':'接口不存在'}


def handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Do not log login codes, auth headers or AI prompts.
            pass

        def run_request(self):
            try:
                n = int(self.headers.get('Content-Length','0'))
                if not 0 <= n <= 16384:
                    raise ValueError('请求体过大')
                body = json.loads(self.rfile.read(n)) if n else {}
                if not isinstance(body,dict):
                    raise ValueError('请求必须为 JSON 对象')
                url = urlsplit(self.path)
                result = app.api(self.command,url.path,parse_qs(url.query),body,
                                 self.headers.get('Authorization','').removeprefix('Bearer '))
                status, payload = result if isinstance(result,tuple) else (200,result)
            except (ValueError,TypeError,KeyError):
                status, payload = 400, {'error':'请求或服务配置无效，请检查时段、金额、登录及配置'}
            except Exception:
                LOG.warning('API operation failed (sensitive details suppressed)')
                status, payload = 502, {'error':'外部服务暂不可用，请稍后重试'}
            data = json.dumps(payload,ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(data)

        do_GET = run_request
        do_POST = run_request
        do_DELETE = run_request
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--demo',action='store_true')
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8000)
    parser.add_argument('--db')
    parser.add_argument('--check-quote',action='store_true',help='Fetch a real BOC quote once, without starting the server or requiring WeChat credentials')
    args = parser.parse_args()
    if args.check_quote:
        try:
            price, stamp = fetch_quote()
        except Exception as error:
            parser.exit(1,quote_failure(error)+'\n')
        print(json.dumps({'source':'中国银行美元现汇买入价','price':price/SCALE,'stamp':stamp,'demo':False},ensure_ascii=False))
        return
    path = args.db or ('.data/demo.sqlite3' if args.demo else '.data/rates.sqlite3')
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    app = Application(path,args.demo)
    if args.demo:
        # Explicit synthetic history, isolated from production data.
        with connect(path) as db:
            if not db.execute('SELECT 1 FROM quotes LIMIT 1').fetchone():
                end = now().replace(microsecond=0)
                for i in range(240):
                    stamp = (end-timedelta(minutes=240-i)).isoformat()
                    p = round((6.85+.025*math.sin((end.timestamp()-(240-i)*60)/900))*SCALE)
                    db.execute('INSERT INTO quotes VALUES(?,?,?)',(stamp,p,end.isoformat()))
    def worker():
        while True:
            try:
                app.collect()
            except Exception:
                LOG.warning('collector cycle failed')
            time.sleep(60)
    threading.Thread(target=worker,daemon=True).start()
    logging.basicConfig(level=logging.INFO)
    LOG.info('mode=%s port=%s', 'SYNTHETIC DEMO' if args.demo else 'BOC LIVE',args.port)
    ThreadingHTTPServer((args.host,args.port),handler(app)).serve_forever()


if __name__ == '__main__':
    main()
