from __future__ import annotations

import http.client
import json
import os
import random
import re
import ssl
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import requests

from .config import PROJECT_ROOT, env_bool, env_float


TRAILING_FILLER_RE = re.compile(r"(?:吧|呀|啊|呢|好吗|行吗|可以吗|谢谢)$")
LEADING_FILLER_RE = re.compile(r"^(?:请|麻烦|帮我|给我|我想|我要|想要|想听)?")
PLAY_PREFIX_RE = re.compile(
    r"^(?:播放一下|播放|放一下|放|播一下|播|来一下|来|听一下|听)(?:一首|首|一下|下)?"
)
NON_MUSIC_KEYWORDS = {
    "新闻",
    "广播",
    "电台",
    "相声",
    "小说",
    "故事",
    "有声",
    "播客",
    "fm",
    "FM",
}
TRACK_URL_CACHE: dict[int, tuple[float, str]] = {}
COOKIE_ATTR_NAMES = {
    "max-age",
    "expires",
    "path",
    "domain",
    "secure",
    "httponly",
    "samesite",
    "priority",
    "partitioned",
}
GENERIC_MUSIC_PATTERNS = [
    re.compile(r"^(?:请|麻烦|帮我|给我|我想|我要|想要|想听)?(?:来|放|播|播放|听)(?:一)?首歌(?:吧|呀|啊|呢)?$"),
    re.compile(r"^(?:请|麻烦|帮我|给我)?随便(?:来|放|播)(?:一)?首歌(?:吧|呀|啊|呢)?$"),
    re.compile(r"^(?:请|麻烦|帮我|给我)?来点音乐(?:吧|呀|啊|呢)?$"),
]
LIKED_PLAYLIST_PATTERNS = [
    re.compile(r"^(?:请|麻烦|帮我|给我|我想|我要)?(?:播放|放|播|打开|来)(?:一下)?(?:我喜欢的歌单|我喜欢的音乐|我的喜欢|我的红心歌单|我收藏的音乐)(?:吧|呀|啊|呢)?$"),
    re.compile(r"^(?:请|麻烦|帮我|给我|我想|我要)?(?:播放|放|播|打开|来)(?:一下)?(?:喜欢列表|喜欢歌单|红心歌单)(?:吧|呀|啊|呢)?$"),
]
HEART_MODE_PATTERNS = [
    re.compile(r"^(?:请|麻烦|帮我|给我|我想|我要)?(?:打开|开启|进入|播放|放|播)(?:一下)?心动模式(?:吧|呀|啊|呢)?$"),
    re.compile(r"^(?:请|麻烦|帮我|给我|我想|我要)?(?:在|从)?(?:我喜欢的歌单|我喜欢的音乐|喜欢列表)(?:里|中)?(?:打开|开启|进入)?心动模式(?:吧|呀|啊|呢)?$"),
]


@dataclass
class MusicTrack:
    song_id: int
    title: str
    artist: str
    album: str
    url: str
    upstream_url: str = ""
    img_url: str = ""
    duration: int = 0

    def public_dict(self) -> dict[str, Any]:
        data = {
            "id": self.song_id,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "url": self.url,
        }
        if self.img_url:
            data["imgUrl"] = self.img_url
        if self.duration > 0:
            data["duration"] = self.duration
        return data


@dataclass
class MusicSearchResult:
    handled: bool
    route: str
    keyword: str = ""
    answer: str = ""
    tracks: list[MusicTrack] = field(default_factory=list)
    error: str = ""

    def result_payload(self) -> dict[str, Any]:
        music_info = [track.public_dict() for track in self.tracks]
        return {
            "result": {
                "count": len(music_info),
                "musicinfo": music_info,
                "pagesize": str(len(music_info)),
                "errorCode": 0,
                "page": "1",
                "source": 1,
            }
        }

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tracks"] = [track.public_dict() for track in self.tracks]
        return data


def music_enabled() -> bool:
    endpoint = str(os.getenv("R1LAB_MUSIC_ENDPOINT") or "").strip()
    return bool(endpoint) and env_bool("R1LAB_MUSIC_ENABLED", True)


def music_timeout_seconds() -> float:
    return env_float("R1LAB_MUSIC_TIMEOUT_SECONDS", 8.0)


