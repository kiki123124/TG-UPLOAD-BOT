from telethon import TelegramClient
import asyncio
import json
import os
import re

api_id = 28004986
api_hash = 'c8b6264a5d87e0e868268b17395b8b94'
channel = '@ZhiHuSaltPicks'
BATCH_SIZE = 100
SYNC_INTERVAL = 300  # 每5分钟自动同步一次

# 解析消息文本，提取标题和类型
msg_title_re = re.compile(r'^标题[:：]\s*(.+)', re.MULTILINE)
msg_type_re = re.compile(r'^类型[:：]\s*(.+)', re.MULTILINE)

def parse_title_and_category(text):
    title_match = msg_title_re.search(text)
    type_match = msg_type_re.search(text)
    title = title_match.group(1).strip() if title_match else None
    category = type_match.group(1).strip() if type_match else '未分类'
    category = category.replace('-', '_')
    return title, category

def load_titles():
    if os.path.exists('channel_titles.json'):
        with open('channel_titles.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

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
        last_id = batch[-1].id if batch[-1].id is not None else 0
        total += len(batch)
        print(f"已拉取 {total} 条消息，当前已解析 {len(titles)} 条")
        save_titles(titles)
        await asyncio.sleep(1)
    print(f'历史消息同步完成，共拉取到 {len(titles)} 条带标题的消息')
    return titles

async def main():
    client = TelegramClient('session_name', api_id, api_hash)
    await client.start()
    print('开始首次全量同步...')
    await fetch_all_titles(client, channel)

if __name__ == '__main__':
    asyncio.run(main()) 