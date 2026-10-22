"""SQLite のバックアップ(npm run backup)。

実行中でも安全にコピーできる sqlite3 のバックアップ機能を使う。
出力: backend/backups/sauna-YYYYmmdd-HHMMSS.db 。古いものは KEEP 世代だけ残す。
バックアップには全員の記録とパスワードのハッシュが入る。共有・公開しないこと(.gitignore済み)。
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from . import db

BACKUP_DIR = Path(__file__).resolve().parent.parent / "backups"
KEEP = 14


def run(dest_dir: Path = BACKUP_DIR, keep: int = KEEP) -> Path:
    src_path = Path(db.db_path())
    if not src_path.exists():
        raise SystemExit(f"DBがまだありません: {src_path}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"sauna-{datetime.now():%Y%m%d-%H%M%S}.db"
    src = sqlite3.connect(src_path)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    dest.chmod(0o600)
    for old in sorted(dest_dir.glob("sauna-*.db"))[:-keep]:
        old.unlink()
    return dest


if __name__ == "__main__":
    out = run()
    print(f"バックアップを作りました: {out}", file=sys.stderr)
