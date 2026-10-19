# AllowanceTracker — 콘솔 가계부 (`budget_app`)

표준 라이브러리만 사용하는 CLI 가계부입니다. 거래를 기록/검색/수정/삭제하고, 월별 요약과 예산 사용률을 확인하며, CSV 로 주고받을 수 있습니다.
(요구사항 원문: [`doc/request.md`](doc/request.md))

## 1. 환경 구축

- Python **3.10 이상**, 외부 라이브러리 없음(`pip install` 불필요)

```bash
git clone https://github.com/aha645/AllowanceTracker.git
cd AllowanceTracker
python --version                           # 3.10 이상인지 확인
python -m budget_app --help                # 전체 도움말 (서브커맨드: python -m budget_app <command> --help)
```

첫 실행 때 현재 폴더에 `data/` 폴더와 아래 파일들이 자동으로 만들어집니다(`--data-dir PATH` 로 저장 폴더 변경 가능).

```text
data/
├── transactions.jsonl       거래 내용 원본. 한 줄이 거래 1건(JSON)이며 끝에만 덧붙임        [필수 3종]
├── categories.jsonl         등록한 카테고리 목록과 카테고리별 사용 건수                   [필수 3종]
├── budgets.jsonl            월별 예산                                                     [필수 3종]
├── transactions.idx         거래 id → jsonl 안의 위치 (조회·수정·삭제용 색인)
├── transactions.date.idx    (거래일자, id) 날짜순 목록 (최신순·기간 조회용 색인)
├── recurring.jsonl          반복 거래 규칙 (보너스)
└── backup/                  `backup` 명령을 실행하면 생기는 타임스탬프 백업 폴더 (보너스)
```

- 구성: `cli`(명령 연결) → `commands/`(기능별 핸들러) → `services`(규칙) → `repository`·`stores`(파일 저장), 공통 처리는 데코레이터 `@command`.

### 테스트 실행

```bash
python -m unittest discover -s tests -v    # 전체 17개 (1초 안팎)

# 090(import) 까지만: 000 부터 그 번호까지 함께 지정 (-k 는 이름에 그 글자가 든 테스트를 고름)
python -m unittest tests.test_requirement_checklist -v -k 000 -k 010 -k 020 -k 030 -k 040 -k 050 -k 060 -k 070 -k 080 -k 090
```

- `tests/test_requirement_checklist.py` 하나에 요구사항 순서대로 17개가 있습니다. `000` 데이터 초기화 → `010`~`095` 10대 기능(add, list, search, summary, budget, category, update, delete, import, export) → `100`~`150` 응용 기능(로그·종료 코드, 표 형식, 저장 원자성, 백업, 반복 거래, 날짜 인덱스).
- 실행 중 명령과 출력이 그대로 보이고, 데이터는 `tests/data/` 에 만들어집니다(`./data` 는 건드리지 않음).
- 앞 테스트가 만든 데이터를 뒤 테스트가 이어받습니다. **테스트 하나만 따로 실행하면 실패할 수 있습니다.** 직전 실행이 남긴 데이터 위에서 동작하기 때문입니다(예: `-k 090` 만 실행하면 같은 CSV 를 한 번 더 가져와 합계가 달라짐). 위 예시처럼 `000`(데이터 초기화)부터 함께 실행하세요.
- 타입 검사(선택): `python -m pip install mypy` 후 `python -m mypy budget_app tests tools`
- 10만 건 이상 대용량 테스트는 [5부](#5-대용량10만-건-이상-데이터-테스트)를 보세요.

## 2. 10대 기능 실행 결과

아래는 **위에서 아래로 순서대로 실제 실행한 결과**입니다(앞 단계에서 만든 데이터를 뒤 단계가 이어받음). 각 항목의 ▶ 를 누르면 명령과 결과가 펼쳐집니다.
직접 따라 하려면 먼저 `rm -rf ./data` 로 데이터를 비우고(⚠ 기존 데이터 삭제) 같은 명령을 순서대로 입력하세요.
`(종료 코드 N)` 은 오류로 끝난 경우에만 표시했습니다(정상은 0).

<details>
<summary><b>1. add — 거래 추가</b></summary>

카테고리가 없으면 차단 → 카테고리 등록 → 대화형/옵션 방식 등록 → 잘못된 입력 거부 → 대화형 재입력

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

$ python -m budget_app add
[안내] 거래 정보를 입력하세요. (Ctrl+C 로 취소)
날짜 (YYYY-MM-DD, 엔터=2024-02-01): 2024-01-05
타입 (income/expense, 엔터=expense): expense
등록된 카테고리: 1) 식비, 2) 교통, 3) 월급, 4) 월세
카테고리 (번호 또는 이름): 식비
금액 (양의 정수): 12000
메모 (선택, 엔터=건너뛰기): 점심
태그 (선택, 쉼표 구분): 외식
[저장 완료] id=TX-1

