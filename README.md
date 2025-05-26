# Telegram 频道书籍上传与同步 Bot

## 项目简介

本项目适用于需要批量上传电子书到 Telegram 频道、同步频道消息到本地的场景。

包含以下功能：
- 电子书批量上传 Bot（支持补发、指定起点上传等）
- 频道消息同步脚本（可通过 Flask API 触发）

## 功能说明

- 批量上传本地电子书到指定频道
- 检查并补发频道缺失书籍
- 同步频道消息到本地 JSON 文件
- 支持命令和按钮两种操作方式

## 快速上手

1. 安装依赖

   ```bash
   pip install -r requirements.txt
   ```

2. 配置环境变量（建议写入 .env 文件或在 shell 中导出）：

   ```bash
   export TELEGRAM_BOT_TOKEN=你的BotToken
   export TG_API_ID=你的api_id
   export TG_API_HASH=你的api_hash
   export TG_CHANNEL=目标频道用户名或ID（如 @your_channel 或 -100xxxxxx）
   ```

3. 启动 Flask 服务（用于同步频道消息）

   ```bash
   python3 fetch_channel_titles_server.py
   ```

4. 启动 Bot

   ```bash
   python3 epub_uploader_bot_fixed_with_retry_v6.py
   ```

## 目录说明

- `epub_uploader_bot_fixed_with_retry_v6.py`  主 bot 脚本
- `fetch_channel_titles.py`                   频道消息同步脚本
- `fetch_channel_titles_server.py`            Flask 服务
- `fix_category_in_channel_titles.py`         分类修正脚本
- `clean_channel_titles.py`                   清理脚本
- `requirements.txt`                          依赖
- `README.md`                                 项目说明
- `.gitignore`                                忽略文件 