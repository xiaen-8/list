#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""优酷片库数据源（xiaen-8/list）

来源：移植 MakkaPakka518 的 youku-worker.js —— 优酷 mtop 首页接口
mtop.youku.columbus.home.query（appKey 24679788，_m_h5_tk 签名），
按分类（电视剧/电影/综艺/动漫/纪录片/少儿）取前 2 页片库列表。
TMDB 富化：片名精确匹配且全局唯一才收（优酷不给年份，只能靠唯一性把关）。
密钥：TMDB_API_KEY（仓库 secrets 已有）。
输出：data/youku.json
"""
import hashlib, time, re, urllib.request, urllib.parse, urllib.error

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
    try:
        async with session.get(url, params=tmdb_params({"query": title}), headers=tmdb_headers(), timeout=aiohttp.ClientTimeout(total=30)) as r:
            if r.status != 200: return []
            d = await r.json()
            return d.get("results") or []
    except Exception:
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

APP = "24679788"
API = "mtop.youku.columbus.home.query"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
CATS = {"tv": "电视剧", "movie": "电影", "show": "综艺", "anime": "动漫", "doc": "纪录片", "kids": "少儿"}
TOK = {"tk": "", "ck": ""}

def _sysinfo():
    return json.dumps({"device":"pcweb","os":"pcweb","ver":"1.0.0.0","userAgent":UA,"guid":"1590141704165YXe","appPackageKey":"com.youku.pcweb","young":0,"brand":"","network":"","ouid":"","idfa":"","scale":"","operator":"","resolution":"","pid":"36370125020498","appkey":APP,"childGender":0,"disableUserRec":"0"}, separators=(",", ":"), ensure_ascii=False)

def _raw(data):
    t = str(int(time.time() * 1000))
    sign = hashlib.md5(f"{TOK['tk']}&{t}&{APP}&{data}".encode()).hexdigest()
    url = (f"https://acs.youku.com/h5/{API}/1.0/?jsv=2.6.1&appKey={APP}&t={t}&sign={sign}"
           f"&api={API}&v=1.0&dataType=json&type=originaljson&data=" + urllib.parse.quote(data))
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.youku.com/ku/webtv", "Cookie": TOK["ck"]})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            sc = r.headers.get_all("Set-Cookie") or []
            body = r.read().decode()
    except urllib.error.HTTPError as e:
        sc = e.headers.get_all("Set-Cookie") or []
        body = e.read().decode()
    joined = "|".join(sc)
    m1 = re.search(r"_m_h5_tk=([^;|,]+)", joined)
    m2 = re.search(r"_m_h5_tk_enc=([^;|,]+)", joined)
    if m1:
        TOK["tk"] = m1.group(1).split("_")[0]
        TOK["ck"] = f"_m_h5_tk={m1.group(1)}; _m_h5_tk_enc={m2.group(1) if m2 else ''}"
    try: return json.loads(body)
    except Exception: return None

def _call(data):
    j = _raw(data)
    if j and j.get("ret") and "TOKEN" in str(j["ret"][0]):
        _raw(data); j = _raw(data)
    return j

def _collect(o, out):
    if not isinstance(o, (dict, list)): return
    if isinstance(o, list):
        for x in o: _collect(x, out)
        return
    d = o.get("data")
    if isinstance(d, dict) and d.get("title") and d.get("action") and (d.get("img") or d.get("hImg")):
        out.append(str(d["title"]))
    for k, v in o.items():
        if k in ("action", "trackShow", "report"): continue
        _collect(v, out)

def _find_session(o):
    if not isinstance(o, (dict, list)): return None
    if isinstance(o, dict):
        s = o.get("session")
        if isinstance(s, dict) and "subIndex" in s and s.get("id"): return s
        for v in o.values():
            r = _find_session(v)
            if r: return r
    else:
        for v in o:
            r = _find_session(v)
            if r: return r
    return None

def fetch_category(cat_cn, pages=2):
    inner = {"utdid":"FE1oI9Z/92QCAWeOjJcxfUHN","gray":"0","pageNo":1,"debug":0,"bizKey":"kuflix_pc_home",
             "appPackageKey":"com.youku.pcweb","showNodeList":1,"reqSubNode":1,"nodeKey":"ALL","ip":"127.0.0.1",
             "bizContext": json.dumps({"filterParam": f"type:{cat_cn}", "category": cat_cn, "optionRefresh": 1}, ensure_ascii=False)}
    pack = lambda obj: json.dumps({"ms_codes":"2019061000","params":json.dumps(obj, ensure_ascii=False),"system_info":_sysinfo()}, ensure_ascii=False)
    titles, seen = [], set()
    j = _call(pack(inner))
    out = []; _collect(j, out)
    for t in out:
        if t not in seen: seen.add(t); titles.append(t)
    sess = _find_session(j)
    for p in range(2, pages + 1):
        if not sess: break
        inner2 = dict(inner, pageNo=p, reqSubNode=0, showNodeList=0, session=json.dumps(sess))
        j2 = _call(pack(inner2))
        o2 = []; _collect(j2, o2)
        if not o2: break
        for t in o2:
            if t not in seen: seen.add(t); titles.append(t)
        sess = _find_session(j2)
    return titles

async def main():
    result = {"last_updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    async with aiohttp.ClientSession() as session:
        for key, cat_cn in CATS.items():
            titles = fetch_category(cat_cn)
            items = []
            for t in titles:
                it = await strict_match(session, t)
                if it: items.append(it)
            result[key] = items
            print(f"youku {cat_cn}: raw {len(titles)} -> matched {len(items)}", flush=True)
    os.makedirs("data", exist_ok=True)
    with open("data/youku.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("saved data/youku.json")

if __name__ == "__main__":
    asyncio.run(main())
