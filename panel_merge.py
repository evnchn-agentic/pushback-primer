"""Merge primary-judge (Qwen3.5-397B) scores + helper-judge (Claude sub-agent)
scores into a 2-judge panel-mean summary that mirrors the official
3-judge-mean leaderboard methodology (green = row mean >= 1.5, red = <=0.5)."""

import csv
import json
from pathlib import Path

scale = Path("runs/scale")
helper = Path("runs/scale/helper_scores")

pairs = [
    ("deepseek/deepseek-chat", "baseline_empty"),
    ("deepseek/deepseek-chat", "premise_check"),
    ("qwen/qwen3-235b-a22b", "baseline_empty"),
    ("qwen/qwen3-235b-a22b", "premise_check"),
]

def load_primary(target, prompt):
    path = scale / f"{target.replace('/', '_')}__{prompt}.jsonl"
    by_id = {}
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("score") is not None:
                by_id[r["id"]] = r
    return by_id

def load_helper(target, prompt):
    path = helper / f"{target.replace('/', '_')}__{prompt}.helper.jsonl"
    by_id = {}
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            by_id[r["id"]] = r
    return by_id

rows = []
for target, prompt in pairs:
    prim = load_primary(target, prompt)
    help = load_helper(target, prompt)
    common = sorted(set(prim) & set(help))
    n = len(common)
    panel_mean = 0.0
    buckets = {"green": 0, "amber": 0, "red": 0}
    agree = 0
    p_scores, h_scores = [], []
    for rid in common:
        p = prim[rid]["score"]
        h = help[rid]["score"]
        p_scores.append(p)
        h_scores.append(h)
        mean = (p + h) / 2
        panel_mean += mean
        if mean >= 1.5:
            buckets["green"] += 1
        elif mean <= 0.5:
            buckets["red"] += 1
        else:
            buckets["amber"] += 1
        if p == h:
            agree += 1
    rows.append({
        "target": target,
        "prompt": prompt,
        "n": n,
        "primary_green": sum(1 for s in p_scores if s == 2),
        "primary_avg": round(sum(p_scores) / n, 3),
        "helper_green": sum(1 for s in h_scores if s == 2),
        "helper_avg": round(sum(h_scores) / n, 3),
        "panel_avg": round(panel_mean / n, 3),
        "panel_green": buckets["green"],
        "panel_amber": buckets["amber"],
        "panel_red": buckets["red"],
        "panel_green_rate": round(buckets["green"] / n, 3),
        "panel_red_rate": round(buckets["red"] / n, 3),
        "agreement_rate": round(agree / n, 3),
    })

with open(scale / "panel_summary.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
    w.writeheader()
    for r in rows:
        w.writerow(r)

print(f"{'target':30s} {'prompt':18s} {'n':>3s} {'P-g':>4s} {'H-g':>4s} {'Panel-g%':>9s} {'Panel-avg':>10s} {'Agree':>6s}")
print("-" * 95)
for r in rows:
    print(f"{r['target']:30s} {r['prompt']:18s} {r['n']:3d} {r['primary_green']:4d} {r['helper_green']:4d} "
          f"{r['panel_green_rate']*100:8.1f}% {r['panel_avg']:10.3f} {r['agreement_rate']*100:5.1f}%")

# Comparison against leaderboard top-10
print("\nReference: canonical v2 leaderboard (3-judge mean panel)")
print(f"  rank 1 anthropic/claude-sonnet-4.6@high    avg=1.87  green=91%")
print(f"  rank 2 anthropic/claude-sonnet-4.6@none    avg=1.86  green=89%")
print(f"  rank 7 qwen/qwen3.5-397b-a17b@high         avg=1.70  green=78%  (== our primary judge)")
print(f"  rank 56 anthropic/claude-sonnet-4@none     avg=0.87  green=29%")
print(f"  rank 94 deepseek/deepseek-v3.2@high        avg=0.46  green=13%")
print(f"  rank 132 deepseek/deepseek-chat            avg=0.22  green= 4%  (our target #1, baseline)")
print(f"  rank 135 mistralai/mistral-large-2512      avg=0.17  green= 2%")
print(f"  rank 120 qwen/qwen3-235b-a22b@none         avg=0.31  green= 6%  (our target #2, baseline)")
