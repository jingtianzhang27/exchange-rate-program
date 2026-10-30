# 小程序 + 云托管：部署模板与踩坑手册

> 这份文档写给「个人/小团队、单实例、SQLite、微信云托管后端、原生小程序」这类规模的项目。
> 我们的「美元结汇观察」就是按这个模板落地的：请求量小、读多写少、后台有定时采集、需要订阅消息提醒。
> 按本文顺序做，能少走我们踩过的弯路；文末有完整踩坑表和错误码速查。

## 0. 先判断：什么时候适合这个模板

| 维度 | 适合 | 不适合（该换方案） |
| --- | --- | --- |
| 实例数 | 单实例（min 1 / max 1） | 需要多实例、高可用、滚动发布 → 云托管 MySQL |
| 存储 | SQLite + 对象存储快照 | 强一致、多写并发、跨实例共享 → MySQL |
| 数据量 | 几十万行以内 | 大表、复杂查询 → MySQL |
| 流量 | 个人工具/小范围使用 | 高并发 → 加网关限流、多实例 |
| 提醒 | 订阅消息（一次性） | 高频推送、长期订阅 → 需要类目资格 |

这套模板的硬约束先说清楚：

- **实例数必须固定 1**：容器本地盘不跨实例共享，SQLite 多实例会各存一份数据，用户请求落到不同实例会看到不同内容。
- **快照恢复有窗口**：默认 10 分钟一次 + 停机一次，最坏丢 10 分钟数据。
- **公网访问不需要**：小程序走 `wx.cloud.callContainer` 内网链路；但**公网出口**必须开，容器要访问 `www.boc.cn`、`api.weixin.qq.com`。

## 1. 上线路线图

| 阶段 | 产出 | 大致耗时 | 容易卡住的地方 |
| --- | --- | --- | --- |
| 0. 账号资质 | 小程序、云托管环境、对象存储桶 | 半天 | 备案周期几天、认证 1–3 天 |
| 1. 后端容器化 | `Dockerfile` + `entrypoint.sh` + `.dockerignore` | 1 小时 | 第三方依赖、CA 信任、打包规则 |
| 2. 云托管部署 | 服务 + 仓库绑定 + 环境变量 | 1 小时 | 端口、实例数、环境变量作用域 |
| 3. 自动部署 | Webhook + GitHub Action 兜底 | 1 小时 | webhook 502、CLI 非交互参数 |
| 4. 持久化 | SQLite 快照 + 启动恢复 | 半天 | 启动时网络未就绪、空库覆盖 |
| 5. 小程序端 | callContainer 对接 | 1 小时 | appid/环境ID/服务名三处一致 |
| 6. 上传体验 | miniprogram-ci 或开发者工具 | 1 小时 | 上传密钥一次性、IP 白名单 |
| 7. 上线 | 备案 + 认证 + 审核 + 发布 | 1–2 周 | 备案是硬门槛 |

## 2. 账号与资质（能提前办的就提前办）

1. **小程序注册**：拿到 AppID，确认主体类型（个人主体有一些类目/能力限制）。
2. **服务类目**：选不对会影响订阅消息等能力；个人主体常用「工具 > 信息查询」。
3. **小程序备案**：不备案不能发布上线，个人主体需要身份证 + 人脸，周期通常几天。**体验版不需要备案**，可以先用体验版验证功能。
4. **微信认证**：不认证不能被搜索、被分享；建议尽早办。
5. **用户隐私保护指引**：发布前要更新；有用户生成内容（UGC）的场景声明要如实填写。
6. **订阅消息**：公众平台「订阅消息」要先开通，再选模板；普通一次性订阅**用户授权一次只能发一条**，长期订阅需要类目资格。
7. **云托管环境**：控制台创建后会分配**环境ID**（形如 `prod-xxxxxxxx`），环境自带一个对象存储桶，记下**桶名**和**地域**（一般 `ap-shanghai`）。

> 建议顺序：先建环境和体验版把功能跑通，备案/认证并行去办，不要等备案下来才开工。

## 3. 后端容器化

### Dockerfile 要点

- 基础镜像用 `python:3.x-slim`，装 `ca-certificates`、时区数据（`zoneinfo` 需要 IANA tzdata）。
- 创建非 root 用户运行；`EXPOSE` 的端口要和云托管「端口」配置一致（本项目 `8000`）。
- `HEALTHCHECK` 只查 `/health` 存活，不代表业务就绪。
- 第三方依赖能少则少；本项目只加了一个可选的 `cos-python-sdk-v5` 用于快照备份，缺失时自动禁用，不影响主流程。

### entrypoint：两件必须做的事

