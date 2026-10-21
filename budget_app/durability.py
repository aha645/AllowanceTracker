"""파일을 장애 복구 가능한 순서로 확정하는 보조 함수와 슬롯 undo 기록.

undo는 '변경 전으로 되돌리기'다. 위치 칸을 덮어쓰기 전에 거래번호 인덱스의 이전 길이와
이전 16바이트를 보관해, 덮어쓰다 꺼져도 원래 칸으로 되돌린다.
"""

from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path

from .models import StorageError


def fsync_dir(path: Path) -> None:
    """파일 이름 변경·삭제 사실도 전원 차단 뒤 유지되도록 디렉터리를 동기화한다."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, payload: bytes) -> None:
    """임시 파일을 완성한 뒤 교체해, 정식 기록 파일은 없거나 완전하도록 만든다."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as fp:
        fp.write(payload)
        fp.flush()  # 먼저 Python 쓰기 버퍼에서 운영체제로 보낸다.
        os.fsync(fp.fileno())  # 그 내용을 저장 장치에 반영하도록 요청한다.
    os.replace(tmp, path)
    fsync_dir(path.parent)  # 파일 내용 fsync와 이름 교체의 확정은 별도 작업이다.


class SlotUndo:
    """기존 idx 형식을 바꾸지 않고 단일 거래 슬롯을 이전 상태로 복구한다.

    기록이 남아 있으면 미완료이므로 되돌린다. 기록 삭제를 확정하면 거래 완료다.
    여러 슬롯을 쓰는 import는 기존의 배치 전체 취소 기록을 계속 사용한다.
    """

    # '<': little-endian, 8s: 서명, Q: 이전 idx 길이, Q: 거래번호, 16s: 이전 슬롯.
    # 헤더 40바이트 + SHA-256 원시 해시 32바이트 = 복구 기록 하나당 72바이트다.
    HEADER = struct.Struct("<8sQQ16s")
    MAGIC = b"TXUNDO01"  # TX(Transaction) + UNDO(되돌리기) + 형식 01

    def __init__(self, index_path: Path) -> None:
        self.index_path = index_path
        self.path = index_path.with_name(index_path.name + ".slot-undo")

    def prepare(self, tx_id: int) -> None:
        """슬롯을 덮기 전에 원래 idx 길이와 원래 바이트를 먼저 안전하게 저장한다."""
        size = self.index_path.stat().st_size
        with self.index_path.open("rb") as fp:
            fp.seek((tx_id - 1) * 16)
            # 새 add라면 이전 슬롯이 없으므로 0을 넣는다. 복구는 원래 길이로 자르면 된다.
            old = fp.read(16) if tx_id <= size // 16 else bytes(16)
        body = self.HEADER.pack(self.MAGIC, size, tx_id, old)
        atomic_write(self.path, body + hashlib.sha256(body).digest())

    def commit(self) -> None:
        """새 슬롯의 fsync가 끝난 뒤 호출한다. undo 삭제까지 확정하면 거래 완료다."""
        self.path.unlink()
        fsync_dir(self.path.parent)

    def recover(self) -> bool:
        """유효한 undo가 남으면 이전 슬롯/길이를 복원한다. 복구했으면 True다."""
        # 임시 이름만 남았다면 원래 슬롯을 수정하기 전이므로 지워도 된다.
        self.path.with_name(self.path.name + ".tmp").unlink(missing_ok=True)
        if not self.path.exists():
            return False
        raw = self.path.read_bytes()
        # 복구 기록이 손상됐다면 무엇을 덮어써야 할지 추측하지 않고 오류로 멈춘다.
        if len(raw) != self.HEADER.size + 32 or hashlib.sha256(raw[:-32]).digest() != raw[-32:]:
            raise StorageError("거래 슬롯 복구 기록이 손상되었습니다.", "백업본으로 복구하세요.")
        magic, size, tx_id, old = self.HEADER.unpack(raw[:-32])
        if magic != self.MAGIC or size % 16 or not 1 <= tx_id <= size // 16 + 1:
            raise StorageError("거래 슬롯 복구 기록의 범위가 잘못되었습니다.", "백업본으로 복구하세요.")
        if self.index_path.stat().st_size < size:
            raise StorageError("복구할 거래번호 인덱스가 잘렸습니다.", "백업본으로 복구하세요.")
        with self.index_path.open("r+b") as fp:
            if tx_id <= size // 16:
                # update/delete: 기존 슬롯의 일부가 찢어져도 원래 16바이트로 덮어 복원한다.
                fp.seek((tx_id - 1) * 16)
                fp.write(old)
            fp.truncate(size)  # add: 새로 생긴 완전/부분 슬롯을 원래 파일 길이로 제거한다.
            fp.flush()
            os.fsync(fp.fileno())
        # 복구 결과를 fsync한 뒤 기록을 지운다. 그 전에 다시 종료돼도 같은 복구를 반복한다.
        self.commit()
        return True
