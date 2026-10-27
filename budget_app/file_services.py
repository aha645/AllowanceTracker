"""파일 입출력 서비스: CSV 가져오기/내보내기, 데이터 백업."""

from __future__ import annotations

import csv
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

from .models import Transaction, ValidationError
from .services import TransactionService
from .validators import format_tags

CSV_FIELDS: tuple[str, ...] = ("date", "type", "category", "amount", "memo", "tags")
#: import 결과에 사유까지 담아 둘 건너뛴 행 수. 나머지는 건수만 센다(잘못된 행이 많아도 메모리가 늘지 않게).
MAX_REPORTED_ERRORS = 20


@dataclass(slots=True)
class ImportReport:
    """CSV 가져오기 결과."""

    imported: int
    skipped: int
    #: 건너뛴 행 중 앞쪽 최대 MAX_REPORTED_ERRORS 건의 (줄 번호, 사유)
    errors: list[tuple[int, str]]


class CsvService:
    """CSV 가져오기/내보내기. 스키마는 date,type,category,amount,memo,tags 고정(UTF-8, 헤더 포함)."""

    def __init__(self, service: TransactionService, data_dir: Path) -> None:
        self.service: TransactionService = service
        self.data_dir: Path = Path(data_dir)

    def import_csv(self, path: Path) -> ImportReport:
        """행 단위로 검증하며 가져온다. 실패한 행은 건너뛰고 사유를 모아 돌려준다.

        파일을 두 번 읽는다. 1차는 검증만 해서 건너뛸 행을 모으고, 2차는 다시 읽으며 유효한 행을
        바로 저장한다. 행을 메모리에 모아 두지 않으면서도, 파일 자체를 못 읽으면(인코딩/형식 오류)
        1차에서 멈추므로 아무것도 저장되지 않는다.
        """
        source = Path(path)
        if not source.exists():
            raise ValidationError(
                f"가져올 CSV 파일이 없습니다: {source}",
                "--from 경로를 확인하세요.",
            )
        if not source.is_file():  # 폴더를 주면 "입출력 오류 … 디스크 여유 공간" 으로 엉뚱하게 안내했다
            raise ValidationError(
                f"가져올 CSV 가 파일이 아닙니다: {source}",
                "--from 에 폴더가 아닌 CSV 파일 경로를 지정하세요.",
            )
        self.service.categories.ensure_registered()
        # 행마다 categories.jsonl 을 열지 않도록 한 번만 읽어 둔다(10만 행이면 20만 번 열던 병목).
        known = set(self.service.categories.names())
        errors: list[tuple[int, str]] = []
        skipped = 0
        # 유효한 행의 카테고리별 건수. 1차에서 합산해 두었다가 거래를 쓰기 전에 한 번만 저장한다
        # (행마다 categories.jsonl 을 다시 쓰지 않도록). 카테고리 수만큼만 차지한다.
        category_counts: dict[str, int] = {}
        for lineno, result in self._iter_rows(source, known):
            if isinstance(result, ValidationError):
                skipped += 1
                if len(errors) < MAX_REPORTED_ERRORS:
                    errors.append((lineno, result.message))
            else:
                category_counts[result.category] = category_counts.get(result.category, 0) + 1
        imported = 0
        if category_counts:
            # 옛 날짜가 섞여 있어도 날짜 인덱스는 add_many 안에서 한 번만 병합된다
            imported = self.service.add_many(
                (tx for _, tx in self._iter_rows(source, known) if isinstance(tx, Transaction)),
                category_counts,
            )
        return ImportReport(imported=imported, skipped=skipped, errors=errors)

    def _iter_rows(
        self, source: Path, known: set[str]
    ) -> Iterator[tuple[int, Transaction | ValidationError]]:
        """CSV 를 한 행씩 검증해 (줄 번호, 거래 또는 그 행의 검증 오류)를 낸다.

        파일 자체를 더 읽을 수 없으면(헤더 누락, UTF-8 아님, 닫히지 않은 따옴표 등) ValidationError 를
        던져 import 전체를 멈춘다.
        """
        with source.open("r", encoding="utf-8-sig", newline="") as fp:
            reader = csv.DictReader(fp)
            try:
                missing = [f for f in ("date", "type", "category", "amount") if f not in (reader.fieldnames or [])]
                if missing:
                    raise ValidationError(
                        f"CSV 헤더에 필수 컬럼이 없습니다: {', '.join(missing)}",
                        f"헤더는 {','.join(CSV_FIELDS)} 형식이어야 합니다.",
                    )
                for lineno, row in enumerate(reader, start=2):
                    try:
                        yield lineno, self.service.build_transaction(
                            date=row.get("date", ""),
                            type_=row.get("type", ""),
                            category=row.get("category", ""),
                            amount=row.get("amount", ""),
                            memo=row.get("memo", "") or "",
                            tags=row.get("tags", "") or "",
                            known_categories=known,
                        )
                    except ValidationError as exc:
                        yield lineno, exc
            except UnicodeDecodeError as exc:
                # gotcha: 파일을 덩어리로 디코딩하므로 실패한 줄 번호는 정확하지 않아 싣지 않는다
                raise ValidationError(
                    f"CSV 파일을 UTF-8 로 읽을 수 없습니다: {source}",
                    "엑셀이라면 'CSV UTF-8(쉼표로 분리)' 형식으로 다시 저장하세요. 아무것도 저장하지 않았습니다.",
                ) from exc
            except csv.Error as exc:
                raise ValidationError(
                    f"CSV 형식이 잘못되어 {reader.line_num}번째 줄 근처를 읽을 수 없습니다: {exc}",
                    '메모 등에 연 따옴표(")가 닫혔는지 확인하세요. 아무것도 저장하지 않았습니다.',
                ) from exc

    def _inside_data_dir(self, target: Path) -> bool:
        """target 이 데이터 폴더 안인지. 경로 문자열이 아니라 실제 폴더가 같은지로 비교한다.

        문자열로 비교하면 대소문자를 구분하지 않는 파일 시스템(macOS 기본)에서 `DATA/...` 가 `data/` 와
        다른 경로로 보여 막지 못했다. 그래서 이미 있는 상위 폴더 중 데이터 폴더와 같은 폴더가 있는지 본다.
        """
        if not self.data_dir.exists():
            return False
        target = target.absolute()
        # target 자신도 본다: `--out data` 처럼 데이터 폴더 자체를 주면 "입출력 오류" 로 엉뚱하게 실패했다
        return any(folder.exists() and folder.samefile(self.data_dir) for folder in (target, *target.parents))

    def export_csv(self, path: Path, transactions: Iterable[Transaction]) -> int:
        """거래를 CSV 로 내보내고 건수를 돌려준다."""
        target = Path(path)
        # 데이터 폴더 안으로 내보내면 transactions.jsonl 같은 저장 파일이나 백업을 CSV 로 덮어쓸 수 있다.
        # 예전에는 그대로 덮어써서 위치 인덱스와 어긋난 저장소가 모든 명령에서 오류로 멈췄다.
        if self._inside_data_dir(target):
            raise ValidationError(
                f"데이터 폴더 안에는 내보낼 수 없습니다: {target}",
                f"저장 파일이나 백업을 덮어쓸 수 있습니다. --out 에 데이터 폴더({self.data_dir}) 밖의 경로를 지정하세요.",
            )
        if target.parent and not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        # 같은 폴더의 임시 파일에 끝까지 쓰고 이름을 바꿔 교체한다. 예전에는 target 을 바로 열어 써서,
        # 도중에 꺼지거나 오류가 나면 이미 있던 파일이 중간까지만 쓴 새 내용으로 바뀌어 있었다.
        # 이름 바꾸기는 한순간이라 target 은 항상 원래 파일이거나 완성된 새 파일이다.
        # 이름을 고정해 두면, 강제 종료로 남은 임시 파일도 다음에 같은 곳으로 내보낼 때 덮어써져 사라진다
        tmp = target.with_name(f".{target.name}.tmp")
        try:
            with tmp.open("w", encoding="utf-8", newline="") as fp:
                writer = csv.DictWriter(fp, fieldnames=list(CSV_FIELDS))
                writer.writeheader()
                for tx in transactions:
                    writer.writerow(
                        {
                            "date": tx.date,
                            "type": tx.type,
                            "category": tx.category,
                            "amount": tx.amount,
                            "memo": tx.memo,
                            "tags": format_tags(tx.tags),
                        }
                    )
                    count += 1
                fp.flush()
                os.fsync(fp.fileno())
            if target.exists():
                shutil.copymode(target, tmp)  # 덮어쓸 때 원래 파일의 권한을 유지한다
            os.replace(tmp, target)
        except BaseException:  # Ctrl+C 포함: 반쯤 쓴 임시 파일을 남기지 않는다
            tmp.unlink(missing_ok=True)
            raise
        return count


class BackupService:
    """data 폴더 전체를 타임스탬프 폴더로 복사하는 백업(보너스)."""

    BACKUP_DIR_NAME = "backup"

    def __init__(self, data_dir: Path) -> None:
        self.data_dir: Path = Path(data_dir)

    def create(self) -> tuple[Path, list[str]]:
        """`data/backup/YYYYmmdd_HHMMSS/` 로 데이터 파일을 복사한다."""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = self.data_dir / self.BACKUP_DIR_NAME / stamp
        target.mkdir(parents=True, exist_ok=True)
        copied: list[str] = []
        for item in sorted(self.data_dir.iterdir()):
            if item.is_dir() or item.name.endswith(".tmp"):
                continue
            shutil.copy2(item, target / item.name)
            copied.append(item.name)
        return target, copied
