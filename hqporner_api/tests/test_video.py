import pytest
from ..api import Client, DownloadConfigRAW


@pytest.fixture
def client() -> Client:
    return Client()

@pytest.mark.asyncio
async def test_video(client):
    video = await client.get_video("https://hqporner.com/hdporn/126829-this_is_our_story.html")
    assert isinstance(video.title, str) and len(video.title) > 1
    assert isinstance(video.video_qualities, list) and len(video.video_qualities) > 1
    assert isinstance(video.tags, list) and len(video.tags) > 1
    assert isinstance(video.length, str) and len(video.length) > 1
    assert isinstance(video.pornstars, list) and len(video.pornstars) > 1
    assert isinstance(video.cdn_url, str) and len(video.cdn_url) > 1
    assert isinstance(video.publish_date, str) and len(video.publish_date) > 1

    config_low = DownloadConfigRAW(quality="best")
    assert await video.download(config_low) is True
