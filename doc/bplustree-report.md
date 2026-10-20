# B+Tree 날짜 인덱스 구현 및 검증 보고서

작성일: 2026-10-20. 기준 커밋: `4713a5bc9406816477d8ffd4b2e0e0fb498b91aa`.
작업 브랜치: `feat/bplustree-date-index`.

## 1. 변경 파일 및 기존 동작 유지

| 파일 | 변경 내용 |
|---|---|
| `budget_app/bplustree.py` | 직접 구현한 디스크 B+Tree, 바이너리 페이지, 분할, 순위·범위·양방향 스캔, CRC32, redo WAL, 구조 검사 |
| `budget_app/durability.py` | durable 원자 파일 저장, 거래번호 슬롯 undo 기록 및 재시작 복구 |
| `budget_app/repository.py` | DateIndex 교체·재노출, 키 경계 조회, 64-bit id 보존, legacy 백업·자동 마이그레이션, 슬롯 undo 통합 |
| `tests/test_bplustree.py` | 독립 정렬 oracle, 10만 번 개별 삽입, 기본/작은 페이지 분할, WAL·root·페이지 손상, 프로세스 강제 종료 및 재복구 |
| `tests/test_bplustree_repository.py` | 10만 거래, 수정·삭제·compact, CSV·recurring·backup, 기존 파일 마이그레이션, 파일 간 일관성·슬롯 장애 복구 |
| `tests/test_requirement_checklist.py` | 기존 17개 시나리오 유지. 150번의 배열 크기·손상 바이트 위치만 페이지 형식에 맞게 변경 |
| `tools/bench_date_index.py` | 원본 커밋의 실제 Sorted Array 구현과 동일 입력·실제 fsync 비교 |
| `tools/bench_large.py` | 과거 날짜 삽입의 측정 이름을 현재 B+Tree 동작으로 정정 |
| `doc/bplustree-design.md` | 구현 전 전체 소스 분석 및 호환성·복구 설계 결정 |
| `doc/bplustree-benchmark.json` | 성능 원시 결과, 환경·표본 수·최솟값·중앙값·최댓값 |
| `README.md` | 현재 포맷·마이그레이션·커밋 규칙 안내. 기존 성능·복구 기록은 과거 버전임을 표시 |
| `doc/bplustree-report.md` | 이 보고서 |

SQLite나 외부 DB·패키지를 사용하지 않는다. 기존 CLI, CSV 스키마와 검증/skip 정책, 카테고리·예산·반복 규칙 기능을 유지한다. `transactions.jsonl`은 append-only이며 수정도 끝에 추가한다. `transactions.idx`는 기존 `<QQ` 16바이트 구조이고 id `N`의 슬롯은 `(N-1)*16`이다. 삭제 슬롯과 최대 발급 id를 compact·마이그레이션에서도 보존한다.

이전 날짜 항목과 삭제된 거래 항목은 **기존처럼 남겨 두고** 현재 슬롯·실제 날짜와 맞지 않으면 조회에서 제외한다. B+Tree의 물리 삭제/재균형은 추가하지 않았으며 기존 `compact`가 stale 항목을 제거한다. 따라서 여기서 N은 살아있는 거래 수가 아니라 compact 전 인덱스 키 수다.

## 2. 바이너리 파일 구조

모든 수치는 little-endian이다. 기본 페이지 크기는 4096바이트이고 page id `P`는 `P*page_size`에 위치한다. 설정한 페이지 크기는 헤더에 저장하므로 재시작 때 자동 인식한다. 지원 크기는 256~65536의 2의 거듭제곱이다. 페이지 끝 4바이트는 앞부분 전체(패딩 포함)의 CRC32다.

### Root 메타데이터: page 0

`struct.Struct("<8sII16sQQQQQQ")`, 고정 헤더 80바이트.

| 오프셋 | 크기 | 필드 |
|---:|---:|---|
| 0 | 8 | magic `DATEBPT1` |
| 8 | 4 | 버전 `1` |
| 12 | 4 | 페이지 크기 |
| 16 | 16 | 파일 UUID: 다른 파일의 WAL 재적용 방지 |
| 32 | 8 | 실제 root page id |
| 40 | 8 | 메타데이터를 포함한 전체 페이지 수 |
| 48 | 8 | 전체 키 수 (stale 포함) |
| 56 | 8 | 첫 leaf page id |
| 64 | 8 | 마지막 leaf page id |
| 72 | 8 | WAL 트랜잭션 세대 번호 |
| 80~4091 | 4012 | 0 패딩 |
| 4092 | 4 | CRC32 |