$ python -m budget_app add --date 2024-01-10 --type expense --category 교통 --amount 20000 --memo 지하철
[저장 완료] id=TX-2

$ python -m budget_app add --date 2024-01-25 --type income --category 월급 --amount 3000000 --memo "1월 급여"
[저장 완료] id=TX-3

$ python -m budget_app add --date 2024-01-31 --type expense --category 월세 --amount 500000 --memo "1월 월세"
[저장 완료] id=TX-4

$ python -m budget_app add --date 2024-13-40 --type expense --category 식비 --amount 1000
[오류] 날짜 형식이 올바르지 않습니다: '2024-13-40'
[힌트] YYYY-MM-DD 형식으로 입력하세요. 예: 2024-01-15
(종료 코드 1)

$ python -m budget_app add --date 2024-01-01 --type expense --category 식비 --amount -500
[오류] 금액은 0보다 커야 합니다: -500
[힌트] 지출/수입 구분은 --type 으로 하고, 금액은 항상 양수로 입력하세요.
(종료 코드 1)

$ python -m budget_app add --date 2024-01-01 --type transfer --category 식비 --amount 1000
[오류] argument --type: invalid choice: 'transfer' (choose from income, expense)
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
메모 (선택, 엔터=건너뛰기): 
태그 (선택, 쉼표 구분): 
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

예산 설정 → summary 사용률 → 덮어쓰기 → 초과 경고 → 0원은 오류

```text
$ python -m budget_app budget set --month 2024-01 --amount 600000
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

등록 / 중복 차단 / 목록 / 사용 중인 카테고리는 삭제 차단 / 미사용 삭제 / 없는 카테고리

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

$ python -m budget_app category remove --name 여가
[삭제 완료] 카테고리 '여가'

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
카테고리 [식비] (번호 또는 이름): 
금액 [12000]: 15000
메모 [점심]: -
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

삭제 → 목록/요약에서 제외 → 이미 삭제했거나 없는 id 는 오류

```text
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

유효한 행만 등록하고 잘못된 행은 사유와 함께 건너뜀 → 요약에 반영

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
[오류] argument --type: invalid choice: 'transfer' (choose from income, expense)
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
| `amount` | ✅ | 양의 정수 |
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

매월 31일 규칙 등록 → 2월에 적용(말일 2024-02-29 로 보정) → 같은 달 재적용은 건너뜀 → 삭제

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

수정은 로그에 새 버전을 덧붙이고 인덱스 슬롯만 덮어씀(줄 수 +1, 인덱스 크기 그대로), 삭제는 로그를 건드리지 않음. 카테고리/예산 파일은 임시파일 + 교체로 저장

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
카테고리 [식비] (번호 또는 이름): 
금액 [15000]: 16000
메모 []: 
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

id 는 가장 크지만 거래일자는 가장 오래된 거래가 맨 위로 올라오지 않음 → 날짜 변경 시 위치 이동 → 삭제 후 compact → 인덱스 삭제 후 자동 복구

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
카테고리 [식비] (번호 또는 이름): 
금액 [7000]: 
메모 [옛거래]: 
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
로그 크기          1,511B  781B
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

### 저장 파일 (`./data`)

| 파일 | 내용 | 방식 |
|---|---|---|
| `transactions.jsonl` | 거래 **내용** 원본 (JSON 1줄 = 1건) | 항상 끝에만 추가 (수정 전 내용도 남음) |
| `transactions.idx` | **id → 현재 내용의 위치** (슬롯 N = id N, 삭제는 `(0, 0)`, 슬롯당 16바이트) | 슬롯 덮어쓰기 |
| `transactions.date.idx` | **(거래일자, id) 날짜순 목록** (항목당 12바이트) — 최신순·기간 조회용 | 끝에 추가 또는 병합 후 교체 |
| `categories.jsonl` · `budgets.jsonl` | 카테고리와 **사용 건수** / 월별 예산 | 전체 재작성 (임시파일 + `os.replace`) |
| `recurring.jsonl` | 반복 거래 규칙 (보너스) | 전체 재작성 (임시파일 + `os.replace`) |
| `backup/` | `backup` 명령이 만드는 백업 | 타임스탬프 폴더 |

