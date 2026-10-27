# AllowanceTracker — 콘솔 가계부 (`budget_app`)

표준 라이브러리만 사용하는 CLI 가계부입니다. 거래를 기록/검색/수정/삭제하고, 월별 요약과 예산 사용률을 확인하며, CSV 로 주고받을 수 있습니다.

## 1. 환경 구축

- Python **3.10 이상**, 외부 라이브러리 없음(`pip install` 불필요)

```bash
git clone https://github.com/aha645/AllowanceTracker.git
cd AllowanceTracker
python --version                           # 3.10 이상인지 확인
python -m budget_app --help                # 전체 도움말 (서브커맨드: python -m budget_app <command> --help)
```

`add`·`list` 같은 하위 명령을 처음 실행할 때 현재 폴더에 `data/` 폴더와 아래 파일들이 자동으로 만들어집니다(`--data-dir PATH` 로 저장 폴더 변경 가능). `--help` 만 실행하거나 하위 명령 없이 실행하면 아무것도 만들어지지 않습니다.

```text
data/
├── transactions.jsonl       거래 내용 원본. 한 줄이 거래 1건(JSON)이며 끝에만 덧붙임
├── transactions.idx         거래 id → jsonl 안의 위치 (조회·수정·삭제용 색인)
├── transactions.date.idx    (거래일자, id) 날짜순 목록 (최신순·기간 조회용 색인)
├── categories.jsonl         등록한 카테고리 목록과 카테고리별 사용 건수
├── budgets.jsonl            월별 예산
└── recurring.jsonl          반복 거래 규칙 (보너스)
```

- 구성: `cli`(명령 연결) → `commands/`(기능별 핸들러) → `services`(규칙) → `transaction_store`·`stores`(파일 저장), 공통 처리는 데코레이터 `@command`. 모듈별 역할은 4부 "코드 구성" 참고.

### 테스트 실행

```bash
python -m unittest discover -s tests -v    # 요구사항 17개 + 저장소 복구·10만 건 15개

# 090(import) 까지만: 000 부터 그 번호까지 함께 지정 (-k 는 이름에 그 글자가 든 테스트를 고름)
python -m unittest tests.test_requirement_checklist -v -k 000 -k 010 -k 020 -k 030 -k 040 -k 050 -k 060 -k 070 -k 080 -k 090
```

`-k 000` 의 `000` 은 아래 함수 이름의 번호입니다. `-k` 는 이름에 그 글자가 들어간 테스트만 실행합니다. 테스트는 번호 순서대로 실행되고 앞 테스트가 `tests/data/` 에 만든 데이터를 뒤 테스트가 이어받으므로, 일부만 실행할 때도 `000` 부터 함께 지정하세요.


| `-k` | 함수 이름 | 테스트 내용 |
|---|---|---|
| `000` | `test_000_reset_data` | `tests/data/` 를 비워 데이터를 처음 상태로 만든다 |
| `010` | `test_010_add` | 카테고리가 없으면 `add` 를 막는지, 카테고리 4개 등록 후 `category list` 로 확인, 대화형·옵션 방식으로 거래 등록 후 `list` 에서 날짜·타입·카테고리·금액·메모(띄어쓰기 포함)·태그가 입력한 그대로인지, 날짜·음수·0 금액·type·미등록 카테고리·위치가 틀린 쉼표(`1,2,3`·`600,00`)를 사유와 함께 거부하고 거래 수가 그대로인지, 천 단위 쉼표 금액(`500,000`) 저장, 대화형에서 잘못 입력하면 다시 묻고 고친 값으로 저장되는지, 엔터로 기본값을 고르면 터미널에서만 그 값을 입력 줄에 다시 보여주는지, 카테고리별 사용 건수 |
| `020` | `test_020_list` | 거래일자 최신순 출력, `--limit 2` 로 최근 2건만, `--limit` 과 `--all` 을 함께 쓰면 거부 |
| `030` | `test_030_search` | 기간·카테고리·타입·메모 키워드·태그 조건과 여러 조건의 AND 결합, 조건이 하나도 없으면 오류 |
| `040` | `test_040_summary` | 2024-01 총 수입·총 지출·잔액, 지출 큰 순서 TOP 2, 거래가 없는 달은 "데이터 없음" |
| `050` | `test_050_budget` | 예산 설정(`600,000` 처럼 쉼표 입력)과 사용률(88.7%), 같은 달 덮어쓰기와 초과 경고(106.4%, 32,000원 초과), 0원 예산 거부, 매 단계 `budget list` 로 저장된 예산이 한 줄이고 거부 뒤에도 그대로인지 |
| `060` | `test_060_category` | 카테고리 추가·중복 차단·목록, 사용 중인 카테고리 삭제 거부, 미사용 삭제, 없는 이름 오류(삭제·거부 뒤마다 `category list` 로 남은 목록 확인), 사용 건수가 실제 거래 수와 같은지, 건수 0 이면 거래를 훑지 않고 바로 삭제 |
| `070` | `test_070_update` | 대화형 수정(엔터는 유지, `-` 는 비움)으로 금액·메모 변경 후 검색·요약에 반영, 없는 id 오류 |
| `080` | `test_080_delete` | `--yes` 없이 터미널에서 실행하면 확인을 묻고 N 이면 지우지 않는지, 삭제 후 목록에서 그 거래만 빠지고 요약·카테고리 사용 건수에 반영되는지, 이미 삭제한 id·없는 id 오류 |
| `090` | `test_090_import` | CSV 가져오기(정상 2건이 쉼표 든 메모·여러 태그까지 그대로 저장, 잘못된 행 2건은 행 번호·사유와 함께 건너뜀), 읽을 수 없는 파일(UTF-8 아님·닫히지 않은 따옴표)은 아무것도 저장하지 않음, 오류 사유 20건 상한, 메모리 부족 안내, 실패한 import 뒤 거래 수가 그대로인지 |
| `095` | `test_095_export` | 월·기간 내보내기와 고정 헤더, 쉼표가 든 메모 보존, 삭제한 거래 제외, 도중에 멈춰도 원래 파일 유지, 조건 없으면 오류, 저장 파일 3개 존재 |
| `100` | `test_100_applied_logging_and_exit_codes` | `--verbose` 일 때만 로그·실행 시간 출력, 데코레이터가 함수 이름·설명 보존, 종료 코드 0·1·130 과 스택트레이스 미노출 |
| `110` | `test_110_applied_table_format` | 표 머리글·구분선, 수입 `+`·지출 `-` 부호와 천 단위 구분, 예산 막대 |
| `120` | `test_120_applied_atomic_storage` | 파일 교체 실패 시 원본 유지, update·delete 때 파일이 바뀌는 방식, 카테고리 건수가 실제보다 적어지지 않는지와 다시 세기, 덜 써진 위치 칸 조각 정리, 중단된 compact 되돌리기·마무리 |
| `130` | `test_130_applied_backup` | `backup` 이 날짜·시각 이름의 폴더에 저장 파일 3개를 그대로 복사하는지 |
| `140` | `test_140_applied_recurring` | 반복 거래 추가 후 `recurring list` 의 규칙 내용, 31일 규칙이 윤년 2월 29일 거래로 실제 저장되는지, 같은 달 재적용 건너뜀(거래 수 그대로), 거래 저장 뒤 기록 전에 멈춰도 중복 생성 안 함, 규칙 삭제 |
| `150` | `test_150_applied_date_index` | 옛 날짜 import·add 가 날짜 순서 위치에 들어가는지, 날짜 수정·삭제 반영, `--limit` 만큼만 읽기, compact·파일 삭제 후 재생성, 저장·import 중단 복구, 날짜 인덱스 손상 감지 |

## 2. 10대 기능 실행 결과

아래는 **위에서 아래로 순서대로 실제 실행한 결과**입니다(앞 단계에서 만든 데이터를 뒤 단계가 이어받음). 각 항목의 ▶ 를 누르면 명령과 결과가 펼쳐집니다.
직접 따라 하려면 먼저 `rm -rf ./data` 로 데이터를 비우고(⚠ 기존 데이터 삭제) 같은 명령을 순서대로 입력하세요.
`(종료 코드 N)` 은 오류로 끝난 경우에만 표시했습니다(정상은 0).

<details>
<summary><b>1. add — 거래 추가</b></summary>

카테고리가 없으면 차단 → 카테고리 등록 → `category list` 로 확인 → 대화형/옵션 방식 등록 → 잘못된 입력 거부 → 대화형 재입력

```text
$ python -m budget_app add
[오류] 등록된 카테고리가 없습니다.
[힌트] category add 로 먼저 등록하세요. 예: python -m budget_app category add --name "식비"
(종료 코드 1)

$ python -m budget_app category add --name 식비
[저장 완료] 카테고리 '식비' 추가

$ python -m budget_app category add --name 교통
[저장 완료] 카테고리 '교통' 추가

$ python -m budget_app category add --name 월급
[저장 완료] 카테고리 '월급' 추가

$ python -m budget_app category add --name 월세
[저장 완료] 카테고리 '월세' 추가

$ python -m budget_app category list
#  카테고리  등록일시
-  --------  -------------------
1  식비      2024-02-01T09:00:00
2  교통      2024-02-01T09:00:00
3  월급      2024-02-01T09:00:00
4  월세      2024-02-01T09:00:00

[완료] 4개

$ python -m budget_app add
[안내] 거래 정보를 입력하세요. (Ctrl+C 로 취소)
날짜 (YYYY-MM-DD, 엔터=2024-02-01): 2024-01-05
타입 (income/expense, 엔터=expense): expense
등록된 카테고리: 1) 식비, 2) 교통, 3) 월급, 4) 월세
카테고리 (번호 또는 이름): 식비
금액 (양의 정수): 12000
메모 (선택, 엔터=건너뛰기, 따옴표 없이 입력): 점심
태그 (선택, 엔터=건너뛰기, 쉼표 구분): 외식
[저장 완료] id=TX-1

$ python -m budget_app add --date 2024-01-10 --type expense --category 교통 --amount 20000 --memo 지하철
[저장 완료] id=TX-2

$ python -m budget_app add --date 2024-01-25 --type income --category 월급 --amount 3000000 --memo "1월 급여"
[저장 완료] id=TX-3

$ python -m budget_app add --date 2024-01-31 --type expense --category 월세 --amount 500,000 --memo "1월 월세"
[저장 완료] id=TX-4

$ python -m budget_app add --date 2024-13-40 --type expense --category 식비 --amount 1000
[오류] 날짜 형식이 올바르지 않습니다: '2024-13-40'
[힌트] YYYY-MM-DD 형식으로 입력하세요. 예: 2024-01-15
(종료 코드 1)

$ python -m budget_app add --date 2024-01-01 --type expense --category 식비 --amount -500
[오류] 금액은 0보다 커야 합니다: -500
[힌트] 지출/수입 구분은 --type 으로 하고, 금액은 항상 양수로 입력하세요.
(종료 코드 1)

$ python -m budget_app add --date 2024-01-01 --type expense --category 식비 --amount 1,2,3
[오류] 금액의 쉼표 위치가 올바르지 않습니다: '1,2,3'
[힌트] 쉼표는 세 자리마다 넣거나 빼고 입력하세요. 예: 600,000 또는 600000
(종료 코드 1)

$ python -m budget_app add --date 2024-01-01 --type transfer --category 식비 --amount 1000
[오류] argument --type: invalid choice: 'transfer' (choose from 'income', 'expense')
[힌트] `python -m budget_app add --help` 로 사용법과 허용 값을 확인하세요.
(종료 코드 2)

$ python -m budget_app add --date 2024-01-01 --type expense --category 없는카테고리 --amount 1000
[오류] 등록되지 않은 카테고리입니다: '없는카테고리'
[힌트] 등록된 카테고리: 식비, 교통, 월급, 월세 / 새로 만들려면 category add 를 사용하세요.
(종료 코드 1)

$ python -m budget_app add
[안내] 거래 정보를 입력하세요. (Ctrl+C 로 취소)
날짜 (YYYY-MM-DD, 엔터=2024-02-01): 2024-13-40
[오류] 날짜 형식이 올바르지 않습니다: '2024-13-40'
[힌트] YYYY-MM-DD 형식으로 입력하세요. 예: 2024-01-15
날짜 (YYYY-MM-DD, 엔터=2024-02-01): 2024-03-01
타입 (income/expense, 엔터=expense): expense
등록된 카테고리: 1) 식비, 2) 교통, 3) 월급, 4) 월세
카테고리 (번호 또는 이름): 없는것
[오류] 등록되지 않은 카테고리입니다: '없는것'
[힌트] 등록된 카테고리: 식비, 교통, 월급, 월세 / 새로 만들려면 category add 를 사용하세요.
카테고리 (번호 또는 이름): 교통
금액 (양의 정수): -1
[오류] 금액은 0보다 커야 합니다: -1
[힌트] 지출/수입 구분은 --type 으로 하고, 금액은 항상 양수로 입력하세요.
금액 (양의 정수): 1500
메모 (선택, 엔터=건너뛰기, 따옴표 없이 입력): 
태그 (선택, 엔터=건너뛰기, 쉼표 구분): 
[저장 완료] id=TX-5
```

