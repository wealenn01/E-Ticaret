import json
import urllib.request

d = json.load(urllib.request.urlopen("http://127.0.0.1:8000/openapi.json", timeout=10))
assistant = sorted([p for p in d["paths"] if "assistant" in p])
print("assistant paths:", len(assistant))
for p in assistant:
    print(" ", p)
print("reports?", "/api/assistant/reports" in d["paths"])