### 저장 형식 선택 — JSONL vs CSV

내부 저장은 **JSONL**, 사람·엑셀과 주고받는 용도(`import`/`export`)는 **CSV** 로 나눴습니다.

| 기준 | JSONL (내부 저장) | CSV (import/export) |
|---|---|---|
| 메모에 쉼표·따옴표·줄바꿈 | 그대로 저장 (JSON 이 이스케이프) | 따옴표로 감싸야 하고, 직접 편집하다 깨지기 쉬움 |
| 태그 같은 목록 값 | `["외식", "점심"]` 배열 그대로 | `"외식,점심"` 문자열로 바꿨다가 다시 나눠야 함 |
| 숫자/문자 구분 | `amount` 가 숫자로 저장됨 | 모든 값이 문자열이라 읽을 때마다 변환·검증 |
| 끝에 덧붙이기 (append) | 한 줄이 독립 레코드라 안전, 헤더 없음 | 헤더와 컬럼 순서가 고정돼 있어야 함 |
| 위치 색인(`transactions.idx`)과의 결합 | 한 줄 = 한 레코드라 byte 범위로 바로 읽음 | 따옴표 안 줄바꿈 때문에 "한 줄 = 한 레코드"가 보장되지 않음 |
| 필드 추가 (스키마 변경) | 새 키만 쓰면 되고 옛 줄도 그대로 읽힘 | 모든 행의 컬럼 수를 맞춰야 함 |
| 엑셀에서 열기·용량 | 불가, 키 이름이 줄마다 반복돼 더 큼 | **가능**, 더 작음 |

**결론:** 이 앱의 저장 엔진은 "끝에만 덧붙이고, 위치 색인으로 한 줄만 읽는다"가 핵심이라, 한 줄이 반드시 한 레코드인 JSONL 이 맞습니다. CSV 의 장점(엑셀 호환)은 사람과 주고받을 때만 필요하므로 `import`/`export` 에서만 씁니다.

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
- `compact` 는 건수를 거래에서 처음부터 다시 셉니다. 건수 기록 이전 형식(`count` 없음)의 파일은 다음 실행 때 한 번 세어 채웁니다.
- 한계: 직접 편집 등으로 건수가 실제보다 **적어지면**, 줄이다가 0 아래로 내려갈 때만 알아채고 다시 셉니다. 그 전에 그 카테고리를 지우면 사용 중인데도 삭제될 수 있으므로, 파일을 직접 고쳤다면 `compact` 를 실행하세요.

### 최신 거래의 기준

최신은 **`(거래일자, id)`** 로 정합니다. 거래일자가 늦은 것이 최신이고, 같은 날짜면 id 가 큰(나중에 등록한) 것이 최신입니다.
`list` 는 날짜순 목록(`transactions.date.idx`)을 **맨 끝부터 거꾸로** 읽으며 해당 거래만 `jsonl` 에서 읽다가 `--limit` 에서 멈춥니다(제너레이터, 전체를 읽지 않음).
옛 날짜 거래를 나중에 가져와도 id 만 커질 뿐 날짜순 목록에서는 제 날짜 위치에 들어가므로 목록 순서가 어긋나지 않습니다.

### 동작별로 파일이 바뀌는 방식

| 동작 | `transactions.jsonl` | `transactions.idx` | `transactions.date.idx` |
|---|---|---|---|
| **add** | 끝에 한 줄 추가 | 슬롯 추가 | 날짜가 가장 늦으면 끝에 추가, 아니면 제 위치에 끼워 넣기 |
| **import** | 유효한 행을 한 번에 끝에 추가 | 슬롯을 한 번에 추가 | 새 항목을 정렬해 한 번에 병합 |
| **update** | 수정본 전체를 끝에 추가 (옛 줄은 남음) | 그 id 슬롯을 새 위치로 덮어씀 | 날짜가 바뀐 경우에만 새 항목 추가 (옛 항목은 읽을 때 무시) |
| **delete** | 변화 없음 | 그 id 슬롯을 `(0, 0)` 으로 | 변화 없음 (삭제된 슬롯은 읽을 때 무시) |
| **compact** | 살아있는 최신본만 새 파일로 옮겨 씀 | 슬롯 번호 그대로, 위치만 갱신 | 살아있는 거래로 처음부터 다시 만듦 |

