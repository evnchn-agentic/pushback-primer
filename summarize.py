"""Read runs/iter1/*.jsonl and write summary.csv."""
import csv
import json
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/iter1")
rows = []
for path in sorted(root.glob("*.jsonl")):
    fname = path.name.removesuffix(".jsonl")
    target, prompt = fname.split("__", 1)
    target = target.replace("_", "/", 1)
    recs = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    scored = [r for r in recs if r.get("score") is not None]
    n = len(scored)
    g = sum(1 for r in scored if r["score"] == 2)
    a = sum(1 for r in scored if r["score"] == 1)
    rd = sum(1 for r in scored if r["score"] == 0)
    avg = sum(r["score"] for r in scored) / n if n else 0
    rows.append({
        "target": target, "prompt": prompt, "n": n, "green": g, "amber": a, "red": rd,
        "green_rate": round(g / n, 3) if n else 0, "red_rate": round(rd / n, 3) if n else 0,
        "avg_score": round(avg, 3),
        "target_cost": round(sum(r.get("target_usage", {}).get("cost", 0) or 0 for r in scored), 5),
        "judge_cost": round(sum(r.get("judge_usage", {}).get("cost", 0) or 0 for r in scored), 5),
        "errors": len(recs) - n,
    })

rows.sort(key=lambda r: (r["target"], -r["avg_score"]))
out = root / "summary.csv"
with open(out, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
    w.writeheader()
    for r in rows:
        w.writerow(r)
print(f"wrote {out}")
for r in rows:
    print(f"{r['target']:30s} {r['prompt']:22s} n={r['n']:3d} green={r['green']:3d} ({r['green_rate']:.0%}) amber={r['amber']:2d} red={r['red']:3d} avg={r['avg_score']:.2f} $tgt={r['target_cost']:.4f} $jdg={r['judge_cost']:.4f}")
print("\n--- total cost ---")
print(f"target ${sum(r['target_cost'] for r in rows):.4f} + judge ${sum(r['judge_cost'] for r in rows):.4f} = ${sum(r['target_cost']+r['judge_cost'] for r in rows):.4f}")
