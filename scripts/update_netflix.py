"""Netflix 周榜（tudum Top10 官方页）：英语电影 / 非英语电影 / 英语剧集 / 非英语剧集，
写入 data/netflix-weekly.json。字段与现有 data/*-hot.json 兼容，widget 端复用同一套 toVideo/sortItems。

逻辑移植自 Rex首页（rex.home.unified）的 Netflix 周榜实现：
- 抓 https://www.netflix.com/tudum/top10/<path> 页面，正则抽出 reactContext 里嵌的 GraphQL JSON；
- 只取 __typename == PulseTop10ItemEntity 且 category 对应的条目，锁定最新 weekEndDate，取 weeklyRank 1–10；
- 每条按「片名 + 年份」回 TMDB 唯一匹配（搜索结果多于一页、或同名同年多于一个 id、或带纪录片题材 99、无海报 → 丢弃），
  宁可少出，不拿别的片顶替。
密钥：TMDB_API_KEY（仓库 secrets 已有）。"""
import asyncio
import aiohttp
import json
import os
import re
import datetime

TMDB_API_KEY = os.environ.get('TMDB_API_KEY')
DATA_DIR = "data"
OUTPUT_FILE = os.path.join(DATA_DIR, "netflix-weekly.json")

tz_bj = datetime.timezone(datetime.timedelta(hours=8))

# (分类名, tudum 路径后缀, GraphQL category, 媒体类型) —— 与 RexHomeNetflixFeeds 一一对应
FEEDS = [
    ("英语电影", "", "ENGLISH_MOVIES", "movie"),
    ("非英语电影", "films-non-english", "NONENGLISH_MOVIES", "movie"),
    ("英语剧集", "tv", "ENGLISH_SERIES", "tv"),
    ("非英语剧集", "tv-non-english", "NONENGLISH_SERIES", "tv"),
]

GENRE_MAP = {28: "动作", 12: "冒险", 16: "动画", 35: "喜剧", 80: "犯罪", 99: "纪录片", 18: "剧情", 10751: "家庭", 14: "奇幻", 36: "历史", 27: "恐怖", 10402: "音乐", 9648: "悬疑", 10749: "爱情", 878: "科幻", 10770: "电视电影", 53: "惊悚", 10752: "战争", 37: "西部", 10759: "动作冒险", 10762: "儿童", 10763: "新闻", 10764: "真人秀", 10765: "科幻奇幻", 10766: "肥皂剧", 10767: "脱口秀", 10768: "战争政治"}
COUNTRY_MAP = {"CN": "中国大陆", "JP": "日本", "KR": "韩国", "US": "美国", "GB": "英国", "TW": "中国台湾", "HK": "中国香港"}

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}


def unescape_js(s):
    """移植 Rex 的转义还原：\\x## / \\u#### / 常规转义，其余原样"""
    def rep(m):
        e = m.group(1)
        if e[0] in ("x", "u"):
            try:
                return chr(int(e[1:], 16))
            except ValueError:
                return e
        table = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
        return table.get(e, e)
    return re.sub(r"\\(x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|.)", rep, s)


def parse_top10(html, category):
    """抽出指定 category 最新一周的 Top10 条目（移植 rexHomeNetflixParse）"""
    m = re.search(r"netflix\.reactContext\.models\.graphql\s*=\s*JSON\.parse\('((?:\\.|[^'\\])*)'\)", html or "")
    if not m:
        raise ValueError("Netflix榜单结构已变化，未找到 reactContext 数据")
    data = json.loads(unescape_js(m.group(1))).get("data")
    if not isinstance(data, dict):
        raise ValueError("Netflix榜单数据格式异常")
    entities = [
        v for v in data.values()
        if isinstance(v, dict) and v.get("__typename") == "PulseTop10ItemEntity"
        and isinstance(v.get("top10"), dict) and v["top10"].get("category") == category
        and v.get("top10Video")
    ]
    weeks = sorted({
        v["top10"].get("weekEndDate", "")
        for v in entities
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v["top10"].get("weekEndDate", "")))
    })
    if not weeks:
        return []
    latest = weeks[-1]
    seen, out = set(), []
    for v in entities:
        t = v["top10"]
        vid = t.get("videoId")
        rank = t.get("weeklyRank")
        if t.get("weekEndDate") != latest or not vid or vid in seen:
            continue
        if not isinstance(rank, int) or rank < 1 or rank > 10:
            continue
        seen.add(vid)
        out.append(v)
    out.sort(key=lambda v: v["top10"]["weeklyRank"])
    return out


def norm_title(s):
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def auth():
    headers = {"accept": "application/json"}
    params = {}
    if TMDB_API_KEY and TMDB_API_KEY.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {TMDB_API_KEY}"
    elif TMDB_API_KEY:
        params["api_key"] = TMDB_API_KEY
    return headers, params


