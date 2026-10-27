import pytest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from hqporner_api import api
from hqporner_api.modules.consts import extractor_html, extractor_random_video


@pytest.mark.parametrize(
    "method,arg",
    [
        ("get_videos_by_actress", "test"),
        ("get_videos_by_category", "milf"),
        ("get_top_porn", "week"),
    ],
)
def test_path_pagination_has_distinct_pages(method, arg):
    client = object.__new__(api.Client)
    client.core = object()
    with patch.object(api.Client, "_video_stream") as stream:
        getattr(client, method)(arg, pages=3)
    urls = stream.call_args.args[0]
    assert len(set(urls)) == 3
    assert urls[1].endswith("/2")


@pytest.mark.parametrize(
    "method,arg", [("search_videos", "a&b/# 日本"), ("get_top_porn", "all-time")]
)
def test_search_and_top_urls(method, arg):
    client = object.__new__(api.Client)
    client.core = object()
    with patch.object(api.Client, "_video_stream") as stream:
        getattr(client, method)(arg, pages=2)
    urls = stream.call_args.args[0]
    if method == "search_videos":
        assert parse_qs(urlsplit(urls[0]).query)["q"] == [arg]
    else:
        assert "/all-time" not in urls[0]


def test_random_links_resolve_once():
    html = '<a class="image featured non-overlay atfib" href="/hdporn/123-test.html"></a><a class="image featured non-overlay atfib" href="https://hqporner.com/hdporn/456.html"></a>'
    assert extractor_random_video(html) == [
        "https://hqporner.com/hdporn/123-test.html",
        "https://hqporner.com/hdporn/456.html",
    ]


def test_absolute_listing_links():
    html = '<div class="6u"><img src="https://cdn.example/image.jpg"><h3 class="meta-data-title"><a href="https://hqporner.com/hdporn/123.html">Test</a></h3></div>'
    row = extractor_html(html)[0]
    assert row["url"] == "https://hqporner.com/hdporn/123.html"
    assert row["thumbnail"] == "https://cdn.example/image.jpg"


def test_no_results_excludes_random_recommendations():
    html = '<h1>Can\'t<span>find</span> ...</h1><div class="6u"><h3 class="meta-data-title"><a href="/hdporn/123.html">Suggested</a></h3></div>'
    assert extractor_html(html) == []


@pytest.mark.asyncio
async def test_cli_closes_session_and_reports_false_download(capsys, tmp_path):
    from unittest.mock import AsyncMock, MagicMock
    from base_api import BaseCore
    from base_api.modules.config import RuntimeConfig

    core = BaseCore(RuntimeConfig())
    video = MagicMock(title="Example", length="10", url="https://example.com/video")
    video.load_fields = AsyncMock()
    video.download = AsyncMock(return_value=False)
    client = MagicMock()
    client.get_video = AsyncMock(return_value=video)
    with (
        patch.object(api, "BaseCore", return_value=core),
        patch.object(api, "Client", return_value=client),
    ):
        await api.run_main(
            ["--download", "https://example.com/video", "--output", str(tmp_path)]
        )
    assert core.session is None
    output = capsys.readouterr().out
    assert "Download complete:" not in output
    assert "Done ->" not in output
    assert "All downloads completed!" not in output
    assert "Download did not complete" in output


@pytest.mark.asyncio
async def test_categories_exclude_navigation_and_duplicates():
    from unittest.mock import AsyncMock

    client = object.__new__(api.Client)
    client.core = object()
    html = '<a class="click-trigger" href="/">Home</a><h3><a href="/category/milf">milf</a></h3><h3><a href="/category/milf">milf</a></h3>'
    with patch.object(api, "get_html_content", AsyncMock(return_value=(False, html))):
        assert await client.get_all_categories() == ["milf"]


