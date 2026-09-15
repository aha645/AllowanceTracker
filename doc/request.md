# 콘솔 가계부(budget_app) 요구사항 체크리스트 & 실행 계획

> 이 문서는 미션 설명을 분석하여 만든 요구사항 체크리스트와, 각 항목을 만족하기 위해 해야 할 구체적인 작업 목록이다.
> 구현 시작 전 이 문서를 기준으로 진행 상황을 갱신한다.

---

## 0. 사전 결정 사항 (설계 확정 필요, 문서에 반드시 고정)

과제 요구사항 상 "택 1"이 필요한 항목들. 구현 전에 아래를 결정하고 README.md에 명시해야 한다.

- [ ] **저장 포맷**: JSONL vs CSV 중 1개 선택 (권장: JSONL — 특수문자/쉼표 포함 메모·태그 처리에 유리)
- [ ] **update 방식**: 옵션 기반(`--id --date ...`) vs 대화형 중 1개 고정 (권장: 옵션 기반 — search/delete와 일관성, 자동화 가능)
- [ ] **카테고리 초기화 정책**: (안 A) 기본 카테고리 자동 생성 vs (안 B) `category add` 선행 유도 (권장: 안 A — add 첫 실행 경험 개선)
- [ ] **저장 폴더 기본값**: `./data` (요구사항 권장값 그대로 사용, `--data-dir`로 변경 가능하게)
- [ ] **거래 ID 형식**: 예시 형식 `TX-000012` 채택 여부 결정 (순번 zero-padding 6자리 권장)

---

## 1. 기능 요구사항 체크리스트 (10대 기능)

### 1-1. 거래 추가 (add)
- [ ] 대화형 입력으로 날짜/타입/카테고리/금액/메모/태그 순차 입력받기
- [ ] 날짜 형식(YYYY-MM-DD) 검증 → 실패 시 재입력 요구 또는 오류+힌트 출력
- [ ] 타입은 `income`/`expense`만 허용 → 그 외 값 재입력 요구
- [ ] 금액은 양수(0 초과)만 허용 → 음수/0/숫자 아님 시 재입력 요구
- [ ] 카테고리는 등록된 목록에 존재해야 함 → 없으면 안내 후 재입력 또는 등록 유도
- [ ] 메모/태그는 선택 입력(엔터로 스킵 가능), 태그는 쉼표 구분 파싱
- [ ] 저장 성공 시 `[저장 완료] id=TX-000012` 형태로 생성된 id 출력
- [ ] 저장은 파일에 append 방식(JSONL 등)으로 안전하게 기록

### 1-2. 거래 목록 (list)
- [ ] `--limit N` 옵션 지원 (기본값 지정, 예: 20)
- [ ] 최신순(날짜/생성순 내림차순) 정렬 출력
- [ ] 파일 전체를 메모리에 한 번에 로드하지 않고 **제너레이터(yield) 기반 스트리밍**으로 읽기
- [ ] `--help` 로 사용법 출력 지원

### 1-3. 거래 검색 (search)
- [ ] `--from`, `--to` (기간 필터)
- [ ] `--category` (카테고리 필터)
- [ ] `--type` (income/expense 필터)
- [ ] `--q` (메모 키워드 검색)
- [ ] `--tag` (태그 필터)
- [ ] 여러 조건 동시 지정 시 AND 조건으로 결합
- [ ] 결과 최신순 정렬 출력
- [ ] 제너레이터 기반 스트리밍 유지 (list와 필터 로직 공유 검토)
- [ ] `--help` 지원

### 1-4. 월별 요약 (summary)
- [ ] `--month YYYY-MM` 필수 입력 처리 및 형식 검증
- [ ] `--top N` 옵션 (기본값 지정, 예: 3)
- [ ] 총 수입 / 총 지출 / 잔액(수입-지출) 계산 및 출력
- [ ] 카테고리별 지출 합계 집계 후 TOP N 정렬 출력
- [ ] 해당 월 데이터가 없을 경우 "데이터 없음" 명확히 출력 (0원 출력과 구분)
- [ ] 예산이 설정된 경우: 사용률(%) 계산 및 출력
- [ ] 예산 초과 시 경고 문구 출력
- [ ] `--help` 지원

### 1-5. 예산 설정/조회 (budget)
- [ ] `budget set --month YYYY-MM --amount <금액>` 구현
- [ ] 금액 양수 검증
- [ ] 저장 성공 메시지 출력 (`[저장 완료] 2024-01 예산 500000원`)
- [ ] 같은 월 재설정 시 덮어쓰기 정책 결정 및 구현
- [ ] budgets 파일에 영구 저장 (transactions/categories와 별도 파일)
- [ ] summary 실행 시 budgets 조회하여 사용률/초과 여부 반영

### 1-6. 카테고리 관리 (category)
- [ ] `category add` — 대화형 또는 옵션으로 카테고리명 입력, 중복 방지
- [ ] `category list` — 전체 카테고리 목록 출력
- [ ] `category remove` — 카테고리 삭제
- [ ] 삭제 대상 카테고리를 사용 중인 거래가 있는지 검사
- [ ] 사용 중이면: (정책 택1) 삭제 차단 메시지 출력 OR 대체 카테고리 지정 요구 → 문서에 고정
- [ ] categories 파일에 영구 저장

