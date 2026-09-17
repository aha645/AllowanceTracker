# 콘솔 가계부(budget_app) 요구사항 체크리스트 & 실행 계획

> 이 문서는 미션 설명을 분석하여 만든 요구사항 체크리스트와, 각 항목을 만족하기 위해 해야 할 구체적인 작업 목록이다.
> 구현 시작 전 이 문서를 기준으로 진행 상황을 갱신한다.
>
> **진행 상황: 구현 완료** (2026-09-16). 아래 체크박스는 실제 구현 결과를 반영한다. 사용법·설계 설명은 [`../README.md`](../README.md) 참고. 검증: `python -m unittest discover -s tests` (테스트 50개).

---

## 0. 설계 확정 사항 (Decision Log)

과제 요구사항 상 "택 1"이 필요했던 항목들을 아래와 같이 확정한다. README.md에도 동일하게 명시해야 한다.

- [x] **저장 포맷**: **JSONL** (`transactions.jsonl`) — 특수문자/쉼표 포함 메모·태그 처리에 유리
- [x] **update 방식**: **옵션 기반** (`update --id <id> [--date ...] [--type ...] [--category ...] [--amount ...] [--memo ...] [--tags ...]`) — search/delete와 CLI 패턴 일관성, 자동화 가능
- [x] **카테고리 초기화 정책**: **안 B** — 카테고리가 비어있으면 `add`를 막고 `category add`를 먼저 하도록 안내 (기본 카테고리 자동 생성 없음)
- [x] **저장 폴더 기본값**: `./data` (`--data-dir` 옵션으로 변경 가능)
- [x] **거래 ID 형식**: zero-padding 없는 정수 표시 `TX-1`, `TX-2`, ... (내부적으로는 순번 정수, 화면 표시 시에만 `TX-` 접두어 부착). zero-padding(`TX-000012`) 방식은 채택하지 않음 — 자릿수 고정 시 대용량 데이터에서 자릿수 초과 문제가 생기기 때문
- [x] **저장 엔진 구조**: append-only 로그(JSONL) + 위치 기반 고정폭 이진 인덱스 조합 채택 (상세는 2번 섹션 참조)
- [x] **거래 id 재번호(renumbering) 정책**: **채택하지 않음**. 삭제 후에도 id는 재사용/재정렬하지 않으며, 중간에 빈 번호(gap)가 생기는 것을 정상 상태로 간주한다.
  - **이유**: (1) 레코드 본문에 id가 저장돼 있어 재번호하려면 해당 레코드를 다시 써야 하고, 자릿수가 바뀌면 append-only 로그의 불변 전제가 깨짐 (2) "위치=id"인 이진 인덱스 설계상 재번호는 뒤따르는 슬롯을 전부 밀어야 해서 O(1) 삭제가 O(n)이 됨 (3) 은행 거래번호·세금계산서 번호처럼 실제 회계 시스템도 취소된 번호를 재사용하지 않는 것이 감사(audit) 관점에서 안전함
  - **"빈 번호가 보기 싫다"는 심미적 요구는 화면 표시 계층에서 해결**: `list`/`search` 출력 시 왼쪽에 별도의 화면용 순번(1, 2, 3...)을 매겨서 보여주고, 내부 참조/검색/수정/삭제는 항상 불변 id(`TX-14`)를 그대로 사용한다.

---

## 1. 기능 요구사항 체크리스트 (10대 기능)

