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

첫 실행 시 저장 폴더(`./data`)와 데이터 파일 5종(필수 3종 transactions/categories/budgets + 보조 2종)이 자동 생성됩니다.
저장 위치는 `--data-dir` 로 바꿀 수 있습니다.

```bash
python -m budget_app --data-dir ~/my-budget list
python -m budget_app --verbose list      # 실행 로그/시간 측정 출력(디버그용)
```

### 테스트 실행

외부 라이브러리 없이 표준 `unittest`로 작성되어 있고, 테스트 파일은 **`tests/test_requirement_checklist.py` 하나**입니다(총 45개).
`doc/request.md` 의 9개 기능(add → list → search → summary → budget → category → update → delete →
import/export)을 요구사항 순서대로 **하나의 시나리오로 이어서** 필수 항목만 검증하고, 이어서 공통 관심사와
보너스를 검증합니다.

| 번호 | 검증 대상 |
|---|---|
| 00x~09x | 사전 준비 + 9개 기능 (앞 단계에서 만든 거래 5건을 뒤 단계가 이어받아 사용) |
| 10x | 데코레이터 (`--verbose` 로그/시간 측정, 함수 메타데이터 보존) |
| 11x | 종료 코드 0 / 1(원인+힌트, 스택트레이스 없음) / 130(Ctrl+C) |
| 12x | 표 포맷 (구분선, 부호, 천 단위 구분, 예산 막대) |
| 13x | 저장 원자성 (임시파일 교체 실패 시 원본 보존, 거래 로그 append 전용 + 인덱스 제자리 갱신) |
| 14x · 15x | 보너스: 백업, 반복 내역 |

CLI를 블랙박스로 호출(`main(argv)`)하고 출력 문자열과 저장 파일만으로 판단합니다. 모든 테스트가
`tests/data` 폴더 하나를 공유하며 번호 순서대로 데이터를 이어받습니다. 데이터는 **`test_000_reset_data`
에서만 지웁니다**(수동 초기화).

- **파일 전체 실행**: 000 이 먼저 초기화하므로 몇 번을 다시 실행해도 같은 결과입니다. 끝난 뒤 `tests/data`에 시나리오 결과가 남습니다.
- **개별 테스트 실행**: 직전 실행이 남긴 데이터 위에서 동작합니다. 처음부터 다시 하려면 `test_000_reset_data` →
  `test_002_prepare_default_categories`(기본 카테고리 사전 등록) → `test_010`, `test_011`, `test_013`(거래 등록) 순서로
  실행해 데이터를 준비한 뒤 원하는 테스트를 실행하세요. 전체 실행이 끝난 직후의 데이터는 시나리오 마지막 상태
  (수정/삭제가 반영된 상태)라서 중간 단계 테스트(list, search, summary 등)는 실패할 수 있습니다.

```bash
# 전체 테스트 실행
python -m unittest discover -s tests -v

# 같은 의미 (파일 하나만 지정)
python -m unittest tests.test_requirement_checklist -v

# 파일을 직접 실행 (테스트 파일 상단의 sys.path 보정 코드 덕분에 이 방식도 동작함)
python tests/test_requirement_checklist.py -v
```

### VSCode 테스트 탭(비커 아이콘) 활성화하기

새 컴퓨터에서 이 프로젝트를 받아 VSCode 테스트 탭까지 쓸 수 있게 만드는 전체 과정을 **1단계(환경 구축) → 2단계(VSCode에서
테스트 실행)** 순서로 정리하면 다음과 같습니다.

> 참고: `uv venv`로 만드는 가상환경(`.venv`)은 프로젝트 폴더 **안에** 있어야 하는데,
> `git clone`은 대상 폴더가 완전히 비어 있어야만 동작합니다(`.venv`가 먼저 들어있으면
> `fatal: 대상 경로가 이미 있고 빈 디렉터리가 아닙니다` 로 실패). 그래서 "가상환경
> 만들기"는 실제로는 **프로젝트를 받은 직후**에 수행합니다. `uv` 설치만 컴퓨터에 한 번
> 해두면 되는 완전히 독립적인 작업이라 1단계에 그대로 둡니다.

