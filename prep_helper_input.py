"""Prepare input JSONL files for the Claude sub-agent helper-judge."""
import json
from pathlib import Path

root = Path("runs/scale")
out_dir = Path("runs/scale/helper_input")
out_dir.mkdir(parents=True, exist_ok=True)

for src in sorted(root.glob("*.jsonl")):
    if src.parent.name != "scale":
        continue
    target_prompt = src.stem
    out = out_dir / f"{target_prompt}.judge.jsonl"
    rows = [json.loads(l) for l in src.read_text().splitlines() if l.strip()]
    scored = [r for r in rows if r.get("score") is not None]
    with open(out, "w") as fh:
        for r in scored:
            fh.write(json.dumps({
                "id": r["id"],
                "question": r.get("question", ""),
                "response": r["response"],
            }) + "\n")
    print(f"{out.name}: {len(scored)} rows")
