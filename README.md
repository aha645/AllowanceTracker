# AllowanceTracker — 콘솔 가계부 (`budget_app`)

표준 라이브러리만 사용하는 CLI 가계부입니다. 거래를 기록/검색/수정/삭제하고, 월별 요약과
예산 사용률을 확인하며, CSV 로 주고받을 수 있습니다.

- 요구사항 원문: [`doc/request.md`](doc/request.md)
- Python **3.10 이상** / 외부 의존성 없음

---

## 1. 실행 방법

```bash
git clone https://github.com/aha645/AllowanceTracker.git && cd AllowanceTracker
python -m budget_app --help              # 전체 도움말
python -m budget_app <command> --help    # 서브커맨드별 도움말
```

첫 실행 시 저장 폴더(`./data`)와 데이터 파일 6종(필수 3종 transactions/categories/budgets + 보조 3종)이 자동 생성됩니다.
저장 위치는 `--data-dir` 로 바꿀 수 있습니다.

```bash
python -m budget_app --data-dir ~/my-budget list
python -m budget_app --verbose list      # 실행 로그/시간 측정 출력(디버그용)
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
| `list` | 거래 목록(**거래일자 최신순**, 같은 날짜면 나중에 등록한 것이 먼저) | `python -m budget_app list --limit 20` |
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

## 3-1. 따라 해보기

아래 명령을 **위에서 아래로 순서대로** 입력하면 앞 단계에서 만든 데이터를 뒤 단계가 이어받습니다
(`TX-1`~`TX-5` 라는 id 도 이 순서대로 만들었을 때 기준입니다). 각 단계의 `# 확인:` 은 그 명령으로 무엇을 확인하는지 설명합니다.
공백이 들어간 값은 따옴표로 감쌉니다. CSV 파일은 현재 폴더에 만듭니다.

```bash
rm -rf ./data      # 처음부터 시작 (⚠ 기존 거래 데이터가 모두 삭제됩니다)
```

### 1) 사전 준비

```bash
python -m budget_app add                      # 확인: 카테고리가 하나도 없으면 add 가 차단된다 ([오류] + [힌트], 종료 코드 1)
python -m budget_app category add --name 식비   # 확인: 카테고리 등록
python -m budget_app category add --name 교통
python -m budget_app category add --name 월급
python -m budget_app category add --name 월세
```

### 2) add — 거래 추가

```bash
python -m budget_app add     # 확인: 대화형 등록. 입력: 2024-01-05 → expense → 식비 → 12000 → 점심 → 외식  (id=TX-1 출력)
python -m budget_app add --date 2024-01-10 --type expense --category 교통 --amount 20000 --memo "지하철"      # 확인: 옵션 방식 등록 (TX-2)
python -m budget_app add --date 2024-01-25 --type income  --category 월급 --amount 3000000 --memo "1월 급여"  # TX-3
python -m budget_app add --date 2024-01-31 --type expense --category 월세 --amount 500000 --memo "1월 월세"   # TX-4
```

잘못된 값은 저장되지 않고 오류가 나는지 확인합니다.

```bash
python -m budget_app add --date 2024-13-40 --type expense --category 식비 --amount 1000        # 날짜 형식 오류 (종료 코드 1)
python -m budget_app add --date 2024-01-01 --type expense --category 식비 --amount -500        # 음수 금액 (1)
python -m budget_app add --date 2024-01-01 --type expense --category 식비 --amount 0           # 0 금액 (1)
python -m budget_app add --date 2024-01-01 --type transfer --category 식비 --amount 1000       # 허용되지 않은 type (2)
python -m budget_app add --date 2024-01-01 --type expense --category 없는카테고리 --amount 1000  # 미등록 카테고리 (1)
```

```bash
python -m budget_app add     # 확인: 대화형에서는 잘못 입력해도 다시 묻는다
                             # 입력: 2024-13-40(오류) → 2024-03-01 → expense → 없는것(오류) → 교통 → -1(오류) → 1500 → (엔터) → (엔터)  (TX-5)
```

### 3) list — 최신순 목록

```bash
python -m budget_app list                  # 확인: 거래일자 최신순 (TX-5, TX-4, TX-3, TX-2, TX-1)
python -m budget_app list --limit 2        # 확인: 최근 2건만 + "전체 5건"
python -m budget_app list --limit 5 --all  # 확인: --limit 과 --all 은 함께 쓸 수 없다 (종료 코드 2)
```

### 4) search — 조건 검색