@pytest.mark.parametrize(
    "actress",
    [
        "Anissa Kate",
        "https://hqporner.com/actress/anissa-kate/",
        "https://www.hqporner.com/actress/anissa-kate/?q=1#videos",
        "http://m.hqporner.com/actress/anissa-kate",
    ],
)
def test_actress_url_and_name(actress):
    assert api.Checks.check_actress(actress) == "anissa-kate"


@pytest.mark.parametrize(
    "actress",
    [
        "",
        "https://hqporner.com.example.com/actress/example",
        "https://hqporner.com/actress/",
        "https://other.example/actress/example",
    ],
)
def test_invalid_actress(actress):
    with pytest.raises(api.InvalidActress):
        api.Checks.check_actress(actress)


@pytest.mark.asyncio
async def test_mobile_fallback_keeps_one_mobile_prefix():
    from unittest.mock import AsyncMock

    client = AsyncMock(side_effect=[api.NotFound("gone"), "page"])
    with patch.object(api, "fetch_content", client):
        assert await api.get_html_content(
            object(), "https://www.hqporner.com/hdporn/123.html"
        ) == (True, "page")
    assert client.await_args_list[1].args[1] == "https://m.hqporner.com/hdporn/123.html"


@pytest.mark.asyncio
async def test_mobile_404_does_not_retry_mobile_again():
    from unittest.mock import AsyncMock

    client = AsyncMock(side_effect=api.NotFound("gone"))
    with patch.object(api, "fetch_content", client), pytest.raises(api.NotFound):
        await api.get_html_content(object(), "https://m.hqporner.com/hdporn/123.html")
    assert client.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "anchor",
    [
        "<a href='//cdn.example/720.mp4' style='x'>720</a>",
        '<a style="x" href="https://cdn.example/720.mp4">720</a>',
        '<a href="//cdn.example/720.mp4">720</a>',
    ],
)
async def test_cdn_links_do_not_depend_on_quotes_or_attribute_order(anchor):
    from unittest.mock import AsyncMock

    video = api.Video(url="https://hqporner.com/hdporn/123.html", core=object())
    watch = '<h1>Example</h1><div id="playerWrapper"><iframe src="//mydaddy.cc/video/abc/"></iframe></div>'
    with patch.object(
        api,
        "get_html_content",
        AsyncMock(side_effect=[(False, watch), (False, anchor)]),
    ):
        data = await video._load_html()
    assert data["direct_download_urls"] == ["https://cdn.example/720.mp4"]

@pytest.mark.asyncio
@pytest.mark.parametrize('option', ['--pages','--concurrency'])
async def test_cli_rejects_zero_limits_before_network(option,tmp_path):
    with patch.object(api,'Client') as client,pytest.raises(SystemExit) as error:
        await api.run_main(['--random','--output',str(tmp_path),option,'0'])
    assert error.value.code == 2
    client.assert_not_called()

def test_default_page_builder_and_invalid_limits():
    assert api.build_page_urls('https://hqporner.com/top') == ['https://hqporner.com/top']
    with pytest.raises(ValueError):
        api.build_page_urls('https://hqporner.com/top',pages=0)
    with pytest.raises(ValueError):
        api.build_page_url('https://hqporner.com/top',0,mode=api.Pagination.PATH)

@pytest.mark.asyncio
async def test_cdn_download_links_inside_javascript_html_string():
    from unittest.mock import AsyncMock
    video=api.Video(url='https://hqporner.com/hdporn/123.html',core=object())
    watch='<h1>Example</h1><div id="playerWrapper"><iframe src="//mydaddy.cc/video/abc/"></iframe></div>'
    cdn='''<script>$("#jw").html("You can download it: <a href='//cdn.example/360.mp4' style='color:#ddd'>360p</a>");</script>'''
    with patch.object(api,'get_html_content',AsyncMock(side_effect=[(False,watch),(False,cdn)])):
        data=await video._load_html()
    assert data['direct_download_urls'] == ['https://cdn.example/360.mp4']