#### 1단계 — 환경 구축 (컴퓨터 하나당 최초 1회)

```bash
# uv 설치 (파이썬 패키지/가상환경 관리 도구)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

설치 스크립트가 마지막에 PATH 설정 방법을 안내합니다(셸 설정 파일에 한 줄 추가
하라는 안내가 보통 나옵니다). 안내대로 반영하거나, 간단히 **새 터미널을 하나
열어서** 아래 명령으로 설치가 됐는지 확인하고 다음 단계로 넘어갑니다.

```bash
uv --version
```

#### 2단계 — 프로젝트 받고 VSCode에서 테스트 실행

```bash
# 1) GitHub 에서 프로젝트 받기
git clone https://github.com/aha645/AllowanceTracker.git
cd AllowanceTracker

# 2) 프로젝트 전용 가상환경 생성
#    (dataclass(slots=True) 등 3.10+ 문법을 쓰므로 3.10 이상 지정)
uv venv .venv --python 3.10

# 3) 가상환경 활성화 — 이후 이 터미널의 python/pip/uv 는 전부 이 .venv 를 가리킴
source .venv/bin/activate

# 4) 활성화된 이 콘솔에서 VSCode 열기 (현재 폴더를 워크스페이스로 오픈)
code .
```

VSCode가 열리면 아래 순서로 테스트 탭을 활성화합니다.

1. **`.vscode/settings.json` 확인** — 아래 내용으로 이 저장소에 포함되어 있어야
   정상입니다(관리자가 `git push` 해 두었다는 전제. 혹시 클론한 저장소에 이 파일이
   없다면 아래 내용 그대로 `.vscode/settings.json` 을 새로 만들면 됩니다):
   ```jsonc
   {
       "python.testing.unittestEnabled": true,
       "python.testing.pytestEnabled": false,
       "python.testing.unittestArgs": [
           "-v",
           "-s", "tests",
           "-p", "test_*.py"
       ]
   }
   ```
   | 키 | 의미 |
   |---|---|
   | `python.testing.unittestEnabled` | 표준 `unittest` 방식으로 테스트를 찾으라는 뜻 — 이걸 켜야 비커 탭이 켜짐 |
   | `python.testing.pytestEnabled` | 이 프로젝트는 pytest(외부 패키지)를 쓰지 않으므로 명시적으로 꺼서 혼선 방지 |
   | `python.testing.unittestArgs` | `-s tests`(탐색 폴더) + `-p test_*.py`(파일 패턴) — `python -m unittest discover -s tests -p test_*.py` 와 동일한 조건 |

2. **인터프리터를 방금 만든 `.venv`(3.10)로 직접 선택** — `Cmd+Shift+P` → **`Python: Select Interpreter`** → `./.venv/bin/python` (또는 `.venv (Python 3.10)`) 선택.
   VSCode 파이썬 확장이 macOS 시스템 기본 Python(주로 3.9, Command Line Tools 번들)을
   자동으로 잡는 경우가 있는데, 이 프로젝트는 `budget_app/models.py`의
   `@dataclass(slots=True)`처럼 **Python 3.10부터** 지원하는 문법을 쓰므로, 3.9가
   선택된 상태에서는 테스트 탐색 자체가 아래 에러로 실패합니다. 그래서 이 선택을
   건너뛸 수 없습니다.
   ```
   TypeError: dataclass() got an unexpected keyword argument 'slots'
   ```
   `.vscode/settings.json`에 `python.defaultInterpreterPath`로 특정 경로를 하드코딩
   하지 않는 이유는, 그 경로가 사람·컴퓨터마다 다르기 때문입니다 — 대신 매번 이 선택
   UI로 지정합니다.

3. **좌측 액티비티바의 테스트(비커) 아이콘 클릭** → `tests/` 아래 `test_requirement_checklist.py` 가 트리로
   나타나면 성공. 각 테스트 옆 ▶(실행) 또는 🐛(디버그) 버튼으로 개별 실행/디버깅이
   가능합니다.

**문제가 안 풀릴 때 체크리스트**

| 증상 | 조치 |
|---|---|
| 테스트 탭에 아무것도 안 뜸 | 테스트 탭 새로고침(↻) 버튼, 또는 `Cmd+Shift+P` → `Test: Refresh Tests` |
| 코드를 고쳤는데 옛날 테스트 이름이 그대로 보임 | 위와 동일 + 그래도 안 되면 `Cmd+Shift+P` → `Developer: Reload Window` |
| `dataclass() got an unexpected keyword argument 'slots'` 에러 | 3.9가 선택된 상태 → `Python: Select Interpreter` 로 `.venv`(3.10) 재선택 |
| 원인을 못 찾겠을 때 | 하단 `Output` 패널 → 드롭다운에서 `Python` 또는 `Python Test Log` 선택해서 실제 에러 확인 |

`.venv/`는 `.gitignore`에 포함되어 있어 커밋되지 않습니다.

**VSCode 디버거에서 CLI 자체를 실행하기**: `.vscode/launch.json` 에 `python -m budget_app`
을 인자와 함께 실행하는 디버그 설정이 준비되어 있습니다. "budget_app: 인자 직접 입력" 설정을
고르면 F5 를 누를 때마다 입력창이 뜨고, 거기에 `list --limit 5` 처럼 원하는 인자를 쳐서
`cmd_add`/`cmd_summary` 등에 브레이크포인트를 걸고 디버깅할 수 있습니다.
(`add` 처럼 `input()` 을 쓰는 대화형 명령은 `console: integratedTerminal` 설정 덕분에
VSCode 통합 터미널에서 정상적으로 키보드 입력을 받습니다.)

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
| `list --all` | 전체 출력 (`--limit` 과 함께 쓸 수 없음) | `python -m budget_app list --all` |
| `search` | 조건 검색(AND 결합) | `python -m budget_app search --from 2024-01-01 --to 2024-01-31 --category "식비" --type expense --q "점심" --tag "외식"` |
| `summary` | 월별 요약 + 예산 사용률 | `python -m budget_app summary --month 2024-01 --top 3` |
| `budget set` | 월 예산 설정(같은 달은 덮어쓰기) | `python -m budget_app budget set --month 2024-01 --amount 500000` |
| `budget show/list/remove` | 예산 조회/전체 목록/삭제 | `python -m budget_app budget show --month 2024-01` |
| `category add/list/remove` | 카테고리 관리 | `python -m budget_app category add --name "식비"` |
| `update` | 거래 수정(**대화형**: 현재 값을 보여주고 바꿀 항목만 입력, 엔터=유지, `-`=메모/태그 비우기) | `python -m budget_app update --id TX-3` |
| `delete` | 거래 삭제 | `python -m budget_app delete --id TX-3 --yes` |
| `import` | CSV 일괄 등록 | `python -m budget_app import --from sample.csv` |
| `export` | CSV 내보내기 | `python -m budget_app export --out export.csv --month 2024-01` |
| `compact` | 오래된(고아) 레코드 정리 | `python -m budget_app compact` |
| `backup` | 데이터 파일 타임스탬프 백업 (보너스) | `python -m budget_app backup` |
| `recurring` | 반복 거래 규칙 관리 (보너스) | `python -m budget_app recurring add --day 25 --type income --category "월급" --amount 3000000` |
| | | `python -m budget_app recurring apply --month 2024-04` |

공통 옵션: `--data-dir PATH`, `--verbose`, `--version`, 모든 커맨드의 `--help`.
모든 옵션은 `--` 표기로 통일되어 있습니다.

### update(대화형) 예시

```
$ python -m budget_app update --id TX-3
[현재 값]
#  id    날짜        타입     카테고리     금액  메모  태그
-  ----  ----------  -------  --------  -------  ----  ----
1  TX-3  2024-01-05  expense  식비      -12,000  점심  외식
[안내] 바꿀 항목만 입력하세요. 엔터=기존 값 유지, 메모/태그는 '-' 입력 시 비움 (Ctrl+C 로 취소)
날짜 [2024-01-05]:
타입 [expense]:
카테고리 [식비] (번호 또는 이름): 교통
금액 [12000]: 15000
메모 [점심]: -
태그 [외식] (쉼표 구분):
[수정 완료] id=TX-3
```

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

왼쪽 `#` 은 화면용 순번이고, `TX-N` 이 내부 불변 id 입니다. 수정/삭제에는 항상 `TX-N` 을 씁니다
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

