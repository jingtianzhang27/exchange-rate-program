# 微信云托管：先验证容器，再完成持久化和小程序调用

仓库根目录提供 `Dockerfile`，镜像仅包含后端，不包含小程序、Git 历史、本地密钥或数据库。容器以普通用户运行，监听 `0.0.0.0:8000`，使用真实中行行情，没有演示模式开关。

> 这类「单实例 + SQLite + 云托管」小程序的通用部署模板、踩坑总表和运维清单，见 [DEPLOY_PLAYBOOK.md](DEPLOY_PLAYBOOK.md)。

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
- `COS_BUCKET`：云托管对象存储桶名，在控制台「对象存储 → 存储配置」查看；不填则关闭快照备份。
- `COS_REGION`：对象存储地域，默认 `ap-shanghai`。
- `BACKUP_INTERVAL_SECONDS` / `BACKUP_KEEP`：可选，快照间隔（默认 600 秒）和保留份数（默认 24）。

AI 可暂不配置。行情读取无需微信密钥；登录和通知需要。云托管实例必须能通过 HTTPS 访问 `www.boc.cn` 和 `api.weixin.qq.com`。

## 部署后验证

通过控制台云端调试调用 `GET /health`：`ok=true` 仅说明 HTTP 服务存活，必须再检查 `demo=false`、`last_attempt` 有值和 `collection_error=null`。查看运行日志是否有中行采集错误；之后再用小程序验证登录、报价和真实消息发送。不要在截图中包含 AppSecret、登录 code 或 token。

小程序已默认使用 `wx.cloud.callContainer()`：环境 ID 为 `exchange-rate-api-d3d5s867d5d6f8`，服务名为 `exchange-rate-api`，`demo:false`。`app.js` 在启动时初始化云能力；登录仍使用 `wx.login()` 获取新 code，后端继续验证 code 并签发自己的会话。请求适配器保留会话 Authorization、查询参数和 HTTP 方法，不依赖云端注入的 OpenID，也不因云连接失败偷偷切回本地。

本地拉取后重新编译小程序，确认实际 AppID 与云端 `WX_APP_ID` 一致。关闭 Mac 本地 Python 后端，验证报价、历史、规则保存仍可用，再预览真机并测试订阅授权和实际投递。云端环境 ID 和服务名是公开配置，密钥仍只保存在后端。切换回本地演示时，手动设置 `transport:'http'`、`demo:true`，并运行 `python3 -m backend.server --demo`。

传输层模拟测试可运行 `node --test miniprogram/tests/request.test.js`；模拟测试不代表真实微信云调用或消息投递已通过。

## 正式每天使用前必须完成

1. **持久化**：容器把 SQLite 数据写在 `/data/rates.sqlite3`，这个路径本身不是持久卷。镜像用对象存储快照兜底（见 README「数据持久化与备份」），但仍要实测恢复路径：重建实例后 `/health` 的 `backup_restored_from` 应有值、`db_quotes` 不为 0。不要把对象存储当作 SQLite 文件系统实时读写；要长期多实例运行，应改为云 MySQL。
2. **持续采集**：确认后台 CPU 不会因无请求而暂停，且实例不会缩容为零。最小实例数设为 1 也要核实其 CPU 分配/计费模式支持后台任务。
3. **单采集实例**：当前数据库和采集器按单进程设计。**实例副本数保持最小 1 / 最大 1**；版本切换时也可能短暂重叠，正式支持多实例或滚动发布前，需要数据库事务与采集/发送任务协调。不要只靠最小实例数避免重复派发。
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

## 从零到上线的完整步骤

这一节按真实操作顺序写，任何一步失败先看后面的「踩坑记录」，不要为了绕过去而关闭校验。

### 1. 准备仓库

1. 把本仓库 fork 到自己的 GitHub 账号（云托管流水线只能绑定有权限的仓库）。
2. 本地 clone 自己的 fork，把上游加为 remote 方便同步：

```bash
git clone git@github.com:<你的账号>/exchange-rate-program.git
cd exchange-rate-program
git remote add upstream https://github.com/jingtianzhang27/exchange-rate-program.git
```

### 2. 创建云托管环境

1. 打开 https://cloud.weixin.qq.com ，用小程序管理员微信扫码登录。
2. 「设置 → 环境设置 → 创建新环境」，记下**环境ID**（形如 `prod-xxxxxxxx`）和环境名称。
3. 左侧「对象存储」会自动生成一个存储桶，在「存储配置」里记下**桶名**和**地域**，快照备份要用。

### 3. 新建服务并绑定仓库

1. 「服务管理 → 服务列表 → 新建服务」，服务名建议与 `app.js` 里的 `cloudService` 一致，本项目是 `exchange-rate-api`。
2. 进入服务 → 「部署发布」→ 选择方式「绑定 GitHub 仓库」→ 点「授权」完成 GitHub OAuth（只授权需要的仓库即可）。
3. 代码仓库选自己的 fork，分支 `main`，端口与容器监听端口一致（本项目 `8000`），目标目录留空或填 `.`，Dockerfile 选「有」。
4. 点「发布」等 2–5 分钟。首版也可以先用「手动上传代码包」跑通，再切回仓库。

### 4. 配置环境变量

「服务 → 服务设置 → 环境变量」逐条添加。保存后平台会重发一次版本（备注通常是 `server config change`），这是正常现象。变量清单见上文。

### 5. 部署后验证

1. 「部署发布」里看到版本状态 `normal`。
2. 「云端调试」→ `GET /health`，确认 `ok=true`、`demo=false`、`last_attempt` 持续更新、`collection_error=null`。
3. 再看 `instance`（当前实例名）和 `backup_*` 字段。快照要等库里至少有一条报价才会第一次上传。
4. 「运行日志」里确认没有证书、TLS、DNS 相关错误；中行 DNS 偶发失败一般下一轮采集会自愈。

