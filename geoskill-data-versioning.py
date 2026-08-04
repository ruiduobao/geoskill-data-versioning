#!/usr/bin/env python3
"""data-versioning — 空间数据版本管理

对矢量数据做轻量版本管理，核心是“快照 + 变更检测”：

- **commit**：把当前状态写成 GeoJSON 快照，分配自增版本号与内容哈希，
  追加到版本日志（versions.json）。
- **diff**：按稳定键（默认 ``id``）比较两个版本，给出 added / removed /
  modified 要素集合与字段级变更。
- **log**：列出全部版本条目。

变更检测基于几何 WKT 与属性字典的精确比较，对 NaN 做相等处理。

数据源：``--input`` 矢量文件，或 ``--synthetic`` 模式生成“基准 + 改动”两个
状态以离线演示完整版本流（commit → commit → diff → log）。

隐私声明 / Privacy：
- 默认完全离线运行，``--synthetic`` 模式不读取任何外部数据。
- 所有处理在本地完成，不上传用户数据。

Usage:
    python data-versioning.py --input parcels.gpkg --message "initial import"
    python data-versioning.py --bbox 116 39 117 40 --synthetic --output-dir ./out

License: MIT
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

VERSION = "1.0.0"
SKILL_NAME = "data-versioning"

# ---- 共享核心库（本地 vendored，随脚本目录一起分发）----
try:
    from _geoskill_core.errors import (
        GeoSkillError, UsageError, ValidationError, ProcessError, to_exit_code,
    )
    from _geoskill_core.manifest import OutputManifest, OutputFile
except ImportError:  # pragma: no cover - fallback minimal definitions
    class GeoSkillError(Exception):
        def __init__(self, message: str, code: int = 7, kind: str = "EGeo", **kw):
            super().__init__(message)
            self.message, self.code, self.kind = message, code, kind

    class UsageError(GeoSkillError):
        def __init__(self, m, **k): super().__init__(m, code=2, kind="EUsage", **k)

    class ValidationError(GeoSkillError):
        def __init__(self, m, **k): super().__init__(m, code=6, kind="EValidate", **k)

    class ProcessError(GeoSkillError):
        def __init__(self, m, **k): super().__init__(m, code=7, kind="EProcess", **k)

    def to_exit_code(exc):
        return getattr(exc, "code", 7)

    OutputManifest = None
    OutputFile = None


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# 校验：bbox / features / input 存在性
# ---------------------------------------------------------------------------
def validate_bbox(bbox) -> None:
    """P0: bbox 合法性前置校验。"""
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValidationError(
            f"bbox must be a 4-element [W S E N]; got {bbox!r}"
        )
    try:
        w, s, e, n = [float(v) for v in bbox]
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"bbox values must be numeric; got {bbox!r}") from exc
    if not (-180.0 <= w <= 180.0 and -180.0 <= e <= 180.0):
        raise ValidationError(
            f"bbox longitude out of range [-180, 180]: W={w}, E={e}"
        )
    if not (-90.0 <= s <= 90.0 and -90.0 <= n <= 90.0):
        raise ValidationError(
            f"bbox latitude out of range [-90, 90]: S={s}, N={n}"
        )
    if w >= e:
        if w > 0 and e < 0 and (e - w) > -360:
            raise ValidationError(
                f"bbox W ({w}) >= E ({e}); cross-180° antimeridian is not "
                f"supported — split into two extents"
            )
        raise ValidationError(f"bbox W ({w}) must be < E ({e})")
    if s >= n:
        raise ValidationError(f"bbox S ({s}) must be < N ({n})")
    area = (e - w) * (n - s)
    if area <= 0:
        raise ValidationError(f"bbox area must be > 0; got {area}")


def validate_synthetic_params(features: int) -> None:
    if not isinstance(features, int) or features < 1:
        raise UsageError(f"--features must be >= 1; got {features}")


# ---------------------------------------------------------------------------
# 核心：变更检测
# ---------------------------------------------------------------------------
def _norm_value(v: Any) -> Any:
    """把 numpy/NaN 值规整为可哈希、可比较的 Python 值。"""
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return "NaN" if math.isnan(f) else round(f, 8)
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def _feature_signature(row: Any, attr_cols: List[str]) -> Tuple[str, Tuple]:
    geom = row.geometry
    wkt = geom.wkt if geom is not None else ""
    attrs = tuple((c, _norm_value(row[c])) for c in attr_cols)
    return wkt, attrs


def detect_changes(old: Any, new: Any, key: str = "id") -> Dict[str, Any]:
    """按键比较两个 GeoDataFrame，返回 added/removed/modified。"""
    old_cols = [c for c in old.columns if c != "geometry"]
    new_cols = [c for c in new.columns if c != "geometry"]
    common_cols = sorted(set(old_cols) & set(new_cols))
    if key not in new.columns or key not in old.columns:
        raise ValidationError(f"key field '{key}' missing in one of the versions")

    def index_gdf(gdf, cols):
        d = {}
        for _, row in gdf.iterrows():
            k = _norm_value(row[key])
            d[k] = _feature_signature(row, cols)
        return d

    old_idx = index_gdf(old, old_cols)
    new_idx = index_gdf(new, new_cols)

    old_keys = set(old_idx)
    new_keys = set(new_idx)
    added = sorted(new_keys - old_keys, key=str)
    removed = sorted(old_keys - new_keys, key=str)

    modified = []
    for k in sorted(old_keys & new_keys, key=str):
        ow, oa = old_idx[k]
        nw, na = new_idx[k]
        changes = {}
        if ow != nw:
            changes["geometry"] = "changed"
        old_a = dict(oa)
        new_a = dict(na)
        for c in common_cols:
            if c == key:
                continue
            if old_a.get(c) != new_a.get(c):
                changes[c] = {"old": old_a.get(c), "new": new_a.get(c)}
        if changes:
            modified.append({"key": str(k), "changes": changes})

    return {
        "added": [str(x) for x in added],
        "removed": [str(x) for x in removed],
        "modified": modified,
        "n_added": len(added),
        "n_removed": len(removed),
        "n_modified": len(modified),
        "n_changed": len(added) + len(removed) + len(modified),
    }


# ---------------------------------------------------------------------------
# 版本存储：commit / load / diff / log
# ---------------------------------------------------------------------------
def _store_files(store_dir: str) -> Tuple[str, str]:
    return store_dir, os.path.join(store_dir, "versions.json")


def _content_hash(gdf: Any) -> str:
    """基于几何 WKT + 属性的稳定内容哈希。"""
    h = hashlib.sha256()
    cols = sorted(c for c in gdf.columns if c != "geometry")
    for _, row in gdf.iterrows():
        geom = row.geometry
        h.update((geom.wkt if geom is not None else "").encode("utf-8"))
        for c in cols:
            h.update(repr(_norm_value(row[c])).encode("utf-8"))
    return h.hexdigest()[:16]


def init_store(store_dir: str) -> None:
    os.makedirs(store_dir, exist_ok=True)
    _, manifest = _store_files(store_dir)
    if not os.path.exists(manifest):
        with open(manifest, "w", encoding="utf-8") as f:
            json.dump({"next_id": 1, "versions": []}, f, indent=2)


def _read_manifest(manifest: str) -> Dict[str, Any]:
    with open(manifest, encoding="utf-8") as f:
        return json.load(f)


def commit(store_dir: str, gdf: Any, message: str = "",
           author: str = "skill") -> Dict[str, Any]:
    """提交一个版本快照，返回版本条目。"""
    init_store(store_dir)
    _, manifest = _store_files(store_dir)
    data = _read_manifest(manifest)
    vid = data["next_id"]
    snapshot = os.path.join(store_dir, f"v{vid}.geojson")
    gdf.to_file(snapshot, driver="GeoJSON")
    entry = {
        "id": vid,
        "tag": f"v{vid}",
        "message": message,
        "author": author,
        "timestamp": _utc_now(),
        "content_hash": _content_hash(gdf),
        "feature_count": int(len(gdf)),
        "snapshot": f"v{vid}.geojson",
    }
    data["versions"].append(entry)
    data["next_id"] = vid + 1
    with open(manifest, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return entry


def load_version(store_dir: str, version_id: int) -> Any:
    import geopandas as gpd
    snapshot = os.path.join(store_dir, f"v{int(version_id)}.geojson")
    if not os.path.exists(snapshot):
        raise UsageError(f"version v{version_id} not found", version=version_id)
    return gpd.read_file(snapshot)


def version_log(store_dir: str) -> List[Dict[str, Any]]:
    _, manifest = _store_files(store_dir)
    if not os.path.exists(manifest):
        return []
    return _read_manifest(manifest)["versions"]


def diff_versions(store_dir: str, v1: int, v2: int, key: str = "id") -> Dict[str, Any]:
    old = load_version(store_dir, v1)
    new = load_version(store_dir, v2)
    changes = detect_changes(old, new, key=key)
    changes["from"] = f"v{v1}"
    changes["to"] = f"v{v2}"
    return changes


# ---------------------------------------------------------------------------
# 合成数据：基准 + 改动两个状态
# ---------------------------------------------------------------------------
def generate_synthetic_base(bbox: List[float], n: int = 20, seed: int = 42) -> Any:
    import geopandas as gpd
    from shapely.geometry import Polygon
    from pyproj import CRS
    rng = np.random.default_rng(seed)
    w, s, e, n_ = bbox
    geoms, vals = [], []
    dx = (e - w) / int(np.ceil(np.sqrt(n)))
    dy = (n_ - s) / int(np.ceil(np.sqrt(n)))
    for i in range(n):
        x = w + (i % 5) * dx * 0.9
        y = s + (i // 5) * dy * 0.9
        d = dx * 0.5
        geoms.append(Polygon([(x, y), (x + d, y), (x + d, y + d), (x, y + d), (x, y)]))
        vals.append(float(rng.uniform(0, 100)))
    return gpd.GeoDataFrame(
        {"id": np.arange(1, n + 1), "value": np.round(vals, 3)},
        geometry=geoms, crs=CRS.from_epsg(4326))


def make_modified(base: Any, seed: int = 7) -> Any:
    """从基准派生一个“改动版”：改一个属性、删一个要素、加一个要素。"""
    import pandas as pd
    import geopandas as gpd
    from shapely.geometry import Polygon
    gdf = base.copy()
    # 修改第一个要素的 value
    gdf.loc[gdf.index[0], "value"] = float(gdf.loc[gdf.index[0], "value"]) + 1000.0
    # 删除最后一个要素
    gdf = gdf.iloc[:-1].copy()
    # 新增一个要素
    b = base.total_bounds
    new_geom = Polygon([(b[0], b[1]), (b[0] + 0.01, b[1]),
                        (b[0] + 0.01, b[1] + 0.01), (b[0], b[1] + 0.01), (b[0], b[1])])
    new_row = gpd.GeoDataFrame(
        {"id": [int(base["id"].max()) + 1], "value": [999.0]},
        geometry=[new_geom], crs=base.crs)
    return gpd.GeoDataFrame(pd.concat([gdf, new_row], ignore_index=True), crs=base.crs)


def read_vector(path: str) -> Any:
    import geopandas as gpd
    # 文件不存在按 CONVENTIONS.md 约定 → UsageError (rc=2)
    if not os.path.exists(path):
        raise UsageError(f"input vector not found: {path}", path=path)
    try:
        return gpd.read_file(path)
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(f"failed to read '{path}': {exc}") from exc


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------
def write_manifest(
    output_dir: str,
    args: argparse.Namespace,
    outputs: List[Dict[str, Any]],
    qa: Dict[str, Any],
    started_at: str,
    exit_code: int,
    bbox: Optional[List[float]],
) -> Optional[str]:
    if OutputManifest is None:
        return None
    cmd = " ".join([SKILL_NAME] + sys.argv[1:])
    man = OutputManifest(
        skill=SKILL_NAME,
        skill_version=VERSION,
        command=cmd,
        started_at=started_at,
        finished_at=_utc_now(),
        exit_code=exit_code,
        inputs={
            "input": getattr(args, "input", None),
            "message": getattr(args, "message", None),
            "key": getattr(args, "key", None),
            "synthetic": bool(getattr(args, "synthetic", False)),
            "bbox": bbox,
        },
        outputs=[OutputFile(**o) for o in outputs],
        qa=qa,
        software={"python": sys.version.split()[0], "skill": SKILL_NAME},
    )
    path = os.path.join(output_dir, "output-manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(man.to_dict(), f, ensure_ascii=False, indent=2, default=str)
    return path


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def process(args: argparse.Namespace) -> int:
    started_at = _utc_now()
    output_dir = args.output_dir
    bbox = list(args.bbox) if args.bbox else None

    # 0) 参数前置校验（P0/P1）
    if args.synthetic or not args.input:
        if bbox is None:
            raise UsageError("provide --bbox (synthetic mode) or --input <vector>")
        validate_bbox(bbox)
        validate_synthetic_params(int(args.features))

    store_dir = os.path.join(output_dir, "version_store")

    if args.input and not args.synthetic:
        current = read_vector(args.input)
        source_note = args.input
        synthetic_flow = False
    else:
        current = generate_synthetic_base(bbox, n=args.features)
        source_note = "synthetic"
        synthetic_flow = True

    if len(current) == 0:
        raise ValidationError("input vector has no features")

    # 1) 所有校验通过后才创建输出目录
    os.makedirs(output_dir, exist_ok=True)

    # commit v1
    e1 = commit(store_dir, current, message=args.message or "baseline",
                author=args.author)
    changes: Optional[Dict[str, Any]] = None
    e2: Optional[Dict[str, Any]] = None
    if synthetic_flow:
        modified = make_modified(current)
        e2 = commit(store_dir, modified, message="synthetic edit",
                    author=args.author)
        changes = diff_versions(store_dir, e1["id"], e2["id"], key=args.key)

    entries = version_log(store_dir)
    report = {
        "skill": SKILL_NAME,
        "source": source_note,
        "key_field": args.key,
        "versions": entries,
        "diff": changes,
    }
    report_path = os.path.join(output_dir, "versioning_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    qa: Dict[str, Any] = {
        "source": source_note,
        "n_versions": len(entries),
        "latest_version": entries[-1]["tag"] if entries else None,
    }
    if changes is not None:
        qa["n_added"] = changes["n_added"]
        qa["n_removed"] = changes["n_removed"]
        qa["n_modified"] = changes["n_modified"]

    outputs = [{"path": report_path, "kind": "json"}]
    man_path = write_manifest(output_dir, args, outputs, qa, started_at, 0, bbox)

    if not args.quiet:
        print(f"[{SKILL_NAME}] source: {source_note}")
        print(f"[{SKILL_NAME}] versions committed: {len(entries)}")
        for e in entries:
            print(f"[{SKILL_NAME}]   {e['tag']} ({e['feature_count']} feats) {e['message']}")
        if changes is not None:
            print(f"[{SKILL_NAME}] diff {changes['from']}..{changes['to']}: "
                  f"+{changes['n_added']} -{changes['n_removed']} ~{changes['n_modified']}")
        if man_path:
            print(f"[{SKILL_NAME}] manifest: {man_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=SKILL_NAME,
        description="Version vector data: commit snapshots, diff versions, show log.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--bbox", nargs=4, type=float, metavar=("W", "S", "E", "N"),
                   help="geographic extent [minLon minLat maxLon maxLat]")
    p.add_argument("--input", help="input vector file to commit")
    p.add_argument("--message", default="", help="commit message")
    p.add_argument("--author", default="skill", help="commit author")
    p.add_argument("--key", default="id", help="stable key field for diff (default: id)")
    p.add_argument("--features", type=int, default=20,
                   help="number of synthetic features (default: 20)")
    p.add_argument("--synthetic", action="store_true",
                   help="generate baseline + edited versions (offline)")
    p.add_argument("--output-dir", default="./output", help="output directory")
    p.add_argument("--quiet", action="store_true", help="suppress console output")
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return process(args)
    except GeoSkillError as exc:
        print(f"[{SKILL_NAME}] ERROR [{exc.kind}] {exc.message}", file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"[{SKILL_NAME}] ERROR {exc}", file=sys.stderr)
        return to_exit_code(exc)


if __name__ == "__main__":
    sys.exit(main())
