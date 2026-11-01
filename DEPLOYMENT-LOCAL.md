# 本地自托管部署说明（DeepSeek + MinIO）

本机部署版本，与上游 `DEVELOPMENT.md` 的差异集中在两处：**LLM 换成 DeepSeek**
（上游是 Gemini/OpenAI/Anthropic 三选三全配），**对象存储换成本地 MinIO**
（上游依赖 AWS S3 + Cloudflare CDN）。网络受限（直连 PyPI / Docker Hub 不通），
构建时统一走国内镜像源。

## 访问地址

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| 前端 | http://10.244.128.73 | 主入口 |
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

### 双语对照阅读（OCR + 翻译）

扫描版 PDF 走 OCR + 翻译，产出「左：原页图 / 右：中文 markdown」的对照视图。
入口是论文阅读页右侧面板的「双语对照」，不是独立路由。

| 组件 | 说明 |
| --- | --- |
| OCR 服务 | 默认固定 MinerU 4.1；`ocr`（DeepSeek-OCR-2）与 `ocr-xiaomi` 仅保留给旧文档/API 兼容 |
| 模型 | DeepSeek 在 `/home/jiang/share/model/DeepSeek-OCR-2`；Xiaomi 在 `/home/jiang/share/model/Xiaomi-OCR-0` |
| 接口 | `127.0.0.1:8105` / `127.0.0.1:8106`，OpenAI 兼容 `/v1/chat/completions` |
| 显存 | OCR 0.20 × 96 ≈ 19 GB，Xiaomi 0.12 × 96 ≈ 12 GB，与 tts-vllm（36 GB）共存 |
| 翻译 | `deepseek-flash`，按阅读顺序 6k 字符分块、6 路并发 |
| 执行 | Celery `bilingual` 队列（`jobs-worker`），不在 API 进程里跑 |
| 前端 | 论文页右侧 Bilingual 面板 |

### 管线

```
POST /api/paper/bilingual/ → 建 PaperDocument(pending) → 提交 Celery
PDF → PyMuPDF 渲染每页 PNG(144 DPI) → 存 MinIO
    → OCR 并发出图（4 路）→ 按块切分、版面坐标回填
    → 按阅读顺序分块翻译（6k/块）→ 逐页组装
    → webhook 回写 stage/进度/页面（/api/webhooks/bilingual/{id}）
```

状态机：`pending → running(stage=render|ocr|prepare|translate|assemble) → completed|failed`。
每页完成即回传，前端 3 秒轮询能逐页看到内容；`updated_at` 超过
`BILINGUAL_STALE_SECONDS`（默认 420s）没有心跳时，GET 会把文档标成失败并允许重试。
DELETE 会 revoke 对应的 Celery 任务。

### MinerU 4.1（默认 OCR Provider）

MinerU 作为文档级解析接入，和 DeepSeek/Xiaomi 共用同一条双语流程（翻译、跨页合并、
逐页回调都不变），不是第二条独立管线。

| 项 | 说明 |
| --- | --- |
| File Parse | `http://127.0.0.1:8003`，`POST /v1/file_parse`，200 直接返回 ZIP，202 用 `request_id` 轮询 |
| Tasks API | `http://127.0.0.1:8000`，`POST /v1/tasks` → PUT 上传 → 轮询 → `result_url` |
| 模式 | `MINERU_API_MODE=auto`（优先 8003，不可达时切 8000）/ `file_parse` / `tasks` |
| 选择 | 前端已移除 OCR 下拉，始终使用 MinerU 4.1；API 仍兼容 `?model=...` 供旧客户端/调试 |
| 产物 | `papers/{paper_id}/ocr/{document_id}/content.md`、`source/*`、`assets/*.png` |

`content.md` 是源语言重建稿，图片链接为同前缀的 `assets/...`（导出/调试用）；
阅读器实际使用 API 返回的预签名 `blocks[].image_url`，不会因为 content.md 的相对链接
而失效。`content_url`、`source_urls`、页面 `diagnostics` 与块 `search_text` 都在
`GET /api/paper/bilingual/` 响应里。

Docker 中 MinerU 在宿主机，不在 compose 里；compose 已给 `server`、`jobs-worker`
加 `host.docker.internal:host-gateway`，并把 `MINERU_FILE_PARSE_URL` 指向
`http://host.docker.internal:8003`。宿主机直接跑 jobs 时默认 `127.0.0.1` 即可。

