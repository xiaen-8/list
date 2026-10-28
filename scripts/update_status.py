"""生成全站榜单（GitHub 版）状态页 status.html：
扫描 data/*.json，统计每个源的条数与文件内 last_updated，渲染成与 NAS 版面板
同款的深色「各源状态」表格。由 update-status.yml 在数据文件变更后自动运行，
也可以本地手动跑：python scripts/update_status.py"""
import json
import os
import datetime

tz_bj = datetime.timezone(datetime.timedelta(hours=8))
DATA_DIR = "data"
OUTPUT_HTML = "status.html"

SOURCES = [
    ("guduo.json", "骨朵热度"),
    ("douban.json", "豆瓣热榜"),
    ("mgtv.json", "芒果TV"),
    ("theater.json", "剧场平台"),
    ("bangumi.json", "热门番剧"),
    ("tmdb.json", "TMDB 榜单"),
    ("bili.json", "B站榜单"),
    ("mal.json", "MAL 热播"),
    ("anilist.json", "AniList 番剧"),
    ("trakt.json", "Trakt 榜单"),
    ("netflix-weekly.json", "Netflix 周榜"),
    ("youku.json", "优酷片库"),
    ("iqiyi.json", "爱奇艺片库"),
    ("wetv.json", "腾讯WeTV"),
    ("mgtvpc.json", "芒果片库"),
]


def count_entries(node):
    """递归统计条目数：列表里带 title 的 dict 算一条"""
    n = 0
    if isinstance(node, list):
        for el in node:
            if isinstance(el, dict) and ("title" in el or "tmdbId" in el):
                n += 1
            else:
                n += count_entries(el)
    elif isinstance(node, dict):
        for v in node.values():
            n += count_entries(v)
    return n


def main():
    rows = []
    for fname, title in SOURCES:
        path = os.path.join(DATA_DIR, fname)
        count, updated, ok = 0, "-", False
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                count = count_entries(data)
                updated = data.get("last_updated") or data.get("updated_at") or "-"
                ok = count > 0
            except Exception:
                updated = "文件损坏"
        rows.append({"title": title, "file": fname, "count": count, "updated": updated, "ok": ok})
        print(f"{'✅' if ok else '⚠️'} {title}: {count} 条, 更新于 {updated}")

    now = datetime.datetime.now(tz_bj).strftime("%Y-%m-%d %H:%M:%S")
    trs = "\n".join(
        f'''<tr><td>{r["title"]}</td><td><span class="badge {'ok' if r["ok"] else 'bad'}">{'正常' if r["ok"] else '无数据'}</span></td><td>{r["count"]}</td><td>{r["updated"]}</td></tr>'''
        for r in rows
    )
    payload = json.dumps({"generated_at": now, "sources": rows}, ensure_ascii=False)
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>全站榜单 · 各源状态</title>
<style>
  body {{ background:#0d1117; color:#e6edf3; font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif; margin:0; padding:24px 16px; }}
  .card {{ max-width:760px; margin:0 auto; background:#151a23; border:1px solid #232a35; border-radius:14px; padding:20px; }}
  h1 {{ font-size:18px; margin:0 0 4px; }}
  .sub {{ color:#8b949e; font-size:12px; margin-bottom:16px; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  th, td {{ text-align:left; padding:10px 8px; border-bottom:1px solid #232a35; }}
  th {{ color:#8b949e; font-weight:500; }}
  .badge {{ padding:2px 10px; border-radius:999px; font-size:12px; }}
  .badge.ok {{ background:#0f2e1d; color:#3fb950; }}
  .badge.bad {{ background:#2e1a0f; color:#d29922; }}
</style>
</head>
<body>
<div class="card">
  <h1>各源状态</h1>
  <div class="sub">全站榜单（GitHub 版）· 生成于 {now}（北京时间）</div>
  <table>
    <tr><th>数据源</th><th>状态</th><th>条数</th><th>上次抓取</th></tr>
    {trs}
  </table>
</div>
<script type="application/json" id="status-data">{payload}</script>
</body>
</html>
"""
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n🎉 状态页已生成: {OUTPUT_HTML}")


if __name__ == "__main__":
    main()
