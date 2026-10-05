"""파일 입출력 서비스: CSV 가져오기/내보내기, 데이터 백업."""

from __future__ import annotations

import csv
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import Transaction, ValidationError
from .services import TransactionService
from .validators import format_tags

CSV_FIELDS: tuple[str, ...] = ("date", "type", "category", "amount", "memo", "tags")


@dataclass(slots=True)
class ImportReport:
    """CSV 가져오기 결과."""

    imported: int
    skipped: int
    errors: list[tuple[int, str]]


class CsvService:
    """CSV 가져오기/내보내기. 스키마는 date,type,category,amount,memo,tags 고정(UTF-8, 헤더 포함)."""

    def __init__(self, service: TransactionService) -> None:
        self.service: TransactionService = service

    def import_csv(self, path: Path) -> ImportReport:
        """행 단위로 검증하며 가져온다. 실패한 행은 건너뛰고 사유를 모아 돌려준다."""
        source = Path(path)
        if not source.exists():
            raise ValidationError(
                f"가져올 CSV 파일이 없습니다: {source}",
                "--from 경로를 확인하세요.",
            )
        self.service.ensure_categories_exist()
        skipped = 0
        errors: list[tuple[int, str]] = []
        valid: list[Transaction] = []
        with source.open("r", encoding="utf-8-sig", newline="") as fp:
            reader = csv.DictReader(fp)
            missing = [f for f in ("date", "type", "category", "amount") if f not in (reader.fieldnames or [])]
            if missing:
                raise ValidationError(
                    f"CSV 헤더에 필수 컬럼이 없습니다: {', '.join(missing)}",
                    f"헤더는 {','.join(CSV_FIELDS)} 형식이어야 합니다.",
                )
            for lineno, row in enumerate(reader, start=2):
                try:
                    tx = self.service.build_transaction(
                        date=row.get("date", ""),
                        type_=row.get("type", ""),
                        category=row.get("category", ""),
                        amount=row.get("amount", ""),
                        memo=row.get("memo", "") or "",
                        tags=row.get("tags", "") or "",
                    )
                except ValidationError as exc:
                    skipped += 1
                    errors.append((lineno, exc.message))
                    continue
                valid.append(tx)
        # 검증을 통과한 행만 한 번에 저장한다(옛 날짜가 섞여 있어도 날짜 인덱스를 한 번만 병합)
        self.service.add_many(valid)
        return ImportReport(imported=len(valid), skipped=skipped, errors=errors)

    def export_csv(self, path: Path, transactions: Iterable[Transaction]) -> int:
        """거래를 CSV 로 내보내고 건수를 돌려준다."""
        target = Path(path)
        if target.parent and not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with target.open("w", encoding="utf-8", newline="") as fp:
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
