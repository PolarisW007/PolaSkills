#!/usr/bin/env python3
"""Validate project research reports and an optional cross-project catalog."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import unquote


REQUIRED_HEADINGS: tuple[tuple[str, str], ...] = (
    ("文档信息与证据口径", r"文档信息|证据口径"),
    ("项目概览", r"项目概览"),
    ("客户、问题与场景", r"客户.*场景|客户、问题与场景|目标客户"),
    ("功能体系", r"功能体系|项目功能|核心功能"),
    ("实际场景案例", r"实际场景案例|场景案例"),
    ("核心技术实现", r"核心技术实现|实现架构|技术架构"),
    ("第三方能力", r"第三方能力|外部依赖"),
    ("边界与限制", r"边界与限制|限制与边界"),
    ("使用、部署与运维", r"使用.*部署.*运维|部署.*运维|使用与运维"),
    ("评价与选型", r"评价与选型|项目评价|采用建议"),
    ("证据与参考", r"证据与参考|参考资料"),
    ("最终结论", r"最终结论|结论摘要"),
)

CASE_FIELDS: tuple[tuple[str, str], ...] = (
    ("案例类型", r"案例类型"),
    ("背景与用户", r"背景与用户|背景.*角色"),
    ("前置条件", r"前置条件"),
    ("真实输入", r"真实输入|输入数据"),
    ("操作步骤", r"操作步骤|执行步骤"),
    ("系统内部处理", r"系统内部处理|内部处理"),
    ("结果与价值", r"结果与价值|输出.*价值"),
    ("异常、边界与恢复", r"异常.*边界|异常.*恢复|边界.*恢复"),
    ("验证证据", r"验证证据|推演依据"),
)

PLACEHOLDER_RE = re.compile(
    r"\{\{[^}]+\}\}|^\s*(?:TODO|TBD|FIXME)(?:\s|:|：|$)|"
    r"^\s*(?:待填写|待补充)(?:\s*[:：]|\s*$)",
    re.IGNORECASE | re.MULTILINE,
)
PERSONAL_PATH_RE = re.compile(
    r"(?:/Users/[^/\s]+/(?:Desktop|Documents|Downloads)/|"
    r"[A-Za-z]:\\Users\\[^\\\s]+\\(?:Desktop|Documents|Downloads)\\)"
)
SECRET_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?:api[_-]?key|token|password|client[_-]?secret)\s*[:=]\s*"
    r"[\"'][A-Za-z0-9_./+=-]{16,}[\"']",
    re.IGNORECASE,
)
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
HEADING_RE = re.compile(r"(?m)^(#{1,6})\s+(.+?)\s*$")
CASE_HEADING_RE = re.compile(
    r"(?m)^(#{2,6})\s+.*案例(?:一|二|三|四|五|[0-9]+)[：:\s].*$"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate evidence-based Chinese project research reports."
    )
    parser.add_argument(
        "--report",
        action="append",
        type=Path,
        default=[],
        help="Report path. Repeat for multiple reports.",
    )
    parser.add_argument("--catalog", type=Path, help="Optional catalog path.")
    parser.add_argument("--json", type=Path, help="Optional machine-readable result path.")
    parser.add_argument(
        "--min-chars",
        type=int,
        default=1800,
        help="Minimum non-whitespace characters per report (default: 1800).",
    )
    return parser.parse_args()


def heading_exists(text: str, pattern: str) -> bool:
    return bool(re.search(rf"(?im)^#{{1,6}}\s+.*(?:{pattern}).*$", text))


def local_link_failures(path: Path, text: str) -> list[str]:
    failures: list[str] = []
    for raw_target in LINK_RE.findall(text):
        target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
        if (
            not target
            or target.startswith(("#", "http://", "https://", "mailto:", "data:"))
        ):
            continue
        target = unquote(target.split("#", 1)[0].split("?", 1)[0])
        if not target:
            continue
        resolved = (
            Path(target).expanduser()
            if Path(target).is_absolute()
            else (path.parent / target)
        ).resolve()
        if not resolved.exists():
            failures.append(f"本地链接不存在：{raw_target}")
    return failures


def case_blocks(text: str) -> list[str]:
    matches = list(CASE_HEADING_RE.finditer(text))
    headings = list(HEADING_RE.finditer(text))
    blocks: list[str] = []
    for match in matches:
        level = len(match.group(1))
        end = len(text)
        for heading in headings:
            if heading.start() <= match.start():
                continue
            if len(heading.group(1)) <= level:
                end = heading.start()
                break
        blocks.append(text[match.start() : end])
    return blocks


def validate_report(path: Path, min_chars: int) -> dict[str, object]:
    failures: list[str] = []
    warnings: list[str] = []
    if not path.is_file():
        return {
            "path": str(path),
            "status": "fail",
            "failures": ["报告文件不存在"],
            "warnings": [],
        }

    text = path.read_text(encoding="utf-8")
    compact_length = len(re.sub(r"\s+", "", text))
    if compact_length < min_chars:
        failures.append(f"正文过短：{compact_length} 字符，小于下限 {min_chars}")

    for label, pattern in REQUIRED_HEADINGS:
        if not heading_exists(text, pattern):
            failures.append(f"缺少必需章节：{label}")

    for label in ("事实", "推断", "建议", "待确认"):
        if label not in text:
            failures.append(f"缺少证据标签：{label}")

    blocks = case_blocks(text)
    if len(blocks) < 2:
        failures.append(f"详细案例不足：发现 {len(blocks)} 个，至少需要 2 个")
    for index, block in enumerate(blocks[:2], start=1):
        for label, pattern in CASE_FIELDS:
            if not re.search(pattern, block, re.IGNORECASE):
                failures.append(f"案例 {index} 缺少字段：{label}")

    if PLACEHOLDER_RE.search(text):
        failures.append("存在模板占位符或 TODO/TBD")
    if PERSONAL_PATH_RE.search(text):
        failures.append("存在个人桌面、文档或下载目录绝对路径")
    if SECRET_RE.search(text):
        failures.append("存在疑似明文密钥或私钥")
    if len(re.findall(r"(?m)^```", text)) % 2:
        failures.append("Markdown 代码围栏不平衡")
    failures.extend(local_link_failures(path, text))

    if not re.search(r"(?m)^```mermaid\s*$|(?:→|->).*(?:→|->)", text):
        warnings.append("未发现架构/执行链流程表达，请人工确认技术关系是否清楚")
    if len(re.findall(r"`[^`\n]+`|https?://", text)) < 3:
        warnings.append("可定位的文件、标识符或来源较少，请人工抽查证据深度")

    return {
        "path": str(path.resolve()),
        "status": "pass" if not failures else "fail",
        "character_count": compact_length,
        "case_count": len(blocks),
        "failures": failures,
        "warnings": warnings,
    }


def resolved_local_links(path: Path, text: str) -> set[Path]:
    resolved: set[Path] = set()
    for raw_target in LINK_RE.findall(text):
        target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
        if (
            not target
            or target.startswith(("#", "http://", "https://", "mailto:", "data:"))
        ):
            continue
        target = unquote(target.split("#", 1)[0].split("?", 1)[0])
        candidate = (
            Path(target).expanduser()
            if Path(target).is_absolute()
            else path.parent / target
        )
        resolved.add(candidate.resolve())
    return resolved


def validate_catalog(path: Path, report_paths: list[Path]) -> dict[str, object]:
    failures: list[str] = []
    if not path.is_file():
        return {
            "path": str(path),
            "status": "fail",
            "failures": ["总目录文件不存在"],
            "warnings": [],
        }
    text = path.read_text(encoding="utf-8")
    required = (
        ("研究对象数量", r"研究对象[：:\s]+\d+\s*个"),
        ("主报告数量", r"主报告[：:\s]+\d+\s*份"),
        ("一屏总览", r"一屏总览|项目总览"),
        ("快速选择", r"快速选择|按需求"),
        ("跨项目结论", r"跨项目.*结论|共性结论"),
        ("研究边界", r"研究边界"),
    )
    for label, pattern in required:
        if not re.search(pattern, text):
            failures.append(f"缺少总目录内容：{label}")
    if PLACEHOLDER_RE.search(text):
        failures.append("总目录存在模板占位符或 TODO/TBD")
    if PERSONAL_PATH_RE.search(text):
        failures.append("总目录存在个人绝对路径")
    failures.extend(local_link_failures(path, text))

    links = resolved_local_links(path, text)
    for report in report_paths:
        if report.resolve() not in links:
            failures.append(f"总目录未链接主报告：{report}")
    return {
        "path": str(path.resolve()),
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "warnings": [],
    }


def main() -> int:
    args = parse_args()
    if not args.report:
        print("FAIL: 至少需要一个 --report")
        return 2
    if args.min_chars < 500:
        print("FAIL: --min-chars 不能小于 500")
        return 2

    report_results = [
        validate_report(path.expanduser().resolve(), args.min_chars)
        for path in args.report
    ]
    catalog_result = (
        validate_catalog(
            args.catalog.expanduser().resolve(),
            [path.expanduser().resolve() for path in args.report],
        )
        if args.catalog
        else None
    )
    all_results = report_results + ([catalog_result] if catalog_result else [])
    failures = [
        f"{item['path']}: {failure}"
        for item in all_results
        for failure in item["failures"]
    ]
    warnings = [
        f"{item['path']}: {warning}"
        for item in all_results
        for warning in item["warnings"]
    ]
    result = {
        "schema_version": "1.0",
        "status": "pass" if not failures else "fail",
        "report_count": len(report_results),
        "catalog_checked": catalog_result is not None,
        "failures": failures,
        "warnings": warnings,
        "results": all_results,
    }

    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.json:
        json_path = args.json.expanduser().resolve()
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
