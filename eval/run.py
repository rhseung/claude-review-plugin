#!/usr/bin/env python3
"""Replay /review:pr on past PRs and score it against bugs we know were there.

    python3 eval/run.py                       # working tree's review/, every case
    python3 eval/run.py --ref origin/main     # the prompts as they are on a ref
    python3 eval/run.py --cases 382-score-wipe,393-back-to-back-recording --jobs 2
    python3 eval/run.py --repeat 3            # one review varies run to run; compare rates
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"
ALLOWED = "Read,Grep,Glob,Agent,Bash(gh pr diff:*),Bash(gh pr view:*),Bash(gh api:*),Edit(/.claude-review/**)"
BLOCKING_ALERTS = {"CAUTION", "WARNING", "IMPORTANT"}
# 첫 실행에서 작업들이 같은 저장소를 동시에 clone 하면 하나만 살아남는다. worktree add 도 같은 lock 을 잡는다
CHECKOUT = threading.Lock()


def sh(*args, cwd=None, env=None, check=True, timeout=None):
    return subprocess.run(args, cwd=cwd, env=env, check=check, timeout=timeout,
                          capture_output=True, text=True)


def plugin_dir(args, work):
    if not args.ref:
        return ROOT / "review", "worktree"
    sha = sh("git", "rev-parse", args.ref, cwd=ROOT).stdout.strip()
    dest = work / "plugins" / sha[:12]
    if not dest.exists():
        dest.mkdir(parents=True)
        archive = subprocess.Popen(["git", "archive", sha, "review"], cwd=ROOT, stdout=subprocess.PIPE)
        subprocess.run(["tar", "-x", "-C", str(dest)], stdin=archive.stdout, check=True)
        archive.wait()
    return dest / "review", f"{args.ref}@{sha[:7]}"


def checkout(case, k, args, work):
    with CHECKOUT:
        return _checkout(case, k, args, work)


def _checkout(case, k, args, work):
    # 로컬 클론의 객체를 빌려 쓰는 별도 저장소. 사용자의 클론에는 worktree 도 ref 도 남기지 않는다
    repo = work / "repos" / case["repo"].replace("/", "__")
    if not repo.exists():
        repo.parent.mkdir(parents=True, exist_ok=True)
        local = args.local_clones.get(case["repo"].split("/")[1])
        if local:
            sh("git", "clone", "--bare", "--shared", local, str(repo))
        else:
            # 비공개 저장소도 받도록 gh 로그인을 빌린다. 토큰을 URL 이나 설정에 남기지 않는다
            sh("git", "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential",
               "clone", "--bare", "--filter=blob:none", f"https://github.com/{case['repo']}.git", str(repo))
    auth = ("-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential")
    url = f"https://github.com/{case['repo']}.git"
    for sha in (case["head"], case["base"]):
        if sh("git", "cat-file", "-t", sha, cwd=repo, check=False).returncode == 0:
            continue
        sh("git", *auth, "fetch", url, f"+refs/pull/{case['pr']}/head:refs/eval/{case['pr']}", cwd=repo, check=False)
        if sh("git", "cat-file", "-t", sha, cwd=repo, check=False).returncode != 0:
            # force push 로 어느 ref 에도 남지 않은 head 는 SHA 로 직접 받는다
            sh("git", *auth, "fetch", url, sha, cwd=repo)
    tree = work / "trees" / f"{case['id']}-r{k}"
    if not tree.exists():
        sh("git", "worktree", "add", "--detach", "--force", str(tree), case["head"], cwd=repo)
    # 지난 실행이 남긴 요청 본문이 다음 실행에 보이지 않게 한다
    shutil.rmtree(tree / ".claude-review", ignore_errors=True)
    return repo, tree


def review(case, k, args, plugin, work, out):
    repo, tree = checkout(case, k, args, work)
    out.mkdir(parents=True, exist_ok=True)
    for name in ("writes.jsonl", "gh.log"):
        (out / name).unlink(missing_ok=True)
    shim = work / "bin"
    shim.mkdir(parents=True, exist_ok=True)
    shutil.copy(EVAL / "gh", shim / "gh")
    empty_gh = work / "gh-config"
    empty_gh.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
    env.update({
        "PATH": f"{shim}:{env['PATH']}",
        # 셸 프로필이 PATH 를 다시 짜서 진짜 gh 가 불려도, 인증이 없으니 GitHub 에 쓰지 못한다
        "GH_CONFIG_DIR": str(empty_gh),
        "EVAL_REAL_GH": args.real_gh,
        "EVAL_GH_TOKEN": args.gh_token,
        "EVAL_OUT": str(out),
        "EVAL_GIT_DIR": str(tree),
        "EVAL_BASE": case["base"],
        "EVAL_HEAD": case["head"],
    })
    prompt = f"/review:pr {case['repo']} {case['pr']} {case['head']} full - 1"
    started = time.time()
    proc = sh("claude", "-p", prompt,
              "--plugin-dir", str(plugin),
              "--model", args.model,
              "--max-turns", "150",
              "--restricted", "--strict-mcp-config", "--no-session-persistence",
              "--tools", "Bash,Read,Grep,Glob,Write,Edit,Agent",
              "--allowedTools", ALLOWED,
              "--output-format", "json",
              cwd=tree, env=env, check=False, timeout=args.timeout)
    (out / "claude.json").write_text(proc.stdout or "")
    (out / "claude.stderr").write_text(proc.stderr or "")
    try:
        meta = json.loads(proc.stdout)
    except json.JSONDecodeError:
        meta = {}
    return {
        "seconds": round(time.time() - started),
        "cost_usd": meta.get("total_cost_usd"),
        "turns": meta.get("num_turns"),
        "is_error": meta.get("is_error", proc.returncode != 0),
        "result": (meta.get("result") or proc.stderr or "")[:200],
    }


def submitted(out, case):
    reviews = []
    path = out / "writes.jsonl"
    if path.exists():
        for line in path.read_text().splitlines():
            w = json.loads(line)
            if re.search(rf"pulls/{case['pr']}/reviews$", w["endpoint"] or "") and isinstance(w["body"], dict):
                reviews.append(w["body"])
    if not reviews:
        return None
    r = reviews[-1]
    return {"event": r.get("event"), "body": r.get("body", ""), "comments": r.get("comments", [])}


def kind_of(text):
    first = next((l for l in text.strip().splitlines() if l.strip()), "")
    m = re.match(r">\s*\[!(\w+)\]", first)
    if m:
        alert = m.group(1).upper()
        return alert, alert in BLOCKING_ALERTS
    m = re.match(r"`?\[(버그|데이터|규칙|사소)\]", first)
    if m:
        return m.group(1), m.group(1) != "사소"
    return "untagged", True


JUDGE = """You grade an automated code review against defects we know were in the change.