### 1-1. 거래 추가 (add)
- [x] 대화형 입력으로 날짜/타입/카테고리/금액/메모/태그 순차 입력받기
- [x] 날짜 형식(YYYY-MM-DD) 검증 → 실패 시 재입력 요구 또는 오류+힌트 출력
- [x] 타입은 `income`/`expense`만 허용 → 그 외 값 재입력 요구
- [x] 금액은 양수(0 초과)만 허용 → 음수/0/숫자 아님 시 재입력 요구
- [x] **카테고리 파일이 비어있으면 `add` 자체를 막고, `[오류] 등록된 카테고리가 없습니다 / [힌트] category add로 먼저 등록하세요` 출력 후 종료** (0번 확정: 안 B)
- [x] 카테고리는 등록된 목록에 존재해야 함 → 없으면 안내 후 재입력 요구 (자유 텍스트 오타/신규 카테고리 자동 생성 없음)
- [x] 메모/태그는 선택 입력(엔터로 스킵 가능), 태그는 쉼표 구분 파싱
- [x] 저장 성공 시 `[저장 완료] id=TX-3` 형태로 생성된 id 출력 (zero-padding 없음)
- [x] 저장 절차: (1) `transactions.jsonl`에 레코드 append → (2) 반환된 byte 범위로 `transactions.idx`의 해당 슬롯 기록 (2번 섹션 참조)

### 1-2. 거래 목록 (list)
- [x] `--limit N` 옵션 지원 (기본값 지정, 예: 20)
- [x] 최신순(id 내림차순) 정렬 출력 — `transactions.idx`를 뒤에서부터 순회하여 구현 (2번 섹션 참조)
- [x] id는 삭제돼도 재번호하지 않으므로 목록에 빈 번호(gap)가 나타날 수 있음 — 정상 동작 (0번 확정 참조)
- [x] (선택) 가독성을 위해 출력 왼쪽에 화면용 순번(1, 2, 3...)을 별도로 매겨 표시 가능 — 내부 id와는 무관한 표시 전용 값
- [x] 파일 전체를 메모리에 한 번에 로드하지 않고 **제너레이터(yield) 기반 스트리밍**으로 읽기
- [x] `--help` 로 사용법 출력 지원

### 1-3. 거래 검색 (search)
- [x] `--from`, `--to` (기간 필터)
- [x] `--category` (카테고리 필터)
- [x] `--type` (income/expense 필터)
- [x] `--q` (메모 키워드 검색)
- [x] `--tag` (태그 필터)
- [x] 여러 조건 동시 지정 시 AND 조건으로 결합
- [x] 결과 최신순 정렬 출력 (list와 동일한 `iter_latest_transactions()` 제너레이터를 필터링해서 재사용)
- [x] 제너레이터 기반 스트리밍 유지
- [x] `--help` 지원

### 1-4. 월별 요약 (summary)
- [x] `--month YYYY-MM` 필수 입력 처리 및 형식 검증
- [x] `--top N` 옵션 (기본값 지정, 예: 3)
- [x] 총 수입 / 총 지출 / 잔액(수입-지출) 계산 및 출력
- [x] 카테고리별 지출 합계 집계 후 TOP N 정렬 출력
- [x] 해당 월 데이터가 없을 경우 "데이터 없음" 명확히 출력 (0원 출력과 구분)
- [x] 예산이 설정된 경우: 사용률(%) 계산 및 출력
- [x] 예산 초과 시 경고 문구 출력
- [x] `--help` 지원

### 1-5. 예산 설정/조회 (budget)
- [x] `budget set --month YYYY-MM --amount <금액>` 구현
- [x] 금액 양수 검증
- [x] 저장 성공 메시지 출력 (`[저장 완료] 2024-01 예산 500000원`)
- [x] 같은 월 재설정 시 덮어쓰기 정책 결정 및 구현
- [x] budgets 파일에 영구 저장 (transactions/categories와 별도 파일)
- [x] summary 실행 시 budgets 조회하여 사용률/초과 여부 반영

### 1-6. 카테고리 관리 (category)
- [x] `category add` — 대화형 또는 옵션으로 카테고리명 입력, 중복 방지
- [x] `category list` — 전체 카테고리 목록 출력
- [x] `category remove` — 카테고리 삭제
- [x] 삭제 대상 카테고리를 사용 중인 거래가 있는지 검사 (`iter_latest_transactions()`를 조기 종료(early-exit) 스캔하여 판정 — 별도 카운터 캐시 불필요)
- [x] 사용 중이면: 삭제 차단 메시지 출력 (대체 카테고리 요구 방식은 채택하지 않음 — 단순성 우선)
- [x] categories 파일에 영구 저장

