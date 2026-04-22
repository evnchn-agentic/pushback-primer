"""Scale run, flat-parallel: full 100-Q set × 2 prompts × 2 targets."""
import csv
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from harness import load_questions, call_target, call_judge
from prompts import PROMPTS

TARGETS = os.environ.get("TARGETS", "deepseek/deepseek-chat,qwen/qwen3-235b-a22b").split(",")
PROMPT_NAMES = os.environ.get("PROMPTS", "baseline_empty,premise_check").split(",")
PAR = int(os.environ.get("PAR", "24"))
OUT = Path(os.environ.get("OUT", "runs/scale"))
OUT.mkdir(parents=True, exist_ok=True)

qs = load_questions(Path("questions.v2.json"))
print(f"targets={TARGETS} prompts={PROMPT_NAMES} n={len(qs)} par={PAR}", file=sys.stderr)

file_locks = {}
existing = {}

def key_path(t, p):
    return OUT / f"{t.replace('/', '_')}__{p}.jsonl"

for t in TARGETS:
    for p in PROMPT_NAMES:
        k = (t, p)
        file_locks[k] = threading.Lock()
        path = key_path(t, p)
        ids = set()
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    try:
                        ids.add(json.loads(line)["id"])
                    except Exception:
                        pass
        existing[k] = ids

worklist = []
for t in TARGETS:
    for p in PROMPT_NAMES:
        for q in qs:
            if q["id"] not in existing[(t, p)]:
                worklist.append((t, p, q))
print(f"worklist: {len(worklist)}", file=sys.stderr)

cnt = [0]
cnt_lock = threading.Lock()

def do(task):
    t, p, q = task
    try:
        resp, use1 = call_target(t, PROMPTS[p], q["question"])
        score, just, use2 = call_judge(q["question"], resp)
        rec = {
            "id": q["id"], "domain": q["domain_group"], "technique": q["technique"],
            "question": q["question"],
            "response": resp, "score": score, "justification": just,
            "target_usage": use1, "judge_usage": use2,
        }
    except Exception as e:
        rec = {"id": q["id"], "error": str(e), "score": None}
    k = (t, p)
    with file_locks[k]:
        with open(key_path(t, p), "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    with cnt_lock:
        cnt[0] += 1
        n = cnt[0]
    if n % 25 == 0 or n == len(worklist):
        sys.stderr.write(f"[{n}/{len(worklist)}] {t} {p} {q['id']} score={rec.get('score')}\n")


with ThreadPoolExecutor(max_workers=PAR) as ex:
    futs = [ex.submit(do, task) for task in worklist]
    for f in as_completed(futs):
        try:
            f.result()
        except Exception as e:
            sys.stderr.write(f"future error: {e}\n")
print("done", file=sys.stderr)