```bash
python -m budget_app search --from 2024-01-10 --to 2024-01-25   # 확인: 기간 → TX-3, TX-2
python -m budget_app search --category 식비                      # 확인: 카테고리 → TX-1
python -m budget_app search --type income                        # 확인: 타입 → TX-3
python -m budget_app search --q 지하철                           # 확인: 메모 키워드 → TX-2
python -m budget_app search --tag 외식                           # 확인: 태그 → TX-1
python -m budget_app search --type expense --from 2024-01-01 --to 2024-01-31 --category 월세   # 확인: 조건 AND 결합 → TX-4
python -m budget_app search                                      # 확인: 조건이 없으면 오류 (종료 코드 1)
```

### 5) summary — 월별 요약

```bash
python -m budget_app summary --month 2024-01 --top 2   # 확인: 수입 3,000,000 / 지출 532,000 / 잔액 2,468,000, 지출 TOP 2 (월세, 교통)
python -m budget_app summary --month 2023-12           # 확인: 거래가 없는 달은 "데이터 없음"
```

### 6) budget — 예산

```bash
python -m budget_app budget set --month 2024-01 --amount 600000
python -m budget_app summary --month 2024-01   # 확인: 예산 사용률 88.7%, 경고 없음
python -m budget_app budget set --month 2024-01 --amount 500000   # 확인: 같은 달은 덮어쓰기
python -m budget_app summary --month 2024-01   # 확인: 사용률 106.4% + [경고] 예산 32,000원 초과
python -m budget_app budget set --month 2024-01 --amount 0        # 확인: 0 이하 금액은 오류 (종료 코드 1)
```

### 7) category — 카테고리 관리

```bash
python -m budget_app category add --name 여가      # 확인: 등록
python -m budget_app category add --name 식비      # 확인: 이미 있는 이름은 오류
python -m budget_app category list                 # 확인: 5개
python -m budget_app category remove --name 식비   # 확인: 사용 중인 카테고리는 삭제할 수 없다
python -m budget_app category remove --name 여가   # 확인: 사용하지 않는 카테고리는 삭제된다
python -m budget_app category remove --name 없는카테고리   # 확인: 없는 카테고리는 오류
```

### 8) update — 거래 수정 (대화형)

```bash
python -m budget_app update --id TX-1   # 확인: 현재 값을 보여주고 바꿀 항목만 입력 (엔터=유지, -=메모/태그 비우기)
                                        # 입력: (엔터) → (엔터) → (엔터) → 15000 → - → (엔터)   (금액만 15,000 으로, 메모는 비움)
python -m budget_app search --category 식비   # 확인: 금액 15,000, 메모 없음, 태그 "외식" 유지
python -m budget_app summary --month 2024-01  # 확인: 총 지출이 535,000 으로 반영
python -m budget_app update --id TX-999999    # 확인: 없는 id 는 입력 전에 오류 (종료 코드 1)
```

### 9) delete — 거래 삭제

```bash
python -m budget_app delete --id TX-2 --yes   # 확인: 삭제 (--yes 가 없으면 y/N 으로 한 번 더 묻는다)
python -m budget_app list                     # 확인: TX-2 가 목록에서 사라진다
python -m budget_app summary --month 2024-01  # 확인: 총 지출 515,000 (교통 20,000 제외)
python -m budget_app delete --id TX-2 --yes   # 확인: 이미 삭제된 id 는 오류
python -m budget_app delete --id TX-999999 --yes   # 확인: 없는 id 는 오류
```

### 10) import / export — CSV

```bash
cat > import.csv <<'CSV'
date,type,category,amount,memo,tags
2024-02-05,expense,식비,8000,"점심, 회사 근처","외식,점심"
2024-02-25,income,월급,3000000,2월 급여,
2024-02-10,expense,없는카테고리,5000,,
2024-02-11,expense,식비,-100,,
CSV

python -m budget_app import --from import.csv    # 확인: imported=2, skipped=2 (미등록 카테고리/음수 금액 행은 사유와 함께 건너뜀)
python -m budget_app summary --month 2024-02     # 확인: 가져온 데이터가 반영 (수입 3,000,000 / 지출 8,000)
python -m budget_app export --out export_2024_02.csv --month 2024-02                  # 확인: 월 단위 내보내기 (2 records)
cat export_2024_02.csv                           # 확인: 헤더 date,type,category,amount,memo,tags / 쉼표가 든 메모도 그대로 보존
python -m budget_app export --out export_range.csv --from 2024-01-01 --to 2024-01-31 # 확인: 기간 내보내기 (3 records, 수정 반영, 삭제한 교통 제외)
python -m budget_app export --out none.csv       # 확인: --month 또는 --from/--to 가 없으면 오류 (종료 코드 1)
ls data                                          # 확인: 데이터가 파일로 저장되어 있다 (transactions / categories / budgets 등)
```

