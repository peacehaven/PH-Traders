"""Insert `import _bootstrap` into an entry script at the only place it is valid."""
import ast, sys
from pathlib import Path

target = Path(sys.argv[1] if len(sys.argv) > 1 else "ml.py")
source = target.read_text(encoding="utf-8")
if "import _bootstrap" in source:
    print(f"{target}: already set up")
    raise SystemExit(0)

tree = ast.parse(source)
line = 0  # 0 means "insert at the very top"
for node in tree.body:
    is_docstring = isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
        and isinstance(node.value.value, str)
    is_future = isinstance(node, ast.ImportFrom) and node.module == "__future__"
    if is_docstring or is_future:
        line = node.end_lineno  # must stay below these
    else:
        break

lines = source.splitlines(True)
lines.insert(line, "\nimport _bootstrap  # re-runs this script inside .venv\n")
target.write_text("".join(lines), encoding="utf-8")
ast.parse(target.read_text(encoding="utf-8"))  # fails loudly if the edit broke the file
print(f"{target}: added `import _bootstrap` after line {line}")