## 3-1. 직접 따라 해보기 (테스트 시나리오와 동일)

`tests/test_requirement_checklist.py` 가 자동으로 실행하는 시나리오를 손으로 그대로 입력해 볼 수 있도록
정리했습니다. 위에서 아래로 **순서대로** 입력하면 앞 단계에서 만든 데이터를 뒤 단계가 이어받습니다
(`TX-1`~`TX-5` 라는 id 도 이 순서대로 만들었을 때 기준입니다).

### 준비

`--data-dir` 를 따로 주지 않으므로 데이터는 기본 저장 폴더 `./data` 에 자동으로 만들어집니다
(첫 실행 시 폴더와 파일이 자동 생성). 처음부터 시작하려면(= `test_000_reset_data`) 기존 데이터 폴더를 지웁니다.

```bash
rm -rf ./data      # ⚠ 지금까지 입력한 실제 데이터가 모두 사라집니다. 지워도 되는 상태에서만 실행하세요.
```

> 메모처럼 **공백이 들어간 값은 따옴표**로 감싸세요(예: `--memo "1월 급여"`). import/export 용 CSV 파일은
> 데이터 폴더 밖(현재 폴더)에 만듭니다. 아래 명령은 모두 프로젝트 폴더에서 `python -m budget_app ...` 로 입력합니다.