### 6. 小程序端对接

1. `miniprogram/project.config.json` 的 `appid` 改成自己的小程序 AppID。
2. `miniprogram/app.js` 里 `cloudEnv` 填云托管**环境ID**，`cloudService` 填服务名，`transport` 保持 `cloud`。
3. 小程序里用 `wx.cloud.callContainer`，不需要配服务器域名；只有连本地或自有后端时才改成 `transport:'http'` 并配置 `apiBase`。

### 7. 上传、体验、上线

1. 方式一：微信开发者工具导入 `miniprogram/`，扫码登录后点「上传」。
2. 方式二：`miniprogram-ci` + 小程序代码上传密钥。密钥在「开发管理 → 开发设置 → 小程序代码上传密钥」生成，**只显示一次**；生成后把上传机器的公网 IP 加进同一页的「IP白名单」，否则会报 `invalid ip`。
3. 「版本管理」→ 开发版本 → 「选为体验版本」→ 生成体验版二维码，管理员和体验成员扫码打开。
4. 正式上线还要：小程序备案、微信认证（不认证不能被搜索/分享）、提交审核、发布。体验版不需要备案，可以先用来验证功能。

## 自动部署与兜底

云托管绑定仓库后，push 到 `main` 会触发构建部署，正常 2–5 分钟上线。但 GitHub 的 push Webhook 偶发 502（GitHub 仓库 Settings → Webhooks → Recent Deliveries 能看到 `failed to connect to host`，GitHub 不会自动重试）。

仓库里的 `.github/workflows/cloudrun-deploy-guard.yml` 是兜底：push 后按分钟轮询版本列表，确认 Webhook 没部署成功，就用 CloudRun CLI 补一次部署，并继续轮询到版本 `normal`；超时就让 Action 失败报警。

需要的仓库配置：

- Secrets：`WX_APPID`（小程序 AppID）、`WX_CLI_KEY`（控制台「设置 → CLI 密钥」生成，只显示一次）
- Variables：`CLOUDRUN_ENV_ID`、`CLOUDRUN_SERVICE`（不配置时用 workflow 里的占位值，记得改成自己的）

CLI 部署命令里有几个必须带的参数，少了会卡在交互式提示：

- `--targetDir=.` 跳过「请选择部署方式」
- `--remark "..."` 跳过「请输入版本备注」
- `--override --noConfirm` 沿用旧版本配置并跳过确认

## 踩坑记录

下面都是实际踩过的，按从难到易排。遇到问题先对应着看，不要靠关校验、关验证的方式绕过。

### 1. 容器访问微信接口报证书错误（verify_code=18）

- 现象：`/api/login` 返回 `wechat_tls_error`，提示「遇到自签名证书」。
- 原因：云托管把 `api.weixin.qq.com` 解析到内网出口，用平台证书终结 TLS，而容器里的 Python 只信任公共 CA。
- 正确做法：启动时把平台证书 `/app/cert/certificate.crt` 追加进系统 CA 包，用 `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` 生效，**校验保持开启**。见 `backend/entrypoint.sh`。
- 错误做法：`ssl._create_unverified_context()`、`verify=False`、关掉 `check_hostname` —— 等于给中间人开门。

### 2. CLI 部署上传空代码包

- 现象：构建日志 `warning [code.zip]: zipfile is empty`，版本 `build_failed`。
- 原因：CloudRun CLI 用 `.dockerignore` 打包，但它不支持 `!` 取反；白名单写法（`**` + `!backend/`）会被它当成全部忽略。
- 做法：`.dockerignore` 只写普通排除项，不要用取反；同时保证 `Dockerfile` 不被排除。

### 3. 重新部署后历史数据没了

- 原因：容器本地盘不持久，每次部署/迁移实例都是一块新盘。
- 做法：`backend/storage.py` 定时（默认 10 分钟）和收到 SIGTERM 时把 SQLite 在线快照传到对象存储；启动时如果数据库不存在就自动恢复。
- 注意：恢复前网络可能还没就绪，要重试；恢复失败时不要上传空库，否则会把远端历史覆盖掉。

### 4. 多实例导致 SQLite 数据分裂

- 每个实例的 `/data` 是独立的，用户请求随机落到实例会看到不同数据；采集器和提醒派发也会重复执行。
- 用 SQLite 就必须把最大实例数设为 1。要多实例先迁云托管 MySQL，并给采集/派发加单实例租约。

### 5. 启动瞬间访问对象存储失败

- 现象：日志 `database restore failed: <urlopen error [Errno 111] Connection refused>`。
- 原因：容器刚起来时平台网络还没就绪。
- 做法：恢复最多重试 12 次 × 5 秒；仍然失败就标记状态并暂停本轮备份，保证不会覆盖远端数据。

### 6. 小程序代码上传报 invalid ip

- 代码上传密钥有 IP 白名单：在「开发设置 → IP白名单」把上传机器的出口 IP 加进去再传。

### 7. AppID 不一致

- 后端 `WX_APP_ID`、`project.config.json` 的 `appid`、云托管环境所属小程序必须是同一个；不一致时微信会返回 `40013` 或 `40029`。

### 8. 订阅消息不是无限推送

- 普通一次性订阅：用户授权一次只能发一条；长期订阅需要类目资格。推送成功不等于用户看到。

### 9. 密钥只放环境变量

- AppSecret、CLI 密钥、代码上传密钥都不要进仓库、构建参数、日志和截图；公开过的密钥先重置再用。