실제 root는 작은 트리에서는 leaf, 커진 트리에서는 internal이다. 각 노드의 root 플래그와 page 0의 root id가 일치하는지 검사한다. 초기 파일은 메타데이터 1개와 빈 root leaf 1개로 8192바이트다.

### Internal / Leaf 공통 헤더

`struct.Struct("<4sBBHQQQ")`, 32바이트.

| 오프셋 | 크기 | 필드 |
|---:|---:|---|
| 0 | 4 | magic `BPTP` |
| 4 | 1 | 종류: leaf=1, internal=2 |
| 5 | 1 | 실제 root면 1 |
| 6 | 2 | 키 수 |
| 8 | 8 | 이전 leaf id (internal은 0) |
| 16 | 8 | 다음 leaf id (internal은 0) |
| 24 | 8 | 해당 노드 아래 전체 키 수 |

Leaf 본문은 `<IQ` 키의 반복이다. 날짜는 `date.toordinal()` unsigned 32-bit, 거래번호는 unsigned 64-bit다. 기본 페이지에는 최대 **338개 키**가 들어간다. `(날짜, id)`를 튜플 비교하므로 같은 날짜도 거래번호 순서대로 정렬되고 id의 상위 비트가 잘리지 않는다.

Internal 본문은 첫 자식의 `<QQ` (page id, 키 수) 16바이트 뒤에 `<IQQQ` (분기 날짜, 분기 id, 오른쪽 자식 id, 오른쪽 자식 키 수) 28바이트가 반복된다. 기본 페이지는 최대 **144개 분기 키 / 145개 자식**이다. 분기 키는 오른쪽 자식 subtree의 최소 키이며, 자식 키 수로 기존 `bisect_left` 순위 및 `iter_desc(lo, hi)` 인터페이스도 보존한다.

## 3. 삽입·페이지 분할 및 조회

1. root부터 internal 분기 키를 이진 탐색해 목표 leaf를 찾는다. 같은 키는 중복 추가하지 않는다.
2. leaf의 정렬된 위치에 삽입한다. 부모 경로의 자식 키 수를 갱신한다.
3. leaf가 339개 키가 되면 169개/170개로 나누고 새 오른쪽 leaf의 최소 키를 부모에 복사한다. 새 leaf의 prev/next, 기존 leaf의 next, 기존 오른쪽 이웃의 prev를 함께 변경한다. 마지막 leaf가 바뀌면 메타데이터도 갱신한다.
4. internal이 145개 키가 되면 가운데 키를 부모로 승격하고 좌우에 72개 키/73개 자식을 둔다. 부모가 넘치면 같은 과정을 위로 반복한다.
5. 기존 root가 분할되면 자식 2개를 갖는 새 internal root를 만들고 page 0의 root id를 바꾼다. 이 변경 전부가 하나의 WAL에 포함된다.

`iter_range(lower, upper, reverse=...)`는 `[lower, upper)` 범위를 한 번 내려가 찾은 leaf부터 연결 목록으로 읽는다. 저장소의 포함 종료 날짜는 `(종료일 ordinal + 1, 0)`으로 바꾸므로 날짜 `9999-12-31`도 넘치는 date 객체 없이 처리한다. 역순은 prev 링크를 이용한다. 키 기준 삽입/검색은 O(log N), K개 키 범위 조회는 O(log N + K)를 목표로 하며, 페이지 크기를 고정한 구현이다.

일반 단건 작업의 clean cache는 최대 64페이지이고, 바뀐 경로와 분할 이웃만 보관·기록한다. 범위 조회는 전체 인덱스를 메모리에 올리지 않는다. bulk import/rebuild는 한 번의 작업에서 변경한 페이지와 입력 키를 모으므로 **배치 크기에 비례하는 메모리**를 사용한다. 무제한 크기 배치의 외부 정렬/상수 메모리 빌더를 구현한 것은 아니다.

