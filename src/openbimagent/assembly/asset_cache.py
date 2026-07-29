"""Asset Cache:hash 去重 + 429 退避(生产级必备)。

builder_fn 生成代码前先查缓存(compute_hash(batch_ctx 关键字段)),命中则返回缓存代码;
未命中则正常生成后 put 进缓存。避免重复生成相同资产,省 token 省 Blender 调用。
429 退避:被 LLM provider 限速时 mark_rate_limited(duration),check_rate_limit() 在
限速窗口内抛 RateLimitError 让调用方走降级链(回退模板)。

缓存键:sha256(json.dumps(params, sort_keys=True)) 前 16 位。
缓存目录:可配,默认 .asset_cache(项目根);测试用 tmp_path 隔离。

两类缓存条目:
- 资产文件:{hash}.blend(put/get,复制源文件)
- 代码字符串:{hash}.txt(put_text/get_text,builder_fn 用)
- 元数据:{hash}.json(params + cached_at,便于人审/失效)
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any

DEFAULT_CACHE_DIR = Path(".asset_cache")
"""默认缓存目录(项目根 .asset_cache);生产可指向共享卷。"""

CACHE_HASH_LEN = 16
"""缓存键长度(sha256 前 16 位,碰撞概率可接受:2^64)。"""


class RateLimitError(RuntimeError):
    """429 限速:在 mark_rate_limited 的窗口内调用 check_rate_limit 抛此异常。

    继承 RuntimeError(降级链 catch Exception 可一并捕获)。消息含 "429" 便于断言。
    """


class AssetCache:
    """资产缓存:基于参数 hash 去重 + 429 退避。

    线程不安全(单进程 builder_fn 用);缓存目录可配(测试用 tmp_path)。
    不可序列化参数用 default=str 兜底(str(obj)),不抛 TypeError。
    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        """初始化缓存目录与限速状态。

        cache_dir 为 None 时用 DEFAULT_CACHE_DIR(.asset_cache);否则用 Path(cache_dir)。
        目录不存在则递归创建(exist_ok=True 幂等)。
        """
        self.cache_dir: Path = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._rate_limited_until: float = 0.0
        """限速到期时间戳(time.time);0 表示未被限速。"""

    def compute_hash(self, params: dict[str, Any]) -> str:
        """计算参数 hash(sha256 前 16 位)。

        sort_keys=True 保证 dict 顺序无关;default=str 兜底不可序列化对象(Path/datetime 等)。
        """
        params_str = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(params_str.encode("utf-8")).hexdigest()[:CACHE_HASH_LEN]

    def get(self, params: dict[str, Any]) -> Path | None:
        """查询缓存(资产文件):命中返回缓存路径,未命中返回 None。

        依次检查 {hash}.blend(资产)/ {hash}.txt(代码),返回首个存在的。
        """
        key = self.compute_hash(params)
        for ext in (".blend", ".txt"):
            path = self.cache_dir / f"{key}{ext}"
            if path.exists():
                return path
        return None

    def put(self, params: dict[str, Any], asset_path: Path) -> Path:
        """写入缓存(资产文件):复制 asset_path 到 {hash}.blend,返回缓存路径。

        已存在则覆盖(shutil.copy 覆盖目标)。同时写 {hash}.json 元数据。
        """
        key = self.compute_hash(params)
        cache_path = self.cache_dir / f"{key}.blend"
        shutil.copy(asset_path, cache_path)
        self._write_meta(key, params)
        return cache_path

    def get_text(self, params: dict[str, Any]) -> str | None:
        """查询缓存(代码字符串):命中返回缓存文本,未命中返回 None。

        builder_fn 用此方法:生成前查缓存代码,命中直接返回。
        """
        key = self.compute_hash(params)
        path = self.cache_dir / f"{key}.txt"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def put_text(self, params: dict[str, Any], text: str) -> Path:
        """写入缓存(代码字符串):写 text 到 {hash}.txt,返回缓存路径。

        builder_fn 用此方法:生成后把代码写进缓存,下次同参命中。已存在则覆盖。
        """
        key = self.compute_hash(params)
        cache_path = self.cache_dir / f"{key}.txt"
        cache_path.write_text(text, encoding="utf-8")
        self._write_meta(key, params)
        return cache_path

    def check_rate_limit(self) -> None:
        """429 退避检查:限速窗口内抛 RateLimitError(消息含 "429"),过期则清状态。

        未被限速(_rate_limited_until <= 0)时直接返回(None)。
        """
        if self._rate_limited_until <= 0:
            return
        remaining = self._rate_limited_until - time.time()
        if remaining > 0:
            raise RateLimitError(f"429 Too Many Requests,请等待 {remaining:.1f}s")
        # 过期:清状态,下次调用放行
        self._rate_limited_until = 0.0

    def mark_rate_limited(self, duration: float) -> None:
        """标记 429 限速:记录到期时间戳 = now + duration(秒)。

        duration < 0 当作 0(立即过期,便于测试)。duration=0 时 check_rate_limit 立即放行
        (因为 _rate_limited_until 设为 now,下次 check 时 remaining<=0)。
        """
        if duration < 0:
            duration = 0
        self._rate_limited_until = time.time() + duration

    def _write_meta(self, key: str, params: dict[str, Any]) -> None:
        """写 {key}.json 元数据(params + cached_at),便于人审与失效诊断。"""
        meta_path = self.cache_dir / f"{key}.json"
        meta_path.write_text(
            json.dumps(
                {"params": params, "cached_at": time.time()},
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )


__all__ = ["AssetCache", "RateLimitError", "DEFAULT_CACHE_DIR"]