### 1-7. 거래 수정 (update)
- [x] **옵션 기반**으로 구현 (0번 확정): `update --id <id> [--date ...] [--type ...] [--category ...] [--amount ...] [--memo ...] [--tags ...]`
- [x] 지정하지 않은 필드는 기존 값 유지
- [x] `--id`로 `transactions.idx`에서 해당 슬롯 조회 → 존재하지 않거나 삭제된 슬롯(`start==end==0`)이면 "없는 데이터" 메시지 출력
- [x] 필드별 부분 수정 지원, 수정 시에도 값 검증(날짜 형식, 양수 금액, 허용 type, 존재 category) 동일 적용
- [x] 성공/실패 메시지 출력
- [x] 저장 절차: (1) 수정된 전체 레코드를 `transactions.jsonl` **끝에 append** → (2) 새 byte 범위로 `transactions.idx`의 **해당 id 슬롯을 in-place 덮어쓰기** (전체 파일 재작성 없음, 2번 섹션 참조)

### 1-8. 거래 삭제 (delete)
- [x] `delete --id <id>` 구현
- [x] `transactions.idx`에서 해당 슬롯 조회 → 존재하지 않거나 이미 삭제된 슬롯이면 오류 메시지 출력
- [x] 삭제 성공 메시지 출력
- [x] 저장 절차: `transactions.idx`의 해당 id 슬롯(16바이트)을 전부 0으로 덮어쓰기 (`transactions.jsonl`은 건드리지 않음 — 고아 데이터로 남았다가 `compact` 시 정리됨)

### 1-9. 가져오기/내보내기 (import/export)
- [x] `import --from <csv>` — CSV 읽어 거래 일괄 등록 (내부적으로 add와 동일한 append+idx 갱신 절차 재사용)
- [x] import 시 행 단위 검증(날짜/타입/카테고리/금액) 및 실패 행 skip 처리, 처리 건수(imported/skipped) 출력
- [x] `export --out <csv>` — 조건에 맞는 거래를 CSV로 저장
- [x] export는 `--month` 또는 (`--from` AND `--to`) 중 최소 1개 조건 필수 → 미지정 시 오류 처리
- [x] CSV 스키마 고정: `date, type, category, amount, memo, tags` (헤더 포함, UTF-8)
- [x] tags는 쉼표 구분 문자열로 CSV 내 저장 (내부 데이터의 리스트 ↔ CSV 문자열 변환 함수 필요)
- [x] 처리 건수 출력 (`[완료] export.csv (12 records)` 등)
- [x] `--help` 지원

### 1-10. 공통: --help
- [x] 모든 서브커맨드에서 `--help` 옵션으로 사용법 출력 (표준 라이브러리 `argparse` 활용 시 자동 제공됨)

---

## 2. 저장 엔진 설계 (Storage Engine Design) — 확정안

`transactions`에 한해 "append-only 로그 + 위치 기반 고정폭 이진 인덱스" 구조를 채택한다. `categories`/`budgets`는 데이터 양이 적으므로 단순 JSONL 전체 재작성(임시파일+`os.replace`) 방식으로 충분하다.

### 2-1. 파일 구성

| 파일 | 역할 | 포맷 |
|---|---|---|
| `data/transactions.jsonl` | 거래 원본 데이터. **append-only** (update도 새 버전을 끝에 append, 기존 내용은 절대 수정/삭제하지 않음) | 텍스트, JSON 한 줄 = 레코드 1개 |
| `data/transactions.idx` | id → 현재 유효 byte 위치 매핑. **위치 기반 고정폭 이진 배열** | 이진, 슬롯당 16바이트 |
| `data/categories.jsonl` | 카테고리 목록 | 텍스트 JSONL, 전체 재작성 방식 |
| `data/budgets.jsonl` | 월별 예산 | 텍스트 JSONL, 전체 재작성 방식 |

