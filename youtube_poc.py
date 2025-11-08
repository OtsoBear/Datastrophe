import os
import time
import random
import re
import json
import argparse
from typing import Dict, Iterable, List, Optional, Tuple

import isodate
import requests
from dotenv import load_dotenv


YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
HASHTAG_REGEX = re.compile(r"(?i)(?<!\w)#([a-z0-9_]+)")


def load_api_key(explicit_key: Optional[str] = None) -> str:
    """
    Load YouTube Data API key from:
    1) explicit argument
    2) environment variable GOOGLE_API_KEY
    3) .env file (GOOGLE_API_KEY)
    """
    if explicit_key:
        return explicit_key
    load_dotenv(override=False)
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GOOGLE_API_KEY. Put it in your environment or .env file.")
    return api_key


def http_get(endpoint: str, params: Dict[str, str]) -> Dict:
    """
    Simple GET wrapper that raises on non-200 and returns parsed JSON.
    """
    response = requests.get(f"{YOUTUBE_API_BASE}/{endpoint}", params=params, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code} for {endpoint}: {response.text}")
    return response.json()


def parse_iso8601_duration_to_seconds(duration_iso8601: str) -> int:
    """
    Convert ISO 8601 duration (e.g., 'PT59S', 'PT1M0S') to seconds.
    """
    try:
        duration = isodate.parse_duration(duration_iso8601)
        return int(duration.total_seconds())
    except Exception:
        return 0


def extract_hashtags(text: Optional[str]) -> List[str]:
    if not text:
        return []
    return list({match.group(1).lower() for match in HASHTAG_REGEX.finditer(text)})


def fetch_trending_candidates(
    api_key: str,
    region_codes: List[str],
    per_region_pages: int = 2,
    max_results_per_page: int = 50,
    sleep_between_calls_s: float = 0.0,
    hl: Optional[str] = None,
) -> List[Dict]:
    """
    Use videos.list with chart=mostPopular to fetch trending candidates across multiple regions.
    We'll filter them to <= 60s later to approximate Shorts.
    Cost: 1 quota unit per call.
    """
    results: List[Dict] = []
    for region in region_codes:
        page_token: Optional[str] = None
        for _ in range(per_region_pages):
            params = {
                "key": api_key,
                "part": "snippet,contentDetails,statistics",
                "chart": "mostPopular",
                "regionCode": region,
                "maxResults": str(max_results_per_page),
            }
            if hl:
                params["hl"] = hl
            if page_token:
                params["pageToken"] = page_token
            data = http_get("videos", params)
            items = data.get("items", [])
            results.extend(items)
            page_token = data.get("nextPageToken")
            if not page_token:
                break
            if sleep_between_calls_s > 0:
                time.sleep(sleep_between_calls_s)
    return results


def fetch_shorts_via_search(
    api_key: str,
    published_after_iso: Optional[str],
    regions: List[str],
    pages_per_region: int = 2,
    max_results_per_page: int = 50,
    sleep_between_calls_s: float = 0.0,
    relevance_language: Optional[str] = None,
) -> List[str]:
    """
    Use search.list to target SHORT videos, optionally limited to recent uploads.
    Note: search.list costs 100 units per call, so use sparingly.
    Returns list of video IDs.
    """
    collected_ids: List[str] = []
    for region in regions:
        page_token: Optional[str] = None
        for _ in range(pages_per_region):
            params = {
                "key": api_key,
                "part": "snippet",
                "type": "video",
                "videoDuration": "short",
                "order": "viewCount",
                "maxResults": str(max_results_per_page),
                "regionCode": region,
            }
            if published_after_iso:
                params["publishedAfter"] = published_after_iso
            if relevance_language:
                params["relevanceLanguage"] = relevance_language
            if page_token:
                params["pageToken"] = page_token
            data = http_get("search", params)
            items = data.get("items", [])
            for it in items:
                vid = it.get("id", {}).get("videoId")
                if vid:
                    collected_ids.append(vid)
            page_token = data.get("nextPageToken")
            if not page_token:
                break
            if sleep_between_calls_s > 0:
                time.sleep(sleep_between_calls_s)
    return list(dict.fromkeys(collected_ids))  # dedupe, preserve order


