"""analyze：拉歌曲 → 取标签（逐首落盘，可续传）→ 分类 → 生成整理方案。

所有平台访问都经由 MusicBackend。标签和曲风树按配置缓存（默认 30 天），
第二次分析同一歌单只需要读歌单与歌曲详情。
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any

from tunetidy.apply.writer import ApplyError
from tunetidy.backends.base import MusicBackend
from tunetidy.classify.pipeline import Assignment, FirstPass, Status, classify_songs
from tunetidy.classify.split import SplitSettings, resolve_groups
from tunetidy.classify.taxonomy import Taxonomy
from tunetidy.config import Config, parse_config
from tunetidy.models import (
    CopyrightStatus,
    GenreTag,
    PlaylistInfo,
    Song,
    SongKey,
    SongTags,
    StyleNode,
)
from tunetidy.plan import Plan, PlanStatus, Source, source_label
from tunetidy.storage import plans as plan_store
from tunetidy.storage import repo

Progress = Callable[[str, int, int], None]  # (阶段, 已完成, 总数)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _key(k: SongKey) -> str:
    return f"{k.platform}:{k.id}"


def collect_songs(backend: MusicBackend, sources: Sequence[PlaylistInfo]) -> list[Song]:
    """多个来源歌单合并去重，保留第一次出现的位置。"""
    seen: set[SongKey] = set()
    out: list[Song] = []
    for src in sources:
        for s in backend.get_tracks(src.id):
            if s.key not in seen:
                seen.add(s.key)
                out.append(s)
    return out


def load_taxonomy(
    backend: MusicBackend, conn: sqlite3.Connection, now: datetime, max_age_days: int
) -> list[StyleNode]:
    nodes = repo.fresh_style_tree(conn, backend.platform, now, max_age_days)
    if nodes is None:
        nodes = backend.get_style_tree()
        repo.save_style_tree(conn, backend.platform, Taxonomy(nodes).version, nodes, now)
    return nodes


def fetch_tags(
    backend: MusicBackend,
    conn: sqlite3.Connection,
    songs: Sequence[Song],
    now: Callable[[], datetime],
    max_age_days: int,
    on_progress: Progress | None = None,
    on_tag: Callable[[SongKey, SongTags | None], None] | None = None,
) -> dict[SongKey, SongTags]:
    """on_tag：每首歌回调一次。先按歌曲顺序报不用联网的（缓存命中的给标签，云盘歌给 None），
    再开始联网（on_progress 的 done、total 只计联网的歌），联网取到一首报一首。"""
    keys = [s.key for s in songs]
    tags = repo.fresh_tags(conn, keys, now(), max_age_days)
    # 云盘歌曲没有公共曲风数据，不去请求
    need = [s.key.id for s in songs if s.key not in tags and s.copyright != CopyrightStatus.CLOUD]
    if on_tag:
        for s in songs:
            if s.key in tags or s.copyright == CopyrightStatus.CLOUD:
                on_tag(s.key, tags.get(s.key))
    if on_progress:
        on_progress("tags", 0, len(need))
    for i, t in enumerate(backend.get_song_tags(need), 1):
        repo.save_tags(conn, t, now())  # 逐首提交，中断后重跑只取剩下的
        tags[t.key] = t
        if on_tag:
            on_tag(t.key, t)
        if on_progress:
            on_progress("tags", i, len(need))
    return tags


def _settings(cfg: Config, split: SplitSettings) -> dict[str, Any]:
    c = asdict(cfg.classify)
    c = {k: list(v) if isinstance(v, tuple) else v for k, v in c.items()}
    return {
        "split": split.to_dict(),
        "classify": c,
        "name_template": cfg.apply.name_template,
        "other_name": cfg.classify.other_name,
    }


def resolve_sources(all_playlists: list[PlaylistInfo], wanted: list[str]) -> list[PlaylistInfo]:
    """按歌单 id 或完整名称找来源歌单（去重保序）；重名或找不到时报错。"""
    by_id = {p.id: p for p in all_playlists}
    out: list[PlaylistInfo] = []
    for w in wanted:
        if w in by_id:
            out.append(by_id[w])
            continue
        named = [p for p in all_playlists if p.name == w]
        if len(named) == 1:
            out.append(named[0])
        elif named:
            raise ApplyError(f"有 {len(named)} 个歌单都叫「{w}」，请改用歌单 id")
        else:
            raise ApplyError(f"找不到歌单「{w}」，先用 tunetidy playlists 查看")
    return list({p.id: p for p in out}.values())


def _snapshot(
    songs: Sequence[Song], tags: dict[SongKey, SongTags], nodes: Sequence[StyleNode]
) -> dict[str, Any]:
    return {
        "songs": [[s.key.platform, s.key.id, s.copyright.value] for s in songs],
        "tags": {
            _key(s.key): json.loads(repo.tags_to_json(tags[s.key])) for s in songs if s.key in tags
        },
        "style_tree": [[n.tag_id, n.name, n.parent_id, n.level] for n in nodes],
    }


def analyze(
    backend: MusicBackend,
    conn: sqlite3.Connection,
    config: Config,
    sources: Sequence[PlaylistInfo],
    *,
    now: Callable[[], datetime] = utcnow,
    on_progress: Progress | None = None,
    split: SplitSettings | None = None,
    on_song: Callable[[Assignment], None] | None = None,
) -> Plan:
    """on_song：每读到一首歌的标签，回调这首歌的第一遍分类结果（与最终结果同一套规则），
    供进度页统计；最终结果还要按各分类首数收尾（合并小类等）。"""
    split = split or SplitSettings()
    if not sources:
        raise ValueError("至少需要一个来源歌单")
    if on_progress:
        on_progress("tracks", 0, len(sources))
    songs = collect_songs(backend, sources)
    if on_progress:
        on_progress("tracks", len(sources), len(sources))
    repo.upsert_songs(conn, songs, now())
    days = config.storage.tag_cache_days
    nodes = load_taxonomy(backend, conn, now(), days)
    taxonomy = Taxonomy(nodes)
    # 分组里写错的曲风在取标签（可能要很久）之前就报出来
    resolve_groups(split, taxonomy, config.classify.other_name)
    on_tag = None
    if on_song is not None:
        first_pass = FirstPass(taxonomy, config.classify, split)
        where = {s.key: (i, s) for i, s in enumerate(songs)}

        def on_tag(key: SongKey, t: SongTags | None) -> None:
            i, s = where[key]
            on_song(first_pass.classify(s, t, i))

    tags = fetch_tags(backend, conn, songs, now, days, on_progress, on_tag)
    assignments = classify_songs(songs, tags, taxonomy, config.classify, split=split)
    created = now()
    plan = Plan(
        id=plan_store.new_plan_id(created),
        created_at=repo.iso(created),
        status=PlanStatus.DRAFT,
        platform=backend.platform,
        sources=tuple(Source(s.id, s.name) for s in sources),
        taxonomy_version=taxonomy.version,
        settings=_settings(config, split),
        snapshot=_snapshot(songs, tags, nodes),
        assignments=tuple(assignments),
    )
    plan_store.save_plan(conn, plan)
    return plan


def _snapshot_inputs(plan: Plan) -> tuple[list[Song], dict[SongKey, SongTags], Taxonomy]:
    snap = plan.snapshot
    nodes = [StyleNode(a, b, c, d) for a, b, c, d in snap["style_tree"]]
    songs = [
        Song(SongKey(p, i), "", (), None, None, None, None, CopyrightStatus(c))
        for p, i, c in snap["songs"]
    ]
    tags = {
        s.key: repo.tags_from_json(s.key, json.dumps(snap["tags"][_key(s.key)]))
        for s in songs
        if _key(s.key) in snap["tags"]
    }
    return songs, tags, Taxonomy(nodes)


def song_genres(plan: Plan) -> dict[SongKey, list[dict[str, Any]]]:
    """每首歌的官方曲风标签（快照里的，按平台给的顺序）；网页"曲风标签"列用。

    used：分类时有没有用上。看主要曲风时，被去掉的用途类和"流行"标签没用上（网页显示成
    浅色）；"带标签就算"时每个标签都参与匹配，全算用上。
    """
    _, tags, taxonomy = _snapshot_inputs(plan)
    any_match = plan.settings.get("split", {}).get("match") == "any"
    kept = {
        a.key: set((a.reason.get("official") or {}).get("kept") or []) for a in plan.assignments
    }

    def tag(key: SongKey, g: GenreTag) -> dict[str, Any]:
        if g.tag_id not in taxonomy.nodes:  # 不在本地曲风树里：按名称归类，算用上
            return {"name": g.name, "used": True}
        chain = taxonomy.chain(g.tag_id)
        return {"name": chain[-1].name, "used": any_match or chain[0].name in kept.get(key, set())}

    out: dict[SongKey, list[dict[str, Any]]] = {}
    for k, t in tags.items():
        seen: dict[str, dict[str, Any]] = {}
        for g in t.genres:
            x = tag(k, g)
            seen.setdefault(x["name"], x)
        out[k] = list(seen.values())
    return out


def classify_from_snapshot(plan: Plan) -> list[Assignment]:
    """只用方案里保存的信号快照重新分类；用于验证"同一快照必然得到同样的结果"。"""
    songs, tags, taxonomy = _snapshot_inputs(plan)
    cfg = parse_config({"classify": plan.settings["classify"]}).classify
    split = SplitSettings.from_dict(plan.settings.get("split", {}))
    return classify_songs(songs, tags, taxonomy, cfg, split=split)


class ReclassifyError(RuntimeError):
    pass


class ConfirmResetError(RuntimeError):
    """方案已批准，调用方没有确认"修改会使批准作废"。"""


class MoveError(ValueError):
    """ "移动到"的参数不对：歌不在方案里、下架的歌、空的分类名。"""


@dataclass(frozen=True)
class ReclassifyResult:
    plan: Plan
    changed: bool  # 方案内容（会对账号做什么）是否变化
    approval_reset: bool  # 原来已批准，因内容变化回到草稿、需要重新批准
    kept_manual: int  # 保留下来的人工调整


def _load_editable(
    conn: sqlite3.Connection, plan_id: str, expected_hash: str | None, confirm_reset: bool
) -> Plan:
    """保存时 replace_assignments 还会在写事务里核对内容和状态都没变，堵住两次读取之间的空隙。"""
    plan = plan_store.load_plan(conn, plan_id)
    if plan.status not in (PlanStatus.DRAFT, PlanStatus.APPROVED):
        raise ReclassifyError(f"方案状态为 {plan.status.value}，不能再修改；请重新 analyze")
    if expected_hash is not None and plan.content_hash != expected_hash:
        raise plan_store.PlanStateError("方案已在别处被修改，请刷新后再试")
    if plan.status == PlanStatus.APPROVED and not confirm_reset:
        raise ConfirmResetError("方案已批准，修改会使批准作废")
    return plan


def _save_edit(conn: sqlite3.Connection, plan: Plan, candidate: Plan) -> ReclassifyResult:
    """内容变化时方案回到草稿、批准作废；只改设置、内容不变时批准保留。"""
    changed = candidate.content_hash != plan.content_hash
    reset = changed and plan.status == PlanStatus.APPROVED
    if reset:
        candidate = replace(
            candidate, status=PlanStatus.DRAFT, approved_hash=None, approved_at=None
        )
    plan_store.replace_assignments(conn, candidate, plan.content_hash, plan.status)
    kept = sum(a.manual for a in candidate.assignments)
    return ReclassifyResult(plan_store.load_plan(conn, plan.id), changed, reset, kept)


def simulate(plan: Plan, split: SplitSettings, *, min_size: int | None = None) -> Plan:
    """按新的拆分方式算出重新分类后的方案，不保存（拆分方式面板预估首数用）。

    只用方案里保存的信号快照，不访问平台；人工调整过的歌保留原分类（最小数量的计数
    仍按它们的自动分类算，影响很小）。
    """
    classify = dict(plan.settings["classify"])
    if min_size is not None:
        classify["min_size"] = min_size
    cfg = parse_config({"classify": classify}).classify  # 顺带校验 min_size
    songs, tags, taxonomy = _snapshot_inputs(plan)
    positions = {a.key: a.position for a in plan.assignments}
    auto = classify_songs(songs, tags, taxonomy, cfg, positions, split)
    manual = {a.key: a for a in plan.assignments if a.manual}
    assignments = tuple(manual.get(a.key, a) for a in auto)
    settings = {**plan.settings, "split": split.to_dict(), "classify": classify}
    return replace(plan, assignments=assignments, settings=settings)


def reclassify(
    conn: sqlite3.Connection,
    plan_id: str,
    split: SplitSettings,
    *,
    min_size: int | None = None,
    expected_hash: str | None = None,
    confirm_reset: bool = True,
) -> ReclassifyResult:
    """按新的拆分方式重新分类并保存（网页上切换拆法时用），规则见 simulate。

    已执行、执行中、已撤销的方案不能重新分类。传了 expected_hash 时，方案内容必须
    还是调用方看到的那一份；confirm_reset 为 False 时，已批准的方案不改动（网页先提示
    "会使批准作废"，用户同意后再带 True 重试）。
    """
    plan = _load_editable(conn, plan_id, expected_hash, confirm_reset)
    return _save_edit(conn, plan, simulate(plan, split, min_size=min_size))


def _selected(plan: Plan, keys: Sequence[SongKey]) -> set[SongKey]:
    wanted = set(keys)
    if not wanted:
        raise MoveError("没有选中任何歌")
    missing = wanted - {a.key for a in plan.assignments}
    if missing:
        raise MoveError(f"有 {len(missing)} 首歌不在这份方案里")
    return wanted


def move_songs(
    conn: sqlite3.Connection,
    plan_id: str,
    keys: Sequence[SongKey],
    category: str | None,
    *,
    expected_hash: str | None = None,
    confirm_reset: bool = True,
) -> ReclassifyResult:
    """人工"移动到"：把歌放进某个分类（已有或新建），category 为 None 时标为"不写入"。

    移动过的歌标记为人工调整，之后重新分类也不会被覆盖。下架或无版权的歌加不进歌单，
    不能移动。expected_hash 与 confirm_reset 同 reclassify。
    """
    plan = _load_editable(conn, plan_id, expected_hash, confirm_reset)
    if category is not None:
        category = category.strip()
        if not category:
            raise MoveError("分类名不能为空")
    by_key = {a.key: a for a in plan.assignments}
    wanted = _selected(plan, keys)
    if any(by_key[k].status == "unavailable" for k in wanted):
        raise MoveError("下架或无版权的歌无法加入歌单，不能移动")
    status: Status = "assigned" if category is not None else "ungrouped"
    assignments = tuple(
        replace(a, status=status, category=category, manual=True) if a.key in wanted else a
        for a in plan.assignments
    )
    return _save_edit(conn, plan, replace(plan, assignments=assignments))


def reset_songs(
    conn: sqlite3.Connection,
    plan_id: str,
    keys: Sequence[SongKey],
    *,
    expected_hash: str | None = None,
    confirm_reset: bool = True,
) -> ReclassifyResult:
    """撤回人工调整：这些歌按当前拆分方式重新自动分类（网页"没分好的歌"里再点一次已选项）。"""
    plan = _load_editable(conn, plan_id, expected_hash, confirm_reset)
    wanted = _selected(plan, keys)
    cleared = replace(
        plan,
        assignments=tuple(
            replace(a, manual=False) if a.key in wanted else a for a in plan.assignments
        ),
    )
    split = SplitSettings.from_dict(plan.settings["split"])
    return _save_edit(conn, plan, simulate(cleared, split))


NAME_MAX = 40  # 网易云的实际上限待实测，先按 40 字


def default_name(plan: Plan, category: str) -> str:
    template = str(plan.settings["name_template"])
    return template.format(category=category, source=source_label(plan.sources))


def rename_playlist(
    conn: sqlite3.Connection,
    plan_id: str,
    category: str,
    name: str | None,
    *,
    expected_hash: str | None = None,
    confirm_reset: bool = True,
) -> ReclassifyResult:
    """建歌单前改某个歌单的名字；空名字或与默认名相同时恢复默认名。

    名字计入内容哈希，所以改名会使批准作废（expected_hash 与 confirm_reset 同 reclassify）。
    """
    plan = _load_editable(conn, plan_id, expected_hash, confirm_reset)
    current = {p.category: p.name for p in plan.playlists()}
    if category not in current:
        raise MoveError("没有这个歌单")
    name = (name or "").strip()
    if len(name) > NAME_MAX:
        raise MoveError(f"歌单名最多 {NAME_MAX} 个字")
    names = dict(plan.settings.get("names") or {})
    if not name or name == default_name(plan, category):
        names.pop(category, None)
        name = default_name(plan, category)
    else:
        names[category] = name
    if any(n == name for c, n in current.items() if c != category):
        raise MoveError("已经有一个同名歌单了")
    settings = {k: v for k, v in plan.settings.items() if k != "names"}
    if names:  # 没有改名时不留这个键，旧方案的设置保持原样
        settings["names"] = names
    return _save_edit(conn, plan, replace(plan, settings=settings))


def genre_counts(plan: Plan) -> dict[str, list[dict[str, Any]]]:
    """方案里出现过的一级曲风和"一级-二级"路径，各有多少首歌带这个标签（含下级）。

    供"只分出 X"挑选 X；数量是"含该标签"的口径，与最终分类数不同。label 是按它分出的
    分类名（曲风名本身可能含 "-"，不能从路径反拆）。
    """
    _, tags, taxonomy = _snapshot_inputs(plan)
    level1: Counter[str] = Counter()
    level2: Counter[str] = Counter()
    labels: dict[str, str] = {}
    for t in tags.values():
        l1: set[str] = set()
        l2: set[str] = set()
        for g in t.genres:
            if g.tag_id not in taxonomy.nodes:
                continue
            chain = taxonomy.chain(g.tag_id)
            l1.add(chain[0].name)
            labels[chain[0].name] = chain[0].name
            if len(chain) > 1:
                path = taxonomy.path(chain[1].tag_id)
                l2.add(path)
                labels[path] = chain[1].name
        level1.update(l1)
        level2.update(l2)

    def rows(c: Counter[str]) -> list[dict[str, Any]]:
        ordered = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))
        return [{"name": k, "label": labels[k], "count": v} for k, v in ordered]

    return {"level1": rows(level1), "level2": rows(level2)}
