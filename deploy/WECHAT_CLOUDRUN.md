# 微信云托管：先验证容器，再完成持久化和小程序调用

仓库根目录提供 `Dockerfile`，镜像仅包含后端，不包含小程序、Git 历史、本地密钥或数据库。容器以普通用户运行，监听 `0.0.0.0:8000`，使用真实中行行情，没有演示模式开关。

## 发布页面填写

| 字段 | 内容 |
| --- | --- |
| GitHub 仓库 | `jingtianzhang27/exchange-rate-program` |
| 分支 | `main` |
| 端口 | `8000` |
| 目标目录 | `.`（仓库根目录；若控制台要求其他根目录表示法，按其提示） |
| Dockerfile 文件 | 有 |
| Dockerfile 名称 | `Dockerfile` |

如果仍然出现 GitHub 文件 404，确认当前 main 分支可以查看根目录 Dockerfile，再检查绑定仓库的访问权限和目标目录；不要用空 Dockerfile 绕过检查。

环境变量直接填值，不加 shell 的 `export` 或外层单引号。密钥只放云托管运行配置，不放 GitHub、构建参数或 Dockerfile：

- `WX_APP_ID`：`wxfab79228c5d3d1a6`。
- `WX_APP_SECRET`：该小程序当前有效密钥；公开过的密钥应先重置。
- `WX_TEMPLATE_ID`：自己的订阅模板 ID。
- `WX_TEMPLATE_MAPPING`：对于编号 28696、字段与已确认模板一致的模板，填写 `{"character_string1":"price","thing5":"reason","time2":"time","thing3":"bank","thing4":"currency"}`。

AI 可暂不配置。行情读取无需微信密钥；登录和通知需要。云托管实例必须能通过 HTTPS 访问 `www.boc.cn` 和 `api.weixin.qq.com`。

## 部署后验证

通过控制台云端调试调用 `GET /health`：`ok=true` 仅说明 HTTP 服务存活，必须再检查 `demo=false`、`last_attempt` 有值和 `collection_error=null`。查看运行日志是否有中行采集错误；之后再用小程序验证登录、报价和真实消息发送。不要在截图中包含 AppSecret、登录 code 或 token。

小程序已默认使用 `wx.cloud.callContainer()`：环境 ID 为 `exchange-rate-api-d3d5s867d5d6f8`，服务名为 `exchange-rate-api`，`demo:false`。`app.js` 在启动时初始化云能力；登录仍使用 `wx.login()` 获取新 code，后端继续验证 code 并签发自己的会话。请求适配器保留会话 Authorization、查询参数和 HTTP 方法，不依赖云端注入的 OpenID，也不因云连接失败偷偷切回本地。

本地拉取后重新编译小程序，确认实际 AppID 与云端 `WX_APP_ID` 一致。关闭 Mac 本地 Python 后端，验证报价、历史、规则保存仍可用，再预览真机并测试订阅授权和实际投递。云端环境 ID 和服务名是公开配置，密钥仍只保存在后端。切换回本地演示时，手动设置 `transport:'http'`、`demo:true`，并运行 `python3 -m backend.server --demo`。

传输层模拟测试可运行 `node --test miniprogram/tests/request.test.js`；模拟测试不代表真实微信云调用或消息投递已通过。

## 正式每天使用前必须完成

1. **持久化**：容器把 SQLite 数据写在 `/data/rates.sqlite3`。这个路径本身并非持久卷，镜像也没有假装保存数据。当前控制台如果不能提供适合 SQLite 锁和 WAL 的可靠持久存储，应将程序改为使用云 MySQL。仅开通 MySQL 或添加环境变量不会自动完成代码迁移。不要将对象存储直接当作 SQLite 文件系统。
2. **持续采集**：确认后台 CPU 不会因无请求而暂停，且实例不会缩容为零。最小实例数设为 1 也要核实其 CPU 分配/计费模式支持后台任务。
3. **单采集实例**：当前数据库和采集器按单进程设计。试运行最多一个实例，版本切换时也可能短暂重叠；正式支持多实例或滚动发布前，需要数据库事务与采集/发送任务协调。不要只靠最小实例数避免重复派发。
4. **持久性测试**：重建实例后确认用户规则、历史和提醒去重状态仍在，再启用长期提醒。
5. **前端联调**：云托管调用已接入代码，仍需真机测试及用户订阅授权。普通订阅配额有限，微信接受发送请求不等于已阅读。

没有持久存储时，只能把首次部署用作短期连通性试验，不能当作已完成长期监测部署。

## 本地容器验证（可选）

在仓库根目录，已安装并启动 Docker 的电脑上运行：

```bash
docker build -t exchange-rate-api .
docker volume create exchange-rate-data
docker run --rm --name exchange-rate-api -p 127.0.0.1:8000:8000 -v exchange-rate-data:/data exchange-rate-api
```

另开终端请求 `http://127.0.0.1:8000/health`。本地 Docker volume 演示持久化，不代表云托管已有同等能力。微信登录和消息测试需要另行通过受保护的运行环境配置注入对应变量。
