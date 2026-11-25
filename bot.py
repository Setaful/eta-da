"""NTE news aggregator -> Telegram chat. Free.

Два режима (переменная GROUP):
  cloud - Telegram-каналы + YouTube (запускается на GitHub Actions)
  local - Twitter + Bilibili + Weibo (запускается на твоём компьютере)
  all   - всё сразу
"""
import os, json, html, time
import requests, feedparser
from bs4 import BeautifulSoup

# Настройки из файла local.env (для запуска на компьютере). Секреты GitHub имеют приоритет.
if os.path.exists("local.env"):
    for _line in open("local.env", encoding="utf-8"):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
GROUP = os.environ.get("GROUP", "all")
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
BILI_COOKIE = os.environ.get("BILI_COOKIE", "")
WEIBO_COOKIE = os.environ.get("WEIBO_COOKIE", "")
X_EVERY = int(os.environ.get("X_EVERY", "3"))  # Twitter проверять раз в N запусков (по умолчанию каждые ~30 мин)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
MOBILE_UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"}

# ---- Разделение по темам (топикам) супергруппы. -------------------------
# Узнай message_thread_id каждого топика и впиши сюда. None = писать в общий чат.
# ВАЖНО: если меняешь, меняй и на GitHub (bot.py), и на компьютере.
TOPICS = {
    "twitter": None,
    "youtube": None,
    "bilibili": None,
    "weibo": None,
    "telegram": None,
    "leaks": None,
}

# ---- Источники ----------------------------------------------------------
RSSHUB = ["http://localhost:1200"]  # запасной путь, если на компьютере стоит свой RSSHub

SOURCES = []
for u in ["NTE_GL", "Nteupdates", "nte_update"]:
    SOURCES.append({"name": f"X @{u}", "topic": "twitter", "type": "x", "user": u})

SOURCES.append({"name": "YouTube", "topic": "youtube", "type": "rss",
                "urls": ["https://www.youtube.com/feeds/videos.xml?channel_id=UCIc-uir_VVR8D2RlUyP29Aw"]})

BILI_UID = "3546636978489848"
SOURCES.append({"name": "Bilibili", "topic": "bilibili", "type": "bili", "uid": BILI_UID,
                "urls": [f"{h}/bilibili/user/dynamic/{BILI_UID}" for h in RSSHUB]})

WEIBO_UID = "7929584207"
SOURCES.append({"name": "Weibo", "topic": "weibo", "type": "weibo", "uid": WEIBO_UID,
                "urls": [f"{h}/weibo/user/{WEIBO_UID}" for h in RSSHUB]})

TG_CHANNELS = ["NTEanabioz", "NTE_Pokoii", "NTEDARK", "Info_Neverness_to_Everness",
               "Neverness_to_EvernessRU", "kvashnya5", "MIU_VERSE", "Neverness_to_Everness_RU",
               "NTENews", "Nevernesstoeverness_leaks", "NevernessToEverness", "hotori_nte", "ntenewsru",
               "kjwqofekj"]
TG_LEAKS = ["nte_blednaya", "Seele_NTE_Leaks"]

for c in TG_CHANNELS:
    SOURCES.append({"name": f"TG {c}", "topic": "telegram", "type": "tg", "channel": c})
for c in TG_LEAKS:
    SOURCES.append({"name": f"Слив {c}", "topic": "leaks", "type": "tg", "channel": c})


def group_of(src):
    return "local" if src["type"] in ("x", "bili", "weibo") else "cloud"


if GROUP != "all":
    SOURCES = [s for s in SOURCES if group_of(s) == GROUP]


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
    errors = []
    for url in src["urls"]:
        host = url.split("/")[2]
        try:
            r = requests.get(url, headers=UA, timeout=45)
            if not r.ok:
                body = BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)[:300]
                errors.append(f"{host}: HTTP {r.status_code}: {body}")
                continue
            feed = feedparser.parse(r.content)
            if not feed.entries:
                errors.append(f"{host}: лента пуста")
                continue
            posts = []
            for e in feed.entries:
                text = BeautifulSoup(e.get("title", "") or e.get("summary", ""), "html.parser").get_text()
                posts.append({"id": e.get("id") or e.get("link"), "text": text.strip(),
                              "url": e.get("link", "")})
            posts.reverse()  # RSS: новые сверху
            return posts
        except Exception as ex:
            errors.append(f"{host}: {str(ex)[:90]}")
    raise RuntimeError("все зеркала не ответили -> " + " | ".join(errors))


