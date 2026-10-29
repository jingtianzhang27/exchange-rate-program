App({
  onLaunch() {
    if (this.globalData.transport === 'cloud' && wx.cloud) wx.cloud.init()
  },
  globalData: {
    transport: 'cloud',
    cloudEnv: 'exchange-rate-api-d3d5s867d5d6f8',
    cloudService: 'exchange-rate-api',
    apiBase: 'http://127.0.0.1:8000',
    demo: false
  }
})