特殊内容规则：

- `table`、`algorithm`、`code` 从**原始 PDF** 按 bbox 裁成 PNG（默认 300 DPI），
  不把 HTML/Markdown 再截图；MinerU 自带区域图只在 PDF 裁剪失败时兜底。
- 算法没有独立类型（MinerU 会落在 `code`），通过 `Algorithm N` / `Procedure` /
  `Pseudocode` / `Require:`/`Ensure:` 等特征识别；代码通过 `guess_lang`、代码围栏、
  关键字与缩进特征识别。误判为 code 的正文会保留为文本并写 `diagnostics`。
- 图片化内容只出现一次：表格不再输出 HTML，算法/代码不再输出代码块；
  标题/图注作为可翻译文本保留一次；`search_text` 留给检索，不进入阅读 Markdown。
- 跨页表格/算法用 `continues_prev` 把两页裁图纵向拼接成一张。
- 旋转页使用 PyMuPDF 的显示坐标（`page.rect` + `get_pixmap(clip=...)`），
  与 MinerU 的 0-1 bbox 一致。
- 无法可靠裁剪时保留文本兜底，并在页 `diagnostics` 里标记 `needs_review`，
  不会伪造 bbox 或声称已图片化。

相关环境变量（worker 侧）：

```bash
OCR_PROVIDER=mineru                 # 默认；需要覆盖时才改
MINERU_FILE_PARSE_URL=http://127.0.0.1:8003
MINERU_TASKS_API_URL=http://127.0.0.1:8000
MINERU_API_MODE=auto
MINERU_TIER=standard
MINERU_OCR_MODE=auto
MINERU_PRESERVE_VISUAL_BLOCKS=true
MINERU_VISUAL_BLOCK_TYPES=table,algorithm,code
MINERU_DPI=300
MINERU_MAX_PDF_MB=100
MINERU_MAX_ZIP_MB=512
MINERU_TASK_TIMEOUT=1800
```

### 表格与坐标

- 表格按 cell 抽取、翻译、回填，保留 `rowspan`/`colspan`；未闭合的 `<table>` 先补闭合，
  单元格边界丢失的表格回退为整块翻译；翻译失败回退原文，不再出现空白块。
- `label[[x0,y0,x1,y1]]` 坐标归一化后存入 `boxes_norm`，点击右侧段落会定位左侧页面区域。
- 有文本层的页面用 `pdf_layout` 把 OCR 块对齐回 PDF 坐标；纯扫描页直接使用模型框。
- 续段统一按“页首/栏首第一句”判断，而不是只看上一页最后一个文本块：
  - 每页第一个正文块、每页右栏第一个正文块，如果开头小写、标点或破折号开头，就并到阅读顺序上
    一个正文块末尾；即使中间隔着表格/图片也继续找；
  - 页首是标题、列表项或不完整句时不会跨标题乱拼；长编号段落（如 `1) CFG Extraction...`）
    可以继续跨页；
  - 页首整栏都是图片、正文从右栏开始时，右栏首句会并到上一页/上一栏正文末尾；
  - 栏尾/页尾连字符用 PDF 文本层判断（`decompi-` + `lation` → `decompilation`）。
  合并后右栏/下一页该段从原位置移除，翻译按整段进行；
  页尾连字符按 PDF 文本层判断（`decompi-` + `lation` 拼成 `decompilation`，不是 `decompi lation`）。
  因此右侧面板的“页”与物理页码不再严格一一对应，下一页开头可能为空或被跳过。
- 有序列表：连续的 `1.`/`2.`/`3.` 或 `1)`/`2)` 在翻译前合并成一个列表块（跨页、跨栏也接续），
  翻译后按源编号校正；前端自定义 `ol` 会透传 `start`，因此单独成块的 `3.` 也不会显示成 `1.`。
- 行间公式统一规范为独立成行的 `$$...$$`。单行 `$$...$$` 会被 remark-math 当成行内公式，
  而 KaTeX 的 `\tag` 只允许出现在行间模式，会直接报错。
- Xiaomi 不输出版面标记，流水线会从 PDF 自身的位图与矢量绘图聚类中恢复图区，
  按阅读顺序插入；图下方 `Figure N:` 题注用于确定下边界，首页标题区会被跳过，
  避免把标题和作者裁进图里。

### 必须记住的坑

