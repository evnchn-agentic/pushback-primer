"""Flat-parallel driver: flattens the (target × prompt × question) matrix
to a single work-list and executes it with one big thread pool. Resume-safe
(relies on harness.run_prompt's done-ids logic but bypassed here — we write
directly per row).

Result files still land at runs/<out>/<target>__<prompt>.jsonl so they can be
summarised the same way.
"""

import csv
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from harness import load_questions, stratified_sample, call_target, call_judge
from prompts import PROMPTS

TARGETS = [
    "deepseek/deepseek-chat",
    "mistralai/mistral-large-2512",
    "qwen/qwen3-235b-a22b",
]
PROMPT_ORDER = [
    "baseline_empty",
    "baseline_helpful",
    "skeptic_lite",
    "premise_check",
    "rigorous_expert",
    "few_shot_pushback",
    "adversarial_prior",
    "two_pass",
]

N = int(os.environ.get("N", "20"))
PAR = int(os.environ.get("PAR", "24"))
OUT = Path(os.environ.get("OUT", "runs/iter1"))
OUT.mkdir(parents=True, exist_ok=True)

qs = load_questions(Path("questions.v2.json"))
sample = stratified_sample(qs, N)
print(f"sample ids: {[q['id'] for q in sample]}", file=sys.stderr)

# load existing results for resume
existing: dict[tuple[str, str], set[str]] = {}
file_locks: dict[tuple[str, str], threading.Lock] = {}

def key_path(target, prompt):
    return OUT / f"{target.replace('/', '_')}__{prompt}.jsonl"

for t in TARGETS:
    for p in PROMPT_ORDER:
        k = (t, p)
        file_locks[k] = threading.Lock()
        path = key_path(t, p)
        ids: set[str] = set()
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    try:
                        ids.add(json.loads(line)["id"])
                    except Exception:
                        pass
        existing[k] = ids

worklist: list[tuple[str, str, dict]] = []
for t in TARGETS:
    for p in PROMPT_ORDER:
        done = existing[(t, p)]
        for q in sample:
            if q["id"] not in done:
                worklist.append((t, p, q))

print(f"worklist size: {len(worklist)} (par={PAR})", file=sys.stderr)

done_counter = [0]
done_lock = threading.Lock()

def do(task):
    target, prompt, q = task
    try:
        resp, use1 = call_target(target, PROMPTS[prompt], q["question"])
        score, just, use2 = call_judge(q["question"], resp)
        rec = {
            "id": q["id"], "domain": q["domain_group"], "technique": q["technique"],
            "response": resp, "score": score, "justification": just,
            "target_usage": use1, "judge_usage": use2, "error": None,
        }
    except Exception as e:
        rec = {"id": q["id"], "error": str(e), "score": None}

    k = (target, prompt)
    with file_locks[k]:
        with open(key_path(target, prompt), "a") as fh:
            fh.write(json.dumps(rec) + "\n")

    with done_lock:
        done_counter[0] += 1
        n = done_counter[0]
    if n % 20 == 0 or n == len(worklist):
        sys.stderr.write(f"[{n}/{len(worklist)}] {target} {prompt} {q['id']} score={rec.get('score')}\n")


with ThreadPoolExecutor(max_workers=PAR) as ex:
    futs = [ex.submit(do, t) for t in worklist]
    for f in as_completed(futs):
        f.result()

# summarise
rows = []
for t in TARGETS:
    for p in PROMPT_ORDER:
        path = key_path(t, p)
        if not path.exists():
            continue
        recs = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        scored = [r for r in recs if r.get("score") is not None]
        n = len(scored)
        g = sum(1 for r in scored if r["score"] == 2)
        a = sum(1 for r in scored if r["score"] == 1)
        rd = sum(1 for r in scored if r["score"] == 0)
        avg = sum(r["score"] for r in scored) / n if n else 0
        rows.append({
            "target": t, "prompt": p, "n": n, "green": g, "amber": a, "red": rd,
            "green_rate": g / n if n else 0, "red_rate": rd / n if n else 0,
            "avg_score": avg,
            "target_cost": sum(r.get("target_usage", {}).get("cost", 0) or 0 for r in scored),
            "judge_cost": sum(r.get("judge_usage", {}).get("cost", 0) or 0 for r in scored),
            "errors": len(recs) - n,
        })

with open(OUT / "summary.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else [])
    w.writeheader()
    for r in rows:
        w.writerow(r)
print(f"wrote {OUT/'summary.csv'}", file=sys.stderr)
for r in rows:
    print(f"{r['target']:32s} {r['prompt']:22s} n={r['n']:3d} green={r['green']:3d} ({r['green_rate']:.0%}) red={r['red']:3d} avg={r['avg_score']:.2f}", file=sys.stderr)
