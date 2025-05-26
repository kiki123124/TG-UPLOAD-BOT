from flask import Flask, jsonify
import threading
import asyncio
import os
import json
from telethon import TelegramClient
import re

app = Flask(__name__)

api_id = int(os.getenv('TG_API_ID'))
api_hash = os.getenv('TG_API_HASH')
channel = '@ZhiHuSaltPicks'
BATCH_SIZE = 100

msg_title_re = re.compile(r'^标题[:：]\s*(.+)', re.MULTILINE)
msg_type_re = re.compile(r'^类型[:：]\s*(.+)', re.MULTILINE)

def parse_title_and_category(text):
    title_match = msg_title_re.search(text)
    type_match = msg_type_re.search(text)
    title = title_match.group(1).strip() if title_match else None
    category = type_match.group(1).strip() if type_match else '未分类'
    category = category.replace('-', '_')
    return title, category

def save_titles(titles):
    with open('channel_titles.json', 'w', encoding='utf-8') as f:
        json.dump(titles, f, ensure_ascii=False, indent=2)

async def fetch_all_titles(client, channel):
    titles = []
    seen = set()
    last_id = None
    total = 0
    while True:
        batch = []
        kwargs = {'limit': BATCH_SIZE}
        if last_id is not None:
            kwargs['max_id'] = last_id
        async for message in client.iter_messages(channel, **kwargs):
            if message.text:
                title, category = parse_title_and_category(message.text)
                if title:
                    key = (title, category)
                    if key not in seen:
                        entry = {
                            'title': title,
                            'filename': title,
                            'category': category.lstrip('#')
                        }
                        titles.append(entry)
                        seen.add(key)
                batch.append(message)
        if not batch:
            break
        last_id = batch[-1].id
        total += len(batch)
        print(f"已拉取 {total} 条消息，当前已解析 {len(titles)} 条")
        save_titles(titles)
        await asyncio.sleep(1)
    print(f'历史消息同步完成，共拉取到 {len(titles)} 条带标题的消息')
    save_titles(titles)
    return titles

def sync_channel_titles():
    async def _sync():
        client = TelegramClient('session_name', api_id, api_hash)
        await client.start()
        await fetch_all_titles(client, channel)
    asyncio.run(_sync())

@app.route('/sync', methods=['POST'])
def sync():
    def run_sync():
        try:
            sync_channel_titles()
        except Exception as e:
            print(f"同步出错: {e}")
    t = threading.Thread(target=run_sync)
    t.start()
    return jsonify({'status': 'sync started'}), 200

if __name__ == '__main__':
    app.run(port=5000, debug=False) 