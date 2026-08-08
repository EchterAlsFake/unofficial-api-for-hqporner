from __future__ import annotations
import os
import re
import copy
import asyncio
import logging
import argparse
from rich.console import Console
from rich.table import Table
from enum import Enum
from random import choice
from typing import AsyncGenerator, ClassVar
from dataclasses import dataclass
from curl_cffi import AsyncSession
from selectolax.lexbor import LexborHTMLParser
from base_api.modules.config import IteratorConfig
from base_api import (
    BaseCore,
    BaseMedia,
    DownloadConfigRAW,
    ErrorAction,
    ErrorMode,
    Helper,
    MediaLoadError,
    MediaLoadErrors,
    RetryPolicy,
    ScrapeErrorContext,
    ScrapeResult,
    media_field,
)
from base_api.modules.static_functions import choose_quality_from_list, normalize_quality_value
from base_api.modules.errors import (
    BotProtectionDetected,
    HTTPStatusError,
    InvalidProxy,
    NetworkRequestError,
    RequestRetriesExhausted,
    ResourceGone,
    UnknownError,
)

from hqporner_api.modules.errors import (NotFound, NetworkError, NotAvailable, UnknownNetworkError, BotDetection,
                                        ProxyError, InvalidActress, DownloadFailed)
from hqporner_api.modules.consts import (root_random, root_url, root_url_category, root_url_top, root_brazzers,
                                         root_url_actress, extractor_random_video, extractor_html,
                                         PATTERN_CDN_URL, PATTERN_EXTRACT_CDN_URLS, PATTERN_RESOLUTION,
                                         PATTERN_CHECK_URL_ACTRESS, headers)
from hqporner_api.modules.locals import Category, Sort


logger = logging.getLogger("HQPorner API")
logger.addHandler(logging.NullHandler())

SCRAPE_RETRY_POLICY = RetryPolicy(max_attempts=3)


def make_iterator_config() -> IteratorConfig:
    return IteratorConfig(
        load_specific_sources=("html",),
        item_retry=SCRAPE_RETRY_POLICY,
        page_retry=SCRAPE_RETRY_POLICY,
        page_error_mode=ErrorMode.SKIP,
        item_error_handler=None,
        page_error_handler=None,
    )


def _is_resource_gone(error: BaseException) -> bool:
    if isinstance(error, ResourceGone):
        return True
    if isinstance(error, MediaLoadError):
        return _is_resource_gone(error.original_error)
    if isinstance(error, MediaLoadErrors):
        return any(_is_resource_gone(item) for item in error.errors)
    return False


async def on_error(context: ScrapeErrorContext) -> ErrorAction:
    logger.error(
        "URL: %s, ERROR: %s, Attempt: %s/%s",
        context.url,
        context.error,
        context.attempt,
        context.max_attempts,
    )

    if _is_resource_gone(context.error):
        return ErrorAction.SKIP

    return ErrorAction.RETRY


async def get_html_content(core: BaseCore, url: str, is_second_attempt: bool = False,
                           is_mobile_fix: bool = False) -> tuple[bool, str]:
    try:
        content = await core.fetch_text(url)
        return is_mobile_fix, content

    except HTTPStatusError as e:
        if e.status_code == 404:
            if is_second_attempt:
                raise NotFound(f"Server returned 404 for: {url}") from e

            mobile_url = url.replace("hqporner.com", "m.hqporner.com")
            return await get_html_content(
                url=mobile_url,
                is_second_attempt=True,
                core=core,
                is_mobile_fix=True,
            )
        raise NetworkError(str(e)) from e

    except (NetworkRequestError, RequestRetriesExhausted) as e:
        raise NetworkError(str(e)) from e

    except InvalidProxy as e:
        raise ProxyError(str(e)) from e

    except BotProtectionDetected as e:
        raise BotDetection(str(e)) from e

    except UnknownError as e:
        raise UnknownNetworkError(str(e)) from e



class Checks:
    """
    Does the same as the decorators, but decorators are not good for IDEs because they get confused, so I moved
    them here.
    """

    @classmethod
    def check_actress(cls, actress: str):
        """
        :param: actress: (str) The name or URL of the actress to check for
        :return: (str) The name of the actress if valid, otherwise raises InvalidActress Exception
        """
        if actress.startswith("https://"):
            match = PATTERN_CHECK_URL_ACTRESS.match(actress)
            if match:
                name_extraction = re.compile(r'https://hqporner.com/actress/(.+)')
                name = name_extraction.search(actress).group(1)
                return name

            else:
                raise InvalidActress

        else:
            actress = actress.replace(" ", "-")  # For later url processing (makes sense, trust me)
            return actress
            # I assume that if it's not a URL, the user was smart enough to enter just the name lol


