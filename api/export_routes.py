"""数据导出/导入: 全量 JSON 备份, 用于换设备 / 还原.

策略:
- 导出: 把用户数据表 dump 成 JSON, 跳过 kline_cache (可从网络重建).
- 导入: 先把当前 DB 复制一份到 backups/ 再覆盖. 失败可手动还原.
- 上传走 multipart/form-data, 体积无上限 (本地用).
"""
from __future__ import annotations
import json
import shutil
import time
from datetime import datetime
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response

from config import config
from database import get_db


router = APIRouter(prefix="/api/data", tags=["data"])


# 哪些表要进 JSON dump. 顺序要满足外键 / 业务约束 (导入时按此顺序写入).
# kline_cache 不导出 (可从网络重建).
_TABLES = [
    "holdings",
    "external_assets",
    "position_actions",
    "unwind_plans",
    "unwind_tranches",
    "custom_alerts",
    "alert_config",
    "cashflow_monthly",
    "morning_briefings",
    "app_config",
    "trade_log",
]

EXPORT_VERSION = 1


async def _table_columns(db: aiosqlite.Connection, table: str) -> list[str]:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    return [r[1] for r in rows]


@router.get("/export")
async def export_all():
    """全量导出. 返回 JSON 文件下载."""
    db = await get_db()
    out: dict = {
        "_meta": {
            "version": EXPORT_VERSION,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "app": "licai",
        },
        "tables": {},
    }
    try:
        for t in _TABLES:
            try:
                cursor = await db.execute(f"SELECT * FROM {t}")
                rows = await cursor.fetchall()
                out["tables"][t] = [dict(r) for r in rows]
            except Exception as e:
                # 表不存在时跳过 (新装版本, 老 DB 缺表)
                print(f"[export] skip {t}: {e}")
                out["tables"][t] = []
    finally:
        await db.close()

    body = json.dumps(out, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    fname = f"licai-backup-{datetime.now().strftime('%Y%m%d-%H%M')}.json"
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "X-Export-Version": str(EXPORT_VERSION),
        },
    )


def _backup_current_db() -> Path:
    """导入前先复制当前 DB 到 backups/, 失败时可手动还原."""
    src = Path(config.db_path)
    backup_dir = src.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = backup_dir / f"pre-import-{ts}.db"
    shutil.copy2(src, dst)
    return dst


@router.post("/import")
async def import_all(file: UploadFile = File(...), mode: str = "replace"):
    """从上传 JSON 还原. mode=replace 清空表后导入; mode=merge 仅 upsert (cashflow / app_config 类 PK 表)."""
    if mode not in ("replace", "merge"):
        raise HTTPException(400, "mode 必须是 replace 或 merge")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "文件为空")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"JSON 解析失败: {e}")

    if not isinstance(payload, dict) or "tables" not in payload:
        raise HTTPException(400, "格式错误: 缺少 tables 字段")
    meta = payload.get("_meta", {})
    if int(meta.get("version", 0)) > EXPORT_VERSION:
        raise HTTPException(400, f"备份文件版本 {meta.get('version')} 比当前应用 ({EXPORT_VERSION}) 还新, 不兼容")

    backup_path = _backup_current_db()

    db = await get_db()
    summary: dict[str, int] = {}
    try:
        await db.execute("BEGIN")
        for t in _TABLES:
            rows = payload["tables"].get(t)
            if rows is None:
                continue
            cols = await _table_columns(db, t)
            if not cols:
                continue  # 表不存在
            # 过滤备份里多余的字段 (新版备份导回老 DB)
            valid_rows = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                valid_rows.append({k: v for k, v in r.items() if k in cols})
            if mode == "replace":
                await db.execute(f"DELETE FROM {t}")
            inserted = 0
            for r in valid_rows:
                if not r:
                    continue
                keys = list(r.keys())
                placeholders = ",".join(["?"] * len(keys))
                col_list = ",".join(keys)
                values = [r[k] for k in keys]
                if mode == "merge":
                    sql = f"INSERT OR REPLACE INTO {t} ({col_list}) VALUES ({placeholders})"
                else:
                    sql = f"INSERT INTO {t} ({col_list}) VALUES ({placeholders})"
                try:
                    await db.execute(sql, values)
                    inserted += 1
                except Exception as e:
                    print(f"[import] skip row in {t}: {e}")
            summary[t] = inserted
        await db.execute("COMMIT")
    except Exception as e:
        try: await db.execute("ROLLBACK")
        except Exception: pass
        raise HTTPException(500, f"导入失败 (已自动备份至 {backup_path.name}, 可手动还原): {e}")
    finally:
        await db.close()

    return {
        "message": "导入完成",
        "mode": mode,
        "pre_import_backup": str(backup_path.name),
        "imported": summary,
    }
