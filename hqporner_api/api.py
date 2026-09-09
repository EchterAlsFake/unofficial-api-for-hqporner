from __future__ import annotations
import os
import re
import copy
import asyncio
import logging
import argparse

from base_api.modules.logger import configure_app_logging
try:
    from rich.console import Console
    from rich.table import Table
except ImportError:
    Console = None
    Table = None
from enum import Enum
from random import choice
from typing import Any, AsyncGenerator, ClassVar
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
    make_iterator_config,
    is_resource_gone,
    default_on_error,
    scrape_stream,
)
from base_api.modules.static_functions import choose_quality_from_list, normalize_quality_value
from base_api.modules.errors import (
    DownloadCancelled,
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

_is_resource_gone = is_resource_gone
on_error = default_on_error


async def get_html_content(core: BaseCore, url: str, is_second_attempt: bool = False,
                           is_mobile_fix: bool = False) -> tuple[bool, str]:
    try:
        content = await core.fetch_text(url)
        return is_mobile_fix, content

    except HTTPStatusError as e:
        logger.exception("Request failed for %s: %s", url, e)
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
        raise NetworkError(f"Request failed for {url}: {e}") from e

    except (NetworkRequestError, RequestRetriesExhausted) as e:
        logger.exception("Request failed for %s: %s", url, e)
        raise NetworkError(f"Request failed for {url}: {e}") from e

    except InvalidProxy as e:
        logger.exception("Request failed for %s: %s", url, e)
        raise ProxyError(f"Request failed for {url}: {e}") from e

    except BotProtectionDetected as e:
        logger.exception("Request failed for %s: %s", url, e)
        raise BotDetection(f"Request failed for {url}: {e}") from e

    except UnknownError as e:
        logger.exception("Request failed for %s: %s", url, e)
        raise UnknownNetworkError(f"Request failed for {url}: {e}") from e

    except Exception:
        logger.exception("Failed to fetch or decode response for %s", url)
        raise



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
                raise InvalidActress(f"Invalid actress URL: {actress}")

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

    raise ValueError(f"Unsupported pagination mode {mode!r} for {base_url}")


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
        cdn_target = data.get("cdn_url")
        if not cdn_target:
            logger.warning("No CDN URL found for %s", self.url)
            data["direct_download_urls"] = []
            return data

        cdn_url = cdn_target if cdn_target.startswith("http") else f"https://{cdn_target.lstrip('/')}"
        try:
            _, cdn_html = await get_html_content(core=self.core, url=cdn_url)
            data["direct_download_urls"] = PATTERN_EXTRACT_CDN_URLS.findall(cdn_html)
            if not data["direct_download_urls"]:
                logger.warning("No direct download URLs extracted from CDN for %s", self.url)
        except Exception as e:
            logger.warning("Failed to fetch CDN content from %s for %s: %s", cdn_url, self.url, e)
            data["direct_download_urls"] = []

        return data

    def _extract_html(self, is_mobile_fix: bool, html_content: str | None = None) -> dict[str, Any]:
        if isinstance(self, bool):
            url = "unknown"
            html_content = is_mobile_fix  # type: ignore
            is_mobile_fix = self
        else:
            url = getattr(self, "url", "unknown")
            assert html_content is not None

        parser = LexborHTMLParser(html_content)

        # Layout anchors: '#playerWrapper' or '.box.page-content' are expected on all video pages
        if not parser.css_first("#playerWrapper") and not parser.css_first(".box.page-content"):
            logger.warning(
                "Video container anchor ('#playerWrapper' / '.box.page-content') not found for %s; page layout may have changed.",
                url,
            )

        if is_mobile_fix:
            title_node = parser.css_first("h1[style*='font-size:18px']") or parser.css_first("h1")
            meta_spans = parser.css("span.meta_data")
            publish_date = meta_spans[0].text(strip=True) if len(meta_spans) > 0 else None
            length = meta_spans[1].text(strip=True) if len(meta_spans) > 1 else None
            tag_nodes = parser.css("a.fol.click-trigger") or parser.css("a[href*='/category/']")
        else:
            title_node = parser.css_first("h1.main-h1") or parser.css_first("h1")
            clock_node = parser.css_first("li.icon.fa-clock-o") or parser.css_first("li.fa-clock-o")
            length = clock_node.text(strip=True) if clock_node else None
            calendar_node = parser.css_first("li.icon.fa-calendar") or parser.css_first("li.fa-calendar")
            publish_date = calendar_node.text(strip=True) if calendar_node else None
            tag_nodes = parser.css("a.tag-link.click-trigger") or parser.css("a[href*='/category/']")

        title = title_node.text(strip=True) if title_node else None
        tags = [el.text(strip=True) for el in tag_nodes if el.text(strip=True)]

        # Graceful fallbacks
        if not length and (meta_spans := parser.css("span.meta_data")) and len(meta_spans) > 1:
            length = meta_spans[1].text(strip=True)
        if not publish_date and (meta_spans := parser.css("span.meta_data")) and len(meta_spans) > 0:
            publish_date = meta_spans[0].text(strip=True)
        if not tags:
            tag_nodes = parser.css("a[href*='/category/']")
            tags = [el.text(strip=True) for el in tag_nodes if el.text(strip=True)]

        if not title:
            logger.warning("Title not found for %s", url)
        if not length:
            logger.warning("Length not found for %s", url)
        if not publish_date:
            logger.warning("Publish date not found for %s", url)
        if not tags:
            logger.warning("Tags not found for %s", url)

        cdn_url = None
        cdn_match = PATTERN_CDN_URL.search(html_content) or re.search(
            r"/blocks/(?:alt|native)player\.php\?i=//(.*?)['\",]", html_content
        )
        if cdn_match:
            cdn_url = cdn_match.group(1)
        else:
            iframe = (
                parser.css_first("#playerWrapper iframe")
                or parser.css_first(".videoWrapper iframe")
                or parser.css_first("iframe")
            )
            if iframe and (src := iframe.attributes.get("src")):
                cdn_url = re.sub(r"^https?:?//|^//", "", src)

        if not cdn_url:
            logger.warning("CDN URL not found for %s", url)

        # Extract pornstars / actresses
        stars = (
            parser.css("li.icon.fa-star-o a")
            or parser.css("li.fa-star-o a")
            or parser.css("a[href*='/actress/']")
        )
        pornstars = list(dict.fromkeys(star.text(strip=True) for star in stars if star.text(strip=True)))

        return {
            "title": title,
            "cdn_url": cdn_url,
            "pornstars": pornstars,
            "length": length,
            "publish_date": publish_date,
            "tags": tags,
        }

    @property
    def video_qualities(self) -> list[str]:
        """
        :return: (list) The available qualities of the video
        """
        quals = self.direct_download_urls or []
        qualities = set()  # Using a set to avoid duplicates

        for url in quals:
            match = PATTERN_RESOLUTION.search(url)
            if match:
                qualities.add(match.group(1))

        return sorted(qualities, key=int)

    async def download(self, configuration: DownloadConfigRAW):
        try:
            await self.load_fields("direct_download_urls", "title")
            cdn_urls = self.direct_download_urls or []
            quals = self.video_qualities  # e.g., ["360", "480", "720"]
            if not quals:
                raise NotAvailable(f"No download qualities available for {self.url}")

            config = copy.deepcopy(configuration)

            qn = normalize_quality_value(config.quality)
            chosen_height = choose_quality_from_list(quals, qn)

            quality_url_map = {}
            for url in cdn_urls:
                if m := PATTERN_RESOLUTION.search(url):
                    quality_url_map[int(m.group(1))] = url

            target_url = quality_url_map.get(chosen_height)
            if not target_url:
                raise NotAvailable(f"Chosen quality {chosen_height} is not available for {self.url}")

            download_url = target_url if target_url.startswith("http") else f"https://{target_url.lstrip('/')}"

            if not config.no_title:
                config.path = os.path.join(config.path, f"{self.title}.mp4")

            return await self.core.legacy_download(url=download_url, configuration=config)
        except DownloadCancelled:
            raise
        except NotAvailable:
            logger.exception("No download qualities available for %s", self.url)
            raise
        except Exception as e:
            logger.exception("Download failed for %s: %s", self.url, e)
            raise DownloadFailed(f"Download failed for {self.url}: {e}") from e


class Client:
    def __init__(self, core: BaseCore | None = None):
        if core is None:
            core = BaseCore()
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
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        return scrape_stream(
            core=self.core,
            constructor=Video,
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

    def get_videos_by_actress(
        self,
        name: str,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        """
        :param pages: (int) The number of pages to fetch
        :param name: The actress name or the URL
        :param iterator_config: Iterator concurrency, loading, ordering, and error behavior.
        :return: Video object
        """
        name = Checks().check_actress(name)
        final_url = f"{root_url_actress}{name}"
        page_urls = build_page_urls(pagination=Pagination.PATH, base=final_url, pages=pages, start_page=0)
        return self._video_stream(page_urls, iterator_config=iterator_config)

    def get_videos_by_category(
        self,
        category: Category | str,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        """
        :param pages: (int) The number of pages to fetch
        :param category: Category: The video category
        :param iterator_config: Iterator concurrency, loading, ordering, and error behavior.
        :return: Video object
        """
        url = f"{root_url_category}{category}"
        page_urls = build_page_urls(pagination=Pagination.PATH, base=url, pages=pages, start_page=0)
        return self._video_stream(page_urls, iterator_config=iterator_config)

    def search_videos(
        self,
        query: str,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        """
        :param query:
        :param pages: (int) How many pages to fetch
        :param iterator_config: Iterator concurrency, loading, ordering, and error behavior.
        :return: Video object
        """
        query = query.replace(" ", "+")
        url = f"{root_url}?q={query}"
        page_urls = build_page_urls(pagination=Pagination.QUERY, base=url, pages=pages, start_page=1)
        return self._video_stream(page_urls, iterator_config=iterator_config)

    def get_top_porn(
        self,
        sort_by: Sort | str,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
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
        return self._video_stream(page_urls, iterator_config=iterator_config)

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

    def get_brazzers_videos(
        self,
        pages: int = 5,
        iterator_config: IteratorConfig | None = None,
    ) -> AsyncGenerator[ScrapeResult[Video], None]:
        """
        :param pages: (int) How many pages to fetch
        :param iterator_config: Iterator concurrency, loading, ordering, and error behavior.
        :return: Video object
        """
        page_urls = build_page_urls(pagination=Pagination.PATH, pages=pages, base=root_brazzers, start_page=0)
        return self._video_stream(page_urls, iterator_config=iterator_config)


def create_parser() -> argparse.ArgumentParser:
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
    parser.add_argument("--quality", metavar="QUALITY", type=str, default="best", help="The video quality (best,half,worst, or e.g., 720)")
    parser.add_argument("--output", metavar="DIR", type=str, help="The output path directory", required=True)
    parser.add_argument("--no-title", metavar="True/False", type=str, default="False",
                        help="Whether to apply video title automatically to output path or not")
    parser.add_argument("--pages", metavar="N", type=int, default=1, help="Number of pages to fetch (default: 1)")
    parser.add_argument("--concurrency", metavar="N", type=int, default=3, help="Max concurrent downloads (default: 3)")
    return parser


async def async_main(args_list: list[str] | None = None):
    parser = create_parser()
    args = parser.parse_args(args_list)
    
    console = Console() if Console else None
    
    def log(msg, style=""):
        if console:
            console.print(f"[{style}]{msg}[/{style}]" if style else msg)
        else:
            print(msg)
    
    no_title_bool = args.no_title.lower() in ("true", "1", "yes", "t", "y") if isinstance(args.no_title, str) else bool(args.no_title)
    config = DownloadConfigRAW(quality=args.quality, path=args.output, no_title=no_title_bool)
    
    client = Client()
    videos = []

    if args.download:
        log(f"Fetching video info for: {args.download}", "bold blue")
        videos.append(await client.get_video(args.download))
        
    elif args.file:
        log(f"Reading URLs from: {args.file}", "bold blue")
        with open(args.file, "r") as file:
            content = file.read().splitlines()
            
        async def fetch_video(url):
            return await client.get_video(url)
            
        fetched = await asyncio.gather(*[fetch_video(url) for url in content if url.strip()])
        videos.extend(fetched)
        
    elif args.random:
        log("Fetching a random video...", "bold blue")
        videos.append(await client.get_random_video())
        
    elif args.search:
        log(f"Searching for: {args.search}", "bold blue")
        async for scrape_result in client.search_videos(args.search, pages=args.pages):
            videos.append(scrape_result.unwrap())
                
    elif args.actress:
        log(f"Fetching videos for actress: {args.actress}", "bold blue")
        async for scrape_result in client.get_videos_by_actress(args.actress, pages=args.pages):
            videos.append(scrape_result.unwrap())
                
    elif args.category:
        log(f"Fetching videos for category: {args.category}", "bold blue")
        async for scrape_result in client.get_videos_by_category(args.category, pages=args.pages):
            videos.append(scrape_result.unwrap())

    if not videos:
        log("No videos found to download.", "bold red")
        return
        
    if console and Table:
        table = Table(title="Videos to Download")
        table.add_column("Title", style="cyan")
        table.add_column("Length", style="magenta")
        table.add_column("URL", style="green", overflow="fold")
        for v in videos:
            table.add_row(str(v.title or "Unknown"), str(v.length or "Unknown"), str(v.url or "Unknown"))
        console.print(table)
    else:
        for v in videos:
            print(f"- {v.title or 'Unknown'} ({v.length or 'Unknown'}): {v.url}")
        
    log(f"\nStarting download of {len(videos)} video(s) with concurrency {args.concurrency}...\n", "bold yellow")

    semaphore = asyncio.Semaphore(args.concurrency)
    
    async def safe_download(video):
        async with semaphore:
            try:
                await video.load_fields("direct_download_urls")
                log(f"Downloading -> {video.title}", "cyan")
                await video.download(configuration=config)
                log(f"Done -> {video.title}", "bold green")
            except Exception as e:
                logger.exception("CLI download failed for %s", video.url)
                log(f"Error downloading {video.url}: {e}", "bold red")

    tasks = [safe_download(v) for v in videos]
    await asyncio.gather(*tasks)
    
    log("All downloads completed!", "bold green")


run_main = async_main


def main():
    configure_app_logging(level=logging.INFO)
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")


if __name__ == "__main__":
    main()
