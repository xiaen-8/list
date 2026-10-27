"""MAL 热播番剧（MyAnimeList），写入 data/mal.json。
走 Jikan 免费 API 取 top anime（filter=airing），逐条回 TMDB 匹配动画（题材须含 16），
字段与现有 data/*-hot.json 兼容，widget 端复用同一套 toVideo/sortItems。
密钥：TMDB_API_KEY（仓库 secrets 已有，仅用于匹配）。"""
import asyncio
import aiohttp
import json
import os
import re
import datetime

TMDB_API_KEY = os.environ.get('TMDB_API_KEY')
DATA_DIR = "data"
OUTPUT_FILE = os.path.join(DATA_DIR, "mal.json")

tz_bj = datetime.timezone(datetime.timedelta(hours=8))

GENRE_MAP = {28: "动作", 12: "冒险", 16: "动画", 35: "喜剧", 80: "犯罪", 99: "纪录片", 18: "剧情", 10751: "家庭", 14: "奇幻", 36: "历史", 27: "恐怖", 10402: "音乐", 9648: "悬疑", 10749: "爱情", 878: "科幻", 10770: "电视电影", 53: "惊悚", 10752: "战争", 37: "西部", 10759: "动作冒险", 10762: "儿童", 10763: "新闻", 10764: "真人秀", 10765: "科幻奇幻", 10766: "肥皂剧", 10767: "脱口秀", 10768: "战争政治"}
COUNTRY_MAP = {"CN": "中国大陆", "JP": "日本", "KR": "韩国", "US": "美国", "GB": "英国", "TW": "中国台湾", "HK": "中国香港"}

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}


def norm(s):
    return re.sub(r'\s+', ' ', str(s or '').strip().lower())


def auth():
    headers = {"accept": "application/json"}
    params = {}
    if TMDB_API_KEY and TMDB_API_KEY.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {TMDB_API_KEY}"
    elif TMDB_API_KEY:
        params["api_key"] = TMDB_API_KEY
    return headers, params


async def search_tv(session, query):
    headers, params = auth()
    params.update({"query": query, "language": "zh-CN", "page": 1})
    try:
        async with session.get("https://api.themoviedb.org/3/search/tv", params=params, headers={**HEADERS, **headers}) as resp:
            if resp.status == 200:
                return (await resp.json()).get("results", [])
    except Exception:
        pass
    return []


async def match_anime(session, titles, cache):
    """titles 按优先级尝试（英文名→罗马字→日文名）；只收动画题材(16)+海报，优先片名相等"""
    key = "||".join(titles)
    if key in cache:
        return cache[key]
    info = None
    for q in titles:
        if not q:
            continue
        results = await search_tv(session, q)
        cands = [r for r in results if 16 in (r.get("genre_ids") or []) and r.get("poster_path")]
        if not cands:
            continue
        best = None
        for r in cands:
            if norm(r.get("name")) == norm(q) or norm(r.get("original_name")) == norm(q):
                best = r
                break
        best = best or cands[0]
        genre_names = [GENRE_MAP.get(g) for g in best.get("genre_ids", []) if GENRE_MAP.get(g)]
        country_names = [COUNTRY_MAP.get(c, c) for c in best.get("origin_country", [])]
        rating = 0
        if best.get("vote_count"):
            try:
                rating = round(float(best.get("vote_average") or 0), 1)
            except (TypeError, ValueError):
                rating = 0
        info = {
            "tmdbId": best["id"],
            "mediaType": "tv",
            "tmdbTitle": best.get("name") or q,
            "releaseDate": best.get("first_air_date") or "",
            "posterPath": best.get("poster_path"),
            "backdropPath": best.get("backdrop_path"),
            "rating": rating,
            "popularity": round(float(best.get("popularity") or 0), 1),
            "overview": best.get("overview") or "暂无简介",
            "regionTitle": "/".join(country_names) if country_names else "",
            "genreTitle": " / ".join(genre_names[:3]) if genre_names else "动画",
        }
        break
    cache[key] = info
    return info


async def main():
    if not TMDB_API_KEY:
        print("❌ 错误: 未检测到 TMDB_API_KEY 环境变量！")
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    items = []
    cache = {}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get("https://api.jikan.moe/v4/top/anime", params={"filter": "airing", "limit": 25}, headers=HEADERS) as resp:
                if resp.status != 200:
                    print(f"❌ Jikan 返回 {resp.status}")
                    raw_list = []
                else:
                    raw_list = (await resp.json()).get("data") or []
        except Exception as e:
            print(f"❌ Jikan 请求异常: {e}")
            raw_list = []
        for raw in raw_list:
            titles = [raw.get("title_english"), raw.get("title"), raw.get("title_japanese")]
            info = await match_anime(session, titles, cache)
            if not info:
                print(f"⚠️ 丢弃无法匹配的条目: {titles[0] or titles[1]}")
                continue
            items.append({
                "title": info["tmdbTitle"],
                "rank": len(items) + 1,
                "category": "tv",
                "heat": raw.get("members") or 0,
                "malScore": raw.get("score") or 0,
                "malTitle": raw.get("title_english") or raw.get("title") or "",
                **info,
            })
            await asyncio.sleep(0.4)  # Jikan 限流礼貌间隔
    final_result = {
        "source": "MyAnimeList",
        "last_updated": datetime.datetime.now(tz_bj).strftime("%Y-%m-%d %H:%M:%S"),
        "airing": items,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_result, f, ensure_ascii=False, indent=2)
    print(f"\n🎉 全部完成！共 {len(items)}/{len(raw_list)} 条匹配成功，数据已保存至 {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