### 2-2. `transactions.idx` 슬롯 포맷

- 슬롯 크기: **16바이트** = `startOffset(8B, unsigned) + endOffset(8B, unsigned)` (`struct.pack("<QQ", start, end)`)
- **슬롯 위치 = 거래 id**: id `N`의 슬롯은 파일의 `(N-1) * 16` 바이트 위치에 있다 (id는 1부터 시작). 슬롯 안에 id 자체를 저장하지 않음 — 위치가 곧 id.
- **삭제 표시(sentinel)**: `start == 0 and end == 0` → 삭제됨. 실제 레코드는 항상 `end > start`이므로 TX-1(오프셋 0에서 시작)이 살아있어도 `(0, 0)`과 절대 혼동되지 않는다.
- **다음 id 계산**: `next_id = idx_파일크기 // 16 + 1`. 슬롯은 삭제돼도 파일에서 제거되지 않고 0으로만 초기화되므로, id가 재사용되는 일이 없다 (별도 카운터 파일 불필요).

### 2-3. 연산별 동작

| 연산 | transactions.jsonl | transactions.idx |
|---|---|---|
| add | 끝에 새 레코드 append, 시작/끝 offset 기록 | 파일을 16바이트만큼 늘리며 새 슬롯 `(start, end)` 기록 |
| update | **끝에 수정된 전체 레코드를 새로 append** (기존 줄은 그대로 방치) | 해당 id 슬롯 `(id-1)*16` 위치를 새 offset으로 **덮어쓰기** (`r+b` 모드) |
| delete | 아무 것도 하지 않음 | 해당 id 슬롯을 16바이트 전부 0으로 덮어쓰기 |
| compact | 살아있는 슬롯(`start != end`)의 데이터만 읽어 새 파일에 옮겨쓰고 통째로 교체 | 같은 id 슬롯 위치에 **새 offset 값만 갱신** (슬롯 개수/순서는 불변) |

### 2-4. 조회 시 최신순 스트리밍

`transactions.idx`는 고정폭 이진 레코드이므로 텍스트(JSONL)와 달리 **역순 순회에 UTF-8 멀티바이트 경계 문제가 없다**. id가 큰 슬롯(=최근 생성)부터 역순으로 읽으면 별도 정렬 없이 최신순이 보장된다.

```python
def iter_latest_transactions() -> Iterator[Transaction]:
    total = idx_slot_count()                      # idx파일크기 // 16
    with open(IDX_PATH, "rb") as idx, open(LOG_PATH, "rb") as log:
        for slot in range(total, 0, -1):           # 최근 id부터 역순
            idx.seek((slot - 1) * 16)
            start, end = struct.unpack("<QQ", idx.read(16))
            if start == 0 and end == 0:
                continue                             # 삭제된 슬롯
            log.seek(start)
            yield Transaction.from_json(log.read(end - start))
```

`list`, `search`, `summary`, `category remove`(사용중 여부 판정), `export`가 모두 이 제너레이터 하나를 기반으로 필터링/조기종료하여 동작한다.

### 2-5. 컴팩션(compact)

update가 누적될수록 `transactions.jsonl`에는 예전 버전(고아 데이터)이 계속 쌓인다. 이를 정리하는 `compact` 절차:

```python
def compact() -> None:
    tmp = LOG_PATH.with_suffix(".tmp")
    with open(IDX_PATH, "r+b") as idx, \
         open(LOG_PATH, "rb") as src, \
         open(tmp, "wb") as dst:
        total = idx_slot_count()
        for slot in range(total):
            idx.seek(slot * 16)
            start, end = struct.unpack("<QQ", idx.read(16))
            if start == 0 and end == 0:
                continue                            # 죽은 슬롯은 건드리지 않음
            src.seek(start)
            data = src.read(end - start)
            new_start = dst.tell()
            dst.write(data)
            idx.seek(slot * 16)
            idx.write(struct.pack("<QQ", new_start, new_start + len(data)))
    os.replace(tmp, LOG_PATH)
```

