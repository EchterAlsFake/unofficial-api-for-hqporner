"""
Copyright (C) Johannes Habel
"""
import os
import re
import asyncio
import logging
import argparse
from enum import Enum
from random import choice
from dataclasses import dataclass
from functools import cached_property
from typing import List, AsyncGenerator
from curl_cffi import AsyncSession, Response
from selectolax.lexbor import LexborHTMLParser
from base_api import ScrapeResult, BaseCore, setup_logger, Helper, DownloadConfigRAW
from base_api.modules.static_functions import choose_quality_from_list, normalize_quality_value
from base_api.modules.errors import InvalidProxy, UnknownError, BotProtectionDetected, NetworkRequestError, ResourceGone

from hqporner_api.modules.errors import (NotFound, NetworkError, NotAvailable, UnknownNetworkError, BotDetection,
                                        ProxyError, InvalidActress, DownloadFailed)
from hqporner_api.modules.consts import (root_random, root_url, root_url_category, root_url_top, root_brazzers,
                                         root_url_actress, extractor_random_video, extractor_html,
                                         PATTERN_CDN_URL, PATTERN_EXTRACT_CDN_URLS, PATTERN_RESOLUTION,
                                         PATTERN_CHECK_URL_ACTRESS, headers)
from hqporner_api.modules.locals import Category, Sort
from hqporner_api.modules.type_hints import on_error_hint

async def on_error(url: str, error: Exception, attempt: int) -> bool:
    print(f"URL: {url}, ERROR: {error}, Attempt: {attempt}")

    if isinstance(error, ResourceGone):
        return False

    return True


async def get_html_content(core: BaseCore, url: str) -> str | None | dict:
    try:
        content = await core.fetch(url)
        if isinstance(content, str):
            return content

        if isinstance(content, Response):
            if content.status_code == 404:
                raise NotFound(f"Server returned 404 for: {url}")

    except NetworkRequestError as e:
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
    def __init__(self):
        self.logger = setup_logger(name="HQPorner API - [Checks]", log_file=None, level=logging.CRITICAL)

    def enable_logging(self, name: str="HQPorner API - [Checks]", log_file: str | None = None,
                       level: int | None=None, log_ip: str | None = None, log_port: int | None = None):

        if not level:
            level = logging.DEBUG

        self.logger = setup_logger(name=name, log_file=log_file, level=level, http_ip=log_ip, http_port=log_port)

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
                    page_param: str = "p", start_page: int = 1) -> List[str]:
    page_urls = []
    base = base.rstrip("/")
    for i, page in enumerate(range(start_page, start_page + pages), start=0):
        page_urls.append(build_page_url(base, page, mode=pagination, page_param=page_param))

    return page_urls


@dataclass(slots=True)
class VideoMetadata:
    title: str
    cdn_url: str
    pornstars: list[str]
    length: str
    publish_date: str
    tags: list


class Video:
    __slots__ = ("metadata", "core", "_video_qualities")

    def __init__(self, metadata: VideoMetadata, core: BaseCore):
        self.metadata = metadata
        self.core = core
        self._video_qualities = None

    @property
    def title(self) -> str:
        return self.metadata.title

    @property
    def cdn_url(self) -> str:
        return self.metadata.cdn_url

    @property
    def pornstars(self) -> list[str]:
        return self.metadata.pornstars

    @property
    def length(self) -> str:
        return self.metadata.length

    @property
    def publish_date(self) -> str:
        return self.metadata.publish_date

    @property
    def tags(self) -> list[str]:
        return self.metadata.tags

    @property
    async def video_qualities(self) -> list:
        """
        :return: (list) The available qualities of the video
        """
        if not self._video_qualities:
            quals = await self.direct_download_urls()
            qualities = set()  # Using a set to avoid duplicates

            for url in quals:
                match = PATTERN_RESOLUTION.search(url)
                if match:
                    qualities.add(match.group(1))

            self._video_qualities = sorted(qualities, key=int)

        return self._video_qualities

    async def direct_download_urls(self) -> list:
        """
        :return: (list) The direct download urls for all available qualities
        """
        cdn_url = f"https://{self.cdn_url}"
        html_content = await get_html_content(core=self.core, url=cdn_url)
        urls = PATTERN_EXTRACT_CDN_URLS.findall(html_content) # Using regex here cuz it's faster in this case
        return urls

    async def download(self, configuration: DownloadConfigRAW):
        cdn_urls = await self.direct_download_urls()
        quals = await self.video_qualities  # e.g., ["360", "480", "720"]
        if not quals:
            raise NotAvailable

        qn = normalize_quality_value(configuration.quality)
        chosen_height = choose_quality_from_list(quals, qn)

        quality_url_map = {int(re.search(r'(\d{3,4})', q).group(1)): url for q, url in zip(quals, cdn_urls)}
        download_url = f"https://{quality_url_map[chosen_height]}"

        if not configuration.no_title:
            configuration.path = os.path.join(configuration.path, f"{self.title}.mp4")

        try:
            return await self.core.legacy_download(url=download_url, configuration=configuration)

        except Exception as e:
            raise DownloadFailed(str(e))