def videos_details_bulk(
    api_key: str,
    video_ids: List[str],
    parts: str = "snippet,contentDetails,statistics",
    batch_size: int = 50,
) -> List[Dict]:
    """
    Retrieve details for up to 50 IDs per call using videos.list.
    """
    details: List[Dict] = []
    for i in range(0, len(video_ids), batch_size):
        chunk = video_ids[i : i + batch_size]
        params = {
            "key": api_key,
            "part": parts,
            "id": ",".join(chunk),
            "maxResults": "50",
        }
        data = http_get("videos", params)
        details.extend(data.get("items", []))
    return details


def fetch_top_comments_with_replies(
    api_key: str,
    video_id: str,
    limit: int = 500,
    order: str = "relevance",
) -> List[Dict]:
    """
    Fetch up to `limit` comments including replies.
    - Retrieve top-level threads via commentThreads.list with maxResults=100 pages.
    - For threads with replies, fetch full reply pages via comments.list as needed.
    Returns list of dicts:
      { type: "comment"|"reply",
        text: str,
        author: str,
        likeCount: int,
        publishedAt: str,
        parentId: Optional[str] }
    """
    comments: List[Dict] = []
    next_page: Optional[str] = None
    while True:
        if len(comments) >= limit:
            break
        params = {
            "key": api_key,
            "part": "snippet,replies",
            "videoId": video_id,
            "maxResults": "100",
            "order": order,
            "textFormat": "plainText",
        }
        if next_page:
            params["pageToken"] = next_page
        data = http_get("commentThreads", params)
        threads = data.get("items", [])
        for th in threads:
            top = th.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
            top_id = th.get("snippet", {}).get("topLevelComment", {}).get("id")
            if top:
                comments.append(
                    {
                        "type": "comment",
                        "id": th.get("id"),
                        "text": top.get("textDisplay"),
                        "author": top.get("authorDisplayName"),
                        "likeCount": top.get("likeCount"),
                        "publishedAt": top.get("publishedAt"),
                        "parentId": None,
                    }
                )
                if len(comments) >= limit:
                    break
            # Fetch all replies beyond the few inline ones
            total_reply_count = th.get("snippet", {}).get("totalReplyCount", 0)
            if total_reply_count and top_id:
                replies_added = fetch_all_replies_for_parent(
                    api_key=api_key,
                    parent_id=top_id,
                    remaining=limit - len(comments),
                )
                comments.extend(replies_added)
                if len(comments) >= limit:
                    break
        next_page = data.get("nextPageToken")
        if not next_page:
            break
    return comments[:limit]


def fetch_all_replies_for_parent(api_key: str, parent_id: str, remaining: int) -> List[Dict]:
    """
    Fetch replies for a given top-level comment (parentId) via comments.list.
    """
    collected: List[Dict] = []
    next_page: Optional[str] = None
    while remaining > 0:
        params = {
            "key": api_key,
            "part": "snippet",
            "parentId": parent_id,
            "maxResults": str(min(100, remaining)),
            "textFormat": "plainText",
        }
        if next_page:
            params["pageToken"] = next_page
        data = http_get("comments", params)
        items = data.get("items", [])
        for it in items:
            sn = it.get("snippet", {})
            collected.append(
                {
                    "type": "reply",
                    "id": it.get("id"),
                    "text": sn.get("textDisplay"),
                    "author": sn.get("authorDisplayName"),
                    "likeCount": sn.get("likeCount"),
                    "publishedAt": sn.get("publishedAt"),
                    "parentId": sn.get("parentId"),
                }
            )
            remaining -= 1
            if remaining <= 0:
                break
        next_page = data.get("nextPageToken")
        if not next_page:
            break
    return collected


def shortlist_video_items_as_shorts(items: List[Dict]) -> List[Dict]:
    """
    Filter items to videos whose contentDetails.duration <= 60 seconds.
    """
    shorts: List[Dict] = []
    for it in items:
        duration_iso = it.get("contentDetails", {}).get("duration", "PT0S")
        if parse_iso8601_duration_to_seconds(duration_iso) <= 60:
            shorts.append(it)
    return shorts


