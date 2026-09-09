# claude-review-plugin

PR 리뷰를 Claude 에 맡기는 플러그인입니다. PR 을 생성할 때와 push 할 때마다 `AGENTS.md` 에
적힌 규칙을 기준으로 변경분을 검토하고, 규칙을 위반한 지점에 인라인 코멘트를 남깁니다.
수정한 뒤에 다시 push 하면 해결된 thread 를 닫고 새로 추가된 변경분만 검토하며, 남은
사항이 없으면 approve 합니다.

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
open https://github.com/apps/claude/installations/new   # Claude GitHub App. 저장소 선택. 최초 1회만
claude setup-token                                      # 구독 OAuth 토큰 (1년). Pro/Max/Team
gh secret set CLAUDE_CODE_OAUTH_TOKEN                   # 위 토큰 붙여넣기
gh variable set ENABLE_CLAUDE_REVIEW --body true
```

## 사용법

- 코멘트에는 thread 답글로 응답합니다. 지적이 타당하면 thread 를 닫습니다.
- 지시는 PR 코멘트에서 `@claude` 를 멘션해서 전달합니다. `@claude review` 라고 쓰면 push
  하지 않아도 다시 검토합니다.
- 비활성화하려면 `gh variable set ENABLE_CLAUDE_REVIEW --body false` 를 실행합니다.
- 포크에서 올라온 PR 은 검토 대상에서 제외합니다.
- 토큰이 만료되면 `claude setup-token` 과 `gh secret set` 을 다시 실행합니다.
- 봇은 요청 본문을 저장소 안의 `.claude-review/` 에 기록합니다. 커밋할 대상은 아니므로
  `.gitignore` 에 등록해 두시기를 권합니다.

## 로컬 실행

```sh
claude plugin marketplace add rhseung/claude-review-plugin
claude plugin install review@rhseung
```

`/review:pr OWNER/REPO NUMBER HEAD_SHA synchronize` 를 실행하면 같은 검토를 로컬에서
수행합니다.

## 구성

- `review/commands/pr.md` - push 할 때마다 실행되는 리뷰 루프
- `review/commands/reply.md` - thread 답글 응답
- `review/commands/mention.md` - `@claude` 멘션 처리
- `.github/workflows/review.yml` - 위 세 가지를 잡으로 묶은 reusable workflow. 검토에는
  opus 를 사용하고, 나머지에는 sonnet 을 사용합니다.
