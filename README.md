# claude-review-plugin

PR 리뷰를 Claude에 맡기는 플러그인입니다. PR을 생성하면 `AGENTS.md`에 적힌 규칙과 저장소의
리뷰 지침(`.github/review.md`)을 기준으로 PR 전체 diff를 검토하고 인라인 코멘트를 남깁니다.

이후 push 할 때마다 해결된 thread를 닫고, 지난 리뷰 이후에 바뀐 부분과 그 변경이 영향을 주는
코드만 검토합니다. force push나 rebase로 지난 리뷰의 커밋이 사라졌으면 PR 전체를 다시
검토합니다. base 브랜치를 merge만 했으면 PR과 base 브랜치가 함께 바꾼 파일만 확인합니다.

모든 코멘트는 종류를 나타내는 GitHub 알림 블록으로 시작합니다.

| 종류 | 알림 | 뜻 | merge를 막는가 |
|---|---|---|---|
| 데이터 | `CAUTION` | 데이터 손실이나 보안 문제 | 막음 |
| 버그 | `WARNING` | 동작이 틀림 | 막음 |
| 규칙 | `IMPORTANT` | `AGENTS.md`나 리뷰 지침의 규칙 위반 | 막음 |
| 제안 | `TIP` | 이름, 더 단순한 방법, 작은 정리 | 막지 않음 |
| 참고 | `NOTE` | 이 PR 범위 밖의 한계, 따로 기록해 둘 내용 | 막지 않음 |

막는 코멘트가 새로 나왔거나 막는 thread가 열려 있으면 request changes로, 아니면 approve로
리뷰를 제출합니다. 세 번째 리뷰 이후로는 데이터와 버그만 코멘트합니다.

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
  actions: read
  id-token: write
jobs:
  review:
    if: vars.ENABLE_CLAUDE_REVIEW == 'true'
    uses: rhseung/claude-review-plugin/.github/workflows/review.yml@v1
    secrets:
      CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

`secrets: inherit`는 같은 조직이나 enterprise 안의 workflow에만 secret을 넘깁니다. 이 플러그인은
개인 계정 저장소에 있으므로, 조직 저장소에서 부를 때 `inherit`를 쓰면 토큰이 빈 값으로 넘어갑니다.
위처럼 토큰을 직접 넘깁니다.

```sh
open https://github.com/apps/claude/installations/new   # Claude GitHub App. 저장소 선택. 최초 1회만
claude setup-token                                      # 구독 OAuth 토큰 (1년). Pro/Max/Team
gh secret set CLAUDE_CODE_OAUTH_TOKEN                   # 위 토큰 붙여넣기
gh variable set ENABLE_CLAUDE_REVIEW --body true
```

## 리뷰 지침

저장소마다 무엇을 볼지는 `.github/review.md`에 적습니다. 파일이 없으면 `AGENTS.md`의
규칙과 범용 기준(로직 오류, 함께 바뀌지 않은 코드, 쓸모없는 추상화)으로만 검토합니다.

```markdown
# 리뷰 지침

## 볼 것
- View가 `@/api`를 import하거나 ViewModel이 UI에 손대는 경우

## 보지 않을 것
- ESLint, Prettier, TypeScript가 잡는 것
```

## 사용법

- 코멘트에는 thread 답글로 응답합니다. 피드백이 타당하면 thread를 닫습니다.
- 지시는 PR 코멘트에서 `@claude`를 멘션해서 전달합니다. `@claude review`라고 쓰면 push
  하지 않아도 다시 검토합니다.
- 비활성화하려면 `gh variable set ENABLE_CLAUDE_REVIEW --body false`를 실행합니다.
- 포크에서 올라온 PR은 검토 대상에서 제외합니다.
- 봇이 연 PR(Dependabot 등)은 기본으로 검토하지 않습니다. 검토하려면 `with: { allowed_bots: 'dependabot[bot]' }`를
  넘기고, Dependabot이 띄운 실행은 Dependabot 시크릿만 받으므로
  `gh secret set CLAUDE_CODE_OAUTH_TOKEN --app dependabot`으로 토큰을 함께 넣습니다.
- 토큰이 만료되면 `claude setup-token`과 `gh secret set`을 다시 실행합니다.
- 봇은 요청 본문을 저장소 안의 `.claude-review/`에 기록합니다. 커밋할 대상은 아니므로
  `.gitignore`에 등록해 두시기를 권합니다.

## 로컬 실행

```sh
claude plugin marketplace add rhseung/claude-review-plugin
claude plugin install review@rhseung
```

`/review:pr OWNER/REPO NUMBER HEAD_SHA`를 실행하면 PR 전체를 같은 기준으로 로컬에서
검토합니다.

## 구성

- `review/commands/pr.md` - PR을 열 때와 push 할 때마다 실행되는 리뷰 루프
- `review/commands/reply.md` - thread 답글 응답
- `review/commands/mention.md` - `@claude` 멘션 처리
- `.github/workflows/review.yml` - 위 세 가지를 잡으로 묶은 reusable workflow. 첫 리뷰에는
  opus를 사용하고, 이후 리뷰와 답글, 멘션에는 sonnet을 사용합니다.
