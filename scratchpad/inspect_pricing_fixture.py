import json


path = r"D:\AI\Panse-System\backend\tests\fixtures\pricing_dump_20260717.txt"
for line in open(path, encoding="utf-8", errors="replace"):
    if '"rows"' not in line:
        continue
    payload = json.loads(line[line.find("{") :])
    rows = [row for row in payload["rows"] if str(row.get("item_id")) == "1036273574687"]
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    break
