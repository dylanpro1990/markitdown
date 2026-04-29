import json
import time
import re
import bs4
import requests
from html import unescape
from typing import Any, BinaryIO, Dict, List, Union
from urllib.parse import parse_qs, urlparse, unquote
from xml.etree import ElementTree

from .._base_converter import DocumentConverter, DocumentConverterResult
from .._stream_info import StreamInfo

# Optional YouTube transcription support
try:
    # Suppress some warnings on library import
    import warnings

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=SyntaxWarning)
        # Patch submitted upstream to fix the SyntaxWarning
        from youtube_transcript_api import YouTubeTranscriptApi

    IS_YOUTUBE_TRANSCRIPT_CAPABLE = True
except ModuleNotFoundError:
    IS_YOUTUBE_TRANSCRIPT_CAPABLE = False

try:
    from yt_dlp import YoutubeDL

    IS_YT_DLP_CAPABLE = True
except ModuleNotFoundError:
    IS_YT_DLP_CAPABLE = False


ACCEPTED_MIME_TYPE_PREFIXES = [
    "text/html",
    "application/xhtml",
]

ACCEPTED_FILE_EXTENSIONS = [
    ".html",
    ".htm",
]


class YouTubeConverter(DocumentConverter):
    """Handle YouTube specially, focusing on the video title, description, and transcript."""

    def accepts(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,  # Options to pass to the converter
    ) -> bool:
        """
        Make sure we're dealing with HTML content *from* YouTube.
        """
        url = stream_info.url or ""
        mimetype = (stream_info.mimetype or "").lower()
        extension = (stream_info.extension or "").lower()

        url = unquote(url)
        url = url.replace(r"\?", "?").replace(r"\=", "=")

        if not url.startswith("https://www.youtube.com/watch?"):
            # Not a YouTube URL
            return False

        if extension in ACCEPTED_FILE_EXTENSIONS:
            return True

        for prefix in ACCEPTED_MIME_TYPE_PREFIXES:
            if mimetype.startswith(prefix):
                return True

        # Not HTML content
        return False

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,  # Options to pass to the converter
    ) -> DocumentConverterResult:
        video_id = self._extract_video_id(stream_info.url or "")

        # Parse the stream
        encoding = "utf-8" if stream_info.charset is None else stream_info.charset
        soup = bs4.BeautifulSoup(file_stream, "html.parser", from_encoding=encoding)

        # Read the meta tags
        metadata: Dict[str, str] = {}

        if soup.title and soup.title.string:
            metadata["title"] = soup.title.string

        for meta in soup(["meta"]):
            if not isinstance(meta, bs4.Tag):
                continue

            for a in meta.attrs:
                if a in ["itemprop", "property", "name"]:
                    key = str(meta.get(a, ""))
                    content = str(meta.get("content", ""))
                    if key and content:  # Only add non-empty content
                        metadata[key] = content
                    break

        # Try reading the description
        try:
            for script in soup(["script"]):
                if not isinstance(script, bs4.Tag):
                    continue
                if not script.string:  # Skip empty scripts
                    continue
                content = script.string
                if "ytInitialData" in content:
                    match = re.search(r"var ytInitialData = ({.*?});", content)
                    if match:
                        data = json.loads(match.group(1))
                        attrdesc = self._findKey(data, "attributedDescriptionBodyText")
                        if attrdesc and isinstance(attrdesc, dict):
                            metadata["description"] = str(attrdesc.get("content", ""))
                    break
        except Exception as e:
            print(f"Error extracting description: {e}")
            pass

        # If YouTube returned a generic shell page, oEmbed is more reliable for title lookup.
        if stream_info.url:
            oembed_metadata = self._fetch_oembed_metadata(stream_info.url)
            if oembed_metadata:
                for key, value in oembed_metadata.items():
                    metadata.setdefault(key, value)

        # Start preparing the page
        webpage_text = "# YouTube\n"

        title = self._get(metadata, ["title", "og:title", "name"])  # type: ignore
        assert isinstance(title, str)

        if title:
            webpage_text += f"\n## {title}\n"

        stats = ""
        views = self._get(metadata, ["interactionCount"])  # type: ignore
        if views:
            stats += f"- **Views:** {views}\n"

        keywords = self._get(metadata, ["keywords"])  # type: ignore
        if keywords:
            stats += f"- **Keywords:** {keywords}\n"

        runtime = self._get(metadata, ["duration"])  # type: ignore
        if runtime:
            stats += f"- **Runtime:** {runtime}\n"

        if len(stats) > 0:
            webpage_text += f"\n### Video Metadata\n{stats}\n"

        description = self._get(metadata, ["description", "og:description"])  # type: ignore
        if description:
            webpage_text += f"\n### Description\n{description}\n"

        transcript_text = ""
        if stream_info.url and IS_YT_DLP_CAPABLE:
            transcript_text = self._fetch_transcript_with_ytdlp(stream_info.url)
        if not transcript_text and IS_YOUTUBE_TRANSCRIPT_CAPABLE and video_id:
            transcript_text = self._fetch_transcript(video_id, **kwargs)
        if not transcript_text and video_id:
            transcript_text = self._fetch_transcript_from_caption_track(str(soup))
        if transcript_text:
            webpage_text += f"\n### Transcript\n{transcript_text}\n"

        if not transcript_text and self._looks_like_generic_youtube_page(metadata):
            webpage_text += (
                "\n### Note\n"
                "YouTube returned a generic page shell and no transcript was available "
                "for this video from the current environment.\n"
            )

        title = title if title else (soup.title.string if soup.title else "")
        assert isinstance(title, str)

        return DocumentConverterResult(
            markdown=webpage_text,
            title=title,
        )

    def _get(
        self,
        metadata: Dict[str, str],
        keys: List[str],
        default: Union[str, None] = None,
    ) -> Union[str, None]:
        """Get first non-empty value from metadata matching given keys."""
        for k in keys:
            if k in metadata:
                return metadata[k]
        return default

    def _findKey(self, json: Any, key: str) -> Union[str, None]:  # TODO: Fix json type
        """Recursively search for a key in nested dictionary/list structures."""
        if isinstance(json, list):
            for elm in json:
                ret = self._findKey(elm, key)
                if ret is not None:
                    return ret
        elif isinstance(json, dict):
            for k, v in json.items():
                if k == key:
                    return json[k]
                if result := self._findKey(v, key):
                    return result
        return None

    def _extract_video_id(self, url: str) -> str:
        if not url:
            return ""

        parsed_url = urlparse(url)
        params = parse_qs(parsed_url.query)
        if "v" in params and params["v"] and params["v"][0]:
            return str(params["v"][0])

        if parsed_url.netloc in {"youtu.be", "www.youtu.be"}:
            return parsed_url.path.strip("/")

        return ""

    def _fetch_oembed_metadata(self, url: str) -> Dict[str, str]:
        try:
            response = requests.get(
                "https://www.youtube.com/oembed",
                params={"url": url, "format": "json"},
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as e:
            print(f"Error fetching YouTube oEmbed metadata: {e}")
            return {}

        metadata: Dict[str, str] = {}
        title = payload.get("title")
        author_name = payload.get("author_name")
        if isinstance(title, str) and title.strip():
            metadata["og:title"] = title.strip()
        if isinstance(author_name, str) and author_name.strip():
            metadata["author"] = author_name.strip()
        return metadata

    def _fetch_transcript(self, video_id: str, **kwargs: Any) -> str:
        ytt_api = YouTubeTranscriptApi()
        transcript_text = ""

        try:
            transcript_list = ytt_api.list(video_id)
        except Exception as e:
            print(f"Error listing transcripts: {e}")
            return ""

        languages = ["en"]
        for transcript in transcript_list:
            languages.append(transcript.language_code)
            break

        try:
            youtube_transcript_languages = kwargs.get(
                "youtube_transcript_languages", languages
            )
            transcript = self._retry_operation(
                lambda: ytt_api.fetch(
                    video_id, languages=youtube_transcript_languages
                ),
                retries=3,
                delay=2,
            )
            if transcript:
                transcript_text = " ".join([part.text for part in transcript])  # type: ignore
        except Exception as e:
            if len(languages) == 1:
                print(f"Error fetching transcript: {e}")
                return ""
            try:
                transcript = (
                    transcript_list.find_transcript(languages)
                    .translate(youtube_transcript_languages[0])
                    .fetch()
                )
                transcript_text = " ".join([part.text for part in transcript])
            except Exception as translate_error:
                print(f"Error translating transcript: {translate_error}")

        return transcript_text

    def _fetch_transcript_from_caption_track(self, page_html: str) -> str:
        track_url = self._extract_caption_track_url(page_html)
        if not track_url:
            return ""

        try:
            response = requests.get(track_url, timeout=15)
            response.raise_for_status()
            return self._parse_caption_xml(response.text)
        except Exception as e:
            print(f"Error fetching transcript from caption track: {e}")
            return ""

    def _fetch_transcript_with_ytdlp(self, url: str) -> str:
        try:
            with YoutubeDL(
                {
                    "quiet": True,
                    "no_warnings": True,
                    "skip_download": True,
                    "writesubtitles": True,
                    "writeautomaticsub": True,
                }
            ) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            print(f"Error fetching transcript with yt-dlp: {e}")
            return ""

        subtitle_entry = self._select_ytdlp_subtitle_entry(info or {})
        if not subtitle_entry:
            return ""

        subtitle_url = subtitle_entry.get("url")
        if not isinstance(subtitle_url, str) or not subtitle_url:
            return ""

        headers = self._build_ytdlp_request_headers(info or {}, url)

        try:
            response = requests.get(subtitle_url, headers=headers, timeout=20)
            response.raise_for_status()
        except Exception as e:
            print(f"Error downloading yt-dlp subtitle track: {e}")
            return ""

        ext = str(subtitle_entry.get("ext") or "").lower()
        if ext == "json3":
            return self._parse_json3_transcript(response.text)
        if ext in {"srv1", "srv2", "srv3", "ttml", "xml"}:
            return self._parse_caption_xml(response.text)
        return self._parse_vtt_transcript(response.text)

    def _build_ytdlp_request_headers(
        self, info: Dict[str, Any], watch_url: str
    ) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        raw_headers = info.get("http_headers")
        if isinstance(raw_headers, dict):
            for key, value in raw_headers.items():
                if isinstance(key, str) and isinstance(value, str):
                    headers[key] = value

        # Reuse yt-dlp's browser-like headers so timedtext requests do not
        # get downgraded into generic or rate-limited responses.
        headers.setdefault(
            "User-Agent",
            (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            ),
        )
        headers.setdefault("Accept", "*/*")
        headers.setdefault("Accept-Language", "en-US,en;q=0.9")
        headers["Referer"] = watch_url
        headers["Origin"] = "https://www.youtube.com"
        return headers

    def _select_ytdlp_subtitle_entry(self, info: Dict[str, Any]) -> Dict[str, Any]:
        candidates: list[tuple[tuple[int, int, int], Dict[str, Any]]] = []

        subtitle_sources = []
        subtitles = info.get("subtitles")
        automatic_captions = info.get("automatic_captions")
        if isinstance(subtitles, dict):
            subtitle_sources.append((0, subtitles))
        if isinstance(automatic_captions, dict):
            subtitle_sources.append((1, automatic_captions))

        preferred_languages = (
            "en",
            "en-US",
            "en-GB",
            "zh-Hans",
            "zh-CN",
            "zh",
            "zh-Hant",
            "zh-TW",
        )
        preferred_exts = ("json3", "srv3", "srv1", "ttml", "vtt")

        for source_rank, source in subtitle_sources:
            for lang, entries in source.items():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    ext = str(entry.get("ext") or "").lower()
                    lang_rank = (
                        preferred_languages.index(lang)
                        if lang in preferred_languages
                        else len(preferred_languages)
                    )
                    ext_rank = (
                        preferred_exts.index(ext)
                        if ext in preferred_exts
                        else len(preferred_exts)
                    )
                    candidates.append(((source_rank, lang_rank, ext_rank), entry))

        if not candidates:
            return {}

        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _extract_caption_track_url(self, page_html: str) -> str:
        match = re.search(r'"captionTracks":\[(.*?)\]', page_html, flags=re.DOTALL)
        if not match:
            return ""

        track_blob = match.group(1)
        candidates = re.findall(r'"baseUrl":"(.*?)"', track_blob)
        if not candidates:
            return ""

        def score(url: str) -> tuple[int, int]:
            lowered = url.lower()
            english = 0 if "lang=en" in lowered else 1
            auto = 1 if ("kind=asr" in lowered or "asr" in lowered) else 0
            return (english, auto)

        best_candidate = sorted(candidates, key=score)[0]
        return self._decode_escaped_url(best_candidate)

    def _decode_escaped_url(self, value: str) -> str:
        try:
            decoded = json.loads(f'"{value}"')
        except Exception:
            decoded = value
        return decoded.replace("\\u0026", "&")

    def _parse_caption_xml(self, xml_text: str) -> str:
        try:
            root = ElementTree.fromstring(xml_text)
        except Exception as e:
            print(f"Error parsing caption XML: {e}")
            return ""

        parts: List[str] = []
        for node in root.findall(".//text"):
            text = "".join(node.itertext()).strip()
            if text:
                parts.append(unescape(text))

        return " ".join(parts)

    def _parse_json3_transcript(self, json_text: str) -> str:
        try:
            payload = json.loads(json_text)
        except Exception as e:
            print(f"Error parsing json3 transcript: {e}")
            return ""

        parts: List[str] = []
        for event in payload.get("events", []):
            if not isinstance(event, dict):
                continue
            segs = event.get("segs")
            if not isinstance(segs, list):
                continue
            text = "".join(
                seg.get("utf8", "") for seg in segs if isinstance(seg, dict)
            ).strip()
            if text:
                parts.append(unescape(text.replace("\n", " ")))
        return " ".join(parts)

    def _parse_vtt_transcript(self, vtt_text: str) -> str:
        parts: List[str] = []
        for line in vtt_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped == "WEBVTT":
                continue
            if "-->" in stripped:
                continue
            if re.fullmatch(r"\d+", stripped):
                continue
            cleaned = re.sub(r"<[^>]+>", "", stripped).strip()
            if cleaned:
                parts.append(unescape(cleaned))
        return " ".join(parts)

    def _looks_like_generic_youtube_page(self, metadata: Dict[str, str]) -> bool:
        title = (metadata.get("title") or metadata.get("og:title") or "").strip()
        description = (
            metadata.get("description") or metadata.get("og:description") or ""
        ).strip()
        has_stats = any(
            metadata.get(key)
            for key in ("interactionCount", "keywords", "duration")
        )

        generic_titles = {
            "",
            "youtube",
            "youtube.com",
            "youtube - broadcast yourself",
        }

        return title.lower() in generic_titles and not description and not has_stats

    def _retry_operation(self, operation, retries=3, delay=2):
        """Retries the operation if it fails."""
        attempt = 0
        while attempt < retries:
            try:
                return operation()  # Attempt the operation
            except Exception as e:
                print(f"Attempt {attempt + 1} failed: {e}")
                if attempt < retries - 1:
                    time.sleep(delay)  # Wait before retrying
                attempt += 1
        # If all attempts fail, raise the last exception
        raise Exception(f"Operation failed after {retries} attempts.")