class Pagination(Enum):
    QUERY = "query"
    PATH = "path"


def build_page_url(base_url: str, page: int, *,
                   mode: Pagination,
                   page_param: str = "p") -> str:
    if page <= 1:
        return base_url

    if mode is Pagination.QUERY:
        sep = "&" if "?" in base_url else "?"
        return f"{base_url}{sep}{page_param}={page}"

    elif mode is Pagination.PATH:
        return f"{base_url.rstrip('/')}/{page}"

    raise ValueError("Please report this whatever you did")


def build_page_urls(base: str, pagination: Pagination = Pagination.QUERY, pages: int | None = None,
                    page_param: str = "p", start_page: int = 1) -> list[str]:
    page_urls = []
    base = base.rstrip("/")
    for i, page in enumerate(range(start_page, start_page + pages), start=0):
        page_urls.append(build_page_url(base, page, mode=pagination, page_param=page_param))

    return page_urls




@dataclass(kw_only=True, slots=True)
class Video(BaseMedia):
    url: str
    core: BaseCore
    title: str | None = media_field("html")
    cdn_url: str | None = media_field("html")
    pornstars: list[str] | None = media_field("html")
    length: str | None = media_field("html")
    publish_date: str | None = media_field("html")
    tags: list[str] | None = media_field("html")
    direct_download_urls: list[str] | None = media_field("html")

    # Optional
    thumbnail: str | None = None

    loader_methods: ClassVar[dict[str, str]] = {"html": "_load_html"}

    async def _load_html(self) -> dict[str, object]:
        is_mobile_fix, html_content = await get_html_content(core=self.core, url=self.url)
        data: dict = await asyncio.to_thread(self._extract_html, is_mobile_fix, html_content)
        cdn_url = f"https://{data['cdn_url']}"
        is_mobile_fix, html_content = await get_html_content(core=self.core, url=cdn_url)
        data["direct_download_urls"] = PATTERN_EXTRACT_CDN_URLS.findall(html_content)
        return data


    @staticmethod
    def _extract_html(is_mobile_fix: bool, html_content: str) -> dict[str, str | list[str]]:
        lexbor = LexborHTMLParser(html_content)

        if is_mobile_fix:
            title = lexbor.css_first("h1[style*='font-size:18px']").text(strip=True)
            length = lexbor.css("span.meta_data")[1].text(strip=True)
            publish_date = lexbor.css("span.meta_data")[0].text(strip=True)
            elements = lexbor.css("a.fol.click-trigger")
            tags = [category.text() for category in elements]

        else:
            title = lexbor.css_first("h1.main-h1").text(strip=True)
            length = lexbor.css_first("li.icon.fa-clock-o").text()
            publish_date = lexbor.css_first("li.icon.fa-calendar").text()
            elements = lexbor.css("a.tag-link.click-trigger")
            tags = [element.text() for element in elements]

        cdn_url = PATTERN_CDN_URL.search(html_content).group(1)
        stars = lexbor.css("a.click-trigger") # Works also for mobile version
        pornstars = [star.text() for star in stars]

        return {
            "title": title,
            "cdn_url": cdn_url,
            "pornstars": pornstars,
            "length": length,
            "publish_date": publish_date,
            "tags": tags
        }


    @property
    def video_qualities(self) -> list:
        """
        :return: (list) The available qualities of the video
        """
        quals = self.direct_download_urls
        qualities = set()  # Using a set to avoid duplicates

        for url in quals:
            match = PATTERN_RESOLUTION.search(url)
            if match:
                qualities.add(match.group(1))

        return sorted(qualities, key=int)


    async def download(self, configuration: DownloadConfigRAW):
        await self.load_fields("direct_download_urls", "title")
        cdn_urls = self.direct_download_urls
        quals = self.video_qualities  # e.g., ["360", "480", "720"]
        if not quals:
            raise NotAvailable

        config = copy.deepcopy(configuration)

        qn = normalize_quality_value(config.quality)
        chosen_height = choose_quality_from_list(quals, qn)

        quality_url_map = {int(re.search(r'(\d{3,4})', q).group(1)): url for q, url in zip(quals, cdn_urls)}
        download_url = f"https://{quality_url_map[chosen_height]}"

        if not config.no_title:
            config.path = os.path.join(config.path, f"{self.title}.mp4")

        try:
            return await self.core.legacy_download(url=download_url, configuration=config)

        except Exception as e:
            raise DownloadFailed(str(e))