### 11) 로그 · 종료 코드 · 표 형식

```bash
python -m budget_app --verbose list   # 확인: --verbose 일 때만 [log] 시작/종료/실행 시간이 출력된다
python -m budget_app list; echo $?    # 확인: 정상은 종료 코드 0 / 표 헤더·구분선, 수입 +3,000,000 · 지출 -500,000 (천 단위 구분)
python -m budget_app delete --id TX-999999 --yes; echo $?   # 확인: 오류는 [오류] + [힌트] (스택트레이스 없음), 종료 코드 1
python -m budget_app add; echo $?     # 확인: 입력 중 Ctrl+C 를 누르면 "[중단]" 과 종료 코드 130
```

### 12) 저장 파일 갱신 방식

```bash
wc -l data/transactions.jsonl; wc -c data/transactions.idx   # 현재 줄 수와 idx 크기를 기록 (예: 8줄, 112바이트)
python -m budget_app update --id TX-1                        # 입력: 금액만 16000, 나머지는 엔터
wc -l data/transactions.jsonl; wc -c data/transactions.idx   # 확인: 수정본이 끝에 추가되어 줄 수 +1, idx 크기는 그대로
python -m budget_app delete --id TX-5 --yes
wc -l data/transactions.jsonl; wc -c data/transactions.idx   # 확인: 삭제는 줄 수·idx 크기를 바꾸지 않는다
```

### 13) 백업

```bash
python -m budget_app backup      # 확인: data/backup/<날짜_시각>/ 폴더가 만들어지고 데이터 파일이 복사된다
ls data/backup/*
```

### 14) 반복 내역

```bash
python -m budget_app recurring add --day 31 --type income --category 월급 --amount 3000000 --memo "월급(반복)"   # 확인: 매월 31일 규칙 등록 (RC-1)
python -m budget_app recurring list                        # 확인: 규칙 목록
python -m budget_app recurring apply --month 2024-02       # 확인: 2024-02-29 로 거래 생성 (31일이 없는 달은 말일로 보정)
python -m budget_app recurring apply --month 2024-02       # 확인: 같은 달 재실행은 건너뜀 (중복 생성 방지)
python -m budget_app recurring remove --id RC-1            # 확인: 규칙 삭제
```

### 15) 옛 날짜 거래 추가 — 날짜 인덱스

```bash
cat > old.csv <<'CSV'
date,type,category,amount,memo,tags
2023-12-15,expense,식비,7000,옛거래,
CSV

python -m budget_app import --from old.csv      # 확인: 옛 날짜 거래를 나중에 가져온다 (id 는 TX-9, 가장 큼)
python -m budget_app list --all                 # 확인: id 가 가장 커도 날짜가 가장 오래돼서 맨 아래에 나온다
python -m budget_app update --id TX-9           # 입력: 날짜만 2024-06-01, 나머지는 엔터
python -m budget_app list --limit 1             # 확인: 날짜를 바꾸니 맨 위(가장 최신)로 올라온다
python -m budget_app search --from 2023-12-01 --to 2023-12-31   # 확인: 옛 날짜 구간에는 더 나오지 않는다
python -m budget_app delete --id TX-9 --yes
python -m budget_app compact                    # 확인: 삭제/수정 찌꺼기를 정리해도 목록 순서는 그대로
rm data/transactions.date.idx                   # 날짜 인덱스를 일부러 삭제
python -m budget_app list --limit 3             # 확인: 다음 실행 때 인덱스가 자동으로 다시 만들어지고 같은 결과가 나온다
```

실습이 끝나면 `rm -rf ./data import.csv export_*.csv none.csv old.csv` 로 정리합니다.

---

## 4. 저장 파일 위치/형식

기본 저장 폴더는 `./data` 이며 파일이 6개로 분리되어 있습니다(필수 3종 `transactions` / `categories` / `budgets` + 인덱스 2개 + 보너스 `recurring`).

