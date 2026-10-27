"""Trakt 榜单（剧集/电影 × 趋势/热门），写入 data/trakt.json，结构与 VPS 版 trakt.json 一致：
{last_updated, trending, popular, movie_trending, movie_popular}。
每条用 Trakt 返回的 ids.tmdb 回 TMDB 取详情（海报/简介/评分），widget 端 toVideo 直接可用。
密钥：TRAKT_CLIENT_ID（仓库 secrets，值=NAS vps-widget config 的 traktToken）、TMDB_API_KEY。"""
import asyncio
import aiohttp
import json
import os
import datetime

TMDB_API_KEY = os.environ.get('TMDB_API_KEY')
TRAKT_CLIENT_ID = os.environ.get('TRAKT_CLIENT_ID', '').strip()
DATA_DIR = "data"
OUTPUT_FILE = os.path.join(DATA_DIR, "trakt.json")
LIMIT = 30

tz_bj = datetime.timezone(datetime.timedelta(hours=8))

GENRE_MAP = {28: "动作", 12: "冒险", 16: "动画", 35: "喜剧", 80: "犯罪", 99: "纪录片", 18: "剧情", 10751: "家庭", 14: "奇幻", 36: "历史", 27: "恐怖", 10402: "音乐", 9648: "悬疑", 10749: "爱情", 878: "科幻", 10770: "电视电影", 53: "惊悚", 10752: "战争", 37: "西部", 10759: "动作冒险", 10762: "儿童", 10763: "新闻", 10764: "真人秀", 10765: "科幻奇幻", 10766: "肥皂剧", 10767: "脱口秀", 10768: "战争政治"}
COUNTRY_MAP = {"CN": "中国大陆", "JP": "日本", "KR": "韩国", "US": "美国", "GB": "英国", "TW": "中国台湾", "HK": "中国香港", "JP ": "日本"}

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
TRAKT_HEADERS = {"trakt-api-version": "2", "Accept": "application/json", "Content-Type": "application/json", "User-Agent": "xiaen-list/1.0"}

LISTS = [
    ("trending", "/shows/trending", "show", "tv"),
    ("popular", "/shows/popular", "show", "tv"),
    ("movie_trending", "/movies/trending", "movie", "movie"),
    ("movie_popular", "/movies/popular", "movie", "movie"),
]


def auth():
    headers = {"accept": "application/json"}
    params = {}
    if TMDB_API_KEY and TMDB_API_KEY.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {TMDB_API_KEY}"
    elif TMDB_API_KEY:
        params["api_key"] = TMDB_API_KEY
    return headers, params


async def tmdb_detail(session, media_type, tmdb_id):
    headers, params = auth()
    params.update({"language": "zh-CN"})
    try:
        async with session.get(f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}", params=params, headers={**HEADERS, **headers}) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception:
        pass
    return None


async def fetch_list(session, sem, key, path, kind, media_type):
    headers = {**TRAKT_HEADERS, "trakt-api-key": TRAKT_CLIENT_ID}
    try:
        async with session.get(f"https://api.trakt.tv{path}", params={"limit": LIMIT}, headers=headers) as resp:
            if resp.status != 200:
                print(f"❌ [{key}] Trakt 返回 {resp.status}")
                return []
            raw = await resp.json()
    except Exception as e:
        print(f"❌ [{key}] 请求异常: {e}")
        return []
    entries = raw if isinstance(raw, list) else []
    items = []
    for i, entry in enumerate(entries):
        node = (entry or {}).get(kind) or entry or {}
        ids = node.get("ids") or {}
        tmdb_id = ids.get("tmdb")
        if not tmdb_id:
            continue
        items.append({
            "rank": i + 1,
            "title": node.get("title") or "",
            "year": str(node.get("year") or "")[:4],
            "watchers": entry.get("watchers") or 0,
            "tmdbId": tmdb_id,
            "mediaType": media_type,
        })

    async def enrich(item):
        async with sem:
            d = await tmdb_detail(session, item["mediaType"], item["tmdbId"])
            await asyncio.sleep(0.2)
        if not d:
            return None
        genre_names = [GENRE_MAP.get(g.get("id")) for g in (d.get("genres") or []) if GENRE_MAP.get(g.get("id"))]
        countries = d.get("origin_country") or [c.get("iso_3166_1") for c in (d.get("production_countries") or [])]
        country_names = [COUNTRY_MAP.get(c, c) for c in countries if c]
        release = d.get("first_air_date") or d.get("release_date") or (f"{item['year']}-01-01" if item["year"] else "")
        rating = 0
        if d.get("vote_count"):
            try:
                rating = round(float(d.get("vote_average") or 0), 1)
            except (TypeError, ValueError):
                rating = 0
        return {
            "title": d.get("name") or d.get("title") or item["title"],
            "rank": item["rank"],
            "year": item["year"] or release[:4],
            "id": item["tmdbId"],
            "tmdbId": item["tmdbId"],
            "type": "tmdb",
            "mediaType": item["mediaType"],
            "description": d.get("overview") or "暂无简介",
            "rating": rating,
            "voteCount": d.get("vote_count") or 0,
            "popularity": round(float(d.get("popularity") or 0), 1),
            "heat": item["watchers"],
            "releaseDate": release,
            "lastUpdateDate": release,
            "posterPath": d.get("poster_path"),
            "backdropPath": d.get("backdrop_path"),
            "genreTitle": " / ".join(genre_names[:3]),
            "regionTitle": "/".join(country_names) if country_names else "",
        }

    enriched = [x for x in await asyncio.gather(*[enrich(it) for it in items]) if x]
    enriched.sort(key=lambda x: x["rank"])
    for idx, x in enumerate(enriched):
        x["rank"] = idx + 1
    print(f"✅ [{key}] {len(enriched)}/{len(items)} 条")
    return enriched


async def main():
    if not TMDB_API_KEY:
        print("❌ 错误: 未检测到 TMDB_API_KEY 环境变量！")
        return
    if not TRAKT_CLIENT_ID:
        print("❌ 错误: 未检测到 TRAKT_CLIENT_ID 环境变量！（需在仓库 secrets 配置）")
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    result = {"last_updated": datetime.datetime.now(tz_bj).strftime("%Y-%m-%d %H:%M:%S")}
    sem = asyncio.Semaphore(4)
    async with aiohttp.ClientSession() as session:
        for key, path, kind, media_type in LISTS:
            result[key] = await fetch_list(session, sem, key, path, kind, media_type)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    total = sum(len(result[k]) for k, *_ in LISTS)
    print(f"\n🎉 全部完成！共 {total} 条，数据已保存至 {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