### 1) 사전 준비 — 카테고리 없이 add 는 막히고, 카테고리를 등록하면 된다 (00x)

```bash
python -m budget_app add                                  # [오류] 등록된 카테고리가 없습니다. + [힌트] → 종료 코드 1
python -m budget_app category add --name 식비             # [저장 완료] 카테고리 '식비' 추가
python -m budget_app category add --name 교통
python -m budget_app category add --name 월급
python -m budget_app category add --name 월세
```

### 2) add — 대화형 1건 + 옵션 3건 (01x)

대화형: `python -m budget_app add` 를 실행하고 프롬프트에 아래 값을 차례로 입력합니다.

```bash
python -m budget_app add
```
| 프롬프트 | 입력 |
|---|---|
| 날짜 | `2024-01-05` |
| 타입 | `expense` |
| 카테고리 (번호 또는 이름) | `식비` |
| 금액 | `12000` |
| 메모 | `점심` |
| 태그 | `외식` |

→ `[저장 완료] id=TX-1`

옵션 방식(프롬프트 없이 한 줄):

```bash
python -m budget_app add --date 2024-01-10 --type expense --category 교통 --amount 20000 --memo "지하철"      # id=TX-2
python -m budget_app add --date 2024-01-25 --type income  --category 월급 --amount 3000000 --memo "1월 급여"   # id=TX-3
python -m budget_app add --date 2024-01-31 --type expense --category 월세 --amount 500000 --memo "1월 월세"    # id=TX-4
```

**잘못된 입력은 저장되지 않고 오류가 나는지 확인** (모두 거래가 늘지 않아야 함):

```bash
python -m budget_app add --date 2024-13-40 --type expense --category 식비 --amount 1000        # 날짜 형식 오류 → 종료 코드 1
python -m budget_app add --date 2024-01-01 --type expense --category 식비 --amount -500        # 음수 금액 → 1
python -m budget_app add --date 2024-01-01 --type expense --category 식비 --amount 0           # 0 금액 → 1
python -m budget_app add --date 2024-01-01 --type transfer --category 식비 --amount 1000       # 허용되지 않은 type → 종료 코드 2 (argparse)
python -m budget_app add --date 2024-01-01 --type expense --category 없는카테고리 --amount 1000  # 미등록 카테고리 → 1
```

