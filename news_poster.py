import os
import asyncio
import feedparser
import re
import html
import random
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from nostr_sdk import Keys, Client, EventBuilder, NostrSigner, RelayUrl

NOSTR_NSEC = os.environ.get("NOSTR_NSEC", "").strip()
if not NOSTR_NSEC:
    raise ValueError("متغير NOSTR_NSEC مفقود في GitHub Secrets")

# --- إعدادات معدل وتوقيت النشر ---
MAX_POSTS_PER_RUN = 6        # نشر 6 مقالات في الدورة الواحدة
SLEEP_BETWEEN_POSTS = 30     # الانتظار 30 ثانية بين كل خبر لتجنب الحظر

# --- قائمة المصادر الموجهة حصراً لمجتمع Nostr و Bitcoin ---
RSS_FEEDS = [
    # Bitcoin & Crypto Core
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://bitcoinmagazine.com/.rss/full/",
    "https://decrypt.co/feed",
    "https://blockworks.co/feed",
    
    # Nostr & Freedom Tech
    "https://nostr.band/rss/news.xml",
    
    # AI & Tech Hardware
    "https://techcrunch.com/feed/"
]

# كلمات مفتاحية إيجابية
TARGET_KEYWORDS = [
    'bitcoin', 'btc', 'crypto', 'nostr', 'lightning', 'sats', 'ai', 'openai', 
    'claude', 'sec', 'fed', 'inflation', 'market', 'privacy', 'security', 
    'hack', 'nvidia', 'gpu', 'apple', 'google', 'trump'
]

# كلمات محظورة تستبعد المقالات الأدبية والتاريخية
EXCLUDE_KEYWORDS = [
    'etymology', 'fiction', 'novel', 'poem', 'essay', 'bradbury', 'review:', 
    'movie', 'culture', 'book', 'history', 'ancient'
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

def is_nostr_relevant(text):
    text_lower = text.lower()
    
    # فحص الكلمات المحظورة أولاً
    if any(bad_word in text_lower for bad_word in EXCLUDE_KEYWORDS):
        return False
        
    # فحص الكلمات المستهدفة
    return any(keyword in text_lower for keyword in TARGET_KEYWORDS)

def extract_media(entry, feed_url):
    video_url = None
    image_url = None

    if 'enclosures' in entry:
        for enc in entry.enclosures:
            mime_type = enc.get('type', '')
            href = enc.get('href', '')
            if mime_type.startswith('video/') or href.endswith('.mp4'):
                video_url = href
                break
            elif mime_type.startswith('image/'):
                image_url = href

    if not image_url and 'media_content' in entry and len(entry.media_content) > 0:
        image_url = entry.media_content[0].get('url')

    if not image_url and not video_url:
        html_sources = []
        if 'content' in entry:
            for c in entry.content:
                html_sources.append(c.get('value', ''))
        if 'summary' in entry:
            html_sources.append(entry.summary)
        
        full_html = " ".join(html_sources)
        if full_html:
            soup = BeautifulSoup(full_html, 'html.parser')
            for img_tag in soup.find_all('img'):
                src = None
                if img_tag.get('srcset'):
                    srcset_parts = img_tag['srcset'].split(',')
                    src = srcset_parts[-1].strip().split(' ')[0]
                if not src:
                    src = img_tag.get('src')
                
                if src:
                    bad_keywords = ['icon', 'avatar', 'logo', 'widget', 'banner', 'ad-', 'meme', 'social', '8Z4v', 'illustration', 'graphic']
                    if any(bad in src.lower() for bad in bad_keywords):
                        continue
                    image_url = urljoin(feed_url, src)
                    break

    if not image_url and not video_url and 'media_thumbnail' in entry and len(entry.media_thumbnail) > 0:
        image_url = entry.media_thumbnail[0].get('url')

    return video_url, image_url

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
            print("تم الوصول للحد الأقصى المطلوب لهذه الدورة (6 منشورات).")
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

                title = clean_text(entry.get('title', ''))
                summary = clean_text(entry.get('summary', ''))
                full_text_check = f"{title} {summary}"

                if not is_nostr_relevant(full_text_check):
                    print(f"تخطي خبر غير ذي صلة: {title}")
                    continue

                video_url, image_url = extract_media(entry, feed_url)

                if not video_url and not image_url:
                    print(f"تخطي الخبر لعدم وجود صورة: {title}")
                    continue

                media_link = video_url if video_url else image_url
                post_text = f"{title}\n\n{summary}\n\n{media_link}"

                builder = EventBuilder.text_note(post_text)
                await client.send_event_builder(builder)
                print(f"({published_count + 1}/{MAX_POSTS_PER_RUN}) تم نشر خبر مستهدف: {title}")
                save_history(post_id)
                published_count += 1

                # انتظار 30 ثانية قبل نشر الخبر التالي في نفس الدورة
                if published_count < MAX_POSTS_PER_RUN:
                    print(f"الانتظار لمدة {SLEEP_BETWEEN_POSTS} ثانية قبل نشر الخبر التالي...")
                    await asyncio.sleep(SLEEP_BETWEEN_POSTS)

        except Exception as e:
            print(f"خطأ في تغذية {feed_url}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
