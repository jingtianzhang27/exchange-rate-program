const app = getApp()
const { request } = require('../../utils/request')
const pad = n => String(n).padStart(2, '0')
function beijingDate(date) {
  const d = new Date(date.getTime() + 8 * 3600000)
  return { date: `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`, time: `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}` }
}
Page({
  data: { tab: 'chart', status: {}, quotePrice: '—', quoteTime: '等待报价', error: '', rules: [], alerts: [],
    start: '08:00', end: '17:00', kind: 'movement', amount: '0.01', directions: ['达到或高于目标价', '达到或低于目标价'],
    directionIndex: 0, pointCount: 0, stats: null, analysis: null, saving: false, chartBusy: false, aiBusy: false },
  onLoad() {
    const end = beijingDate(new Date()), start = beijingDate(new Date(Date.now() - 24 * 3600000))
    this.setData({ fromDate: start.date, fromTime: start.time, toDate: end.date, toTime: end.time })
    this.refresh()
  },
  onShow() { this.timer = setInterval(() => this.refreshStatus().catch(() => {}), 60000) },
  onHide() { clearInterval(this.timer) },
  onUnload() { clearInterval(this.timer) },
  async login() {
    if (this.loginPending) return this.loginPending
    this.loginPending = (async () => {
      let code
      if (app.globalData.demo) {
        code = wx.getStorageSync('demoIdentity') || `demo-${Date.now()}-${Math.random()}`
        wx.setStorageSync('demoIdentity', code)
      } else {
        code = (await new Promise((resolve, reject) => wx.login({ success: resolve, fail: reject }))).code
      }
      const result = await this.http('/api/login', 'POST', { code }, false)
      if (result.demo !== app.globalData.demo) throw new Error('前后端演示模式不一致，请检查配置')
      this.token = result.token
    })()
    try { await this.loginPending } finally { this.loginPending = null }
  },
  http(path, method = 'GET', data, authenticated = true) {
    return request(app.globalData, path, method, data, authenticated ? this.token : null)
      .then(r => {
        if (r.statusCode === 401) this.token = null
        if (r.statusCode >= 200 && r.statusCode < 300) return r.data
        throw new Error((r.data && r.data.error) || `服务错误 ${r.statusCode}`)
      })
  },
  async api(path, method, data) {
    if (!this.token) await this.login()
    return this.http(path, method, data)
  },
  showError(e) { this.setData({ error: e.message || '操作失败，请重试' }) },
  async refresh() {
    this.setData({ error: '' })
    try {
      await this.refreshStatus()
      await Promise.all([this.loadRules(), this.loadAlerts(), this.loadHistory()])
    } catch (e) { this.showError(e) }
  },
  async refreshStatus() {
    const status = await this.api('/api/status')
    this.setData({ status, quotePrice: status.quote ? status.quote.price.toFixed(4) : '—',
      quoteTime: status.quote ? status.quote.stamp.slice(0, 19).replace('T', ' ') + ' 北京时间' : '等待中行报价' })
  },
  selectTab(e) { this.setData({ tab: e.currentTarget.dataset.tab }); if (this.data.tab === 'chart') wx.nextTick(() => this.drawChart(this.points || [])); if (this.data.tab === 'alerts') this.loadAlerts().catch(e => this.showError(e)) },
  startChange(e) { this.setData({ start: e.detail.value }) },
  endChange(e) { this.setData({ end: e.detail.value }) },
  kindChange(e) { this.setData({ kind: e.detail.value, amount: e.detail.value === 'movement' ? '0.01' : '6.70' }) },
  amountChange(e) { this.setData({ amount: e.detail.value }) },
  directionChange(e) { this.setData({ directionIndex: Number(e.detail.value) }) },
  fromDateChange(e) { this.setData({ fromDate: e.detail.value, analysis: null }) },
  fromTimeChange(e) { this.setData({ fromTime: e.detail.value, analysis: null }) },
  toDateChange(e) { this.setData({ toDate: e.detail.value, analysis: null }) },
  toTimeChange(e) { this.setData({ toTime: e.detail.value, analysis: null }) },
  range() {
    const d = this.data
    const start = `${d.fromDate}T${d.fromTime}:00+08:00`, end = `${d.toDate}T${d.toTime}:59+08:00`
    if (new Date(start) > new Date(end)) throw new Error('开始时间不能晚于结束时间')
    return `?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`
  },
  async loadHistory() {
    this.setData({ chartBusy: true, analysis: null })
    try {
      const result = await this.api('/api/history' + this.range())
      this.points = result.points
      this.setData({ pointCount: result.total, stats: result.stats })
      wx.nextTick(() => this.drawChart(result.points))
    } catch (e) { this.showError(e) } finally { this.setData({ chartBusy: false }) }
  },
  async analyze() {
    this.setData({ aiBusy: true, error: '' })
    try {
      await this.loadHistory()
      const analysis = await this.api('/api/analysis' + this.range())
      if (analysis.mode === 'statistics') {
        const s = analysis.stats
        analysis.text += `\n该时段共 ${s.count} 条报价，从 ${s.first} 变化至 ${s.last} 元，变化 ${s.change} 元（${s.change_percent}%）；区间 ${s.low}–${s.high} 元。历史走势不代表未来。`
      }
      this.setData({ analysis })
    } catch (e) { this.showError(e) } finally { this.setData({ aiBusy: false }) }
  },
  async loadRules() { const r = await this.api('/api/rules'); this.setData({ rules: r.rules }) },
  async saveRule() {
    if (this.data.saving) return
    this.setData({ saving: true, error: '' })
    try {
      const d = this.data
      if (!(Number(d.amount) > 0 && Number(d.amount) < 100)) throw new Error('请输入大于 0、小于 100 的金额')
      await this.api('/api/rules', 'POST', { kind: d.kind, start: d.start, end: d.end, amount: d.amount, direction: d.directionIndex === 0 ? 'above' : 'below' })
      await this.loadRules()
      wx.showToast({ title: '规则已保存' })
    } catch (e) { this.showError(e) } finally { this.setData({ saving: false }) }
  },
  async deleteRule(e) {
    const answer = await new Promise(resolve => wx.showModal({ title: '删除监测', content: '确定删除这条规则和对应触发记录？', success: resolve }))
    if (!answer.confirm) return
    try { await this.api('/api/rules/' + e.currentTarget.dataset.id, 'DELETE'); await this.loadRules() } catch (e) { this.showError(e) }
  },
  subscribe() {
    // Must be invoked directly from a user gesture, without awaiting network calls first.
    const template = this.data.status.template_id
    if (!this.data.status.notifications_ready || !template) return this.showError(new Error('请先在服务端接入微信订阅消息'))
    wx.requestSubscribeMessage({ tmplIds: [template], success: async result => {
      try {
        if (result[template] !== 'accept') throw new Error('未获得订阅授权，可在微信设置中检查权限')
        await this.api('/api/subscriptions', 'POST', { accepted: true })
        await this.refreshStatus()
        wx.showToast({ title: '已记录本次授权' })
      } catch (e) { this.showError(e) }
    }, fail: () => this.showError(new Error('订阅授权失败，请检查模板和小程序权限')) })
  },
  async loadAlerts() {
    const r = await this.api('/api/alerts')
    const names = { pending: '待发送', sending: '发送结果待核实', sent: '已请求微信发送', failed: '发送失败/未知', needs_subscription: '需重新授权', expired: '已过期' }
    const sides = { up: '上涨达标', down: '下跌达标', above: '达到目标上限', below: '达到目标下限' }
    this.setData({ alerts: r.alerts.map(a => ({ ...a, label: sides[a.side], statusLabel: names[a.status] || a.status, displayTime: a.stamp.slice(0, 19).replace('T', ' ') })) })
  },
  drawChart(points) {
    if (this.data.tab !== 'chart') return
    wx.createSelectorQuery().in(this).select('#rateChart').fields({ node: true, size: true }).exec(result => {
      if (!result[0]) return
      const { node, width: w, height: h } = result[0], ctx = node.getContext('2d')
      const dpr = wx.getWindowInfo().pixelRatio
      node.width = w * dpr; node.height = h * dpr; ctx.scale(dpr, dpr)
      ctx.clearRect(0, 0, w, h)
      if (!points.length) return
      const values = points.map(p => p.price), min = Math.min(...values), max = Math.max(...values)
      const margin = Math.max((max - min) * 0.15, 0.0002), low = min - margin, high = max + margin
      const left = 56, right = w - 12, top = 12, bottom = h - 26
      ctx.font = '10px sans-serif'; ctx.fillStyle = '#7b8e96'; ctx.strokeStyle = '#e7eeed'; ctx.lineWidth = 1
      for (let i = 0; i < 4; i++) {
        const y = top + (bottom - top) * i / 3
        ctx.fillText((high - (high - low) * i / 3).toFixed(4), 0, y + 4)
        ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke()
      }
      const first = new Date(points[0].stamp).getTime(), last = new Date(points[points.length - 1].stamp).getTime()
      ctx.strokeStyle = '#11695c'; ctx.lineWidth = 2; ctx.beginPath()
      points.forEach((p, i) => {
        const t = new Date(p.stamp).getTime(), x = left + (t - first) / (last - first || 1) * (right - left)
        const y = bottom - (p.price - low) / (high - low) * (bottom - top)
        // Show gaps longer than ten minutes instead of implying continuous coverage.
        if (!i || t - new Date(points[i - 1].stamp).getTime() > 600000) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
        ctx.fillStyle = '#11695c'; ctx.fillRect(x - 1, y - 1, 2, 2)
      })
      ctx.stroke(); ctx.fillStyle = '#7b8e96'
      ctx.fillText(points[0].stamp.slice(5, 16).replace('T', ' '), left, h - 5)
      ctx.textAlign = 'right'; ctx.fillText(points[points.length - 1].stamp.slice(5, 16).replace('T', ' '), right, h - 5)
    })
  }
})
