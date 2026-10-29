const test = require('node:test')
const assert = require('node:assert/strict')
const { request } = require('../utils/request')
const config = { transport: 'cloud', cloudEnv: 'test-env', cloudService: 'rates' }

test('cloud login omits bearer token; private APIs preserve method, body and query', async () => {
  const calls = []
  global.wx = { cloud: { callContainer: async options => {
    calls.push(options)
    return { statusCode: 200, data: { ok: true } }
  } } }
  await request(config, '/api/login', 'POST', { code: 'test-code' }, null)
  await request(config, '/api/rules', 'POST', { amount: '.01' }, 'session-token')
  await request(config, '/api/history?start=a&end=b', 'GET', undefined, 'session-token')
  await request(config, '/api/rules/r1', 'DELETE', undefined, 'session-token')
  assert.equal(calls[0].header.Authorization, undefined)
  assert.deepEqual(calls[0].data, { code: 'test-code' })
  assert.equal(calls[1].header.Authorization, 'Bearer session-token')
  assert.equal(calls[1].header['X-WX-SERVICE'], 'rates')
  assert.equal(calls[1].config.env, 'test-env')
  assert.equal(calls[2].path, '/api/history?start=a&end=b')
  assert.equal(calls[3].method, 'DELETE')
})

test('cloud HTTP failures remain responses; transport failures never call localhost', async () => {
  global.wx = { cloud: { callContainer: async () => ({ statusCode: 400, data: '{"error":"bad code"}' }) },
    request: () => { throw new Error('must not fall back') } }
  assert.deepEqual((await request(config, '/api/login', 'POST', {})).data, { error: 'bad code' })
  wx.cloud.callContainer = async () => { throw new Error('private platform details') }
  await assert.rejects(request(config, '/api/status', 'GET'), /云托管连接失败/)
  wx.cloud.callContainer = async () => ({ statusCode: 502, data: '<html>proxy error</html>' })
  await assert.rejects(request(config, '/api/status', 'GET'), /返回格式异常/)
})

test('explicit HTTP mode continues to support local development', async () => {
  let called
  global.wx = { request: options => { called = options; options.success({ statusCode: 200, data: { demo: true } }) } }
  const response = await request({ transport: 'http', apiBase: 'http://127.0.0.1:8000' }, '/api/status', 'GET', undefined, 'local-token')
  assert.equal(called.url, 'http://127.0.0.1:8000/api/status')
  assert.equal(response.data.demo, true)
  assert.equal(called.header.Authorization, 'Bearer local-token')
  assert.equal(called.header['X-WX-SERVICE'], undefined)
  await assert.rejects(request(config, '/api/status', 'GET'), /配置缺失/)
})