- update·delete 로 남은 옛 줄과 옛 항목은 읽을 때 건너뛰고, `python -m budget_app compact` 가 정리합니다. **id 는 바뀌지 않고** 삭제된 번호는 재사용되지 않습니다.
- `transactions.date.idx` 는 `jsonl`/`idx` 에서 언제든 다시 만들 수 있어서, 없거나 손상되면 다음 실행 때 자동으로 재생성됩니다.
- 쓰는 순서는 `jsonl`(내용) → `transactions.date.idx`(날짜 항목) → `transactions.idx`(위치) 이고, **위치를 쓰는 순간이 저장 완료**입니다. 그 전에 꺼지면 날짜 항목만 남는데, 아직 위치가 없는 id 라서 읽을 때 건너뛰고 그 id 는 다음 저장 때 다시 쓰입니다. 그래서 저장된 거래가 목록·요약에서만 빠지는 일이 없습니다.
- id 0 처럼 정상적으로 생길 수 없는 날짜 항목을 만나면 건너뛰지 않고 `[오류]` 와 함께 `compact`(날짜 인덱스 재생성)를 안내합니다.
- 디스크 오류나 직접 편집으로 날짜 항목이 **다른 거래를 가리키게** 손상되면, 읽는 중에는 수정 전 옛 항목과 구분할 수 없어 그 거래가 목록에서 빠집니다. 대신 `list` 가 끝까지 읽었을 때(`--all` 이거나 `--limit` 에 닿지 않음) 출력 건수가 저장된 거래 수와 다르면 `[경고]` 와 함께 `compact` 를 안내합니다.
- `add`/`import` 가 위치(`transactions.idx`)를 쓰다 꺼져 끝에 16바이트가 안 되는 조각이 남으면, 다음 실행 때 잘라 내고 `[안내]` 로 알립니다. 그 거래는 저장되지 않은 것이고, 남은 `jsonl` 줄과 날짜 항목은 위처럼 건너뛰다가 `compact` 때 정리됩니다.
- `compact` 는 위치를 제자리에서 고치므로, 시작 전에 `transactions.idx.compact-backup` 복사본을 만듭니다(임시 이름으로 다 쓰고 확정한 뒤 이름을 바꿔, 이 이름의 파일은 항상 완전함). 도중에 꺼지면 다음 실행 때 자동으로 정리하고 `[안내]` 로 알립니다.
  - `transactions.jsonl.tmp` 가 남아 있으면 로그 교체 전에 멈춘 것 → 복사본으로 위치를 되돌림(compact 전 상태, 잃는 데이터 없음. 다시 실행하면 됨)
  - 없으면 교체는 끝난 것 → 새 로그와 새 위치가 이미 짝이라 복사본만 지움
- 한계: 위 건수 검사는 `list` 에만 있어서, `search`/`summary`/`export` 는 손상된 상태에서도 경고 없이 빠진 결과를 냅니다(`compact` 로 복구). `transactions.idx` 는 삭제해도 줄지 않으며, 같은 `--data-dir` 의 동시 실행은 고려하지 않았습니다(1인 사용 전제).

## 5. 대용량(10만 건 이상) 데이터 테스트

### 실행 방법

```bash
python tools/bench_large.py              # 10만 건
python tools/bench_large.py --n 300000   # 건수 지정 (100만 건은 몇 분 걸림)
```

임시 폴더에 5년치(2020~2024) 거래 N건을 CSV 로 만들어 `import` 한 뒤, 명령마다 실제 CLI 를 새 프로세스로 실행해 **시간**과 **최대 메모리(RSS)** 를 잽니다. 끝나면 임시 폴더를 지우므로 `./data` 는 건드리지 않습니다.
파이썬 시작 비용이 모든 수치에 들어가므로, 빈 저장소에서 `category list` 를 실행한 값(**0.07s, 14MB**)을 기준선으로 함께 봅니다.

### 결과 (macOS, Python 3.10)

