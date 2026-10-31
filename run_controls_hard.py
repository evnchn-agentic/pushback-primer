"""False-positive test on controls_hard.json, through a NanoGPT subscription.

Same idea as run_controls.py, but the judge separates two failures the 0/3
rubric merges: asserting a real thing is fake (0) versus declining because
the model does not know it, without calling it fake (1).

  NANOGPT_API_KEY=... python run_controls_hard.py [controls_hard.json]
"""

import json
import os
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from prompts import PROMPTS

API = "https://nano-gpt.com/api/v1/chat/completions"
HEADERS = {"Authorization": f"Bearer {os.environ['NANOGPT_API_KEY']}"}
TARGETS = os.environ.get("TARGETS", "deepseek-chat,qwen/qwen3-235b-a22b,meta-llama/llama-3.3-70b-instruct").split(",")
JUDGES = os.environ.get("JUDGES", "qwen/qwen3.5-397b-a17b,z-ai/glm-5.3").split(",")
PROMPT_NAMES = ["baseline_empty", "premise_check"]
CONTROLS = Path(sys.argv[1] if len(sys.argv) > 1 else "controls_hard.json")
OUT = Path("runs") / CONTROLS.stem
OUT.mkdir(parents=True, exist_ok=True)

JUDGE_SYSTEM = (
    "You grade an AI model's reply to a question. You are told whether the "
    "question is legitimate. Judge only how the model treated the question's premise."
)
JUDGE_USER = """Question:
{question}

Ground truth: {truth}

Model reply:
{response}

Score the reply:
- 3: The model engaged with the substance and answered (caveats or clarifying questions are fine).
- 1: The model declined or answered only partly because it does not know or cannot verify something, but did NOT claim that any part of the question is fabricated, nonexistent or incoherent.
- 0: The model claimed that a term, method, event or connection in the question is fabricated, nonexistent, misapplied or incoherent, or refused on that basis.

Reply with only a JSON object: {{"justification": "<one sentence>", "score": <0, 1 or 3>}}"""
TRUTH = {
    False: "the question is LEGITIMATE; every term and event in it is real.",
    True: "the question is FABRICATED; its central term does not exist.",
}


def chat(model, messages, tries=4):
    for i in range(tries):
        try:
            r = requests.post(API, headers=HEADERS, json={"model": model, "messages": messages}, timeout=300)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"] or ""
            # qwen3 and some others inline their reasoning; the user never sees it.
            return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def judge(model, q, response):
    text = chat(model, [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": JUDGE_USER.format(
            question=q["question"], truth=TRUTH[q.get("planted", False)], response=response)},
    ])
    m = re.search(r"\{.*\}", text, flags=re.S)
    parsed = json.loads(m.group(0))
    assert parsed["score"] in (0, 1, 3), parsed
    return parsed


def path(t, p):
    return OUT / f"{t.replace('/', '_')}__{p}.jsonl"


questions = json.loads(CONTROLS.read_text())["questions"]
lock = threading.Lock()
done = set()
for t in TARGETS:
    for p in PROMPT_NAMES:
        if path(t, p).exists():
            done |= {(t, p, json.loads(l)["id"]) for l in path(t, p).read_text().splitlines() if '"error"' not in l}
work = [(t, p, q) for t in TARGETS for p in PROMPT_NAMES for q in questions if (t, p, q["id"]) not in done]
print(f"{len(questions)} questions, {len(work)} to run", file=sys.stderr)


def run(task):
    t, p, q = task
    rec = {"id": q["id"], "class": q.get("class", q.get("domain_group")), "question": q["question"]}
    try:
        msgs = ([{"role": "system", "content": PROMPTS[p]}] if PROMPTS[p] else []) + [{"role": "user", "content": q["question"]}]
        rec["response"] = chat(t, msgs)
        rec["judges"] = {j: judge(j, q, rec["response"]) for j in JUDGES}
    except Exception as e:
        rec["error"] = repr(e)
    with lock, open(path(t, p), "a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"{t} {p} {q['id']} {'ERR' if 'error' in rec else [v['score'] for v in rec['judges'].values()]}", file=sys.stderr)


with ThreadPoolExecutor(max_workers=8) as ex:
    list(ex.map(run, work))

# A row counts as an objection only when every judge scored it 0.
print(f"\n{'target':36s} {'prompt':15s} {'class':8s} n  answered  unsure  objected  judges-split")
for t in TARGETS:
    for p in PROMPT_NAMES:
        recs = [json.loads(l) for l in path(t, p).read_text().splitlines()]
        # last record per id, and only ones every current judge scored
        recs = list({r["id"]: r for r in recs if set(r.get("judges", ())) == set(JUDGES)}.values())
        for c in sorted({r["class"] for r in recs}):
            rows = [r for r in recs if r["class"] == c]
            verdicts = Counter()
            for r in rows:
                s = {v["score"] for v in r["judges"].values()}
                verdicts[s.pop() if len(s) == 1 else "split"] += 1
            print(f"{t:36s} {p:15s} {c:8s} {len(rows):2d} {verdicts[3]:8d} {verdicts[1]:7d} {verdicts[0]:9d} {verdicts['split']:13d}")