</details>

<details>
<summary><b>2. list — 거래 목록(최신순)</b></summary>

거래일자 최신순, `--limit`, `--limit` 과 `--all` 은 함께 쓸 수 없음

```text
$ python -m budget_app list
#  id    날짜        타입     카테고리        금액  메모      태그
-  ----  ----------  -------  --------  ----------  --------  ----
1  TX-5  2024-03-01  expense  교통          -1,500
2  TX-4  2024-01-31  expense  월세        -500,000  1월 월세
3  TX-3  2024-01-25  income   월급      +3,000,000  1월 급여
4  TX-2  2024-01-10  expense  교통         -20,000  지하철
5  TX-1  2024-01-05  expense  식비         -12,000  점심      외식

[완료] 5건 출력

$ python -m budget_app list --limit 2
#  id    날짜        타입     카테고리      금액  메모      태그
-  ----  ----------  -------  --------  --------  --------  ----
1  TX-5  2024-03-01  expense  교통        -1,500
2  TX-4  2024-01-31  expense  월세      -500,000  1월 월세

[완료] 2건 출력 / 전체 5건

$ python -m budget_app list --limit 5 --all
[오류] argument --all: not allowed with argument --limit
[힌트] `python -m budget_app list --help` 로 사용법과 허용 값을 확인하세요.
(종료 코드 2)
```

</details>

<details>
<summary><b>3. search — 거래 검색</b></summary>

기간 / 카테고리 / 타입 / 메모 키워드 / 태그 / 조건 AND 결합 / 조건 없음은 오류

```text
$ python -m budget_app search --from 2024-01-10 --to 2024-01-25
#  id    날짜        타입     카테고리        금액  메모      태그
-  ----  ----------  -------  --------  ----------  --------  ----
1  TX-3  2024-01-25  income   월급      +3,000,000  1월 급여
2  TX-2  2024-01-10  expense  교통         -20,000  지하철

[완료] 2건 검색됨

$ python -m budget_app search --category 식비
#  id    날짜        타입     카테고리     금액  메모  태그
-  ----  ----------  -------  --------  -------  ----  ----
1  TX-1  2024-01-05  expense  식비      -12,000  점심  외식

[완료] 1건 검색됨

$ python -m budget_app search --type income
#  id    날짜        타입    카테고리        금액  메모      태그
-  ----  ----------  ------  --------  ----------  --------  ----
1  TX-3  2024-01-25  income  월급      +3,000,000  1월 급여

[완료] 1건 검색됨

$ python -m budget_app search --q 지하철
#  id    날짜        타입     카테고리     금액  메모    태그
-  ----  ----------  -------  --------  -------  ------  ----
1  TX-2  2024-01-10  expense  교통      -20,000  지하철

[완료] 1건 검색됨

$ python -m budget_app search --tag 외식
#  id    날짜        타입     카테고리     금액  메모  태그
-  ----  ----------  -------  --------  -------  ----  ----
1  TX-1  2024-01-05  expense  식비      -12,000  점심  외식

[완료] 1건 검색됨

$ python -m budget_app search --type expense --from 2024-01-01 --to 2024-01-31 --category 월세
#  id    날짜        타입     카테고리      금액  메모      태그
-  ----  ----------  -------  --------  --------  --------  ----
1  TX-4  2024-01-31  expense  월세      -500,000  1월 월세

[완료] 1건 검색됨

$ python -m budget_app search
[오류] 검색 조건이 하나도 지정되지 않았습니다.
[힌트] --from/--to/--month/--category/--type/--q/--tag 중 최소 하나를 지정하세요.
(종료 코드 1)
```

</details>

<details>
<summary><b>4. summary — 월별 요약</b></summary>

총 수입/지출/잔액, 카테고리별 지출 TOP N, 거래가 없는 달은 "데이터 없음"

```text
$ python -m budget_app summary --month 2024-01 --top 2
[2024-01 요약]
  총 수입  3,000,000원
  총 지출  532,000원
  잔액     2,468,000원  (거래 4건)

[카테고리별 지출 TOP 2]
순위  카테고리       지출   비중
----  --------  ---------  -----
   1  월세      500,000원  94.0%
   2  교통       20,000원   3.8%

[예산]
  설정되지 않음
[힌트] budget set --month 2024-01 --amount <금액> 으로 예산을 설정할 수 있습니다.

$ python -m budget_app summary --month 2023-12
[2023-12 요약]
  데이터 없음 (해당 월에 등록된 거래가 없습니다)
[힌트] `add` 또는 `import --from <csv>` 로 거래를 등록하세요.
```

</details>

<details>
<summary><b>5. budget — 예산 설정/조회</b></summary>

예산 설정 → summary 사용률 → `budget list` 로 저장 확인 → 덮어쓰기 → 초과 경고 → 0원은 오류 → 거부 뒤에도 예산 그대로

```text
$ python -m budget_app budget set --month 2024-01 --amount 600,000
[저장 완료] 2024-01 예산 600,000원

$ python -m budget_app summary --month 2024-01 --top 1
[2024-01 요약]
  총 수입  3,000,000원
  총 지출  532,000원
  잔액     2,468,000원  (거래 4건)

[카테고리별 지출 TOP 1]
순위  카테고리       지출   비중
----  --------  ---------  -----
   1  월세      500,000원  94.0%

[예산]
  예산     600,000원
  사용     532,000원 (88.7%) [##################..]
  잔여     68,000원

$ python -m budget_app budget list
월            예산       사용  사용률  상태
-------  ---------  ---------  ------  ----
2024-01  600,000원  532,000원   88.7%

[완료] 1개

$ python -m budget_app budget set --month 2024-01 --amount 500000
[저장 완료] 2024-01 예산 500,000원
[안내] 기존 예산 600,000원을 덮어썼습니다.

$ python -m budget_app summary --month 2024-01 --top 1
[2024-01 요약]
  총 수입  3,000,000원
  총 지출  532,000원
  잔액     2,468,000원  (거래 4건)

[카테고리별 지출 TOP 1]
순위  카테고리       지출   비중
----  --------  ---------  -----
   1  월세      500,000원  94.0%

[예산]
  예산     500,000원
  사용     532,000원 (106.4%) [####################]
  [경고] 예산을 32,000원 초과했습니다!

$ python -m budget_app budget set --month 2024-01 --amount 0
[오류] 금액은 0보다 커야 합니다: 0
[힌트] 지출/수입 구분은 --type 으로 하고, 금액은 항상 양수로 입력하세요.
(종료 코드 1)

$ python -m budget_app budget list
월            예산       사용  사용률  상태
-------  ---------  ---------  ------  ----
2024-01  500,000원  532,000원  106.4%  초과

[완료] 1개
```

</details>

<details>
<summary><b>6. category — 카테고리 관리</b></summary>

등록 / 중복 차단 / 목록 / 사용 중인 카테고리는 삭제 차단 / 미사용 삭제 / 없는 카테고리 (삭제를 시도할 때마다 `category list` 로 남은 목록 확인)

```text
$ python -m budget_app category add --name 여가
[저장 완료] 카테고리 '여가' 추가

$ python -m budget_app category add --name 식비
[오류] 카테고리 '식비' 은(는) 이미 등록되어 있습니다.
[힌트] category list 로 등록된 목록을 확인하세요.
(종료 코드 1)

$ python -m budget_app category list
#  카테고리  등록일시
-  --------  -------------------
1  식비      2024-02-01T09:00:00
2  교통      2024-02-01T09:00:00
3  월급      2024-02-01T09:00:00
4  월세      2024-02-01T09:00:00
5  여가      2024-02-01T09:00:00

[완료] 5개

$ python -m budget_app category remove --name 식비
[오류] 카테고리 '식비' 은(는) 사용 중이라 삭제할 수 없습니다 (예: TX-1 2024-01-05).
[힌트] search --category "식비" 로 확인 후, 해당 거래를 update/delete 한 뒤 다시 시도하세요.
(종료 코드 1)

$ python -m budget_app category list
#  카테고리  등록일시
-  --------  -------------------
1  식비      2024-02-01T09:00:00
2  교통      2024-02-01T09:00:00
3  월급      2024-02-01T09:00:00
4  월세      2024-02-01T09:00:00
5  여가      2024-02-01T09:00:00

[완료] 5개

$ python -m budget_app category remove --name 여가
[삭제 완료] 카테고리 '여가'

$ python -m budget_app category list
#  카테고리  등록일시
-  --------  -------------------
1  식비      2024-02-01T09:00:00
2  교통      2024-02-01T09:00:00
3  월급      2024-02-01T09:00:00
4  월세      2024-02-01T09:00:00

[완료] 4개

$ python -m budget_app category remove --name 없는카테고리
[오류] 카테고리 '없는카테고리' 을(를) 찾을 수 없습니다.
[힌트] category list 로 목록을 확인하세요.
(종료 코드 1)
```