| 명령 | 10만 건 | 30만 건 | 100만 건 | 판정 |
|---|---:|---:|---:|---|
| `list --limit 20` | 0.11s · 14MB | 0.17s · 14MB | 0.39s · 14MB | 거의 일정 (병목 ⑤) |
| `summary --month 2024-06` | 0.10s · 14MB | 0.15s · 15MB | 0.33s · 15MB | 그 달 건수에 비례 (정상) |
| `search --month 2024-06 --category 식비` | 0.07s · 14MB | 0.07s · 14MB | 0.08s · 14MB | 일정 |
| `search --category 식비 --limit 20` | 0.07s · 14MB | 0.07s · 14MB | 0.07s · 14MB | 일정 |
| `add` 가장 늦은 날짜 | 0.07s · 14MB | 0.07s · 14MB | 0.08s · 14MB | 일정 |
| `add` 옛 날짜 | 0.11s · 15MB | 0.19s · 16MB | 0.43s · 16MB | 병목 ③ |
| `category remove` (사용되지 않는 카테고리) | 0.07s · 14MB | 0.07s · 14MB | 0.07s · 14MB | 일정 (개선 적용, 예전 100만 건 15.44s) |
| `import` (빈 저장소에 N건) | 6.31s · 23MB | 18.93s · 38MB | 62.16s · 90MB | 병목 ① |
| `search --q 없는말` (기간 조건 없음) | 1.62s · 14MB | 4.81s · 14MB | 16.39s · 14MB | **병목 ②** |
| `export` 전체 기간 | 2.07s · 14MB | 6.32s · 14MB | 20.71s · 15MB | **병목 ②** |
| `compact` | 1.72s · 48MB | 5.17s · 112MB | 17.76s · 352MB | **병목 ④** |

| 파일 | 10만 건 | 30만 건 | 100만 건 |
|---|---:|---:|---:|
| `transactions.jsonl` | 13.2MB | 40.0MB | 133.8MB |
| `transactions.idx` | 1.5MB | 4.6MB | 15.3MB |
| `transactions.date.idx` | 1.1MB | 3.4MB | 11.4MB |

**잘 되는 부분:** 기간을 주거나 `--limit` 이 있는 조회와 일반 `add` 는 100만 건에서도 기준선과 거의 같고, **조회 메모리는 14~15MB 로 건수와 무관**합니다. 날짜 인덱스를 이진 탐색해 필요한 구간만 읽고(100만 건에서 약 20번 읽기), 제너레이터라 `--limit` 에서 멈추기 때문입니다.

### 병목과 개선안

**① `import` — 시간과 메모리가 건수에 비례 (개선 적용됨)**

- 적용한 개선: 행마다 `categories.jsonl` 을 두 번 열던 것을 시작할 때 한 번만 읽고, 유효한 행을 리스트에 모으지 않고 CSV 를 두 번 읽으며(1차 검증, 2차 바로 저장) 저장하고, 색인 값은 객체 대신 `bytearray`·`array("q")` 에 담습니다. 10만 건 기준 **20.90s · 123MB → 6.31s · 23MB**.
- 남은 원인: 날짜 인덱스를 한 번에 병합하려고 새 항목을 모두 모아 정렬하므로 메모리가 건당 약 70바이트씩 늘고(100만 건 91MB), CSV 를 두 번 읽고 검증해 100만 건에 약 1분이 걸립니다.
- 개선안: 정렬을 조각 파일로 나눠 하는 **외부 정렬**(④와 같은 방법)로 메모리를 고정합니다. 시간은 2차 읽기 때 검증을 다시 하지 않도록 1차 결과(유효한 줄 번호)를 비트 배열로 남기면 줄일 수 있습니다.

**② 기간 조건 없는 전체 훑기 — 시간이 건수에 비례 (100만 건 16~21초)**

- 원인: `search --q`/`--tag`(기간 없음), 전체 `export` 는 날짜 인덱스 전체를 돌며 거래마다 `transactions.idx` 와 `transactions.jsonl` 에서 위치를 옮겨(seek) 읽고 JSON 을 해석합니다. 메모리는 늘지 않습니다.
- 적용한 개선 (`category remove`): 사용 여부를 확인하려고 전체를 훑던 것을, `categories.jsonl` 에 저장한 카테고리별 **사용 건수**로 바로 판단합니다(4부 참고). 사용되지 않는 카테고리 삭제가 100만 건 기준 **15.44s → 0.07s**. 사용 중인 카테고리는 최근 거래에서 금방 찾아 거부합니다. 대가로 `add`·`delete`·카테고리를 바꾸는 `update` 가 `categories.jsonl` 을 한 번 더 쓰지만, 카테고리가 수십 개라 측정상 차이가 없었습니다(`add` 0.07~0.08s).
- 개선안 (`export` 전체, 기간 없는 `search`): 범위가 넓으면 `transactions.jsonl` 을 **처음부터 순서대로** 읽고(살아있는 줄인지는 `transactions.idx` 로 확인) 결과만 날짜순으로 정렬합니다. 위치 이동이 없어지는 대신 결과 건수만큼 메모리를 씁니다.
- 개선안 (메모 검색): 자주 쓴다면 단어 → id 목록 색인 파일을 따로 둡니다. 쓰기가 느려지고 파일이 하나 늘어납니다.