### 1-7. 거래 수정 (update)
- [ ] 0번 항목에서 확정한 방식(옵션 or 대화형)으로 구현, README에 명시
- [ ] `--id` (또는 대화형 id 입력)로 대상 조회
- [ ] 존재하지 않는 id 처리 → "없는 데이터" 메시지 출력
- [ ] 필드별 부분 수정 지원 (date/type/category/amount/memo/tags)
- [ ] 수정 시에도 값 검증(날짜 형식, 양수 금액, 허용 type, 존재 category) 동일 적용
- [ ] 성공/실패 메시지 출력
- [ ] 파일 전체 재작성 시 임시 파일 + rename(원자적 교체) 방식 적용

### 1-8. 거래 삭제 (delete)
- [ ] `delete --id <id>` 구현
- [ ] 존재하지 않는 id 처리 → 오류 메시지 출력
- [ ] 삭제 성공 메시지 출력
- [ ] 파일 재작성 시 임시 파일 + rename 방식 적용 (update와 로직 공유 검토)

### 1-9. 가져오기/내보내기 (import/export)
- [ ] `import --from <csv>` — CSV 읽어 거래 일괄 등록
- [ ] import 시 행 단위 검증(날짜/타입/카테고리/금액) 및 실패 행 skip 처리, 처리 건수(imported/skipped) 출력
- [ ] `export --out <csv>` — 조건에 맞는 거래를 CSV로 저장
- [ ] export는 `--month` 또는 (`--from` AND `--to`) 중 최소 1개 조건 필수 → 미지정 시 오류 처리
- [ ] CSV 스키마 고정: `date, type, category, amount, memo, tags` (헤더 포함, UTF-8)
- [ ] tags는 쉼표 구분 문자열로 CSV 내 저장 (내부 데이터의 리스트 ↔ CSV 문자열 변환 함수 필요)
- [ ] 처리 건수 출력 (`[완료] export.csv (12 records)` 등)
- [ ] `--help` 지원

### 1-10. 공통: --help
- [ ] 모든 서브커맨드에서 `--help` 옵션으로 사용법 출력 (표준 라이브러리 `argparse` 활용 시 자동 제공됨)

---

## 2. 데이터/저장 요구사항 체크리스트

- [ ] Transaction 데이터 모델을 `dataclass`로 정의 (필드: id, type, date, amount, category, memo, tags)
- [ ] 최소 2개 이상 클래스 사용 (예: `Transaction`, `TransactionRepository`, `CategoryStore`, `BudgetStore`, `BudgetService`)
- [ ] 저장 파일 3개 이상 분리: `transactions.<fmt>`, `categories.<fmt>`, `budgets.<fmt>`
- [ ] 기본 저장 폴더 `./data`, `--data-dir` 옵션으로 변경 가능하게 구현
- [ ] 프로그램 최초 실행 시(파일 없음) 자동 생성 또는 초기화 안내 메시지 출력
- [ ] 카테고리 파일 비어있을 때 정책(0번에서 결정) 적용
- [ ] update/delete 시 원자적 교체(임시 파일 → os.replace) 적용하여 쓰기 도중 실패해도 원본 보존

---

## 3. 아키텍처/구조 요구사항 체크리스트

- [ ] 모듈 3개 이상으로 분리 (권장 구조):
  - [ ] `models.py` — Transaction 등 데이터 구조 (dataclass, 타입 힌트)
  - [ ] `repository.py` — 파일 I/O, 제너레이터 기반 스트리밍 읽기, 원자적 쓰기
  - [ ] `services.py` — 검증/검색/요약/예산 계산 등 비즈니스 로직
  - [ ] `cli.py` — argparse 기반 커맨드 파싱 및 대화형 입력 처리
  - [ ] `decorators.py` — 공통 관심사(로그/예외/시간 측정) 데코레이터
  - [ ] `__main__.py` — `python -m budget_app` 진입점
- [ ] 각 계층의 책임을 README 또는 코드 주석 최소화하되 구조로 명확히 분리 (모델/저장소/서비스/CLI)
- [ ] 모든 공개 함수/메서드에 타입 힌트 적용 (매개변수, 반환값)

---

## 4. 제너레이터 요구사항 체크리스트

- [ ] transactions 파일 읽기 함수는 `yield`를 사용한 제너레이터로 구현 (예: `def iter_transactions() -> Iterator[Transaction]`)
- [ ] list/search 모두 이 제너레이터를 소비하는 방식으로 구현 (전체 리스트 로드 금지)
- [ ] "최신순 정렬"과 "스트리밍"이 상충하지 않도록 설계 검토
  - 참고 구현 전략: 파일을 역순으로 읽거나, limit/필터 적용 후 필요한 만큼만 정렬 버퍼링하는 방식 채택 (완전한 무제한 스트리밍이 어렵다면, "한 번에 전체를 리스트 변수로 로드하지 않고 제너레이터를 경유해 순회 처리한다"는 최소 요건은 반드시 충족)