class VideoBuilder:
    def __init__(self, url: str, core: BaseCore, html_content: str | None = None):
        """
        :param url: (str) The URL of the video
        """
        self.url = url
        self.core = core
        self.logger = setup_logger(name="HQPorner API - [Video]", log_file=None, level=logging.CRITICAL)
        self.is_mobile_fix = False
        self._lexbor: LexborHTMLParser | None = None
        if "m.hqporner" in self.url:
            self.is_mobile_fix = True

        self.html_content = html_content

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.clean()

    def enable_logging(self, name: str="HQPorner API - [Video]", log_file: str | None = None,
                       level: int | None = None, log_ip: str | None = None, log_port: int | None = None):
        if not level:
            level = logging.DEBUG
        self.logger = setup_logger(name=name, log_file=log_file, level=level, http_ip=log_ip, http_port=log_port)

    @property
    def lexbor(self) -> LexborHTMLParser:
        if not self._lexbor:
            raise ValueError("You probably forgot to call init")

        return self._lexbor

    async def init(self):
        if not self.html_content:
            try:
                self.html_content = await get_html_content(core=self.core, url=self.url)

            except NotFound:
                self.logger.warning("404 Error! Applying experimental workaround...")
                self.html_content = await self.core.fetch(
                    url=str(self.url).replace("https://hqporner.com", "https://m.hqporner.com"))
                self.is_mobile_fix = True

        return await asyncio.to_thread(self._extract_metadata_sync)

    def _extract_metadata_sync(self) -> Video:
        assert isinstance(self.html_content, str)
        self._lexbor = LexborHTMLParser(self.html_content)

        meta = VideoMetadata(
            title=self.title,
            cdn_url=self.cdn_url,
            pornstars=self.pornstars,
            length=self.length,
            publish_date=self.publish_date,
            tags=self.tags,

        )

        return Video(metadata=meta, core=self.core)


    @cached_property
    def title(self) -> str:
        """
        :return: (str) The video title (lowercase)
        """
        if self.is_mobile_fix:
            return self.lexbor.css_first("h1[style*='font-size:18px']").text(strip=True)

        return self.lexbor.css_first("h1.main-h1").text(strip=True)

    @cached_property
    def cdn_url(self) -> str:
        """
        :return: (str) The Content Delivery Network URL for the video (can be used to embed the video)
        """
        return PATTERN_CDN_URL.search(self.html_content).group(1)

    @cached_property
    def pornstars(self) -> list[str]:
        """
        :return: (list) The list of pornstars featured in the video
        """
        stars = self.lexbor.css("a.click-trigger") # Works also for mobile version
        return [star.text() for star in stars]


    @cached_property
    def length(self) -> str:
        """
        :return: (str) The length of the video in h / m / s format
        """
        if self.is_mobile_fix:
            return self.lexbor.css("span.meta_data")[1].text(strip=True)

        return self.lexbor.css_first("li.icon.fa-clock-o").text()

    @cached_property
    def publish_date(self) -> str:
        """
        :return: (str) How many months ago the video was uploaded
        """
        if self.is_mobile_fix:
            return self.lexbor.css("span.meta_data")[0].text(strip=True)

        return self.lexbor.css_first("li.icon.fa-calendar").text()

    @cached_property
    def tags(self) -> list:
        """
        :return: (list) A list of tags (categories) featured in this video
        """
        if self.is_mobile_fix:
            elements = self.lexbor.css("a.fol.click-trigger")
            return [category.text() for category in elements]

        elements = self.lexbor.css("a.tag-link.click-trigger")
        return [element.text() for element in elements]


    async def clean(self):
        """
        This function destroys the class without destroying it :)
        """
        self.core = None
        self.url = None
        self.logger = None
        self.html_content = None
        self._lexbor = None


