# Call the streaming POST /run like the browser does and print events as they arrive.
# usage: test_stream.py <session_id> <debug_break 0|1> <circuit json> <text...>
# A message the agent treats as a question prints the chat reply; a build prints the full result.
import json, sys, time
import httpx

sid, brk, circuit, text = sys.argv[1], sys.argv[2] == "1", json.loads(sys.argv[3]), " ".join(sys.argv[4:])
t0 = time.time()
with httpx.stream("POST", "http://localhost:8000/run", timeout=180,
                  json={"session_id": sid, "text": text, "circuit": circuit, "debug_break": brk}) as r:
    if r.status_code != 200:
        print("HTTP", r.status_code, r.read().decode()); sys.exit(1)
    for line in r.iter_lines():
        if not line.startswith("data: "):
            continue
        e = json.loads(line[6:])
        if e["type"] == "step":
            print(f"  [{time.time() - t0:5.1f}s] {e['step']}: {e['note'][:150]}")
        elif e["type"] == "error":
            print("  ERROR:", e["message"])
        elif e.get("kind") == "chat":
            print(f"CHAT REPLY ({e['elapsed_ms'] / 1000:.1f}s, lines {e['lines'] or '-'}): {e['reply']}")
            print("  spoken:", e["spoken_summary"])
        else:
            x = e["explanation"]
            print(f"RESULT status={e['status']} attempts={e['attempts']} total={e['elapsed_ms']/1000:.1f}s llm={e['llm']}")
            print("  checks:", json.dumps(e["checks"]), "| stimulus:", json.dumps(e["stimulus"]))
            for v in x.get("verified", []):
                print(f"  {'OK  ' if v['ok'] else 'FAIL'} {v['detail']}")
            for f in x.get("fixes_made", []):
                print(f"  fix {f['attempt']} [{f['stage']}]: {f['change']}")
            print("  summary:", x.get("summary"))
            for row in x.get("lines", []):
                print(f"  {row['line']:3d} {row['code'][:50]:50s} {row['note']}")
            print("  try_next:", x.get("try_next"))
            print("  spoken:", e["spoken_summary"])
            if e["status"] != "passed":
                print("  problem:", (e["problem"] or "")[:400])
