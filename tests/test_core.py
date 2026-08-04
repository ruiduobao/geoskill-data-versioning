"""Core algorithm tests for data-versioning."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from conftest import mod as M


def _square(x, y, d=1.0):
    from shapely.geometry import Polygon
    return Polygon([(x, y), (x + d, y), (x + d, y + d), (x, y + d), (x, y)])


def _make_gdf(ids, values, geoms):
    import geopandas as gpd
    from pyproj import CRS
    return gpd.GeoDataFrame({"id": ids, "value": values},
                            geometry=geoms, crs=CRS.from_epsg(4326))


class TestNormValue:
    def test_nan_equal(self):
        assert M._norm_value(float("nan")) == M._norm_value(float("nan"))

    def test_numpy_int(self):
        assert M._norm_value(np.int64(5)) == 5


class TestDetectChanges:
    def test_added_removed_modified(self):
        old = _make_gdf([1, 2, 3], [10.0, 20.0, 30.0],
                        [_square(0, 0), _square(2, 0), _square(4, 0)])
        new = _make_gdf([2, 3, 4], [20.0, 99.0, 40.0],
                        [_square(2, 0), _square(4, 0), _square(6, 0)])
        ch = M.detect_changes(old, new, "id")
        assert ch["added"] == ["4"]
        assert ch["removed"] == ["1"]
        assert [x["key"] for x in ch["modified"]] == ["3"]
        assert ch["n_changed"] == 3

    def test_geometry_change_detected(self):
        old = _make_gdf([1], [10.0], [_square(0, 0)])
        new = _make_gdf([1], [10.0], [_square(5, 5)])  # 同属性、几何移动
        ch = M.detect_changes(old, new, "id")
        assert ch["n_modified"] == 1
        assert "geometry" in ch["modified"][0]["changes"]

    def test_no_changes(self):
        old = _make_gdf([1, 2], [10.0, 20.0], [_square(0, 0), _square(2, 0)])
        new = _make_gdf([1, 2], [10.0, 20.0], [_square(0, 0), _square(2, 0)])
        ch = M.detect_changes(old, new, "id")
        assert ch["n_changed"] == 0

    def test_missing_key_raises(self):
        old = _make_gdf([1], [10.0], [_square(0, 0)])
        new = _make_gdf([1], [10.0], [_square(0, 0)]).rename(columns={"id": "fid"})
        with pytest.raises(M.ValidationError):
            M.detect_changes(old, new, "id")


class TestVersionStore:
    def test_commit_and_load_roundtrip(self, tmp_path):
        store = str(tmp_path / "store")
        gdf = _make_gdf([1, 2, 3], [10.0, 20.0, 30.0],
                        [_square(0, 0), _square(2, 0), _square(4, 0)])
        e1 = M.commit(store, gdf, "baseline")
        assert e1["id"] == 1
        assert e1["feature_count"] == 3
        back = M.load_version(store, 1)
        assert len(back) == 3
        assert set(back["id"]) == {1, 2, 3}

    def test_content_hash_differs(self, tmp_path):
        store = str(tmp_path / "store")
        g1 = _make_gdf([1], [10.0], [_square(0, 0)])
        g2 = _make_gdf([1], [99.0], [_square(0, 0)])
        e1 = M.commit(store, g1)
        e2 = M.commit(store, g2)
        assert e1["content_hash"] != e2["content_hash"]

    def test_same_content_same_hash(self, tmp_path):
        store = str(tmp_path / "store")
        g1 = _make_gdf([1], [10.0], [_square(0, 0)])
        g2 = _make_gdf([1], [10.0], [_square(0, 0)])
        e1 = M.commit(store, g1)
        e2 = M.commit(store, g2)
        assert e1["content_hash"] == e2["content_hash"]

    def test_version_ids_increment(self, tmp_path):
        store = str(tmp_path / "store")
        gdf = _make_gdf([1], [10.0], [_square(0, 0)])
        ids = [M.commit(store, gdf)["id"] for _ in range(3)]
        assert ids == [1, 2, 3]

    def test_log_order(self, tmp_path):
        store = str(tmp_path / "store")
        gdf = _make_gdf([1], [10.0], [_square(0, 0)])
        M.commit(store, gdf, "first")
        M.commit(store, gdf, "second")
        log = M.version_log(store)
        assert [e["message"] for e in log] == ["first", "second"]

    def test_load_missing_version_raises(self, tmp_path):
        store = str(tmp_path / "store")
        M.init_store(store)
        with pytest.raises(M.UsageError):
            M.load_version(store, 99)


class TestDiffVersions:
    def test_diff_counts(self, tmp_path):
        store = str(tmp_path / "store")
        base = M.generate_synthetic_base([116, 39, 117, 40], n=20)
        mod = M.make_modified(base)
        e1 = M.commit(store, base, "base")
        e2 = M.commit(store, mod, "edit")
        d = M.diff_versions(store, e1["id"], e2["id"], key="id")
        assert d["n_added"] == 1
        assert d["n_removed"] == 1
        assert d["n_modified"] == 1
        assert d["from"] == "v1" and d["to"] == "v2"


class TestSynthetic:
    def test_base_and_modified(self):
        base = M.generate_synthetic_base([116, 39, 117, 40], n=20)
        mod = M.make_modified(base)
        assert len(base) == 20
        assert len(mod) == 20  # 删一个 + 加一个
        ch = M.detect_changes(base, mod, "id")
        assert ch["n_added"] == 1
        assert ch["n_removed"] == 1
        assert ch["n_modified"] == 1

    def test_read_missing_raises(self):
        with pytest.raises(M.UsageError):
            M.read_vector("/nonexistent/nope.shp")
