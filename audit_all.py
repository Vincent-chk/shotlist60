#!/usr/bin/env python3
"""复验 runs/ 下的所有记录：按每条记录 meta.json 里写的原稿，重新审计 shots.json。

不需要 API Key，CI 里跑的就是它。任何一条记录未通过就返回 1。
"""
import json
import sys
from pathlib import Path

import verify

HERE = Path(__file__).resolve().parent


def main() -> int:
    runs_dir = HERE / "runs"
    runs = sorted(p for p in runs_dir.iterdir() if (p / "meta.json").exists()) if runs_dir.exists() else []
    if not runs:
        print("runs/ 下没有可复验的记录")
        return 1
    failed = 0
    print(f"{'记录':<26} {'提示词':<14} 结果")
    for run in runs:
        meta = json.loads((run / "meta.json").read_text(encoding="utf-8"))
        source_path = Path(meta["source"])
        if not source_path.is_absolute():
            source_path = HERE / source_path
        source = source_path.read_text(encoding="utf-8")
        result = verify.audit(verify.load_shots(run / "shots.json"), source)
        verdict = "通过" if result["passed"] else "未通过"
        print(f"{run.name:<26} {meta.get('prompt', '?'):<14} {result['fails']} 失败 / {result['warns']} 警告 -> {verdict}")
        failed += 0 if result["passed"] else 1
    print(f"\n{len(runs)} 条记录，{failed} 条未通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