class Client(Helper):
    def __init__(self, core: BaseCore = BaseCore()):
        super().__init__(core, video_constructor=VideoBuilder)
        self.core = core
        self.core.initialize_session()
        assert isinstance(self.core.session, AsyncSession)
        self.core.session.headers.update(headers) # These headers MUST be applied, otherwise the API will not work!
        self.logger = setup_logger(name="HQPorner API - [Client]", log_file=None, level=logging.CRITICAL)

    def enable_logging(self, name = "HQPorner API - [Client]", log_file: str | None = None,
                       level: int | None = None, log_ip: str | None = None, log_port: int | None = None):
        if not level:
            level = logging.DEBUG
        self.logger = setup_logger(name=name, log_file=log_file, level=level, http_ip=log_ip, http_port=log_port)

    async def get_video(self, url: str) -> Video:
        """
        :param url: The video URL
        :return: Video object
        """
        return await VideoBuilder(url, self.core).init()

    async def get_videos_by_actress(self, name: str, pages: int = 5, videos_concurrency: int | None = None,
                            pages_concurrency: int | None = None,
                            on_video_error: on_error_hint = on_error,
                            on_page_error: on_error_hint = None,
                            keep_original_order: bool = False) -> AsyncGenerator[ScrapeResult, None]:
        """
        :param pages: (int) The number of pages to fetch
        :param name: The actress name or the URL
        :param videos_concurrency: (int) How many threads to use to fetch videos
        :param pages_concurrency: (int) How many threads to use to fetch pages
        :param on_page_error:
        :param on_video_error:
        :param keep_original_order:
        :return: Video object
        """
        name = Checks().check_actress(name)
        final_url = f"{root_url_actress}{name}"
        page_urls = build_page_urls(pagination=Pagination.PATH, base=final_url, pages=pages, start_page=0)

        videos_concurrency = videos_concurrency or self.core.configuration.videos_concurrency
        pages_concurrency = pages_concurrency or self.core.configuration.pages_concurrency
        assert videos_concurrency and pages_concurrency
        async for scrape_result in self.iterator(target_page_urls=page_urls, max_video_concurrency=videos_concurrency,
                                 max_page_concurrency=pages_concurrency, video_link_extractor=extractor_html,
                                 on_video_error=on_video_error, on_page_error=on_page_error,
                                 keep_original_order=keep_original_order):
            yield scrape_result

    async def get_videos_by_category(self, category: Category | str, pages: int = 5, videos_concurrency: int | None = None,
                            pages_concurrency: int | None = None,
                                     on_video_error: on_error_hint = on_error,
                                     on_page_error: on_error_hint = None,
                                     keep_original_order: bool = False,
                                     ) -> AsyncGenerator[ScrapeResult, None]:
        """
        :param pages: (int) The number of pages to fetch
        :param category: Category: The video category
        :param videos_concurrency: (int) How many threads to use to fetch videos
        :param pages_concurrency: (int) How many threads to use to fetch pages
        :param on_video_error:
        :param on_page_error:
        :param keep_original_order:
        :return: Video object
        """
        url = f"{root_url_category}{category}"
        page_urls = build_page_urls(pagination=Pagination.PATH, base=url, pages=pages, start_page=0)
        videos_concurrency = videos_concurrency or self.core.configuration.videos_concurrency
        pages_concurrency = pages_concurrency or self.core.configuration.pages_concurrency
        assert videos_concurrency and pages_concurrency
        async for scrape_result in self.iterator(target_page_urls=page_urls, max_video_concurrency=videos_concurrency,
                                 max_page_concurrency=pages_concurrency, video_link_extractor=extractor_html,
                                 on_video_error=on_video_error, on_page_error=on_page_error,
                                 keep_original_order=keep_original_order):
            yield scrape_result


    async def search_videos(self, query: str, pages: int = 5, videos_concurrency: int | None = None,
                            pages_concurrency: int | None = None, on_video_error: on_error_hint = on_error,
                            on_page_error: on_error_hint = None,
                            keep_original_order: bool = False) -> AsyncGenerator[ScrapeResult, None]:
        """
        :param query:
        :param pages: (int) How many pages to fetch
        :param videos_concurrency: (int) How many threads to use to fetch videos
        :param pages_concurrency: (int) How many threads to use to fetch pages
        :param on_video_error:
        :param on_page_error:
        :param keep_original_order:
        :return: Video object
        """
        query = query.replace(" ", "+")
        url = f"{root_url}?q={query}"
        page_urls = build_page_urls(pagination=Pagination.QUERY, base=url, pages=pages, start_page=1)
        videos_concurrency = videos_concurrency or self.core.configuration.videos_concurrency
        pages_concurrency = pages_concurrency or self.core.configuration.pages_concurrency
        assert videos_concurrency and pages_concurrency
        async for scrape_result in self.iterator(target_page_urls=page_urls, max_video_concurrency=videos_concurrency,
                                 max_page_concurrency=pages_concurrency, video_link_extractor=extractor_html,
                                 on_video_error=on_video_error, on_page_error=on_page_error,
                                 keep_original_order=keep_original_order):
            yield scrape_result

    async def get_top_porn(self, sort_by: Sort | str, pages: int = 5,videos_concurrency: int | None = None,
                           pages_concurrency: int | None = None, on_video_error: on_error_hint = on_error,
                           on_page_error: on_error_hint = None,
                           keep_original_order: bool = False) -> AsyncGenerator[ScrapeResult, None]:
        """
        :param pages: (int) How many pages to fetch
        :param sort_by: all_time, month, week
        :param videos_concurrency: (int) How many threads to use to fetch videos
        :param pages_concurrency: (int) How many threads to use to fetch pages
        :param on_video_error:
        :param on_page_error:
        :param keep_original_order:
        :return: Video object
        """
        if sort_by == "all_time":
            url = root_url_top

        else:
            url = f"{root_url_top}{sort_by}"

        page_urls = build_page_urls(base=url, start_page=0, pagination=Pagination.PATH, pages=pages)
        videos_concurrency = videos_concurrency or self.core.configuration.videos_concurrency
        pages_concurrency = pages_concurrency or self.core.configuration.pages_concurrency
        assert videos_concurrency and pages_concurrency
        async for scrape_result in self.iterator(target_page_urls=page_urls, max_video_concurrency=videos_concurrency,
                                 max_page_concurrency=pages_concurrency, video_link_extractor=extractor_html,
                                 on_video_error=on_video_error, on_page_error=on_page_error,
                                 keep_original_order=keep_original_order):
            yield scrape_result

    async def get_all_categories(self) -> list[str]:
        """
        :return: (list) Returns all categories of HQporner as a list of strings
        """
        html_content = await get_html_content(url="https://hqporner.com/categories", core=self.core)
        assert isinstance(html_content, str)
        parser = LexborHTMLParser(html_content)
        results = parser.css("a.click-trigger")
        return [result.text() for result in results]

    async def get_random_video(self) -> Video:
        """
        :return: Video object (random video from HQPorner)
        """
        html_content = await get_html_content(url=root_random, core=self.core)
        assert isinstance(html_content, str)
        videos = extractor_random_video(html_content)
        self.logger.info(f"Got {len(videos)} videos from HQPorner")
        video = choice(videos) # The random-porn from HQPorner returns 3 videos, so we pick one of them
        return await VideoBuilder(f"{video}", self.core).init()

    async def get_brazzers_videos(self, pages: int = 5, videos_concurrency: int | None = None,
                            pages_concurrency: int | None = None, on_video_error: on_error_hint = on_error,
                            on_page_error: on_error_hint = None,
                            keep_original_order: bool = False) -> AsyncGenerator[ScrapeResult, None]:
        """
        :param pages: (int) How many pages to fetch
        :param videos_concurrency: (int) How many threads to use to fetch videos
        :param pages_concurrency: (int) How many threads to use to fetch pages
        :param on_video_error:
        :param on_page_error:
        :param keep_original_order:
        :return: Video object
        """
        page_urls = build_page_urls(pagination=Pagination.PATH, pages=pages, base=root_brazzers, start_page=0)
        videos_concurrency = videos_concurrency or self.core.configuration.videos_concurrency
        pages_concurrency = pages_concurrency or self.core.configuration.pages_concurrency
        assert videos_concurrency and pages_concurrency
        async for scrape_result in self.iterator(target_page_urls=page_urls, video_link_extractor=extractor_html,
                                 max_video_concurrency=videos_concurrency, max_page_concurrency=pages_concurrency,
                                 keep_original_order=keep_original_order, on_video_error=on_video_error,
                                 on_page_error=on_page_error):
            yield scrape_result


async def main():
    parser = argparse.ArgumentParser(description="API Command Line Interface")
    parser.add_argument("--download", metavar="URL (str)", type=str, help="URL to download from")
    parser.add_argument("--quality", metavar="best,half,worst", type=str, help="The video quality (best,half,worst)",
                        required=True)
    parser.add_argument("--file", metavar="Source to .txt file", type=str,
                        help="(Optional) Specify a file with URLs (separated with new lines)")
    parser.add_argument("--output", metavar="Output directory", type=str, help="The output path (with filename)",
                        required=True)
    parser.add_argument("--no-title", metavar="True,False", type=str,
                        help="Whether to apply video title automatically to output path or not", required=True)


    args = parser.parse_args()
    config = DownloadConfigRAW(quality=args.quality, path=args.output, no_title=args.no_title)
    if args.download:
        client = Client()
        video = await client.get_video(args.download)
        await video.download(configuration=config)

    if args.file:
        videos = []
        client = Client()

        with open(args.file, "r") as file:
            content = file.read().splitlines()

        for url in content:
            videos.append(await client.get_video(url))

        for video in videos:
            await video.download(configuration=config)

if __name__ == "__main__":
    asyncio.run(main())