```sh
# 1) 信任云托管平台证书（api.weixin.qq.com 的 TLS 由平台终结）
if [ -r /app/cert/certificate.crt ]; then
  cat /etc/ssl/certs/ca-certificates.crt /app/cert/certificate.crt > /tmp/ca-bundle.crt
  export SSL_CERT_FILE=/tmp/ca-bundle.crt
  export REQUESTS_CA_BUNDLE=/tmp/ca-bundle.crt
fi
# 2) 启动服务
exec python -m backend.server --host 0.0.0.0 --port 8000 --db /data/rates.sqlite3
```

> 千万不要用 `verify=False` / `_create_unverified_context()` 绕过证书问题——等于给中间人开门，而且平台证书路径会变，今天绕过明天还会炸。

### .dockerignore 的坑

- **云托管 CLI 打包不支持 `!` 取反**（白名单写法会被它当成全部忽略，构建报 `zipfile is empty`）。
- 只写普通排除项，例如 `.git/**`、`miniprogram/**`、`**/__pycache__/**`、`**/.env*`、`**/*.sqlite3`。
- 确认 `Dockerfile` 没有被排除（CLI 依赖它在根目录构建）。

## 4. 云托管部署

1. **建服务**：服务名建议与小程序 `cloudService` 一致，例如 `exchange-rate-api`。
2. **绑定仓库**：部署发布 → 「绑定 GitHub 仓库」→ 授权 OAuth（只勾需要的仓库）→ 选 fork、分支 `main`、端口 `8000`、目标目录留空或 `.`、Dockerfile「有」。
3. **环境变量**（服务设置 → 环境变量，保存后会重发一次版本，备注 `server config change`，正常）：

| 变量 | 说明 |
| --- | --- |
| `WX_APP_ID` | 小程序 AppID，必须与 `project.config.json` 一致 |
| `WX_APP_SECRET` | 小程序密钥，只放环境变量 |
| `WX_TEMPLATE_ID` | 订阅消息模板 ID（未开通可先不填） |
| `WX_TEMPLATE_MAPPING` | 模板字段映射 JSON，按实际模板字段填写 |
| `COS_BUCKET` / `COS_REGION` | 快照用的桶名/地域；不填则关闭备份 |
| `BACKUP_INTERVAL_SECONDS` / `BACKUP_KEEP` | 可选，默认 600 秒 / 24 份 |

4. **实例副本数**：最小 1 / **最大 1**。最小 0 会被缩容到 0，后台采集直接停；最大 >1 会让 SQLite 分裂、提醒可能重复发。
5. **公网访问**：不用开；**公网出口**保持开启。
6. **验证**：控制台「云端调试」→ `GET /health`，确认 `ok=true`、`demo=false`、`last_attempt` 持续更新、`collection_error=null`；再看 `instance`、`backup_*`、`db_quotes`。

## 5. 自动部署与兜底

- 绑定仓库后，push 到 `main` 会触发云托管构建部署，正常 2–5 分钟上线。
- **Webhook 会偶发 502**：GitHub 仓库 Settings → Webhooks → Recent Deliveries 能看到 `failed to connect to host`，而且 GitHub 不会自动重试。丢了这一次，代码就一直不部署。
- 兜底方案：GitHub Action（`.github/workflows/cloudrun-deploy-guard.yml`）
  1. push 后按分钟轮询 `wxcloud version:list`；
  2. 发现「本次 push 之后创建、状态 normal」的版本 → 说明 Webhook 成功，直接退出；
  3. 否则用 CloudRun CLI 补部署，再轮询到 normal；
  4. 超时就让 Action 变红，避免「以为部署了其实没有」。

GitHub 侧需要的配置：

- Secrets：`WX_APPID`、`WX_CLI_KEY`（CLI 密钥，控制台「设置 → CLI 密钥」生成，只显示一次）
- Variables：`CLOUDRUN_ENV_ID`、`CLOUDRUN_SERVICE`

CLI 非交互参数（少一个就会卡在交互提示）：

```bash
wxcloud run:deploy . \
  --envId <ENV_ID> --serviceName <SERVICE> \
  --containerPort 8000 --dockerfile Dockerfile \
  --override --noConfirm --detach \
  --targetDir=. --remark "ci: <sha>"
```

> 注意：`version:list` 的时间是北京时间（UTC+8）；判断「push 之后」时要换算。`build_failed` 的版本也要排除。

## 6. 数据持久化与备份

