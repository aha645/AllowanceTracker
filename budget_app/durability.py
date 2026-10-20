"""Small durable-file primitives; no database or external dependencies."""

from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path

from .models import StorageError


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, payload: bytes) -> None:
    """Publish complete bytes, never a partial journal, including directory sync."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as fp:
        fp.write(payload)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(tmp, path)
    fsync_dir(path.parent)


class SlotUndo:
    """Undo one in-place 16-byte id slot without changing the idx format.

    A durable journal means *uncommitted*. Its durable removal commits the slot.
    Import continues to use its original whole-batch truncation journal.
    """

    HEADER = struct.Struct("<8sQQ16s")
    MAGIC = b"TXUNDO01"

    def __init__(self, index_path: Path) -> None:
        self.index_path = index_path
        self.path = index_path.with_name(index_path.name + ".slot-undo")

    def prepare(self, tx_id: int) -> None:
        size = self.index_path.stat().st_size
        with self.index_path.open("rb") as fp:
            fp.seek((tx_id - 1) * 16)
            old = fp.read(16) if tx_id <= size // 16 else bytes(16)
        body = self.HEADER.pack(self.MAGIC, size, tx_id, old)
        atomic_write(self.path, body + hashlib.sha256(body).digest())

    def commit(self) -> None:
        self.path.unlink()
        fsync_dir(self.path.parent)

    def recover(self) -> bool:
        self.path.with_name(self.path.name + ".tmp").unlink(missing_ok=True)
        if not self.path.exists():
            return False
        raw = self.path.read_bytes()
        if len(raw) != self.HEADER.size + 32 or hashlib.sha256(raw[:-32]).digest() != raw[-32:]:
            raise StorageError("거래 슬롯 복구 기록이 손상되었습니다.", "백업본으로 복구하세요.")
        magic, size, tx_id, old = self.HEADER.unpack(raw[:-32])
        if magic != self.MAGIC or size % 16 or not 1 <= tx_id <= size // 16 + 1:
            raise StorageError("거래 슬롯 복구 기록의 범위가 잘못되었습니다.", "백업본으로 복구하세요.")
        if self.index_path.stat().st_size < size:
            raise StorageError("복구할 거래번호 인덱스가 잘렸습니다.", "백업본으로 복구하세요.")
        with self.index_path.open("r+b") as fp:
            if tx_id <= size // 16:
                fp.seek((tx_id - 1) * 16)
                fp.write(old)
            fp.truncate(size)
            fp.flush()
            os.fsync(fp.fileno())
        self.commit()
        return True