**대화형에서는 잘못 입력해도 다시 묻는지 확인** — 아래 순서로 입력합니다. 오류 메시지가 3번 나오고 마지막에
저장됩니다(`id=TX-5`). 이 거래(2024-03, 교통, 1,500원)는 이후 단계에서 "가장 최근 거래"로 쓰입니다.

| 프롬프트 | 입력 | 결과 |
|---|---|---|
| 날짜 | `2024-13-40` | 오류 → 다시 묻는다 |
| 날짜 | `2024-03-01` | |
| 타입 | `expense` | |
| 카테고리 | `없는것` | 오류 → 다시 묻는다 |
| 카테고리 | `교통` | |
| 금액 | `-1` | 오류 → 다시 묻는다 |
| 금액 | `1500` | |
| 메모 / 태그 | (엔터, 엔터) | `[저장 완료] id=TX-5` |

### 3) list — 최신순, `--limit`, `--limit` 과 `--all` 은 함께 못 쓴다 (02x)

```bash
python -m budget_app list                    # 5건. TX-5, TX-4, TX-3, TX-2, TX-1 순서(나중에 등록한 것이 위)
python -m budget_app list --limit 2          # 최근 2건(TX-5, TX-4)만 + "[완료] 2건 출력 / 전체 5건"
python -m budget_app list --limit 5 --all    # error: argument --all: not allowed with argument --limit → 종료 코드 2
```

### 4) search — 조건별 필터, AND 결합, 조건 없음은 오류 (03x)

```bash
python -m budget_app search --from 2024-01-10 --to 2024-01-25     # 기간 → TX-3, TX-2
python -m budget_app search --category 식비                        # 카테고리 → TX-1
python -m budget_app search --type income                          # 타입 → TX-3
python -m budget_app search --q 지하철                             # 메모 키워드 → TX-2
python -m budget_app search --tag 외식                             # 태그 → TX-1
python -m budget_app search --type expense --from 2024-01-01 --to 2024-01-31 --category 월세   # AND 결합 → TX-4 만
python -m budget_app search                                        # [오류] 검색 조건이 하나도 지정되지 않았습니다. → 1
```

### 5) summary — 수입/지출/잔액, TOP N, 데이터 없는 달 (04x)

```bash
python -m budget_app summary --month 2024-01 --top 2
```
```
[2024-01 요약]
  총 수입  3,000,000원
  총 지출  532,000원            ← 12,000 + 20,000 + 500,000
  잔액     2,468,000원  (거래 4건)

[카테고리별 지출 TOP 2]        ← 큰 순서: 월세, 교통 (식비는 TOP 2 밖이라 나오지 않음)
```
```bash
python -m budget_app summary --month 2023-12     # "데이터 없음" 이 분명하게 출력된다
```

### 6) budget — 설정 → 사용률 → 덮어쓰기 → 초과 경고 (05x)

```bash
python -m budget_app budget set --month 2024-01 --amount 600000
python -m budget_app summary --month 2024-01        # 사용 532,000원 (88.7%) [##################..] / 잔여 68,000원 — 경고 없음
python -m budget_app budget set --month 2024-01 --amount 500000    # "기존 예산 600,000원을 덮어썼습니다."
python -m budget_app summary --month 2024-01        # 사용 532,000원 (106.4%) / [경고] 예산을 32,000원 초과했습니다!
python -m budget_app budget set --month 2024-01 --amount 0         # [오류] 금액은 0보다 커야 합니다 → 1
```

### 7) category — 중복 차단, 목록, 사용 중인 카테고리는 삭제 불가 (06x)

