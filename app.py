import json
import os
import time
import hashlib
import re
import threading
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import urlparse

try:
    from flask import Flask, jsonify, render_template, request
    import feedparser
    import requests
except ImportError:
    raise SystemExit("请先运行 launch.py 安装依赖")

app = Flask(__name__)

# ---------- 配置 ----------
TRUSTED_DOMAINS = {
    "techcrunch.com", "theverge.com", "venturebeat.com",
    "technologyreview.com", "openai.com", "anthropic.com",
    "deepmind.google", "blog.google", "ai.google",
    "huggingface.co", "arxiv.org", "reuters.com",
    "bloomberg.com", "wired.com", "arstechnica.com",
    "zdnet.com", "theregister.com", "engadget.com",
}

RSS_FEEDS = [
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("The Verge", "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml"),
    ("VentureBeat AI", "https://venturebeat.com/category/ai/feed/"),
    ("MIT Tech Review", "https://www.technologyreview.com/topic/artificial-intelligence/feed"),
    ("Anthropic Blog", "https://www.anthropic.com/blog.rss"),
    ("OpenAI Blog", "https://openai.com/news/rss.xml"),
]

PRODUCTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "products.json")
CACHE_TTL = 3600  # 1 hour

# ---------- 缓存 ----------
_cache = {}
_cache_lock = threading.Lock()


def cache_get(key):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry["ts"]) < CACHE_TTL:
            return entry["data"]
    return None


def cache_set(key, data):
    with _cache_lock:
        _cache[key] = {"data": data, "ts": time.time()}


# ---------- 新闻抓取 ----------
def fetch_feed(name, url):
    items = []
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        feed = feedparser.parse(resp.content)
        for entry in feed.entries[:15]:
            link = getattr(entry, "link", "")
            domain = urlparse(link).netloc.replace("www.", "")
            if domain not in TRUSTED_DOMAINS:
                continue
            published = getattr(entry, "published_parsed", None)
            pub_ts = time.mktime(published) if published else time.time()
            summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
            # strip HTML tags
            summary = re.sub(r"<[^>]+>", "", summary).strip()[:500]
            items.append({
                "id": hashlib.md5((entry.title + link).encode()).hexdigest()[:8],
                "title_en": entry.title.strip(),
                "summary_en": summary,
                "link": link,
                "source": name,
                "domain": domain,
                "published_ts": pub_ts,
                "published_str": datetime.fromtimestamp(pub_ts).strftime("%m-%d %H:%M"),
            })
    except Exception as e:
        app.logger.warning(f"Feed {name} failed: {e}")
    return items


def is_similar(a, b, threshold=0.6):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() > threshold


def filter_and_dedupe(all_items):
    # sort by time desc
    all_items.sort(key=lambda x: x["published_ts"], reverse=True)
    # keep only today + yesterday
    cutoff = time.time() - 48 * 3600
    all_items = [i for i in all_items if i["published_ts"] > cutoff]
    # deduplicate by title similarity, track source count
    seen = []
    title_sources = {}  # id -> set of sources
    for item in all_items:
        dup = False
        for s in seen:
            if is_similar(item["title_en"], s["title_en"]):
                # merge sources
                title_sources[s["id"]].add(item["source"])
                dup = True
                break
        if not dup:
            seen.append(item)
            title_sources[item["id"]] = {item["source"]}
    # credibility: require 1+ trusted source (already guaranteed by domain whitelist)
    # boost score for multi-source
    for item in seen:
        sources = title_sources.get(item["id"], set())
        item["source_count"] = len(sources)
        item["all_sources"] = list(sources)
        item["credible"] = True  # domain-whitelist already filters
    return seen[:30]


def translate_batch(texts):
    try:
        from googletrans import Translator
        tr = Translator()
        results = []
        for text in texts:
            if not text:
                results.append("")
                continue
            try:
                result = tr.translate(text, dest="zh-cn", src="en")
                results.append(result.text)
            except Exception:
                results.append(text)
        return results
    except ImportError:
        return texts


def fetch_news():
    cached = cache_get("news")
    if cached:
        return cached

    all_items = []
    threads = []
    results = [[] for _ in RSS_FEEDS]

    def fetch_one(idx, name, url):
        results[idx] = fetch_feed(name, url)

    for i, (name, url) in enumerate(RSS_FEEDS):
        t = threading.Thread(target=fetch_one, args=(i, name, url))
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=15)

    for r in results:
        all_items.extend(r)

    filtered = filter_and_dedupe(all_items)

    # translate titles and summaries
    titles_en = [i["title_en"] for i in filtered]
    summaries_en = [i["summary_en"] for i in filtered]
    titles_zh = translate_batch(titles_en)
    summaries_zh = translate_batch(summaries_en)

    for i, item in enumerate(filtered):
        item["title_zh"] = titles_zh[i] if i < len(titles_zh) else item["title_en"]
        item["summary_zh"] = summaries_zh[i] if i < len(summaries_zh) else item["summary_en"]

    cache_set("news", filtered)
    return filtered


# ---------- 产品数据 ----------
def load_products():
    with open(PRODUCTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def get_products_with_news(news_items):
    products = load_products()
    # build set of news keywords
    news_text = " ".join(
        (i["title_en"] + " " + i["title_zh"]).lower() for i in news_items
    )
    for category, data in products.items():
        for product in data["products"]:
            has_news = any(kw.lower() in news_text for kw in product["keywords"])
            product["has_news"] = has_news
            if has_news:
                # find matching news
                matching = []
                for item in news_items:
                    combined = (item["title_en"] + " " + item["title_zh"]).lower()
                    if any(kw.lower() in combined for kw in product["keywords"]):
                        matching.append({"title_zh": item["title_zh"], "link": item["link"]})
                product["related_news"] = matching[:3]
            else:
                product["related_news"] = []
    return products


# ---------- Routes ----------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/flow")
def flow():
    flow_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "flow.html")
    with open(flow_path, encoding="utf-8") as f:
        return f.read()


@app.route("/api/news")
def api_news():
    try:
        news = fetch_news()
        return jsonify({"ok": True, "data": news, "date": datetime.now().strftime("%Y年%m月%d日")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/products")
def api_products():
    try:
        news = cache_get("news") or []
        products = get_products_with_news(news)
        return jsonify({"ok": True, "data": products})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/refresh")
def api_refresh():
    with _cache_lock:
        _cache.clear()
    return jsonify({"ok": True, "message": "缓存已清除，下次请求将重新抓取"})


if __name__ == "__main__":
    app.run(port=7788, debug=False, threaded=True)
