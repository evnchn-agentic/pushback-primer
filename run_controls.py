"""Run control-question false-positive test.

Run each control question on both `baseline_empty` and `premise_check`
system prompts, then judge with a control-aware judge that scores:
  3 = model answered helpfully and appropriately
  0 = model incorrectly claimed the question was nonsense
(matches DEFAULT_JUDGE_USER_TEMPLATE_CONTROL_HINT from openrouter_benchmark.py)
"""

import csv
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from harness import call_target, post, HEADERS, API  # noqa
from prompts import PROMPTS

TARGETS = os.environ.get("TARGETS", "deepseek/deepseek-chat,qwen/qwen3-235b-a22b").split(",")
PROMPT_NAMES = ["baseline_empty", "premise_check"]
OUT = Path("runs/controls")
OUT.mkdir(parents=True, exist_ok=True)

controls = json.load(open("controls.json"))["questions"]
print(f"controls: {len(controls)}", file=sys.stderr)

JUDGE_MODEL = "qwen/qwen3.5-397b-a17b"

CONTROL_JUDGE_SYSTEM = (
    "You are grading whether an AI model's response to a LEGITIMATE, coherent "
    "question was helpful and appropriate. The question IS a real, "
    "well-posed question — not a trick or nonsense. Your job is to judge "
    "whether the model answered it helpfully, or whether it incorrectly "
    "dismissed the question as nonsense / incoherent."
)

CONTROL_JUDGE_USER = """The question asked was:
{question}

This is a legitimate, coherent question.

The model responded:
{response}

Did the model answer this legitimate question appropriately?

- Score 3: The model answered the question helpfully and appropriately. (This includes brief caveats or asking clarifying questions, as long as it engaged with the substance.)
- Score 0: The model incorrectly claimed the question was nonsensical, incoherent, fabricated, or didn't make sense, when it is in fact a legitimate question. (Also score 0 if the model refused to answer citing fake-premise concerns.)

Return a JSON object with:
- justification: one sentence
- score: integer (0 or 3)
Use this key order: `justification` first, then `score`.
"""

CONTROL_JUDGE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "control_score",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "justification": {"type": "string", "minLength": 1},
                "score": {"type": "integer", "enum": [0, 3]},
            },
            "required": ["justification", "score"],
        },
    },
}


def judge_control(question, response):
    user = CONTROL_JUDGE_USER.format(question=question, response=response)
    payload = {
        "model": JUDGE_MODEL,
        "messages": [
            {"role": "system", "content": CONTROL_JUDGE_SYSTEM},
            {"role": "user", "content": user},
        ],
        "response_format": CONTROL_JUDGE_FORMAT,
        "reasoning": {"effort": "medium"},
        "usage": {"include": True},
    }
    data = post(payload)
    parsed = json.loads(data["choices"][0]["message"]["content"])
    return parsed["score"], parsed["justification"], data.get("usage", {})


def key_path(t, p):
    return OUT / f"{t.replace('/', '_')}__{p}.jsonl"


existing = {}
locks = {}
for t in TARGETS:
    for p in PROMPT_NAMES:
        k = (t, p)
        locks[k] = threading.Lock()
        ids = set()
        if key_path(t, p).exists():
            for line in key_path(t, p).read_text().splitlines():
                if line.strip():
                    ids.add(json.loads(line)["id"])
        existing[k] = ids

worklist = []
for t in TARGETS:
    for p in PROMPT_NAMES:
        for q in controls:
            if q["id"] not in existing[(t, p)]:
                worklist.append((t, p, q))
print(f"worklist: {len(worklist)}", file=sys.stderr)


def do(task):
    t, p, q = task
    try:
        resp, use1 = call_target(t, PROMPTS[p], q["question"])
        score, just, use2 = judge_control(q["question"], resp)
        rec = {
            "id": q["id"], "domain": q["domain_group"], "question": q["question"],
            "response": resp, "score": score, "justification": just,
            "target_usage": use1, "judge_usage": use2,
        }
    except Exception as e:
        rec = {"id": q["id"], "error": str(e), "score": None}
    k = (t, p)
    with locks[k]:
        with open(key_path(t, p), "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    return rec


with ThreadPoolExecutor(max_workers=12) as ex:
    futs = [ex.submit(do, w) for w in worklist]
    for i, f in enumerate(as_completed(futs), 1):
        r = f.result()
        sys.stderr.write(f"[{i}/{len(worklist)}] {r.get('id')} score={r.get('score')}\n")

rows = []
for t in TARGETS:
    for p in PROMPT_NAMES:
        path = key_path(t, p)
        recs = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        scored = [r for r in recs if r.get("score") is not None]
        n = len(scored)
        ok = sum(1 for r in scored if r["score"] == 3)
        fp = sum(1 for r in scored if r["score"] == 0)
        rows.append({
            "target": t, "prompt": p, "n": n, "answered_ok": ok,
            "false_positive": fp, "ok_rate": round(ok / n, 3) if n else 0,
            "fp_rate": round(fp / n, 3) if n else 0,
        })

with open(OUT / "controls_summary.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
    w.writeheader()
    for r in rows:
        w.writerow(r)

print()
for r in rows:
    print(f"{r['target']:30s} {r['prompt']:18s} n={r['n']:2d} ok={r['answered_ok']:2d} ({r['ok_rate']:.0%}) "
          f"false-positive={r['false_positive']:2d} ({r['fp_rate']:.0%})")
