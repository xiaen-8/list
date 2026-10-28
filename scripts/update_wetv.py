#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""腾讯 WeTV 片库数据源（xiaen-8/list）

来源：WeTV（wetv.vip，腾讯视频海外版）简体站频道页 SSR：
https://wetv.vip/zh-cn?id=<频道id> 的 __NEXT_DATA__ 里带各模块片单
（电视剧 10259 / 电影 10261 / 综艺 10042 / 动漫 10262），每频道取前 60 个去重标题。
标题先做确定性清洗（去「（未剪辑版）」类括号后缀、去「第X季/第X部」尾巴）再匹配。
TMDB 富化：片名精确匹配且全局唯一才收，宁缺毋滥（与优酷源同口径）。
密钥：TMDB_API_KEY（仓库 secrets 已有）。
输出：data/wetv.json
"""
import re, urllib.request, urllib.error

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

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
CHANS = {"tv": "10259", "movie": "10261", "show": "10042", "anime": "10262"}
MAX_TITLES = 60

def clean_title(t):
    t = (t or "").strip()
    t = re.sub(r"[（(][^）)]*[）)]\s*$", "", t).strip()          # 去尾部括号后缀（未剪辑版 等）
    t = re.sub(r"第[一二三四五六七八九十0-9]+[季部]\s*$", "", t).strip()  # 去季/部尾巴
    return t

def fetch_channel(chid):
    url = f"https://wetv.vip/zh-cn?id={chid}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        html = r.read().decode("utf-8", "ignore")
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m: return []
    data = json.loads(m.group(1))["props"]["pageProps"]["data"]
    titles, seen = [], set()
    for mod in data.get("modules") or []:
        name = mod.get("name") or ""
        if "筛选" in name or "标签" in name: continue
        for it in mod.get("items") or []:
            t = clean_title(it.get("title"))
            if not t or t in seen: continue
            if re.fullmatch(r"\d{4}", t): continue
            seen.add(t); titles.append(t)
            if len(titles) >= MAX_TITLES: return titles
    return titles

async def main():
    result = {"last_updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    async with aiohttp.ClientSession() as session:
        for key, chid in CHANS.items():
            titles = fetch_channel(chid)
            items = []
            for t in titles:
                it = await strict_match(session, t)
                if it: items.append(it)
            result[key] = items
            print(f"wetv {key} (ch {chid}): raw {len(titles)} -> matched {len(items)}", flush=True)
    os.makedirs("data", exist_ok=True)
    with open("data/wetv.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("saved data/wetv.json")

if __name__ == "__main__":
    asyncio.run(main())