---

## 5. 데코레이터 요구사항 체크리스트

- [ ] 데코레이터 최소 1개 구현 및 실제 커맨드 함수에 적용
- [ ] 후보: `@log_call` (실행 로그), `@handle_errors` (예외 처리 → 원인+힌트 출력 후 sys.exit), `@timeit` (실행 시간 측정)
- [ ] 여러 데코레이터를 조합 적용 시 `functools.wraps` 사용하여 메타데이터 보존

---

## 6. 입력 검증 요구사항 체크리스트

- [ ] 날짜 형식 검증 (`datetime.strptime(value, "%Y-%m-%d")` 활용, 실패 시 오류)
- [ ] 금액 검증: 정수/양수 여부
- [ ] type 검증: `income`/`expense`만 허용
- [ ] category 검증: 등록된 카테고리 목록에 존재하는지 확인
- [ ] 검증 실패 시 정책 일관성 유지 (대화형 입력은 재입력 루프, 옵션 입력은 오류+힌트 출력 후 종료)

---

## 7. 오류 처리 / 종료 코드 요구사항 체크리스트

- [ ] 커스텀 예외 클래스 정의 (예: `ValidationError`, `NotFoundError`)
- [ ] 모든 예외는 스택트레이스 대신 `[오류] 원인` + `[힌트] 해결 방법` 형태로 출력
- [ ] 정상 종료 시 `sys.exit(0)` (또는 자연 종료)
- [ ] 오류 종료 시 `sys.exit(1)` 등 0이 아닌 코드 반환
- [ ] 예외 처리 데코레이터(`@handle_errors`)에서 예외를 잡아 위 형식으로 출력 후 종료 코드 설정

---

## 8. CLI 규칙 체크리스트

- [ ] 모든 옵션은 `--` 표기로 통일 (`--help`, `--limit`, `--from`, `--to`, `--month`, `--top`, `--id`, `--amount`, `--category`, `--type`, `--q`, `--tag`, `--out`, `--data-dir` 등)
- [ ] `argparse`의 subparsers로 `add/list/search/summary/budget/category/update/delete/import/export` 서브커맨드 구성
- [ ] `python -m budget_app <command> [options]` 형태로 실행 가능하도록 `__main__.py` 구성

---

## 9. README.md 필수 포함 항목 체크리스트

- [ ] 실행 방법 (`python -m budget_app ...` 예시, Python 버전 요구사항)
- [ ] 저장 파일 위치/형식 (`./data/transactions.jsonl` 등, 포맷 선택 이유)
- [ ] 주요 명령 예시 (add/list/search/summary/budget/category/update/delete/import/export 각각 최소 1개 실행 예시)
- [ ] import/export CSV 스키마 표 (date/type/category/amount/memo/tags, required 여부, UTF-8/헤더 포함 명시)
- [ ] update 방식 고정 여부 명시 (옵션 vs 대화형)
- [ ] 카테고리 초기화 정책 명시
- [ ] (선택) 아키텍처/모듈 구조 설명

---

## 10. 보너스 과제 체크리스트 (선택)

- [ ] 백업 기능: `backup` 커맨드로 타임스탬프 포함 백업 파일 생성 (예: `data/backup/20240115_120000/`)
- [ ] 반복 내역 기능: 반복 규칙 등록 및 특정 월 자동 생성 로직
- [ ] 출력 포맷 테이블 정렬: 외부 라이브러리 없이 컬럼 폭 계산하여 정렬 출력하는 포맷터 분리
- [ ] 저장 원자성 강화: 모든 쓰기 작업(add 포함)에 임시 파일 + rename 적용 검토

---

## 11. 구현 순서 제안 (실행 계획)

1. [ ] 프로젝트 스캐폴딩: `budget_app/` 패키지 생성, 위 6개 모듈 파일 생성, `data/` 폴더 및 `.gitignore` 정리
2. [ ] `models.py`: `Transaction` dataclass, 커스텀 예외 클래스 정의
3. [ ] `repository.py`: JSONL(or CSV) 읽기(제너레이터)/append/원자적 재작성 함수, `CategoryStore`, `BudgetStore` 구현
4. [ ] `decorators.py`: `handle_errors`, `log_call`, `timeit` 구현
5. [ ] `services.py`: 검증 로직, 검색/필터, summary 집계, budget 사용률 계산, import/export 변환 로직
6. [ ] `cli.py`: argparse 서브커맨드 정의, 각 커맨드별 대화형/옵션 입력 처리, 출력 포맷팅
7. [ ] `__main__.py`: 진입점 연결, 최상위 예외 처리 및 종료 코드 처리
8. [ ] 초기 실행 시나리오 테스트 (파일 없음 → 자동 생성/안내)
9. [ ] 10대 기능 수동 테스트 (섹션 1 체크리스트 기준)
10. [ ] README.md 작성 (섹션 9 체크리스트 기준)
11. [ ] (선택) 보너스 과제 구현