**③ 옛 날짜 `add` — 날짜 인덱스 파일 전체를 다시 씀 (100만 건 0.42초)**

- 원인: 가장 늦은 날짜보다 이른 거래는 `transactions.date.idx` 를 처음부터 다시 쓰며 끼워 넣습니다(`DateIndex.merge`, 100만 건이면 매번 11.4MB).
- 개선안: 옛 날짜 항목은 작은 **보조 파일에 모아 두고** 읽을 때 두 파일을 합쳐 읽다가, 보조 파일이 일정 크기를 넘거나 `compact` 할 때 한 번에 병합합니다.

**④ `compact` — 메모리가 건수에 비례 (100만 건 349MB)**

- 원인: `rebuild_date_index()` 가 살아있는 거래의 `(날짜, id)` 를 튜플로 모두 모아 메모리에서 정렬합니다. 또 카테고리 사용 건수를 다시 세려고 거래 전체를 한 번 더 읽어서, 건수 저장을 넣은 뒤 시간이 늘었습니다(100만 건 11.20s → 17.76s).
- 개선안: ①과 같은 정수 배열로 바꾸면 건당 약 120바이트가 8바이트가 되고, 일정 개수씩 정렬한 조각 파일을 `heapq.merge` 로 합치는 **외부 정렬**을 쓰면 메모리가 조각 크기로 고정됩니다. 카테고리 건수는 날짜 인덱스를 다시 만들 때 이미 거래를 하나씩 읽으므로, 그때 함께 세면 추가로 읽지 않아도 됩니다.

**⑤ `list` 의 전체 건수 — 위치 인덱스 전체를 훑음 (100만 건 0.42초)**

- 원인: "`N건 출력 / 전체 M건`" 과 건수 불일치 경고를 위해 `stats()` 가 `transactions.idx` 의 모든 칸(100만 건이면 15.3MB)을 파이썬 반복문으로 셉니다.
- 개선안: 살아있는 건수를 작은 메타 파일에 저장해 add/delete/import 때 갱신하거나, 삭제된 칸 수만 세어 두고 `칸 수 − 삭제 수` 로 계산합니다.

### 우선순위

1. ~~② 카테고리 사용 건수~~: **적용 완료** (`category remove` 100만 건 15.44s → 0.07s).
2. **④ compact**: 정수 배열로 352MB 를 크게 줄이고, 카테고리 건수를 날짜 인덱스 재생성과 한 번에 세어 늘어난 시간을 되돌립니다.
3. ⑤, ③, ②의 순차 읽기, ①·④의 외부 정렬은 100만 건 이상을 자주 다룰 때 적용합니다.

## 6. 작업 도중 전원이 꺼지면 — 명령별 복구 방식

### 먼저 알아 둘 세 가지 원칙

1. **저장 완료는 `transactions.idx`(위치)에 쓰는 순간 하나로 정합니다.** 내용(`transactions.jsonl`)과 날짜 순서(`transactions.date.idx`)는 그보다 **먼저** 씁니다. 그래서 위치를 쓰기 전에 꺼지면, 먼저 써 둔 것들은 "아무 위치도 가리키지 않는 쓰레기"가 되어 읽을 때 무시되고 `compact` 때 지워집니다.
2. **카테고리 사용 건수(`count`)는 실제보다 많게만 틀리게 합니다.** 늘릴 때는 거래보다 먼저, 줄일 때는 거래보다 나중에 바꿉니다. 많게 틀리면 "안 쓰는 카테고리를 지울 때 거래를 한 번 확인한다" 정도로 끝나지만, 적게 틀리면 쓰는 카테고리를 지울 수 있기 때문입니다.
3. **복구는 다음에 실행하는 아무 명령이 시작할 때 자동으로 합니다.** `list` 든 `add` 든 상관없습니다(도움말 `--help` 와 명령 없이 실행한 경우는 제외). 바로잡은 것이 있으면 맨 위에 `[안내]` 로 알려 줍니다.