Known defects:
{known}

The review the bot submitted. Item 0 is the review body; the rest are inline comments.
{items}

1. For each known defect, find the item that identifies the same defect: the same root
   cause and the same wrong behavior. Mentioning the same file or area without the defect
   does not count. A body that names the defect counts.
2. For every inline comment you did not match, judge from its text alone: "valid" (a real
   problem or a sound suggestion), "doubtful" (unclear or likely not a problem) or "wrong".

Reply with JSON only, no prose:
{{"defects": [{{"id": "...", "item": <index or null>, "why": "..."}}],
  "others": [{{"item": <index>, "verdict": "valid|doubtful|wrong", "why": "..."}}]}}"""


def judge(case, result, args, out):
    if args.resume and (out / "judge.json").exists():
        text = json.loads((out / "judge.json").read_text()).get("result", "")
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
    items = [f"[0] review body ({result['event']}):\n{result['body']}"]
    for i, c in enumerate(result["comments"], 1):
        items.append(f"[{i}] {c.get('path')}:{c.get('line')}\n{c.get('body')}")
    known = "\n".join(f"- {e['id']} ({e['where']}): {e['bug']}" for e in case["expect"])
    prompt = JUDGE.format(known=known, items="\n\n".join(items))
    proc = sh("claude", "-p", prompt, "--model", args.judge_model,
              "--restricted", "--strict-mcp-config", "--no-session-persistence",
              "--tools", "", "--output-format", "json", check=False, timeout=600)
    (out / "judge.json").write_text(proc.stdout or "")
    text = json.loads(proc.stdout).get("result", "") if proc.stdout else ""
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {"defects": [], "others": []}


def score(case, result, verdict):
    rows = []
    found = {d["id"]: d.get("item") for d in verdict.get("defects", [])}
    for e in case["expect"]:
        item = found.get(e["id"])
        caught = item is not None
        blocking = False
        if caught and item and 0 < item <= len(result["comments"]):
            blocking = kind_of(result["comments"][item - 1]["body"])[1]
        rows.append({"id": e["id"], "caught": caught, "blocking": blocking, "item": item})
    others = verdict.get("others", [])
    return {
        "defects": rows,
        "comments": len(result["comments"]),
        "others": {v: sum(1 for o in others if o.get("verdict") == v) for v in ("valid", "doubtful", "wrong")},
        "event": result["event"],
    }


def run_case(case, k, args, plugin, work, outdir):
    out = outdir / case["id"] / f"r{k}"
    reuse = args.rejudge or (args.resume and submitted(out, case) is not None)
    if reuse and (out / "writes.jsonl").exists():
        meta = json.loads((out / "meta.json").read_text())
    else:
        meta = review(case, k, args, plugin, work, out)
        (out / "meta.json").write_text(json.dumps(meta, indent=2))
    result = submitted(out, case)
    if result is None:
        # 한도에 걸린 실행은 리뷰를 못 낸 것이 아니라 시작도 못 한 것이다. 섞이면 프롬프트 탓으로 읽힌다
        limit = re.search(r"hit your .*limit|usage limit", meta.get("result") or "", re.I)
        error = "usage limit" if limit else "no review submitted"
        return {"case": case["id"], "repo": case["repo"], "set": case.get("set", "tuned"), "run": k, "history": case["history"], "meta": meta, "error": error}
    (out / "review.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    verdict = judge(case, result, args, out)
    return {"case": case["id"], "repo": case["repo"], "set": case.get("set", "tuned"), "run": k, "history": case["history"], "meta": meta, **score(case, result, verdict)}


def summarize(results, label, args):
    lines = [f"# {label}", "", f"model `{args.model}`, judge `{args.judge_model}`, {args.repeat} run(s) per case", "",
             "| case | before | caught | blocking | comments/run | other: valid/doubtful/wrong | verdicts | time/run | cost/run |",
             "|---|---|---|---|---|---|---|---|---|"]
    tot = {"defects": 0, "caught": 0, "blocking": 0, "cost": 0.0}
    # 프롬프트를 고칠 때 본 저장소와 처음 보는 저장소를 따로 본다. 합치면 맞춤 효과가 섞인다
    per_repo = {}
    by_case = {}
    for r in results:
        by_case.setdefault((r["history"], r["case"]), []).append(r)
    for (history, case), runs in sorted(by_case.items()):
        ok = [r for r in runs if "error" not in r]
        n = len(runs)
        costs = [r["meta"]["cost_usd"] for r in runs if r["meta"].get("cost_usd") is not None]
        secs = [r["meta"]["seconds"] for r in runs if r["meta"].get("seconds") is not None]
        tot["cost"] += sum(costs)
        cost = f"${sum(costs) / len(costs):.2f}" if costs else "-"
        time_ = f"{round(sum(secs) / len(secs))}s" if secs else "-"
        if not ok:
            errs = ", ".join(sorted({f"{r['error']} x{sum(1 for x in runs if x['error'] == r['error'])}" for r in runs}))
            lines.append(f"| {case} | {history} | {errs} | | | | | {time_} | {cost} |")
            continue
        per = len(ok[0]["defects"])
        caught = sum(x["caught"] for r in ok for x in r["defects"])
        blocking = sum(x["blocking"] for r in ok for x in r["defects"])
        total = per * len(ok)
        tot["defects"] += total
        tot["caught"] += caught
        tot["blocking"] += blocking
        for key in (runs[0].get("set", "tuned"), runs[0].get("repo", "?")):
            rp = per_repo.setdefault(key, [0, 0, 0])
            rp[0] += caught
            rp[1] += blocking
            rp[2] += total
        comments = sum(r["comments"] for r in ok) / len(ok)
        o = {v: sum(r["others"][v] for r in ok) for v in ("valid", "doubtful", "wrong")}
        events = {}
        for r in ok:
            events[r["event"]] = events.get(r["event"], 0) + 1
        verdicts = ", ".join(f"{e} x{c}" for e, c in events.items())
        errors = {}
        for r in runs:
            if "error" in r:
                errors[r["error"]] = errors.get(r["error"], 0) + 1
        failed = "".join(f" (+{c} {e})" for e, c in errors.items())
        lines.append(f"| {case} | {history} | {caught}/{total}{failed} | {blocking}/{total} | {comments:.1f} "
                     f"| {o['valid']}/{o['doubtful']}/{o['wrong']} | {verdicts} | {time_} | {cost} |")
    lines += ["", f"caught {tot['caught']}/{tot['defects']}, as blocking {tot['blocking']}/{tot['defects']}, "
                  f"total cost ${tot['cost']:.2f}"]
    lines += [f"- {r}: caught {c}/{t}, as blocking {b}/{t}" for r, (c, b, t) in sorted(per_repo.items())]
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ref", help="git ref of this repo whose review/ to use (default: working tree)")
    p.add_argument("--cases", help="comma-separated case ids (default: all)")
    p.add_argument("--model", default="claude-opus-5-5")
    p.add_argument("--judge-model", default="claude-sonnet-5-5")
    p.add_argument("--jobs", type=int, default=3)
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--name", help="results folder name (default: label and time)")
    p.add_argument("--work", default=os.environ.get("EVAL_WORK", str(Path(os.environ.get("TMPDIR", "/tmp")) / "claude-review-eval")))
    p.add_argument("--local-clone", action="append", default=[],
                   help="a local clone to borrow objects from, matched by folder name; repeatable")
    p.add_argument("--rejudge", action="store_true", help="reuse the last reviews in --name and only judge again")
    p.add_argument("--resume", action="store_true", help="in --name, keep runs that submitted a review and redo the rest")
    args = p.parse_args()

    args.real_gh = shutil.which("gh")
    args.local_clones = {Path(c).expanduser().name: str(Path(c).expanduser()) for c in args.local_clone}
    args.gh_token = sh("gh", "auth", "token").stdout.strip()
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    cases = json.loads((EVAL / "cases.json").read_text())
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    plugin, label = plugin_dir(args, work)
    name = args.name or f"{re.sub(r'[^A-Za-z0-9._-]', '_', label)}-{time.strftime('%m%d-%H%M')}"
    outdir = EVAL / "results" / name
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"{label}: {len(cases)} cases -> {outdir}", file=sys.stderr)

    results = []
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_case, c, k, args, plugin, work, outdir): (c, k)
                   for k in range(1, args.repeat + 1) for c in cases}
        for f in cf.as_completed(futures):
            c, k = futures[f]
            try:
                r = f.result()
            except Exception as e:  # 한 사례가 실패해도 나머지 결과는 남긴다
                r = {"case": c["id"], "repo": c["repo"], "set": c.get("set", "tuned"), "run": k, "history": c["history"], "meta": {},
                     "error": f"{type(e).__name__}: {e}"[:200]}
            results.append(r)
            print(f"done {c['id']} r{k}", file=sys.stderr)

    (outdir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    summary = summarize(results, label, args)
    (outdir / "summary.md").write_text(summary + "\n")
    print(summary)


if __name__ == "__main__":
    main()
