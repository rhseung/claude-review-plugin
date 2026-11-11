#!/usr/bin/env python3
"""List the places outside a PR's changed lines that name a symbol the PR changed.

    gh pr diff 12 | python3 refs.py [CHECKOUT] > .claude-review/refs.md

CHECKOUT is a checkout of the PR head, the current directory by default. The unified diff
comes on stdin.
"""
import re
import subprocess
import sys
from collections import defaultdict

# 리뷰어가 diff 를 다 읽고 나면 바뀐 함수를 부르는 곳을 찾으러 가지 않는 일이 많았다.
# 모델이 찾을지 말지에 맡기지 않도록, 그 목록을 미리 뽑아 손에 쥐여 준다

CALLABLE = re.compile(
    r"\b(?:func|def|function|fn)\s+([A-Za-z_]\w*)"
    r"|\b([A-Za-z_]\w*)\s*(?::[^=]*)?=\s*(?:async\s+)?(?:\([^)]*\)|\w+)\s*=>"
    r"|^\s*([A-Za-z_]\w*)\s*\(\)\s*\{"
)
TYPE = re.compile(r"\b(?:class|struct|enum|protocol|interface|type|extension|module|trait|impl)\s+([A-Za-z_]\w*)")
# 함수 안의 지역 변수는 밖에서 부를 수 없다. 파일이나 타입 바로 아래 선언만 본다
MEMBER = re.compile(r"^(?:\t| {0,4})(?!\s)(?:[\w@]+\s+)*?(?:var|let|const|static|case|val)\s+([A-Za-z_]\w*)")
FIELD = re.compile(r"(?:^|[{,(\s])([A-Za-z_]\w*)\??\s*:(?!:)")
SKIP_PATHS = re.compile(r"(\.md|\.lock|\.snap|\.min\.\w+|\.svg|lock\.json|lock\.yaml|\.sum)$")
COMMON = set("""
    self this super true false null none nil undefined return value values key keys data item
    items index name names type types id ids text title body path paths file files line lines
    result results error errors event events state props options config default label count
    size width height left right top bottom start end next prev args kwargs input output
    string number boolean object array list map set get new old some all any init main test
    tests describe expect should when then else case break continue async await import export
    from public private static final override const let var func function class struct enum
    """.split())
MAX_HITS = 25
MAX_FILES = 20
MAX_LINES = 250
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|[\w-]*-tests)/|[._-](test|spec)s?\.\w+$|Tests?\.\w+$")


def parse(diff):
    files = {}
    path = None
    new_line = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = unquote(raw[4:]).removeprefix("b/") if raw[4:] != "/dev/null" else None
            if path:
                files[path] = {"added": set(), "defs": set(), "calls": set(), "fields": set()}
            continue
        if raw.startswith("--- ") or path is None:
            continue
        m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)", raw)
        if m:
            new_line = int(m.group(1))
            # hunk 머리에는 고친 줄을 감싼 함수가 나온다. 몸통만 바뀐 함수도 이렇게 잡힌다
            collect(m.group(2), files[path])
            continue
        if raw.startswith("+"):
            files[path]["added"].add(new_line)
            line = raw[1:]
            new_line += 1
        elif raw.startswith("-"):
            line = raw[1:]
        else:
            new_line += 1
            continue
        collect(line, files[path])
        for f in FIELD.finditer(line):
            files[path]["fields"].add(f.group(1))
    return files


def unquote(path):
    # git 은 ASCII 밖의 글자가 든 경로를 따옴표와 8진수로 감싸 낸다
    if path.startswith('"') and path.endswith('"'):
        return path[1:-1].encode("latin1").decode("unicode_escape").encode("latin1").decode("utf8")
    return path


def collect(line, f):
    for m in CALLABLE.finditer(line):
        name = next(g for g in m.groups() if g)
        f["defs"].add(name)
        f["calls"].add(name)
    for m in TYPE.finditer(line):
        f["defs"].add(m.group(1))
    m = MEMBER.match(line)
    if m:
        f["defs"].add(m.group(1))


