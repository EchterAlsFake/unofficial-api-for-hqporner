import pytest
from ..api import Client


@pytest.fixture
def client() -> Client:
    return Client()


@pytest.mark.asyncio
async def test_actress(client):

    idx = 0
    async for result in client.get_videos_by_actress(name="anissa kate"):
        idx += 1

        assert isinstance(result.video.title, str) and len(result.video.title) > 1
        if idx >= 3:
            break


@pytest.mark.asyncio
async def test_category(client):
    idx = 0
    async for result in client.get_videos_by_category("milf"):
        idx += 1
        assert isinstance(result.video.title, str) and len(result.video.title) > 1

        if idx >= 3:
            break


@pytest.mark.asyncio
async def test_search(client):
    idx = 0
    async for result in client.search_videos("nancy a"):
        idx += 1
        assert isinstance(result.video.title, str) and len(result.video.title) > 1

        if idx >= 3:
            break

@pytest.mark.asyncio
@pytest.mark.parametrize("sort_option", ["week", "month", "all-time"])
async def test_top_porn(client, sort_option):
    idx = 0
    async for result in client.get_top_porn(sort_by=sort_option):
        idx += 1
        assert isinstance(result.video.title, str) and len(result.video.title) > 1

        if idx >= 3:
            break

@pytest.mark.asyncio
async def test_all_categories(client):
    assert isinstance(await client.get_all_categories(), list)

@pytest.mark.asyncio
async def test_random(client):
    for i in range(3):
        random_video = await client.get_random_video()
        assert isinstance(random_video.title, str) and len(random_video.title) > 1
