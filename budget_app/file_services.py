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

        파일은 한 번만 읽는다. 유효한 행은 읽는 대로 저장소에 흘려보내되, 저장소는 끝까지 읽은 뒤에야
        확정하므로(append_transactions) 도중에 멈추면 아무것도 저장되지 않는다. 행을 메모리에 모아 두지 않는다.
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

        def valid_rows() -> Iterator[Transaction]:
            nonlocal skipped
            for lineno, result in self._iter_rows(source, known):
                if isinstance(result, ValidationError):
                    skipped += 1
                    if len(errors) < MAX_REPORTED_ERRORS:
                        errors.append((lineno, result.message))
                else:
                    yield result

        # 옛 날짜가 섞여 있어도 날짜 인덱스는 add_many 안에서 한 번만 병합된다
        imported = self.service.add_many(valid_rows())
        return ImportReport(imported=imported, skipped=skipped, errors=errors)

    def _iter_rows(
        self, source: Path, known: set[str]
    ) -> Iterator[tuple[int, Transaction | ValidationError]]:
        """CSV 를 한 행씩 검증해 (줄 번호, 거래 또는 그 행의 검증 오류)를 낸다.

        물리적인 한 줄을 한 행으로 읽는다. 저장할 때 메모·태그의 줄바꿈을 공백으로 바꾸므로 이 앱이 내보낸
        CSV 에는 여러 줄에 걸친 칸이 없다. 그래서 닫히지 않은 따옴표나 UTF-8 이 아닌 바이트는 그 줄만
        건너뛴다. 예전처럼 파일 전체를 CSV 로 읽으면 따옴표 하나가 뒤 행들을 메모로 흡수하거나, 따옴표 두 개가
        우연히 짝을 이뤄 두 행이 한 거래로 합쳐졌다. 헤더를 읽을 수 없을 때만 ValidationError 로 멈춘다.
        """
        with source.open("rb") as fp:
            header = self._parse_line(fp.readline(), encoding="utf-8-sig")
            if isinstance(header, ValidationError):
                raise ValidationError(
                    f"CSV 헤더를 읽을 수 없습니다: {header.message}",
                    f"첫 줄은 {','.join(CSV_FIELDS)} 형식이어야 합니다. 엑셀이라면 'CSV UTF-8(쉼표로 분리)' 형식으로 저장하세요.",
                )
            missing = [f for f in ("date", "type", "category", "amount") if f not in header]
            if missing:
                raise ValidationError(
                    f"CSV 헤더에 필수 컬럼이 없습니다: {', '.join(missing)}",
                    f"헤더는 {','.join(CSV_FIELDS)} 형식이어야 합니다.",
                )
            for lineno, raw in enumerate(fp, start=2):
                values = self._parse_line(raw)
                if isinstance(values, ValidationError):
                    yield lineno, values
                    continue
                if not values:  # 빈 줄은 csv.DictReader 처럼 건너뛴다
                    continue
                row = dict(zip(header, values))
                try:
                    yield lineno, self.service.build_transaction(
                        date=row.get("date", ""),
                        type_=row.get("type", ""),
                        category=row.get("category", ""),
                        amount=row.get("amount", ""),
                        memo=row.get("memo", ""),
                        tags=row.get("tags", ""),
                        known_categories=known,
                    )
                except ValidationError as exc:
                    yield lineno, exc

    @staticmethod
    def _parse_line(raw: bytes, encoding: str = "utf-8") -> list[str] | ValidationError:
        """CSV 한 줄을 칸 목록으로 바꾼다. 읽을 수 없으면 그 줄의 오류를 돌려준다."""
        try:
            text = raw.decode(encoding).rstrip("\r\n")
        except UnicodeDecodeError:
            return ValidationError(
                "UTF-8 이 아닌 글자가 있습니다.",
                "엑셀이라면 'CSV UTF-8(쉼표로 분리)' 형식으로 다시 저장하세요.",
            )
        if not text:
            return []
        try:
            # strict 가 아니면 "점심"맛있음 처럼 닫은 따옴표 뒤에 글자가 붙어도 오류 없이 이어 붙여 읽는다
            return next(csv.reader([text], strict=True))
        except csv.Error as exc:
            return ValidationError(
                f'따옴표(")가 맞지 않습니다: {exc}',
                '메모 등에 연 따옴표가 닫혔는지, 칸 안의 따옴표를 "" 로 두 번 썼는지 확인하세요.',
            )

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
