"""NTE news aggregator -> Telegram chat. Free: runs on GitHub Actions."""
import os, json, html
import requests, feedparser
from bs4 import BeautifulSoup

TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
STATE_FILE = "state.json"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}

# ---- Разделение по темам (топикам) супергруппы. -------------------------
# Узнай message_thread_id каждого топика и впиши сюда. None = писать в общий чат.
TOPICS = {
    "twitter": None,
    "youtube": None,
    "bilibili": None,
    "weibo": None,
    "telegram": None,
    "leaks": None,
}

# ---- Источники ----------------------------------------------------------
NITTER = ["https://nitter.net", "https://nitter.poast.org", "https://nitter.privacydev.net"]
RSSHUB = ["https://rsshub.app", "https://rsshub.rssforever.com"]

SOURCES = []
for u in ["NTE_GL", "Nteupdates", "nte_update"]:
    SOURCES.append({"name": f"X @{u}", "topic": "twitter", "type": "rss",
                    "urls": [f"{h}/{u}/rss" for h in NITTER]})

SOURCES.append({"name": "YouTube", "topic": "youtube", "type": "rss",
                "urls": ["https://www.youtube.com/feeds/videos.xml?channel_id=UCIc-uir_VVR8D2RlUyP29Aw"]})

SOURCES.append({"name": "Bilibili", "topic": "bilibili", "type": "rss",
                "urls": [f"{h}/bilibili/user/dynamic/3546636978489848" for h in RSSHUB]})

# WEIBO_UID — числовой ID официального аккаунта (см. инструкцию). Пока пусто — источник пропускается.
WEIBO_UID = "7929584207"
if WEIBO_UID:
    SOURCES.append({"name": "Weibo", "topic": "weibo", "type": "rss",
                    "urls": [f"{h}/weibo/user/{WEIBO_UID}" for h in RSSHUB]})

TG_CHANNELS = ["NTEanabioz", "NTE_Pokoii", "NTEDARK", "Info_Neverness_to_Everness",
               "Neverness_to_EvernessRU", "kvashnya5", "MIU_VERSE", "Neverness_to_Everness_RU",
               "NTENews", "Nevernesstoeverness_leaks", "NevernessToEverness", "hotori_nte", "ntenewsru"]
TG_LEAKS = ["nte_blednaya", "Seele_NTE_Leaks"]

for c in TG_CHANNELS:
    SOURCES.append({"name": f"TG {c}", "topic": "telegram", "type": "tg", "channel": c})
for c in TG_LEAKS:
    SOURCES.append({"name": f"Слив {c}", "topic": "leaks", "type": "tg", "channel": c})


# ---- Загрузчики (возвращают посты от старых к новым) --------------------
def fetch_tg(src):
    r = requests.get(f"https://t.me/s/{src['channel']}", headers=UA, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    posts = []
    for m in soup.select(".tgme_widget_message"):
        pid = m.get("data-post")
        if not pid:
            continue
        t = m.select_one(".tgme_widget_message_text")
        posts.append({"id": pid, "text": t.get_text("\n").strip() if t else "(медиа)",
                      "url": f"https://t.me/{pid}"})
    return posts


def fetch_rss(src):
    last_err = None
    for url in src["urls"]:
        try:
            r = requests.get(url, headers=UA, timeout=20)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
            if not feed.entries:
                continue
            posts = []
            for e in feed.entries:
                text = BeautifulSoup(e.get("title", "") or e.get("summary", ""), "html.parser").get_text()
                posts.append({"id": e.get("id") or e.get("link"), "text": text.strip(),
                              "url": e.get("link", "")})
            posts.reverse()  # RSS: новые сверху
            return posts
        except Exception as ex:
            last_err = ex
    raise RuntimeError(f"все зеркала не ответили: {last_err}")


def send(topic_key, name, post):
    text = f"<b>{html.escape(name)}</b>\n{html.escape(post['text'][:900])}\n{post['url']}"
    data = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    thread = TOPICS.get(topic_key)
    if thread:
        data["message_thread_id"] = thread
    r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=data, timeout=20)
    if not r.ok:
        print("send error:", r.text)


def main():
    try:
        state = json.load(open(STATE_FILE))
    except Exception:
        state = {}

    for src in SOURCES:
        name = src["name"]
        try:
            posts = fetch_tg(src) if src["type"] == "tg" else fetch_rss(src)
        except Exception as ex:
            print(f"[{name}] ошибка: {ex}")
            continue

        seen = state.get(name)
        if seen is None:  # первый запуск: просто запоминаем, не спамим
            state[name] = [p["id"] for p in posts][-100:]
            continue
        for p in posts:
            if p["id"] not in seen:
                send(src["topic"], name, p)
                seen.append(p["id"])
        state[name] = seen[-100:]

    json.dump(state, open(STATE_FILE, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