1. **`--limit-mm-per-prompt` 要传 JSON**：`'{"image": 1}'`，写成 `image=1` 会直接启动失败。
2. **`max_tokens` 不能等于上下文长度**：图片 token 已占约 1.2k，`--max-model-len 8192` 下 `max_tokens` 必须留余量（这里用 4096），否则请求 400。
3. **Triton 编译错误**：OCR 路径上的内核引用了模块级常量（`LOG2E`），需要 `TRITON_ALLOW_NON_CONSTEXPR_GLOBALS=1`。
4. **表格 `colspan` 大于推断列数**：`table_normalise` 会把 span 夹到网格宽度，否则放置循环会死循环（表现为 worker CPU 100%、进度不动）。
5. **翻译不要一次发整篇**：整篇一次请求会跑满 `max_tokens` 并丢 `@@BLOCK` 编号；6k 分块 + `deepseek-flash` 实测把 13 页从 468s 降到约 100s。

### 依赖

`pymupdf` 在 server 与 jobs 两个镜像里都已固化；本功能不再需要手工 `pip install`。

## vLLM-Omni 推理后端

TTS 现在由 **vLLM-Omni** 提供（官方镜像 `vllm/vllm-omni:v0.30.0`，内置 vllm 0.30.0 + vllm_omni 0.30.0）。

| 项 | 值 |
| --- | --- |
| 服务 | `tts-vllm`，端口 `8103:8000` |
| 启动命令 | `vllm serve /models/Qwen3-TTS-12Hz-1.7B-CustomVoice --omni --served-model-name Qwen3-TTS-12Hz-1.7B-CustomVoice` |
| 接口 | OpenAI 兼容 `POST /v1/audio/speech`（和旧服务一致，应用零改动） |
| 显存 | 约 36 GB（`--gpu-memory-utilization 0.35`，含 KV cache 预留） |
| 冷启动 | 约 6 分钟（FlashInfer autotune + CUDA Graph capture，每次重启重来） |

### 性能对比（同一段 1248 字中文）

| 指标 | 旧实现（qwen-tts + HF） | vLLM-Omni | 提升 |
| --- | --- | --- | --- |
| 生成耗时 | 416 s | **22 s** | **~19×** |
| RTF | 1.25 | **0.086** | ~14.5× |
| GPU 利用率 | 17% | **94%（峰值 100%）** | — |
| 功耗 | 131 W | **345 W** | — |

端到端「生成音频概述」：**13 分 14 秒 → 66 秒**（同一篇论文，产出 4.1 分钟中文音频）。

### 空闲释放显存（自动睡眠）

GPU 常驻 36 GB 太浪费，尤其是这种"偶尔生成一次"的功能。现在由 **`tts-proxy`** 托管生命周期：

```
server → tts-proxy:8000 → tts-vllm:8000
             │
             ├─ 无请求满 TTS_IDLE_SECONDS（默认 300s）→ POST /v1/omni/sleep
             └─ 下一个请求 → POST /v1/omni/wakeup + POST /resume → 转发
```

| 动作 | 耗时 | 显存 |
| --- | --- | --- |
| 空闲 5 分钟后自动睡眠 | 0.4 s | 36.8 GB → **2.6 GB** |
| 睡眠中收到请求，自动唤醒 | ~0.5 s | 恢复到 36.8 GB |
| 唤醒 + 合成 208 字（应用链路） | 4.1 s | — |

睡眠用 **level=1**（权重卸载到内存，不是丢弃），所以唤醒是主机到显存的内存拷贝，而不是重新加载模型。

### 为什么需要代理

vLLM-Omni 自身没有"空闲即释放"的能力，而且它的睡眠接口有三个坑：

1. 通用的 `POST /sleep` 在 Omni 引擎上会报 `'int' object is not iterable`（bug），必须用 **`POST /v1/omni/sleep`**，body 为 `{"stage_ids": [0,1], "level": 1}`
2. 睡眠接口需要启动时加 `--enable-sleep-mode`
3. `wakeup` 之后**必须再调 `POST /resume`**，否则 AR 引擎的调入门禁仍处于暂停，合成请求会一直挂住（这个坑我自己踩过一次，表现为请求 180 秒无响应）

代理把这些细节封装掉了，应用侧只看到普通的 OpenAI 兼容接口。

### 运维入口

```bash
curl localhost:8104/health              # {"status":"ok","sleeping":true|false}
curl -X POST localhost:8104/_proxy/sleep   # 立即睡眠
curl -X POST localhost:8104/_proxy/wake    # 立即唤醒
```

