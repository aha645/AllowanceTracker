# 날짜 인덱스 변경 설계 (구현 전 확정)

기준: `main` 커밋 `4713a5bc9406816477d8ffd4b2e0e0fb498b91aa`.
전체 28개 파일의 Git blob, tree 및 원본 커밋 해시를 검증했고, 수정 전 기존 unittest 17개가 통과했다.

## 기존 코드 분석

- `cli.py` → `commands/` → `context.py`의 서비스 조립 → `services.py` / `file_services.py` → `repository.py`, `stores.py` 구조다. 모델·입력 검증·표 출력과 명령 인터페이스는 유지한다.
- `TransactionRepository`는 JSONL에 새 레코드와 수정 버전을 append하고, `transactions.idx`의 `(id-1)*16` 위치에 `<QQ` 오프셋을 저장한다. 삭제 슬롯은 `(0,0)`이며 compact에서도 슬롯 수와 id를 보존한다.
- 기존 `DateIndex`는 `<IQ` (ordinal, id) 12바이트 정렬 배열이다. 최신 날짜 추가는 append, 과거 날짜 삽입은 전체 파일 병합이다. `bisect_left`는 순위, `iter_desc`는 순위 구간을 제공한다.
- 저장 순서는 JSONL fsync → 날짜 항목 fsync → 위치 슬롯 fsync다. 아직 커밋하지 않은 날짜 항목, 수정 전 날짜 항목, 삭제된 슬롯은 조회에서 제외한다. 이 논리 삭제 정책을 유지한다.
- `append_transactions`는 CSV import의 스트리밍 입력을 받고 JSONL·슬롯·날짜 항목을 묶어 기록한다. `.import-pending`이 남으면 슬롯 파일을 import 전 크기로 잘라 전체 import를 취소한다. CSV 스키마, 두 번 검증, 잘못된 행 skip, export 원자 교체를 유지한다.
- 검색·월별 요약·예산·export·recurring은 날짜 범위를 계산해 `iter_latest_transactions`를 소비한다. 같은 날짜에서는 큰 id가 먼저다. recurring은 source 식별자로 중복 생성 없이 재개한다.
- compact는 위치 파일 복사본과 임시 JSONL을 이용해 중단 이전/이후를 구분한다. 날짜 인덱스는 offset을 갖지 않으므로 compact 중에도 id 기준 조회가 가능하며 마지막에 재생성한다.
- `stores.py`는 카테고리·예산·반복규칙을 원자 교체한다. 카테고리 건수는 거래보다 먼저 늘리고 나중에 줄여 장애 시 실제보다 많게만 남기는 정책이다. 변경하지 않는다.
- 기존 테스트 중 150번의 파일 길이 및 바이트 손상 주입만 12바이트 배열 포맷에 종속된다. 같은 동작 검증과 장애 시나리오는 유지하고 페이지 포맷에 맞게 주입 위치를 바꾼다. 나머지 기존 16개 시나리오는 그대로 실행한다.

## 결정한 구현 방식

1. `bplustree.py`에 표준 라이브러리만 사용하는 B+Tree를 분리하고 `repository.DateIndex`로 재노출한다. 기본 페이지는 4096바이트, page 0은 root 메타데이터, 실제 root는 leaf 또는 internal이다. 모든 페이지는 CRC32로 검사한다.
2. 키는 `<IQ`로 날짜 ordinal과 unsigned 64-bit id를 온전히 저장한다. leaf는 앞/뒤 page id를 가진다. internal은 분기 키와 자식 page id, 자식 항목 수를 보관한다. 자식 수로 기존 순위 API도 O(log N)에 유지한다. 실제 저장소 범위 조회는 키 경계에서 바로 leaf를 찾아 스트리밍한다.
3. 삽입은 경로의 페이지와 분할된 이웃 leaf만 수정한다. leaf split은 오른쪽 최소 키를 부모에 복사하고, internal split은 가운데 키를 승격한다. root split은 새 root를 만들고 메타데이터를 같은 WAL로 갱신한다. 삭제와 날짜 수정은 기존의 stale-entry 필터링을 유지하고 compact에서 제거한다.
4. 이중 leaf 링크를 가진 Copy-on-Write는 페이지 복사 전파와 링크 버전 관리가 복잡하므로, **변경된 페이지의 after-image를 담은 redo WAL**을 선택한다. 체크섬을 포함한 WAL을 임시 파일에 fsync하고 rename + 디렉터리 fsync로 확정한 뒤에만 본 파일을 수정한다. 데이터 페이지 → 메타데이터 페이지 → 파일 fsync → WAL 삭제 + 디렉터리 fsync 순서다. WAL이 있으면 모든 이미지를 검증한 뒤 재적용한다. 재적용 중 다시 꺼져도 동일하게 반복한다.
5. 날짜 인덱스 WAL의 완료는 거래 자체의 완료와 다르다. B+Tree에는 커밋 전 키가 남을 수 있으며 기존처럼 슬롯·실제 날짜로 걸러낸다. JSONL 기록 후 날짜 인덱스 실패 시 위치 슬롯은 갱신하지 않는다. JSONL 전체에서 최종 버전을 추정해 삭제 거래를 되살리지 않는다.
6. 새 add/update/delete의 위치 슬롯 torn write를 막기 위해 별도 undo 기록에 **원래 슬롯과 원래 idx 길이**를 durable하게 저장한다. 위치 슬롯 fsync 후 undo 기록 삭제 + 디렉터리 fsync를 완료 시점으로 삼는다. 중단된 add는 길이를 복원하고 update/delete는 원래 슬롯을 복원한다. 기존 import journal 및 compact 복구를 먼저 처리하고, 그 후 단일 슬롯 undo와 날짜 WAL을 복구한다.
7. 기존 12바이트 인덱스를 발견하면 원본을 `.legacy.bak`로 durable하게 보존한다. 살아있는 거래의 진실은 JSONL+idx이므로 이를 읽어 임시 B+Tree를 완성·fsync하고 원자 교체한다. 기존 id/JSONL/idx는 마이그레이션에서 변경하지 않는다. 인덱스 누락·메타데이터 손상도 같은 방식으로 재생성한다. 손상된 leaf는 오류로 알리고 compact로 재생성한다.

## 검증 및 측정 계획

- 원본 17개 시나리오 회귀 테스트, 10만 건 거래 저장과 독립 키 정렬 oracle 대조, 과거 날짜·동일 날짜·수정·삭제·양방향·빈 범위·최대 날짜·64-bit id 검증.
- 기본 페이지에서 leaf/internal/root split 검증과 작은 페이지에서 더 깊은 트리 검증. 자식 수와 양방향 링크를 독립 순회해 확인한다.
- WAL 확정 전/후, 각 페이지 기록, root 갱신, fsync, WAL 제거 단계 및 단일 슬롯·import·compact·migration 중단을 주입한다. 실제 별도 프로세스의 강제 종료 후 재시작도 검사한다.
- 고정 seed 데이터와 동일 파일시스템에서 원본 Sorted Array 저장소를 기준 커밋에서 로드해 실제 fsync를 포함한 거래 추가/과거 삽입/날짜 수정/범위 검색을 비교한다. 표본 수·범위 결과 건수·데이터 규모·메모리/시간 측정 범위를 보고한다.
- 같은 data-dir의 동시 다중 writer는 기존과 같이 지원 범위 밖이다. 운영체제와 파일시스템이 fsync/원자 rename을 준수한다는 전제가 있으며 물리 매체 고장은 백업이 필요하다.
