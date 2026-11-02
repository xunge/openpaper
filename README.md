# OpenPaper · MinerU OCR 双语阅读版

在 [OpenPaper](https://github.com/khoj-ai/openpaper) 基础上改造的自托管版本，重点增加：

- **MinerU 4.1 OCR**：扫描版 PDF 的版面重建、公式、图片、表格/算法/代码块处理
- **中文双语对照**：OCR 原文 + DeepSeek 翻译，按页展示进度
- **跨页 / 跨栏段落续接**：页首、栏首断句自动拼回上一段
- **表格 / 算法 / 代码 / Figure 图片化**：从原始 PDF 按 bbox 裁剪，而不是输出 HTML/代码块
- **Markdown / ZIP / Word 导出**：Word 使用 `academic-reference.docx` 参考模板排版
- **本地化部署**：MinIO、Postgres、RabbitMQ、Redis、本地邮箱密码登录、无限额度开关
- **中文界面**：应用内主要业务界面已汉化

> 本项目基于 OpenPaper（AGPL-3.0）。上游的聊天、引用、批注、项目、发现、Zotero、音频等功能仍然保留。
> 更细的本机运维记录见 [DEPLOYMENT-LOCAL.md](./DEPLOYMENT-LOCAL.md)。

---

## 1. 相对上游 OpenPaper 做了什么

| 模块 | 改造内容 |
| --- | --- |
| OCR | 接入 **MinerU 4.1.3**；默认 provider 固定为 `mineru`；保留 DeepSeek-OCR-2 / Xiaomi-OCR-0 作为旧文档/API 兼容 |
| OCR 客户端 | 支持 MinerU `file_parse` 200/202、Tasks API 兜底、ZIP 安全解压、轮询、超时与重试 |
| 版面重建 | MinerU 结构化 Markdown → OpenPaper `pages[].blocks[]`；保留标题、段落、公式 LaTeX、图注、阅读顺序 |
| 特殊内容 | `table` / `algorithm` / `code` / `figure` 从**原始 PDF** 300 DPI 裁剪为 PNG；图片标题独立；表格不再重复输出 HTML |
| 跨页/跨栏 | 合并页首或右栏首句小写/非句首的正文，允许中间隔表格或图片；短列表阻断，长编号段落可续接；PDF 连字符用文本层判断 |
| 列表 | 连续 `1. / 2. / 3.`、`1) / 2)` 翻译前合并，跨页/跨栏继续；高度可配置 |
| 翻译 | DeepSeek `deepseek-flash`（可改）；按阅读顺序 6k 字符分块、6 路并发；逐页回传进度 |
| 执行 | 双语流程迁移到 Celery `bilingual` 队列；`jobs-worker` 执行 OCR/翻译，API 进程不阻塞 |
| 导出 | 新增 Markdown、完整 ZIP、Word 下载；Word 由 Pandoc 3.9 转换，使用 `templates/academic-reference.docx` |
| 存储 | 支持 MinIO / 任意 S3 兼容存储；`S3_ENDPOINT_URL` / `S3_PUBLIC_URL` |
| 认证 | 新增本地邮箱密码登录、注册、`set_password` CLI；保留 session cookie；支持 `UNLIMITED_USAGE` |
| UI | 应用内主要页面汉化；双语面板改为 Word 默认下载；修复状态标签、Open/Tags/详情字段、工具栏跳动等 |
| 构建 | server/jobs/client Docker 构建支持国内 PyPI/NPM 镜像参数；server 镜像内置 Pandoc |

### 主要新增/改造文件

```text
jobs/src/bilingual/
  mineru_client.py      MinerU HTTP 客户端、ZIP 安全解压、轮询
  mineru_layout.py      结构化 Markdown/图片化/图注/诊断
  continuation.py       跨页与跨栏续接
  text_normalise.py     公式、列表编号规整
  pipeline.py           OCR → prepare → translate → assemble
  task.py               Celery 双语任务

server/app/helpers/paper_export.py    Markdown/ZIP/DOCX 导出
server/app/api/export_api.py          下载 API
server/app/helpers/ocr_probe.py       MinerU 可用性探测
server/templates/academic-reference.docx
templates/academic-reference.docx
client/src/components/bilingual/BilingualPanel.tsx
```

---

## 2. 功能概览

### 保留的上游能力

- 论文阅读、PDF 高亮/批注/笔记
- AI 对话与可点击引用
- 项目、跨论文分析、数据表、图表
- 论文发现、Zotero 导入
- 音频概述（需要额外部署 TTS，见第 9 节）

### 本版本新增能力

- 扫描 PDF 的双语 OCR 阅读
- MinerU 版面重建
- 表格/算法/代码/Figure 原 PDF 裁剪图
- 跨页、跨栏段落和列表续接
- Markdown / 图片 ZIP / Word 导出
- 本地 MinIO 对象存储
- 本地账号密码登录
- 中文界面

---

## 3. 系统架构

```text
Browser
  │
  ├── Next.js client :80        （同一主机，API 走 server:8000）
  │
  ├── FastAPI server :8100      （上传、阅读、导出、鉴权、webhook）
  │       │
  │       ├── Postgres :5432
  │       ├── MinIO :9000       （PDF、页面 PNG、OCR 图片、DOCX 缓存）
  │       ├── RabbitMQ :5672    （Celery broker）
  │       └── Redis :6379       （Celery result）
  │
  └── Celery jobs
          ├── jobs-api :8101
          ├── jobs-worker          （OCR、翻译、Zotero、音频任务）
          └── jobs-beat

MinerU（宿主机，GPU）
  ├── mineru-api :8000          （vLLM + MinerU 模型）
  ├── mineru-fileparse :8003    （文件解析 API，供 OpenPaper 使用）
  └── mineru-webui :7860        （可选调试）
```

OpenPaper 容器通过 `host.docker.internal:8003/8000` 访问宿主机 MinerU。

---

## 4. 服务器要求

### 最低建议

| 项 | 建议 |
| --- | --- |
| OS | Ubuntu 22.04 / 24.04 LTS，x86_64 |
| CPU | 8 核以上 |
| RAM | 32 GB 以上（MinerU + vLLM + 服务） |
| GPU | NVIDIA 24 GB 显存以上（RTX 4090 / A10 / L4 / A100 等） |
| 驱动 | 支持 CUDA 13 的 NVIDIA Driver（当前 MinerU 镜像基于 CUDA 13.0.2；驱动至少 580 系列） |
| 磁盘 | 150 GB 以上 SSD（MinerU 镜像约 25 GB，模型已内置） |
| Docker | Docker Engine 24+，Docker Compose v2 |
| GPU Runtime | NVIDIA Container Toolkit |

> 没有 GPU 不能运行本 README 的 MinerU 本地部署；可以改为远程 MinerU 服务，但需要相应修改 `MINERU_*_URL`。
>
> 如果还要部署 Qwen3-TTS 音频，需额外显存/磁盘，见第 9 节。MinerU 和 TTS 默认都抢 GPU，建议分卡或只启用一个。

### 软件安装

```bash
# Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
newgrp docker

# NVIDIA Container Toolkit（Ubuntu）
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# 验证
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

如果 `nvidia-smi` 显示的驱动版本低于 MinerU 基础镜像要求，请改用匹配的 `vllm/vllm-openai` 基础镜像重新构建 MinerU，或升级驱动。

---

## 5. 新服务器部署总流程

```text
1. 安装 Docker / NVIDIA 驱动
2. 准备 MinerU 镜像
3. 启动 MinerU API + file-parse
4. 拉取 OpenPaper 代码
5. 写 server/.env、jobs/.env、client/.env.local
6. 启动 Postgres/RabbitMQ/Redis/MinIO
7. 创建 MinIO bucket + CORS
8. 构建并启动 OpenPaper 服务
9. 创建管理员账号
10. 验证 OCR / 翻译 / 导出
```

以下命令默认：

```bash
export SERVER_IP=192.0.2.10          # 换成你的服务器公网/内网 IP 或域名
export OPENPAPER_DIR=/opt/openpaper
```

如果使用域名，把 `SERVER_IP` 换成域名；生产环境强烈建议 HTTPS。

如果 80 端口已被占用，可把 client 改到 3100：

```bash
export CLIENT_PORT=3100
# 此时 CLIENT_DOMAIN 也要相应改为 http://${SERVER_IP}:3100
```

### 5.1 脚本化部署（新增）

上面的手动步骤已经整理成可重复执行的脚本：

```text
scripts/deploy/
├── deploy.sh                 # 总入口
├── 00-preflight.sh
├── 10-install-docker.sh
├── 20-install-nvidia.sh
├── 30-load-mineru.sh
├── 40-start-mineru.sh
├── 50-init-env.sh
├── 60-init-minio.sh
├── 70-start-openpaper.sh
├── 80-create-admin.sh
└── 90-verify.sh
```

首次使用：

```bash
cd /opt/openpaper
cp deploy.env.example deploy.env
chmod 600 deploy.env
vi deploy.env                 # 填 IP/域名、DeepSeek Key、MinIO 密码、GPU

scripts/deploy/deploy.sh all
```

`all` 会执行：

```text
preflight → Docker → NVIDIA Toolkit → MinerU 镜像
→ MinerU 启动 → env 生成 → MinIO bucket/CORS
→ OpenPaper 构建/迁移/启动 → 健康检查
```

然后**人工创建管理员**：

```bash
scripts/deploy/deploy.sh admin
```

也可以分阶段执行：

```bash
scripts/deploy/deploy.sh preflight
scripts/deploy/deploy.sh docker
scripts/deploy/deploy.sh nvidia
scripts/deploy/deploy.sh mineru
scripts/deploy/deploy.sh env
scripts/deploy/deploy.sh minio
scripts/deploy/deploy.sh openpaper
scripts/deploy/deploy.sh verify
```

常用调试：

```bash
DRY_RUN=1 scripts/deploy/deploy.sh env     # 只打印，不写文件
FORCE_ENV=1 scripts/deploy/deploy.sh env   # 重新生成 env
SKIP_GPU=1 scripts/deploy/deploy.sh preflight
DRY_RUN=1 scripts/deploy/deploy.sh mineru
```

脚本会尽量幂等，但以下仍然必须人工确认：

- NVIDIA **驱动**是否安装（Toolkit 脚本只装容器运行时）
- MinerU 镜像从哪里来（`MINERU_IMAGE_TAR` / `MINERU_IMAGE_REGISTRY`）
- `deploy.env` 中的 API Key、密码、域名、GPU 编号
- 管理员密码（终端交互输入）
- 域名 DNS、TLS、防火墙/安全组

`deploy.env` 不要提交 Git。

---

## 6. MinerU 完整部署

### 6.1 准备镜像

本机已有 `mineru:4` 镜像，约 25 GB，模型已经烘进镜像：

```bash
docker image inspect mineru:4 --format '{{.Size}}'
```

模型路径：

```text
/root/.mineru/models/MinerU2.5-Pro-2605-1.2B
/root/.mineru/models/MinerU-4_models_torch
```

#### 方式 A：从旧服务器传输镜像（最快、最一致）

```bash
# 旧服务器
docker save mineru:4 | gzip -1 > mineru4.tar.gz
scp mineru4.tar.gz user@NEW_SERVER:/tmp/

# 新服务器
gunzip -c /tmp/mineru4.tar.gz | docker load
docker image inspect mineru:4 >/dev/null && echo OK
```

#### 方式 B：在新服务器重新构建

需要能访问 Docker Hub / vLLM 镜像和模型下载源，构建时间较长，磁盘至少 60 GB。

```bash
mkdir -p /opt/mineru-build && cd /opt/mineru-build
cat > Dockerfile <<'EOF'
FROM vllm/vllm-openai:v0.21.0

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 \
    && rm -rf /var/lib/apt/lists/*

# 国内网络默认走阿里云 PyPI；海外可去掉 -i 参数
RUN python3 -m pip install -U 'mineru[torch]>=4.0,<5' \
    -i https://mirrors.aliyun.com/pypi/simple \
    --break-system-packages \
    && python3 -m pip cache purge

# 下载 MinerU 4.1 standard 模型并烘进镜像
RUN mineru-kit models download \
    --tier standard \
    --small-backend torch \
    --vlm-engine vllm \
    -s modelscope

ENV MINERU_MODEL_SMALL_BACKEND=torch
ENV MINERU_MODEL_VLM_ENGINE=vllm
ENV MINERU_TELEMETRY_DISABLED=1
ENTRYPOINT ["/bin/bash", "-c", "export MINERU_MODEL_SOURCE=local && exec \"$@\"", "--"]
EOF

docker build --network=host -t mineru:4 .
```

构建完成后验证：

```bash
docker run --rm mineru:4 mineru-kit --help | head
```

#### 方式 C：镜像仓库

```bash
# 旧服务器
docker tag mineru:4 registry.example.com/openpaper/mineru:4
docker push registry.example.com/openpaper/mineru:4

# 新服务器
docker pull registry.example.com/openpaper/mineru:4
docker tag registry.example.com/openpaper/mineru:4 mineru:4
```

> 镜像很大，建议使用私有 Registry 或 `docker save`，不要每次重新构建。

### 6.2 准备目录和网络

```bash
sudo mkdir -p /opt/mineru/{input,output,state/fileparse}
sudo chown -R "$USER":"$USER" /opt/mineru

docker network inspect mineru >/dev/null 2>&1 || docker network create mineru
```

### 6.3 启动 MinerU API

`mineru-api` 需要 GPU。下面默认使用 GPU 0；多卡机器改成实际编号：

```bash
docker run -d \
  --name mineru-api \
  --restart unless-stopped \
  --gpus '"device=0"' \
  --shm-size=16g \
  --network mineru \
  -p 8000:8000 \
  -v /opt/mineru/input:/workspace/input:ro \
  -v /opt/mineru/output:/workspace/output \
  mineru:4 \
  api-server --host 0.0.0.0 --port 8000 --tier standard
```

等待 API 就绪：

```bash
for i in $(seq 1 60); do
  curl -fsS http://127.0.0.1:8000/v1/health && break
  echo "waiting mineru-api..."
  sleep 10
done
```

应该返回 200。查看模型/层：

```bash
curl -s http://127.0.0.1:8000/v1/tiers | head -c 1000
docker logs --tail 100 mineru-api
nvidia-smi
```

### 6.4 启动 file-parse 服务

OpenPaper 优先使用 8003 的 `file_parse`；8000 Tasks API 作为兜底。

```bash
docker run -d \
  --name mineru-fileparse \
  --restart unless-stopped \
  --network mineru \
  -p 8003:8003 \
  -v /opt/mineru/state/fileparse:/state \
  mineru:4 \
  file-parse-server \
    --host 0.0.0.0 \
    --port 8003 \
    --api-url http://mineru-api:8000 \
    --state-dir /state
```

验证：

```bash
curl -fsS http://127.0.0.1:8003/docs >/dev/null && echo "file-parse OK"
curl -fsS http://127.0.0.1:8000/v1/health >/dev/null && echo "api OK"
```

可用一个 PDF 做最小测试：

```bash
curl -f -X POST \
  -F "file=@/path/to/sample.pdf" \
  http://127.0.0.1:8003/v1/file_parse \
  -o /tmp/mineru-result.zip \
  -D /tmp/mineru-headers.txt
cat /tmp/mineru-headers.txt
unzip -l /tmp/mineru-result.zip | head
```

如果返回 202，表示异步任务；OpenPaper 的 `mineru_client.py` 会自动轮询。

### 6.5 可选 WebUI

```bash
docker run -d \
  --name mineru-webui \
  --restart unless-stopped \
  --network mineru \
  -p 7860:7860 \
  mineru:4 \
  webui --server-name 0.0.0.0 --server-port 7860 --api-url http://mineru-api:8000
```

访问 `http://SERVER_IP:7860` 仅用于调试，不要暴露到公网。

### 6.6 让 OpenPaper 访问 MinerU

OpenPaper 的 compose 已经给 `server` 和 `jobs-worker` 配了：

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

所以容器内使用：

```text
MINERU_FILE_PARSE_URL=http://host.docker.internal:8003
MINERU_TASKS_API_URL=http://host.docker.internal:8000
```

不要写成 `127.0.0.1`，容器里的 127.0.0.1 是容器自己。

验证容器到 MinerU：

```bash
cd /opt/openpaper
docker compose -f docker-compose.dev.yml exec -T server \
  python -c "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:8003/docs').status)"
```

> 安全：8000/8003/7860 只应对 OpenPaper 所在主机开放，不能直接暴露公网。云安全组只放行 80/443；本机防火墙限制 8000/8003 来源。

---

## 7. 部署 OpenPaper

### 7.1 把项目上传到新服务器

方式 A：通过 Git

```bash
sudo mkdir -p /opt/openpaper
sudo chown "$USER":"$USER" /opt/openpaper
git clone <YOUR_REPOSITORY_URL> /opt/openpaper
cd /opt/openpaper
```

方式 B：直接 rsync 当前工作目录（适合还没有远端仓库）

```bash
# 在项目所在机器执行
rsync -av --progress   --exclude node_modules   --exclude .next   --exclude __pycache__   --exclude .git   /home/jiang/projects/openpaper/ user@NEW_SERVER:/opt/openpaper/

# 新服务器
cd /opt/openpaper
```

把仓库中的 `templates/academic-reference.docx` 一并保留；server 镜像构建时会复制 `server/templates/` 到 `/app/templates/`。

### 7.2 写 server/.env

先生成一个由 server 与 jobs 共用的内部密钥：

```bash
export JOBS_SECRET=$(openssl rand -hex 32)
```

然后写入：

```bash
cat > server/.env <<EOF
DEBUG=False

# 数据库（容器内地址；compose 也会覆盖）
DATABASE_URL=postgresql://postgres:postgres@postgres:5432/annotated-paper

# LLM：DeepSeek（OpenAI 兼容）
DEFAULT_LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-your-deepseek-key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_DEFAULT_MODEL=deepseek-chat
DEEPSEEK_FAST_MODEL=deepseek-chat
# 如果你的网关使用其它模型名，例如 deepseek-v4-pro / deepseek-flash，按实际填写

# 对象存储：MinIO
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin
AWS_REGION=us-east-1
S3_BUCKET_NAME=openpaper
# 必须同时能被浏览器和容器访问；不要用 minio:9000（浏览器无法解析）
S3_ENDPOINT_URL=http://${SERVER_IP}:9000
S3_PUBLIC_URL=http://${SERVER_IP}:9000/openpaper

# Celery / webhook
CELERY_BROKER_URL=pyamqp://guest:guest@rabbitmq:5672//
CELERY_API_URL=http://jobs-api:8001
WEBHOOK_BASE_URL=http://server:8000
JOBS_INTERNAL_SECRET=${JOBS_SECRET}

# 前端 / API 地址（按你的实际端口）
CLIENT_DOMAIN=http://${SERVER_IP}
API_DOMAIN=http://${SERVER_IP}:8100
SESSION_COOKIE_DOMAIN=

# 自托管：不做额度限制
UNLIMITED_USAGE=true
MAX_UPLOAD_SIZE_MB=200
ALLOW_LOCAL_REGISTRATION=true

# OCR / 双语
OCR_PROVIDER=mineru
MINERU_FILE_PARSE_URL=http://host.docker.internal:8003
MINERU_TASKS_API_URL=http://host.docker.internal:8000
MINERU_API_MODE=auto
MINERU_TIER=standard
MINERU_OCR_MODE=auto
MINERU_PRESERVE_VISUAL_BLOCKS=true
MINERU_VISUAL_BLOCK_TYPES=table,algorithm,code

# Word / Markdown 导出
EXPORT_MAX_ASSET_MB=64
EXPORT_MAX_TOTAL_MB=512
EXPORT_PANDOC_TIMEOUT=120
EXPORT_PANDOC_CONCURRENCY=2
EXPORT_REFERENCE_DOC=/app/templates/academic-reference.docx

# 可选：音频 TTS（不部署 TTS 就不要设置）
# TTS_BASE_URL=http://tts-proxy:8000/v1
# TTS_MODEL=Qwen3-TTS-12Hz-1.7B-CustomVoice
EOF
```

要点：

- `S3_ENDPOINT_URL` 必须填**浏览器和容器都能访问的宿主机 IP/域名**，因为平台会返回预签名 URL；不要填 `minio:9000`。
- 如果云主机的公网 IP 不支持容器回环访问（NAT hairpin），改用云内网 IP、DNS 名称或 MinIO 反向代理域名。
- 如果使用 HTTPS/反向代理，`CLIENT_DOMAIN` 用 `https://your.domain`，MinIO 也要 HTTPS 或独立域名。
- `JOBS_INTERNAL_SECRET` 必须与 `jobs/.env` 完全一致。
- `UNLIMITED_USAGE=true` 只适合自托管；商业部署请去掉。

### 7.3 写 jobs/.env

```bash
cat > jobs/.env <<EOF
DEBUG=False
SERVICE_NAME=jobs
LOG_LEVEL=INFO

DEFAULT_LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-your-deepseek-key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_DEFAULT_MODEL=deepseek-chat
DEEPSEEK_FAST_MODEL=deepseek-chat

AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin
AWS_REGION=us-east-1
S3_BUCKET_NAME=openpaper
S3_ENDPOINT_URL=http://${SERVER_IP}:9000
S3_PUBLIC_URL=http://${SERVER_IP}:9000/openpaper

CELERY_BROKER_URL=pyamqp://guest:guest@rabbitmq:5672//
CELERY_RESULT_BACKEND=redis://redis:6379/0
WEBHOOK_BASE_URL=http://server:8000
JOBS_INTERNAL_SECRET=${JOBS_SECRET}

OCR_PROVIDER=mineru
MINERU_FILE_PARSE_URL=http://host.docker.internal:8003
MINERU_TASKS_API_URL=http://host.docker.internal:8000
MINERU_API_MODE=auto
MINERU_TIER=standard
MINERU_OCR_MODE=auto
MINERU_PRESERVE_VISUAL_BLOCKS=true
MINERU_VISUAL_BLOCK_TYPES=table,algorithm,code
MINERU_DPI=300
MINERU_MAX_PDF_MB=100
MINERU_MAX_ZIP_MB=512
MINERU_TASK_TIMEOUT=1800

TRANSLATE_MODEL=deepseek-chat
TRANSLATE_CHUNK_CHARS=6000
TRANSLATE_WORKERS=6
EOF
```


### 7.4 写 client/.env.local

如果是本 README 的直接 IP/端口部署：

```bash
printf 'NEXT_PUBLIC_API_URL=\n' > client/.env.local
```

这样浏览器会访问 `http://SERVER_IP:8100/api`；需要确保 8100 可从浏览器访问。

如果使用反向代理、希望走同源 `/api`：

```bash
printf 'NEXT_PUBLIC_API_URL=https://your.domain\n' > client/.env.local
```

并在 compose 中把 `NEXT_PUBLIC_API_URL` 改成同一值；Next 的 `API_INTERNAL_URL=http://server:8000` 会把 `/api` 代理到后端。

### 7.5 启动基础服务并创建 MinIO bucket

```bash
cd /opt/openpaper

docker compose -f docker-compose.dev.yml up -d postgres rabbitmq redis minio

# 等健康
docker compose -f docker-compose.dev.yml ps
```

创建 bucket：

```bash
docker run --rm \
  --network openpaper-dev_default \
  -e MC_HOST_local="http://minioadmin:minioadmin@minio:9000" \
  minio/mc mb --ignore-existing local/openpaper

docker run --rm \
  --network openpaper-dev_default \
  -e MC_HOST_local="http://minioadmin:minioadmin@minio:9000" \
  minio/mc ls local/openpaper
```

配置浏览器 CORS（页面需要直接读取 MinIO 预签名 PDF/图片）：

```bash
cat > /opt/openpaper/minio-cors.json <<EOF
{
  "CORSRules": [
    {
      "AllowedOrigins": ["http://${SERVER_IP}", "http://${SERVER_IP}:3100"],
      "AllowedMethods": ["GET", "HEAD"],
      "AllowedHeaders": ["*"],
      "ExposeHeaders": ["ETag", "Content-Length", "Content-Type"],
      "MaxAgeSeconds": 3600
    }
  ]
}
EOF

docker run --rm \
  --network openpaper-dev_default \
  -v /opt/openpaper/minio-cors.json:/cors.json:ro \
  -e MC_HOST_local="http://minioadmin:minioadmin@minio:9000" \
  minio/mc cors set local/openpaper /cors.json
```

如果使用 HTTPS，把 `https://your.domain` 加入 `AllowedOrigins`。

### 7.6 构建并启动 OpenPaper

只启动需要的服务，**不要启动 `tts`、`tts-vllm`、`ocr`、`ocr-xiaomi`**；这些会额外占用 GPU，且 MinerU 已经承担 OCR。

```bash
cd /opt/openpaper

docker compose -f docker-compose.dev.yml up -d --build \
  postgres rabbitmq redis minio migrate \
  server jobs-api jobs-worker jobs-beat client
```

首次启动会：

- 构建 server / jobs / client 镜像
- 运行 Alembic 迁移
- 启动 FastAPI、Celery、Next.js

检查：

```bash
docker compose -f docker-compose.dev.yml ps
curl -fsS http://127.0.0.1:8100/docs >/dev/null && echo "server OK"
curl -fsS http://127.0.0.1:8101/health && echo "jobs OK"
curl -fsS http://127.0.0.1/ >/dev/null && echo "client OK"
```

### 7.7 创建第一个账号

```bash
docker compose -f docker-compose.dev.yml exec server \
  python -m app.scripts.set_password \
  --email admin@example.com \
  --name "Admin" \
  --admin
```

按提示输入密码。浏览器打开：

```text
http://SERVER_IP/login
```

如果客户端不在 80 端口，按 `CLIENT_PORT` 调整。

---

## 8. 验证平台

### 8.1 上传并跑通 OCR

1. 登录后上传一个扫描 PDF。
2. 打开论文，右侧打开 **Bilingual view**。
3. 点击开始 OCR + 翻译。
4. 确认状态从 `pending → running(render/ocr/prepare/translate/assemble) → completed`。
5. 确认页面出现：
   - 中文渲染文本
   - 公式 LaTeX
   - 表格/算法/代码/Figure 图片
   - 跨页/跨栏段落已拼接
   - 连续编号列表显示为 `1. 2. 3.` 或 `1) 2) 3)`

后端排查：

```bash
docker compose -f docker-compose.dev.yml logs -f jobs-worker
docker compose -f docker-compose.dev.yml logs -f server
docker exec mineru-api nvidia-smi
```

### 8.2 验证下载

双语对照面板标题栏右侧：

- **Word**：默认下载 `.docx`
- 下拉菜单：Markdown / 完整 ZIP

Word 检查：

- 中文宋体、英文 Times New Roman
- 正文 12pt，首行缩进 2 字符（`firstLineChars=200`）
- 单倍行距、段前段后 0
- 标题 1–6 级字号、加粗
- 图片居中且不缩进
- 图注为 `Image Caption` / `Table Caption`
- 公式为 Word 原生公式

### 8.3 测试命令（可选）

```bash
# 后端测试
docker compose -f docker-compose.dev.yml exec -T server \
  python -m unittest discover -s tests -t .

# 前端类型检查
docker compose -f docker-compose.dev.yml exec -T client \
  node_modules/.bin/tsc --noEmit
```

当前已知：server 测试中 `UNLIMITED_USAGE=true` 会导致 5 个额度断言失败；前端 `AudioOverview.tsx` 有两个既有 `nova` 类型错误。这两项不影响部署。

---

## 9. 可选：音频 TTS

不部署 TTS 不影响阅读、OCR、翻译、导出；只是音频概述不可用。

本仓库 compose 包含两套后端，默认不要全开：

- `tts`：Qwen3-TTS 自定义镜像
- `tts-vllm`：vLLM-Omni
- `tts-proxy`：空闲挂起/唤醒代理

MinerU 和 TTS 会争抢 GPU，建议：

- 单卡：只开 MinerU，或只开 TTS
- 多卡：MinerU API 用 GPU 0，TTS 用 GPU 1

下载模型：

```bash
cd /opt/openpaper
TTS_MODELS_DIR=/opt/models/qwen3-tts \
  bash scripts/fetch-tts-models.sh
```

启动 vLLM-Omni + proxy：

```bash
TTS_MODELS_DIR=/opt/models/qwen3-tts \
docker compose -f docker-compose.dev.yml up -d tts-vllm tts-proxy
```

在 `server/.env` 配置：

```bash
TTS_BASE_URL=http://tts-proxy:8000/v1
TTS_MODEL=Qwen3-TTS-12Hz-1.7B-CustomVoice
TTS_MAX_CHUNK_CHARS=800
```

更细的显存、睡眠、音色说明见 [DEPLOYMENT-LOCAL.md](./DEPLOYMENT-LOCAL.md)。

---

## 10. 旧 OCR Provider（可选）

默认只用 MinerU。DeepSeek-OCR-2 / Xiaomi-OCR-0 仅保留给旧文档和 API 调试：

```bash
# 旧 provider 需要额外模型和显存，不建议新部署启用
docker compose -f docker-compose.dev.yml up -d ocr ocr-xiaomi
```

前端已经没有 OCR 模型下拉，始终使用 MinerU；API 仍兼容 `?model=...`。

---

## 11. 生产化建议

当前 `docker-compose.dev.yml` 是单机自托管/开发栈，功能完整，但默认带开发特性：

- 源码 bind mount
- `UVICORN_RELOAD=True`
- Next.js `yarn dev`
- 端口直接暴露

正式对外服务建议：

1. 修改 compose 中默认的 `minioadmin` / `postgres` 密码，并同步更新 `S3_*`、`DATABASE_URL`；不要在生产继续使用示例密码。
2. 修改 compose：
   - `UVICORN_RELOAD: "False"`
   - 移除 `server`、`client` 的源码 bind mount
   - client 改用 `client/Dockerfile` 的 production 构建
   - `client/Dockerfile` 当前写死了上游 `NEXT_PUBLIC_API_URL=https://api.openpaper.ai`，构建前需改成自己的域名/同源 `/api`
3. 只暴露 80/443；Postgres、RabbitMQ、Redis、MinIO、MinerU 端口不直接对公网开放。
4. 使用 Nginx/Caddy 终止 TLS。
5. 设置 `CLIENT_DOMAIN=https://your.domain`、`S3_PUBLIC_URL=https://files.your.domain/openpaper`。
6. 备份 Postgres、MinIO 数据和 `.env`。
7. 不要把 `UNLIMITED_USAGE=true` 用在商业服务。
8. 定期 `docker compose pull` / 重建镜像，先备份再升级。

### 备份

```bash
# Postgres
docker compose -f docker-compose.dev.yml exec -T postgres \
  pg_dump -U postgres -d annotated-paper -Fc > /backup/openpaper-$(date +%F).dump

# MinIO（示例：停写或使用 mc mirror）
docker run --rm --network openpaper-dev_default \
  -e MC_HOST_local="http://minioadmin:minioadmin@minio:9000" \
  minio/mc mirror local/openpaper /backup/minio-openpaper
```

### 升级

```bash
cd /opt/openpaper
git pull
docker compose -f docker-compose.dev.yml build server jobs-api jobs-worker jobs-beat client
docker compose -f docker-compose.dev.yml up -d \
  postgres rabbitmq redis minio migrate \
  server jobs-api jobs-worker jobs-beat client
```

---

## 12. 常见问题

### MinerU 起不来 / GPU 不可用

```bash
nvidia-smi
docker logs mineru-api
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

- 驱动版本不够：换匹配的 vLLM 基础镜像或升级驱动。
- 显存不足：停掉 TTS/其他 OCR，确认只启动 `mineru-api`。
- 镜像 25 GB：确认磁盘空间。

### OpenPaper 容器访问不到 MinerU

```bash
docker compose -f docker-compose.dev.yml exec -T server \
  python -c "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:8003/docs').status)"
```

- Linux 需要 Docker 20.10+ 的 `host-gateway`。
- 如果仍失败，把 `MINERU_*_URL` 改成宿主机实际 IP，并确保防火墙允许容器网段。
- 不能写 `127.0.0.1`。

### 上传成功但 PDF 打不开 / 图片 403

- 检查 `S3_ENDPOINT_URL` 是否是浏览器可达的地址，而不是 `minio:9000`。
- 检查 MinIO bucket `openpaper` 是否存在。
- 检查 MinIO CORS 是否包含 `CLIENT_DOMAIN`。
- 检查 `S3_PUBLIC_URL` 与 `CLIENT_DOMAIN` 的协议/域名/端口。

### 登录后立刻跳回登录页

- `CLIENT_DOMAIN` 必须与浏览器地址完全一致（协议、域名、端口）。
- IP 地址不要把 `SESSION_COOKIE_DOMAIN` 设成 IP；保持空。
- 反向代理时确认 `X-Forwarded-Proto` 和 HTTPS。

### OCR 卡住

```bash
docker compose -f docker-compose.dev.yml logs -f jobs-worker
docker compose -f docker-compose.dev.yml logs -f server
```

- 检查 worker 是否监听 `bilingual` 队列。
- 检查 MinerU 8003/8000。
- `BILINGUAL_STALE_SECONDS` 默认 420 秒；卡死文档会标记失败并允许重试。

### Word 下载 503

server 镜像没有 Pandoc 时会报 `Pandoc is not available`。重新构建 server：

```bash
docker compose -f docker-compose.dev.yml build server
docker compose -f docker-compose.dev.yml up -d server
```

`pypandoc-binary` 会随 server 镜像安装 Pandoc 3.9，不需要 apt / LibreOffice。

---

## 13. 目录说明

```text
openpaper/
├── client/                 Next.js 15 + React 19
├── server/                 FastAPI + SQLAlchemy + Alembic
│   └── templates/          Word 参考模板（构建用）
├── jobs/                   Celery jobs（OCR、翻译、音频、Zotero）
├── templates/              源模板 academic-reference.docx
├── docker/                 TTS / TTS proxy 构建文件
├── docker-compose.dev.yml  单机部署 compose
├── deploy.env.example      部署变量模板
├── scripts/deploy/        新服务器分阶段部署脚本
├── scripts/                dev/fixture/TTS 脚本
├── DEPLOYMENT-LOCAL.md     更详细的本机部署记录
└── README.md               本文件
```

---

## 14. 测试与已知限制

已验证：

- jobs 测试：71 个通过
- server 测试：323 个，5 个既有额度断言失败（`UNLIMITED_USAGE=true`）
- 前端 `tsc`：仅既有 `AudioOverview.tsx` 两个 `nova` 类型错误
- 真实 MinerU 文档：OCR、跨页/跨栏合并、列表、表格/算法/代码/Figure 图片、Markdown/ZIP/Word 导出均已跑通

已知限制：

1. MinerU 需要 GPU；CPU-only 部署需要另选模型/服务。
2. MinerU 镜像约 25 GB，首次传输/构建较慢。
3. `docker-compose.dev.yml` 是开发栈，不是 hardened production manifest。
4. DeepSeek 不接受 PDF/图片二进制，PDF 以抽取文本送入 LLM；OCR 由 MinerU 负责。
5. 未配置 TTS 时音频概述不可用。
6. 未配置 E2B 时图表单位换算/计算列不可用（阅读、OCR、翻译、导出不受影响）。
7. 未配置 Stripe 时计费接口不可用；自托管建议 `UNLIMITED_USAGE=true`。

---

## 15. 上游与许可证

- 上游：https://github.com/khoj-ai/openpaper
- 本仓库保留上游 AGPL-3.0 许可证；二次分发/部署请遵守 AGPL。
- 上游功能说明见 [DEVELOPMENT.md](./DEVELOPMENT.md)、[server/README.md](./server/README.md)、[client/README.md](./client/README.md)、[jobs/README.md](./jobs/README.md)。
- 本机详细改造记录见 [DEPLOYMENT-LOCAL.md](./DEPLOYMENT-LOCAL.md)。