class Client:
    def __init__(self, core: BaseCore = BaseCore()):
        self.core = core
        self.core.initialize_session()
        self.helper = Helper(core=self.core, constructor=Video)
        assert isinstance(self.core.session, AsyncSession)
        self.core.session.headers.update(headers) # These headers MUST be applied, otherwise the API will not work!

    def _video_stream(
        self,
        page_urls: list[str],
        *,
        iterator_config: IteratorConfig | None = None,
    ):
        if iterator_config is None:
            iterator_config = make_iterator_config()

        return self.helper.iterator(
            target_page_urls=page_urls,
            item_extractor=extractor_html,
            iterator_config=iterator_config,
        )

    async def get_video(self, url: str, load_html: bool = True) -> Video:
        """
        :param url: The video URL
        :return: Video object
        """
        video = Video(url=url, core=self.core)
        if load_html:
            await video.load_sources("html")
        return video

    async def get_videos_by_actress(
        self,
        name: str,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult, None]:
        """
        :param pages: (int) The number of pages to fetch
        :param name: The actress name or the URL
        :param iterator_config: Iterator concurrency, loading, ordering, and error behavior.
        :return: Video object
        """
        name = Checks().check_actress(name)
        final_url = f"{root_url_actress}{name}"
        page_urls = build_page_urls(pagination=Pagination.PATH, base=final_url, pages=pages, start_page=0)

        stream = self._video_stream(page_urls, iterator_config=iterator_config)
        async with stream:
            async for scrape_result in stream:
                yield scrape_result

    async def get_videos_by_category(
        self,
        category: Category | str,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult, None]:
        """
        :param pages: (int) The number of pages to fetch
        :param category: Category: The video category
        :param iterator_config: Iterator concurrency, loading, ordering, and error behavior.
        :return: Video object
        """
        url = f"{root_url_category}{category}"
        page_urls = build_page_urls(pagination=Pagination.PATH, base=url, pages=pages, start_page=0)
        stream = self._video_stream(page_urls, iterator_config=iterator_config)
        async with stream:
            async for scrape_result in stream:
                yield scrape_result


    async def search_videos(
        self,
        query: str,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult, None]:
        """
        :param query:
        :param pages: (int) How many pages to fetch
        :param iterator_config: Iterator concurrency, loading, ordering, and error behavior.
        :return: Video object
        """
        query = query.replace(" ", "+")
        url = f"{root_url}?q={query}"
        page_urls = build_page_urls(pagination=Pagination.QUERY, base=url, pages=pages, start_page=1)
        stream = self._video_stream(page_urls, iterator_config=iterator_config)
        async with stream:
            async for scrape_result in stream:
                yield scrape_result

    async def get_top_porn(
        self,
        sort_by: Sort | str,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult, None]:
        """
        :param pages: (int) How many pages to fetch
        :param sort_by: all_time, month, week
        :param iterator_config: Iterator concurrency, loading, ordering, and error behavior.
        :return: Video object
        """
        if sort_by == "all_time":
            url = root_url_top

        else:
            url = f"{root_url_top}{sort_by}"

        page_urls = build_page_urls(base=url, start_page=0, pagination=Pagination.PATH, pages=pages)
        stream = self._video_stream(page_urls, iterator_config=iterator_config)
        async with stream:
            async for scrape_result in stream:
                yield scrape_result

    async def get_all_categories(self) -> list[str]:
        """
        :return: (list) Returns all categories of HQporner as a list of strings
        """
        _, html_content = await get_html_content(url="https://hqporner.com/categories", core=self.core)
        assert isinstance(html_content, str)
        parser = LexborHTMLParser(html_content)
        results = parser.css("a.click-trigger")
        return [result.text() for result in results]

    async def get_random_video(self, load_html: bool = True) -> Video:
        """
        :return: Video object (random video from HQPorner)
        """
        _, html_content = await get_html_content(url=root_random, core=self.core)
        assert isinstance(html_content, str)
        videos = extractor_random_video(html_content)
        video_url = choice(videos) # The random-porn from HQPorner returns 3 videos, so we pick one of them
        video = Video(url=f"{video_url}", core=self.core)
        if load_html:
            await video.load_sources("html")
        return video

    async def get_brazzers_videos(
        self,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult, None]:
        """
        :param pages: (int) How many pages to fetch
        :param iterator_config: Iterator concurrency, loading, ordering, and error behavior.
        :return: Video object
        """
        page_urls = build_page_urls(pagination=Pagination.PATH, pages=pages, base=root_brazzers, start_page=0)
        stream = self._video_stream(page_urls, iterator_config=iterator_config)
        async with stream:
            async for scrape_result in stream:
                yield scrape_result


