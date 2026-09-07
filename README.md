# claude-review-plugin

PR 리뷰를 Claude 에 맡긴다. PR 생성과 push 마다 `AGENTS.md` 의 규칙을 기준으로 변경분을 검토하고,
위반 지점에 인라인 코멘트를 남긴다. 수정 후 push 하면 해결된 스레드를 닫고 새 변경분만
재검토하며, 남은 사항이 없으면 approve 한다.

## 설치

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

## 사용법

- 코멘트에는 스레드 답글로 응답한다. 타당하면 스레드를 닫는다.
- 지시는 PR 코멘트에 `@claude` 멘션으로 한다. `@claude review` 는 push 없는 재검토.
- 비활성화: `gh variable set ENABLE_CLAUDE_REVIEW --body false`.
- 포크 PR 은 검토 대상에서 제외한다.
- 토큰 만료 시 `claude setup-token` 과 `gh secret set` 을 재실행한다.
- 봇은 요청 본문을 레포 안 `.claude-review/` 에 기록한다. 커밋 대상은 아니지만 `.gitignore` 등록을 권한다.

## 로컬 실행

```sh
claude plugin marketplace add rhseung/claude-review-plugin
claude plugin install review@rhseung
```

`/review:pr OWNER/REPO NUMBER HEAD_SHA synchronize` 로 같은 검토를 로컬에서 실행한다.

## 구성

- `review/commands/pr.md` - push 마다 도는 리뷰 루프
- `review/commands/reply.md` - 스레드 답글 응답
- `review/commands/mention.md` - `@claude` 멘션 처리
- `.github/workflows/review.yml` - 위 셋을 잡으로 묶은 reusable workflow. 검토는 opus, 나머지는 sonnet