## 4. 장애 복구와 파일 간 일관성

### 왜 WAL을 선택했는가

Copy-on-Write는 원래 페이지 보존에 유리하지만 양방향 leaf 링크의 버전과 복사 전파를 함께 관리해야 한다. 이 구현은 변경 페이지 after-image redo WAL을 사용해 root·부모·분할 leaf·이웃 링크를 같은 복구 단위로 묶는다.

WAL 헤더는 `<8sI16sQQ` 44바이트: magic `BPTWAL01`, 페이지 크기, 파일 UUID, 목표 페이지 수, 이미지 수다. 이후 `<Q page_id> + 전체 페이지 이미지`가 반복되고, 파일 끝의 SHA-256 32바이트가 WAL 전체 본문을 검증한다. 이미지 자체도 각 페이지의 CRC32로 검사한다.

정상 순서:

1. 변경할 페이지를 메모리에서 완성한다. 본 인덱스는 아직 수정하지 않는다.
2. `.wal.tmp`에 WAL 전체를 쓰고 flush/fsync한다.
3. `.wal`로 원자 rename하고 디렉터리를 fsync한다.
4. 본 파일의 변경 노드들을 쓰고 **마지막에 page 0**을 쓴다.
5. 파일 길이를 확정하고 flush/fsync한다.
6. WAL을 지우고 디렉터리를 fsync한다.

재시작은 헤더를 읽기 **전에** WAL을 확인한다. WAL 길이·SHA-256·페이지 CRC·UUID·주소를 전부 확인한 뒤 전체 이미지를 다시 쓰고 fsync한다. 재적용 중 또 종료되어도 WAL이 남아 같은 내용을 반복 적용한다.

| 장애 지점 | 복구 결과 |
|---|---|
| WAL 임시 파일 작성 중 / publish 전 | 본 인덱스는 이전 상태, 임시 WAL 폐기 |
| WAL publish 후 / 페이지 쓰기 중 | WAL 전체 redo |
| leaf/internal split 또는 양방향 링크 일부 기록 후 | 모든 관련 페이지와 링크를 같은 WAL로 redo |
| root 메타데이터 일부만 기록됨 | WAL의 page 0 이미지로 복구 |
| 데이터 fsync 후 WAL 삭제 전 | 같은 이미지를 다시 적용해도 결과 동일 |
| WAL 자체가 손상되어 검증 불가 | 저장소가 확정된 idx+JSONL에서 날짜 인덱스 재생성 |
| WAL 없이 leaf가 손상됨 | CRC/구조 오류와 compact 안내. compact로 재생성 |

### 날짜 인덱스 완료와 거래 완료의 구분

날짜 WAL을 재적용했다고 거래 자체를 커밋하지 않는다. **거래의 존재·최신 버전·삭제 여부는 기존 `transactions.idx`가 결정한다.** JSONL이나 날짜 키만 보고 거래를 복원하면 삭제한 거래를 되살리거나 실패한 import를 부분 반영할 수 있으므로 그렇게 하지 않는다.

단일 add/update/delete는 `transactions.idx.slot-undo`에 원래 파일 길이와 원래 슬롯을 먼저 확정한다. `<8sQQ16s` 40바이트 헤더(magic `TXUNDO01`, 이전 길이, 거래번호, 이전 슬롯) + SHA-256 32바이트다. 슬롯 기록·fsync 뒤 undo 기록 삭제와 디렉터리 fsync를 마치면 거래 완료다. 기록이 남으면 add의 파일 길이 또는 update/delete의 원래 슬롯을 복구한다. 이 복구도 다시 종료되면 재실행할 수 있다.

JSONL 기록 후 날짜 인덱스 실패 시 슬롯은 아직 바꾸지 않아 기존 거래가 유지된다. 새 로그 줄은 고아로 남아 compact에서 정리한다. 미완료 날짜 키는 슬롯 범위·삭제 상태·실제 날짜와 대조해 제외하므로 목록·검색·요약·export·recurring의 기존 동작이 유지된다.