环境变量：`TTS_IDLE_SECONDS`（根 `.env`，默认 300）、`TTS_PROXY_PORT`（默认 8104）。

> 注意：`tts-vllm` 的 8103 端口已在 compose 里改为只绑定 `127.0.0.1`（因为 `VLLM_SERVER_DEV_MODE=1` 会暴露 `/sleep`、`/resume` 等控制接口）。该改动在下次重建容器时生效——重建意味着一次 6-9 分钟的引擎预热。

### 两个实现细节

**1. 模型名。** vLLM 默认用挂载路径当模型 id（`/models/...`），应用若依赖它会把这个路径写进配置。用 `--served-model-name` 固定成 `Qwen3-TTS-12Hz-1.7B-CustomVoice` 后，切换后端不影响应用配置。

**2. 音色归一化。** vLLM 严格校验音色，只认 9 个 Qwen id；旧客户端/历史请求里的 `coral`、`nova` 等 OpenAI 名字会被 400 拒绝。`app/llm/speech.py` 里加了 `normalize_voice()`，在发请求前把旧名字映射到 Qwen 音色。

### 回退到旧实现

旧服务（`tts`，qwen-tts 直跑）仍保留在 compose 里，只是已停止：

```bash
# 切回旧实现
sed -i 's|^TTS_BASE_URL=.*|TTS_BASE_URL=http://tts:8000/v1|' server/.env
docker compose -f docker-compose.dev.yml up -d tts
docker compose -f docker-compose.dev.yml up -d --force-recreate server
```

## 数据安全铁律

2026-10-26 发生过一次事故：清理测试数据时用脚本清空了整个 MinIO 桶，把用户上传的 PDF 一并删掉了（对象存储没有版本控制，删了就没了）。由此定下三条规则：

1. **永远不要"清空桶"**。删对象必须按 key 前缀白名单精确匹配，例如只删 `uploads/<paper_id>-bilingual-page-*.png`。
2. **破坏性操作前先备份**：

```bash
# 数据库
docker compose -f docker-compose.dev.yml exec -T postgres \
  pg_dump -U postgres -Fc annotated-paper > /home/jiang/backups/db-$(date +%Y%m%d-%H%M).dump

# 对象（按前缀，别全量）
DEV_HOST=... docker compose -f docker-compose.dev.yml exec -T server \
  python scripts/backup_objects.py --prefix uploads/
```

3. **删用户/论文用脚本按 FK 联动删**，不要手写 DELETE 猜依赖顺序：`scripts/delete_papers.py`、`scripts/cleanup_user.py`。

## 访问端口

前端发布在 **80**，直接访问 `http://10.244.128.73` 即可（原 `:3100` 已停用）。

根 `.env` 控制：

```
CLIENT_PORT=80
```

注意三处联动，改端口时都要跟着改：

1. `docker-compose.dev.yml` 的 `ports: "${CLIENT_PORT}:3000"`
2. compose 里的 `CLIENT_DOMAIN: http://${DEV_HOST}:${CLIENT_PORT}`（给 server 做 CORS）
3. `server/.env` 的 `CLIENT_DOMAIN`

**默认端口的坑**：发布到 80 时 compose 会算出 `http://host:80`，但浏览器发出的 `Origin` 是 `http://host`（省略默认端口），CORS 不匹配会导致登录失败。`server/app/main.py` 的 `_allowed_origins()` 会自动把 `:80`/`:443` 归一化掉，同时支持逗号分隔多来源。

## 模型权重存放

Qwen3-TTS 权重共 **4.8 GB**，不放在 git 仓库里，也不应该放：

```
/home/jiang/models/qwen3-tts/            # 仓库之外
├── Qwen3-TTS-12Hz-1.7B-CustomVoice/     4.2G
└── Qwen3-TTS-Tokenizer-12Hz/            619M
```

- compose 通过环境变量读取：`${TTS_MODELS_DIR:-/home/jiang/models/qwen3-tts}:/models:ro`
- `.gitignore` 已忽略 `tts-models/` 与 `/models/`，`git add -A` 不会把权重带进去
- 新机器用脚本重新拉取（走 ModelScope，国内直连）：

```bash
TTS_MODELS_DIR=/home/jiang/models/qwen3-tts bash scripts/fetch-tts-models.sh
```

脚本是幂等的：权重已存在时直接跳过。