```bash
python -m budget_app category add --name 여가       # 저장 완료
python -m budget_app category add --name 식비       # [오류] ... 이미 등록되어 있습니다. → 1
python -m budget_app category list                  # 5개(식비, 교통, 월급, 월세, 여가)
python -m budget_app category remove --name 식비    # [오류] ... 사용 중이라 삭제할 수 없습니다 → 1  (거래 TX-1 이 쓰는 중)
python -m budget_app category remove --name 여가    # [삭제 완료] (아무 거래도 안 쓰므로 삭제됨)
python -m budget_app category remove --name 없는카테고리   # [오류] 찾을 수 없습니다 → 1
```

### 8) update — 대화형 수정 (07x)

`TX-1`(식비 12,000원, 메모 "점심", 태그 "외식")을 수정합니다. 현재 값이 먼저 표시되고, **엔터 = 기존 값 유지,
`-` = 메모/태그 비우기** 입니다.

```bash
python -m budget_app update --id TX-1
```
| 프롬프트 | 입력 | 의미 |
|---|---|---|
| 날짜 / 타입 / 카테고리 | (엔터 ×3) | 유지 |
| 금액 `[12000]` | `15000` | 12,000 → 15,000 |
| 메모 `[점심]` | `-` | 메모 비움 |
| 태그 `[외식]` | (엔터) | 유지 |

→ `[수정 완료] id=TX-1` + 변경된 필드 표(`amount`, `memo`)

```bash
python -m budget_app search --category 식비         # 금액 -15,000, 메모 없음, 태그 "외식" 유지
python -m budget_app summary --month 2024-01        # 총 지출 535,000원 (15,000 + 20,000 + 500,000)
python -m budget_app update --id TX-999999          # [오류] TX-999999 거래를 찾을 수 없습니다. → 1 (입력 프롬프트로 가지 않음)
```

### 9) delete — 삭제가 list/summary 에 반영된다 (08x)

```bash
python -m budget_app delete --id TX-2 --yes         # [삭제 완료] id=TX-2 (2024-01-10 교통 20,000원)   (--yes 없으면 y/N 확인)
python -m budget_app list                           # TX-2 가 사라진다(4건)
python -m budget_app summary --month 2024-01        # 총 지출 515,000원 (교통 20,000 제외)
python -m budget_app delete --id TX-2 --yes         # [오류] 거래를 찾을 수 없습니다 → 1 (이미 삭제됨)
python -m budget_app delete --id TX-999999 --yes    # [오류] → 1 (없는 id)
```

### 10) import / export (09x)

**import** — 현재 폴더에 `import.csv` 를 만듭니다(4행 중 2행만 유효):

```bash
cat > import.csv <<'CSV'
date,type,category,amount,memo,tags
2024-02-05,expense,식비,8000,"점심, 회사 근처","외식,점심"
2024-02-25,income,월급,3000000,2월 급여,
2024-02-10,expense,없는카테고리,5000,,
2024-02-11,expense,식비,-100,,
CSV

python -m budget_app import --from import.csv
```
```
[완료] import.csv → imported=2, skipped=2

[건너뛴 행]
행  사유
 4  등록되지 않은 카테고리입니다: '없는카테고리'
 5  금액은 0보다 커야 합니다: -100
```
```bash
python -m budget_app summary --month 2024-02        # 총 수입 3,000,000원 / 총 지출 8,000원 (가져온 데이터가 반영됨)
```

**export** — 월 단위 / 기간 단위로 내보내고, 조건이 없으면 거부됩니다.

```bash
python -m budget_app export --out export_2024_02.csv --month 2024-02                    # [완료] ... (2 records)
cat export_2024_02.csv             # 헤더 date,type,category,amount,memo,tags + 2행. "점심, 회사 근처" 처럼 쉼표가 든 메모도 그대로
python -m budget_app export --out export_range.csv --from 2024-01-01 --to 2024-01-31    # (3 records) ← 식비(15000, 수정 반영), 월급, 월세. 삭제한 교통은 없음
python -m budget_app export --out none.csv          # [오류] 내보내기 조건이 없습니다. → 1
ls data                       # budgets.jsonl categories.jsonl transactions.jsonl (+ transactions.idx, recurring.jsonl) 영구 저장 확인
```