def music_max_results() -> int:
    raw = str(os.getenv("R1LAB_MUSIC_MAX_RESULTS") or "").strip()
    if not raw:
        return 5
    try:
        return max(1, min(10, int(raw)))
    except ValueError:
        return 5


def music_level() -> str:
    return str(os.getenv("R1LAB_MUSIC_LEVEL") or "exhigh").strip() or "exhigh"


def music_public_base_url() -> str:
    base_url = str(os.getenv("R1LAB_MUSIC_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if base_url:
        return base_url
    return "http://asrv3.hivoice.cn"


def music_cache_seconds() -> int:
    raw = str(os.getenv("R1LAB_MUSIC_CACHE_SECONDS") or "").strip()
    if not raw:
        return 1800
    try:
        return max(60, min(86400, int(raw)))
    except ValueError:
        return 1800


def music_playlist_results() -> int:
    raw = str(os.getenv("R1LAB_MUSIC_PLAYLIST_RESULTS") or "").strip()
    if not raw:
        return 20
    try:
        return max(5, min(100, int(raw)))
    except ValueError:
        return 20


def music_playlist_fetch_limit() -> int:
    raw = str(os.getenv("R1LAB_MUSIC_PLAYLIST_FETCH_LIMIT") or "").strip()
    if not raw:
        return 200
    try:
        return max(20, min(1000, int(raw)))
    except ValueError:
        return 200


def music_cookie_file() -> Path:
    raw = str(os.getenv("R1LAB_MUSIC_COOKIE_FILE") or "").strip()
    if raw:
        return Path(raw)
    return PROJECT_ROOT / "data" / "music_cookie.txt"


def normalize_music_cookie(raw_cookie: str) -> str:
    text = raw_cookie.strip()
    if not text:
        return ""

    parts: list[str]
    if ";;" in text:
        parts = text.split(";;")
    else:
        parts = [text]

    cookie_map: dict[str, str] = {}
    for part in parts:
        for token in part.split(";"):
            item = token.strip()
            if not item or "=" not in item:
                continue
            name, value = item.split("=", 1)
            key = name.strip()
            if not key or key.lower() in COOKIE_ATTR_NAMES:
                continue
            cleaned_value = value.strip()
            if not cleaned_value:
                continue
            if key in cookie_map:
                cookie_map.pop(key, None)
            cookie_map[key] = cleaned_value

    return "; ".join(f"{key}={value}" for key, value in cookie_map.items())


def load_music_cookie() -> str:
    env_cookie = str(os.getenv("R1LAB_MUSIC_COOKIE") or "").strip()
    if env_cookie:
        return normalize_music_cookie(env_cookie)
    cookie_file = music_cookie_file()
    if not cookie_file.exists():
        return ""
    raw_cookie = cookie_file.read_text(encoding="utf-8").strip()
    normalized = normalize_music_cookie(raw_cookie)
    if normalized and normalized != raw_cookie:
        cookie_file.write_text(normalized, encoding="utf-8")
    return normalized


def save_music_cookie(cookie: str) -> None:
    cleaned = normalize_music_cookie(cookie)
    cookie_file = music_cookie_file()
    cookie_file.parent.mkdir(parents=True, exist_ok=True)
    cookie_file.write_text(cleaned, encoding="utf-8")


def extract_special_music_intent(text: str) -> str | None:
    cleaned = re.sub(r"\s+", "", text).strip("。！？!?，,、；;：:")
    if not cleaned:
        return None
    if any(pattern.match(cleaned) for pattern in HEART_MODE_PATTERNS):
        return "heart_mode"
    if any(pattern.match(cleaned) for pattern in LIKED_PLAYLIST_PATTERNS):
        return "liked_playlist"
    return None


def extract_music_keyword(text: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", text).strip(" \t\r\n。！？!?，,、；;：:")
    if not cleaned:
        return None

    lowered = cleaned.lower()
    if any(keyword in cleaned for keyword in NON_MUSIC_KEYWORDS):
        return None

    looks_like_music = (
        "的歌" in cleaned
        or "歌曲" in cleaned
        or bool(re.match(r"^(?:请|麻烦|帮我|给我|我想|我要|想要|想听)?(?:来|放|播|播放|听)", cleaned))
    )
    if not looks_like_music:
        return None

    cleaned = LEADING_FILLER_RE.sub("", cleaned)
    cleaned = PLAY_PREFIX_RE.sub("", cleaned)
    cleaned = cleaned.replace("一首歌", "")
    cleaned = cleaned.replace("一首", "")
    cleaned = re.sub(r"^首", "", cleaned)
    cleaned = cleaned.replace("歌曲", "")
    cleaned = re.sub(r"的歌(?:曲)?$", "", cleaned)
    cleaned = TRAILING_FILLER_RE.sub("", cleaned)
    cleaned = cleaned.strip(" \t\r\n。！？!?，,、；;：:")
    if len(cleaned) < 2:
        return None
    if lowered in {"播放", "来一首", "放一首", "听一下"}:
        return None
    return cleaned


def is_generic_music_request(text: str) -> bool:
    cleaned = re.sub(r"\s+", "", text).strip("。！？!?，,、；;：:")
    if not cleaned:
        return False
    return any(pattern.match(cleaned) for pattern in GENERIC_MUSIC_PATTERNS)


def _join_path(base_path: str, child: str) -> str:
    base = base_path.rstrip("/")
    suffix = child.lstrip("/")
    if not base:
        return f"/{suffix}"
    return f"{base}/{suffix}"


def _request_json(endpoint: str, request_path: str, query: dict[str, Any]) -> tuple[int, Any]:
    parts = urlsplit(endpoint)
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"Invalid music endpoint: {endpoint}")

    path = _join_path(parts.path, request_path)
    effective_query = dict(query)
    cookie = load_music_cookie()
    if cookie and "cookie" not in effective_query:
        effective_query["cookie"] = cookie
    if effective_query:
        path = f"{path}?{urlencode(effective_query, doseq=True)}"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    headers = {
        "Accept": "application/json",
        "User-Agent": "r1_lab/0.2",
    }
    timeout = music_timeout_seconds()
    if parts.scheme == "https":
        connection: http.client.HTTPConnection = http.client.HTTPSConnection(
            parts.hostname,
            port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
    else:
        connection = http.client.HTTPConnection(parts.hostname, port, timeout=timeout)
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        body = response.read()
        if not body:
            return response.status, {}
        return response.status, json.loads(body.decode("utf-8", errors="replace"))
    finally:
        connection.close()


def _cache_track_url(song_id: int, upstream_url: str) -> None:
    TRACK_URL_CACHE[song_id] = (time.time() + music_cache_seconds(), upstream_url)


def _cleanup_track_cache() -> None:
    now = time.time()
    expired = [song_id for song_id, (expires_at, _) in TRACK_URL_CACHE.items() if expires_at < now]
    for song_id in expired:
        TRACK_URL_CACHE.pop(song_id, None)


def proxy_track_url(song_id: int) -> str:
    return f"{music_public_base_url()}/music/netease/{song_id}.mp3"


def login_status() -> tuple[int, Any]:
    endpoint = str(os.getenv("R1LAB_MUSIC_ENDPOINT") or "").strip().rstrip("/")
    if not endpoint:
        return 503, {"error": "music_endpoint_missing"}
    cookie = load_music_cookie()
    response = requests.post(
        f"{endpoint}/login/status?timestamp={int(time.time() * 1000)}",
        json={"cookie": cookie},
        timeout=music_timeout_seconds(),
    )
    return response.status_code, response.json()


def _fetch_song_urls(ids: list[int], endpoint: str) -> tuple[int, dict[int, str]]:
    url_status, url_payload = _request_json(
        endpoint,
        "song/url/v1",
        {
            "id": ",".join(str(song_id) for song_id in ids),
            "level": music_level(),
        },
    )
    url_map: dict[int, str] = {}
    data_items = (url_payload or {}).get("data") or []
    if isinstance(data_items, list):
        for item in data_items:
            if not isinstance(item, dict):
                continue
            try:
                item_id = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            url_map[item_id] = url
            _cache_track_url(item_id, url)
    return url_status, url_map


def _normalize_song_entry(song: dict[str, Any]) -> dict[str, Any]:
    artists = song.get("ar")
    if artists is None:
        artists = song.get("artists") or []
    album = song.get("al")
    if album is None:
        album = song.get("album") or {}
    duration = song.get("dt")
    if duration is None:
        duration = song.get("duration") or 0
    return {
        "id": song.get("id"),
        "name": song.get("name"),
        "ar": artists,
        "al": album,
        "dt": duration,
    }


def _current_music_user_id() -> int | None:
    status, payload = login_status()
    if status >= 400:
        raise RuntimeError(f"music login status unavailable: {status}")
    data = (payload or {}).get("data") or {}
    if not isinstance(data, dict):
        return None
    profile = data.get("profile") or {}
    account = data.get("account") or {}
    for raw in (profile.get("userId"), account.get("id")):
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return None


def _request_music_user_playlists(endpoint: str, user_id: int) -> list[dict[str, Any]]:
    status, payload = _request_json(
        endpoint,
        "user/playlist",
        {"uid": user_id, "limit": music_playlist_results(), "offset": 0},
    )
    if status >= 400:
        raise RuntimeError(f"user/playlist status={status}")
    playlist_items = (payload or {}).get("playlist") or []
    if not isinstance(playlist_items, list):
        return []
    return [item for item in playlist_items if isinstance(item, dict)]


def _find_liked_playlist(playlists: list[dict[str, Any]], user_id: int) -> dict[str, Any] | None:
    for playlist in playlists:
        try:
            if int(playlist.get("specialType") or 0) == 5:
                return playlist
        except (TypeError, ValueError):
            continue

    for playlist in playlists:
        name = str(playlist.get("name") or "").strip()
        creator = playlist.get("creator") or {}
        creator_id = creator.get("userId")
        if "喜欢" in name:
            try:
                if int(creator_id) == user_id:
                    return playlist
            except (TypeError, ValueError):
                return playlist
    return None


def _extract_song_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    candidates: list[Any] = []
    for key in ("songs", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            candidates = value
            break

    if not candidates:
        playlist = payload.get("playlist")
        if isinstance(playlist, dict):
            tracks = playlist.get("tracks")
            if isinstance(tracks, list):
                candidates = tracks

    songs: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("songInfo"), dict):
            songs.append(_normalize_song_entry(item["songInfo"]))
            continue
        if isinstance(item.get("songData"), dict):
            songs.append(_normalize_song_entry(item["songData"]))
            continue
        songs.append(_normalize_song_entry(item))
    return songs


def _request_playlist_tracks(endpoint: str, playlist_id: int, limit: int | None = None) -> list[dict[str, Any]]:
    status, payload = _request_json(
        endpoint,
        "playlist/track/all",
        {"id": playlist_id, "limit": limit or music_playlist_results(), "offset": 0},
    )
    if status >= 400:
        raise RuntimeError(f"playlist/track/all status={status}")
    return _extract_song_list(payload)


def _shuffle_songs(songs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(songs) <= 1:
        return songs
    shuffled = list(songs)
    random.SystemRandom().shuffle(shuffled)
    return shuffled


def _request_intelligence_tracks(endpoint: str, playlist_id: int, seed_song_id: int) -> list[dict[str, Any]]:
    status, payload = _request_json(
        endpoint,
        "playmode/intelligence/list",
        {
            "id": seed_song_id,
            "pid": playlist_id,
            "sid": seed_song_id,
            "count": music_playlist_results(),
        },
    )
    if status >= 400:
        raise RuntimeError(f"playmode/intelligence/list status={status}")
    return _extract_song_list(payload)


def _build_tracks(
    songs: list[dict[str, Any]],
    keyword: str,
    endpoint: str,
    limit: int | None = None,
) -> list[MusicTrack]:
    max_tracks = limit or music_max_results()
    ids: list[int] = []
    song_meta: dict[int, dict[str, Any]] = {}
    for song in songs:
        if not isinstance(song, dict):
            continue
        try:
            song_id = int(song.get("id"))
        except (TypeError, ValueError):
            continue
        ids.append(song_id)
        song_meta[song_id] = song
        if len(ids) >= max_tracks:
            break

    if not ids:
        return []

    _, url_map = _fetch_song_urls(ids, endpoint)
    tracks: list[MusicTrack] = []
    for song_id in ids:
        upstream_url = url_map.get(song_id, "")
        if not upstream_url:
            continue
        song = song_meta[song_id]
        artists = song.get("ar") or []
        artist_name = ""
        if isinstance(artists, list) and artists:
            artist_name = str((artists[0] or {}).get("name") or "").strip()
        album = song.get("al") or {}
        tracks.append(
            MusicTrack(
                song_id=song_id,
                title=str(song.get("name") or "").strip() or keyword,
                artist=artist_name,
                album=str(album.get("name") or "").strip(),
                url=proxy_track_url(song_id),
                upstream_url=upstream_url,
                img_url=str(album.get("picUrl") or "").strip(),
                duration=max(0, int(song.get("dt") or 0)),
            )
        )
    return tracks


def _request_liked_playlist_tracks(endpoint: str) -> tuple[str, list[dict[str, Any]]]:
    user_id = _current_music_user_id()
    if not user_id:
        raise RuntimeError("music_login_required")
    playlists = _request_music_user_playlists(endpoint, user_id)
    liked_playlist = _find_liked_playlist(playlists, user_id)
    if not liked_playlist:
        raise RuntimeError("liked_playlist_not_found")
    playlist_name = str(liked_playlist.get("name") or "我喜欢的音乐").strip() or "我喜欢的音乐"
    playlist_id = int(liked_playlist["id"])
    songs = _request_playlist_tracks(endpoint, playlist_id, limit=music_playlist_fetch_limit())
    songs = _shuffle_songs(songs)
    return playlist_name, songs


def _request_heart_mode_tracks(endpoint: str) -> tuple[str, list[dict[str, Any]]]:
    user_id = _current_music_user_id()
    if not user_id:
        raise RuntimeError("music_login_required")
    playlists = _request_music_user_playlists(endpoint, user_id)
    liked_playlist = _find_liked_playlist(playlists, user_id)
    if not liked_playlist:
        raise RuntimeError("liked_playlist_not_found")
    playlist_name = str(liked_playlist.get("name") or "我喜欢的音乐").strip() or "我喜欢的音乐"
    playlist_id = int(liked_playlist["id"])
    base_tracks = _request_playlist_tracks(endpoint, playlist_id, limit=1)
    if not base_tracks:
        raise RuntimeError("liked_playlist_empty")
    seed_song_id = int(base_tracks[0]["id"])
    songs = _request_intelligence_tracks(endpoint, playlist_id, seed_song_id)
    if not songs:
        songs = _request_playlist_tracks(endpoint, playlist_id)
    return playlist_name, songs


def _fetch_default_songs(endpoint: str) -> list[dict[str, Any]]:
    status, payload = _request_json(endpoint, "personalized/newsong", {})
    if status < 400:
        result = (payload or {}).get("result") or []
        songs: list[dict[str, Any]] = []
        if isinstance(result, list):
            for item in result:
                if not isinstance(item, dict):
                    continue
                song = item.get("song")
                if isinstance(song, dict):
                    songs.append(_normalize_song_entry(song))
                if len(songs) >= music_max_results():
                    return songs
        if songs:
            return songs

    status, payload = _request_json(endpoint, "top/song", {"type": 0})
    if status >= 400:
        return []
    result = (payload or {}).get("data") or []
    songs = []
    if isinstance(result, list):
        for item in result:
            if not isinstance(item, dict):
                continue
            songs.append(_normalize_song_entry(item))
            if len(songs) >= music_max_results():
                break
    return songs


def resolve_track_url(song_id: int) -> str:
    _cleanup_track_cache()
    cached = TRACK_URL_CACHE.get(song_id)
    now = time.time()
    if cached and cached[0] >= now:
        return cached[1]

    endpoint = str(os.getenv("R1LAB_MUSIC_ENDPOINT") or "").strip().rstrip("/")
    if not endpoint:
        raise RuntimeError("music endpoint is not configured")
    _, url_map = _fetch_song_urls([song_id], endpoint)
    upstream_url = url_map.get(song_id, "").strip()
    if not upstream_url:
        raise RuntimeError(f"song url unavailable for id={song_id}")
    return upstream_url


def open_track_stream(song_id: int, range_header: str | None = None) -> requests.Response:
    upstream_url = resolve_track_url(song_id)
    headers: dict[str, str] = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 8.1; Phicomm-R1) AppleWebKit/537.36",
    }
    if range_header:
        headers["Range"] = range_header
    response = requests.get(
        upstream_url,
        headers=headers,
        timeout=music_timeout_seconds(),
        stream=True,
        allow_redirects=True,
    )
    response.raise_for_status()
    return response


def handle_music_request(text: str) -> MusicSearchResult:
    if not music_enabled():
        return MusicSearchResult(handled=False, route="music_disabled")

    endpoint = str(os.getenv("R1LAB_MUSIC_ENDPOINT") or "").strip().rstrip("/")
    special_intent = extract_special_music_intent(text)
    keyword = extract_music_keyword(text)
    generic_request = is_generic_music_request(text)
    if not special_intent and not keyword and not generic_request:
        return MusicSearchResult(handled=False, route="music_skip")

    try:
        route = "music_play"
        answer = "好的，已为您播放。"
        songs: list[dict[str, Any]]
        if special_intent == "liked_playlist":
            route = "music_liked_playlist"
            playlist_name, songs = _request_liked_playlist_tracks(endpoint)
            keyword = playlist_name
            answer = f"好的，随机播放你的{playlist_name}。"
            if not songs:
                return MusicSearchResult(
                    handled=True,
                    route="music_playlist_empty",
                    keyword=keyword,
                    answer=f"你的{playlist_name}里暂时没有可播放的歌曲。",
                )
        elif special_intent == "heart_mode":
            route = "music_heart_mode"
            playlist_name, songs = _request_heart_mode_tracks(endpoint)
            keyword = f"{playlist_name}心动模式"
            answer = "好的，已为你打开心动模式。"
            if not songs:
                return MusicSearchResult(
                    handled=True,
                    route="music_heart_mode_empty",
                    keyword=keyword,
                    answer="心动模式暂时没有拿到可播放的歌曲。",
                )
        elif generic_request:
            route = "music_generic_play"
            keyword = "推荐歌曲"
            answer = "好的，给你放一首歌。"
            songs = _fetch_default_songs(endpoint)
            if not songs:
                return MusicSearchResult(
                    handled=True,
                    route="music_no_match",
                    keyword=keyword,
                    answer="暂时没有找到可播放的歌曲。",
                )
        else:
            search_status, search_payload = _request_json(endpoint, "cloudsearch", {"keywords": keyword})
            if search_status >= 400:
                return MusicSearchResult(
                    handled=True,
                    route="music_error",
                    keyword=keyword,
                    answer="音乐接口暂时不可用。",
                    error=f"cloudsearch status={search_status}",
                )

            songs = (((search_payload or {}).get("result") or {}).get("songs") or [])
            if not isinstance(songs, list) or not songs:
                return MusicSearchResult(
                    handled=True,
                    route="music_no_match",
                    keyword=keyword,
                    answer="没找到能播放的歌曲。",
                )
        track_limit = music_playlist_results() if special_intent else music_max_results()
        tracks = _build_tracks(songs, keyword or "推荐歌曲", endpoint, limit=track_limit)

        if not tracks:
            return MusicSearchResult(
                handled=True,
                route="music_no_url",
                keyword=keyword,
                answer="这首歌暂时拿不到播放地址。",
            )

        return MusicSearchResult(
            handled=True,
            route=route,
            keyword=keyword,
            answer=answer,
            tracks=tracks,
        )
    except Exception as exc:
        error_text = str(exc)
        if "music_login_required" in error_text:
            return MusicSearchResult(
                handled=True,
                route="music_login_required",
                keyword=keyword or special_intent or "",
                answer="网易云登录还没有同步，请重新扫码登录一次。",
                error=error_text,
            )
        if "liked_playlist_not_found" in error_text:
            return MusicSearchResult(
                handled=True,
                route="music_playlist_missing",
                keyword=keyword or special_intent or "",
                answer="还没有找到你的喜欢歌单。",
                error=error_text,
            )
        if "liked_playlist_empty" in error_text:
            return MusicSearchResult(
                handled=True,
                route="music_playlist_empty",
                keyword=keyword or special_intent or "",
                answer="你的喜欢歌单里暂时没有可播放的歌曲。",
                error=error_text,
            )
        return MusicSearchResult(
            handled=True,
            route="music_error",
            keyword=keyword,
            answer="音乐接口暂时不可用。",
            error=error_text,
        )
