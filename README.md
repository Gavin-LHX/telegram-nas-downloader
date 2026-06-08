# Telegram NAS Downloader

一个用于把 Telegram 群组/频道里的视频自动下载到 NAS 目录的轻量工具。它使用 Telegram 客户端 API 登录你的个人账号，适合归档你已经有权限访问的私有群、私有频道和公开频道内容。

> 请只下载和保存你有权归档的内容，并遵守 Telegram 服务条款、群组/频道规则和当地法律法规。

## 主要功能

- 监听指定 Telegram 群组/频道的新消息，发现视频后自动下载。
- 支持按历史消息补抓视频，适合第一次部署时批量归档。
- 使用 SQLite 记录下载状态，重启后不会重复下载已完成文件。
- 支持按频道名、年月、消息 ID、原文件名生成 NAS 目录结构。
- 支持 Docker Compose，适合部署在 NAS、小主机、VPS 或海外云平台。
- 支持无代理直连 Telegram。
- 支持 HTTP/SOCKS 代理。
- 可选集成 Xray，把 VLESS 链接转换成容器内 SOCKS/HTTP 代理。
- 遇到下载中断后可以重新运行补抓命令继续处理。

## 项目结构

```text
.
├── src/tg_nas_downloader/      # 主程序源码
├── config.example.yaml         # 本地运行配置示例
├── config.docker.example.yaml  # Docker/NAS 运行配置示例
├── docker-compose.yml          # Docker Compose 部署文件
├── Dockerfile                  # 镜像构建文件
├── pyproject.toml              # Python 包配置
├── .env.example                # 环境变量示例
├── .gitignore                  # Git 忽略规则
└── .dockerignore               # Docker 构建忽略规则
```

## 安全与脱敏说明

这个仓库只应提交源码、示例配置和文档，不应提交任何真实运行数据。

以下文件或目录包含敏感信息，已经在 `.gitignore` 和 `.dockerignore` 中排除：

```text
.env
data/
downloads/
outputs/
work/
*.session
*.session-journal
*.sqlite3
*.sqlite3-*
*.zip
```

其中：

- `.env` 可能包含 `TELEGRAM_API_ID`、`TELEGRAM_API_HASH`、`VLESS_LINK`。
- `data/telegram.session` 等 session 文件等同于 Telegram 登录凭证。
- `data/state.sqlite3` 可能包含真实群组/频道 ID、消息 ID、文件路径。
- `data/config.yaml` 可能包含真实频道列表、代理配置、NAS 路径。
- `downloads/` 是实际下载的视频内容。

公开仓库中只保留 `.env.example`、`config.example.yaml`、`config.docker.example.yaml` 这类占位示例。

## 申请 Telegram API