def filter_by_snippet_language(items: List[Dict], allowed_langs: List[str], strict: bool = False) -> List[Dict]:
    """
    Keep items whose snippet.defaultAudioLanguage or snippet.defaultLanguage starts with any allowed_lang.
    If neither field exists:
      - strict=False: keep the item (best-effort filter)
      - strict=True:  drop the item
    """
    allowed = [lang.lower() for lang in allowed_langs]
    kept: List[Dict] = []
    for it in items:
        sn = it.get("snippet", {}) or {}
        langs = []
        val1 = (sn.get("defaultAudioLanguage") or "").lower()
        val2 = (sn.get("defaultLanguage") or "").lower()
        if val1:
            langs.append(val1)
        if val2:
            langs.append(val2)
        if langs:
            if any(any(l.startswith(a) for l in langs) for a in allowed):
                kept.append(it)
        else:
            if not strict:
                kept.append(it)
    return kept


def sample_videos(items: List[Dict], n: int, popularity_bias: float = 0.5) -> List[Dict]:
    """
    Sample a mix of popular and less popular videos.
    - popularity_bias: fraction drawn from most popular half (by viewCount), rest from the other half.
    """
    if not items:
        return []
    with_views = []
    for it in items:
        vc = int(it.get("statistics", {}).get("viewCount", "0") or "0")
        with_views.append((vc, it))
    with_views.sort(key=lambda x: x[0], reverse=True)
    mid = len(with_views) // 2
    popular = [x[1] for x in with_views[:mid]]
    less_popular = [x[1] for x in with_views[mid:]]
    n_pop = int(round(n * popularity_bias))
    n_less = max(0, n - n_pop)
    sampled = random.sample(popular, min(n_pop, len(popular))) + random.sample(less_popular, min(n_less, len(less_popular)))
    random.shuffle(sampled)
    return sampled[:n]


def assemble_video_record(video_item: Dict, comments: List[Dict]) -> Dict:
    """
    Build a compact JSON-serializable record for downstream analysis.
    """
    snippet = video_item.get("snippet", {}) or {}
    statistics = video_item.get("statistics", {}) or {}
    description = snippet.get("description", "") or ""
    tags = snippet.get("tags", []) or []
    hashtags = sorted(set(extract_hashtags(description)) | {t[1:].lower() for t in tags if t.startswith("#")})
    return {
        "videoId": video_item.get("id"),
        "title": snippet.get("title"),
        "description": description,
        "hashtags": hashtags,
        "channelId": snippet.get("channelId"),
        "publishedAt": snippet.get("publishedAt"),
        "categoryId": snippet.get("categoryId"),
        "thumbnails": snippet.get("thumbnails"),
        "duration": video_item.get("contentDetails", {}).get("duration"),
        "engagement": {
            "viewCount": statistics.get("viewCount"),
            "likeCount": statistics.get("likeCount"),
            "commentCount": statistics.get("commentCount"),
        },
        "comments": comments,
    }


