import pytest
from ..api import Client, DownloadConfigRAW, Video


@pytest.fixture
def client() -> Client:
    return Client()


def test_video_extract_html():
    html_snippet = """<div class="9u important(collapse) mobile-logo-margin">

<div class="content content-left">

<div class="box page-content">

<section>

<div class="12u">

<a href="#" id="linkAltPlayer" onclick="altPlayer();this.innerHTML = 'Back to native';return false;" class="button click-trigger" style="font-size: 1em;border-bottom-left-radius: 0px;border-bottom-right-radius: 0px;">Go to alternative player</a>
<a href="https://go.mayzaent.com/smartpop/27ac941e7a902ae4da4cedbdd22f13c7824f9a006b7f0243731ab1d56dca97d6?userId=d102808c53ebaa9305f8811927f765df14185d9d3a72896b3594657af78baeaf" rel="sponsored" target="_blank" class="button hide_ad_marker" style="font-size: 1em;float: right;background: transparent;color: #ff8397;padding-right: 0px;"><span style="color: #6b7770;">HQPORNER OFFER:</span> Stripchat - Free Live Cam Sex</a>
<script async="" src="https://mc.webvisor.org/metrika/tag_ww.js"></script><script type="text/javascript">
function altPlayer() {
$.ajax({
url: '/blocks/altplayer.php?i=//mydaddy.cc/video/67aa773382d70db3cb/',
cache: false,
beforeSend: function() { $('#playerWrapper').html('<h3 style="padding: 20px 30px;">One moment ...</h3><img src="/images/loader.gif" width="64" id="loader">'); },
success: function(html) {
$('#playerWrapper').innerHTML = '';
$('#playerWrapper').html(html);
}
});
$('#linkAltPlayer').attr('onclick', 'nativePlayer();this.innerHTML = \\'Go to alternative player\\';return false;');
return false;
}

function nativePlayer() {
$.ajax({
url: '/blocks/nativeplayer.php?i=//mydaddy.cc/video/67aa773382d70db3cb/',
cache: false,
beforeSend: function() { $('#playerWrapper').html('<h3 style="padding: 20px 30px;">One moment ...</h3><img src="/images/loader.gif" width="64" id="loader">'); },
success: function(html) {
$('#playerWrapper').innerHTML = '';
$('#playerWrapper').html(html);
}
});
$('#linkAltPlayer').attr('onclick', 'altPlayer();this.innerHTML = \\'Back to native\\';return false;');
return false;
}
</script>

<div class="videoWrapper" id="playerWrapper" style="background:#000;">

<h3 style="padding: 20px 30px;">Loading may take some time ...</h3>

<iframe width="560" height="350" src="//mydaddy.cc/video/67aa773382d70db3cb/" frameborder="0" allowfullscreen=""></iframe>

</div>

<header>

<h1 class="main-h1" style="line-height: 1em;">
screaming for harder pushes!</h1>

<!--<p><i class="icon fa-info"></i> Info <i class="icon fa-users"></i> Pornstars <i class="icon fa-comment"></i> Comments <i class="icon fa-share"></i> Share</p>-->
<ul class="meta">
<li class="icon fa-calendar">today</li>


<li class="icon fa-clock-o">42m 8s</li>

<li class="icon fa-star-o">featuring <a href="/actress/emy-rouse" title="See all Emy Rouse videos" class="click-trigger">Emy Rouse</a></li>

</ul>
</header>

</div>

</section>

<section>
	<h3>This video belongs to the following categories</h3>
	<p>
		<a href="/category/1080p-porn" class="tag-link click-trigger">1080p</a><a href="/category/60fps-porn" class="tag-link click-trigger">60fps</a><a href="/category/anal-sex-hd" class="tag-link click-trigger">anal</a><a href="/category/big-dick" class="tag-link click-trigger">big dick</a><a href="/category/blowjob" class="tag-link click-trigger">blowjob</a><a href="/category/creampie" class="tag-link click-trigger">creampie</a><a href="/category/interracial" class="tag-link click-trigger">interracial</a><a href="/category/latina" class="tag-link click-trigger">latina</a><a href="/category/moaning" class="tag-link click-trigger">moaning</a><a href="/category/shaved-pussy" class="tag-link click-trigger">shaved pussy</a><a href="/category/small-tits" class="tag-link click-trigger">small tits</a>	</p>
</section>

</div>

</div>
</div>"""

    data = Video._extract_html(False, html_snippet)
    assert data["title"] == "screaming for harder pushes!"
    assert data["cdn_url"] == "mydaddy.cc/video/67aa773382d70db3cb/"
    assert data["pornstars"] == ["Emy Rouse"]
    assert data["length"] == "42m 8s"
    assert data["publish_date"] == "today"
    assert data["tags"] == [
        "1080p",
        "60fps",
        "anal",
        "big dick",
        "blowjob",
        "creampie",
        "interracial",
        "latina",
        "moaning",
        "shaved pussy",
        "small tits",
    ]


@pytest.mark.asyncio
async def test_video(client):
    video = await client.get_video("https://hqporner.com/hdporn/126829-this_is_our_story.html")
    assert isinstance(video.title, str) and len(video.title) > 1
    assert isinstance(video.video_qualities, list) and len(video.video_qualities) > 1
    assert isinstance(video.tags, list) and len(video.tags) > 1
    assert isinstance(video.length, str) and len(video.length) > 1
    assert isinstance(video.pornstars, list) and len(video.pornstars) >= 1
    assert isinstance(video.cdn_url, str) and len(video.cdn_url) > 1
    assert isinstance(video.publish_date, str) and len(video.publish_date) > 1

    config_low = DownloadConfigRAW(quality="best")
    assert await video.download(config_low) is True
