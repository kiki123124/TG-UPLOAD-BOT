# Telegram 频道书籍上传与同步 Bot

## 项目简介

本项目包含：
- Telegram 书籍上传 Bot（支持批量上传、补发、指定起点上传等）
- 频道消息同步脚本（支持 Flask API 触发）

## 功能介绍

- 批量上传本地书籍到频道
- 检查并补发缺失书籍
- 同步频道消息到本地 JSON
- 支持命令和按钮两种操作方式

## 快速开始

1. 安装依赖

   ```bash
   pip install -r requirements.txt
   ```

2. 配置 Telegram Bot Token（在 `epub_uploader_bot_fixed_with_retry_v6.py` 里设置）

3. 启动 Flask 服务（用于同步）

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