- 살아있는 슬롯만 골라 새 로그 파일로 옮겨 적고(가져갈 것만 챙기고 헌 집은 통째로 교체하는 방식 — 죽은 바이트 위치를 알아낼 필요 자체가 없음), `os.replace`로 원자적 교체한다.
- idx는 슬롯 개수·순서가 그대로 유지된 채 offset 값만 갱신되므로 id 재매핑이 필요 없다.
- **실행 시점**: 이 프로그램은 명령마다 새로 뜨는 1회성 CLI라 진짜 "유휴시간 자동 실행"은 불가능하다. 아래 중 하나로 트리거한다.
  - [x] (권장) 명시적 `compact` 커맨드 제공 (보너스 "백업" 커맨드와 유사한 유지보수용 명령)
  - [ ] (선택) 매 커맨드 실행 시작 시 고아 데이터 비율이 임계치를 넘으면 자동 실행 (opportunistic compaction)
  - 구현 시 **자동 실행 대신 안내로 대체**: 고아 비율이 50%(`TransactionRepository.COMPACT_HINT_RATIO`)를 넘으면, 로그에 고아 레코드를 만들어내는 명령인 `update`·`delete` 실행 직후 `[안내] ... compact 명령으로 정리할 수 있습니다` 를 출력한다(`cli.py`의 `maybe_hint_compact()`). 사용자가 모르는 사이에 로그 파일 전체를 재작성하는 것보다, 시점을 사용자가 고르게 하는 편이 안전하다고 판단

### 2-6. 알려진 한계 (README에 명시할 것)

- [x] offset이 8바이트라 이론상 로그 파일 상한은 매우 크지만(사실상 무제한 수준), 이 규모의 개인 가계부에서는 문제되지 않음을 명시
- [x] idx 슬롯 in-place 덮어쓰기(16바이트) 도중 정확히 그 순간 프로세스가 강제 종료되면 해당 슬롯이 손상될 이론적 가능성이 있음 (WAL 등 완전한 크래시 안전성은 이 과제 범위 밖) — README에 알려진 한계로 기술
- [x] `transactions.jsonl` 자체는 컴팩션 전까지 계속 커질 수 있음 → `compact` 명령으로 정리 가능함을 안내

---

## 3. 데이터/저장 요구사항 체크리스트

- [x] Transaction 데이터 모델을 `dataclass`로 정의 (필드: id, type, date, amount, category, memo, tags)
- [x] 최소 2개 이상 클래스 사용 (예: `Transaction`, `TransactionRepository`, `TransactionIndex`, `CategoryStore`, `BudgetStore`, `BudgetService`)
- [x] 저장 파일 3개 이상 분리: `transactions.jsonl`, `transactions.idx`, `categories.jsonl`, `budgets.jsonl` (총 4개 — 요구사항의 "3개 이상" 충족)
- [x] 기본 저장 폴더 `./data`, `--data-dir` 옵션으로 변경 가능하게 구현
- [x] 프로그램 최초 실행 시(파일 없음) 자동 생성 (빈 `transactions.jsonl`/`transactions.idx`/`categories.jsonl`/`budgets.jsonl`)
- [x] 카테고리 파일이 비어있으면 `add` 차단 + `category add` 유도 (0번 확정: 안 B)
- [x] `categories.jsonl`/`budgets.jsonl`은 임시 파일 + `os.replace` 원자적 교체로 재작성
- [x] `transactions.idx`는 슬롯 단위 in-place 덮어쓰기, `transactions.jsonl`은 append 전용(컴팩션 시에만 재작성)

---

## 4. 아키텍처/구조 요구사항 체크리스트

