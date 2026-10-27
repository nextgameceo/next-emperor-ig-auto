import copy
import io
import json
import urllib.error
from datetime import datetime

import pytest
from PIL import Image

from ig_bot import analyzer, generator, renderer, validator
from ig_bot.config import JST, load_config
from ig_bot.ig_client import IGClient, IGError
from ig_bot.pipeline import check_token, in_post_window, run_collect, run_prepare, run_publish
from ig_bot.state import StateStore
from tests.fakes import GOOD_POST, REVIEW_OK, FakeClaude, FakeIG

CFG = load_config()
THU_21 = datetime(2026, 10, 29, 21, 0, tzinfo=JST)   # 木曜
WED_02 = datetime(2026, 10, 28, 2, 0, tzinfo=JST)    # 水曜


def post(**kw):
    p = copy.deepcopy(GOOD_POST)
    p.update(kw)
    return p


# ---------------------------------------------------------------- validator
def test_good_post_passes():
    assert validator.validate(post(), [], CFG) == []


@pytest.mark.parametrize("field,value,needle", [
    ("caption", "必ず転職できます。プロフィールのリンクから", "禁止表現"),
    ("caption", "詳しくは next-emperor.com へ", "URL"),
    ("caption", "提携企業は30社あります", "禁止表現"),
    ("caption", "気軽にDMください", "禁止表現"),
    ("caption", "本文 #転職", "ハッシュタグ"),
    ("hashtags", ["#夜職から昼職", "#稼げる仕事", "#転職"], "許可リスト外"),
    ("hashtags", ["#転職"], "ハッシュタグは"),
    ("title", "とても長いタイトルがここに入って二十二文字を超えてしまう例です", "タイトルが長すぎ"),
])
def test_validator_rejects(field, value, needle):
    issues = validator.validate(post(**{field: value}), [], CFG)
    assert any(needle in i for i in issues), issues


def test_slide_count_and_similarity():
    assert any("スライド" in i for i in validator.validate(post(slides=GOOD_POST["slides"][:2]), [], CFG))
    hist = [validator.history_key(GOOD_POST)]
    assert any("過去" in i for i in validator.validate(post(), hist, CFG))


def test_caption_composition():
    cap = validator.compose_caption(post())
    assert cap.endswith("#夜職から昼職 #転職 #キャリアチェンジ #NEXTEMPEROR")
    assert "\n\n#" in cap


# ---------------------------------------------------------------- generator
def test_choose_audience_ratio():
    assert generator.choose_audience([], CFG) == "seeker"
    hist = [{"audience": "seeker"}, {"audience": "seeker"}]
    assert generator.choose_audience(hist, CFG) == "company"
    hist = [{"audience": "company"}, {"audience": "seeker"}, {"audience": "seeker"}]
    assert generator.choose_audience(hist, CFG) == "seeker"


def test_choose_theme_avoids_recent_and_is_deterministic():
    hist = [{"audience": "seeker", "theme": t} for t in ("strengths", "job_types", "life_rhythm", "job_check")]
    t1 = generator.choose_theme(CFG, "seeker", hist, None, "2026-10-29")
    assert t1 not in {"strengths", "job_types", "life_rhythm", "job_check"}
    assert t1 == generator.choose_theme(CFG, "seeker", hist, None, "2026-10-29")


def test_generate_ok_after_retry():
    bad = post(caption="必ず転職できます")
    claude = FakeClaude([bad, GOOD_POST, REVIEW_OK])
    r = generator.generate_post(claude, CFG, [], None, "2026-10-29")
    assert r["status"] == "ok"
    assert len(r["attempts"]) == 2
    assert "必ず" in claude.calls[1]["prompt"]  # 不合格理由が次の試行に渡る


def test_generate_fails_without_fallback():
    claude = FakeClaude(["not json", post(caption="無料です"), GOOD_POST, {"ok": False, "issues": ["事実にない"]}])
    r = generator.generate_post(claude, CFG, [], None, "2026-10-29")
    assert r["status"] == "failed"
    assert len(r["attempts"]) == 3


# ---------------------------------------------------------------- renderer
def test_render_sizes(tmp_path):
    p = post(audience="company", theme="potential")
    paths = renderer.render_post(p, CFG, tmp_path, "seed")
    assert len(paths) == len(p["slides"]) + 2
    for path in paths:
        im = Image.open(path)
        assert im.size == (1080, 1350) and im.format == "JPEG"