</details>

<details>
<summary><b>7. update — 거래 수정(대화형)</b></summary>

현재 값을 보여주고 바꿀 항목만 입력 (엔터=유지, `-`=메모/태그 비우기) → 요약에 반영 → 없는 id

```text
$ python -m budget_app update --id TX-1
[현재 값]
#  id    날짜        타입     카테고리     금액  메모  태그
-  ----  ----------  -------  --------  -------  ----  ----
1  TX-1  2024-01-05  expense  식비      -12,000  점심  외식
[안내] 바꿀 항목만 입력하세요. 엔터=기존 값 유지, 메모/태그는 '-' 입력 시 비움 (Ctrl+C 로 취소)
날짜 [2024-01-05]: 
타입 [expense]: 
등록된 카테고리: 1) 식비, 2) 교통, 3) 월급, 4) 월세
카테고리 [식비] (번호 또는 이름): 
금액 [12000]: 15000
메모 [점심] (따옴표 없이 입력): -
태그 [외식] (쉼표 구분): 
[수정 완료] id=TX-1
필드    이전   이후
------  -----  -----
amount  12000  15000
memo    점심

$ python -m budget_app search --category 식비
#  id    날짜        타입     카테고리     금액  메모  태그
-  ----  ----------  -------  --------  -------  ----  ----
1  TX-1  2024-01-05  expense  식비      -15,000        외식

[완료] 1건 검색됨

$ python -m budget_app summary --month 2024-01 --top 1
[2024-01 요약]
  총 수입  3,000,000원
  총 지출  535,000원
  잔액     2,465,000원  (거래 4건)

[카테고리별 지출 TOP 1]
순위  카테고리       지출   비중
----  --------  ---------  -----
   1  월세      500,000원  93.5%

[예산]
  예산     500,000원
  사용     535,000원 (107.0%) [####################]
  [경고] 예산을 35,000원 초과했습니다!

$ python -m budget_app update --id TX-999999
[오류] TX-999999 거래를 찾을 수 없습니다.
[힌트] list 명령으로 존재하는 id 를 확인하세요.
(종료 코드 1)
```

</details>

<details>
<summary><b>8. delete — 거래 삭제</b></summary>

`--yes` 없이 실행하면 확인 질문(N 이면 취소) → 삭제 → 목록/요약에서 제외 → 이미 삭제했거나 없는 id 는 오류

```text
$ python -m budget_app delete --id TX-2
#  id    날짜        타입     카테고리     금액  메모    태그
-  ----  ----------  -------  --------  -------  ------  ----
1  TX-2  2024-01-10  expense  교통      -20,000  지하철
위 거래를 삭제할까요? (y/N): N
[안내] 삭제를 취소했습니다.

$ python -m budget_app delete --id TX-2 --yes
[삭제 완료] id=TX-2 (2024-01-10 교통 20,000원)
[안내] 삭제된 id 는 재사용되지 않습니다(번호 gap 은 정상입니다).

$ python -m budget_app list
#  id    날짜        타입     카테고리        금액  메모      태그
-  ----  ----------  -------  --------  ----------  --------  ----
1  TX-5  2024-03-01  expense  교통          -1,500
2  TX-4  2024-01-31  expense  월세        -500,000  1월 월세
3  TX-3  2024-01-25  income   월급      +3,000,000  1월 급여
4  TX-1  2024-01-05  expense  식비         -15,000            외식

[완료] 4건 출력

$ python -m budget_app summary --month 2024-01 --top 1
[2024-01 요약]
  총 수입  3,000,000원
  총 지출  515,000원
  잔액     2,485,000원  (거래 3건)

[카테고리별 지출 TOP 1]
순위  카테고리       지출   비중
----  --------  ---------  -----
   1  월세      500,000원  97.1%

[예산]
  예산     500,000원
  사용     515,000원 (103.0%) [####################]
  [경고] 예산을 15,000원 초과했습니다!

$ python -m budget_app delete --id TX-2 --yes
[오류] TX-2 거래를 찾을 수 없습니다.
[힌트] list 명령으로 존재하는 id 를 확인하세요.
(종료 코드 1)

$ python -m budget_app delete --id TX-999999 --yes
[오류] TX-999999 거래를 찾을 수 없습니다.
[힌트] list 명령으로 존재하는 id 를 확인하세요.
(종료 코드 1)
```

</details>

<details>
<summary><b>9. import — CSV 가져오기</b></summary>

유효한 행만 등록하고 잘못된 행은 사유와 함께 건너뜀 → `list` 에서 쉼표 든 메모·여러 태그까지 그대로인지 확인 → 요약에 반영

```text
$ cat > import.csv <<'EOF'
date,type,category,amount,memo,tags
2024-02-05,expense,식비,8000,"점심, 회사 근처","외식,점심"
2024-02-25,income,월급,3000000,2월 급여,
2024-02-10,expense,없는카테고리,5000,,
2024-02-11,expense,식비,-100,,
EOF

$ python -m budget_app import --from import.csv
[완료] import.csv → imported=2, skipped=2

[건너뛴 행]
행  사유
--  --------------------------------------------
 4  등록되지 않은 카테고리입니다: '없는카테고리'
 5  금액은 0보다 커야 합니다: -100

$ python -m budget_app list --all
#  id    날짜        타입     카테고리        금액  메모             태그
-  ----  ----------  -------  --------  ----------  ---------------  ---------
1  TX-5  2024-03-01  expense  교통          -1,500
2  TX-7  2024-02-25  income   월급      +3,000,000  2월 급여
3  TX-6  2024-02-05  expense  식비          -8,000  점심, 회사 근처  외식,점심
4  TX-4  2024-01-31  expense  월세        -500,000  1월 월세
5  TX-3  2024-01-25  income   월급      +3,000,000  1월 급여
6  TX-1  2024-01-05  expense  식비         -15,000                   외식

[완료] 6건 출력

$ python -m budget_app summary --month 2024-02 --top 1
[2024-02 요약]
  총 수입  3,000,000원
  총 지출  8,000원
  잔액     2,992,000원  (거래 2건)

[카테고리별 지출 TOP 1]
순위  카테고리     지출    비중
----  --------  -------  ------
   1  식비      8,000원  100.0%

[예산]
  설정되지 않음
[힌트] budget set --month 2024-02 --amount <금액> 으로 예산을 설정할 수 있습니다.
```

</details>

<details>
<summary><b>10. export — CSV 내보내기</b></summary>

월 단위 / 기간 단위 내보내기(수정·삭제 반영), 조건이 없으면 오류

```text
$ python -m budget_app export --out export_2024_02.csv --month 2024-02
[완료] export_2024_02.csv (2 records)

$ cat export_2024_02.csv
date,type,category,amount,memo,tags
2024-02-25,income,월급,3000000,2월 급여,
2024-02-05,expense,식비,8000,"점심, 회사 근처","외식,점심"

$ python -m budget_app export --out export_range.csv --from 2024-01-01 --to 2024-01-31
[완료] export_range.csv (3 records)

$ cat export_range.csv
date,type,category,amount,memo,tags
2024-01-31,expense,월세,500000,1월 월세,
2024-01-25,income,월급,3000000,1월 급여,
2024-01-05,expense,식비,15000,,외식

$ python -m budget_app export --out none.csv
[오류] 내보내기 조건이 없습니다.
[힌트] --month YYYY-MM 또는 --from YYYY-MM-DD --to YYYY-MM-DD 를 지정하세요.
(종료 코드 1)

$ ls data
budgets.jsonl
categories.jsonl
recurring.jsonl
transactions.date.idx
transactions.idx
transactions.jsonl
```

</details>

<details>
<summary><b>공통 — 예외 처리 · 종료 코드 · 로그</b></summary>

오류는 스택트레이스 없이 `[오류]`+`[힌트]`, 정상 0 / 오류 1 / 사용법 오류 2 / Ctrl+C 130, `--verbose` 일 때만 로그

```text
$ python -m budget_app delete --id TX-999999 --yes
[오류] TX-999999 거래를 찾을 수 없습니다.
[힌트] list 명령으로 존재하는 id 를 확인하세요.
(종료 코드 1)

$ python -m budget_app add --type transfer
[오류] argument --type: invalid choice: 'transfer' (choose from 'income', 'expense')
[힌트] `python -m budget_app add --help` 로 사용법과 허용 값을 확인하세요.
(종료 코드 2)

$ python -m budget_app add
[안내] 거래 정보를 입력하세요. (Ctrl+C 로 취소)
날짜 (YYYY-MM-DD, 엔터=2024-02-01): ^C

[중단] 사용자가 입력을 취소했습니다.
(종료 코드 130)

$ python -m budget_app --verbose list --limit 1
[log] -> cmd_list 시작
#  id    날짜        타입     카테고리    금액  메모  태그
-  ----  ----------  -------  --------  ------  ----  ----
1  TX-5  2024-03-01  expense  교통      -1,500

[완료] 1건 출력 / 전체 6건
[log] cmd_list 실행 시간 0.28ms
[log] <- cmd_list 종료
```

</details>

**import/export CSV 스키마** (UTF-8, 헤더 포함)

| 컬럼 | 필수 | 설명 |
|---|---|---|
| `date` | ✅ | `YYYY-MM-DD` |
| `type` | ✅ | `income` / `expense` |
| `category` | ✅ | 이미 등록된 카테고리 |
| `amount` | ✅ | 양의 정수. 천 단위 쉼표 허용(`"600,000"`, CSV 에서는 따옴표로 감쌈) |
| `memo` | ❌ | 문자열 |
| `tags` | ❌ | 쉼표(,) 구분 문자열 |

export 는 `--month` 또는 `--from`+`--to` 중 하나 이상이 필수입니다.

**import 가 문제를 만났을 때**

| 문제 | 동작 |
|---|---|
| 행 내용 오류 (날짜 형식, 미등록 카테고리, 0 이하 금액 등) | 그 행만 건너뛰고 사유를 `[건너뛴 행]` 표로 출력, 나머지는 저장 |
| 파일 오류 (필수 컬럼 누락, UTF-8 이 아님, 닫히지 않은 따옴표) | **아무것도 저장하지 않고** `[오류]`/`[힌트]` 출력, 종료 코드 1 |

## 3. 보너스 · 응용 기능 실행 결과

<details>
<summary><b>보너스 — 백업</b></summary>

타임스탬프 폴더에 데이터 파일을 복사

```text
$ python -m budget_app backup
[완료] 백업 생성: data/backup/20240201_090000
  - budgets.jsonl
  - categories.jsonl
  - recurring.jsonl
  - transactions.date.idx
  - transactions.idx
  - transactions.jsonl

$ ls data/backup/*
budgets.jsonl
categories.jsonl
recurring.jsonl
transactions.date.idx
transactions.idx
transactions.jsonl
```