시작 시 순서: 기존 compact 복구 → 기존 import 전체 rollback → 단일 슬롯 undo → 기존 부분 슬롯 정리 → 날짜 WAL redo/메타데이터 검사/필요 시 재생성 → 기존 카테고리 초기화다. import와 compact의 기존 완료 지점·재시작 정책은 유지한다. 카테고리 건수도 기존처럼 장애 시 실제보다 많게만 남긴다.

### 기존 인덱스 마이그레이션

기존 12바이트 배열을 발견하면 정확한 원본을 `.legacy.bak.tmp`에 복사·fsync하고 `.legacy.bak`로 rename·디렉터리 fsync한다. 그 다음 살아있는 거래만 idx+JSONL에서 읽어 `.build` B+Tree를 만들고 fsync한 뒤 원래 경로로 원자 교체한다. 이미 있는 최초 백업은 덮어쓰지 않는다.

백업 또는 빌드 중 실패하면 원래 배열은 그대로다. 교체 전 종료되면 다음 실행에 다시 마이그레이션하고, 교체 후 종료되면 완성된 B+Tree를 사용한다. 이 과정에서는 JSONL·거래번호 인덱스의 바이트나 id를 변경하지 않는다.

동일 data-dir의 동시 프로세스 실행은 기존처럼 지원하지 않는다. 복구 보장은 파일시스템이 fsync·원자 rename을 준수한다는 전제다. 실제 매체 고장, 모든 원본 파일의 손상, WAL 도입 전 이미 손상된 거래번호 슬롯은 날짜 인덱스만으로 복구할 수 없다.

## 5. 성능 비교

원본 구현을 기준 커밋에서 `git show`로 읽어 별도 모듈로 실행했다. 새로운 코드로 정렬 배열을 흉내 낸 결과가 아니다. 고정 seed 645, 2020~2024 날짜 분포, 동일 파일시스템·프로세스에서 실제 파일에 기록하고 fsync를 생략하거나 mock하지 않았다. 각 단건 작업 15회 중앙값이며 Python 시작·CLI 표 출력은 제외한다. 범위 조회는 결과를 전부 소비·검증한다. 캐시를 강제로 비우지 않았으므로 디스크 하드웨어 일반 성능으로 해석하면 안 된다.

### 10만 건: 저장소 전체 연산

JSONL·거래번호 인덱스와 새 슬롯 undo 비용을 모두 포함한다. 날짜 범위는 2022-06-10~12, 결과 158건이다.

| 연산 | Sorted Array | B+Tree | 해석 |
|---|---:|---:|---|
| 최신 날짜 거래 추가 | 1.150ms | 4.074ms | 약 3.54배 느림: 배열 append에 비해 WAL·슬롯 undo fsync 비용 추가 |
| 과거 날짜 거래 삽입 | 22.711ms | 3.984ms | 약 5.70배 빠름 |
| 기존 거래 날짜 수정 | 21.947ms | 4.014ms | 약 5.47배 빠름 |
| 날짜 범위 조회 | 1.441ms | 1.297ms | 약 1.11배 빠름 |
| 10만 거래 일괄 저장 | 0.882s | 1.155s | 약 1.31배 느림 |
| 날짜 인덱스 크기 (작업 후) | 1,200,540B | 1,851,392B | 페이지 여유 공간·분기·체크섬 비용 |

### 날짜 인덱스만 비교

| 데이터 수 / 연산 | Sorted Array | B+Tree |
|---|---:|---:|
| 1만 / 과거 삽입 | 2.992ms | 1.864ms |
| 10만 / 과거 삽입 | 21.186ms | 1.859ms |
| 10만 / 날짜 변경 키 추가 | 21.285ms | 1.929ms |
| 10만 / 최신 키 추가 | 0.351ms | 1.975ms |
| 1만 / 범위 20건 | 0.052ms | 0.143ms |
| 10만 / 범위 158건 | 0.236ms | 0.146ms |

과거 삽입은 배열에서 N 증가에 따라 재작성 비용이 증가했고 B+Tree는 경로 페이지 갱신 수준으로 유지됐다. 모든 작업이 빨라지는 것은 아니다. 작은 범위에서는 전체 leaf 디코딩·CRC 검사 비용이 있고, 최신 날짜 단건 추가는 WAL 확정 비용 때문에 느리다. 10만 건 측정 트리는 높이 3, 메타데이터 포함 452페이지였다(인덱스 단독, 측정용 추가 45키 포함).

