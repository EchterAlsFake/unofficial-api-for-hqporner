import re
from selectolax.lexbor import LexborHTMLParser

PATTERN_CDN_URL = re.compile(r"url: '/blocks/altplayer\.php\?i=//(.*?)',")
PATTERN_CHECK_URL_ACTRESS = re.compile(r"https://hqporner.com/actress/(.*)")
PATTERN_EXTRACT_CDN_URLS = re.compile(r"href='//(.*?)' style=")
PATTERN_RESOLUTION = re.compile(r'(\d{3,4})\.mp4')


root_url = "https://hqporner.com/"
root_url_actress = "https://hqporner.com/actress/"
root_url_category = "https://hqporner.com/category/"
root_url_top = "https://hqporner.com/top/"
root_random = "https://hqporner.com/random-porn"
root_brazzers = "https://hqporner.com/studio/free-brazzers-videos"

headers = {
    "Referer": "https://hqporner.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.63 Safari/537.36",
}


def extractor_html(content: str):
    stuff = []
    parser = LexborHTMLParser(content)
    videos = parser.css('div[class="6u"]')

    for video in videos:
        img_el = video.css_first("img")
        thumbnail = f"https:{img_el.attributes.get('src')}" if img_el else None

        title_el = video.css_first("h3.meta-data-title a")

        if title_el:
            title = title_el.text(strip=True)
            _url = title_el.attributes.get("href")
            url = f"https://www.hqporner.com{_url}" if _url else None
        else:
            title, url = None, None

        if not isinstance(url, str) or not url:
            continue

        length_el = video.css_first("span.fa-clock-o")
        length = length_el.text(strip=True) if length_el else None

        stuff.append({
            "url": url,
            "title": title,
            "thumbnail": thumbnail,
            "length": length
        })

    return stuff

def extractor_random_video(content: str):
    parser = LexborHTMLParser(content)
    videos = parser.css("a.image.featured.non-overlay.atfib")
    return [f"{root_url}{m.attributes.get('href')}" for m in videos]