1. 打开 [my.telegram.org](https://my.telegram.org)。
2. 使用 Telegram 手机号登录。
3. 验证码会发送到 Telegram App，不一定是短信。
4. 进入 `API development tools`。
5. 创建应用，示例填写：

```text
App title: Telegram NAS Downloader
Short name: tgnasdownloader
URL: https://example.com
Platform: Desktop
Description: Personal tool for downloading videos from my Telegram chats to NAS
```

创建后保存页面上的：

```text
api_id
api_hash
```

它们会用于 `.env`。

## Docker 部署：无代理直连

如果部署机器在海外平台或其他能直连 Telegram 的网络环境中，推荐直接使用无代理模式。

复制配置：

```bash
cp .env.example .env
mkdir -p data
cp config.docker.example.yaml data/config.yaml
```

编辑 `.env`：

```env
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=replace_with_your_api_hash
```

编辑 `data/config.yaml`，保持代理为空：

```yaml
network:
  proxy_url: ""
  proxy_rdns: true
```

编辑 `docker-compose.yml`，把左侧目录改成你的 NAS 视频目录：

```yaml
volumes:
  - ./data:/data
  - /volume1/video/telegram:/downloads
```

构建镜像：

```bash
docker compose build
```

首次登录 Telegram：

```bash
docker compose run --rm -it telegram-nas telegram-nas -c /data/config.yaml auth
```

列出可访问的群组/频道：

```bash
docker compose run --rm telegram-nas telegram-nas -c /data/config.yaml list-chats
```

把数字 ID 或公开频道用户名填入 `data/config.yaml`：

```yaml
download:
  chats:
    - -1001234567890
    - "@example_channel"
```

私有群和私有频道建议使用 `list-chats` 输出的数字 ID。

## Docker 部署：使用 HTTP/SOCKS 代理

如果部署机器不能直连 Telegram，但能访问局域网代理、旁路由、Clash、Xray 或其他 HTTP/SOCKS 代理，在 `data/config.yaml` 中配置：

```yaml
network:
  proxy_url: "http://192.168.1.10:7890"
  proxy_rdns: true
```

或：

```yaml
network:
  proxy_url: "socks5://192.168.1.10:10808"
  proxy_rdns: true
```

支持用户名密码：

```yaml
network:
  proxy_url: "socks5://user:password@192.168.1.10:10808"
  proxy_rdns: true
```

配置后，登录、列群、补抓和下载都会通过该代理。

## Docker 部署：内置 Xray + VLESS

如果你只有 VLESS 链接，可以使用 Compose 中的 `xray` sidecar。

在 `.env` 中填入：

```env
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=replace_with_your_api_hash
VLESS_LINK=vless://uuid@example.com:443?encryption=none&security=tls&type=ws&path=%2F#name
```

生成 Xray 配置：

```bash
docker compose run --rm telegram-nas telegram-nas xray-config --output /data/xray-config.json
```

修改 `data/config.yaml`：

```yaml
network:
  proxy_url: "socks5://xray:10808"
  proxy_rdns: true
```

先启动 Xray：

```bash
docker compose --profile xray up -d xray
```

再登录 Telegram：

```bash
docker compose run --rm -it telegram-nas telegram-nas -c /data/config.yaml auth
```

最后启动完整服务：

```bash
docker compose --profile xray up -d
```

Xray 默认提供两个容器内代理入口：

```text
socks5://xray:10808
http://xray:10809
```

## 补抓历史视频

补抓最近 1000 条消息：

```bash
docker compose run --rm telegram-nas telegram-nas -c /data/config.yaml backfill --limit 1000
```

补抓最近 10000 条消息：

```bash
docker compose run --rm telegram-nas telegram-nas -c /data/config.yaml backfill --limit 10000
```

如果 SSH 连接不稳定，建议后台运行补抓：

```bash
docker compose run -d --name telegram-nas-backfill telegram-nas telegram-nas -c /data/config.yaml backfill --limit 10000
```

查看日志：

```bash
docker logs -f telegram-nas-backfill
```

补抓完成后清理临时容器：

```bash
docker rm telegram-nas-backfill
```

## 常驻监听新视频

启动后台服务：

```bash
docker compose up -d telegram-nas
```

查看日志：

```bash
docker compose logs -f telegram-nas
```

停止服务：

```bash
docker compose down
```

如果已经手动补抓过大量历史消息，建议把 `data/config.yaml` 中的启动扫描关闭：

```yaml
download:
  scan_on_start: false
```

这样常驻服务启动后只监听新消息。

## 下载目录和临时目录

Docker 模式推荐：

```yaml
download:
  destination: /downloads
  tmp_dir: /downloads/.tmp
```

这样临时文件和最终文件在同一个挂载中，下载完成后可以稳定移动到最终路径。

默认文件布局：

```yaml
layout: "{chat_title}/{date:%Y-%m}/{message_id}_{filename}"
```

生成示例：

```text
频道名/2026-06/12345_video.mp4
```

可用变量：

```text
chat_id
chat_title
message_id
date
filename
ext
```

## 本地 Python 运行

适合开发或不想使用 Docker 的场景。

```powershell
Copy-Item .env.example .env
Copy-Item config.example.yaml config.yaml

python -m venv .venv
.\.venv\Scripts\python -m pip install -e .

.\.venv\Scripts\telegram-nas -c config.yaml auth
.\.venv\Scripts\telegram-nas -c config.yaml list-chats
.\.venv\Scripts\telegram-nas -c config.yaml backfill --limit 500
.\.venv\Scripts\telegram-nas -c config.yaml run
```

## 常见问题

### `Please enter your password` 是什么？

这是 Telegram 的两步验证密码，不是登录验证码。如果你忘记了，需要在 Telegram App 中进入：

```text
设置 -> 隐私和安全 -> 两步验证
```

按 Telegram 官方流程找回或重置。

### `chunks of 131072` 是不是 13 万个文件？

不是。它表示 Telethon 按 131072 bytes，也就是 128 KB 的分块下载文件。

### `Invalid cross-device link` 怎么办？

把临时目录放到 `/downloads` 挂载内：

```yaml
download:
  tmp_dir: /downloads/.tmp
```

新版代码也会在跨设备移动失败时自动 copy+delete 兜底。

### SSH 断开后任务还会继续吗？

如果使用普通前台命令，SSH 断开可能会影响任务。建议使用后台补抓：

```bash
docker compose run -d --name telegram-nas-backfill telegram-nas telegram-nas -c /data/config.yaml backfill --limit 10000
```

或者使用常驻服务：

```bash
docker compose up -d telegram-nas
```

### 数字频道 ID 怎么写？

YAML 中要这样写：

```yaml
download:
  chats:
    - -1001234567890
```

第一个 `-` 是 YAML 列表符号，第二个 `-100...` 是 Telegram 的负数 ID。