**为什么不用 Git LFS**：4.8 GB 的二进制放进 LFS 会让 clone 变得极慢，而且 LFS 存储按量计费；权重是可从公开模型仓库重建的产物，用"脚本 + gitignore"比"版本化二进制"更合适。

## 本地 TTS（Qwen3-TTS）

音频概述不再依赖 Azure，改用本地容器：

| 组件 | 值 |
| --- | --- |
| 服务 | `openpaper-dev-tts`，`docker/tts/`（`python:3.12-slim` + torch + qwen-tts） |
| 模型 | `Qwen3-TTS-12Hz-1.7B-CustomVoice`（Apache-2.0），9 个内置音色 |
| 模型路径 | 宿主机 `/home/jiang/projects/openpaper/tts-models`，容器内 `/models` |
| 接口 | `POST /v1/audio/speech`（OpenAI 兼容），`GET /health`，`GET /v1/voices` |
| 端口 | `8102`（`TTS_PORT`） |
| GPU | `gpus: all`（nvidia container runtime），显存约 5 GB |

Open Paper 侧只需要在 `server/.env` 里指向它：

```
TTS_BASE_URL=http://tts:8000/v1
TTS_MODEL=Qwen3-TTS-12Hz-1.7B-CustomVoice
```

`app/llm/speech.py` 里 `TTS_BASE_URL` 存在时走 OpenAI 兼容分支（`LangfuseOpenAI` +
`base_url`），否则回落到原来的 Azure 分支。前端传来的 OpenAI 音色名（`nova`/`coral`…）
由服务端映射到 Qwen 音色，应用代码不用改。

### 性能与调优

实测 1.7B 模型在单张 6000 卡上约 **1.25 倍实时**（416 字 → 80 秒音频，耗时 99 秒）。

- `TTS_MAX_CHUNK_CHARS`（默认 800）：分片大小。Open Paper 会按它切分文本、逐片合成、
  再拼成一个 WAV。**这是超时预算，不是音质参数**：单次请求必须小于客户端的 300 秒超时。
- 想要更快可以改用 `Qwen3-TTS-12Hz-0.6B-CustomVoice`（约 2-3 倍速度，音质略降），
  下载后把 `QWEN_TTS_MODEL` 和 `TTS_MODEL` 一起改掉即可。
- 一篇论文的音频概述脚本约 8000 字，完整生成需要 30 分钟以上，属于长任务——
  它在后台任务里跑，前端有进度。

### 排障

- 首次调用慢：Triton 要 JIT 编译，需要镜像里有 `gcc`（Dockerfile 已装）。
- `RuntimeError: Failed to find C compiler`：镜像是从旧版本构建的，重建即可。
- 合成超时：调小 `TTS_MAX_CHUNK_CHARS`。

## 选择对话模型

论文阅读页的聊天面板右上角（齿轮图标）可以选择模型：

| 选项 | 实际模型 | 特点 |
| --- | --- | --- |
| DeepSeek V4 Pro | `deepseek-v4-pro` | 默认，推理更稳，适合长文分析 |
| DeepSeek Flash | `deepseek-flash` | 更快更便宜，适合快速问答 |

选择通过请求体里的 `llm_model` 传给服务端，服务端在流式调用时用它覆盖默认模型；
不传则用 `DEEPSEEK_DEFAULT_MODEL`。

模型列表来自 `GET /api/message/models`，由服务端按"已配置 provider 的 default/fast 模型"
自动生成——换 provider 或改 `DEEPSEEK_*_MODEL` 环境变量后，选项会自动跟着变，不需要改前端。

## 中英双语

顶栏右侧有一个 **中文 / EN** 开关（语言图标），选择存在浏览器 localStorage
（`op_language`），刷新后保留。

选择中文时同时影响三件事：

| 范围 | 机制 | 说明 |
| --- | --- | --- |
| AI 回答 | 前端把 `Accept-Language: zh-CN` 随请求发出；`main.py` 的中间件写入 ContextVar，`app/llm/base.py` 给所有对话类调用追加"用简体中文回答"的指令 | 论文标题、引文原文、术语、公式保持原文 |
| 新上传论文的摘要/要点 | 上传接口读取同一个请求头，把 `language` 放进 Celery 任务参数；`jobs/src/llm_client.py` 在抽取的 system prompt 上追加中文输出指令 | **只对之后上传的论文生效**，已入库的英文摘要不会自动重译 |
| 界面文案 | `client/src/lib/i18n.tsx` 的字典 + `useI18n()` 的 `t()` | 未翻译的字符串自动回退英文 |