| 파일 | 역할 | 포맷 | 쓰기 방식 |
|---|---|---|---|
| `data/transactions.jsonl` | 거래 원본 데이터 | 텍스트, JSON 1줄 = 레코드 1개 | **append 전용** (수정도 새 버전을 끝에 추가) |
| `data/transactions.idx` | id → 현재 내용이 있는 위치 | 이진, 슬롯당 16바이트 | 슬롯 덮어쓰기 |
| `data/transactions.date.idx` | (거래일자, id) 날짜순 목록 — 최신순/기간 조회용 | 이진, 항목당 12바이트 | 끝에 추가 또는 병합 후 교체 |
| `data/categories.jsonl` | 카테고리 목록 | 텍스트 JSONL | 전체 재작성 (임시파일 + `os.replace`) |
| `data/budgets.jsonl` | 월별 예산 | 텍스트 JSONL | 전체 재작성 (임시파일 + `os.replace`) |
| `data/recurring.jsonl` | 반복 거래 규칙 (보너스) | 텍스트 JSONL | 전체 재작성 (임시파일 + `os.replace`) |

JSONL 을 쓰는 이유: 메모·태그에 쉼표나 따옴표가 들어가도 안전하고, 한 줄이 한 레코드라 끝에 추가하기 쉽습니다.
CSV 는 `import`/`export` 에서만 씁니다.

### 거래 데이터 파일 관리

거래는 세 파일이 역할을 나눠 관리합니다.

| 파일 | 역할 |
|---|---|
| `transactions.jsonl` | 거래 **내용** 원본. 항상 끝에만 덧붙인다(수정 전 내용도 남는다). |
| `transactions.idx` | **id → 현재 내용의 위치**. 슬롯 N 이 id N 이고, 삭제된 id 는 `(0, 0)`. |
| `transactions.date.idx` | **(거래일자, id) 날짜순 목록**. 최신순 조회에 쓴다. |

**최신 거래의 기준은 `(거래일자, id)`** 입니다. 거래일자가 늦은 것이 최신이고, 같은 날짜면 id 가 큰(나중에 등록한) 것이 최신입니다.
`list` 는 날짜순 목록을 **맨 끝부터 거꾸로** 읽으면서 해당 거래만 `jsonl` 에서 읽고, `--limit` 개수가 차면 멈춥니다(전체를 읽지 않음).
옛 날짜 거래를 나중에 import 해도 id 는 커지지만 날짜순 목록에서는 제 날짜 위치에 들어가므로 `list` 순서가 어긋나지 않습니다.

**동작별로 파일이 바뀌는 방식**

| 동작 | `transactions.jsonl` | `transactions.idx` | `transactions.date.idx` |
|---|---|---|---|
| **add** | 끝에 한 줄 추가 | 슬롯 추가 | 날짜가 가장 늦으면 끝에 추가, 아니면 제 위치에 끼워 넣기 |
| **import** | 유효한 행을 한 번에 끝에 추가 | 슬롯을 한 번에 추가 | 새 항목을 정렬해 한 번에 병합 |
| **update** | 수정본 전체를 끝에 추가 (옛 줄은 남음) | 그 id 슬롯을 새 위치로 덮어씀 | 날짜가 바뀐 경우에만 새 항목 추가 (옛 항목은 읽을 때 무시) |
| **delete** | 변화 없음 | 그 id 슬롯을 `(0, 0)` 으로 | 변화 없음 (삭제된 슬롯은 읽을 때 무시) |
| **compact** | 살아있는 최신본만 새 파일로 옮겨 씀 | 슬롯 번호 그대로, 위치만 갱신 | 살아있는 거래로 처음부터 다시 만듦 |

`date.idx` 는 `jsonl`/`idx` 에서 언제든 다시 만들 수 있어서, 파일이 없거나 손상되면 다음 실행 때 자동으로 재생성됩니다.
update 와 delete 로 남은 옛 줄·옛 항목은 읽을 때 건너뛰고, `compact` 가 정리합니다(id 는 바뀌지 않고 삭제된 번호는 재사용되지 않습니다).

### compact

`update` 와 `delete` 가 쌓이면 `transactions.jsonl` 에 더 이상 쓰이지 않는 옛 줄이 남아 파일이 커집니다.
`python -m budget_app compact` 로 정리하며, `update`/`delete` 후 옛 줄이 절반을 넘으면 CLI 가 안내합니다(자동 실행은 하지 않음).

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
- `export` → `import` 왕복이 가능합니다.

---

## 6. 주요 정책

| 항목 | 결정 |
|---|---|
| 저장 포맷 | **JSONL** |
| `update` 방식 | **대화형**으로 고정 — `update --id TX-3` 후 항목별 입력 (엔터=유지, 메모/태그는 `-` 입력 시 비움, 검증 실패 시 재입력) |
| 카테고리 초기화 | 카테고리가 비어 있으면 `add` 를 막고 `category add` 를 안내 (기본 카테고리를 만들지 않음) |
| 카테고리 삭제 | 사용 중이면 **삭제를 막음** |
| 예산 재설정 | 같은 달은 **덮어쓰기** |
| 거래 id | `TX-1`, `TX-2` … 삭제해도 **재사용·재번호하지 않음** (번호가 비는 것은 정상) |
| 목록 번호 | `list`/`search` 왼쪽 `#` 은 화면용 순번이고, 수정/삭제에는 `TX-N` 을 사용 |
| 검증 실패 | 대화형은 **다시 입력**, 옵션 방식은 **오류 + 힌트 후 종료(코드 1)** |

