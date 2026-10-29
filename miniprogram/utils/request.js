// Keep one transport for login, rules, history and analysis. Never fall back to
// localhost after a cloud failure; that could silently select another database.
function request(config, path, method, data, token) {
  return new Promise((resolve, reject) => {
    const header = { 'content-type': 'application/json' }
    if (token) header.Authorization = `Bearer ${token}`
    const fail = () => reject(new Error(config.transport === 'cloud'
      ? '云托管连接失败，请检查环境 ID、服务名称、小程序关联权限及服务运行状态'
      : '无法连接服务，请检查后端、HTTPS 合法域名及网络'))
    const success = response => {
      if (typeof response.data === 'string') {
        try { response.data = JSON.parse(response.data) } catch (_) {
          reject(new Error('后端返回格式异常，请检查云托管服务和接口路径'))
          return
        }
      }
      resolve(response)
    }
    if (config.transport === 'cloud') {
      if (!config.cloudEnv || !config.cloudService || !wx.cloud || !wx.cloud.callContainer) {
        reject(new Error('云托管配置缺失或微信版本不支持云托管调用'))
        return
      }
      // CloudRun returns a Promise. Preserve path/query, HTTP method and auth.
      wx.cloud.callContainer({
        config: { env: config.cloudEnv }, path, method, data,
        header: { ...header, 'X-WX-SERVICE': config.cloudService }, timeout: 35000
      }).then(success, fail)
    } else if (config.transport === 'http') {
      wx.request({ url: config.apiBase + path, method, data, header, timeout: 35000, success, fail })
    } else {
      reject(new Error('请求模式无效，请将 transport 设为 cloud 或 http'))
    }
  })
}

module.exports = { request }
