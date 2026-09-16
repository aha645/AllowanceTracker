# AllowanceTracker — 콘솔 가계부 (`budget_app`)

표준 라이브러리만 사용하는 CLI 가계부입니다. 거래를 기록/검색/수정/삭제하고, 월별 요약과
예산 사용률을 확인하며, CSV 로 주고받을 수 있습니다.

- 요구사항 원문: [`doc/request.md`](doc/request.md)
- Python **3.10 이상** (개발·검증 환경: 3.12) / 외부 의존성 없음

---

## 1. 실행 방법

```bash
git clone <repo> && cd AllowanceTracker
python -m budget_app --help              # 전체 도움말
python -m budget_app <command> --help    # 서브커맨드별 도움말
```

첫 실행 시 저장 폴더(`./data`)와 데이터 파일 4종이 자동 생성됩니다.
저장 위치는 `--data-dir` 로 바꿀 수 있습니다.

```bash
python -m budget_app --data-dir ~/my-budget list
python -m budget_app --verbose list      # 실행 로그/시간 측정 출력(디버그용)
```

테스트 실행:

```bash
python -m unittest discover -s tests -v   # 50개 테스트
```

---

## 2. 빠른 시작

```bash
# 1) 카테고리를 먼저 등록해야 거래를 추가할 수 있습니다 (정책: 안 B)
python -m budget_app category add --name "식비"
python -m budget_app category add --name "교통"
python -m budget_app category add --name "월급"

# 2) 대화형으로 거래 추가
python -m budget_app add

# 3) 목록 / 요약 확인
python -m budget_app list --limit 10
python -m budget_app budget set --month 2024-01 --amount 500000
python -m budget_app summary --month 2024-01 --top 3
```

---

## 3. 명령어 레퍼런스 (실행 예시)

| 명령 | 설명 | 예시 |
|---|---|---|
| `add` | 거래 추가 (옵션 없이 실행하면 대화형) | `python -m budget_app add` |
| `add` (옵션) | 자동화용 비대화형 추가 | `python -m budget_app add --date 2024-01-05 --type expense --category "식비" --amount 12000 --memo "점심" --tags "외식,점심"` |
| `list` | 거래 목록(최신순) | `python -m budget_app list --limit 20` |
| `search` | 조건 검색(AND 결합) | `python -m budget_app search --from 2024-01-01 --to 2024-01-31 --category "식비" --type expense --q "점심" --tag "외식"` |
| `summary` | 월별 요약 + 예산 사용률 | `python -m budget_app summary --month 2024-01 --top 3` |
| `budget set` | 월 예산 설정(같은 달은 덮어쓰기) | `python -m budget_app budget set --month 2024-01 --amount 500000` |
| `budget show/list/remove` | 예산 조회/전체 목록/삭제 | `python -m budget_app budget show --month 2024-01` |
| `category add/list/remove` | 카테고리 관리 | `python -m budget_app category add --name "식비"` |
| `update` | 거래 수정(옵션 기반) | `python -m budget_app update --id TX-3 --amount 15000 --memo "저녁"` |
| `delete` | 거래 삭제 | `python -m budget_app delete --id TX-3 --yes` |
| `import` | CSV 일괄 등록 | `python -m budget_app import --from sample.csv` |
| `export` | CSV 내보내기 | `python -m budget_app export --out export.csv --month 2024-01` |
| `compact` | 오래된(고아) 레코드 정리 | `python -m budget_app compact` |
| `backup` | 데이터 파일 타임스탬프 백업 (보너스) | `python -m budget_app backup` |
| `recurring` | 반복 거래 규칙 관리 (보너스) | `python -m budget_app recurring add --day 25 --type income --category "월급" --amount 3000000` |
| | | `python -m budget_app recurring apply --month 2024-04` |

공통 옵션: `--data-dir PATH`, `--verbose`, `--version`, 모든 커맨드의 `--help`.
모든 옵션은 `--` 표기로 통일되어 있습니다.

### 출력 예시

```
$ python -m budget_app list --limit 3
#  id    날짜        타입     카테고리        금액  메모         태그
-  ----  ----------  -------  --------  ----------  -----------  ---------
1  TX-5  2024-01-07  expense  식비          -5,500  커피         카페,간식
2  TX-4  2024-02-02  expense  식비         -30,000  친구랑 저녁  외식
3  TX-3  2024-01-25  income   월급      +3,000,000  1월 급여

[완료] 3건 출력 / 전체 5건
```

왼쪽 `#` 은 화면용 순번이고, `TX-N` 이 내부 불변 id 입니다. 수정/삭제/검색에는 항상 `TX-N` 을 씁니다
(`--id 3` 처럼 숫자만 써도 됩니다).

