"""B站榜单（PGC 番剧榜 / 国创榜），写入 data/bili.json，结构与 VPS 版 bili.json 一致：
{last_updated, bangumi, guochuang}。
抓 api.bilibili.com/pgc/web/rank/list（day=3），逐条清洗标题后回 TMDB 匹配动画（题材须含 16）。
密钥：TMDB_API_KEY（仓库 secrets 已有，仅用于匹配）。"""
import asyncio
import aiohttp
import json
import os
import re
import datetime

TMDB_API_KEY = os.environ.get('TMDB_API_KEY')
DATA_DIR = "data"
OUTPUT_FILE = os.path.join(DATA_DIR, "bili.json")
MAX_PER_CAT = 50

tz_bj = datetime.timezone(datetime.timedelta(hours=8))

CATEGORIES = [
    ("番剧榜", "bangumi", 1),
    ("国创榜", "guochuang", 4),
]

GENRE_MAP = {28: "动作", 12: "冒险", 16: "动画", 35: "喜剧", 80: "犯罪", 99: "纪录片", 18: "剧情", 10751: "家庭", 14: "奇幻", 36: "历史", 27: "恐怖", 10402: "音乐", 9648: "悬疑", 10749: "爱情", 878: "科幻", 10770: "电视电影", 53: "惊悚", 10752: "战争", 37: "西部", 10759: "动作冒险", 10762: "儿童", 10763: "新闻", 10764: "真人秀", 10765: "科幻奇幻", 10766: "肥皂剧", 10767: "脱口秀", 10768: "战争政治"}
COUNTRY_MAP = {"CN": "中国大陆", "JP": "日本", "KR": "韩国", "US": "美国", "GB": "英国", "TW": "中国台湾", "HK": "中国香港"}

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}
BILI_HEADERS = {**HEADERS, "Referer": "https://www.bilibili.com/", "Accept-Language": "zh-CN,zh;q=0.9"}

BRACKET_L = chr(0x3010)
BRACKET_R = chr(0x3011)


def clean_title(title):
    t = str(title or "").strip()
    t = re.sub(r"\(.*?\)", "", t)
    t = re.sub(r"（.*?）", "", t)
    t = re.sub(BRACKET_L + ".*?" + BRACKET_R, "", t)
    t = re.sub(r"\[.*?\]", "", t)
    t = re.sub(r"第[一二三四五六七八九十0-9]+[季期部]", "", t)
    t = re.sub(r"(?i)season\s*\d+", "", t)
    t = t.replace("年番", "").replace("特别篇", "")
    return re.sub(r"\s+", " ", t).strip()


def norm(s):
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


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


async def match_anime(session, raw_title, cache):
    clean = clean_title(raw_title)
    if not clean:
        return None
    if clean in cache:
        return cache[clean]
    results = await search_tv(session, clean)
    cands = [r for r in results if 16 in (r.get("genre_ids") or []) and r.get("poster_path")]
    best = None
    for r in cands:
        if norm(r.get("name")) == norm(clean) or norm(r.get("original_name")) == norm(clean):
            best = r
            break
    if not best and cands:
        best = cands[0]
    info = None
    if best:
        genre_names = [GENRE_MAP.get(g) for g in best.get("genre_ids", []) if GENRE_MAP.get(g)]
        country_names = [COUNTRY_MAP.get(c, c) for c in best.get("origin_country", [])]
        rating = 0
        if best.get("vote_count"):
            try:
                rating = round(float(best.get("vote_average") or 0), 1)
            except (TypeError, ValueError):
                rating = 0
        info = {
            "id": best["id"],
            "tmdbId": best["id"],
            "type": "tmdb",
            "mediaType": "tv",
            "tmdbTitle": best.get("name") or clean,
            "releaseDate": best.get("first_air_date") or "",
            "lastUpdateDate": best.get("first_air_date") or "",
            "posterPath": best.get("poster_path"),
            "backdropPath": best.get("backdrop_path"),
            "rating": rating,
            "voteCount": best.get("vote_count") or 0,
            "popularity": round(float(best.get("popularity") or 0), 1),
            "description": best.get("overview") or "暂无简介",
            "regionTitle": "/".join(country_names) if country_names else "",
            "genreTitle": " / ".join(genre_names[:3]) if genre_names else "动画",
        }
    cache[clean] = info
    return info


async def fetch_category(session, cat_name, season_type, cache):
    url = "https://api.bilibili.com/pgc/web/rank/list"
    try:
        async with session.get(url, params={"day": 3, "season_type": season_type}, headers=BILI_HEADERS) as resp:
            if resp.status != 200:
                print(f"❌ [{cat_name}] B站返回 {resp.status}")
                return []
            data = await resp.json()
    except Exception as e:
        print(f"❌ [{cat_name}] 请求异常: {e}")
        return []
    raw_list = ((data.get("result") or {}).get("list") or [])[:MAX_PER_CAT]
    items = []
    for raw in raw_list:
        info = await match_anime(session, raw.get("title", ""), cache)
        if not info:
            continue
        stat = raw.get("stat") or {}
        items.append({
            "title": info["tmdbTitle"],
            "rank": len(items) + 1,
            "category": "tv",
            "heat": stat.get("view") or 0,
            "biliTitle": raw.get("title", ""),
            "newEp": (raw.get("new_ep") or {}).get("index_show") or "",
            **info,
        })
        await asyncio.sleep(0.15)
    print(f"✅ [{cat_name}] {len(items)}/{len(raw_list)} 条匹配成功")
    return items


async def main():
    if not TMDB_API_KEY:
        print("❌ 错误: 未检测到 TMDB_API_KEY 环境变量！")
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    result = {"last_updated": datetime.datetime.now(tz_bj).strftime("%Y-%m-%d %H:%M:%S")}
    cache = {}
    async with aiohttp.ClientSession() as session:
        for cat_name, key, season_type in CATEGORIES:
            result[key] = await fetch_category(session, cat_name, season_type, cache)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    total = sum(len(result[k]) for _, k, _ in CATEGORIES)
    print(f"\n🎉 全部完成！共 {total} 条，数据已保存至 {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
