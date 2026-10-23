# 本地自托管部署说明（DeepSeek + MinIO）

本机部署版本，与上游 `DEVELOPMENT.md` 的差异集中在两处：**LLM 换成 DeepSeek**
（上游是 Gemini/OpenAI/Anthropic 三选三全配），**对象存储换成本地 MinIO**
（上游依赖 AWS S3 + Cloudflare CDN）。网络受限（直连 PyPI / Docker Hub 不通），
构建时统一走国内镜像源。

## 访问地址

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| 前端 | http://10.244.128.73:3100 | 主入口 |
| API 文档 | http://10.244.128.73:8100/docs | Swagger |
| Jobs API | http://10.244.128.73:8101/health | Celery 任务 API |
| MinIO 控制台 | http://10.244.128.73:9001 | 账号密码 `minioadmin` / `minioadmin` |
| RabbitMQ 控制台 | http://10.244.128.73:15673 | `guest` / `guest` |
| Postgres | 127.0.0.1:5433（仅本机） | 库名 `annotated-paper` |

## 登录

**纯本地账号密码，不依赖任何外网服务。** 库里只允许一种登录方式：邮箱 + 密码，
密码以 scrypt 哈希存在本地 Postgres 的 `users.password_hash` 里。

当前账号：

| 项 | 值 |
| --- | --- |
| 登录名 | `jiang@openpaper.dev` |
| 密码 | `op-38d44f656a664aad`（首次登录后请改掉） |
| 数据 | 12 篇论文、2 个项目（原 fixture 库，账号 id 未变） |

改密码（两种方式）：

```bash
# 方式一：命令行，最省事（改完立即生效，旧会话仍有效）
docker compose -f docker-compose.dev.yml exec -T server \
  python -m app.scripts.set_password --email jiang@openpaper.dev

# 方式二：已登录状态下调接口
curl -X POST http://10.244.128.73:8100/api/auth/password \
  -H "Content-Type: application/json" \
  -b "session_token=<你的 token>" \
  -d '{"current_password":"旧密码","new_password":"新密码"}'
```

新增账号（`--admin` 可给管理员标志）：

```bash
docker compose -f docker-compose.dev.yml exec -T server \
  python -m app.scripts.set_password --email someone@openpaper.dev --name "Someone"
```

不传 `--password` 时会交互式输入两次，避免密码进入 shell 历史和进程列表。

### 注册

登录页底部有"Create one"入口，也可以直接访问 http://10.244.128.73:3100/register
填写邮箱、密码（可选姓名），提交后自动登录。

- 账号和 scrypt 密码哈希写入本地 `users.password_hash`，不出网
- 新账号自动获得 **researcher** 额度（论文 500 篇、项目 100 个等）。因为这里没有
  Stripe，落到 Basic 档就等于永远无法升级，所以注册时直接签一条有效期约 100 年的
  本地 subscription 记录（`app/helpers/entitlements.py`）。这不是付费记录，不产生扣费
- 新账号的库是**空的**：现有的 12 篇论文属于 `jiang@openpaper.dev`

要关闭公开注册、只允许管理员用 CLI 建号：

```bash
# server/.env 里加一行，然后重启 server
ALLOW_LOCAL_REGISTRATION=false
```

关闭后 `/register` 接口返回 403，页面会显示 "Registration is disabled on this
deployment."。

### 为什么不能用 Google / 邮箱

Google OAuth 要求回调 URI 是公网可注册的域名，本机只有内网地址；邮箱验证码需要
Resend 发信（`RESEND_API_KEY`），同样要外网。所以登录页已经只保留账号密码表单，
原来的 Google 按钮和邮箱验证码流程在 UI 上已移除（后端路由还在，但没有可用凭据，
调了也走不通）。

### 备用：用 session token 登录

如果忘记密码，可以在服务器上直接铸一个 token 再换成 cookie：

```bash
DEV_HOST=10.244.128.73 bash scripts/dev session
# 浏览器打开：
# http://10.244.128.73:8100/api/auth/session?token=<token>
```

token 在 URL 里，会留在浏览器历史和访问日志，仅限受信网络。

## 解除额度限制

