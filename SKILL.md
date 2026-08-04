---
name: geoskill-data-versioning
description: '对矢量数据做变更检测、版本快照与差异比较，支持 commit / diff / log 操作。Change detection, version snapshots and diff comparison for vector data with commit / diff / log operations.'
---

# 空间数据版本管理 | Spatial Data Versioning

Provides lightweight version management for vector data, centered on "snapshot + change detection":

- **commit**: writes the current state as a GeoJSON snapshot, assigns an auto-incrementing version number (v1, v2, …) and a content hash (SHA-256, based on geometry WKT + attributes), and appends it to the version log versions.json.
- **diff**: compares two versions by a stable key (default `id`), reporting added / removed / modified sets and field-level change details (including geometry-change flags).
- **log**: lists all version entries (version tag, timestamp, feature count, hash, commit message).

Change detection performs NaN-safe comparisons of attribute values and exact WKT matching for geometries. Suitable for data-update tracking, change auditing, and incremental distribution. `--synthetic` mode generates two states ("baseline + changes") and demonstrates the full commit → commit → diff → log workflow offline.

## Dependencies / 依赖

```bash
pip install numpy rasterio geopandas shapely fiona pyproj
```

## Usage / 使用方法

### Basic Usage

```bash
python geoskill-data-versioning.py --bbox 116.0 39.0 117.0 40.0 --synthetic --output-dir ./out
```

### Example 1 (synthetic data, full version stream, offline)

```bash
python geoskill-data-versioning.py --bbox 116.0 39.0 117.0 40.0 --synthetic --features 30 --output-dir ./repo
```

### Example 2: commit real data as the baseline version

```bash
python geoskill-data-versioning.py --input parcels.gpkg --message "initial import" --output-dir ./parcel_repo
```

### Example 3: specify a comparison key field

```bash
python geoskill-data-versioning.py --input buildings.shp --key osm_id --message "2025 update" --output-dir ./bld_repo
```

### Example 4: commit with author information

```bash
python geoskill-data-versioning.py --bbox 121.0 31.0 122.0 32.0 --synthetic --author zhangsan --message "demo" --output-dir ./repo2
```

### Example 5: quiet commit

```bash
python geoskill-data-versioning.py --input roads.geojson --message "v1" --quiet --output-dir ./roads_repo
```

## Output / 输出

| File | Format | Description |
|---|---|---|
| `version_store/v{N}.geojson` | GeoJSON | Snapshot of each version |
| `version_store/versions.json` | JSON | Version log (tag/hash/timestamp/feature count) |
| `versioning_report.json` | JSON | Version list + diff results |
| `output-manifest.json` | JSON | Run manifest |

## Data Source / 数据源 / Source

- `--input`: local vector file
- `--synthetic`: locally generates two states, baseline + changes

## Privacy / 隐私声明 / Privacy

- Runs offline by default; `--synthetic` mode requires no network at all.
- All processing is done locally; no user data is uploaded.

## License / License

MIT

---

<!-- ===== 中文原文 (Chinese Original) ===== -->

---
name: geoskill-data-versioning
description: '对矢量数据做变更检测、版本快照与差异比较，支持 commit / diff / log 操作。Change detection, version snapshots and diff comparison for vector data with commit / diff / log operations.'
---

# 空间数据版本管理 | Spatial Data Versioning

对矢量数据做轻量级版本管理，核心是“快照 + 变更检测”：

- **commit**：把当前状态写成 GeoJSON 快照，分配自增版本号（v1, v2, …）
  与内容哈希（SHA-256，基于几何 WKT + 属性），追加到版本日志 versions.json。
- **diff**：按稳定键（默认 `id`）比较两个版本，给出 added / removed /
  modified 集合与字段级变更明细（含几何变化标记）。
- **log**：列出全部版本条目（版本 tag、时间戳、要素数、哈希、提交信息）。

变更检测对属性值做 NaN 安全比较，几何用 WKT 精确比对。适合数据更新追踪、
变更审计、增量分发。`--synthetic` 模式生成“基准 + 改动”两个状态，离线
演示完整的 commit → commit → diff → log 流程。

## 依赖

```bash
pip install numpy rasterio geopandas shapely fiona pyproj
```

## 使用方法

### 基本用法

```bash
python geoskill-data-versioning.py --bbox 116.0 39.0 117.0 40.0 --synthetic --output-dir ./out
```

### 示例 1（合成数据，完整版本流，离线）

```bash
python geoskill-data-versioning.py --bbox 116.0 39.0 117.0 40.0 --synthetic --features 30 --output-dir ./repo
```

### 示例 2：提交真实数据为基线版本

```bash
python geoskill-data-versioning.py --input parcels.gpkg --message "initial import" --output-dir ./parcel_repo
```

### 示例 3：指定比较键字段

```bash
python geoskill-data-versioning.py --input buildings.shp --key osm_id --message "2025 update" --output-dir ./bld_repo
```

### 示例 4：带作者信息的提交

```bash
python geoskill-data-versioning.py --bbox 121.0 31.0 122.0 32.0 --synthetic --author zhangsan --message "demo" --output-dir ./repo2
```

### 示例 5：静默提交

```bash
python geoskill-data-versioning.py --input roads.geojson --message "v1" --quiet --output-dir ./roads_repo
```

## 输出

| 文件 | 格式 | 说明 |
|---|---|---|
| `version_store/v{N}.geojson` | GeoJSON | 各版本快照 |
| `version_store/versions.json` | JSON | 版本日志（tag/哈希/时间/要素数） |
| `versioning_report.json` | JSON | 版本列表 + diff 结果 |
| `output-manifest.json` | JSON | 运行清单 |

## 数据源 / Source

- `--input`：本地矢量文件
- `--synthetic`：本地生成基准 + 改动两个状态

## 隐私声明 / Privacy

- 默认离线运行，`--synthetic` 模式完全无网络。
- 所有处理在本地完成，不上传用户数据。

## License

MIT
