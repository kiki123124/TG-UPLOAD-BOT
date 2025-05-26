import json

file = 'channel_titles.json'
with open(file, 'r', encoding='utf-8') as f:
    data = json.load(f)

for entry in data:
    if isinstance(entry, dict) and 'category' in entry:
        entry['category'] = entry['category'].lstrip('#')

with open(file, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print('已批量去除所有category字段的#前缀') 