- [x] 모듈 3개 이상으로 분리 (권장 구조):
  - [x] `models.py` — `Transaction` dataclass, 커스텀 예외 (타입 힌트 포함)
  - [x] `repository.py` — `transactions.jsonl`/`transactions.idx` I/O, 제너레이터 기반 조회, in-place 인덱스 갱신, `compact()`
  - [x] `category_store.py` / `budget_store.py` (또는 `repository.py`에 통합) — 카테고리/예산 JSONL 재작성 로직
  - [x] `services.py` — 검증/검색/요약/예산 계산 등 비즈니스 로직
  - [x] `cli.py` — argparse 기반 커맨드 파싱 및 대화형 입력 처리
  - [x] `decorators.py` — 공통 관심사(로그/예외/시간 측정) 데코레이터
  - [x] `__main__.py` — `python -m budget_app` 진입점
- [x] 각 계층의 책임을 모델/저장소/서비스/CLI로 명확히 분리
- [x] 모든 공개 함수/메서드에 타입 힌트 적용 (매개변수, 반환값)

---

## 5. 제너레이터 요구사항 체크리스트

- [x] `iter_latest_transactions() -> Iterator[Transaction]` — `transactions.idx`를 역순으로 순회하며 `transactions.jsonl`에서 해당 byte 범위만 읽어 `yield` (2-4 참조)
- [x] list/search/summary/category remove/export 모두 이 제너레이터를 소비 (전체 리스트를 변수에 로드하지 않음)
- [x] 고정폭 이진 인덱스를 역순 순회하므로 텍스트 역순 읽기의 UTF-8 경계 문제가 없음을 확인

---

## 6. 데코레이터 요구사항 체크리스트

- [x] 데코레이터 최소 1개 구현 및 실제 커맨드 함수에 적용
- [x] 후보: `@log_call` (실행 로그), `@handle_errors` (예외 처리 → 원인+힌트 출력 후 `sys.exit`), `@timeit` (실행 시간 측정)
- [x] 여러 데코레이터를 조합 적용 시 `functools.wraps` 사용하여 메타데이터 보존

---

## 7. 입력 검증 요구사항 체크리스트

- [x] 날짜 형식 검증 (`datetime.strptime(value, "%Y-%m-%d")` 활용, 실패 시 오류)
- [x] 금액 검증: 정수/양수 여부
- [x] type 검증: `income`/`expense`만 허용
- [x] category 검증: 등록된 카테고리 목록에 존재하는지 확인
- [x] 검증 실패 시 정책 일관성 유지 (대화형 입력은 재입력 루프, 옵션 입력은 오류+힌트 출력 후 종료)

---

## 8. 오류 처리 / 종료 코드 요구사항 체크리스트

- [x] 커스텀 예외 클래스 정의 (예: `ValidationError`, `NotFoundError`)
- [x] 모든 예외는 스택트레이스 대신 `[오류] 원인` + `[힌트] 해결 방법` 형태로 출력
- [x] 정상 종료 시 `sys.exit(0)` (또는 자연 종료)
- [x] 오류 종료 시 `sys.exit(1)` 등 0이 아닌 코드 반환
- [x] 예외 처리 데코레이터(`@handle_errors`)에서 예외를 잡아 위 형식으로 출력 후 종료 코드 설정

---

## 9. CLI 규칙 체크리스트

- [x] 모든 옵션은 `--` 표기로 통일 (`--help`, `--limit`, `--from`, `--to`, `--month`, `--top`, `--id`, `--amount`, `--category`, `--type`, `--q`, `--tag`, `--out`, `--data-dir` 등)
- [x] `argparse`의 subparsers로 `add/list/search/summary/budget/category/update/delete/import/export` (+ `compact`) 서브커맨드 구성
- [x] `python -m budget_app <command> [options]` 형태로 실행 가능하도록 `__main__.py` 구성

---

## 10. README.md 필수 포함 항목 체크리스트