### 모든 명령이 시작할 때 하는 일 (이 순서대로)

| 순서 | 확인하는 것 | 발견하면 |
|---|---|---|
| 1 | `transactions.idx.compact-backup` 이 남아 있나 (compact 가 끝나지 않음) | 아래 compact 표대로 되돌리거나 마무리 |
| 2 | `transactions.idx` 끝에 16바이트가 안 되는 조각이 있나 (add/import 가 위치를 쓰다 꺼짐) | 조각을 잘라 냄. 그 거래는 저장되지 않은 것 |
| 3 | `transactions.date.idx` 가 없거나 크기가 12의 배수가 아닌가 | 위치·내용 파일에서 처음부터 다시 만듦 |
| 4 | `categories.jsonl` 에 `count` 가 없는 예전 형식인가 | 거래를 한 번 세어 채움 |

### add — 거래 1건 추가

| 꺼진 시점 | 남는 것 | 다음 명령에서 |
|---|---|---|
| ① 카테고리 건수 +1 을 저장한 뒤 | 건수만 1 많음 | 그대로 둠(안전). 그 카테고리를 지울 때 거래를 확인해 보고 허용 |
| ② `jsonl` 에 내용을 쓰는 도중 / 쓴 뒤 | 반쯤 쓴 줄 또는 완전한 줄 | 무시됨(위치가 없음). 다음 add 의 줄이 그 뒤에 이어 붙어 한 줄로 보일 수 있지만 읽기에는 지장 없고 compact 때 정리 |
| ③ 날짜 항목을 쓴 뒤 | 위치 칸 수보다 큰 id 의 날짜 항목 | 읽을 때 건너뜀. 그 id 는 다음 add 가 다시 받고, 남은 항목은 날짜가 안 맞거나 중복이라 함께 무시됨 |
| ④ 위치 칸을 쓰는 도중 | 16바이트가 안 되는 조각 | 시작 단계 2 에서 잘라 냄 → 저장 안 된 것으로 처리 |
| ⑤ 위치 칸을 다 쓴 뒤 | 없음 | **저장 완료** |

결과: 꺼진 add 는 **완전히 저장됐거나, 아예 없던 일** 둘 중 하나입니다. 목록에만 빠지고 id 로는 찾히는 식의 어중간한 상태는 생기지 않습니다.

### import — CSV 여러 건 가져오기

| 꺼진 시점 | 남는 것 | 다음 명령에서 |
|---|---|---|
| ① 1차 읽기(검증) 중 | 없음 | 아무것도 저장 안 됨. 다시 import |
| ② 카테고리별 건수 합계를 **한 번에** 저장한 뒤 | 건수만 많음 | 안전(add ① 과 같음) |
| ③ 2차 읽기로 `jsonl` 에 쓰는 중 / 날짜 항목을 쓴 뒤 | 위치 없는 줄들, 칸 수보다 큰 id 의 날짜 항목들 | 모두 무시됨 |
| ④ 위치 칸 묶음을 쓰는 도중 | 일부 칸만 써짐(끝에 조각이 있을 수 있음) | 조각은 잘라 내고, **완전히 써진 칸의 거래까지만 저장된 것**이 됨 |
| ⑤ 위치 칸을 다 쓴 뒤 | 없음 | **저장 완료** |

④ 의 경우 앞쪽 일부 행만 들어갔을 수 있으니, `list` 로 확인한 뒤 들어가지 않은 행만 다시 가져오세요(같은 CSV 를 통째로 다시 가져오면 앞쪽 행이 두 번 들어갑니다).

### update — 거래 수정

| 꺼진 시점 | 남는 것 | 다음 명령에서 |
|---|---|---|
| ① (카테고리를 바꾸는 경우) 새 카테고리 +1 을 저장한 뒤 | 새 카테고리 건수만 1 많음 | 안전 |
| ② 수정한 내용을 `jsonl` 끝에 쓴 뒤 | 위치 없는 새 줄 | 무시됨. 거래는 **수정 전 내용** 그대로 |
| ③ (날짜를 바꾸는 경우) 새 날짜 항목을 쓴 뒤 | 새 날짜 항목 | 위치가 아직 수정 전 내용을 가리켜 날짜가 안 맞으므로 무시됨. 목록에서 수정 전 날짜에 한 번만 나옴 |
| ④ 위치 칸 16바이트를 덮어쓴 뒤 | 없음 | **수정 완료** |
| ⑤ (카테고리를 바꾸는 경우) 옛 카테고리 −1 을 저장하기 전 | 옛 카테고리 건수만 1 많음 | 안전 |