```
$ python -m budget_app summary --month 2024-02
[2024-02 요약]
  총 수입  0원
  총 지출  30,000원
  잔액     -30,000원  (거래 1건)

[카테고리별 지출 TOP 3]
순위  카테고리      지출    비중
----  --------  --------  ------
   1  식비      30,000원  100.0%

[예산]
  예산     10,000원
  사용     30,000원 (300.0%) [####################]
  [경고] 예산을 20,000원 초과했습니다!
```

---

## 4. 저장 파일 위치/형식

기본 저장 폴더는 `./data` 이며 파일 4개로 분리되어 있습니다.

| 파일 | 역할 | 포맷 | 쓰기 방식 |
|---|---|---|---|
| `data/transactions.jsonl` | 거래 원본 데이터 | 텍스트, JSON 1줄 = 레코드 1개 | **append 전용** (수정도 새 버전을 끝에 추가) |
| `data/transactions.idx` | id → 현재 유효 byte 위치 | 이진, 슬롯당 **16바이트** 고정폭 | 슬롯 단위 in-place 덮어쓰기 |
| `data/categories.jsonl` | 카테고리 목록 | 텍스트 JSONL | 전체 재작성 (임시파일 + `os.replace`) |
| `data/budgets.jsonl` | 월별 예산 | 텍스트 JSONL | 전체 재작성 (임시파일 + `os.replace`) |
| `data/recurring.jsonl` | 반복 거래 규칙 (보너스) | 텍스트 JSONL | 전체 재작성 (임시파일 + `os.replace`) |

**왜 JSONL 인가** — 메모·태그에 쉼표나 따옴표가 들어가도 이스케이프 고민 없이 안전하게 저장되고,
한 줄이 곧 한 레코드라 줄 단위 append 와 byte 범위 읽기가 자연스럽습니다. CSV 는 사람이 주고받는
교환 포맷(`import`/`export`)으로만 씁니다.

**왜 카테고리/예산은 통째로 다시 쓰는가** — 데이터가 수십~수백 건 수준이라 append + 인덱스 구조의
복잡도를 감수할 이유가 없습니다. 대신 임시 파일에 전부 쓴 뒤 `os.replace` 로 바꿔 끼워, 쓰다가 죽어도
원본이 반쯤 망가지지 않게 했습니다.

### `transactions.idx` 구조

```
슬롯 = struct.pack("<QQ", startOffset, endOffset)   # 8B + 8B = 16B
id N 의 슬롯 위치 = (N - 1) * 16                     # 슬롯의 "위치"가 곧 id
삭제 표시            = start == 0 and end == 0        # 16바이트를 0으로 채움
다음 id             = 파일 크기 // 16 + 1
```

- 슬롯 안에 id 를 따로 저장하지 않습니다. **위치가 곧 id** 이므로 id 조회는 `seek` 한 번(O(1))입니다.
- 실제 레코드는 항상 `end > start` 이므로, offset 0 에서 시작하는 `TX-1` 이 살아 있어도
  삭제 표시 `(0, 0)` 과 혼동되지 않습니다.
- 삭제해도 슬롯은 파일에서 제거되지 않고 0으로만 초기화되므로 **id 는 재사용되지 않습니다**
  (별도의 카운터 파일이 필요 없습니다).

### 연산별 동작

| 연산 | `transactions.jsonl` | `transactions.idx` |
|---|---|---|
| `add` | 끝에 레코드 append | 16바이트 슬롯을 끝에 추가 |
| `update` | 수정된 **전체 레코드를 끝에 append** (기존 줄은 그대로 방치) | 해당 id 슬롯을 새 offset 으로 덮어쓰기 |
| `delete` | 아무것도 하지 않음 | 해당 id 슬롯 16바이트를 0으로 초기화 |
| `compact` | 살아있는 레코드만 새 파일로 옮겨 쓰고 `os.replace` 로 교체 | 슬롯 개수·순서는 그대로, offset 값만 갱신 |

### 왜 `compact` 가 필요한가

`update` 는 기존 줄을 고치지 않고 새 버전을 끝에 덧붙입니다(append-only). 그래서 수정이 쌓일수록
`transactions.jsonl` 에 아무도 참조하지 않는 **고아 레코드**가 남고 파일이 계속 커집니다.
`compact` 는 인덱스가 가리키는 살아있는 레코드만 골라 새 파일로 옮겨 쓴 뒤 통째로 교체합니다.
슬롯의 개수와 순서는 그대로 두고 offset 값만 고치므로 **id 는 절대 바뀌지 않습니다.**

```bash
python -m budget_app compact
```