</details>

<details>
<summary><b>보너스 — 반복 내역</b></summary>

매월 31일 규칙 등록 → 2월에 적용(말일 2024-02-29 로 보정) → `list` 에 실제로 저장됐는지 확인 → 같은 달 재적용은 건너뜀 → 삭제

```text
$ python -m budget_app recurring add --day 31 --type income --category 월급 --amount 3000000 --memo "월급(반복)"
[저장 완료] id=RC-1 (매월 31일 월급 3,000,000원)

$ python -m budget_app recurring list
id    주기       타입    카테고리         금액  메모        태그  적용월수
----  ---------  ------  --------  -----------  ----------  ----  --------
RC-1  매월 31일  income  월급      3,000,000원  월급(반복)               0

[완료] 1개

$ python -m budget_app recurring apply --month 2024-02
[완료] 2024-02 반복 거래 생성 1건, 이미 적용되어 건너뜀 0건
#  id    날짜        타입    카테고리        금액  메모        태그
-  ----  ----------  ------  --------  ----------  ----------  ----
1  TX-8  2024-02-29  income  월급      +3,000,000  월급(반복)

$ python -m budget_app list --all
#  id    날짜        타입     카테고리        금액  메모             태그
-  ----  ----------  -------  --------  ----------  ---------------  ---------
1  TX-5  2024-03-01  expense  교통          -1,500
2  TX-8  2024-02-29  income   월급      +3,000,000  월급(반복)
3  TX-7  2024-02-25  income   월급      +3,000,000  2월 급여
4  TX-6  2024-02-05  expense  식비          -8,000  점심, 회사 근처  외식,점심
5  TX-4  2024-01-31  expense  월세        -500,000  1월 월세
6  TX-3  2024-01-25  income   월급      +3,000,000  1월 급여
7  TX-1  2024-01-05  expense  식비         -15,000                   외식

[완료] 7건 출력

$ python -m budget_app recurring apply --month 2024-02
[완료] 2024-02 반복 거래 생성 0건, 이미 적용되어 건너뜀 1건

$ python -m budget_app recurring remove --id RC-1
[삭제 완료] id=RC-1
```

</details>

<details>
<summary><b>보너스 — 테이블 정렬</b></summary>

외부 라이브러리 없이 한글 폭까지 계산해 열을 맞추고, 수입 `+` / 지출 `-` 부호와 천 단위 구분을 출력(예산 막대는 5번 budget 의 summary 참고)

```text
$ python -m budget_app list --limit 3
#  id    날짜        타입     카테고리        금액  메모        태그
-  ----  ----------  -------  --------  ----------  ----------  ----
1  TX-5  2024-03-01  expense  교통          -1,500
2  TX-8  2024-02-29  income   월급      +3,000,000  월급(반복)
3  TX-7  2024-02-25  income   월급      +3,000,000  2월 급여

[완료] 3건 출력 / 전체 7건

$ python -m budget_app budget list
월            예산       사용  사용률  상태
-------  ---------  ---------  ------  ----
2024-01  500,000원  515,000원  103.0%  초과

[완료] 1개
```

</details>

<details>
<summary><b>보너스 — 저장 원자성</b></summary>

거래를 수정하면 `transactions.jsonl` 끝에 새 버전 한 줄을 덧붙이고, `transactions.idx` 에서는 그 거래의 위치 칸만 덮어씀(줄 수는 1 늘고 위치 파일 크기는 그대로). 삭제는 `transactions.jsonl` 을 건드리지 않음. 카테고리·예산 파일은 임시 파일에 다 쓴 뒤 이름을 바꿔 한 번에 교체

```text
$ wc -l data/transactions.jsonl; wc -c data/transactions.idx
       9 data/transactions.jsonl
     128 data/transactions.idx

$ python -m budget_app update --id TX-1
[현재 값]
#  id    날짜        타입     카테고리     금액  메모  태그
-  ----  ----------  -------  --------  -------  ----  ----
1  TX-1  2024-01-05  expense  식비      -15,000        외식
[안내] 바꿀 항목만 입력하세요. 엔터=기존 값 유지, 메모/태그는 '-' 입력 시 비움 (Ctrl+C 로 취소)
날짜 [2024-01-05]: 
타입 [expense]: 
등록된 카테고리: 1) 식비, 2) 교통, 3) 월급, 4) 월세
카테고리 [식비] (번호 또는 이름): 
금액 [15000]: 16000
메모 [] (따옴표 없이 입력): 
태그 [외식] (쉼표 구분): 
[수정 완료] id=TX-1
필드    이전   이후
------  -----  -----
amount  15000  16000

$ wc -l data/transactions.jsonl; wc -c data/transactions.idx
      10 data/transactions.jsonl
     128 data/transactions.idx

$ python -m budget_app delete --id TX-5 --yes
[삭제 완료] id=TX-5 (2024-03-01 교통 1,500원)
[안내] 삭제된 id 는 재사용되지 않습니다(번호 gap 은 정상입니다).

$ wc -l data/transactions.jsonl; wc -c data/transactions.idx
      10 data/transactions.jsonl
     128 data/transactions.idx
```

</details>

<details>
<summary><b>응용 — 옛 날짜 거래를 나중에 가져와도 최신순 유지</b></summary>

나중에 가져온 옛 날짜 거래는 id 가 가장 커도 목록 맨 위가 아니라 제 날짜 위치에 나옴 → 날짜를 바꾸면 목록 위치도 이동 → 삭제 후 compact → 날짜 인덱스 파일을 지워도 자동으로 다시 만듦

```text
$ cat > old.csv <<'EOF'
date,type,category,amount,memo,tags
2023-12-15,expense,식비,7000,옛거래,
EOF

$ python -m budget_app import --from old.csv
[완료] old.csv → imported=1, skipped=0

$ python -m budget_app list --all
#  id    날짜        타입     카테고리        금액  메모             태그
-  ----  ----------  -------  --------  ----------  ---------------  ---------
1  TX-8  2024-02-29  income   월급      +3,000,000  월급(반복)
2  TX-7  2024-02-25  income   월급      +3,000,000  2월 급여
3  TX-6  2024-02-05  expense  식비          -8,000  점심, 회사 근처  외식,점심
4  TX-4  2024-01-31  expense  월세        -500,000  1월 월세
5  TX-3  2024-01-25  income   월급      +3,000,000  1월 급여
6  TX-1  2024-01-05  expense  식비         -16,000                   외식
7  TX-9  2023-12-15  expense  식비          -7,000  옛거래

[완료] 7건 출력

$ python -m budget_app update --id TX-9
[현재 값]
#  id    날짜        타입     카테고리    금액  메모    태그
-  ----  ----------  -------  --------  ------  ------  ----
1  TX-9  2023-12-15  expense  식비      -7,000  옛거래
[안내] 바꿀 항목만 입력하세요. 엔터=기존 값 유지, 메모/태그는 '-' 입력 시 비움 (Ctrl+C 로 취소)
날짜 [2023-12-15]: 2024-06-01
타입 [expense]: 
등록된 카테고리: 1) 식비, 2) 교통, 3) 월급, 4) 월세
카테고리 [식비] (번호 또는 이름): 
금액 [7000]: 
메모 [옛거래] (따옴표 없이 입력): 
태그 [] (쉼표 구분): 
[수정 완료] id=TX-9
필드  이전        이후
----  ----------  ----------
date  2023-12-15  2024-06-01

$ python -m budget_app list --limit 1
#  id    날짜        타입     카테고리    금액  메모    태그
-  ----  ----------  -------  --------  ------  ------  ----
1  TX-9  2024-06-01  expense  식비      -7,000  옛거래

[완료] 1건 출력 / 전체 7건

$ python -m budget_app delete --id TX-9 --yes
[삭제 완료] id=TX-9 (2024-06-01 식비 7,000원)
[안내] 삭제된 id 는 재사용되지 않습니다(번호 gap 은 정상입니다).

$ python -m budget_app compact
[완료] compact
항목                 이전  이후
-----------------  ------  ----
거래 데이터 크기   1,537B  807B
살아있는 거래         6건   6건
슬롯 수(=최대 id)       9     9

[안내] 730B 를 회수했습니다. id 는 변하지 않습니다.

$ rm data/transactions.date.idx

$ python -m budget_app list --limit 2
#  id    날짜        타입    카테고리        금액  메모        태그
-  ----  ----------  ------  --------  ----------  ----------  ----
1  TX-8  2024-02-29  income  월급      +3,000,000  월급(반복)
2  TX-7  2024-02-25  income  월급      +3,000,000  2월 급여

[완료] 2건 출력 / 전체 6건

$ ls data
backup
budgets.jsonl
categories.jsonl
recurring.jsonl
transactions.date.idx
transactions.idx
transactions.jsonl
```

</details>

## 4. 설계의 핵심 — 거래 데이터 파일 관리

### 코드 구성

명령은 `cli` → `commands/` → `services`·`file_services` → `transaction_store`·`stores` 방향으로만 호출합니다. 아래 계층은 위 계층을 모릅니다. 저장소(Store)는 `AppContext.create` 에서 한 번만 만들어 서비스들이 나눠 쓰고, 커맨드는 서비스만 거쳐 데이터를 다룹니다(저장소에 직접 닿지 않음). `models`·`validators`·`formatter`·`console`·`decorators` 는 여러 계층이 같이 쓰는 공통 도구이고, `durability` 는 `transaction_store`·`stores` 가 같이 쓰는 저장 보조 도구입니다.

| 데이터 | 저장소 (파일 읽기·쓰기) | 서비스 (규칙) |
|---|---|---|
| 거래 | `TransactionStore` (`transaction_store.py`) | `TransactionService` |
| 카테고리 | `CategoryStore` (`stores.py`) | `CategoryService` |
| 예산 | `BudgetStore` (`stores.py`) | `BudgetService` |
| 반복 규칙 | `RecurringStore` (`stores.py`) | `RecurringService` |
| CSV 가져오기·내보내기 | 없음. `TransactionService` 를 거쳐 저장·조회 | `CsvService` (`file_services.py`) |
| 백업 | 없음. `data/` 폴더의 파일을 그대로 복사 | `BackupService` (`file_services.py`) |