환경과 전체 수치는 [`bplustree-benchmark.json`](bplustree-benchmark.json)에 보존했다.

## 6. 테스트 실행 결과

수정 전 기존 테스트: **17개 통과** (0.526초).
최종 실행: **40개 모두 통과** (30.212초). 기존 17개 + B+Tree 10개 + 저장소/마이그레이션/파일 간 복구 13개다.
`python -m compileall -q budget_app tests tools` 및 `git diff --check`도 통과했다.

```text
Ran 40 tests in 30.212s

OK
```

검증 범위:

- 4096바이트 페이지에 **10만 번 개별 merge 호출**: fsync·WAL 활성, 과거/동일 날짜 혼합, 독립 `sorted()` oracle와 양방향 전체 결과 대조, 높이 3 이상 확인.
- **10만 거래 저장**: 범위 조회, 과거 추가, 날짜 수정, 거래 삭제, compact 전후 내용·id·최대 발급 id 보존 확인.
- 256바이트 페이지에서 높이 4 이상 트리: 매 배치마다 자식 키 수·separator·동일 leaf 깊이·페이지 도달성·양방향 링크 검증. 순위 API 및 key-range 결과를 Python bisect/정렬 결과와 대조.
- 동일 키 재삽입 무변경, 같은 날짜 정렬, 날짜 원복 수정 중복 방지, 빈 범위·최대 날짜·32-bit를 넘는 id·uint64 최댓값 확인.
- 기존 파일·stale/삭제/고아 항목 마이그레이션, 원본 백업 바이트 동일성, 백업 실패, 교체 전후 중단, 누락/손상 인덱스 재생성 확인.
- CSV 1200행 import/export roundtrip, 잘못된 행 skip, 반복 거래 윤년 말일·재적용, backup 및 compact 검증. 기존 17개 시나리오의 import/compact/recurring 중단 복구 검증도 유지.
- 실제 별도 Python 프로세스를 `os._exit`로 종료: WAL 임시 저장/publish/페이지 기록/root 기록/data fsync/WAL 제거, root split, 다단계 internal split, 거래 슬롯 커밋 직전. 새 인스턴스에서 복구 결과 확인.
- 페이지·root torn-write, 손상 WAL·슬롯 undo 체크섬, JSONL 후 날짜 기록 실패, WAL rename 실패, 슬롯 부분 쓰기 및 복구 중 재중단 검증.

프로세스 강제 종료와 파일 바이트 손상으로 장애 경계를 시험했으며, 물리 전원 차단 장비로 시험한 것은 아니다.

## 7. 재현 명령 및 배포 상태

```bash
python -m unittest discover -s tests -v
python -m compileall -q budget_app tests tools
python tools/bench_date_index.py --sizes 10000 100000 --samples 15 --out doc/bplustree-benchmark.json
```

원본 전체 파일·트리·커밋은 GitHub 플러그인으로 확보하고 해시 검증하여 원본 커밋을 가진 shallow Git 작업 트리로 복원했다. 로컬 `feat/bplustree-date-index` 브랜치에서 작업했다. 플러그인의 원격 브랜치 생성은 `403 Resource not accessible by integration`으로 거부되었으므로 원격 반영은 완료하지 않았다. 최종 패치로 기존 저장소에 동일 변경을 적용할 수 있다.

패치를 적용할 때는 원래 저장소의 기준 커밋 또는 이를 포함한 main에서 다음처럼 새 브랜치를 만든다(같은 이름의 브랜치가 이미 있다면 기존 작업을 먼저 확인한다).

```bash
git switch -c feat/bplustree-date-index
git am /path/to/AllowanceTracker-bplustree.patch
```

Git bundle을 사용할 때는 기준 커밋이 있는 기존 저장소에서 다음 명령으로 브랜치와 커밋을 가져올 수 있다. bundle은 기준 커밋 이후 변경만 포함한다.

```bash
git fetch /path/to/AllowanceTracker-bplustree.bundle feat/bplustree-date-index:feat/bplustree-date-index
git switch feat/bplustree-date-index
```