### 语言开关的位置

- 主应用（首页 / 文献库 / 项目 / 问答 / 发现）：顶栏右侧
- **论文详情页**：右上角，在"分享"按钮旁边。这个路由组 `(paper)` 有自己独立的
  layout（不含 `AppHeader`），所以需要单独挂 `I18nProvider` 和 `LanguageToggle`；
  开关放在 `md` 响应式容器之外，窄屏也可见。

### 已汉化的界面

- 侧栏导航（首页 / 文献库 / 项目 / 问答 / 发现）及折叠菜单的"查看全部…"
- 顶栏语言开关、账号菜单（设置 / 反馈 / 套餐 / 退出登录 / 新功能）
- 首页快捷操作卡片（上传论文 / 新建项目 / 发现论文）
- 登录页、注册页
- 首页：搜索框占位符、最近论文、查看文献库、页脚（核心理念 / 博客 / 关于 / GitHub）
- 论文库页：标题、上传按钮、空状态、特色说明、上传上限弹窗、表格列头（标题 / 作者）、
  筛选框占位符、筛选面板（筛选、排序方式、筛选条件、作者 / 标签 / 状态、
  发表日期最新/最早、无匹配作者/标签、无标签）
- 项目页：标题、空状态（整理你的研究 / 副标题 / 创建第一个项目 / 了解更多）、特色说明、
  搜索框、加载与错误提示、新建项目、筛选无结果、项目上限提示与 toast
- 问答页：思考过程的进度提示、错误提示、空状态、输入框占位符

- 阅读器：工具条（搜索 / 上一个下一个匹配 / 无结果 / 放大缩小 / 高亮颜色 /
  行内批注开关）、批注面板（新建笔记、回复、编辑、删除、保存、取消、
  "这篇论文还没有批注" 及各输入框占位符）
- 发现页：发现论文、开放获取、查找更多文献、正在检索：
- 设置页：个人资料、集成、邮箱、姓名
- 计费页：方案卡片、月付/年付、订阅状态（取消 / 即将结束 / 支付逾期 / 支付未完成）、
  更改计费周期、联系支持等
- 弹窗：上传面板（从链接导入、正在处理你的论文）、新建项目（标题 / 描述）

未覆盖：官网落地页（`OpenPaperLanding`，登录前可见）的营销长文案、发现页结果卡片
的部分元信息、以及少量由属性/对象数组驱动的标签。

### 怎么补充其它页面

字典的 key 就是英文原文，加一行即可：

```ts
// client/src/lib/i18n.tsx
const zh: Record<string, string> = {
    "Some English label": "某个中文标签",
}
```

然后在组件里包一层 `t()`：

```tsx
const { t } = useI18n()
<span>{t("Some English label")}</span>
```

没进字典的字符串原样显示，所以可以一个页面一个页面地补，不会出现空白或报错。

## 注册

登录页底部有"Create one"入口，也可以直接访问 http://10.244.128.73/register
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

## 对象存储策略

MinIO 建桶只用一条命令，**但缩略图需要额外一条匿名只读策略**，否则首页的论文封面全部 403：

```bash
docker compose -f docker-compose.dev.yml exec -T server \
  python - < docker/init-minio.py
```

这个脚本做两件事：

1. 建 `openpaper` 桶
2. 给 `uploads/*preview*` 开匿名 `GetObject`

原因：`papers.preview_url` 是入库时写死的**未签名 URL**（上游把桶挂在公开 CDN 上，所以从不签名），而 MinIO 的桶默认私有。策略只放开预览图，**PDF 仍然走短时效的 presigned URL**，匿名访问会返回 403。

重建数据卷（`down -v`）后策略会丢失，需要重新跑一次。