결과: **수정 전 그대로이거나, 수정이 끝난 상태** 둘 중 하나입니다. 다시 수정하면 됩니다.

### delete — 거래 삭제

| 꺼진 시점 | 남는 것 | 다음 명령에서 |
|---|---|---|
| ① 위치 칸을 0 으로 덮어쓰기 전 | 없음 | 삭제 안 됨. 다시 delete |
| ② 위치 칸을 0 으로 덮어쓴 뒤 | 없음 | **삭제 완료**(내용 줄과 날짜 항목은 읽을 때 건너뛰고 compact 때 정리) |
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
| ④ 날짜 인덱스를 다시 만드는 중 | 옛 날짜 인덱스 또는 임시 파일 | 날짜 인덱스는 위치가 아니라 id 를 담고 있어 옛것도 맞게 읽힘(지울 항목만 남아 있음) |
| ⑤ 카테고리 건수를 다시 세기 전 | 건수가 많게 틀려 있을 수 있음 | 안전 |

②·③ 을 구분하는 방법: 복사본이 남아 있을 때 `transactions.jsonl.tmp` 도 남아 있으면 ②(교체 전), 없으면 ③(교체 후)입니다.

### category — 카테고리 추가·삭제

`categories.jsonl` 은 임시 파일에 전부 쓴 뒤 이름을 바꿔 교체합니다. 꺼지면 **바꾸기 전 파일 그대로이거나 바뀐 파일** 둘 중 하나이고, 남은 임시 파일은 다음 저장 때 덮어씁니다. 삭제는 사용 건수가 0 이면 바로, 0 이 아니면 거래를 확인한 뒤 실행합니다.

### recurring apply — 반복 거래 만들기

규칙마다 "거래 1건 저장(add 와 같음) → 규칙에 이번 달 적용함 기록" 순서로 진행합니다. 만든 거래에는 어느 규칙의 몇 월분인지 표시(`"source": "RC-1/2024-03"`)를 함께 저장합니다(직접 등록한 거래에는 이 필드가 없음).

| 꺼진 시점 | 남는 것 | 다시 apply 하면 |
|---|---|---|
| ① 거래를 저장하는 도중 | add 표와 같음(저장 안 됨) | 그 규칙의 거래를 새로 만듦 |
| ② 거래는 저장됐고 "적용함"을 기록하기 전 | 거래 1건, 규칙에는 미적용 | 그 달에 같은 표시의 거래가 이미 있으므로 **새로 만들지 않고 기록만 마저 함**("이미 적용되어 건너뜀") |
| ③ 기록까지 끝난 뒤 | 없음 | 건너뜀 |

예전에는 ② 에서 같은 거래가 한 번 더 생겼습니다. 이 확인은 그 달 구간만 읽습니다.

### 카테고리 사용 건수가 틀렸을 때 바로잡는 곳

| 상황 | 바로잡는 곳 |
|---|---|
| 실제보다 많음(위의 꺼짐 대부분) | 그 카테고리를 지울 때 거래를 확인해 쓰는 거래가 없으면 삭제 허용 / `compact` 때 다시 셈 |
| 줄이다가 0 아래로 내려감(직접 편집 등으로 적어진 경우) | 그 순간 거래를 처음부터 다시 셈 |
| `count` 가 없는 예전 파일 | 다음 명령 시작 때 다시 셈 |

### 한계 (자동으로 복구되지 않는 것)

- **`import` 위 ④**: 일부 행만 들어간 상태는 자동으로 되돌리지 않습니다.
- 이름 바꾸기로 교체하는 파일(`categories.jsonl`, `budgets.jsonl`, `recurring.jsonl`, 날짜 인덱스를 다시 쓸 때)은 폴더까지 디스크에 확정하지는 않습니다(`compact` 의 복사본·교체만 확정함). 운영체제에 따라 꺼진 직후 이름 바꾸기가 되돌아갈 가능성이 이론상 남아 있습니다.
- 디스크 고장이나 직접 편집으로 파일 내용 자체가 망가진 경우는 위 순서로 막을 수 없습니다. `backup` 으로 만든 폴더의 파일을 `data/` 에 복사해 되돌리세요.