def test_wrap_kinsoku():
    f = renderer.font("sans", 40)
    lines = renderer.wrap("あいうえおかきくけこさしすせそたちつてと、なにぬねの。", f, 40 * 20)
    assert not any(l.startswith(("、", "。")) for l in lines)


def test_text_fits_detects_overflow():
    long_body = "とても長い本文です。" * 30
    assert renderer.text_fits(post(slides=[{"heading": "見出し", "body": long_body}]))


# ---------------------------------------------------------------- pipeline
def prepared(tmp_path, now=THU_21):
    store = StateStore(tmp_path / "state")
    claude = FakeClaude([GOOD_POST, REVIEW_OK])
    r = run_prepare(store=store, config=CFG, claude=claude, now=now, scheduled=False, force=False)
    return store, r


def test_prepare_and_reuse(tmp_path):
    store, r = prepared(tmp_path)
    assert r["status"] == "ok"
    rec = store.get_post("2026-10-29")
    assert rec["status"] == "draft" and len(rec["images"]) == 6
    assert (store.root / rec["images"][0]).exists()
    r2 = run_prepare(store=store, config=CFG, claude=FakeClaude([]), now=THU_21, scheduled=False, force=False)
    assert r2["status"] == "exists"


def test_prepare_scheduled_window():
    ok, _ = in_post_window(THU_21, CFG)
    assert ok
    ok, why = in_post_window(WED_02, CFG)
    assert not ok and "投稿日" in why


def test_prepare_failed_records(tmp_path):
    store = StateStore(tmp_path / "s")
    claude = FakeClaude([RuntimeError("x")] * 3)
    r = run_prepare(store=store, config=CFG, claude=claude, now=THU_21, scheduled=False, force=False)
    assert r["status"] == "failed" and store.get_post("2026-10-29")["status"] == "failed"


def test_publish_dry_run_does_not_call_ig(tmp_path):
    store, _ = prepared(tmp_path)
    r = run_publish(mode="dry_run", store=store, config=CFG, ig=None, now=THU_21, sha="abc", scheduled=False)
    assert r["status"] == "dry_run"
    assert r["urls"][0] == "https://raw.githubusercontent.com/nextgameceo/next-emperor-ig-auto/abc/images/2026-10-29/slide01.jpg"
    assert store.get_post("2026-10-29")["status"] == "draft"


def test_publish_live(tmp_path):
    store, _ = prepared(tmp_path)
    ig = FakeIG()
    r = run_publish(mode="live", store=store, config=CFG, ig=ig, now=THU_21, sha="abc", scheduled=True,
                    sleep=lambda s: None, reachable=lambda u: True)
    assert r["status"] == "published"
    assert len(ig.items) == 6 and ig.published == ["carousel1"]
    assert ig.carousels[0][1].endswith("#NEXTEMPEROR")
    rec = store.get_post("2026-10-29")
    assert rec["status"] == "published" and rec["permalink"]
    # 2回目（予備スケジュール）は何もしない
    r2 = run_publish(mode="live", store=store, config=CFG, ig=ig, now=THU_21, sha="abc", scheduled=True,
                     sleep=lambda s: None, reachable=lambda u: True)
    assert r2["status"] == "skipped" and ig.published == ["carousel1"]


def test_publish_duplicate_guard(tmp_path):
    store, _ = prepared(tmp_path)
    cap = store.get_post("2026-10-29")["caption"]
    ig = FakeIG(recent=[{"id": "m9", "caption": cap, "timestamp": "2026-10-29T12:00:00+0000"}])
    r = run_publish(mode="live", store=store, config=CFG, ig=ig, now=THU_21, sha="abc", scheduled=False,
                    sleep=lambda s: None, reachable=lambda u: True)
    assert r["status"] == "skipped" and ig.items == []
    assert store.get_post("2026-10-29")["media_id"] == "m9"


def test_publish_private_repo_error(tmp_path):
    store, _ = prepared(tmp_path)
    with pytest.raises(IGError, match="公開"):
        run_publish(mode="live", store=store, config=CFG, ig=FakeIG(), now=THU_21, sha="abc",
                    scheduled=False, sleep=lambda s: None, reachable=lambda u: False)


