#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""芒果TV片库数据源（xiaen-8/list）

来源：移植 MakkaPakka518 的 mgtv.video.pc.makka 模块 —— 芒果 pianku 接口
rider/list/pcweb/v3，六个频道（电视剧/电影/综艺/动漫/纪录片/少儿）各按
模块默认排序取前 3 页。TMDB 富化：片名精确匹配且全局唯一才收（有年份线索
时还须年份一致），宁缺毋滥。
密钥：TMDB_API_KEY（仓库 secrets 已有）。
输出：data/mgtvpc.json（与 data/mgtv.json 的热榜互不冲突）
"""
import asyncio, json, os, re, datetime
import aiohttp

TMDB_API_KEY = __import__("os").environ.get("TMDB_API_KEY", "")
GENRE_MAP = {28:"动作",12:"冒险",16:"动画",35:"喜剧",80:"犯罪",99:"纪录片",18:"剧情",10751:"家庭",14:"奇幻",36:"历史",27:"恐怖",10402:"音乐",9648:"悬疑",10749:"爱情",878:"科幻",10770:"电视电影",53:"惊悚",10752:"战争",37:"西部",10759:"动作冒险",10762:"儿童",10763:"新闻",10764:"真人秀",10765:"科幻奇幻",10766:"肥皂剧",10767:"脱口秀",10768:"战争政治"}
MG_BASE = "https://pianku.api.mgtv.com/rider/list/pcweb/v3"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

# key -> (channelId, 默认排序, 默认筛选)，与 Makka 模块各频道缺省一致
CHANNELS = {
    "tv":    (2,  "c2", {"kind": "a1", "area": "a1", "year": "all", "feature": "all", "chargeInfo": "a1"}),
    "movie": (3,  "c2", {"kind": "a1", "edition": "a1", "area": "a1", "year": "all", "chargeInfo": "a1"}),
    "show":  (1,  "c2", {"kind": "a1", "chargeInfo": "a1"}),
    "anime": (50, "c1", {"kind": "a1", "area": "a1", "edition": "a1"}),
    "doc":   (51, "c1", {"kind": "a1"}),
    "kids":  (10, "c1", {"kind": "a1", "fitAge": "a1"}),
}
CAT_CN = {"tv": "电视剧", "movie": "电影", "show": "综艺", "anime": "动漫", "doc": "纪录片", "kids": "少儿"}

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

def clean_title(raw):
    t = raw.strip()
    t = re.sub(r"第[一二三四五六七八九十百\d]+[季期部章]", "", t)
    t = re.sub(r"(?i)Season\s*\d+", "", t)
    t = re.sub(r"\(.*?\)|（.*?）|\[.*?\]|【.*?】", "", t)
    return re.sub(r"\s+", " ", t).strip()

def split_year(raw_title, subtitle):
    """从副标题/标题里提取年份线索，并把标题里的年份剥掉。"""
    year = None
    m = re.search(r"\b(20\d{2})\b", subtitle or "")
    if m: year = m.group(1)
    title = clean_title(raw_title)
    if not year:
        m2 = re.search(r"\b(20\d{2})\b", raw_title)
        if m2:
            year = m2.group(1)
            title = title.replace(year, "").strip()
    return title, year

async def fetch_channel(session, key, pages=3):
    cid, sort, filters = CHANNELS[key]
    seen, out = set(), []
    headers = {"User-Agent": UA, "Referer": "https://www.mgtv.com/"}
    for page in range(1, pages + 1):
        params = {"allowedRC": "1", "platform": "pcweb", "channelId": cid,
                  "pn": page, "pc": 24, "hudong": 1, "_support": "10000000", "sort": sort}
        params.update(filters)
        try:
            async with session.get(MG_BASE, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status != 200: break
                data = await r.json()
        except Exception:
            break
        docs = (data.get("data") or {}).get("hitDocs") or []
        if not docs: break
        for doc in docs:
            raw = (doc.get("title") or "").strip()
            if not raw: continue
            title, year = split_year(raw, doc.get("subtitle") or "")
            if not title or title in seen: continue
            seen.add(title)
            out.append((title, year))
        await asyncio.sleep(0.4)
    return out

async def main():
    result = {"last_updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    async with aiohttp.ClientSession() as session:
        for key in CHANNELS:
            pairs = await fetch_channel(session, key)
            items = []
            for title, year in pairs:
                it = await strict_match(session, title, year)
                if it: items.append(it)
            result[key] = items
            print(f"mgtvpc {CAT_CN[key]}: raw {len(pairs)} -> matched {len(items)}", flush=True)
    os.makedirs("data", exist_ok=True)
    with open("data/mgtvpc.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("saved data/mgtvpc.json")

if __name__ == "__main__":
    asyncio.run(main())