| 모듈 | 역할 | 주요 클래스·함수 |
|---|---|---|
| `__main__.py` · `cli.py` | `python -m budget_app` 진입점, argparse 서브커맨드 정의, 사용법 오류도 `[오류]`/`[힌트]` 로 출력 | `main`, `build_parser`, `HintArgumentParser` |
| `commands/` | 서브커맨드 핸들러. 입력을 받아 서비스를 부르고 결과를 출력. 파일 이름이 명령 이름: `transaction`(add·list·search·update·delete) · `summary` · `budget` · `category` · `recurring` · `files`(import·export·compact·backup) | `cmd_add`, `cmd_list`, `cmd_summary` … (모두 `@command`) |
| `context.py` | 저장소를 한 번만 만들어 서비스들에 나눠 주고(조립), 시작 시 복구를 실행한 뒤 핸들러에는 서비스만 넘김 | `AppContext.create` |
| `console.py` | 핸들러 공용 보조: 오류 출력, id 해석, 올바른 값이 나올 때까지 다시 묻기, search·export 검색 조건 만들기, 거래 표·예산 사용률 출력 | `print_error`, `parse_tx_id`, `prompt_until_valid`, `build_criteria`, `render_transactions` |
| `services.py` | 규칙: 거래 추가·수정·삭제·검색·월별 요약·compact, 카테고리 등록·삭제(사용 중이면 거부)·검증·사용 건수, 예산 설정·조회·삭제와 사용률, 반복 거래 등록·적용 | `TransactionService`, `CategoryService`, `BudgetService`, `RecurringService` |
| `file_services.py` | CSV 가져오기·내보내기, 백업 | `CsvService`, `BackupService` |
| `validators.py` | 날짜·월·타입·금액·일자·태그 검증과 정규화 | `validate_date`, `validate_amount` … |
| `transaction_store.py` | 거래 저장 엔진: `transactions.jsonl` · `.idx` · `.date.idx` 읽기·쓰기, 시작 시 복구, `compact` | `TransactionStore`, `TransactionIndex`, `DateIndex` |
| `durability.py` | 파일을 꺼져도 안전한 순서로 확정(`fsync`, 임시 파일 교체), 위치 칸 되돌리기 기록 | `fsync_dir`, `atomic_write`, `SlotUndo` |
| `stores.py` | 카테고리·예산·반복 규칙 JSONL (임시 파일 + `os.replace` 로 전체 재작성) | `CategoryStore`, `BudgetStore`, `RecurringStore` |
| `models.py` | 데이터 모델과 예외(원인 `message` + 힌트 `hint`) | `Transaction`, `ValidationError`, `StorageError` |
| `formatter.py` | 외부 라이브러리 없이 한글 폭을 계산해 표 정렬, 금액·예산 막대 형식 | `format_table`, `display_width`, `format_amount`, `format_bar` |
| `decorators.py` | 공통 처리 데코레이터와 종료 코드 | 아래 표 |

**데코레이터** — 모든 서브커맨드 핸들러(21개)에 `@command` 하나만 붙입니다.

| 데코레이터 | 하는 일 | 출력 |
|---|---|---|
| `@command` | 아래 셋을 `handle_errors(log_call(timeit(func)))` 순서로 묶음 | — |
| `@handle_errors` | 예외를 잡아 종료 코드로 바꿈. `BudgetAppError`·파일 없음·권한·입출력·메모리 부족 → `[오류]`+`[힌트]`, 종료 코드 1 / Ctrl+C·입력 끝 → `[중단]`, 130 / 정상 0. 스택트레이스는 사용자에게 보이지 않고 `--verbose` 일 때만 로그로 남김 | stderr |
| `@log_call` | 핸들러 시작·종료 기록 (`-> cmd_list 시작`, `<- cmd_list 종료`) | `--verbose` 일 때만 stderr |
| `@timeit` | 실행 시간 측정 (`cmd_list 실행 시간 0.28ms`) | `--verbose` 일 때만 stderr |

모두 `functools.wraps` 를 써서 원래 함수 이름과 설명이 유지됩니다(테스트 100 에서 확인).

### 저장 파일 (`./data`)

| 파일 | 내용 | 방식 |
|---|---|---|
| `transactions.jsonl` | 거래 **내용** 원본 (JSON 1줄 = 1건) | 항상 끝에만 추가 (수정 전 내용도 남음) |
| `transactions.idx` | **id → 현재 내용의 위치**. id N 의 위치는 N번째 16바이트 칸에 있고, 삭제하면 그 칸을 `(0, 0)` 으로 바꿈 (아래에서는 이 칸을 **위치 칸** 이라고 부름) | add·import 는 끝에 칸 추가, update·delete 는 그 칸 덮어쓰기 |
| `transactions.date.idx` | **(거래일자, id) 날짜순 목록** (항목당 12바이트) — 최신순·기간 조회용 | 끝에 추가 또는 병합 후 교체 |
| `transactions.idx.slot-undo` | 위치 칸 하나를 바꾸기 전의 위치 파일 길이와 원래 16바이트 (+ SHA-256) | 칸을 바꾸는 동안에만 있음. 남아 있으면 다음 실행 때 되돌림 |
| `categories.jsonl` · `budgets.jsonl` | 카테고리와 **사용 건수** / 월별 예산 | 임시 파일에 전체를 다시 쓴 뒤 `os.replace` 로 교체 |
| `recurring.jsonl` | 반복 거래 규칙 (보너스) | 임시 파일에 전체를 다시 쓴 뒤 `os.replace` 로 교체 |
| `backup/` | `backup` 명령이 만드는 백업 | 타임스탬프 폴더 |

### 저장 형식 선택 — JSONL vs CSV

내부 저장은 **JSONL**, 사람·엑셀과 주고받는 용도(`import`/`export`)는 **CSV** 로 나눴습니다.

| 기준 | JSONL (내부 저장) | CSV (import/export) |
|---|---|---|
| 메모에 쉼표·따옴표·줄바꿈 | 쉼표는 그대로, 따옴표와 줄바꿈은 `\"`·`\n` 으로 바꿔 씀(`json` 모듈이 자동 처리) | 칸을 따옴표로 감싸고, 칸 안의 따옴표는 `""` 로 두 번 씀(`csv` 모듈이 자동 처리). 손으로 고치다 따옴표 하나만 빠져도 그 뒤 열이 어긋남 |
| 태그 같은 목록 값 | 배열 문법이 있어 `["외식", "점심"]` 목록 그대로 저장 | 한 칸에는 문자열 하나만 들어가, 쉼표로 이어 붙였다가(`"외식,점심"`) 읽을 때 다시 잘라야 함. 그래서 태그 이름에는 쉼표를 쓸 수 없음 |
| 숫자/문자 구분 | 둘 다 텍스트지만 JSON 문법이 구분함(`12000` 은 숫자, `"12000"` 은 문자열). `json.loads` 가 숫자를 바로 `int` 로 돌려줌 | 형식에 구분이 없어 모든 칸이 문자열로 읽힘. 프로그램이 `int()` 로 바꾸고 숫자가 아니면 거부해야 함 |
| 끝에 덧붙이기 (append) | 줄마다 키 이름이 들어 있어, 헤더나 열 순서를 신경 쓰지 않고 한 줄씩 덧붙이면 됨 | 첫 줄 헤더의 열 순서에 맞춰 덧붙여야 하고, 빈 파일이면 헤더부터 써야 함 |
| 위치 파일(`transactions.idx`)과 함께 쓰기 | 줄바꿈이 `\n` 으로 바뀌어 저장되므로 한 줄 = 거래 1건. 시작·끝 바이트 위치로 그 줄만 바로 읽음 | 메모 안의 줄바꿈이 따옴표 안에 그대로 들어가 거래 1건이 여러 줄이 될 수 있어, "한 줄 = 거래 1건"이 보장되지 않음 |
| 필드 추가 (스키마 변경) | 줄마다 키 이름이 있어서 옛 줄은 그대로 두고 새 줄에만 새 키를 쓰면 됨. 키가 없는 옛 줄은 읽는 코드가 기본값을 넣음(예: `source` 가 없으면 `""`) | 열 이름이 헤더 한 줄에만 있어, 열을 추가하면 헤더와 기존 행을 모두 고쳐 파일 전체를 다시 써야 함 |
| 엑셀에서 열기·용량 | 엑셀로 바로 열 수 없음. 키 이름이 줄마다 반복돼 더 큼 | **열 수 있음**, 더 작음. 다만 `export` 파일은 BOM 없는 UTF-8 이라 더블클릭으로 열면 한글이 깨질 수 있어, 엑셀의 "데이터 → 텍스트/CSV 가져오기"에서 UTF-8 을 골라 열어야 함 |

**결론:** 이 앱의 저장 엔진은 "끝에만 덧붙이고, 위치 파일로 그 줄만 바로 읽는다"가 핵심이라, 한 줄이 반드시 거래 1건인 JSONL 이 맞습니다. CSV 의 장점(엑셀 호환)은 사람과 주고받을 때만 필요하므로 `import`/`export` 에서만 씁니다.

### 파일 형식 (실제 내용)

`.jsonl` 은 한 줄이 JSON 하나인 텍스트 파일입니다. 아래는 위 실행을 마친 뒤의 실제 내용입니다(`compact` 로 옛 줄이 정리된 상태라 `transactions.jsonl` 에는 살아있는 최신 내용만 있습니다).

```text
$ head -n 2 data/transactions.jsonl data/categories.jsonl data/budgets.jsonl
==> data/transactions.jsonl <==
{"id": 1, "date": "2024-01-05", "type": "expense", "category": "식비", "amount": 16000, "memo": "", "tags": ["외식"]}
{"id": 3, "date": "2024-01-25", "type": "income", "category": "월급", "amount": 3000000, "memo": "1월 급여", "tags": []}

==> data/categories.jsonl <==
{"count": 2, "created_at": "2024-02-01T09:00:00", "name": "식비"}
{"count": 0, "created_at": "2024-02-01T09:00:00", "name": "교통"}

==> data/budgets.jsonl <==
{"amount": 500000, "month": "2024-01", "updated_at": "2024-02-01T09:00:00"}
```

`count` 는 그 카테고리를 쓰는 살아있는 거래 수입니다(교통은 TX-2 를 삭제해 0). `category remove` 는 이 값이 0 이면 거래를 훑지 않고 바로 지우고, 0 이 아니면 거래를 훑어 정말 쓰는지 확인합니다.

### 카테고리 사용 건수를 맞게 유지하는 방법

- 거래 파일과 `categories.jsonl` 은 따로 저장되므로 중간에 꺼지면 어긋날 수 있습니다. 그래서 건수는 **늘릴 때는 거래보다 먼저, 줄일 때는 거래보다 나중에** 바꿉니다(`update` 로 카테고리를 바꾸면 새 카테고리 +1 → 거래 저장 → 옛 카테고리 −1). 이렇게 하면 꺼져도 건수가 실제보다 **많게만** 틀립니다.
- 많게 틀리면: 안 쓰는 카테고리를 지울 때 0 이 아니라서 거래를 한 번 훑고, 쓰는 거래가 없으면 그대로 지웁니다. 데이터는 망가지지 않습니다.
- `import` 는 1차 검증 때 카테고리별 건수를 메모리에 합산했다가, 거래를 쓰기 전에 `categories.jsonl` 을 **한 번만** 다시 씁니다(행마다 쓰지 않음).
- `compact` 는 건수를 거래에서 처음부터 다시 셉니다. `count` 가 없는 옛 형식의 파일은 다음 실행 때 한 번 세어 채웁니다.
- 한계: 직접 편집 등으로 건수가 실제보다 **적어지면**, 줄이다가 0 아래로 내려갈 때만 알아채고 다시 셉니다. 그 전에 그 카테고리를 지우면 사용 중인데도 삭제될 수 있으므로, 파일을 직접 고쳤다면 `compact` 를 실행하세요.