CLI 는 고아 데이터 비율이 50% 를 넘으면 `compact` 를 안내합니다(자동 실행은 하지 않습니다 —
명령마다 새로 뜨는 1회성 CLI 라 "유휴 시간 자동 정리"가 성립하지 않기 때문입니다).

### 최신순 조회

`transactions.idx` 는 고정폭 이진 파일이라 **역순 순회에 UTF-8 멀티바이트 경계 문제가 없습니다.**
id 가 큰 슬롯부터 거꾸로 읽으면 별도 정렬 없이 최신순이 보장되고, 해당 byte 범위만 `seek` 해서 읽으므로
파일 전체를 메모리에 올리지 않습니다. `list` / `search` / `summary` / `export` / `category remove`
(사용 중 여부 판정) 모두 이 제너레이터 하나(`TransactionRepository.iter_latest_transactions()`)를
소비하며, 필요한 만큼만 읽고 조기 종료합니다.

---

## 5. import / export CSV 스키마

스키마는 고정이며 **UTF-8, 헤더 포함**입니다. (`import` 는 BOM 이 있는 파일도 읽습니다.)

```csv
date,type,category,amount,memo,tags
2024-03-01,expense,식비,8000,"점심, 회사 근처","외식,점심"
2024-03-02,income,월급,3000000,3월 급여,
```

| 컬럼 | 필수 | 형식 | 설명 |
|---|---|---|---|
| `date` | ✅ | `YYYY-MM-DD` | 자릿수를 맞춰야 함 (`2024-3-1` 불가) |
| `type` | ✅ | `income` \| `expense` | 그 외 값은 해당 행 skip |
| `category` | ✅ | 문자열 | **이미 등록된 카테고리만** 허용 (자동 생성하지 않음) |
| `amount` | ✅ | 0보다 큰 정수 | 쉼표 포함(`12,000`) 허용 |
| `memo` | ❌ | 문자열 | 비워도 됨. 쉼표/따옴표 포함 가능(CSV 규칙대로 인용) |
| `tags` | ❌ | 쉼표 구분 문자열 | 내부 리스트 ↔ CSV 문자열로 변환. 예: `외식,점심` |

- `import` 는 **행 단위로 검증**하고, 실패한 행은 건너뛴 뒤 사유와 함께 `imported=N, skipped=M` 를 출력합니다.
- `export` 는 `--month` 또는 (`--from` **AND** `--to`) 중 **최소 한 가지 조건이 필수**입니다
  (실수로 전체를 덤프하지 않도록). `--category` / `--type` / `--q` / `--tag` 를 추가로 AND 결합할 수 있습니다.
- `export` → `import` 왕복이 가능합니다(테스트로 검증).

---

## 6. 주요 정책 (Decision Log)

| 항목 | 결정 | 이유 |
|---|---|---|
| 저장 포맷 | **JSONL** | 특수문자·쉼표를 포함한 메모/태그 처리에 유리 |
| `update` 방식 | **옵션 기반** (`update --id TX-3 --amount 15000`) | `search`/`delete` 와 CLI 패턴이 일관되고 자동화 가능. 지정하지 않은 필드는 기존 값 유지 |
| 카테고리 초기화 | **안 B** — 카테고리가 비어 있으면 `add` 를 차단하고 `category add` 를 안내 | 기본 카테고리를 임의로 만들어 주지 않음 |
| 거래 id 표시 | `TX-1`, `TX-2` … (**zero-padding 없음**) | 자릿수를 고정하면 대용량에서 자릿수 초과 문제가 생김 |
| id 재번호 | **하지 않음** — 삭제 후 빈 번호(gap)는 정상 | ① 레코드 본문에 id 가 있어 재번호하려면 다시 써야 하고 append-only 전제가 깨짐 ② "위치=id" 인덱스에서 재번호는 뒤 슬롯을 전부 밀어야 해서 O(1) 삭제가 O(n)이 됨 ③ 실제 회계 시스템도 취소된 번호를 재사용하지 않음(감사 관점) |
| 빈 번호 심미성 | **표시 계층에서 해결** | `list`/`search` 출력 왼쪽에 화면용 순번(1,2,3…)을 따로 매김. 내부 참조는 항상 불변 id |
| 카테고리 삭제 | 사용 중이면 **차단** | 대체 카테고리를 요구하는 방식은 단순성을 위해 채택하지 않음 |
| 예산 재설정 | 같은 달은 **덮어쓰기** (이전 금액을 안내 메시지로 표시) | 월 예산은 한 달에 하나여야 자연스러움 |
| 검증 실패 처리 | 대화형 입력은 **재입력 루프**, 옵션 입력은 **오류+힌트 후 종료(exit 1)** | 자동화 스크립트가 잘못된 값으로 조용히 진행되지 않도록 |

### 종료 코드

