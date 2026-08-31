"""Keep the requested function-by-function Office reference in sync with source."""

import ast
from pathlib import Path


def test_every_office_function_is_documented():
    repository = Path(__file__).resolve().parents[3]
    root = repository / "src" / "pc_manager_agent"
    paths = set(root.glob("**/*office*.py"))
    for name in ("office", "tools/office_tools", "safety/office", "backup"):
        paths.update((root / name).glob("*.py"))
    document = (repository / "docs" / "api-office-automation.md").read_text(encoding="utf-8")

    def check(nodes, prefix=""):
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                check(node.body, prefix + node.name + ".")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + node.name
                assert f"### `{name}`" in document
                signature = f"{name}({ast.unparse(node.args)}) -> "
                signature += ast.unparse(node.returns) if node.returns else "None"
                assert signature in document
                check(node.body, name + ".")

    for path in paths:
        assert path.relative_to(root).as_posix() in document
        check(ast.parse(path.read_text(encoding="utf-8")).body)