- 容器本地盘**不持久**：重新部署/迁移实例就是新盘，SQLite 直接清零。
- 方案：SQLite 在线快照 → 云托管对象存储；启动时如果 `/data/rates.sqlite3` 不存在就自动恢复。
- 对象存储**不需要申请 COS 账号**：容器内调用平台接口 `GET http://api.weixin.qq.com/_/cos/getauth` 拿当前环境存储桶的**临时密钥**（只在内存用，几分钟过期）。
- 工程细节（都是踩出来的）：
  - 启动瞬间平台网络可能还没就绪 → 恢复要重试（我们设 12 次 × 5 秒）；
  - 恢复失败 / 库里没有报价时**禁止上传快照**，否则空库会把远端历史覆盖掉；
  - 快照用 SQLite 官方 online backup（`Connection.backup()`），不要在运行中直接拷文件；
  - 对象存储只做快照，**不要**把 SQLite 文件放对象存储实时读写（锁和 WAL 会坏）。
- RPO：默认 10 分钟 + SIGTERM 一次；接受不了就缩短 `BACKUP_INTERVAL_SECONDS`。
- 需要多实例/强一致时，迁云托管 MySQL；迁移前把采集和提醒派发改成单实例租约。

## 7. 小程序端

- 云托管调用：`wx.cloud.callContainer({ config:{env:<环境ID>}, path, method, header:{'X-WX-SERVICE':<服务名>} })`。
- **不需要配服务器域名、不需要公网访问**；只有连本地/自有 HTTPS 后端时才改 `transport:'http'`。
- 三处必须一致：`project.config.json` 的 `appid` = 后端 `WX_APP_ID` = 云托管环境所属小程序。
- `wx.cloud.init()` 不是必须的，但保留时建议 try/catch（没有云开发环境时可能抛错）。
- 上传方式二选一：
  - 微信开发者工具：扫码登录 → 上传（最省事，适合人类操作）；
  - `miniprogram-ci`：需要「代码上传密钥」+ 出口 IP 白名单，适合 CI。
- 体验版：版本管理 → 开发版本 → 「选为体验版本」→ 生成二维码（**有效期约 7 天**，过期重新生成）；只有管理员/体验成员能打开。
- 正式上线：备案 → 认证 → 提交审核 → 发布。审核前把隐私指引、类目、UGC 声明都补齐。

## 8. 上线前检查清单

- [ ] 实例数 min=1 / max=1，公网出口开启
- [ ] `WX_APP_ID` / `WX_APP_SECRET` / 模板变量都在环境变量里，仓库无密钥
- [ ] `/health` 返回 `ok=true`、`demo=false`、`collection_error=null`
- [ ] `/health` 的 `backup_enabled=true`，且 `backup_last_ok_at` 有值
- [ ] 重建一次实例（重新部署），确认 `backup_restored_from` 有值、`db_quotes` 不为 0
- [ ] 小程序 `appid`、`cloudEnv`、`cloudService` 三处正确
- [ ] push 一次代码，确认 Webhook 或兜底 Action 至少一条成功
- [ ] 上传密钥单独保管，IP 白名单只放需要的出口 IP
- [ ] 体验版真机跑通登录、报价、规则、提醒授权
- [ ] 备案完成、认证完成、隐私指引 / UGC 声明已更新
- [ ] 订阅消息模板 ID 与字段映射和实际模板一致
- [ ] 备份桶权限确认（只有本环境可读写）

## 9. 踩坑全表