| 코드 | 의미 |
|---|---|
| `0` | 정상 종료 |
| `1` | 검증 실패 / 데이터 없음 / 입출력 오류 (`[오류]` + `[힌트]` 출력) |
| `2` | argparse 사용법 오류 (잘못된 옵션 등) |
| `130` | 사용자가 Ctrl+C / EOF 로 입력을 취소 |

예외는 스택트레이스 대신 항상 아래 형식으로 출력됩니다(원인 추적이 필요하면 `--verbose`).

```
[오류] 등록되지 않은 카테고리입니다: '외식비'
[힌트] 등록된 카테고리: 식비, 교통, 월급 / 새로 만들려면 category add 를 사용하세요.
```

---

## 7. 아키텍처 / 모듈 구조

```
budget_app/
├── __main__.py     python -m budget_app 진입점 (종료 코드 전달)
├── cli.py          argparse 서브커맨드 정의, 대화형 입력, 출력 포맷팅
├── services.py     검증·검색·요약·예산 계산·CSV 변환·백업·반복규칙 (비즈니스 로직)
├── repository.py   TransactionRepository / TransactionIndex (append-only 로그 + 이진 인덱스)
├── stores.py       JsonlStore / CategoryStore / BudgetStore / RecurringStore
├── models.py       Transaction·Category·Budget·RecurringRule dataclass, 커스텀 예외
├── formatter.py    외부 라이브러리 없는 표 정렬(전각 문자 폭 계산), 금액/막대 포맷
└── decorators.py   handle_errors / log_call / timeit (functools.wraps 로 메타데이터 보존)
tests/              unittest 기반 테스트 50개 (저장 엔진 / 서비스 / CLI)
```

계층 책임은 **모델 → 저장소 → 서비스 → CLI** 로 분리되어 있습니다.
저장 방식(파일 포맷)은 `repository`/`stores` 만 알고, 규칙은 `services` 가, 사람과의 입출력은
`cli` 가 담당합니다. 모든 공개 함수/메서드에 타입 힌트가 붙어 있습니다.

주요 클래스: `Transaction`, `TransactionIndex`, `TransactionRepository`, `JsonlStore`,
`CategoryStore`, `BudgetStore`, `RecurringStore`, `TransactionService`, `BudgetService`,
`CsvService`, `BackupService`, `RecurringService`, `SearchCriteria`, `MonthlySummary`.

데코레이터는 `@command` (= `@handle_errors` + `@log_call` + `@timeit`) 하나로 묶어 모든 커맨드
핸들러에 적용했습니다. 예외를 `[오류]`/`[힌트]` 형식으로 바꾸고 종료 코드를 정하는 일을 커맨드마다
반복하지 않기 위해서입니다.

---

## 8. 알려진 한계

- **크래시 안전성**: `transactions.idx` 의 16바이트 슬롯을 덮어쓰는 도중 정확히 그 순간 프로세스가
  강제 종료되면 해당 슬롯이 손상될 이론적 가능성이 있습니다. WAL 같은 완전한 크래시 안전성은
  이 과제 범위 밖입니다. (쓰기 후 `fsync` 는 호출하며, 카테고리/예산 파일은 임시파일 + `os.replace`
  로 원자적으로 교체합니다.)
- **로그 파일 증가**: `update` 가 쌓이면 `transactions.jsonl` 이 계속 커집니다. `compact` 명령으로
  정리할 수 있고, 고아 비율이 50% 를 넘으면 CLI 가 안내합니다.
- **파일 크기 상한**: offset 이 8바이트(unsigned)라 이론상 로그 파일 상한은 사실상 무제한 수준이며,
  개인 가계부 규모에서는 문제되지 않습니다.
- **동시 실행**: 같은 `--data-dir` 에 대해 여러 프로세스를 동시에 실행하는 상황은 고려하지 않았습니다
  (파일 잠금 없음). 1인 사용 CLI 를 전제로 합니다.
- **삭제된 id 의 gap**: 설계상 정상 동작입니다(위 Decision Log 참조).

---

## 9. 보너스 구현 현황

- ✅ **백업**: `backup` — `data/backup/YYYYmmdd_HHMMSS/` 로 데이터 파일 복사
- ✅ **반복 내역**: `recurring add/list/remove/apply` — 매월 반복 규칙 등록 후 `apply --month` 로
  해당 월 거래 생성(같은 달 중복 적용 방지, 말일 보정: 매월 31일 규칙 → 2024-02-29)
- ✅ **출력 포맷 테이블 정렬**: `formatter.py` — 외부 라이브러리 없이 전각 문자 폭까지 계산해 정렬
- ✅ **저장 원자성 강화**: 카테고리/예산/반복규칙 파일도 임시파일 + `os.replace` 로 교체
