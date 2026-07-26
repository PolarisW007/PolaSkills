#!/usr/bin/env python3
"""Self-contained deterministic harness for pola-project-research-report."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
DISCOVER = SKILL_DIR / "scripts" / "discover_projects.py"
VALIDATE = SKILL_DIR / "scripts" / "validate_research_outputs.py"


class HarnessFailure(RuntimeError):
    """Raised when a harness assertion fails."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise HarnessFailure(message)
    print(f"PASS: {message}")


def run(argv: list[str], expected: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != expected:
        raise HarnessFailure(
            f"command exit {result.returncode}, expected {expected}: {argv}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def good_report(project: str, evidence_link: str) -> str:
    return textwrap.dedent(
        f"""\
        # {project} 项目研究报告

        ## 0. 文档信息与证据口径

        研究对象：`fixture/{project.lower()}`；研究日期：2026-07-26；固定版本：测试快照。
        **事实**来自源码和配置，**推断**来自多项事实，**建议**用于选型，**待确认**表示证据不足。

        ## 1. 项目概览

        面向需要整理项目资料的研发负责人，本项目把分散文档加工成可追溯的研究入口。
        最适合小型工程审阅；不适合替代生产安全审计。核心证据见[项目说明]({evidence_link})。

        ## 2. 客户、问题与场景

        研发负责人接手陌生项目时，需要在短时间内知道入口、核心模块、风险和下一步验证。
        原流程是在 README、配置和代码间反复跳转；项目用统一清单减少遗漏。第二类用户是
        架构师，他们在技术选型前需要把功能承诺和真实实现分开。

        ## 3. 功能体系

        核心功能包括证据盘点、功能映射、架构解释、案例推演和质量门禁。用户输入项目目录，
        系统读取 `README.md`、清单和入口文件，输出带证据等级的报告。失败时保留未确认项，
        不把缺失文件解释为不存在。

        ```mermaid
        flowchart LR
          A["项目目录"] --> B["证据盘点"]
          B --> C["功能与架构分析"]
          C --> D["研究报告"]
        ```

        ## 4. 实际场景案例

        ### 4.1 案例一：研发负责人接手服务

        - 案例类型：场景推演
        - 背景与用户：研发负责人需要在一天内理解一个陌生服务。
        - 前置条件：获得只读源码和固定 commit。
        - 真实输入：README、`pyproject.toml`、入口文件和测试目录。
        - 操作步骤：先固定版本；再定位入口；随后追踪核心调用；最后核对测试。
        - 系统内部处理：发现器列出候选证据，研究者把功能绑定到模块和数据流。
        - 结果与价值：负责人得到可复核的架构图、边界和下一验证清单。
        - 异常、边界与恢复：缺少测试时标为待确认，通过最小无副作用样例补证据。
        - 验证证据或推演依据：项目说明和入口代码支持该流程。

        ### 4.2 案例二：产品经理比较两个方案

        - 案例类型：本地验证
        - 背景与用户：产品经理需要比较自建工具和第三方服务。
        - 前置条件：两个项目均有固定版本和许可证。
        - 真实输入：功能表、部署脚本、第三方依赖和限制清单。
        - 操作步骤：先按场景对齐功能；再比较数据流；随后检查成本；最后列采用条件。
        - 系统内部处理：报告把用户价值映射到模块、鉴权、外部传输和失败降级。
        - 结果与价值：决策从功能数量排名变为带约束的条件化选择。
        - 异常、边界与恢复：价格或维护状态变化时重新核验动态来源。
        - 验证证据或推演依据：本地报告校验和链接检查均通过。

        ## 5. 核心技术实现

        执行链为：目录输入 → 路径过滤 → 项目标记识别 → 证据清单 → 人工研究 → 报告门禁。
        发现器只读文件系统，不执行项目代码；验证器检查章节、案例和链接。状态保存在 JSON
        清单和 Markdown 报告中，没有数据库。重复扫描不会改动项目，因此天然幂等。

        ## 6. 第三方能力与外部依赖

        默认不依赖外部服务。若研究动态 Release 或价格，可访问官方页面；传输的是查询请求，
        不应上传私有源码。联网不可用时保留本地结论并把动态信息标为待确认。

        ## 7. 边界与限制

        静态发现不能判断业务正确性，也不能证明生产性能、安全或许可适用性。个人路径和密钥
        不写入报告。代码许可证不自动覆盖示例图片、字体和第三方内容。成熟度判断只代表快照。

        ## 8. 使用、部署与运维

        最小路径是运行发现器、人工研究、套用模板并运行验证器。脚本只需要 Python 标准库。
        没有后台服务、数据库或部署；升级时保留报告路径，失败后修复文档并重跑门禁即可。

        ## 9. 项目评价与选型建议

        优势是统一决策口径，成立条件是研究者真正回看源码；代价是比改写 README 更耗时。
        建议先对一个代表项目试点。若只有一句简介需求，完整报告可能过重，可改为只读简报。

        ## 10. 其他注意事项

        动态数据必须标注日期；AI 生成内容可能误读代码；网络受限时不能宣称最新。报告是研究
        判断，不是安全认证、法律意见或厂商承诺。未确认事项应留在正文而不是藏在脚注。

        ## 11. 证据与参考

        核心定位来自[项目说明]({evidence_link})；执行链由 `scripts/discover_projects.py` 的行为支持。
        案例一是场景推演，案例二是本地验证，不代表真实客户部署。

        ## 12. 最终结论

        1. 项目本质：证据型项目研究流程。
        2. 最适合解决：陌生项目理解和多项目选型。
        3. 核心机制：清单、证据映射、完整案例和门禁。
        4. 最大优势：把用户价值与真实实现连接起来。
        5. 最大限制：静态检查不能代替真实运行。
        6. 值得采用的条件：能够访问固定源码并接受人工复核。
        7. 采用前仍需验证：动态信息、生产性能和许可。
        """
    )


def catalog() -> str:
    return textwrap.dedent(
        """\
        # Fixture 项目研究总目录

        更新时间：2026-07-26
        研究对象：2 个
        主报告：2 份

        ## 1. 如何阅读

        先看一屏总览，再按需求选择；动态信息只代表测试快照。

        ## 2. 一屏总览

        | 项目 | 类型 | 用户与场景 | 核心实现 | 成熟度 | 建议 | 主报告 |
        | --- | --- | --- | --- | --- | --- | --- |
        | Alpha | Python 工具 | 研发接手项目 | 清单与验证 | 测试夹具 | 可试用 | [报告](reports/alpha-report.md) |
        | Beta | Agent Skill | 统一研究流程 | 指令与脚本 | 测试夹具 | 可试用 | [报告](reports/beta-report.md) |

        ## 3. 按需求快速选择

        需要扫描 Python 项目时选择 Alpha；需要规范 Agent 工作流时选择 Beta。

        ## 4. 跨项目共性结论

        两个样本都表明，结构门禁只能保证下限，关键事实仍需回到原始证据。

        ## 5. 研究边界

        这是合成测试数据，没有联网、付费调用或生产验证。
        """
    )


def validate_skill_package() -> None:
    skill_md = SKILL_DIR / "SKILL.md"
    agents = SKILL_DIR / "agents" / "openai.yaml"
    text = skill_md.read_text(encoding="utf-8")
    check(SKILL_DIR.name == "pola-project-research-report", "目录名符合 Skill 名称")
    check(text.startswith("---\n"), "SKILL.md 包含 YAML frontmatter")
    check(
        "name: pola-project-research-report" in text,
        "frontmatter name 正确",
    )
    check(
        "description:" in text and "TODO" not in text,
        "description 已完成且无 TODO",
    )
    check(agents.is_file(), "agents/openai.yaml 存在")
    check(
        'display_name: "Pola项目研究及报告"'
        in agents.read_text(encoding="utf-8"),
        "中文显示名称正确",
    )
    for relative in (
        "references/research-workflow.md",
        "references/report-contract.md",
        "references/evidence-and-writing.md",
        "assets/research-report-template.md",
        "assets/research-catalog-template.md",
    ):
        check((SKILL_DIR / relative).is_file(), f"资源存在：{relative}")


def validate_python_syntax() -> None:
    for path in (DISCOVER, VALIDATE, Path(__file__).resolve()):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    check(True, "Python 脚本语法通过")


def main() -> int:
    try:
        validate_skill_package()
        validate_python_syntax()

        with tempfile.TemporaryDirectory(prefix="pola-project-research-harness-") as temp:
            root = Path(temp)
            write(root / "alpha" / "README.md", "# Alpha\n")
            write(root / "alpha" / "pyproject.toml", "[project]\nname='alpha'\n")
            write(root / "alpha" / "main.py", "print('alpha')\n")
            write(root / "beta" / "README.md", "# Beta\n")
            write(root / "beta" / "SKILL.md", "---\nname: beta\n---\n")
            write(root / "node_modules" / "noise" / "package.json", "{}\n")

            first = run(
                [
                    sys.executable,
                    str(DISCOVER),
                    "--root",
                    str(root),
                    "--max-depth",
                    "3",
                ]
            )
            inventory = json.loads(first.stdout)
            project_paths = {item["path"] for item in inventory["projects"]}
            check({"alpha", "beta"} <= project_paths, "发现 Python 项目和 Agent Skill")
            check(
                all("node_modules" not in item for item in project_paths),
                "发现器排除 node_modules",
            )

            second = run(
                [
                    sys.executable,
                    str(DISCOVER),
                    "--root",
                    str(root),
                    "--max-depth",
                    "3",
                ]
            )
            second_inventory = json.loads(second.stdout)
            check(
                inventory["projects"] == second_inventory["projects"],
                "重复发现得到相同项目清单",
            )
            missing_root = run(
                [
                    sys.executable,
                    str(DISCOVER),
                    "--root",
                    str(root / "does-not-exist"),
                ],
                expected=2,
            )
            check(
                json.loads(missing_root.stdout)["status"] == "error",
                "不存在的工作目录安全失败",
            )

            alpha_report = root / "reports" / "alpha-report.md"
            beta_report = root / "reports" / "beta-report.md"
            write(alpha_report, good_report("Alpha", "../alpha/README.md"))
            write(beta_report, good_report("Beta", "../beta/README.md"))
            catalog_path = root / "catalog.md"
            write(catalog_path, catalog())
            validation_json = root / "validation.json"
            run(
                [
                    sys.executable,
                    str(VALIDATE),
                    "--report",
                    str(alpha_report),
                    "--report",
                    str(beta_report),
                    "--catalog",
                    str(catalog_path),
                    "--json",
                    str(validation_json),
                ]
            )
            validation = json.loads(validation_json.read_text(encoding="utf-8"))
            check(validation["status"] == "pass", "完整报告和总目录通过质量门禁")
            check(validation["report_count"] == 2, "质量门禁覆盖全部测试报告")

            bad_report = root / "reports" / "bad-report.md"
            synthetic_private_path = "/" + "Users/example/Desktop/private-project"
            synthetic_secret = "api" + "_key = '" + "abcdefghijklmnop" + "'"
            write(
                bad_report,
                "# Bad 项目研究报告\n\n## 项目概览\n\n"
                "TODO：稍后补充。[缺失证据](missing-evidence.md)\n"
                f"本地位置：{synthetic_private_path}\n"
                f"错误示例：{synthetic_secret}\n",
            )
            bad = run(
                [
                    sys.executable,
                    str(VALIDATE),
                    "--report",
                    str(bad_report),
                ],
                expected=1,
            )
            bad_result = json.loads(bad.stdout)
            check(
                any("详细案例不足" in item for item in bad_result["failures"]),
                "质量门禁拒绝缺少案例的报告",
            )
            check(
                any("占位符" in item for item in bad_result["failures"]),
                "质量门禁拒绝 TODO 占位内容",
            )
            check(
                any("本地链接不存在" in item for item in bad_result["failures"]),
                "质量门禁拒绝失效本地链接",
            )
            check(
                any("个人" in item for item in bad_result["failures"]),
                "质量门禁拒绝个人绝对路径",
            )
            check(
                any("明文密钥" in item for item in bad_result["failures"]),
                "质量门禁拒绝疑似明文密钥",
            )

        print("HARNESS PASS: pola-project-research-report")
        return 0
    except (HarnessFailure, OSError, json.JSONDecodeError) as exc:
        print(f"HARNESS FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
