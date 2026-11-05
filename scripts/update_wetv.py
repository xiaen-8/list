#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""腾讯 WeTV 片库数据源（xiaen-8/list）

来源：WeTV（wetv.vip，腾讯视频海外版）简体站频道页：
https://wetv.vip/zh-cn?id=<频道id>，旧版前端在 __NEXT_DATA__ 里带各模块片单，
新版前端（App Router）在 self.__next_f 的 RSC 流里带同一份 modules 数据，两种都解析。
频道 id（2026-11-02 换新，旧 id 10259/10261/10042/10262 已退役、只回空壳）：
电视剧 10314 / 电影 10316 / 综艺 10236 / 动漫 10317。每频道取前 60 个去重标题。
标题先做确定性清洗（去「（未剪辑版）」类括号后缀、去「第X季/第X部」尾巴）再匹配。
TMDB 富化：片名精确匹配且全局唯一才收，宁缺毋滥（与优酷源同口径）。
密钥：TMDB_API_KEY（仓库 secrets 已有）。
输出：data/wetv.json
"""
import re, urllib.request, urllib.error

import asyncio, json, os, datetime, time
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
CHANS = {"tv": "10314", "movie": "10316", "show": "10236", "anime": "10317"}
MAX_TITLES = 60

def clean_title(t):
    t = (t or "").strip()
    t = re.sub(r"｜.*$", "", t).strip()                          # 去「｜宣传语」尾巴（新版片单常见）
    t = re.sub(r"[（(][^）)]*[）)]\s*$", "", t).strip()          # 去尾部括号后缀（未剪辑版 等）
    t = re.sub(r"第[一二三四五六七八九十0-9]+[季部]\s*$", "", t).strip()  # 去季/部尾巴
    t = re.sub(r"\s*S\d+\s*$", "", t).strip()                   # 去 S4 这类季尾巴
    return t

def _titles_from_next_data(html):
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m: return []
    try:
        data = json.loads(m.group(1))["props"]["pageProps"]["data"]
    except Exception:
        return []
    if not isinstance(data, dict): return []
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

def _titles_from_flight(html):
    # 新版 App Router：片单在 self.__next_f.push 的 RSC 流字符串里，解码后结构同为 modules->items
    parts = re.findall(r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)', html, re.S)
    if not parts: return []
    dec = []
    for p in parts:
        try: dec.append(json.loads(p))
        except Exception: pass
    blob = "\n".join(dec)
    if '"isNewHomePage":true' in blob: return []  # 新版前端偶尔忽略 ?id= 直接回首页，首页推荐不能当频道片单
    titles, seen = [], set()
    for t in re.findall(r'"type":"ALBUM","title":"([^"]+)"', blob):
        t = clean_title(t)
        if not t or t in seen: continue
        if re.fullmatch(r"\d{4}", t): continue
        seen.add(t); titles.append(t)
        if len(titles) >= MAX_TITLES: break
    return titles

def fetch_channel(chid):
    # WeTV 新旧两版前端正在分流：同一地址可能回旧版 SSR 或新版 RSC 流，两种都解析；
    # 还会间歇回坏变体：__NEXT_DATA__ 里 data={} 的空壳页、或忽略 ?id= 的首页（RSC 流靠
    # isNewHomePage 守卫挡掉）。坏变体经 CloudFront 缓存约 68 秒（Cache-Control max-age=68），
    # 同一频道会在一两分钟窗口内持续回坏变体、过后自愈，四频道轮流中招（2026-11-05 实测：
    # 12:18 定时跑 movie/anime 空、午间复测又换 tv/show 空）。频道 id 未退役——每个频道都
    # 实测拿到过完整片单（tv 49 / movie 53 / show 30 / anime 30）。任一方式拿到片单即返回；
    # 单轮内递增间隔重试，都没拿到交给 main 的跨轮补抓（间隔须跨过缓存窗口）。
    url = f"https://wetv.vip/zh-cn?id={chid}"
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                html = r.read().decode("utf-8", "ignore")
            titles = _titles_from_next_data(html) or _titles_from_flight(html)
            if titles: return titles
            print(f"wetv ch {chid}: attempt {attempt + 1} got empty variant ({len(html)} bytes)", flush=True)
        except Exception as e:
            print(f"wetv ch {chid}: attempt {attempt + 1} error {type(e).__name__}", flush=True)
        time.sleep(4 + attempt * 2)
    return []

async def main():
    result = {"last_updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    # 先把四频道片单抓齐：首轮没拿到的频道等 75 秒、再 90 秒各补抓一轮，
    # 让重试跨过坏变体的 CDN 缓存窗口（约 68 秒），避免整频道 raw=0。
    titles_map = {}
    for key, chid in CHANS.items():
        titles_map[key] = fetch_channel(chid)
    for wait in (75, 90):
        missing = [(k, c) for k, c in CHANS.items() if not titles_map[k]]
        if not missing: break
        print(f"wetv refetch after {wait}s: {[k for k, _ in missing]}", flush=True)
        time.sleep(wait)
        for key, chid in missing:
            titles_map[key] = fetch_channel(chid)
    async with aiohttp.ClientSession() as session:
        for key, chid in CHANS.items():
            titles = titles_map[key]
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
