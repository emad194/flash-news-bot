import os
import asyncio
import feedparser
import requests
import re
import html
import random
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from nostr_sdk import Keys, Client, EventBuilder, NostrSigner, RelayUrl

NOSTR_NSEC = os.environ.get("NOSTR_NSEC", "").strip()
if not NOSTR_NSEC:
    raise ValueError("متغير NOSTR_NSEC مفقود في GitHub Secrets")

# --- إعدادات معدل النشر ---
MAX_POSTS_PER_RUN = 2        # عدد المنشورات الأقصى في الدورة الواحدة (كل 15 دقيقة)
SLEEP_BETWEEN_POSTS = 90     # الانتظار بالثواني بين كل منشور والآخر

# --- قائمة المصادر الشاملة ---
RSS_FEEDS = [
    # --- Crypto & Bitcoin ---
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://bitcoinmagazine.com/.rss/full/",
    "https://decrypt.co/feed",
    "https://blockworks.co/feed",
    
    # --- Nostr & Bitcoin News ---
    "https://nostr.band/rss/news.xml",
    
    # --- Tech & Innovation ---
    "https://techcrunch.com/feed/",
    "https://www.theverge.com/rss/index.xml",
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://www.wired.com/feed/category/business/latest/rss",
    
    # --- World Politics & Global Finance ---
    "https://feeds.feedburner.com/reuters/topNews",
    "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best",
    "https://feeds.bloomberg.com/economics/news.rss",
    "http://feeds.bbci.co.uk/news/world/rss.xml",
    "https://search.cnbc.com/rs/search/combineddigest.ca?headline=plaintext&selectedNodeId=100003114&minProcessedTime=0&maxProcessedTime=endofday&cd=100"
]

HISTORY_FILE = "published_posts.txt"

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_history(post_id):
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{post_id}\n")

def clean_text(raw_text):
    if not raw_text:
        return ""
    text = html.unescape(raw_text)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('*', '')
    text = re.sub(r'\[.*?\]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def extract_media(entry, feed_url):
    video_url = None
    image_url = None

    # 1. فحص Enclosures الرسمية
    if 'enclosures' in entry:
        for enc in entry.enclosures:
            mime_type = enc.get('type', '')
            href = enc.get('href', '')
            if mime_type.startswith('video/') or href.endswith('.mp4'):
                video_url = href
                break
            elif mime_type.startswith('image/'):
                image_url = href

    # 2. فحص Media RSS الخاصة بوكالات الأخبار
    if not image_url and 'media_content' in entry and len(entry.media_content) > 0:
        image_url = entry.media_content[0].get('url')
    elif not image_url and 'media_thumbnail' in entry and len(entry.media_thumbnail) > 0:
        image_url = entry.media_thumbnail[0].get('url')

    # 3. فحص الـ HTML مع تصفية الإعلانات والأيقونات
    if not image_url and not video_url:
        html_sources = []
        if 'summary' in entry:
            html_sources.append(entry.summary)
        if 'content' in entry:
            for c in entry.content:
                html_sources.append(c.get('value', ''))
        
        full_html = " ".join(html_sources)
        if full_html:
            soup = BeautifulSoup(full_html, 'html.parser')
            for img_tag in soup.find_all('img'):
                src = img_tag.get('src') or (img_tag.get('srcset', '').split(',')[0].split(' ')[0] if img_tag.get('srcset') else None)
                if src:
                    bad_keywords = ['icon', 'avatar', 'logo', 'widget', 'banner', 'ad-', 'meme', 'social']
                    if any(bad in src.lower() for bad in bad_keywords):
                        continue
                    image_url = urljoin(feed_url, src)
                    break

    return video_url, image_url

def upload_to_nostr_build(media_url, is_video=False):
    if not media_url:
        return None

    try:
        resp = requests.get(media_url, timeout=15)
        if resp.status_code == 200:
            ext = 'mp4' if is_video else 'jpg'
            mime = 'video/mp4' if is_video else 'image/jpeg'
            files = {'file': (f'media.{ext}', resp.content, mime)}
            upload_resp = requests.post('https://nostr.build/api/v2/upload/files', files=files, timeout=30)
            if upload_resp.status_code == 200:
                data = upload_resp.json()
                if 'data' in data and len(data['data']) > 0:
                    return data['data'][0]['url']
    except Exception as e:
        print(f"فشل الرفع لـ nostr.build: {e}")
    
    return None

async def main():
    history = load_history()
    
    keys = Keys.parse(NOSTR_NSEC)
    signer = NostrSigner.keys(keys)
    client = Client(signer)

    await client.add_relay(RelayUrl.parse("wss://relay.damus.io"))
    await client.add_relay(RelayUrl.parse("wss://nos.lol"))
    await client.add_relay(RelayUrl.parse("wss://relay.primal.net"))
    await client.connect()

    published_count = 0

    feeds = RSS_FEEDS.copy()
    random.shuffle(feeds)

    for feed_url in feeds:
        if published_count >= MAX_POSTS_PER_RUN:
            print("تم الوصول للحد الأقصى للمنشورات لهذه الدورة.")
            break

        try:
            feed = feedparser.parse(feed_url)
            if not feed.entries:
                continue

            for entry in feed.entries:
                if published_count >= MAX_POSTS_PER_RUN:
                    break

                post_id = entry.get('id') or entry.get('link')
                if not post_id or post_id in history:
                    continue

                # استخراج وسائط الخبر
                video_url, image_url = extract_media(entry, feed_url)

                # الشرط الصارم: إذا لم تتوفر صورة أو فيديو للمقال، يتم تخطي الخبر فوراً إلى الخبر التالي
                if not video_url and not image_url:
                    print(f"تخطي الخبر بسبب عدم وجود صورة رئيسية: {entry.get('title', '')}")
                    continue

                media_link = None
                if video_url:
                    if "youtube.com" in video_url or "youtu.be" in video_url:
                        media_link = video_url
                    else:
                        media_link = upload_to_nostr_build(video_url, is_video=True)
                elif image_url:
                    media_link = upload_to_nostr_build(image_url, is_video=False)

                # إذا فشل الرفع لـ nostr.build ولم يتم الحصول على رابط صورة شغال، يتم التخطي أيضاً
                if not media_link:
                    print(f"تخطي الخبر بسبب فشل رفع الصورة/الفيديو: {entry.get('title', '')}")
                    continue

                title = clean_text(entry.get('title', ''))
                summary = clean_text(entry.get('summary', ''))

                post_text = f"{title}\n\n{summary}\n\n{media_link}"

                builder = EventBuilder.text_note(post_text)
                await client.send_event_builder(builder)
                print(f"تم بنجاح نشر: {title}")
                save_history(post_id)
                published_count += 1

                if published_count < MAX_POSTS_PER_RUN:
                    print(f"الانتظار لمدة {SLEEP_BETWEEN_POSTS} ثانية قبل الخبر التالي...")
                    await asyncio.sleep(SLEEP_BETWEEN_POSTS)

        except Exception as e:
            print(f"خطأ في تغذية {feed_url}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