### 11) 데코레이터 · 종료 코드 · 표 포맷 (10x ~ 12x)

```bash
python -m budget_app --verbose list      # 표 아래에 [log] -> cmd_list 시작 / [log] cmd_list 실행 시간 ...ms / [log] <- cmd_list 종료 (stderr)
python -m budget_app list                # --verbose 가 없으면 [log] 줄이 나오지 않는다
```

종료 코드는 직전 명령 직후 `echo $?` 로 확인합니다.

```bash
python -m budget_app list; echo $?                          # 0   정상
python -m budget_app delete --id TX-999999 --yes; echo $?   # 1   [오류] + [힌트] 가 나오고 Traceback(스택트레이스)은 없다
python -m budget_app add                                    # 프롬프트에서 Ctrl+C 를 누른다 → "[중단] 사용자가 입력을 취소했습니다."
echo $?                                                     # 130
```

표 포맷 확인: `python -m budget_app list` 결과에서 ① 헤더 아래 구분선(`-  ----  ---`) ② 수입 `+3,000,000` / 지출 `-500,000` 부호와 천 단위 구분
③ `python -m budget_app summary --month 2024-01` 의 예산 막대 `[####################]` 를 눈으로 확인합니다.

### 12) 저장 원자성 — 로그는 덧붙이기만, 인덱스는 제자리 갱신 (13x)

`transactions.jsonl` 의 줄 수와 `transactions.idx` 의 크기를 기록해 두고 수정/삭제 전후를 비교합니다.

```bash
wc -l data/transactions.jsonl; wc -c data/transactions.idx     # 예: 8줄, 112바이트

python -m budget_app update --id TX-1               # 금액만 16000 으로 바꾼다(나머지는 엔터)
wc -l data/transactions.jsonl; wc -c data/transactions.idx     # 줄 수 +1 (새 버전이 끝에 append), idx 크기는 그대로
tail -2 data/transactions.jsonl     # 맨 끝줄이 TX-1 의 새 버전(amount 16000), 바로 앞에 다른 거래 줄 — 옛 버전은 위쪽에 남아 있음

python -m budget_app delete --id TX-5 --yes         # delete 는 로그를 건드리지 않는다
wc -l data/transactions.jsonl; wc -c data/transactions.idx     # 줄 수·idx 크기 모두 그대로 (슬롯만 0 으로 초기화)
```

> 카테고리/예산 파일이 "임시 파일 + `os.replace`" 로 안전하게 교체되는지(디스크 오류를 흉내 내 원본이 보존되는지)는
> 손으로 재현하기 어려워 자동 테스트(`test_130_...`)로만 검증합니다.

### 13) 보너스 — 백업 (14x)

```bash
python -m budget_app backup                         # [완료] 백업 생성: data/backup/20261005_175830 (날짜_시각) + 복사된 파일 목록
ls data/backup/*              # 데이터 파일들이 그대로 복사되어 있다
```

### 14) 보너스 — 반복 내역 (15x)

```bash
python -m budget_app recurring add --day 31 --type income --category 월급 --amount 3000000 --memo "월급(반복)"   # id=RC-1
python -m budget_app recurring list                          # 매월 31일 규칙 1개
python -m budget_app recurring apply --month 2024-02         # 생성 1건 — 날짜가 2024-02-31 이 아니라 윤년 말일 2024-02-29 로 보정된다
python -m budget_app recurring apply --month 2024-02         # 생성 0건, "이미 적용되어 건너뜀 1건" — 같은 달 중복 생성 방지
python -m budget_app recurring remove --id RC-1              # 삭제
python -m budget_app recurring list                          # "등록된 반복 규칙이 없습니다."
```

실습이 끝나면 `rm -rf ./data import.csv export_*.csv none.csv` 로 정리하세요(`./data` 에는 실습 데이터만 들어 있어야 합니다).

---

## 4. 저장 파일 위치/형식