自托管不卖套餐，researcher 的 500 篇 / 3 GB / 150k 对话额度这类上限只对"引导升级"
有意义。现在由 `server/.env` 里的一个开关统一抬起：

```
UNLIMITED_USAGE=true
MAX_UPLOAD_SIZE_MB=200
```

- `UNLIMITED_USAGE=true`：`get_plan_limits()` 对全部 8 项额度（上传篇数、知识库容量、
  对话额度、音频概述、项目数、数据表、discover 检索、项目内论文数）统一返回
  `1_000_000_000`，所有 `can_user_*` 检查因此全部放行
- 为什么不是 `inf`：`/api/subscription/usage` 是 JSON，`Infinity` 不是合法 JSON
  （Starlette 序列化时会直接 500），所以用一个大整数占位
- 接口额外返回 `"unlimited": true`，前端据此把界面显示成 **Unlimited**，而不是
  `1000000000 total`
- `MAX_UPLOAD_SIZE_MB=200`：单个 PDF 的体积上限，从 30 MB 放宽。这是文件级限制，
  不是套餐额度

要恢复原来的分级限制，把 `UNLIMITED_USAGE` 改成 `false`。

⚠️ **改完 `server/.env` 必须重建容器**，`restart` 不会重读 `env_file`：

```bash
docker compose -f docker-compose.dev.yml up -d --force-recreate server
```

## 常用命令

```bash
cd /home/jiang/projects/openpaper
export DEV_HOST=10.244.128.73

docker compose -f docker-compose.dev.yml ps                  # 状态
docker compose -f docker-compose.dev.yml logs -f server      # 后端日志
docker compose -f docker-compose.dev.yml logs -f jobs-worker # 任务日志
docker compose -f docker-compose.dev.yml restart server      # 重启后端
docker compose -f docker-compose.dev.yml down                # 停止（保留数据）
docker compose -f docker-compose.dev.yml down -v             # 停止并清空数据
```

容器均为 `restart: unless-stopped`，Docker 已设为开机自启，宿主机重启后会自动恢复。

## 配置

- `server/.env` — LLM、S3、Celery、域名
- `jobs/.env` — 同上的 worker 侧，另有 `CELERY_RESULT_BACKEND`
- `client/.env.local` — `NEXT_PUBLIC_API_URL`

切换模型改 `DEEPSEEK_DEFAULT_MODEL` / `DEEPSEEK_FAST_MODEL`（可选
`deepseek-v4-pro`、`deepseek-flash`），然后 `docker compose ... up -d server
jobs-worker jobs-beat jobs-api`。

## 相对上游的代码改动