def test_publish_outside_window_skips(tmp_path):
    store, _ = prepared(tmp_path, now=datetime(2026, 10, 29, 2, 0, tzinfo=JST))
    ig = FakeIG()
    r = run_publish(mode="live", store=store, config=CFG, ig=ig,
                    now=datetime(2026, 10, 29, 2, 0, tzinfo=JST), sha="abc", scheduled=True,
                    sleep=lambda s: None, reachable=lambda u: True)
    assert r["status"] == "skipped" and ig.items == []


# ---------------------------------------------------------------- collect / analyze / token
def _published_store(tmp_path, n_seeker, n_company):
    store = StateStore(tmp_path / "st")
    data = {"posts": {}}
    i = 0
    for aud, n in (("seeker", n_seeker), ("company", n_company)):
        for _ in range(n):
            i += 1
            d = f"2026-10-{i:02d}"
            data["posts"][d] = {"date": d, "status": "published", "audience": aud, "theme": "strengths" if aud == "seeker" else "potential",
                                "title": f"t{i}", "media_id": f"m{i}",
                                "metrics": {"reach": 100 + i, "saved": i, "shares": 1, "likes": 5, "comments": 0}}
    store.save_posts(data)
    return store


def test_collect_updates(tmp_path):
    store = _published_store(tmp_path, 1, 0)
    ig = FakeIG(insights={"m1": {"reach": 500, "saved": 9}})
    r = run_collect(store=store, config=CFG, ig=ig, now=datetime(2026, 10, 20, 3, 30, tzinfo=JST))
    assert r["updated"] == 1 and store.get_post("2026-10-01")["metrics"]["reach"] == 500


def test_analyze_insufficient_skips_claude(tmp_path):
    store = _published_store(tmp_path, 2, 1)
    ins, report = analyzer.run_analysis(posts=store.posts_sorted(), config=CFG, claude=FakeClaude([]),
                                        now=datetime(2026, 10, 28, 9, 30, tzinfo=JST))
    assert ins["data_sufficiency"] == "insufficient" and "自動反映" in report


def test_analyze_sufficient(tmp_path):
    store = _published_store(tmp_path, 5, 4)
    resp = {"summary": "保存が多い", "findings": [],
            "recommendations": {"seeker": {"prefer_themes": ["strengths", "bogus"], "avoid_themes": [], "style_notes": ["短く"]},
                                "company": {}}}
    ins, _ = analyzer.run_analysis(posts=store.posts_sorted(), config=CFG, claude=FakeClaude([resp]),
                                   now=datetime(2026, 10, 28, 9, 30, tzinfo=JST))
    assert ins["data_sufficiency"] == "sufficient"
    assert ins["recommendations"]["seeker"]["prefer_themes"] == ["strengths"]


def test_token_warning(tmp_path):
    store = StateStore(tmp_path / "t")
    soon = int(datetime(2026, 11, 5, tzinfo=JST).timestamp())
    res = check_token(FakeIG(debug={"is_valid": True, "expires_at": 0, "data_access_expires_at": soon}),
                      store, CFG, WED_02)
    assert any("データアクセス" in w for w in res["warnings"])
    res = check_token(FakeIG(), store, CFG, WED_02)
    assert res["warnings"] == []


# ---------------------------------------------------------------- ig_client
def test_ig_error_hides_token():
    body = json.dumps({"error": {"message": "Invalid OAuth access token", "code": 190}}).encode()

    def transport(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad", {}, io.BytesIO(body))

    ig = IGClient("SECRET_TOKEN_123", "1784", transport=transport, sleep=lambda s: None)
    with pytest.raises(IGError) as e:
        ig.recent_media()
    assert e.value.token_invalid and "SECRET_TOKEN_123" not in str(e.value)


def test_ig_publish_not_retried():
    calls = []
    body = json.dumps({"error": {"message": "temp", "code": 2, "is_transient": True}}).encode()

    def transport(req, timeout):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 500, "x", {}, io.BytesIO(body))

    ig = IGClient("T", "1784", transport=transport, sleep=lambda s: None)
    with pytest.raises(IGError):
        ig.publish("c1")
    assert len(calls) == 1
    with pytest.raises(IGError):
        ig.container_status("c1")
    assert len(calls) == 4  # 読み取りは3回まで再試行


def test_ig_insights_parse():
    def transport(req, timeout):
        return {"data": [{"name": "reach", "values": [{"value": 321}]}, {"name": "saved", "values": [{"value": 7}]}]}

    ig = IGClient("T", "1784", transport=transport)
    assert ig.media_insights("m1", ["reach", "saved"]) == {"reach": 321, "saved": 7}
