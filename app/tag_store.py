"""标签存储：与容器元数据解耦的本地标签库，为智能标签预留扩展点。

设计要点：
- 标签按「相对路径」索引，独立于视频文件，不写回容器，避免格式兼容问题。
- 每个标签带 source（manual / auto / detected / imported）与 confidence，
  未来智能标签写入时无需迁移数据结构。
- meta 为自由扩展字段（模型名、检测器类型、命中规则等）。
- all() 返回聚合视图，便于后续做「按标签筛选 / 标签云」。
"""
import json
import time
from pathlib import Path
from threading import RLock

from .config import DATA_DIR

TAGS_FILE = DATA_DIR / ".workbuddy" / "tags.json"
_lock = RLock()

VALID_SOURCES = {"manual", "auto", "detected", "imported"}


def _empty() -> dict:
    return {"version": 1, "updated_at": time.time(), "items": {}}


def _load() -> dict:
    with _lock:
        if not TAGS_FILE.exists():
            return _empty()
        try:
            data = json.loads(TAGS_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
                return _empty()
            return data
        except (json.JSONDecodeError, OSError, ValueError):
            return _empty()


def _save(data: dict) -> None:
    with _lock:
        data["updated_at"] = time.time()
        TAGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        TAGS_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get(rel: str) -> list:
    """返回单个文件的标签列表。"""
    return _load()["items"].get(rel, {}).get("tags", [])


def add(rel: str, name: str, source: str = "manual",
        confidence: float = 1.0, meta: dict | None = None) -> list:
    """新增标签；同名（含来源）已存在则更新置信度与 meta。"""
    name = str(name).strip()
    if not name:
        raise ValueError("标签名不能为空")
    if source not in VALID_SOURCES:
        raise ValueError(f"无效来源: {source}")
    data = _load()
    item = data["items"].setdefault(rel, {"tags": [], "updated_at": time.time()})
    tags = item["tags"]
    existing = next((t for t in tags
                     if t["name"] == name and t["source"] == source), None)
    if existing:
        existing["confidence"] = float(confidence)
        existing["meta"] = meta or {}
    else:
        tags.append({
            "name": name,
            "source": source,
            "confidence": float(confidence),
            "meta": meta or {},
            "created_at": time.time(),
        })
    item["updated_at"] = time.time()
    _save(data)
    return tags


def remove(rel: str, name: str, source: str | None = None) -> list:
    """删除标签；source 为空时删除所有同名标签。"""
    data = _load()
    item = data["items"].get(rel)
    if not item:
        return []
    before = len(item["tags"])
    item["tags"] = [t for t in item["tags"]
                    if not (t["name"] == name and
                            (source is None or t["source"] == source))]
    if len(item["tags"]) != before:
        item["updated_at"] = time.time()
        _save(data)
    return item["tags"]


def clear(rel: str) -> None:
    """清空单个文件的全部标签。"""
    data = _load()
    if rel in data["items"]:
        del data["items"][rel]
        _save(data)


def all_tags() -> dict:
    """聚合视图：{标签名: {"count": n, "sources": [...]}}。"""
    data = _load()
    result: dict = {}
    for item in data["items"].values():
        for t in item.get("tags", []):
            entry = result.setdefault(t["name"], {"count": 0, "sources": set()})
            entry["count"] += 1
            entry["sources"].add(t.get("source", "manual"))
    return {k: {"count": v["count"], "sources": sorted(v["sources"])}
            for k, v in result.items()}


def suggest(rel: str) -> list:
    """智能标签建议的扩展点。

    当前返回空列表。后续接入规则引擎或本地模型时，只需在此返回
    [{"name", "source": "auto", "confidence", "meta"}] 即可，
    前端与 API 无需改动。
    """
    return []
