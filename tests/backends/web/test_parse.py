"""用录制并脱敏的响应测试解析函数。"""

from tests.conftest import load_fixture
from tunetidy.backends.web import parse
from tunetidy.models import CopyrightStatus, SongKey


def test_parse_user() -> None:
    user = parse.parse_user(load_fixture("netease/account_get.json"))
    assert user is not None
    assert user.platform == "netease"
    assert user.id == "10000001"


def test_parse_user_logged_out() -> None:
    assert parse.parse_user({"code": 200, "account": None, "profile": None}) is None


def test_parse_playlists_counts_cloud_tracks_and_ownership() -> None:
    pls = parse.parse_playlists(load_fixture("netease/user_playlist.json"))
    assert [p.owned for p in pls] == [True, True, False]
    # "喜欢的音乐"：trackCount 1031 + cloudTrackCount 1，与 App 显示一致
    assert pls[0].track_count == 1032
    assert pls[1].track_count == 31


def test_parse_playlist_detail_snapshot() -> None:
    snap = parse.parse_playlist_detail(load_fixture("netease/playlist_detail.json"))
    assert snap.name == "飙升榜"
    assert snap.private is False
    assert snap.subscribed_count > 0
    assert len(snap.track_ids) == 20
    assert snap.track_ids[:3] == ("2078101758", "3451531277", "486218073")


def test_parse_song_detail_normal_and_vip() -> None:
    songs = parse.parse_song_detail(load_fixture("netease/song_detail.json"))
    assert len(songs) == 20
    assert songs["211520"].copyright == CopyrightStatus.VIP  # privilege fee=1
    assert songs["2078101758"].copyright == CopyrightStatus.NORMAL  # fee=8
    assert songs["3451609995"].copyright == CopyrightStatus.NORMAL  # fee=0
    s = songs["2078101758"]
    assert s.key == SongKey("netease", "2078101758")
    assert s.artists and s.artists[0].name and s.artists[0].id
    assert s.album_id and s.album_name
    assert s.duration_ms and s.duration_ms > 0
    assert s.publish_year is None  # publishTime=0


def test_parse_song_detail_unavailable_and_cloud() -> None:
    body = load_fixture("netease/song_detail_edge_cases.json")
    songs = list(parse.parse_song_detail(body).values())
    assert songs[0].copyright == CopyrightStatus.UNAVAILABLE  # privilege st=-200
    cloud = songs[1]
    assert cloud.copyright == CopyrightStatus.CLOUD
    assert cloud.album_id is None  # al.id = 0
    assert cloud.artists[0].id is None
    assert cloud.artists[0].name  # 退回 pc.ar（脱敏后为 "redacted"）


def test_copyright_priority_cloud_before_unavailable() -> None:
    assert parse.copyright_status({"t": 1}, {"st": -200, "fee": 1}) == CopyrightStatus.CLOUD
    assert parse.copyright_status({}, {"st": -200, "fee": 1}) == CopyrightStatus.UNAVAILABLE
    assert parse.copyright_status({"fee": 1}, None) == CopyrightStatus.VIP
    assert parse.copyright_status({"st": -1}, None) == CopyrightStatus.UNAVAILABLE


def test_parse_song_wiki_genres_language_bpm() -> None:
    tags = parse.parse_song_wiki("2078101758", load_fixture("netease/song_wiki_0.json"))
    assert [g.name for g in tags.genres] == ["流行"]
    assert all(g.tag_id.isdigit() for g in tags.genres)
    assert tags.biz_tags == ("悲伤",)
    assert tags.language == "国语、英语"
    assert tags.bpm == 65


def test_parse_song_wiki_without_bpm() -> None:
    tags = parse.parse_song_wiki("3451531277", load_fixture("netease/song_wiki_1.json"))
    assert [g.name for g in tags.genres] == ["嘻哈说唱"]
    assert tags.bpm is None
    assert tags.biz_tags == ("悲伤", "孤独")


def test_parse_song_wiki_missing_basic_block() -> None:
    tags = parse.parse_song_wiki("1", {"code": 200, "data": {"blocks": []}})
    assert tags.genres == () and tags.language is None and tags.bpm is None


def test_wiki_basic_block_is_small_part() -> None:
    body = load_fixture("netease/song_wiki_0.json")
    block = parse.wiki_basic_block(body)
    assert block is not None and block["code"] == parse.WIKI_BASIC_BLOCK


def test_parse_style_tree_matches_m0() -> None:
    nodes = parse.parse_style_tree(load_fixture("netease/style_list.json"))
    assert len(nodes) == 648
    by_level = {lv: sum(n.level == lv for n in nodes) for lv in (1, 2, 3)}
    assert by_level == {1: 28, 2: 278, 3: 342}
    ids = {n.tag_id: n for n in nodes}
    pop = next(n for n in nodes if n.name == "流行" and n.level == 1)
    assert pop.parent_id is None
    assert any(n.parent_id == pop.tag_id and n.name == "华语流行" for n in nodes)
    assert all(n.parent_id is None or n.parent_id in ids for n in nodes)