已知残留：fixture 里那 11 篇论文的 `preview_url` 指向上游 `https://openpaper.ai/...`，本地没有对应对象，封面和 PDF 都打不开——这与策略无关，自己上传的论文正常。

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
| `client/src/lib/i18n.tsx`、`components/LanguageToggle.tsx` | 新增语言上下文与开关（localStorage → `Accept-Language`） |
| `client/src/lib/api.ts` | 所有请求（含 SSE 流）附带 `Accept-Language` |
| `server/app/main.py`、`app/llm/base.py` | 按请求设置 ContextVar，给对话类 LLM 调用追加语言指令（结构化输出调用除外） |
| `server/app/api/paper_upload_api.py`、`app/helpers/pdf_jobs.py` | 把语言随上传任务传给 worker |
| `jobs/src/llm_client.py`、`src/tasks.py` | 抽取 system prompt 追加中文输出指令，任务结束复位 |
| `client/src/app/(main)/register/page.tsx`、`login/page.tsx` | 新增注册页，登录页增加注册入口 |
| `server/app/scripts/set_password.py` | 新增：创建账号 / 重设密码的 CLI |
| `client/src/app/(main)/login/page.tsx` | 登录页改为账号密码表单，移除 Google 与邮箱验证码入口 |
| `server/app/api/auth_api.py` | 新增 `GET /api/auth/session?token=`：把已存在的 session token 换成 httponly cookie 并跳回前端，替代"控制台贴 cookie"（token 必须已在 `sessions` 表中，属换取而非绕过；`next` 只接受站内相对路径） |
| `server/app/llm/base.py` | 新增 `get_chat_model_choices()` / `get_default_chat_model()`；`send_message_stream(..., model=)` 支持显式指定模型（流式与降级路径都生效） |
| `server/app/api/message_api.py`、`app/llm/paper_operations.py`、`multi_paper_operations.py` | 请求新增 `llm_model`，透传到流式调用 |
| `client/src/components/SidePanelContent.tsx` | 模型选择器从"选 provider"改为"选模型"，发送 `llm_model` |
| `client/src/lib/providers.tsx` | 没有 `NEXT_PUBLIC_POSTHOG_KEY` 时跳过 `posthog.init` 与 pageview 捕获，不再刷 "initialized without a token" 警告 |
| `client/src/app/apple-icon.svg` | 由 `icon.svg` 改名：它和 `public/icon.svg` 同时占用 `/icon.svg` 路由，Next 报冲突导致该地址 500 |
| `server/app/database/telemetry.py`、`jobs/src/telemetry.py` | 无 `POSTHOG_API_KEY` 时改用空实现，启动和周期任务不再输出 PostHog ERROR/WARNING |
| `jobs/src/bilingual/`、`src/bilingual/task.py` | 双语流水线迁入 Celery：OCR、版面、翻译、表格重建、进度回调；队列 `bilingual` |
| `server/app/api/paper_bilingual_api.py`、`app/helpers/bilingual_jobs.py` | 提交/revoke Celery 任务，逐页回传，僵死超时恢复 |
| `server/app/api/webhook_api.py` | `/api/webhooks/bilingual/{id}` 接收进度/完成/失败，按 `task_id` 忽略旧任务 |
| 迁移 `f4b2c8d1e930` | `paper_documents` 新增 `task_id`/`stage`/`stage_progress`/`stage_total`；唯一约束改为 `(paper_id, user_id)` |
| `client/src/components/bilingual/BilingualPanel.tsx`、`lib/i18n.tsx` | 阶段进度、失败重试、部分页面保留 |
| `jobs/src/bilingual/mineru_client.py` | MinerU 4.1 客户端：file_parse 200/202、tasks API 兜底、轮询、ZIP 安全解压、大小/超时/重试限制 |
| `jobs/src/bilingual/mineru_layout.py` | 结构化块重建 Markdown；table/algorithm/code 从原 PDF 300 DPI 切图；图注、跨页拼接、诊断与 search_text |
| `jobs/src/bilingual/ocr_providers.py`、`ocr_client.py` | 注册 `mineru` provider 与健康检查路径 |
| `jobs/src/bilingual/pipeline.py`、`task.py` | MinerU 文档级分支复用同一翻译/跨页合并/回调流程；保留 search_text 与 diagnostics |
| `server/app/helpers/ocr_probe.py` | 默认 provider 为 MinerU，并探测 host-gateway 地址可用性 |
| `server/app/api/paper_bilingual_api.py`、`server/tests/test_bilingual_serialise.py` | 暴露 content.md / MinerU 源文件 URL、search_text、diagnostics |
| `docker-compose.dev.yml`、`server/.env.example` | MinerU 环境变量与 `host.docker.internal:host-gateway` |
| `client/src/components/bilingual/BilingualPanel.tsx`、`lib/i18n.tsx` | MinerU 阶段、图片点击查看大图、search_text 类型 |
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
