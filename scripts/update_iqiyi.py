#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""爱奇艺片库数据源（xiaen-8/list）

来源：爱奇艺 pcw-api 片库推荐接口 search/recommend/list
（channel_id：1 电影 / 2 电视剧 / 3 纪录片 / 4 动漫；mode=11 片库模式），
每频道取前 2 页（每页 30）。
TMDB 富化：片名 + 年份（period）唯一匹配才收，宁缺毋滥。
密钥：TMDB_API_KEY（仓库 secrets 已有）。
输出：data/iqiyi.json
"""

import asyncio, json, os, datetime
import aiohttp

TMDB_API_KEY = os.environ.get("TMDB_API_KEY") or ""
GENRE_MAP = {28:"动作",12:"冒险",16:"动画",35:"喜剧",80:"犯罪",99:"纪录片",18:"剧情",10751:"家庭",14:"奇幻",36:"历史",27:"恐怖",10402:"音乐",9648:"悬疑",10749:"爱情",878:"科幻",10770:"电视电影",53:"惊悚",10752:"战争",37:"西部",10759:"动作冒险",10762:"儿童",10763:"新闻",10764:"真人秀",10765:"科幻奇幻",10766:"肥皂剧",10767:"脱口秀",10768:"战争政治"}

def tmdb_headers():
    if TMDB_API_KEY.startswith("eyJ"):
        return {"Authorization": "Bearer " + TMDB_API_KEY, "accept": "application/json"}
    return {"accept": "application/json"}

def tmdb_params(extra=None):
    p = {"language": "zh-CN"}
    if TMDB_API_KEY and not TMDB_API_KEY.startswith("eyJ"):
        p["api_key"] = TMDB_API_KEY
    if extra: p.update(extra)
    return p

def build_item(hit, media):
    rd = hit.get("release_date") or hit.get("first_air_date") or ""
    genres = ",".join(GENRE_MAP.get(g, "") for g in (hit.get("genre_ids") or []) if GENRE_MAP.get(g))
    return {
        "id": str(hit.get("id")), "type": "tmdb",
        "title": hit.get("title") or hit.get("name") or "",
        "description": hit.get("overview") or "",
        "rating": hit.get("vote_average") or 0,
        "voteCount": hit.get("vote_count") or 0,
        "popularity": hit.get("popularity") or 0,
        "releaseDate": rd, "lastUpdateDate": rd,
        "posterPath": hit.get("poster_path") or "",
        "backdropPath": hit.get("backdrop_path") or "",
        "mediaType": media, "genreTitle": genres,
    }

async def tmdb_search(session, media, title):
    url = f"https://api.themoviedb.org/3/search/{media}"
    for attempt in range(3):
        try:
            async with session.get(url, params=tmdb_params({"query": title}), headers=tmdb_headers(), timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    d = await r.json()
                    return d.get("results") or []
        except Exception:
            pass
        await asyncio.sleep(1.5 * (attempt + 1))
    return []

async def strict_match(session, title, year=None):
    """片名精确相等（译名或原名）且全局唯一才收；给了年份还须年份一致。宁缺毋滥。"""
    cands = []
    for media in ("tv", "movie"):
        for hit in await tmdb_search(session, media, title):
            names = [hit.get("title"), hit.get("name"), hit.get("original_title"), hit.get("original_name")]
            if title not in [n for n in names if n]: continue
            if year:
                rd = hit.get("release_date") or hit.get("first_air_date") or ""
                if not rd.startswith(str(year)): continue
            cands.append((media, hit))
    if len(cands) != 1: return None
    media, hit = cands[0]
    if not hit.get("poster_path"): return None
    return build_item(hit, media)

CHANS = {"movie": 1, "tv": 2, "doc": 3, "anime": 4}
IQIYI_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

async def fetch_channel(session, chid):
    rows, seen = [], set()
    for page in (1, 2):
        url = "https://pcw-api.iqiyi.com/search/recommend/list"
        params = {"channel_id": chid, "data_type": 1, "mode": 11, "page_id": page, "page_docs": 1, "ret_num": 30}
        try:
            async with session.get(url, params=params, headers={"User-Agent": IQIYI_UA}, timeout=aiohttp.ClientTimeout(total=30)) as r:
                d = await r.json(content_type=None)
        except Exception:
            break
        lst = ((d.get("data") or {}).get("list")) or []
        if not lst: break
        for it in lst:
            name = (it.get("name") or "").strip()
            if not name or name in seen: continue
            seen.add(name)
            period = str(it.get("period") or "")
            year = int(period[:4]) if period[:4].isdigit() else None
            rows.append((name, year))
    return rows

async def main():
    result = {"last_updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    async with aiohttp.ClientSession() as session:
        for key, chid in CHANS.items():
            rows = await fetch_channel(session, chid)
            items = []
            for name, year in rows:
                it = await strict_match(session, name, year)
                if it: items.append(it)
            result[key] = items
            print(f"iqiyi ch{chid} ({key}): raw {len(rows)} -> matched {len(items)}", flush=True)
    os.makedirs("data", exist_ok=True)
    with open("data/iqiyi.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("saved data/iqiyi.json")

if __name__ == "__main__":
    asyncio.run(main())
