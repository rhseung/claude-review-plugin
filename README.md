# claude-review-plugin

PR 리뷰 봇. PR 이 열리거나 push 될 때마다 Claude 가 `AGENTS.md` 기준으로 리뷰한다. 피드백은
인라인 코멘트로 달리고, 고쳐서 push 하면 그 스레드를 닫고 새 변경만 다시 본다. 남은 게 없으면
approve 한다.

## 레포에 붙이기

`.github/workflows/review.yml`:

```yaml
name: review
on:
  pull_request:
    types: [opened, synchronize, ready_for_review, reopened]
  pull_request_review_comment:
    types: [created]
  issue_comment:
    types: [created]
permissions:
  contents: read
  pull-requests: write
  id-token: write
jobs:
  review:
    if: vars.ENABLE_CLAUDE_REVIEW == 'true'
    uses: rhseung/claude-review-plugin/.github/workflows/review.yml@main
    secrets: inherit
```

```sh
open https://github.com/apps/claude/installations/new   # Claude GitHub App. 레포 선택. 한 번만
claude setup-token                                      # 구독 OAuth 토큰(1년). Pro/Max/Team
gh secret set CLAUDE_CODE_OAUTH_TOKEN                   # 위 토큰 붙여넣기
gh variable set ENABLE_CLAUDE_REVIEW --body true
```

## 쓰는 법

- 코멘트에 답글을 달면 그 스레드 안에서 답한다. 납득하면 닫는다.
- 시킬 일은 PR 코멘트에 `@claude` 를 붙인다. `@claude review` 는 push 없이 다시 보기.
- 끄기: `gh variable set ENABLE_CLAUDE_REVIEW --body false`.
- 포크 PR 은 건너뛴다.
- 토큰이 만료되면 `claude setup-token` 과 `gh secret set` 을 다시 돌린다.
- 봇은 요청 본문을 레포 안 `.claude-review/` 에 쓴다. 커밋하지 않지만 `.gitignore` 에 넣어두면 안전하다.

## 로컬에서

```sh
claude plugin marketplace add rhseung/claude-review-plugin
claude plugin install review@rhseung
```

`/review:pr OWNER/REPO NUMBER HEAD_SHA synchronize` 로 같은 리뷰를 로컬에서 돌릴 수 있다.

## 구조

- `review/commands/pr.md` - push 마다 도는 리뷰 루프
- `review/commands/reply.md` - 스레드 답글 응답
- `review/commands/mention.md` - `@claude` 멘션 처리
- `.github/workflows/review.yml` - 위 셋을 잡으로 묶은 reusable workflow. 리뷰는 opus, 나머지는 sonnet