기본 저장 폴더는 `./data` 이며 파일이 5개로 분리되어 있습니다(요구사항의 필수 3종 `transactions` / `categories` / `budgets` + 인덱스 `transactions.idx` + 보너스 `recurring.jsonl`).

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

`update`는 기존 줄을 고치지 않고 새 버전을 끝에 덧붙이고(append-only), `delete`는
로그를 아예 건드리지 않은 채 인덱스 슬롯만 0으로 초기화합니다. 그래서 **`update`와
`delete`가 쌓일수록** `transactions.jsonl`에 아무도 참조하지 않는 **고아 레코드**가
남고 파일이 계속 커집니다. `compact`는 인덱스가 가리키는 살아있는 레코드만 골라 새
파일로 옮겨 쓴 뒤 통째로 교체합니다. 슬롯의 개수와 순서는 그대로 두고 offset 값만
고치므로 **id는 절대 바뀌지 않습니다.**

```bash
python -m budget_app compact
```

CLI는 `update`·`delete` 실행 직후 고아 데이터 비율을 확인해서, 50%를 넘으면 `compact`를
안내합니다(`console.py`의 `maybe_hint_compact()`). 자동으로 `compact`를 실행하지는 않습니다 —
명령마다 새로 뜨는 1회성 CLI라 "유휴 시간 자동 정리"가 성립하지 않기도 하고, 사용자
모르게 로그 파일 전체를 재작성하는 것보다 시점을 사용자가 직접 고르게 하는 편이
안전하다고 판단했기 때문입니다.

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
| `update` 방식 | **대화형 기반(안 B)** 으로 고정 — `update --id TX-3` 후 항목별 입력 (필드 옵션 없음) | 현재 값을 보면서 고칠 수 있어 수정이 수월함. 엔터=기존 값 유지, 메모/태그는 `-` 입력 시 비움, 검증 실패 시 재입력 |
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
├── cli.py          argparse 파서 정의 + main (커맨드 ↔ 핸들러 연결만 담당)
├── context.py      AppContext — 저장소/서비스 조립
├── console.py      핸들러 공용 입출력(오류 출력, id 파싱, 재입력 루프, 거래 표 출력)
├── commands/       커맨드 핸들러 (서브커맨드 1개 = 함수 1개)
│   ├── transaction.py  add / list / search / update / delete
│   ├── report.py       summary / budget set·show·list·remove
│   ├── category.py     category add / list / remove
│   ├── data.py         import / export / compact / backup
│   └── recurring.py    recurring add / list / remove / apply
├── validators.py   입력 검증·정규화(날짜/월/타입/금액/일자/태그)
├── services.py     거래 CRUD·검색·월별 요약, 예산 계산, 반복규칙 (비즈니스 로직)
├── file_services.py  CSV 가져오기/내보내기, 백업
├── repository.py   TransactionRepository / TransactionIndex (append-only 로그 + 이진 인덱스)
├── stores.py       JsonlStore / CategoryStore / BudgetStore / RecurringStore
├── models.py       Transaction·Category·Budget·RecurringRule dataclass, 커스텀 예외
├── formatter.py    외부 라이브러리 없는 표 정렬(전각 문자 폭 계산), 금액/막대 포맷
└── decorators.py   handle_errors / log_call / timeit (functools.wraps 로 메타데이터 보존)
tests/
└── test_requirement_checklist.py  인수 테스트 — 9개 기능 시나리오 + 데코레이터/종료코드/포맷/원자성/보너스 (unittest 기반, 총 45개)
```

계층 책임은 **모델 → 저장소 → 서비스 → 커맨드 핸들러/CLI** 로 분리되어 있습니다.
저장 방식(파일 포맷)은 `repository`/`stores` 만 알고, 규칙은 `services` 가, 사람과의 입출력은
`commands/`(핸들러)·`console.py`(공용 입출력)가, 커맨드 연결은 `cli` 가 담당합니다. 모든 공개 함수/메서드에 타입 힌트가 붙어 있습니다.

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
