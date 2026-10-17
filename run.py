#!/usr/bin/env python3
"""ShotList60 生成器：把干货稿交给火山方舟上的 Seed 深度思考模型，改成 60 秒口播分镜，然后自动审计。

两条路都能走：
  有 key   SHOTLIST_API_KEY=... python run.py --source sources/coffee.md --out runs/demo
  没 key   python run.py --print-prompt --source sources/coffee.md        # 打印完整提示词，粘到方舟控制台或任意对话窗口
           python run.py --from-response reply.txt --source sources/coffee.md --out runs/demo   # 把模型回复存成文件再喂进来

环境变量（凭据只从环境变量读，不进代码、不进输出文件）
  SHOTLIST_API_KEY   方舟 API Key（也接受 ARK_API_KEY）
  SHOTLIST_BASE_URL  默认 https://ark.cn-beijing.volces.com/api/v3（任何 OpenAI 兼容接口都行）
  SHOTLIST_MODEL     默认 doubao-seed-1-6-250615，换成你开通的任意 Seed 深度思考模型 ID 即可
  SHOTLIST_THINKING  enabled（默认）/ disabled，控制深度思考开关

产物（写进 --out 目录）
  prompt_rendered.md  实际发出去的完整提示词
  raw_response.txt    模型原始回复
  shots.json          抽取出来的分镜 JSON
  audit.txt / audit.json  审计报告
  meta.json           模型、接口主机、token 用量（不含 key）
"""
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

import verify

HERE = Path(__file__).resolve().parent
DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_MODEL = "doubao-seed-1-6-250615"
DEFAULT_PROMPT = HERE / "prompts" / "v2.md"
REQUEST_TIMEOUT_SECONDS = 600


def render_prompt(prompt_path: Path, source_path: Path) -> str:
    template = prompt_path.read_text(encoding="utf-8")
    if "{{SOURCE}}" not in template:
        raise SystemExit(f"提示词模板 {prompt_path} 里没有 {{{{SOURCE}}}} 占位符")
    return template.replace("{{SOURCE}}", source_path.read_text(encoding="utf-8").strip())


def validate_base_url(url: str) -> str:
    """只放行 http/https，且目标主机不能是本机、环回、私有或保留地址。"""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SystemExit(f"接口地址只支持 http/https：{url}")
    host = parsed.hostname
    if not host:
        raise SystemExit(f"接口地址缺少主机名：{url}")
    lowered = host.lower()
    if lowered == "localhost" or lowered.endswith(".localhost") or lowered.endswith(".local"):
        raise SystemExit(f"拒绝请求本机地址：{host}")
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
        except socket.gaierror as exc:
            raise SystemExit(f"无法解析接口主机 {host}：{exc}")
        addresses = {ipaddress.ip_address(info[4][0]) for info in infos}
    for address in addresses:
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast or address.is_unspecified):
            raise SystemExit(f"拒绝请求私有/保留地址：{host} -> {address}")
    return url.rstrip("/")


def call_model(base_url: str, api_key: str, model: str, prompt: str, thinking: str) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    if thinking in ("enabled", "disabled"):
        payload["thinking"] = {"type": thinking}
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:600]
        raise SystemExit(f"接口返回 HTTP {exc.code}：{body}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"请求接口失败：{exc.reason}")


def write_outputs(out_dir: Path, *, prompt: str, raw: str, data: dict, source_path: Path,
                  result: dict, report: str, meta: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "prompt_rendered.md").write_text(prompt, encoding="utf-8")
    (out_dir / "raw_response.txt").write_text(raw, encoding="utf-8")
    (out_dir / "shots.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "audit.txt").write_text(report + "\n", encoding="utf-8")
    (out_dir / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="ShotList60 生成器")
    parser.add_argument("--source", default=str(HERE / "sources" / "coffee.md"), help="原稿文件")
    parser.add_argument("--prompt", default=str(DEFAULT_PROMPT), help="提示词模板（含 {{SOURCE}} 占位符）")
    parser.add_argument("--out", help="产物目录，例如 runs/2026-10-18-demo")
    parser.add_argument("--from-response", help="跳过调用，直接读取已有的模型回复文件")
    parser.add_argument("--print-prompt", action="store_true", help="只打印渲染后的提示词，不调用模型")
    parser.add_argument("--model", help="覆盖 SHOTLIST_MODEL；粘贴模式下只作为记录在 meta.json 里的标注")
    args = parser.parse_args(argv)

    source_path, prompt_path = Path(args.source), Path(args.prompt)
    prompt = render_prompt(prompt_path, source_path)
    if args.print_prompt:
        print(prompt)
        return 0
    if not args.out:
        parser.error("需要 --out 指定产物目录（或用 --print-prompt 只看提示词）")

    meta = {
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "prompt": str(prompt_path.relative_to(HERE)) if prompt_path.is_relative_to(HERE) else str(prompt_path),
        "source": str(source_path.relative_to(HERE)) if source_path.is_relative_to(HERE) else str(source_path),
    }

    if args.from_response:
        raw = Path(args.from_response).read_text(encoding="utf-8")
        meta.update({"mode": "from-response", "response_file": Path(args.from_response).name,
                     "model": args.model})
    else:
        api_key = os.environ.get("SHOTLIST_API_KEY") or os.environ.get("ARK_API_KEY")
        if not api_key:
            raise SystemExit("没有找到 SHOTLIST_API_KEY / ARK_API_KEY。没有 key 的话用 --print-prompt + --from-response 两步走。")
        base_url = validate_base_url(os.environ.get("SHOTLIST_BASE_URL", DEFAULT_BASE_URL))
        model = args.model or os.environ.get("SHOTLIST_MODEL", DEFAULT_MODEL)
        thinking = os.environ.get("SHOTLIST_THINKING", "enabled")
        print(f"-> 调用 {urllib.parse.urlparse(base_url).hostname} / {model}（thinking={thinking}）…")
        response = call_model(base_url, api_key, model, prompt, thinking)
        try:
            raw = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise SystemExit(f"接口回复里没有 choices[0].message.content：{json.dumps(response, ensure_ascii=False)[:600]}")
        meta.update({
            "mode": "api",
            "model": response.get("model", model),
            "base_host": urllib.parse.urlparse(base_url).hostname,
            "thinking": thinking,
            "usage": response.get("usage"),
        })

    try:
        data = verify.parse_response_text(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "raw_response.txt").write_text(raw, encoding="utf-8")
        print(f"[ERROR] 模型回复不是可解析的 JSON：{exc}；原始回复已存到 {out_dir / 'raw_response.txt'}")
        return 2

    source = source_path.read_text(encoding="utf-8")
    result = verify.audit(data, source)
    report = verify.render_report(result, data, source_path.name, source)
    write_outputs(Path(args.out), prompt=prompt, raw=raw, data=data, source_path=source_path,
                  result=result, report=report, meta=meta)
    print(report)
    print(f"\n产物已写入 {args.out}/")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
