import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

def fix_imports(lines):
    """
    If a file has:   from pydantic import BaseModel
    ensure it becomes: from pydantic import BaseModel, ConfigDict
    (and does not duplicate ConfigDict if already there).
    """
    out = []
    for line in lines:
        if line.strip().startswith("from pydantic import "):
            if "BaseModel" in line and "ConfigDict" not in line:
                # keep trailing comments
                parts = line.split("#", 1)
                left = parts[0].rstrip()
                comment = (" #"+parts[1]) if len(parts) > 1 else ""
                left = left.rstrip()
                # add ConfigDict before any trailing spaces/newline
                left = left[:-1] if left.endswith("\n") else left
                if left.endswith(","):
                    new = f"{left} ConfigDict\n{comment}"
                else:
                    new = left.replace("BaseModel", "BaseModel, ConfigDict") + "\n"
                    if comment:
                        new = new.rstrip("\n") + comment
                out.append(new if new.endswith("\n") else new + "\n")
                continue
        out.append(line)
    return out

def remove_simple_config_block(lines):
    """
    Replace a *simple* inner class Config block that only sets `orm_mode = True`
    with:  model_config = ConfigDict(from_attributes=True)

    We do this conservatively:
    - We only replace if the Config block contains no other non-blank, non-comment lines.
    - If it contains anything else, we leave it and print a note.
    """
    out = []
    i = 0
    changed = False
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.lstrip()
        if stripped.startswith("class Config:"):
            # indentation of the class line
            indent = line[: len(line) - len(stripped)]
            block = [line]
            i += 1
            # Capture block: subsequent lines that are more indented than the class line,
            # or blank/comment lines with any indent.
            while i < n:
                nxt = lines[i]
                nxt_stripped = nxt.lstrip()
                nxt_indent = nxt[: len(nxt) - len(nxt_stripped)]
                if nxt.strip() == "" or nxt_stripped.startswith("#"):
                    block.append(nxt)
                    i += 1
                    continue
                if len(nxt_indent) > len(indent):
                    block.append(nxt)
                    i += 1
                else:
                    break

            # Analyze the block body (excluding the header line)
            body = block[1:]
            # keep only meaningful (non-blank, non-comment) lines
            meaningful = [b for b in body if b.strip() and not b.lstrip().startswith("#")]

            # Is it a simple "orm_mode = True" block?
            only_orm = (
                len(meaningful) == 1 and
                "orm_mode" in meaningful[0] and
                "=" in meaningful[0] and
                "True" in meaningful[0]
            )

            if only_orm:
                out.append(f"{indent}model_config = ConfigDict(from_attributes=True)\n")
                changed = True
            else:
                # leave it as-is if it has more than orm_mode
                out.extend(block)
                print(f"[note] Skipped complex Config block at indent {len(indent)}", file=sys.stderr)
            continue
        else:
            out.append(line)
            i += 1
    return out, changed

def process_file(path: Path) -> bool:
    src = path.read_text(encoding="utf-8").splitlines(keepends=True)
    orig = src[:]
    src = fix_imports(src)
    src, changed_cfg = remove_simple_config_block(src)
    if src != orig:
        path.write_text("".join(src), encoding="utf-8")
        return True
    return False

def main():
    if not APP.exists():
        print(f"Could not find app directory at {APP}")
        sys.exit(1)
    changed = 0
    py_files = list(APP.rglob("*.py"))
    for f in py_files:
        if f.name.startswith("__"):
            continue
        if process_file(f):
            changed += 1
    print(f"Updated {changed} file(s).")

if __name__ == "__main__":
    main()