| 文件 | 改动 |
| --- | --- |
| `server/app/llm/provider.py` | 新增 `DeepSeekProvider`（OpenAI 兼容，无原生 PDF 输入，结构化输出降级为 `json_object`）；`OpenAIProvider` 抽出 `provider_enum`/`supports_json_schema` 供子类覆盖 |
| `server/app/llm/base.py` | 新增 `LLMProvider.DEEPSEEK`；只初始化有 key 的 provider（上游是全初始化）；默认 provider 由 `DEFAULT_LLM_PROVIDER` 或首个有 key 的 provider 决定 |
| `server/app/**` 8 个调用点 | `LLMProvider.GEMINI` → `LLMProvider.DEEPSEEK` |
| `jobs/src/llm_client.py` | 由 Google GenAI SDK 改为 OpenAI SDK：显式缓存改为 no-op（DeepSeek 自动前缀缓存），PDF 改为抽取文本内联，`response_schema` 改为 `json_object` + Pydantic 校验重试 |
| `server/app/helpers/s3.py`、`jobs/src/s3_service.py` | 支持 `S3_ENDPOINT_URL`（MinIO，path-style + SigV4）与 `S3_PUBLIC_URL`；未设置时行为与上游一致 |
| `server/app/helpers/unit_conversion.py` | E2B 缺失不再是启动错误，仅禁用图表单位换算 |
| `server/app/helpers/scrape.py` | Firecrawl 缺失不再阻止启动，仅禁用网页抓取 |
| `server/app/api/subscription/config.py` | Stripe 缺失不再阻止启动，仅禁用计费接口 |
| `server/app/auth/utils.py` | 空 `SESSION_COOKIE_DOMAIN` 归一化为 None（IP 地址不能做 cookie Domain） |
| `docker-compose.dev.yml` | 新增 MinIO 服务与 `miniodata` 卷；`SESSION_COOKIE_DOMAIN` 默认留空 |
| `server/app/auth/passwords.py` | 新增：scrypt 密码哈希（标准库，无需新依赖），存储格式 `scrypt$n$r$p$salt$hash` |
| `server/app/database/models.py` + 迁移 `a7c1e9d24b30` | `users` 表新增可空 `password_hash` |
| `server/app/api/auth_api.py` | 新增 `POST /api/auth/login`（邮箱+密码，签发会话 cookie）与 `POST /api/auth/password`（改密）；错误密码固定延迟 0.5s，账号不存在时也走一次哈希校验以避免时序泄露 |
| `server/app/helpers/subscription_limits.py` | 新增 `UNLIMITED_USAGE` 开关：开启时 `get_plan_limits()` 返回 `UNLIMITED_VALUE`（1e9）；usage 响应新增 `unlimited` 字段 |
| `server/app/helpers/parser.py` | `MAX_UPLOAD_SIZE_MB` 改为环境变量（默认仍是 30） |
| `client/src/hooks/useSubscription.ts`、`lib/schema.ts`、papers/projects 页 | 支持 `unlimited`：百分比恒为 0，界面显示 Unlimited |
| `server/app/api/auth_api.py` + `app/helpers/entitlements.py` | 新增 `POST /api/auth/register`（邮箱+密码建号并自动登录），受 `ALLOW_LOCAL_REGISTRATION` 开关控制；注册与 CLI 建号都会签发 researcher 订阅，避免无计费环境下卡在 Basic 档 |
| `client/src/app/(main)/register/page.tsx`、`login/page.tsx` | 新增注册页，登录页增加注册入口 |
| `server/app/scripts/set_password.py` | 新增：创建账号 / 重设密码的 CLI |
| `client/src/app/(main)/login/page.tsx` | 登录页改为账号密码表单，移除 Google 与邮箱验证码入口 |
| `server/app/api/auth_api.py` | 新增 `GET /api/auth/session?token=`：把已存在的 session token 换成 httponly cookie 并跳回前端，替代"控制台贴 cookie"（token 必须已在 `sessions` 表中，属换取而非绕过；`next` 只接受站内相对路径） |
| `client/src/lib/providers.tsx` | 没有 `NEXT_PUBLIC_POSTHOG_KEY` 时跳过 `posthog.init` 与 pageview 捕获，不再刷 "initialized without a token" 警告 |
| `client/src/app/apple-icon.svg` | 由 `icon.svg` 改名：它和 `public/icon.svg` 同时占用 `/icon.svg` 路由，Next 报冲突导致该地址 500 |
| `server/app/database/telemetry.py`、`jobs/src/telemetry.py` | 无 `POSTHOG_API_KEY` 时改用空实现，启动和周期任务不再输出 PostHog ERROR/WARNING |
| `server/Dockerfile`、`jobs/Dockerfile`、`client/Dockerfile.dev` | 加 `PIP_INDEX_URL` / `NPM_REGISTRY` 构建参数（默认国内源）；jobs 镜像改为先建用户再装依赖，并改用 `uv export` + 显式 venv 安装 |

## 已知限制

1. **fixture 里的 11 篇论文打不开 PDF**：它们的 `file_url` 指向上游的
   `https://openpaper.ai/...`，本地 MinIO 没有这些对象。自己上传的论文正常。
   要一份干净的库：`bash scripts/dev reset` 后不要 seed，直接上传。
2. **没有图表换算 / 计算列**：需要 `E2B_API_KEY`。
3. **没有计费**：需要 `STRIPE_API_KEY`。
4. **网页抓取式引用回溯不可用**：需要 `FIRECRAWL_API_KEY`。
5. **浏览器 OAuth 不可用**：内网地址无法注册 Google 回调，只能用 session token。
6. **图片类多模态能力缺失**：DeepSeek 不接受图片/PDF 二进制输入，PDF 以抽取文本
   送入模型。
