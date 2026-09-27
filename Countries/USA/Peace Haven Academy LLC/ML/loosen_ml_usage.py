"""Let this entry point accept any words after the filename."""
import ast
import re
from pathlib import Path

target = Path("ml.py")
source = target.read_text(encoding="utf-8")
patched = source
removed = 0

# the guard as one 'if' plus its body: raise SystemExit / print+exit
patterns = [
    r"\n[ \t]*if sys\.argv\[1:\][^\n]*\n(?:[ \t]+[^\n]*\n)+?(?=[ \t]*(?:raise SystemExit\(main|sys\.exit\(main|return main))",
    r"\n[ \t]*if args and args != \[[^\]]*\][^\n]*\n(?:[ \t]+[^\n]*\n)+?(?=[ \t]*(?:raise SystemExit\(main|sys\.exit\(main|return main))",
]
for pattern in patterns:
    patched, count = re.subn(pattern, "\n", patched, count=1)
    removed += count

if not removed:
    print("Could not find the usage check. Lines that mention it:")
    for number, line in enumerate(source.splitlines(), 1):
        if "argv" in line or "Usage" in line:
            print(f"  {number}: {line.strip()}")
    raise SystemExit(1)

ast.parse(patched)                      # refuse to save a broken file
target.write_text(patched, encoding="utf-8")
print("ml.py now accepts any words, e.g. 'python ml.py holdings'")