def grep(root, name):
    proc = subprocess.run(["git", "-C", root, "grep", "-n", "-w", "-I", "-F", "-e", name, "--", "."],
                          capture_output=True, text=True, errors="replace")
    hits = []
    # splitlines 는 줄 안의 제어 문자에서도 끊는다. git grep 은 줄마다 개행으로만 나눈다
    for row in proc.stdout.split("\n"):
        parts = row.split(":", 2)
        if len(parts) == 3 and parts[1].isdigit() and not SKIP_PATHS.search(parts[0]):
            hits.append((parts[0], int(parts[1]), parts[2].strip()))
    return hits


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    # 바이너리 파일이 든 diff 도 있다. 글자를 못 읽는 줄 때문에 목록 전체를 잃지 않는다
    sys.stdin.reconfigure(errors="replace")
    files = parse(sys.stdin.read())
    defs, fields, calls = defaultdict(set), defaultdict(set), set()
    for path, f in files.items():
        # 테스트 파일에서 정의한 것은 다른 곳에서 부르지 않는다
        if SKIP_PATHS.search(path) or TEST_PATH.search(path):
            continue
        for name in f["defs"]:
            defs[name].add(path)
        calls |= f["calls"]
        for name in f["fields"] - f["defs"]:
            fields[name].add(path)
    sections = []
    for kind, names in (("def", defs), ("field", fields)):
        for name in names:
            if len(name) < 4 or name.lower() in COMMON or (kind == "field" and name in defs):
                continue
            hits = [(p, ln, t) for p, ln, t in grep(root, name) if ln not in files.get(p, {}).get("added", set())]
            if kind == "def" and name in calls:
                # 함수는 이름이 흔해도 부르는 자리만 세면 몇 곳 남지 않는다
                n = re.escape(name)
                call = re.compile(r"\b" + n + r"\s*[(<{]|\." + n + r"\b|#selector\([^)]*" + n)
                hits = [h for h in hits if call.search(h[2])]
            if kind == "field":
                # 인자 이름표나 다른 객체의 같은 키가 섞이지 않게, 값을 읽어 가는 자리만 남긴다
                n = re.escape(name)
                member = re.compile(r"\." + n + r"\b|\[['\"]" + n + r"['\"]\]")
                hits = [h for h in hits if member.search(h[2])]
            outside = [h for h in hits if h[0] not in files]
            if not outside:
                continue
            sections.append((kind, len({h[0] for h in hits}), name, hits))
    out, total = [], 0
    for kind, _, name, hits in sorted(sections, key=lambda s: (s[0], s[1], s[2])):
        where = ", ".join(sorted((defs if kind == "def" else fields)[name]))
        if len(hits) <= MAX_HITS:
            out.append(f"### `{name}` ({where})")
            out += [f"- {p}:{ln}: {t[:120]}" for p, ln, t in hits]
            total += len(hits) + 1
        elif len({h[0] for h in hits}) > MAX_FILES:
            out.append(f"### `{name}` ({where}): {len(hits)} places in {len({h[0] for h in hits})} files, too common to list")
            total += 1
        else:
            # 많이 쓰는 이름도 버리지 않는다. 바뀐 함수를 여기저기서 부르는 경우가 가장 놓치기 쉽다
            per = defaultdict(list)
            for p, ln, _ in hits:
                per[p].append(ln)
            out.append(f"### `{name}` ({where}): {len(hits)} places in {len(per)} files")
            for p in sorted(per, key=lambda p: (p in files, TEST_PATH.search(p) is not None, p))[:MAX_FILES]:
                out.append(f"- {p}: lines {', '.join(map(str, per[p][:12]))}{' ...' if len(per[p]) > 12 else ''}")
            total += min(len(per), MAX_FILES) + 1
        if total > MAX_LINES:
            out.append("\n(cut at the line limit)")
            break
    print("# Places outside the changed lines that name a changed symbol\n")
    print("\n".join(out) if out else "None.")


if __name__ == "__main__":
    main()