### 최신 거래의 기준

최신은 **`(거래일자, id)`** 로 정합니다. 거래일자가 늦은 것이 최신이고, 같은 날짜면 id 가 큰(나중에 등록한) 것이 최신입니다.
`list` 는 날짜순 목록(`transactions.date.idx`)을 **맨 끝부터 거꾸로** 읽으며 해당 거래만 `jsonl` 에서 읽다가 `--limit` 에 닿으면 멈춥니다(필요한 만큼만 한 건씩 읽고, 전체를 읽지 않음). 기간을 주면 이진 탐색으로 그 구간의 시작과 끝을 찾습니다.
옛 날짜 거래를 나중에 가져와도 id 만 커질 뿐 날짜순 목록에서는 제 날짜 위치에 들어가므로 목록 순서가 어긋나지 않습니다.

### 동작별로 파일이 바뀌는 방식

| 동작 | `transactions.jsonl` | `transactions.idx` | `transactions.date.idx` |
|---|---|---|---|
| **add** | 끝에 한 줄 추가 | 되돌리기 기록 후 위치 칸 추가 | 날짜가 가장 늦으면 끝에 추가, 아니면 제 위치에 끼워 넣어 파일 전체를 다시 씀 |
| **import** | 유효한 행을 한 번에 끝에 추가 | import 전 파일 크기를 기록한 뒤 위치 칸을 한 번에 추가 | 새 항목을 배열에 모아 정렬해 한 번에 병합 |
| **update** | 수정본 전체를 끝에 추가 (옛 줄은 남음) | 되돌리기 기록 후 그 id 의 위치 칸을 새 위치로 덮어씀 | 날짜가 바뀐 경우에만 새 항목 추가. 더 예전 날짜로 바꾸면 파일을 다시 쓰면서 그 id 의 옛 항목도 뺌. 더 최신 날짜면 끝에 덧붙이고 옛 항목은 남김(읽을 때 무시) |
| **delete** | 변화 없음 | 되돌리기 기록 후 그 id 의 위치 칸을 `(0, 0)` 으로 | 변화 없음 (삭제된 칸의 항목은 읽을 때 무시) |
| **compact** | 살아있는 최신본만 새 파일로 옮겨 씀 | 칸 번호(=id)는 그대로, 위치 값만 갱신 | 살아있는 거래로 처음부터 다시 만듦 |

- update·delete 로 남은 옛 줄과 옛 날짜 항목(더 최신 날짜로 바꾼 update, delete)은 읽을 때 건너뛰고, `python -m budget_app compact` 가 정리합니다. **id 는 바뀌지 않고** 삭제된 번호는 재사용되지 않습니다.
- `transactions.date.idx` 는 `jsonl`/`idx` 에서 언제든 다시 만들 수 있어서, 없거나 손상되면(크기가 12의 배수가 아님) 다음 실행 때 자동으로 재생성하고, 파일이 있었으면 `[안내]` 로 알립니다.
- add 의 쓰는 순서는 `jsonl`(내용) → `transactions.date.idx`(날짜 항목) → `.slot-undo`(되돌리기 기록) → `transactions.idx`(위치 칸) → `.slot-undo` 삭제이고, **되돌리기 기록을 지우는 순간이 거래 1건의 저장 완료**입니다. 그 전에 꺼지면 위치 칸을 원래대로 되돌리고, 먼저 써 둔 날짜 항목은 아직 위치가 없는 id 라서 읽을 때 건너뜁니다. 그래서 저장된 거래가 목록·요약에서만 빠지는 일이 없습니다.
- update 는 옛 날짜 항목을 지울 수 있어서 되돌리기 기록을 날짜 인덱스보다 **먼저** 씁니다(`jsonl` → `.slot-undo` → 날짜 인덱스 → 위치 칸 → `.slot-undo` 삭제). 옛 항목을 지운 날짜 인덱스를 쓴 뒤 위치 칸을 쓰기 전에 꺼지면, 다음 실행이 되돌리기 기록을 보고 위치 칸을 되돌리고 날짜 인덱스도 처음부터 다시 만듭니다. 그래서 수정 전 거래가 목록에서 빠지지 않습니다. import 는 `transactions.idx.import-pending` 삭제가 완료 시점입니다(6부).
- id 0 처럼 정상적으로 생길 수 없는 날짜 항목을 만나면 건너뛰지 않고 `[오류]` 와 함께 `compact`(날짜 인덱스 재생성)를 안내합니다.
- 디스크 오류나 직접 편집으로 날짜 항목이 **다른 거래를 가리키게** 손상되면, 읽는 중에는 수정 전 옛 항목과 구분할 수 없어 그 거래가 목록에서 빠집니다. 대신 `list` 가 끝까지 읽었을 때(`--all` 이거나 `--limit` 에 닿지 않음) 출력 건수가 저장된 거래 수와 다르면 `[경고]` 와 함께 `compact` 를 안내합니다.
- `add`/`import` 가 위치(`transactions.idx`)를 쓰다 꺼져 끝에 16바이트가 안 되는 조각이 남으면, 다음 실행 때 잘라 내고 `[안내]` 로 알립니다. 그 거래는 저장되지 않은 것이고, 남은 `jsonl` 줄과 날짜 항목은 위처럼 건너뛰다가 `compact` 때 정리됩니다.
- `compact` 는 위치를 제자리에서 고치므로, 시작 전에 `transactions.idx.compact-backup` 복사본을 만듭니다(임시 이름으로 다 쓰고 확정한 뒤 이름을 바꿔, 이 이름의 파일은 항상 완전함). 도중에 꺼지면 다음 실행 때 자동으로 정리하고 `[안내]` 로 알립니다.
  - `transactions.jsonl.tmp` 가 남아 있으면 거래 데이터 교체 전에 멈춘 것 → 복사본으로 위치를 되돌림(compact 전 상태, 잃는 데이터 없음. 다시 실행하면 됨)
  - 없으면 교체는 끝난 것 → 새 거래 데이터와 새 위치가 이미 짝이라 복사본만 지움
- 한계: 위 건수 검사는 `list` 에만 있어서, `search`/`summary`/`export` 는 손상된 상태에서도 경고 없이 빠진 결과를 냅니다(`compact` 로 복구). `transactions.idx` 는 삭제해도 줄지 않으며, 같은 `--data-dir` 의 동시 실행은 고려하지 않았습니다(1인 사용 전제).

## 5. 대용량(10만 건 이상) 데이터 테스트

### 측정 방법

```bash
python tools/bench_large.py              # 10만 건
python tools/bench_large.py --n 300000   # 건수 지정 (100만 건은 몇 분 걸림)
```

임시 폴더에 5년치(2020~2024) 거래 N건을 CSV 로 만들어 `import` 한 뒤, 명령마다 실제 CLI 를 새 프로세스로 실행해 **시간**과 **최대 메모리(RSS)** 를 잽니다. 끝나면 임시 폴더를 지우므로 `./data` 는 건드리지 않습니다. 파이썬 시작 비용이 모든 수치에 들어가므로, 빈 저장소에서 `category list` 를 실행한 값을 기준선으로 함께 봅니다. 0.1초 안팎의 짧은 명령은 실행할 때마다 ±0.1초쯤 흔들립니다.

### 결과

측정 환경: macOS 15.7.4 (Intel Core i5-8600 3.1GHz 6코어, 메모리 32GB, PCIe SSD·APFS), Python 3.10.22 (`.venv`). 기준선(빈 저장소 `category list`)은 0.08s · 16MB 입니다.

| 명령 | 10만 건 | 30만 건 | 100만 건 | 판정 |
|---|---:|---:|---:|---|
| `list --limit 20` | 0.11s · 16MB | 0.17s · 16MB | 0.40s · 16MB | 거의 일정 (병목 ⑤) |
| `summary --month 2024-06` | 0.10s · 16MB | 0.15s · 16MB | 0.35s · 16MB | 그 달 건수에 비례 (정상) |
| `search --month 2024-06 --category 식비` | 0.08s · 16MB | 0.07s · 16MB | 0.07s · 16MB | 일정 |
| `search --category 식비 --limit 20` | 0.07s · 16MB | 0.07s · 16MB | 0.07s · 16MB | 일정 |
| `add` 가장 늦은 날짜 | 0.08s · 16MB | 0.08s · 16MB | 0.08s · 16MB | 일정 |
| `add` 옛 날짜 | 0.11s · 16MB | 0.20s · 19MB | 0.46s · 17MB | 병목 ③ |
| `category remove` (사용되지 않는 카테고리) | 0.08s · 16MB | 0.07s · 16MB | 0.07s · 15MB | 일정 |
| `import` (빈 저장소에 N건) | 6.51s · 23MB | 19.19s · 31MB | 63.42s · 61MB | 병목 ① |
| `search --q 없는말` (기간 조건 없음) | 1.70s · 16MB | 4.90s · 16MB | 15.95s · 16MB | **병목 ②** |
| `export` 전체 기간 | 2.12s · 16MB | 6.24s · 16MB | 20.40s · 16MB | **병목 ②** |
| `compact` | 1.69s · 20MB | 5.03s · 27MB | 15.85s · 49MB | 시간은 건수에 비례, 메모리는 작음 |

| 파일 | 10만 건 | 30만 건 | 100만 건 |
|---|---:|---:|---:|
| `transactions.jsonl` | 13.2MB | 40.0MB | 133.8MB |
| `transactions.idx` | 1.5MB | 4.6MB | 15.3MB |
| `transactions.date.idx` | 1.1MB | 3.4MB | 11.4MB |

**잘 되는 부분:** 기간을 주거나 `--limit` 이 있는 조회와 일반 `add` 는 100만 건에서도 기준선과 거의 같고, **메모리는 조회·compact·import 모두 15~61MB** 입니다. 날짜 인덱스를 이진 탐색해 필요한 구간만 읽고(100만 건에서 약 20번 읽기), 한 건씩 꺼내 쓰다가 `--limit` 에 닿으면 멈추기 때문입니다.

### 병목과 개선안

**① `import` — 시간이 건수에 비례 (메모리는 개선 적용됨)**

