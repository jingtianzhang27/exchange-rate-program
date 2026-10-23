import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from backend.core import SCALE, now, parse_boc, connect


def quote_failure(error):
    """Classify public quote failures without printing URLs, headers or credentials."""
    if isinstance(error, urllib.error.HTTPError):
        return f'中行行情请求返回 HTTP {error.code}，请检查网站访问限制或网络代理'
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, ssl.SSLCertVerificationError):
        return '中行 HTTPS 证书验证失败，请修复 Python 的 CA 证书配置，不要关闭证书验证'
    if isinstance(reason, ssl.SSLError):
        return '中行 HTTPS 连接失败，请检查 TLS 和网络代理配置'
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return '中行行情请求超时，请检查网络后重试'
    if isinstance(reason, socket.gaierror):
        return '中行域名解析失败，请检查 DNS 和网络配置'
    if isinstance(error, urllib.error.URLError):
        return '无法连接中行行情网站，请检查网络、代理和网站访问权限'
    if isinstance(error, UnicodeError):
        return '中行页面字符编码解析失败'
    if isinstance(error, ValueError):
        return '中行页面解析失败，请检查牌价表格、价格和发布时间格式'
    return '行情处理失败，请检查本地数据库及服务运行状态'


def request_json(url, payload=None, headers=None):
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, headers={'Content-Type':'application/json', **(headers or {})})
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)


def fetch_quote():
    req = urllib.request.Request('https://www.boc.cn/sourcedb/whpj/', headers={'User-Agent':'ExchangeRateMonitor/0.1'})
    with urllib.request.urlopen(req, timeout=25) as response:
        raw = response.read(2_000_000)
        charset = response.headers.get_content_charset() or ('utf-8' if b'utf-8' in raw[:4000].lower() else 'gb18030')
    return parse_boc(raw.decode(charset))


def wx_config():
    appid, secret = os.getenv('WX_APP_ID'), os.getenv('WX_APP_SECRET')
    if not appid or not secret:
        raise ValueError('尚未配置微信 AppID / AppSecret')
    return appid, secret


def wx_login(code):
    appid, secret = wx_config()
    result = request_json('https://api.weixin.qq.com/sns/jscode2session?' + urllib.parse.urlencode({
        'appid':appid, 'secret':secret, 'js_code':code, 'grant_type':'authorization_code'}))
    if 'openid' not in result:
        raise ValueError('微信登录失败，请重新登录')
    return result['openid']


class WechatSender:
    def __init__(self):
        self.token, self.expiry = None, 0

    def configured(self):
        return all(os.getenv(k) for k in ('WX_APP_ID','WX_APP_SECRET','WX_TEMPLATE_ID','WX_TEMPLATE_MAPPING'))

    def send(self, openid, alert):
        if time.time() >= self.expiry:
            appid, secret = wx_config()
            result = request_json('https://api.weixin.qq.com/cgi-bin/token?' + urllib.parse.urlencode({
                'grant_type':'client_credential','appid':appid,'secret':secret}))
            if 'access_token' not in result:
                raise ValueError('微信 access_token 获取失败')
            self.token = result['access_token']
            self.expiry = time.time() + int(result.get('expires_in',7200)) - 120
        # Map approved WeChat template field names to semantic values, never assume template IDs.
        mapping = json.loads(os.environ['WX_TEMPLATE_MAPPING'])
        values = {'price': f'{alert["price"]/SCALE:.4f}', 'time': alert['stamp'][:19].replace('T',' '),
                  'title':'美元结汇价提醒', 'reason': {'up':'涨幅达标','down':'跌幅达标','above':'达到目标上限','below':'达到目标下限'}[alert['side']]}
        if not mapping or any(v not in values for v in mapping.values()):
            raise ValueError('微信模板字段映射无效')
        result = request_json('https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token=' + self.token,
                              {'touser':openid, 'template_id':os.environ['WX_TEMPLATE_ID'], 'page':'pages/index/index',
                               'data':{key:{'value':values[value]} for key,value in mapping.items()}})
        if result.get('errcode',0):
            if result['errcode'] in (40001,42001):
                self.expiry = 0
            raise ValueError('微信返回错误码 ' + str(result['errcode']))


def dispatch(path, sender):
    if not sender.configured():
        return
    with connect(path) as db:
        alerts = db.execute("SELECT a.*, u.openid, u.credits FROM alerts a JOIN users u ON a.user_id=u.id WHERE a.status='pending' ORDER BY a.id").fetchall()
    for alert in alerts:
        with connect(path) as db:
            if (now() - datetime.fromisoformat(alert['stamp'])).total_seconds() > 600:
                db.execute("UPDATE alerts SET status='expired',detail='报价已过期，未发送' WHERE id=?", (alert['id'],))
                continue
            if not db.execute('UPDATE users SET credits=credits-1 WHERE id=? AND credits>0', (alert['user_id'],)).rowcount:
                db.execute("UPDATE alerts SET status='needs_subscription',detail='请重新授权订阅消息' WHERE id=?", (alert['id'],))
                continue
            db.execute("UPDATE alerts SET status='sending' WHERE id=?", (alert['id'],))
        try:
            sender.send(alert['openid'],alert)
            status, detail = 'sent', '微信已接受发送请求'
        except Exception:
            # Avoid duplicate deliveries after an ambiguous network timeout. Never log tokens/URLs.
            status, detail = 'failed', '发送失败或结果未知，请检查微信模板、配额、服务器日志并重新授权'
        with connect(path) as db:
            db.execute('UPDATE alerts SET status=?,detail=? WHERE id=?', (status,detail,alert['id']))


def analyze(stats):
    key, model = os.getenv('AI_API_KEY'), os.getenv('AI_MODEL')
    if not key or not model:
        return {'mode':'statistics','text':'AI 服务未配置，以下仅为历史统计。', 'stats':stats}
    base = os.getenv('AI_BASE_URL','https://api.openai.com/v1').rstrip('/')
    if not base.startswith('https://'):
        raise ValueError('AI 服务必须使用 HTTPS')
    result = request_json(base + '/chat/completions', {
        'model':model, 'messages':[
            {'role':'system','content':'你是汇率数据分析助手。仅分析提供的中行美元现汇买入价历史统计，用中文简短说明走势、区间波动和数据局限。无宏观新闻或实时外部数据，不得编造原因，不得预测确定收益，不提供买卖指令。说明历史趋势不代表未来。'},
            {'role':'user','content':json.dumps(stats,ensure_ascii=False)}], 'max_tokens':700},
        {'Authorization':'Bearer ' + key})
    return {'mode':'ai','text':result['choices'][0]['message']['content'], 'stats':stats}