async def match_tmdb(session, entry, media, feed_title):
    """按片名+年份唯一匹配 TMDB（移植 rexHomeNetflixMatch），匹配不上返回 None"""
    video = entry.get("top10Video") or {}
    parent = video.get("parentShow") or {}
    if media == "tv" and parent.get("title"):
        title = parent["title"]
        # parentShow 常常没有 releaseYear，年份要回退到条目本身（Rex 原版即如此回退）
        year = parent.get("releaseYear") or video.get("releaseYear")
    else:
        title = video.get("title")
        year = video.get("releaseYear")
    try:
        year = int(year)
    except (TypeError, ValueError):
        return None
    if not title or year < 1888:
        return None
    headers, params = auth()
    params.update({"query": title, "language": "zh-CN", "include_adult": "false", "page": 1})
    url = f"https://api.themoviedb.org/3/search/{media}"
    try:
        async with session.get(url, params=params, headers={**HEADERS, **headers}) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
    except Exception:
        return None
    # 注：Rex 原版还有「搜索结果多于一页直接丢弃」的规则，但实测对 Animals 这类
    # 通用片名会误杀（TMDB 搜出 29 页、正确条目就在第一条）。唯一性改由下方
    # 「片名+年份精确过滤后只剩一个 id」保证，不再用页数做预筛。
    target = norm_title(title)
    found = {}
    loose = {}
    for r in data.get("results", []):
        date = (r.get("release_date") if media == "movie" else r.get("first_air_date")) or ""
        names = [r.get("title"), r.get("original_title")] if media == "movie" else [r.get("name"), r.get("original_name")]
        if str(date)[:4] != str(year):
            continue
        if 99 in (r.get("genre_ids") or []):
            continue
        if not r.get("poster_path"):
            continue
        loose[r["id"]] = r
        if any(norm_title(n) == target for n in names if n):
            found[r["id"]] = r
    if len(found) != 1:
        # 兜底：非英语片的 tudum 标题是 Netflix 英文展示名，与 TMDB 原名/中文名都对不上
        # （如 Therapy Done Right ↔ Lo dejamos acá）。若整个搜索只有一个结果且同年，
        # 唯一性依然成立，接受它；其余情况照 Rex 的严规丢弃。
        if not found and (data.get("total_results") or 0) == 1 and len(loose) == 1:
            found = loose
        else:
            return None
    best = next(iter(found.values()))
    rank = entry["top10"]["weeklyRank"]
    week_end = entry["top10"].get("weekEndDate", "")
    genre_names = [GENRE_MAP.get(g) for g in best.get("genre_ids", []) if GENRE_MAP.get(g)]
    country_names = [COUNTRY_MAP.get(c, c) for c in best.get("origin_country", [])]
    rating = 0
    if best.get("vote_count"):
        try:
            rating = round(float(best.get("vote_average") or 0), 1)
        except (TypeError, ValueError):
            rating = 0
    zh_title = best.get("title") or best.get("name") or title
    return {
        "title": zh_title,
        "rank": rank,
        "category": media,
        "tmdbId": best["id"],
        "mediaType": media,
        "tmdbTitle": zh_title,
        "releaseDate": best.get("release_date") or best.get("first_air_date") or "",
        "posterPath": best.get("poster_path"),
        "backdropPath": best.get("backdrop_path"),
        "rating": rating,
        "popularity": round(float(best.get("popularity") or 0), 1),
        "overview": best.get("overview") or video.get("shortSynopsis") or "暂无简介",
        "regionTitle": "/".join(country_names) if country_names else "",
        "genreTitle": " / ".join(genre_names[:3]) if genre_names else "",
        "netflixTitle": title,
        "weekEndDate": week_end,
        "sourceRank": f"Netflix · {feed_title} · 第{rank}名",
    }


async def fetch_feed(session, feed_title, path, category, media):
    url = f"https://www.netflix.com/tudum/top10/{path}"
    try:
        async with session.get(url, headers=HEADERS) as resp:
            if resp.status != 200:
                print(f"❌ [{feed_title}] tudum 返回 {resp.status}")
                return []
            html = await resp.text()
    except Exception as e:
        print(f"❌ [{feed_title}] 请求异常: {e}")
        return []
    try:
        entries = parse_top10(html, category)
    except ValueError as e:
        print(f"❌ [{feed_title}] {e}")
        return []
    items = []
    for entry in entries:
        item = await match_tmdb(session, entry, media, feed_title)
        if item:
            items.append(item)
        else:
            v = (entry.get("top10Video") or {}).get("title", "?")
            print(f"⚠️ [{feed_title}] 丢弃无法唯一匹配的条目: {v}")
        await asyncio.sleep(0.2)
    print(f"✅ [{feed_title}] {len(items)}/{len(entries)} 条匹配成功")
    return items


async def main():
    if not TMDB_API_KEY:
        print("❌ 错误: 未检测到 TMDB_API_KEY 环境变量！")
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    categories = {}
    async with aiohttp.ClientSession() as session:
        for feed in FEEDS:
            categories[feed[0]] = await fetch_feed(session, *feed)
    final_result = {
        "source": "Netflix",
        "last_updated": datetime.datetime.now(tz_bj).strftime("%Y-%m-%d %H:%M:%S"),
        "categories": categories,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_result, f, ensure_ascii=False, indent=2)
    total = sum(len(v) for v in categories.values())
    print(f"\n🎉 全部完成！共 {total} 条，数据已保存至 {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