- 적용한 개선: 행마다 `categories.jsonl` 을 두 번 열던 것을 시작할 때 한 번만 읽고, 유효한 행을 리스트에 모으지 않고 CSV 를 두 번 읽으며(1차 검증, 2차 바로 저장) 저장합니다. 날짜 항목은 날짜(4바이트)와 id(8바이트) 배열에 따로 담고, 날짜별로 나눠 정렬합니다(계수 정렬). 100만 건이면 날짜 항목 12MB, 정렬용 id 배열 8MB, 위치 칸 16MB 를 메모리에 모으고, 여기에 파이썬 기본 약 15MB 가 더해져 import 의 최대 메모리가 61MB 입니다.
- 남은 원인: CSV 를 두 번 읽고 검증해 100만 건에 약 63초가 걸립니다.
- 개선안: 2차 읽기 때 검증을 다시 하지 않도록 1차 결과(유효한 줄 번호)를 비트 배열로 남깁니다.

**② 기간 조건 없는 전체 훑기 — 시간이 건수에 비례 (100만 건 16~20초)**

- 원인: `search --q`/`--tag`(기간 없음), 전체 `export` 는 날짜 인덱스 전체를 돌며 거래마다 `transactions.idx` 와 `transactions.jsonl` 에서 위치를 옮겨(seek) 읽고 JSON 을 해석합니다. 메모리는 늘지 않습니다.
- 적용한 개선 (`category remove`): 사용 여부를 확인하려고 전체를 훑던 것을, `categories.jsonl` 에 저장한 카테고리별 **사용 건수**로 바로 판단합니다(4부 참고). 사용되지 않는 카테고리 삭제는 100만 건에서도 0.07s 입니다. 사용 중인 카테고리는 최근 거래에서 금방 찾아 거부합니다.
- 개선안 (`export` 전체, 기간 없는 `search`): 범위가 넓으면 `transactions.jsonl` 을 **처음부터 순서대로** 읽고(살아있는 줄인지는 `transactions.idx` 로 확인) 결과만 날짜순으로 정렬합니다. 위치 이동이 없어지는 대신 결과 건수만큼 메모리를 씁니다.
- 개선안 (메모 검색): 자주 쓴다면 단어 → id 목록 색인 파일을 따로 둡니다. 쓰기가 느려지고 파일이 하나 늘어납니다.

**③ 옛 날짜 `add`·날짜를 바꾸는 `update` — 날짜 인덱스 파일 전체를 다시 씀 (100만 건 0.46초)**

- 원인: 가장 늦은 날짜보다 이른 항목은 정렬 배열의 중간에 들어가야 해서, `transactions.date.idx` 를 처음부터 읽으며 끼워 넣어 임시 파일에 전부 다시 쓰고 교체합니다(`DateIndex.merge_keys`, 100만 건이면 매번 11.4MB). 기존 파일은 4096항목(48KB)씩 읽으므로 메모리는 늘지 않고 시간만 건수에 비례합니다. update 는 이때 그 id 의 옛 날짜 항목도 함께 뺍니다.
- 판단: 1만 건이면 한 번에 수 ms 라 느낄 수 없고, 10만 건에서도 0.11초입니다. 개인 가계부 규모에서는 그대로 둡니다.
- 개선안: 옛 날짜 항목이 아주 잦아지면 작은 **보조 파일에 모아 두고** 읽을 때 두 파일을 합쳐 읽다가, 보조 파일이 일정 크기를 넘거나 `compact` 할 때 한 번에 병합합니다.

**④ `compact` — 시간이 건수에 비례 (메모리는 개선 적용됨, 100만 건 49MB)**

- 적용한 개선: 날짜 인덱스를 다시 만들 때 `(날짜, id)` 튜플(건당 약 120바이트) 대신 ①과 같은 배열에 모아 계수 정렬하고, 파일에는 4096항목씩 나눠 씁니다.
- 남은 원인: 카테고리 사용 건수를 다시 세려고 거래 전체를 한 번 더 읽습니다.
- 개선안: 날짜 인덱스를 다시 만들 때 이미 거래를 하나씩 읽으므로, 그때 카테고리 건수도 함께 셉니다.

**⑤ `list` 의 전체 건수 — 위치 파일 전체를 훑음 (100만 건 0.40초)**

- 원인: "`N건 출력 / 전체 M건`" 과 건수 불일치 경고를 위해 `stats()` 가 `transactions.idx` 의 모든 칸(100만 건이면 15.3MB)을 파이썬 반복문으로 셉니다.
- 개선안: 살아있는 건수를 작은 메타 파일에 저장해 add/delete/import 때 갱신하거나, 삭제된 칸 수만 세어 두고 `칸 수 − 삭제 수` 로 계산합니다.

### 우선순위

1. **⑤ list 전체 건수**: 가장 자주 쓰는 명령이라 효과가 큽니다.
2. **④ compact 시간**: 카테고리 건수를 날짜 인덱스 재생성과 한 번에 셉니다.
3. ③의 보조 파일, ②의 순차 읽기, ①의 검증 결과 재사용은 100만 건 이상을 자주 다룰 때 적용합니다.

## 6. 작업 도중 전원이 꺼지면 — 명령별 복구 방식

### 먼저 알아 둘 네 가지 원칙

1. **거래 1건(add·update·delete)의 저장 완료는 되돌리기 기록(`transactions.idx.slot-undo`)을 지우는 순간 하나로 정합니다.** 순서는 `transactions.jsonl`(내용) → `transactions.date.idx`(날짜 순서) → 되돌리기 기록 → `transactions.idx`(위치 칸) → 되돌리기 기록 삭제입니다(update 는 되돌리기 기록을 날짜 순서보다 먼저 씁니다. 아래 update 표). 되돌리기 기록에는 위치 칸을 바꾸기 전의 파일 길이와 원래 16바이트를 적어 둡니다. 기록이 남아 있으면 위치 칸이 반쯤 써졌어도 원래대로 되돌립니다. import 는 따로 `transactions.idx.import-pending` 을 지우는 순간이 완료입니다.
2. **거래가 있는지 없는지는 위치 칸(`transactions.idx`)만 정합니다.** 내용과 날짜 순서는 그보다 **먼저** 씁니다. 그래서 위치 칸이 생기기 전에 꺼지면, 먼저 써 둔 내용과 날짜 항목은 어떤 위치 칸도 가리키지 않으므로 읽을 때 무시되고 `compact` 때 지워집니다. 날짜 인덱스는 언제든 위치·내용 파일에서 다시 만들 수 있는 보조 자료입니다.
3. **카테고리 사용 건수(`count`)는 실제보다 많게만 틀리게 합니다.** 늘릴 때는 거래보다 먼저, 줄일 때는 거래보다 나중에 바꿉니다. 많게 틀리면 "안 쓰는 카테고리를 지울 때 거래를 한 번 확인한다" 정도로 끝나지만, 적게 틀리면 쓰는 카테고리를 지울 수 있기 때문입니다.
4. **복구는 다음에 실행하는 아무 명령이 시작할 때 자동으로 합니다.** `list` 든 `add` 든 상관없습니다(도움말 `--help` 와 명령 없이 실행한 경우는 제외). 바로잡은 것이 있으면 맨 위에 `[안내]` 로 알려 줍니다.

날짜 인덱스는 두 가지로 씁니다. 새 항목이 모두 마지막 항목보다 늦으면 파일 끝에 덧붙이고, 그렇지 않으면(옛 날짜, import, compact, 손상 복구) 옆의 `transactions.date.idx.tmp` 에 전체를 끝까지 쓴 뒤 이름을 바꿔 한 번에 교체합니다. 그래서 꺼져도 날짜 인덱스는 "바꾸기 전" 이나 "바꾼 뒤"(끝에 덧붙이다 끊기면 크기가 12의 배수가 아니게 되어 다음 실행 때 다시 만듦)입니다.

### 모든 명령이 시작할 때 하는 일 (이 순서대로)

| 순서 | 확인하는 것 | 발견하면 |
|---|---|---|
| 1 | `transactions.idx.compact-backup` 이 남아 있나 (compact 가 끝나지 않음) | 아래 compact 표대로 되돌리거나 마무리 |
| 2 | `transactions.idx.import-pending` 이 남아 있나 (import 가 끝나지 않음) | 위치 파일을 import 전 크기로 잘라 **그 import 전체를 취소** |
| 3 | `transactions.idx.slot-undo` 가 남아 있나 (거래 1건 저장이 끝나지 않음) | 위치 파일을 기록된 길이로 자르거나 원래 16바이트로 되돌림 → **그 거래는 바꾸기 전 상태**. 그 저장이 날짜 인덱스를 이미 고쳤을 수 있으므로 날짜 인덱스도 처음부터 다시 만듦 |
| 4 | `transactions.idx` 끝에 16바이트가 안 되는 조각이 있나 | 조각을 잘라 냄 |
| 5 | `transactions.date.idx` 가 없거나 크기가 12의 배수가 아닌가 | 위치·내용 파일에서 처음부터 다시 만듦. 파일이 있었으면 `[안내]` 로 알림(첫 실행처럼 없던 파일은 조용히 만듦). 날짜 인덱스는 보조 자료라 백업하지 않음 |
| 6 | `categories.jsonl` 에 `count` 가 없는 예전 형식인가 | 거래를 한 번 세어 채움 |

날짜 인덱스 안쪽의 손상(id 0 같은 있을 수 없는 항목)은 시작할 때 모두 훑지 않고, 읽다가 발견하면 `[오류]` 와 함께 `compact`(날짜 인덱스 재생성)를 안내합니다.

### add — 거래 1건 추가

| 꺼진 시점 | 남는 것 | 다음 명령에서 |
|---|---|---|
| ① 카테고리 건수 +1 을 저장한 뒤 | 건수만 1 많음 | 그대로 둠(안전). 그 카테고리를 지울 때 거래를 확인해 보고 허용 |
| ② `jsonl` 에 내용을 쓰는 도중 / 쓴 뒤 | 반쯤 쓴 줄 또는 완전한 줄 | 무시됨(위치가 없음). 다음 add 의 줄이 그 뒤에 이어 붙어 한 줄로 보일 수 있지만 읽기에는 지장 없고 compact 때 정리 |
| ③ 날짜 항목을 쓰는 도중 / 쓴 뒤 | 끝에 덧붙이다 만 조각, `.tmp`, 또는 새 항목이 든 날짜 인덱스 | 조각이면 시작 단계 5 에서 다시 만들고, `.tmp` 는 다음 병합 때 덮어씀. 어느 쪽이든 그 항목은 위치 칸이 없는 id 라서 읽을 때 건너뜀 |
| ④ 되돌리기 기록을 쓴 뒤, 위치 칸을 쓰는 도중 / 다 쓴 뒤 | 되돌리기 기록 + 반쯤 또는 전부 써진 위치 칸 | 시작 단계 3: 위치 파일을 원래 길이로 잘라 냄 → **저장 안 된 것으로 처리** |
| ⑤ 되돌리기 기록을 지운 뒤 | 없음 | **저장 완료** |

결과: 꺼진 add 는 **완전히 저장됐거나, 아예 없던 일** 둘 중 하나입니다. 저장 안 된 거래의 id 는 다음 add 가 다시 받고, 남은 날짜 항목은 날짜가 안 맞거나 중복이라 함께 무시됩니다.

