"""asset_cache 单元测试(Relay 018 任务 E1)。

覆盖 6 项:
- compute_hash:长度 16 + 稳定性(同参同 hash)+ 区分性(不同参不同 hash)
- get 未命中:返回 None
- put/get 命中:资产文件(.blend)+ 代码字符串(.txt)+ 元数据(.json)
- check_rate_limit 未限速:不抛
- mark_rate_limited + check:抛 RateLimitError(含 "429")
- 限速过期:短 duration + sleep 后 check 不抛

全程 tmp_path 隔离,不污染项目目录。
"""

from __future__ import annotations

import time

import pytest

from openbimagent.assembly.asset_cache import AssetCache, RateLimitError


def test_compute_hash(tmp_path):
    """compute_hash 返回 16 字符;同参同 hash;不同参不同 hash。"""
    cache = AssetCache(cache_dir=tmp_path)
    h1 = cache.compute_hash({"batch": ["a"], "assets": [{"id": "a"}]})
    h2 = cache.compute_hash({"batch": ["a"], "assets": [{"id": "a"}]})
    h3 = cache.compute_hash({"batch": ["b"], "assets": [{"id": "b"}]})
    assert len(h1) == 16, f"hash 长度应为 16,实际 {len(h1)}"
    assert h1 == h2, "同参应同 hash(稳定性)"
    assert h1 != h3, "不同参应不同 hash(区分性)"
    # dict 顺序无关(sort_keys)
    h4 = cache.compute_hash({"assets": [{"id": "a"}], "batch": ["a"]})
    assert h1 == h4, "dict 字段顺序不同应同 hash"


def test_get_cache_miss(tmp_path):
    """get 未命中返回 None。"""
    cache = AssetCache(cache_dir=tmp_path)
    assert cache.get({"id": "miss"}) is None
    assert cache.get_text({"id": "miss"}) is None


def test_put_and_get_cache_hit(tmp_path):
    """put 资产文件后 get 命中;put_text 后 get_text 命中;元数据 .json 落盘。"""
    cache = AssetCache(cache_dir=tmp_path)
    # 资产文件缓存
    src = tmp_path / "src.blend"
    src.write_bytes(b"FAKE_BLEND")
    params = {"batch": ["a"], "assets": [{"id": "a"}]}
    cache_path = cache.put(params, src)
    assert cache_path.exists(), "缓存文件应存在"
    assert cache_path.suffix == ".blend"
    got = cache.get(params)
    assert got is not None and got == cache_path, "put 后 get 应命中"
    # 代码字符串缓存
    code_params = {"batch": ["b"], "assets": [{"id": "b"}]}
    code = "import bpy\nbpy.ops.mesh.primitive_cube_add()\n"
    txt_path = cache.put_text(code_params, code)
    assert txt_path.exists() and txt_path.suffix == ".txt"
    assert cache.get_text(code_params) == code, "put_text 后 get_text 应命中"
    # 元数据 .json 落盘
    key = cache.compute_hash(params)
    meta = tmp_path / f"{key}.json"
    assert meta.exists(), "元数据 .json 应落盘"
    # 碰撞隔离:不同 params 的缓存文件不互相覆盖
    other = cache.get({"batch": ["zzz"]})
    assert other is None, "不同 params 不应命中"


def test_check_rate_limit_not_limited(tmp_path):
    """未被限速时 check_rate_limit 不抛。"""
    cache = AssetCache(cache_dir=tmp_path)
    # 默认未限速,多次调用都不抛
    cache.check_rate_limit()
    cache.check_rate_limit()


def test_mark_rate_limited(tmp_path):
    """mark_rate_limited 后 check_rate_limit 抛 RateLimitError,消息含 "429"。"""
    cache = AssetCache(cache_dir=tmp_path)
    cache.mark_rate_limited(60)
    with pytest.raises(RateLimitError, match="429"):
        cache.check_rate_limit()
    # RateLimitError 应是 RuntimeError 子类(降级链 catch Exception 可一并捕获)
    assert issubclass(RateLimitError, RuntimeError)


def test_rate_limit_expires(tmp_path):
    """标记限速短 duration + sleep 后 check_rate_limit 不抛(限速已过期)。"""
    cache = AssetCache(cache_dir=tmp_path)
    # 用短 duration(0.3s)加速测试,逻辑同 1s;sleep 0.35s 确保过期
    cache.mark_rate_limited(0.3)
    time.sleep(0.35)
    cache.check_rate_limit()  # 不抛即过期
    # 过期后状态清空,再次调用也不抛
    cache.check_rate_limit()