async def async_main():
    parser = argparse.ArgumentParser(description="HQPorner API Command Line Interface")
    
    # Modes
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--download", metavar="URL", type=str, help="URL to download a single video from")
    group.add_argument("--file", metavar="FILE", type=str, help="Specify a file with URLs (separated with new lines)")
    group.add_argument("--search", metavar="QUERY", type=str, help="Search videos by query")
    group.add_argument("--actress", metavar="NAME", type=str, help="Get videos by actress name or URL")
    group.add_argument("--category", metavar="CATEGORY", type=str, help="Get videos by category")
    group.add_argument("--random", action="store_true", help="Download a random video")

    # Options
    parser.add_argument("--quality", metavar="QUALITY", type=str, help="The video quality (best,half,worst, or e.g., 720)", required=True)
    parser.add_argument("--output", metavar="DIR", type=str, help="The output path directory", required=True)
    parser.add_argument("--no-title", metavar="True/False", type=str, default="False",
                        help="Whether to apply video title automatically to output path or not")
    parser.add_argument("--pages", metavar="N", type=int, default=1, help="Number of pages to fetch (default: 1)")
    parser.add_argument("--concurrency", metavar="N", type=int, default=3, help="Max concurrent downloads (default: 3)")

    args = parser.parse_args()
    
    console = Console()
    
    no_title_bool = args.no_title.lower() in ("true", "1", "yes", "t", "y") if isinstance(args.no_title, str) else bool(args.no_title)
    config = DownloadConfigRAW(quality=args.quality, path=args.output, no_title=no_title_bool)
    
    client = Client()
    videos = []

    if args.download:
        console.print(f"[bold blue]Fetching video info for:[/bold blue] {args.download}")
        videos.append(await client.get_video(args.download))
        
    elif args.file:
        console.print(f"[bold blue]Reading URLs from:[/bold blue] {args.file}")
        with open(args.file, "r") as file:
            content = file.read().splitlines()
            
        async def fetch_video(url):
            return await client.get_video(url)
            
        fetched = await asyncio.gather(*[fetch_video(url) for url in content if url.strip()])
        videos.extend(fetched)
        
    elif args.random:
        console.print("[bold blue]Fetching a random video...[/bold blue]")
        videos.append(await client.get_random_video())
        
    elif args.search:
        console.print(f"[bold blue]Searching for:[/bold blue] {args.search}")
        async for scrape_result in client.search_videos(args.search, pages=args.pages):
            videos.append(scrape_result.unwrap())
                
    elif args.actress:
        console.print(f"[bold blue]Fetching videos for actress:[/bold blue] {args.actress}")
        async for scrape_result in client.get_videos_by_actress(args.actress, pages=args.pages):
            videos.append(scrape_result.unwrap())
                
    elif args.category:
        console.print(f"[bold blue]Fetching videos for category:[/bold blue] {args.category}")
        async for scrape_result in client.get_videos_by_category(args.category, pages=args.pages):
            videos.append(scrape_result.unwrap())

    if not videos:
        console.print("[bold red]No videos found to download.[/bold red]")
        return
        
    table = Table(title="Videos to Download")
    table.add_column("Title", style="cyan")
    table.add_column("Length", style="magenta")
    table.add_column("URL", style="green", overflow="fold")
    
    for v in videos:
        table.add_row(v.title or "Unknown", v.length or "Unknown", v.url)
        
    console.print(table)
    console.print(f"\n[bold yellow]Starting download of {len(videos)} video(s) with concurrency {args.concurrency}...[/bold yellow]\n")

    semaphore = asyncio.Semaphore(args.concurrency)
    
    async def safe_download(video):
        async with semaphore:
            try:
                await video.load_fields("direct_download_urls")
                console.print(f"[cyan]Downloading ->[/cyan] {video.title}")
                await video.download(configuration=config)
                console.print(f"[bold green]Done ->[/bold green] {video.title}")
            except Exception as e:
                console.print(f"[bold red]Error downloading {video.title or video.url}:[/bold red] {e}")

    tasks = [safe_download(v) for v in videos]
    await asyncio.gather(*tasks)
    
    console.print("[bold green]All downloads completed![/bold green]")


def main():
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")

if __name__ == "__main__":
    main()