# --- Twitter / X ---
def parse_x_html(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag:
        raise RuntimeError("в ответе нет данных ленты")
    data = json.loads(tag.string)
    entries = data["props"]["pageProps"]["timeline"]["entries"]
    posts = []
    for e in entries:
        if e.get("type") != "tweet":
            continue
        t = e["content"]["tweet"]
        tid = t.get("id_str") or str(t.get("id"))
        screen = (t.get("user") or {}).get("screen_name", "i")
        text = t.get("full_text") or t.get("text") or ""
        posts.append({"id": tid, "text": html.unescape(text).strip(),
                      "url": f"https://x.com/{screen}/status/{tid}"})
    posts.sort(key=lambda p: int(p["id"]))
    return posts


def fetch_x(src):
    last_err = None
    for host in ("syndication.twitter.com", "syndication.x.com"):
        try:
            url = f"https://{host}/srv/timeline-profile/screen-name/{src['user']}"
            r = requests.get(url, headers=UA, timeout=25)
            if r.status_code == 429:
                last_err = "лимит запросов Twitter (429), попробую позже"
                break
            r.raise_for_status()
            posts = parse_x_html(r.text)
            if posts:
                return posts
            last_err = f"{host}: лента пуста"
        except Exception as ex:
            last_err = ex
    raise RuntimeError(f"Twitter недоступен ({last_err})")


# --- Bilibili ---
def parse_bili(j):
    posts = []
    for it in j["data"]["items"]:
        mod = (it.get("modules") or {}).get("module_dynamic") or {}
        text = ((mod.get("desc") or {}).get("text") or "").strip()
        major = mod.get("major") or {}
        for key in ("archive", "opus", "article"):
            if key in major:
                title = (major[key].get("title") or "").strip()
                if title and title not in text:
                    text = f"{title}\n{text}".strip()
        did = it.get("id_str")
        if did:
            posts.append({"id": did, "text": text or "(новая запись)", "url": f"https://t.bilibili.com/{did}"})
    posts.sort(key=lambda p: int(p["id"]))
    return posts


def snippet(r):
    return f"HTTP {r.status_code}, ответ: {r.text[:120]!r}"


def fetch_bili(src):
    headers = dict(UA, Referer="https://space.bilibili.com/")
    if BILI_COOKIE:
        headers["Cookie"] = BILI_COOKIE
    err = None
    try:
        s = requests.Session()
        if not BILI_COOKIE:
            s.get("https://www.bilibili.com/", headers=UA, timeout=20)  # получаем базовые cookie
        r = s.get("https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space",
                  params={"host_mid": src["uid"]}, headers=headers, timeout=25)
        try:
            j = r.json()
        except ValueError:
            raise RuntimeError(snippet(r) + ("" if BILI_COOKIE else " (скорее всего нужен BILI_COOKIE в local.env)"))
        if j.get("code") == 0:
            return parse_bili(j)
        err = f"Bilibili API: код {j.get('code')}, {j.get('message')}"
        if not BILI_COOKIE:
            err += " (нужен BILI_COOKIE в local.env)"
    except Exception as ex:
        err = f"Bilibili API: {str(ex)[:200]}"
    try:
        return fetch_rss(src)
    except Exception as ex:
        raise RuntimeError(f"{err} | RSSHub: {str(ex)[:80]}")


# --- Weibo ---
def parse_weibo(j):
    posts = []
    for card in j["data"]["cards"]:
        mb = card.get("mblog")
        if not mb:
            continue
        text = BeautifulSoup(mb.get("text", ""), "html.parser").get_text(" ", strip=True)
        posts.append({"id": str(mb["id"]), "text": text or "(новая запись)",
                      "url": f"https://m.weibo.cn/detail/{mb['id']}"})
    posts.sort(key=lambda p: int(p["id"]))
    return posts


def fetch_weibo(src):
    uid = src["uid"]
    err = None
    try:
        s = requests.Session()
        if not WEIBO_COOKIE:
            s.get("https://m.weibo.cn/", headers=MOBILE_UA, timeout=20)  # cookie гостя
        headers = dict(MOBILE_UA, Referer=f"https://m.weibo.cn/u/{uid}",
                       **{"X-Requested-With": "XMLHttpRequest", "MWeibo-Pwa": "1"})
        if WEIBO_COOKIE:
            headers["Cookie"] = WEIBO_COOKIE
        r = s.get("https://m.weibo.cn/api/container/getIndex",
                  params={"type": "uid", "value": uid, "containerid": f"107603{uid}"},
                  headers=headers, timeout=25)
        try:
            j = r.json()
        except ValueError:
            raise RuntimeError(snippet(r))
        if j.get("ok") == 1:
            posts = parse_weibo(j)
            if posts:
                return posts
            err = "Weibo API: записей нет"
        else:
            err = f"Weibo API: ok={j.get('ok')}, {j.get('msg', '')}" + (
                " (нужен WEIBO_COOKIE в local.env)" if j.get("ok") == -100 and not WEIBO_COOKIE else "")
    except Exception as ex:
        err = f"Weibo API: {str(ex)[:200]}"
    try:
        return fetch_rss(src)
    except Exception as ex:
        raise RuntimeError(f"{err} | RSSHub: {str(ex)[:80]}")


FETCHERS = {"tg": fetch_tg, "x": fetch_x, "bili": fetch_bili, "weibo": fetch_weibo, "rss": fetch_rss}


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
        state = json.load(open(STATE_FILE, encoding="utf-8"))
    except Exception:
        state = {}

    for src in SOURCES:
        name = src["name"]
        try:
            if src["type"] == "x":
                if int(time.time() // 600) % X_EVERY != 0:
                    continue  # Twitter проверяем реже, чтобы не ловить лимит
                time.sleep(5)
            posts = FETCHERS[src["type"]](src)
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

    json.dump(state, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