- [x] 실행 방법 (`python -m budget_app ...` 예시, Python 버전 요구사항)
- [x] 저장 파일 위치/형식: `./data/transactions.jsonl`(로그) + `./data/transactions.idx`(16B 고정 이진 인덱스) + `./data/categories.jsonl` + `./data/budgets.jsonl`, 각각의 역할과 포맷 선택 이유
- [x] `transactions.idx`의 구조(슬롯=id 위치, 16바이트, 삭제 시 0 초기화)와 `compact` 명령의 필요성 설명
- [x] 주요 명령 예시 (add/list/search/summary/budget/category/update/delete/import/export/compact 각각 최소 1개 실행 예시)
- [x] import/export CSV 스키마 표 (date/type/category/amount/memo/tags, required 여부, UTF-8/헤더 포함 명시)
- [x] update 방식 고정: 옵션 기반이라고 명시
- [x] 카테고리 초기화 정책 명시: 안 B(카테고리 없으면 add 차단 + 안내)
- [x] 2-6의 "알려진 한계" 명시
- [x] 아키텍처/모듈 구조 설명

---

## 11. 보너스 과제 체크리스트 (선택)

- [x] 백업 기능: `backup` 커맨드로 타임스탬프 포함 백업 파일 생성 (예: `data/backup/20240115_120000/`)
- [x] 반복 내역 기능: 반복 규칙 등록 및 특정 월 자동 생성 로직
- [x] 출력 포맷 테이블 정렬: 외부 라이브러리 없이 컬럼 폭 계산하여 정렬 출력하는 포맷터 분리
- [x] 저장 원자성 강화: `categories.jsonl`/`budgets.jsonl` 재작성에도 임시 파일 + rename 일관 적용 (transactions는 2번 섹션 설계로 이미 반영됨)

---

## 12. 구현 순서 제안 (실행 계획)

1. [ ] 프로젝트 스캐폴딩: `budget_app/` 패키지 생성, 모듈 파일 생성, `data/` 폴더 및 `.gitignore` 정리
2. [ ] `models.py`: `Transaction` dataclass, 커스텀 예외 클래스 정의
3. [ ] `repository.py` — 핵심 저장 엔진 구현 (2번 섹션 기준):
   - [x] `append_transaction(tx) -> int` (log append + idx 슬롯 추가, 신규 id 반환)
   - [x] `read_transaction(tx_id) -> Transaction | None` (idx 단일 슬롯 조회 + log seek)
   - [x] `update_transaction(tx_id, tx) -> bool` (log append + idx 슬롯 덮어쓰기)
   - [x] `delete_transaction(tx_id) -> bool` (idx 슬롯 0 초기화)
   - [x] `iter_latest_transactions() -> Iterator[Transaction]` (idx 역순 순회 제너레이터)
   - [x] `compact() -> None`
   - [x] `CategoryStore`, `BudgetStore` (JSONL 전체 재작성 방식)
4. [ ] `decorators.py`: `handle_errors`, `log_call`, `timeit` 구현
5. [ ] `services.py`: 검증 로직, 검색/필터, summary 집계, budget 사용률 계산, import/export 변환 로직
6. [ ] `cli.py`: argparse 서브커맨드 정의(`add/list/search/summary/budget/category/update/delete/import/export/compact`), 대화형/옵션 입력 처리, 출력 포맷팅
7. [ ] `__main__.py`: 진입점 연결, 최상위 예외 처리 및 종료 코드 처리
8. [ ] 초기 실행 시나리오 테스트 (파일 없음 → 자동 생성, 카테고리 없이 add 시도 → 차단 메시지)
9. [ ] 저장 엔진 단위 테스트: add 여러 건 → update → delete → list(최신순 확인) → compact 후 데이터 무결성 확인
10. [ ] 10대 기능 수동 테스트 (섹션 1 체크리스트 기준)
11. [ ] README.md 작성 (섹션 10 체크리스트 기준)
12. [ ] (선택) 보너스 과제 구현
