#!/usr/bin/env python3
"""ShotList60 审计器：对「干货稿 -> 60 秒口播分镜」的 JSON 结果做机器核对。

检查项
  schema      字段齐全、类型正确
  duration    镜头秒数合计 == total_seconds == 60，单镜秒数在合理区间
  speed       每镜语速（念出来的字数 / 秒）：理想 4-5 字/秒，超过 6.5 判失败；长数字按中文读法估音节
  hook        钩子 3 秒内念得完
  numbers     标题、钩子、口播里出现的每个阿拉伯数字都必须能在原稿找到（数字溯源）
  cn_numerals 出现中文数字时告警：机器溯源不了，请人工核对
  datapoints  至少保留原稿 3 个关键数据

用法
  python verify.py --source sources/coffee.md --shots runs/xxx/shots.json [--json runs/xxx/audit.json]
  --shots 也可以直接指向模型原始回复（带 ```json 围栏也行），会自动抽取 JSON。

退出码：0 通过；1 有失败项；2 输入无法解析。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

TARGET_SECONDS = 60
SPEED_IDEAL = (4.0, 5.0)
SPEED_WARN_LOW = 3.0
SPEED_WARN_HIGH = 5.5
SPEED_FAIL_HIGH = 6.5
SHOT_SECONDS_RANGE = (2, 15)
SHOT_COUNT_RANGE = (6, 20)
MIN_DATAPOINTS = 3
HOOK_WARN_CHARS = 20
TITLE_WARN_CHARS = 30

NUM_RE = re.compile(r"\d+(?:\.\d+)?")
SPEAKABLE_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")
LONG_DIGITS_RE = re.compile(r"\d{5,}")
LONG_DIGITS_EXTRA_PER_DIGIT = 0.8
CN_NUMERAL_RE = re.compile(r"[零〇一二两三四五六七八九十百千万亿]{2,}")
FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*$", re.MULTILINE)

LEVEL_ORDER = {"PASS": 0, "WARN": 1, "FAIL": 2}


class Finding(dict):
    """一条审计结论：check / level / message / details。"""

    def __init__(self, check: str, level: str, message: str, details: Optional[dict] = None):
        super().__init__(check=check, level=level, message=message, details=details or {})


# ---------- 基础工具 ----------

def speakable_count(text: str) -> int:
    """估算念出来有多少个字：汉字、字母、数字各算 1，标点和空白不算。

    5 位以上的长数字按中文读法估：2147483647 念出来是「二十一亿四千七百四十八万三千六百四十七」，
    18 个字而不是 10 个，所以每位额外加 0.8。年份、三位数这类 4 位以内的数字仍按 1 字/位。
    """
    text = text or ""
    extra = sum(int(round(len(m.group()) * LONG_DIGITS_EXTRA_PER_DIGIT)) for m in LONG_DIGITS_RE.finditer(text))
    return len(SPEAKABLE_RE.findall(text)) + extra


def normalize_number(token: str) -> str:
    """把 "5.0" / "05" / "1.40" 统一成 "5" / "5" / "1.4"，避免写法差异造成误判。"""
    try:
        value = float(token)
    except ValueError:
        return token
    if value == int(value):
        return str(int(value))
    return repr(value)


def extract_numbers(text: str) -> List[str]:
    return [normalize_number(t) for t in NUM_RE.findall(text or "")]


def parse_response_text(text: str) -> dict:
    """从模型原始回复里抽出 JSON：去掉 ``` 围栏，取第一个 { 到最后一个 } 之间的内容。"""
    stripped = FENCE_RE.sub("", text).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("回复里找不到 JSON 对象")
    return json.loads(stripped[start:end + 1])


def load_shots(path: Path) -> dict:
    return parse_response_text(path.read_text(encoding="utf-8"))


# ---------- 各项检查 ----------

def check_schema(data: dict) -> List[Finding]:
    findings: List[Finding] = []
    required: Dict[str, tuple] = {
        "title": (str,), "hook": (str,), "shots": (list,),
        "total_seconds": (int, float), "bgm": (str,), "cta": (str,),
    }
    for key, types in required.items():
        if key not in data:
            findings.append(Finding("schema", "FAIL", f"缺少字段 {key}"))
            continue
        value = data[key]
        if isinstance(value, bool) or not isinstance(value, types):
            findings.append(Finding("schema", "FAIL", f"字段 {key} 类型不对（得到 {type(value).__name__}）"))
            continue
        if isinstance(value, str) and not value.strip():
            findings.append(Finding("schema", "FAIL", f"字段 {key} 是空的"))

    shots = data.get("shots") if isinstance(data.get("shots"), list) else []
    for idx, shot in enumerate(shots, 1):
        if not isinstance(shot, dict):
            findings.append(Finding("schema", "FAIL", f"第 {idx} 个镜头不是对象"))
            continue
        for key in ("no", "visual", "narration", "seconds"):
            if key not in shot:
                findings.append(Finding("schema", "FAIL", f"第 {idx} 个镜头缺少字段 {key}"))
        seconds = shot.get("seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            findings.append(Finding("schema", "FAIL", f"第 {idx} 个镜头的 seconds 不是数字"))
        if not str(shot.get("narration", "")).strip():
            findings.append(Finding("schema", "FAIL", f"第 {idx} 个镜头的口播台词是空的"))

    if shots and not (SHOT_COUNT_RANGE[0] <= len(shots) <= SHOT_COUNT_RANGE[1]):
        findings.append(Finding("schema", "WARN",
                                f"镜头数 {len(shots)} 不在常见区间 {SHOT_COUNT_RANGE[0]}-{SHOT_COUNT_RANGE[1]}"))
    title = data.get("title", "")
    if isinstance(title, str) and speakable_count(title) > TITLE_WARN_CHARS:
        findings.append(Finding("schema", "WARN", f"标题 {speakable_count(title)} 字，偏长（建议不超过 {TITLE_WARN_CHARS}）"))

    if not any(f["level"] == "FAIL" for f in findings):
        findings.insert(0, Finding("schema", "PASS", f"字段齐全，{len(shots)} 个镜头"))
    return findings


def _valid_shots(data: dict) -> List[dict]:
    shots = data.get("shots") if isinstance(data.get("shots"), list) else []
    return [s for s in shots if isinstance(s, dict)
            and isinstance(s.get("seconds"), (int, float)) and not isinstance(s.get("seconds"), bool)]


def check_duration(data: dict) -> List[Finding]:
    shots = _valid_shots(data)
    if not shots:
        return [Finding("duration", "FAIL", "没有可用的镜头，无法核对时长")]
    total = sum(s["seconds"] for s in shots)
    declared = data.get("total_seconds")
    findings: List[Finding] = []
    for s in shots:
        if not (SHOT_SECONDS_RANGE[0] <= s["seconds"] <= SHOT_SECONDS_RANGE[1]):
            findings.append(Finding("duration", "FAIL",
                                    f"镜头 {s.get('no')} 时长 {s['seconds']} 秒，超出单镜合理区间 "
                                    f"{SHOT_SECONDS_RANGE[0]}-{SHOT_SECONDS_RANGE[1]} 秒"))
    if abs(total - TARGET_SECONDS) > 1e-9:
        findings.append(Finding("duration", "FAIL", f"镜头秒数合计 {total:g}，不等于 {TARGET_SECONDS}",
                                {"sum": total}))
    if declared != TARGET_SECONDS:
        findings.append(Finding("duration", "FAIL", f"total_seconds 写的是 {declared!r}，应为 {TARGET_SECONDS}"))
    if not findings:
        findings.append(Finding("duration", "PASS", f"秒数合计 {total:g} = total_seconds {declared}，单镜均在区间内",
                                {"sum": total}))
    return findings


def shot_speed_rows(data: dict) -> List[dict]:
    rows = []
    for s in _valid_shots(data):
        chars = speakable_count(str(s.get("narration", "")))
        seconds = s["seconds"]
        cps = chars / seconds if seconds else float("inf")
        if cps > SPEED_FAIL_HIGH:
            level = "FAIL"
        elif cps > SPEED_WARN_HIGH or cps < SPEED_WARN_LOW:
            level = "WARN"
        else:
            level = "PASS"
        rows.append({"no": s.get("no"), "seconds": seconds, "chars": chars,
                     "cps": round(cps, 2), "level": level})
    return rows


def check_speed(data: dict) -> List[Finding]:
    rows = shot_speed_rows(data)
    if not rows:
        return [Finding("speed", "FAIL", "没有可用的镜头，无法核对语速")]
    fails = [r for r in rows if r["level"] == "FAIL"]
    warns = [r for r in rows if r["level"] == "WARN"]
    total_chars = sum(r["chars"] for r in rows)
    total_seconds = sum(r["seconds"] for r in rows)
    avg = total_chars / total_seconds if total_seconds else 0
    summary = f"全片 {total_chars} 字 / {total_seconds:g} 秒，平均 {avg:.1f} 字/秒（理想 {SPEED_IDEAL[0]:g}-{SPEED_IDEAL[1]:g}）"
    details = {"rows": rows, "avg_cps": round(avg, 2)}
    if fails:
        worst = ", ".join(f"镜头 {r['no']} {r['cps']} 字/秒" for r in fails)
        return [Finding("speed", "FAIL", f"{len(fails)} 镜念不完（超过 {SPEED_FAIL_HIGH} 字/秒）：{worst}；{summary}", details)]
    if warns:
        which = ", ".join(f"镜头 {r['no']} {r['cps']} 字/秒" for r in warns)
        return [Finding("speed", "WARN", f"{len(warns)} 镜偏离理想语速：{which}；{summary}", details)]
    return [Finding("speed", "PASS", summary, details)]


def spoken_fields(data: dict) -> List[Tuple[str, str]]:
    """需要做数字溯源的字段：标题、钩子、每镜口播。画面描述不念出声，不查。"""
    fields = [("title", str(data.get("title", ""))), ("hook", str(data.get("hook", "")))]
    for s in data.get("shots") or []:
        if isinstance(s, dict):
            fields.append((f"镜头 {s.get('no')}", str(s.get("narration", ""))))
    return fields


def check_numbers(data: dict, source: str) -> Tuple[List[Finding], List[str], List[dict]]:
    source_numbers = set(extract_numbers(source))
    matched: List[str] = []
    missing: List[dict] = []
    seen = set()
    for field, text in spoken_fields(data):
        for raw in NUM_RE.findall(text):
            norm = normalize_number(raw)
            if norm in source_numbers:
                if norm not in seen:
                    seen.add(norm)
                    matched.append(norm)
            else:
                pos = text.find(raw)
                snippet = text[max(0, pos - 8): pos + len(raw) + 8]
                missing.append({"field": field, "number": raw, "snippet": snippet})
    total = len(matched) + len(missing)
    details = {"matched": matched, "missing": missing, "source_numbers": sorted(source_numbers)}
    if missing:
        shown = "；".join(f"{m['field']}「…{m['snippet']}…」里的 {m['number']}" for m in missing[:5])
        more = f"（另有 {len(missing) - 5} 处）" if len(missing) > 5 else ""
        return ([Finding("numbers", "FAIL", f"{len(missing)} 处数字在原稿找不到：{shown}{more}", details)],
                matched, missing)
    return ([Finding("numbers", "PASS", f"口播出现 {total} 处数字，全部能在原稿找到（去重后 {len(matched)} 个）", details)],
            matched, missing)


def check_datapoints(matched: List[str]) -> List[Finding]:
    if len(matched) >= MIN_DATAPOINTS:
        return [Finding("datapoints", "PASS", f"保留原稿数据 {len(matched)} 个：{'、'.join(matched)}",
                        {"matched": matched})]
    return [Finding("datapoints", "FAIL",
                    f"只保留了 {len(matched)} 个原稿数据（要求至少 {MIN_DATAPOINTS} 个）：{'、'.join(matched) or '无'}",
                    {"matched": matched})]


def check_hook(data: dict) -> List[Finding]:
    hook = str(data.get("hook", ""))
    n = speakable_count(hook)
    if n > HOOK_WARN_CHARS:
        return [Finding("hook", "WARN", f"钩子 {n} 字，3 秒内念不完（建议不超过 {HOOK_WARN_CHARS} 字）")]
    return [Finding("hook", "PASS", f"钩子 {n} 字，3 秒内念得完")]


def check_cn_numerals(data: dict) -> List[Finding]:
    """中文数字（两千年、一九零零年）机器溯源不了，列出来提醒人工核对。"""
    hits = []
    for field, text in spoken_fields(data):
        for m in CN_NUMERAL_RE.finditer(text):
            hits.append({"field": field, "text": m.group()})
    if hits:
        shown = "、".join(f"{h['field']}「{h['text']}」" for h in hits[:6])
        more = f"（另有 {len(hits) - 6} 处）" if len(hits) > 6 else ""
        return [Finding("cn_numerals", "WARN",
                        f"{len(hits)} 处中文数字无法机器溯源，请人工核对：{shown}{more}", {"hits": hits})]
    return [Finding("cn_numerals", "PASS", "没有中文数字，数字溯源覆盖完整")]


# ---------- 汇总 ----------

def audit(data: dict, source: str) -> dict:
    findings: List[Finding] = []
    findings += check_schema(data)
    findings += check_duration(data)
    findings += check_speed(data)
    findings += check_hook(data)
    number_findings, matched, _missing = check_numbers(data, source)
    findings += number_findings
    findings += check_cn_numerals(data)
    findings += check_datapoints(matched)
    worst = max((LEVEL_ORDER[f["level"]] for f in findings), default=0)
    fails = sum(1 for f in findings if f["level"] == "FAIL")
    warns = sum(1 for f in findings if f["level"] == "WARN")
    return {
        "passed": worst < LEVEL_ORDER["FAIL"],
        "fails": fails,
        "warns": warns,
        "findings": [dict(f) for f in findings],
        "speed_rows": shot_speed_rows(data),
    }


CHECK_LABEL = {
    "schema": "结构", "duration": "时长", "speed": "语速", "hook": "钩子",
    "numbers": "数字溯源", "cn_numerals": "中文数字", "datapoints": "数据点",
}


def render_report(result: dict, data: dict, source_name: str, source: str) -> str:
    shots = data.get("shots") if isinstance(data.get("shots"), list) else []
    lines = ["== ShotList60 审计报告 ==",
             f"原稿：{source_name}（{speakable_count(source)} 可念字）  分镜：{len(shots)} 镜  "
             f"声明时长：{data.get('total_seconds')!r} 秒", ""]
    for f in result["findings"]:
        lines.append(f"[{f['level']}] {CHECK_LABEL.get(f['check'], f['check']):<4} {f['message']}")
    rows = result["speed_rows"]
    if rows:
        lines += ["", "镜头  秒  字数  字/秒  判定"]
        for r in rows:
            lines.append(f"{str(r['no']):>4}  {r['seconds']:>2g}  {r['chars']:>4}  {r['cps']:>5.2f}  {r['level']}")
    verdict = "通过" if result["passed"] else "未通过"
    lines += ["", f"结论：{result['fails']} 项失败 / {result['warns']} 项警告 -> {verdict}"]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="ShotList60 审计器")
    parser.add_argument("--source", required=True, help="原稿文件（txt/md）")
    parser.add_argument("--shots", required=True, help="分镜 JSON，或模型原始回复文件")
    parser.add_argument("--json", help="把审计结果另存为 JSON")
    parser.add_argument("--quiet", action="store_true", help="只输出结论行")
    args = parser.parse_args(argv)

    source_path, shots_path = Path(args.source), Path(args.shots)
    source = source_path.read_text(encoding="utf-8")
    try:
        data = load_shots(shots_path)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"[ERROR] 无法从 {shots_path} 解析出 JSON：{exc}")
        return 2

    result = audit(data, source)
    report = render_report(result, data, source_path.name, source)
    if args.quiet:
        print(report.splitlines()[-1])
    else:
        print(report)
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
