import json

input_file = 'channel_titles.json'
output_file = 'clean_channel_titles.json'

with open(input_file, 'r', encoding='utf-8') as f:
    data = json.load(f)

# 只保留对象类型的 entry
cleaned = [entry for entry in data if isinstance(entry, dict)]

with open(output_file, 'w', encoding='utf-8') as f:
    json.dump(cleaned, f, ensure_ascii=False, indent=2)

print(f'清理完成，保留对象 {len(cleaned)} 条，结果已保存到 {output_file}') 