def run_youtube_poc(
    api_key: Optional[str],
    out_path: str,
    regions: List[str],
    target_video_count: int = 100,
    comments_per_video: int = 500,
    use_search_boost: bool = True,
    published_after_iso: Optional[str] = None,
    ui_language: Optional[str] = None,
    relevance_language: Optional[str] = None,
    filter_langs: Optional[List[str]] = None,
    strict_lang: bool = False,
) -> Tuple[List[Dict], str]:
    """
    End-to-end:
      1) Fetch trending candidates across regions
      2) Optionally boost with search for short videos
      3) Filter to <= 60s
      4) Sample to target_video_count (mix popular and less popular)
      5) Fetch top comments (+ replies) per video
      6) Persist JSONL to out_path
    Returns (records, out_path)
    """
    key = load_api_key(api_key)
    trending = fetch_trending_candidates(
        api_key=key,
        region_codes=regions,
        per_region_pages=2,
        hl=ui_language,
    )
    trending_shorts = shortlist_video_items_as_shorts(trending)
    candidate_ids = [it["id"] for it in trending_shorts]

    if use_search_boost:
        boost_ids = fetch_shorts_via_search(
            api_key=key,
            published_after_iso=published_after_iso,
            regions=regions,
            pages_per_region=1,
            relevance_language=relevance_language,
        )
        candidate_ids = list(dict.fromkeys(candidate_ids + boost_ids))
        # Pull details for boosted IDs (search only returns snippet)
        boosted_details = videos_details_bulk(key, boost_ids, parts="snippet,contentDetails,statistics")
        trending_shorts.extend(shortlist_video_items_as_shorts(boosted_details))

    # Deduplicate by videoId
    by_id: Dict[str, Dict] = {}
    for it in trending_shorts:
        vid = it.get("id")
        if isinstance(vid, dict):
            vid = vid.get("videoId")
        if not vid:
            continue
        by_id[str(vid)] = it
    all_short_items = list(by_id.values())

    # Optional language filtering (best-effort)
    if filter_langs:
        all_short_items = filter_by_snippet_language(all_short_items, filter_langs, strict=strict_lang)

    # Top up details if any items lack statistics
    missing_stats = [it.get("id") for it in all_short_items if "statistics" not in it]
    missing_stats = [vid for vid in missing_stats if isinstance(vid, str)]
    if missing_stats:
        filled = videos_details_bulk(key, missing_stats, parts="snippet,contentDetails,statistics")
        filled_map = {it["id"]: it for it in filled}
        for i, it in enumerate(all_short_items):
            vid = it.get("id")
            if isinstance(vid, str) and vid in filled_map:
                all_short_items[i] = filled_map[vid]

    sampled_items = sample_videos(all_short_items, n=target_video_count, popularity_bias=0.6)

    records: List[Dict] = []
    for idx, item in enumerate(sampled_items, 1):
        vid = item.get("id") if isinstance(item.get("id"), str) else item.get("id", {}).get("videoId")
        if not vid:
            continue
        comments = fetch_top_comments_with_replies(api_key=key, video_id=vid, limit=comments_per_video)
        record = assemble_video_record(item, comments)
        records.append(record)

    # Persist as JSONL
    with open(out_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return records, out_path


def _truncate(text: Optional[str], max_len: int = 120) -> str:
    if not text:
        return ""
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def _print_record_summary(rec: Dict) -> None:
    engagement = rec.get("engagement") or {}
    hashtags = rec.get("hashtags") or []
    print(
        f"- {rec.get('videoId')} | { _truncate(rec.get('title')) }\n"
        f"  hashtags: {', '.join(hashtags[:10])}\n"
        f"  views: {engagement.get('viewCount')}  likes: {engagement.get('likeCount')}  comments: {engagement.get('commentCount')}  fetched_comments: {len(rec.get('comments') or [])}\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="YouTube Shorts POC: fetch metadata and top comments.")
    parser.add_argument("--api-key", type=str, default=None, help="YouTube Data API key (or set GOOGLE_API_KEY)")
    parser.add_argument("--out", type=str, default="youtube_poc_output.jsonl", help="Output JSONL file")
    parser.add_argument("--regions", type=str, default="US,GB", help="Comma-separated region codes, e.g. US,GB,DE")
    parser.add_argument("--videos", type=int, default=10, help="Target number of videos")
    parser.add_argument("--comments", type=int, default=50, help="Comments (+replies) per video")
    parser.add_argument("--search-boost", action="store_true", help="Use search.list to boost Shorts discovery (costly)")
    parser.add_argument("--published-after", type=str, default=None, help="ISO datetime to limit search boost, e.g. 2025-11-08T00:00:00Z")
    parser.add_argument("--lang", type=str, default=None, help="Language hint (e.g., fi). Sets videos.list hl and search.list relevanceLanguage.")
    parser.add_argument("--filter-lang", type=str, default=None, help="Comma-separated language codes to keep by snippet language fields (e.g., fi,sv).")
    parser.add_argument("--strict-lang", action="store_true", help="Drop videos that lack language hints if filtering is enabled.")
    parser.add_argument("--show", action="store_true", help="Print human-readable metadata summaries")
    parser.add_argument("--show-json", action="store_true", help="Print full JSON records to stdout")
    args = parser.parse_args()

    regions = [r.strip() for r in args.regions.split(",") if r.strip()]
    filter_langs = [l.strip() for l in args.filter_lang.split(",")] if args.filter_lang else None
    records, out_path = run_youtube_poc(
        api_key=args.api_key,
        out_path=args.out,
        regions=regions,
        target_video_count=args.videos,
        comments_per_video=args.comments,
        use_search_boost=args.search_boost,
        published_after_iso=args.published_after,
        ui_language=args.lang,
        relevance_language=args.lang,
        filter_langs=filter_langs,
        strict_lang=args.strict_lang,
    )
    print(f"Wrote {len(records)} records to {out_path}")
    if args.show_json:
        for rec in records:
            print(json.dumps(rec, ensure_ascii=False))
    elif args.show:
        print("\nMetadata summaries:\n")
        for rec in records:
            _print_record_summary(rec)


if __name__ == "__main__":
    main()

