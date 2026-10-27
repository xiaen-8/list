"""TMDB 榜单：本周趋势(电影/剧集) / 热门(电影/剧集) / 高分(电影/剧集) / 正在上映，
写入 data/tmdb-rank.json。字段与现有 data/*-hot.json 兼容
（tmdbId/mediaType/title/posterPath/backdropPath/rating/releaseDate/overview/genreTitle/regionTitle/rank/popularity），
widget 端复用同一套 toVideo/sortItems。

逻辑对标 Rex首页（rex.home.unified）的 TMDB 精选：同样的 7 个官方 feed、
只要有海报的条目、评分带 vote_count>0 才记。
密钥：TMDB_API_KEY（仓库 secrets 已有）。"""
import asyncio
import aiohttp
import json
import os
import datetime

TMDB_API_KEY = os.environ.get('TMDB_API_KEY')
DATA_DIR = "data"
OUTPUT_FILE = os.path.join(DATA_DIR, "tmdb-rank.json")

tz_bj = datetime.timezone(datetime.timedelta(hours=8))

# (分类名, TMDB 路径, 媒体类型) —— 与 Rex首页 RexHomeTmdbPicks 一一对应
FEEDS = [
    ("本周电影趋势", "/trending/movie/week", "movie"),
    ("本周剧集趋势", "/trending/tv/week", "tv"),
    ("热门电影", "/movie/popular", "movie"),
    ("热门剧集", "/tv/popular", "tv"),
    ("高分电影", "/movie/top_rated", "movie"),
    ("高分剧集", "/tv/top_rated", "tv"),
    ("正在上映", "/movie/now_playing", "movie"),
]

GENRE_MAP = {28: "动作", 12: "冒险", 16: "动画", 35: "喜剧", 80: "犯罪", 99: "纪录片", 18: "剧情", 10751: "家庭", 14: "奇幻", 36: "历史", 27: "恐怖", 10402: "音乐", 9648: "悬疑", 10749: "爱情", 878: "科幻", 10770: "电视电影", 53: "惊悚", 10752: "战争", 37: "西部", 10759: "动作冒险", 10762: "儿童", 10763: "新闻", 10764: "真人秀", 10765: "科幻奇幻", 10766: "肥皂剧", 10767: "脱口秀", 10768: "战争政治"}
COUNTRY_MAP = {"CN": "中国大陆", "JP": "日本", "KR": "韩国", "US": "美国", "GB": "英国", "TW": "中国台湾", "HK": "中国香港"}

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}


def auth():
    """与仓库其它脚本一致：eyJ 开头走 Bearer，否则走 api_key 参数"""
    headers = {"accept": "application/json"}
    params = {}
    if TMDB_API_KEY and TMDB_API_KEY.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {TMDB_API_KEY}"
    elif TMDB_API_KEY:
        params["api_key"] = TMDB_API_KEY
    return headers, params


def build_item(raw, media, rank):
    if not raw.get("poster_path"):
        return None
    title = raw.get("title") or raw.get("name") or ""
    if not title:
        return None
    genre_names = [GENRE_MAP.get(g) for g in raw.get("genre_ids", []) if GENRE_MAP.get(g)]
    country_names = [COUNTRY_MAP.get(c, c) for c in raw.get("origin_country", [])]
    rating = 0
    if raw.get("vote_count"):
        try:
            rating = round(float(raw.get("vote_average") or 0), 1)
        except (TypeError, ValueError):
            rating = 0
    return {
        "title": title,
        "rank": rank,
        "category": media,
        "tmdbId": raw.get("id"),
        "mediaType": media,
        "tmdbTitle": title,
        "releaseDate": raw.get("release_date") or raw.get("first_air_date") or "",
        "posterPath": raw.get("poster_path"),
        "backdropPath": raw.get("backdrop_path"),
        "rating": rating,
        "popularity": round(float(raw.get("popularity") or 0), 1),
        "overview": raw.get("overview") or "暂无简介",
        "regionTitle": "/".join(country_names) if country_names else "",
        "genreTitle": " / ".join(genre_names[:3]) if genre_names else "",
    }


async def fetch_feed(session, name, path, media):
    headers, params = auth()
    params.update({"language": "zh-CN", "page": 1})
    url = f"https://api.themoviedb.org/3{path}"
    try:
        async with session.get(url, params=params, headers={**HEADERS, **headers}) as resp:
            if resp.status != 200:
                print(f"❌ [{name}] TMDB 返回 {resp.status}")
                return []
            data = await resp.json()
    except Exception as e:
        print(f"❌ [{name}] 请求异常: {e}")
        return []
    items = []
    for raw in data.get("results", []):
        item = build_item(raw, media, len(items) + 1)
        if item:
            items.append(item)
    print(f"✅ [{name}] {len(items)} 条")
    return items


async def main():
    if not TMDB_API_KEY:
        print("❌ 错误: 未检测到 TMDB_API_KEY 环境变量！")
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    categories = {}
    async with aiohttp.ClientSession() as session:
        for name, path, media in FEEDS:
            categories[name] = await fetch_feed(session, name, path, media)
            await asyncio.sleep(0.3)
    final_result = {
        "source": "TMDB",
        "last_updated": datetime.datetime.now(tz_bj).strftime("%Y-%m-%d %H:%M:%S"),
        "categories": categories,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_result, f, ensure_ascii=False, indent=2)
    total = sum(len(v) for v in categories.values())
    print(f"\n🎉 全部完成！共 {total} 条，数据已保存至 {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