### 종료 코드

| 코드 | 의미 |
|---|---|
| `0` | 정상 종료 |
| `1` | 검증 실패 / 데이터 없음 / 입출력 오류 (`[오류]` + `[힌트]` 출력) |
| `2` | 사용법 오류 (잘못된 옵션 등) |
| `130` | 사용자가 Ctrl+C / EOF 로 입력을 취소 |

오류는 스택트레이스 대신 항상 아래 형식으로 출력됩니다(원인 추적이 필요하면 `--verbose`).

```
[오류] 등록되지 않은 카테고리입니다: '외식비'
[힌트] 등록된 카테고리: 식비, 교통, 월급 / 새로 만들려면 category add 를 사용하세요.
```

---

## 7. 모듈 구조

```
budget_app/
├── __main__.py     python -m budget_app 진입점
├── cli.py          argparse 파서 정의 + main (커맨드 ↔ 핸들러 연결)
├── context.py      저장소/서비스 조립
├── console.py      핸들러 공용 입출력(오류 출력, id 파싱, 재입력 루프, 거래 표 출력)
├── commands/       커맨드 핸들러 (서브커맨드 1개 = 함수 1개)
│   ├── transaction.py  add / list / search / update / delete
│   ├── report.py       summary / budget
│   ├── category.py     category add / list / remove
│   ├── data.py         import / export / compact / backup
│   └── recurring.py    recurring add / list / remove / apply
├── validators.py   입력 검증(날짜/월/타입/금액/일자/태그)
├── services.py     거래 검색·월별 요약, 예산 계산, 반복 규칙 (비즈니스 로직)
├── file_services.py  CSV 가져오기/내보내기, 백업
├── repository.py   거래 저장소 (jsonl 로그 + id 인덱스 + 날짜 인덱스)
├── stores.py       카테고리 / 예산 / 반복 규칙 저장소 (JSONL)
├── models.py       Transaction 등 dataclass, 커스텀 예외
├── formatter.py    표 정렬(한글 폭 계산), 금액/막대 포맷
└── decorators.py   @command = 예외 처리 + 로그 + 시간 측정
tests/
└── test_requirement_checklist.py
```

계층은 **모델 → 저장소 → 서비스 → 커맨드 핸들러/CLI** 로 나뉩니다. 파일 형식은 `repository`/`stores` 만 알고,
규칙은 `services` 가, 사용자 입출력은 `commands/`·`console.py` 가 담당합니다.
공통 처리(예외를 `[오류]`/`[힌트]` 로 바꾸고 종료 코드 정하기, 로그, 시간 측정)는 데코레이터 `@command` 하나로 모든 커맨드에 적용합니다.

---

## 8. 알려진 한계

- **크래시 안전성**: 파일을 쓰는 도중 프로세스가 강제 종료되면 인덱스가 어긋날 수 있습니다(완전한 복구 기능은 없음).
  날짜 인덱스는 `compact` 로 다시 만들 수 있습니다.
- **파일 증가**: `update` 가 쌓이면 `transactions.jsonl` 이 커지고(`compact` 로 정리), `transactions.idx` 는 발급한 id 수 × 16바이트라
  삭제해도 줄지 않습니다.
- **동시 실행**: 같은 `--data-dir` 에 여러 프로세스를 동시에 실행하는 경우는 고려하지 않았습니다(1인 사용 전제).

---

## 9. 보너스 구현 현황

- ✅ **백업**: `backup` — `data/backup/YYYYmmdd_HHMMSS/` 로 데이터 파일 복사
- ✅ **반복 내역**: `recurring add/list/remove/apply` — 매월 반복 규칙 등록 후 `apply --month` 로
  해당 월 거래 생성(같은 달 중복 적용 방지, 말일 보정: 매월 31일 규칙 → 2024-02-29)
- ✅ **출력 포맷 테이블 정렬**: `formatter.py` — 외부 라이브러리 없이 전각 문자 폭까지 계산해 정렬
- ✅ **저장 원자성 강화**: 카테고리/예산/반복규칙 파일도 임시파일 + `os.replace` 로 교체