| # | 现象 | 根因 | 处理 |
| --- | --- | --- | --- |
| 1 | `/api/login` 返回 `wechat_tls_error`（verify_code=18） | 平台在出口用自签名证书终结 `api.weixin.qq.com` TLS | entrypoint 把 `/app/cert/certificate.crt` 追加进 CA 包，**不要关校验** |
| 2 | 构建失败 `zipfile is empty` | `.dockerignore` 用了 `!` 取反，CLI 打包不支持 | 改成纯排除项，保留 Dockerfile |
| 3 | push 后一直不部署 | Webhook 502，GitHub 不重试 | 查 GitHub Webhooks deliveries；加 Action 兜底 |
| 4 | 重新部署后历史没了 | 容器盘不持久 | 对象存储快照 + 启动恢复 |
| 5 | 启动日志 `restore ... Connection refused` | 容器启动瞬间网络未就绪 | 恢复重试（12×5 秒） |
| 6 | 用户看到的数据时有时无 | 多实例各自 SQLite | max 实例数设 1；多实例上 MySQL |
| 7 | 提醒重复发 | 多实例都跑了采集/派发 | 同上；派发加单实例租约 |
| 8 | `invalid ip: x.x.x.x` | 代码上传密钥 IP 白名单 | 控制台「开发设置 → IP白名单」加出口 IP |
| 9 | `40013` / `40125` / `40029` | AppID / 密钥 / code 不一致或过期 | 三处 AppID 对齐；code 一次性 |
| 10 | 提醒一直不生效 | 订阅消息未开通 / 模板 ID 未配 | 公众平台开通订阅消息，配 `WX_TEMPLATE_ID`/`MAPPING` |
| 11 | 采集 `中行域名解析失败` 偶发 | 平台 DNS 抖动 | 下一轮采集会自愈；持续失败查运行日志 |
| 12 | 环境 ID 填错导致 callContainer 失败 | 控制台环境 ID 与 `app.js` 不一致 | 从控制台环境下拉/部署日志里取真实环境 ID |
| 13 | 服务缩容后不再采集 | 最小实例数 0 | 设最小 1 |
| 14 | 改完环境变量没生效 | 改的是版本级变量，或没重发 | 服务设置里改会触发 `server config change` 重发 |
| 15 | 体验版打不开 | 不是管理员/体验成员；二维码过期 | 成员管理加体验成员；重新生成二维码 |
| 16 | 发布后搜不到小程序 | 未认证 / 未备案 | 完成认证与备案 |
| 17 | 云端调试看不到 `/health` 细节 | 嵌套 JSON 折叠 | 加平铺字段（本项目已加 `backup_*`、`db_quotes`） |
| 18 | 线上日志混着多个版本难排查 | 日志默认混排 | 按版本筛选/缩短时间范围；日志 tag 有 pod 信息 |
| 19 | CLI 登录失败 | `--privateKey` 要传密钥**内容**，不是文件路径 | 直接传密钥字符串 |
| 20 | CLI 卡在交互提示 | 缺 `--targetDir` / `--remark` | 补上非交互参数 |

## 10. 运维 Runbook

- **发布**：`git push` 到 `main`；看 GitHub Actions / 云托管「部署发布」版本状态。
- **回滚**：云托管「部署发布 → 历史版本 → 回退」，或 `wxcloud run:rollback`。
- **改环境变量**：服务设置里改，保存即触发重发；改完用云端调试复查 `/health`。
- **验证备份**：重新部署一次，看 `/health.backup_restored_from` 与 `db_quotes`。
- **看日志**：云托管「运行日志」；重点搜 `WARNING`、`restore`、`snapshot`、`collection`。
- **排查部署漏走**：GitHub 仓库 Settings → Webhooks → Recent Deliveries。
- **排查登录**：`/health` 正常不代表微信登录正常；用云端调试打一次 `/api/login`（假 code 返回 `40029` 说明 TLS 和密钥都对）。

## 11. 常用命令速查

```bash
# 本地测试
python3 -m unittest discover -s backend/tests -v

# 云托管 CLI（需要先 wxcloud login）
wxcloud env:list
wxcloud service:list --envId <ENV_ID>
wxcloud version:list --envId <ENV_ID> --serviceName <SERVICE>
wxcloud run:deploy . --envId <ENV_ID> --serviceName <SERVICE> \
  --containerPort 8000 --dockerfile Dockerfile \
  --override --noConfirm --detach --targetDir=. --remark "manual"

# 小程序代码上传（miniprogram-ci）
node upload.js   # 项目自备脚本：appid + privateKeyPath + projectPath

# GitHub 侧
gh secret set WX_CLI_KEY -R <owner>/<repo>
gh api repos/<owner>/<repo>/hooks/<hook_id>/deliveries
gh run list -R <owner>/<repo>
```

## 12. 成本与配额（按量计费，小项目很低）

- 云托管：按 CPU/内存/实例时长计费，单实例 1 核 2G 跑小流量很便宜；注意最小实例 1 不再缩容到 0。
- 对象存储：快照几十 KB～几 MB，存储和请求费用基本可忽略。
- 订阅消息：按模板配额，普通一次性订阅用户授权一次只能用一次。
- 小程序：注册免费；认证有年费；备案免费。

## 13. 这个模板里已经固化到代码的东西

| 文件 | 作用 |
| --- | --- |
| `backend/entrypoint.sh` | 信任平台证书 + 启动服务 |
| `backend/storage.py` | SQLite 快照上传/恢复/保留/防覆盖 |
| `backend/server.py` | 启动恢复、`/health` 诊断字段、SIGTERM 快照 |
| `Dockerfile` | 非 root、健康检查、可选 COS 依赖、entrypoint |
| `.dockerignore` | CLI 可用的纯排除规则 |
| `.github/workflows/cloudrun-deploy-guard.yml` | Webhook 失败兜底部署 |
| `deploy/WECHAT_CLOUDRUN.md` | 单项目部署细节 |
| 本文件 | 模板级清单、踩坑总表、runbook |