### import — CSV 여러 건 가져오기

| 꺼진 시점 | 남는 것 | 다음 명령에서 |
|---|---|---|
| ① 1차 읽기(검증) 중 | 없음 | 아무것도 저장 안 됨. 다시 import |
| ② 카테고리별 건수 합계를 **한 번에** 저장한 뒤 | 건수만 많음 | 안전(add ① 과 같음) |
| ③ 2차 읽기로 `jsonl` 에 쓰는 중 | 위치 없는 줄들 | 무시됨 |
| ④ 날짜 항목을 병합하는 중 / 병합한 뒤 | `transactions.date.idx.tmp`, 또는 교체가 끝난 새 날짜 인덱스 | 교체 전이면 원래 날짜 인덱스 그대로(남은 `.tmp` 는 다음 병합 때 덮어씀). 교체 뒤면 새 항목은 위치 칸이 없는 id 라서 무시됨 |
| ⑤ "import 전 위치 파일 크기"를 `transactions.idx.import-pending` 에 기록한 뒤 | 기록 파일 | 시작 단계 2: 위치 파일은 아직 그대로라 기록만 지움 → 아무것도 저장 안 됨 |
| ⑥ 위치 칸 묶음을 쓰는 도중 / 다 쓴 뒤 기록을 지우기 전 | 기록 파일 + 일부 또는 전부 써진 칸 | 시작 단계 2: 기록된 크기로 잘라 **import 전체를 취소**(일부 행만 들어가는 일 없음) |
| ⑦ 기록 파일을 지운 뒤 | 없음 | **저장 완료** |

결과: 꺼진 import 는 **CSV 전체가 들어갔거나, 하나도 안 들어갔거나** 둘 중 하나입니다. `[안내]` 에 취소됐다고 나오면 같은 CSV 를 그대로 다시 가져오면 됩니다.

### update — 거래 수정

| 꺼진 시점 | 남는 것 | 다음 명령에서 |
|---|---|---|
| ① (카테고리를 바꾸는 경우) 새 카테고리 +1 을 저장한 뒤 | 새 카테고리 건수만 1 많음 | 안전 |
| ② 수정한 내용을 `jsonl` 끝에 쓴 뒤 | 위치 없는 새 줄 | 무시됨. 거래는 **수정 전 내용** 그대로 |
| ③ 되돌리기 기록을 쓴 뒤, (날짜를 바꾸는 경우) 날짜 인덱스를 고치는 도중 / 고친 뒤 | 되돌리기 기록 + 새 날짜 항목이 들어가고 (더 예전 날짜면) 옛 항목이 빠진 날짜 인덱스 | 시작 단계 3: 위치 칸을 되돌리고 날짜 인덱스를 처음부터 다시 만듦 → 목록에서 수정 전 날짜에 한 번만 나옴 |
| ④ 위치 칸 16바이트를 덮어쓰는 도중 / 다 쓴 뒤 | 되돌리기 기록 + 반쯤 또는 전부 바뀐 칸 | 시작 단계 3: 원래 16바이트로 되돌리고 날짜 인덱스를 다시 만듦 → 수정 전 내용 |
| ⑤ 되돌리기 기록을 지운 뒤 | 없음 | **수정 완료** |
| ⑥ (카테고리를 바꾸는 경우) 옛 카테고리 −1 을 저장하기 전 | 옛 카테고리 건수만 1 많음 | 안전 |

결과: **수정 전 그대로이거나, 수정이 끝난 상태** 둘 중 하나입니다. 다시 수정하면 됩니다.

### delete — 거래 삭제

| 꺼진 시점 | 남는 것 | 다음 명령에서 |
|---|---|---|
| ① 되돌리기 기록을 쓴 뒤, 위치 칸을 0 으로 덮어쓰는 도중 / 다 쓴 뒤 | 되돌리기 기록 | 시작 단계 3: 원래 칸으로 되돌림 → 삭제 안 됨. 다시 delete |
| ② 되돌리기 기록을 지운 뒤 | 없음 | **삭제 완료**(내용 줄과 날짜 항목은 읽을 때 건너뛰고 compact 때 정리) |
| ③ 카테고리 −1 을 저장하기 전 | 건수만 1 많음 | 안전 |

### export — CSV 내보내기

거래 데이터 파일은 읽기만 하므로 **데이터는 전혀 영향을 받지 않습니다.** 내보낼 파일은 같은 폴더의 임시 파일(`.<파일이름>.tmp`, 예: `.out.csv.tmp`)에 끝까지 쓴 뒤 이름을 바꿔 한 번에 교체합니다.

| 꺼진 시점 | `--out` 파일 | 다음에 |
|---|---|---|
| 임시 파일에 쓰는 도중 | **원래 파일 그대로**(없었으면 없음) | 다시 export. 남은 임시 파일은 같은 이름으로 내보낼 때 덮어써져 사라짐 |
| 이름을 바꾼 뒤 | 완성된 새 파일 | 할 일 없음 |

Ctrl+C 나 오류로 멈추면 임시 파일도 바로 지웁니다. 이미 있던 파일을 덮어쓸 때는 그 파일의 권한을 그대로 유지합니다.

### compact — 옛 줄 정리

| 꺼진 시점 | 남는 것 | 다음 명령에서 |
|---|---|---|
| ① 위치 복사본을 임시 이름(`…compact-backup.tmp`)으로 만드는 중 | 반쯤 만든 복사본 | 지움. 위치 파일은 아직 그대로라 compact 전 상태 |
| ② 새 내용 파일(`transactions.jsonl.tmp`)을 쓰면서 위치 칸을 새 위치로 고치는 중 | 복사본 + `.tmp` + 일부만 고쳐진 위치 파일 | **복사본으로 위치 파일을 되돌리고 `.tmp` 를 지움** → compact 전 상태. 잃는 데이터 없음, 다시 compact |
| ③ `.tmp` 를 진짜 `transactions.jsonl` 로 바꾼 뒤(완료), 복사본을 지우기 전 | 복사본만 남음 | 새 내용과 새 위치가 이미 짝이 맞으므로 **복사본만 지움** |
| ④ 날짜 인덱스를 다시 만드는 중 | 옛 날짜 인덱스 + `transactions.date.idx.tmp` | 날짜 인덱스는 위치가 아니라 id 를 담고 있어 옛것도 맞게 읽힘(지울 항목만 남아 있음). `.tmp` 는 다음에 다시 만들 때 덮어씀 |
| ⑤ 카테고리 건수를 다시 세기 전 | 건수가 많게 틀려 있을 수 있음 | 안전 |

②·③ 을 구분하는 방법: 복사본이 남아 있을 때 `transactions.jsonl.tmp` 도 남아 있으면 ②(교체 전), 없으면 ③(교체 후)입니다.

### category — 카테고리 추가·삭제

`categories.jsonl` 은 임시 파일에 전부 쓴 뒤 이름을 바꿔 교체하고, 이름 바꾸기도 폴더까지 디스크에 확정합니다(`budgets.jsonl`, `recurring.jsonl`, 날짜 인덱스를 병합·재생성할 때, 되돌리기 기록을 만들고 지울 때도 같음). 확정하지 않으면 전원이 꺼진 뒤 교체가 되돌아가 "건수를 거래보다 먼저 늘린다"는 순서가 뒤집힐 수 있기 때문입니다. 꺼지면 **바꾸기 전 파일 그대로이거나 바뀐 파일** 둘 중 하나이고, 남은 임시 파일은 다음 저장 때 덮어씁니다. 삭제는 사용 건수가 0 이면 바로, 0 이 아니면 거래를 확인한 뒤 실행합니다.

### recurring apply — 반복 거래 만들기

규칙마다 "거래 1건 저장(add 와 같음) → 규칙에 이번 달 적용함 기록" 순서로 진행합니다. 만든 거래에는 어느 규칙의 몇 월분인지 표시(`"source": "RC-1/2024-03"`)를 함께 저장합니다(직접 등록한 거래에는 이 필드가 없음).

| 꺼진 시점 | 남는 것 | 다시 apply 하면 |
|---|---|---|
| ① 거래를 저장하는 도중 | add 표와 같음(저장 안 됨) | 그 규칙의 거래를 새로 만듦 |
| ② 거래는 저장됐고 "적용함"을 기록하기 전 | 거래 1건, 규칙에는 미적용 | 그 달에 같은 표시의 거래가 이미 있으므로 **새로 만들지 않고 기록만 마저 함**("이미 적용되어 건너뜀") |
| ③ 기록까지 끝난 뒤 | 없음 | 건너뜀 |

② 를 확인할 때는 그 달 구간의 거래만 읽습니다.

### 카테고리 사용 건수가 틀렸을 때 바로잡는 곳

| 상황 | 바로잡는 곳 |
|---|---|
| 실제보다 많음(위의 꺼짐 대부분) | 그 카테고리를 지울 때 거래를 확인해 쓰는 거래가 없으면 삭제 허용 / `compact` 때 다시 셈 |
| 줄이다가 0 아래로 내려감(직접 편집 등으로 적어진 경우) | 그 순간 거래를 처음부터 다시 셈 |
| `count` 가 없는 예전 파일 | 다음 명령 시작 때 다시 셈 |

### 한계 (자동으로 복구되지 않는 것)

- 이 프로그램이 하는 확정(`fsync`)은 운영체제까지입니다. 디스크 장치가 받은 데이터를 자체 캐시에 들고 있다가 전원과 함께 잃는 경우는 막지 못합니다(macOS 의 일반 `fsync` 는 장치 캐시까지 비우지 않습니다).
- 강제 종료 시험은 프로세스 강제 종료와 파일 바이트 손상으로 했고, 실제 전원 차단 장비로 시험하지는 않았습니다.
- 되돌리기 기록(`.slot-undo`) 자체가 망가지면(길이가 72바이트가 아니거나, 앞 40바이트의 SHA-256 이 함께 저장한 해시와 다르면) 무엇을 되돌릴지 추측하지 않고 `[오류]` 로 멈춥니다.
- 디스크 고장이나 직접 편집으로 파일 내용 자체가 망가진 경우는 위 순서로 막을 수 없습니다. `backup` 으로 만든 폴더의 파일을 `data/` 에 복사해 되돌리세요.
- 같은 `data-dir` 을 여러 프로세스가 동시에 쓰는 것은 지원하지 않습니다.

### 바이너리 검증 팁
transactions.idx 내용
```
od -t u8 data/transactions.idx 
0000000                                 0                               133
0000020                                 134                               247
0000040                                 611                               747
0000060                                 363                               488
0000100                                 489                               610
```
transactions.date.idx 내용
```
hexdump -v -e '1/4 "%u," 1/8 "%u\n"' data/transactions.date.idx | while IFS=, read -r val1 val2; do
    [ -z "$val1" ] && continue
    echo "$(date -r $(( (val1 - 719163) * 86400 )) "+%Y-%m-%d"),$val2"
done
```
