import os
import asyncio
import feedparser
import requests
import re
from nostr_sdk import Keys, Client, EventBuilder, NostrSigner, RelayUrl

NOSTR_NSEC = os.environ.get("NOSTR_NSEC", "").strip()
if not NOSTR_NSEC:
    raise ValueError("متغير NOSTR_NSEC مفقود في GitHub Secrets")

# مصادر أخبار توفر فيديوهات وصور دقيقة
RSS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://techcrunch.com/feed/",
    "https://cointelegraph.com/rss",
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

def extract_media(entry):
    """استخراج رابط الفيديو أو الصورة من تغذية RSS"""
    video_url = None
    image_url = None

    # 1. البحث في Enclosures (غالبًا تحتوي على ملفات الميديا المباشرة كالـ mp4)
    if 'enclosures' in entry:
        for enc in entry.enclosures:
            mime_type = enc.get('type', '')
            href = enc.get('href', '')
            if mime_type.startswith('video/') or href.endswith('.mp4'):
                video_url = href
                break
            elif mime_type.startswith('image/'):
                image_url = href

    # 2. البحث عن روابط YouTube المدمجة في الوصف/المقال
    if not video_url:
        content_text = entry.get('summary', '') + entry.get('description', '')
        yt_match = re.search(r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[^\s"<]+)', content_text)
        if yt_match:
            video_url = yt_match.group(1)

    # 3. إذا لم يوجد فيديو، نأخذ الصورة من media_content
    if not video_url and not image_url:
        if 'media_content' in entry and len(entry.media_content) > 0:
            image_url = entry.media_content[0].get('url')
        elif 'media_thumbnail' in entry and len(entry.media_thumbnail) > 0:
            image_url = entry.media_thumbnail[0].get('url')

    return video_url, image_url

def upload_to_nostr_build(media_url, is_video=False):
    """رفع الميديا (صور أو فيديوهات قصيرة) إلى nostr.build"""
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
        print(f"فشل رفع الميديا لـ nostr.build: {e}")
    return media_url  # استخدام الرابط الأصلي في حال فشل الرفع

async def main():
    history = load_history()
    
    keys = Keys.parse(NOSTR_NSEC)
    signer = NostrSigner.keys(keys)
    client = Client(signer)

    await client.add_relay(RelayUrl.parse("wss://relay.damus.io"))
    await client.add_relay(RelayUrl.parse("wss://nos.lol"))
    await client.add_relay(RelayUrl.parse("wss://relay.primal.net"))
    await client.connect()

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            if not feed.entries:
                continue

            for entry in feed.entries[:3]:
                post_id = entry.get('id') or entry.get('link')
                if not post_id or post_id in history:
                    continue

                title = entry.get('title', '').strip()
                summary = entry.get('summary', '').strip()
                
                # تنظيف الـ HTML
                summary = re.sub(r'<[^>]+>', '', summary).strip()

                video_url, image_url = extract_media(entry)
                media_link = None

                if video_url:
                    # إذا كان فيديو YouTube يوضع كما هو وتطبيقات Nostr تشغله فوراً
                    if "youtube.com" in video_url or "youtu.be" in video_url:
                        media_link = video_url
                    else:
                        media_link = upload_to_nostr_build(video_url, is_video=True)
                elif image_url:
                    media_link = upload_to_nostr_build(image_url, is_video=False)

                # صياغة النص النهائي
                post_text = f"**{title}**\n\n{summary}"
                if media_link:
                    post_text += f"\n\n{media_link}"

                builder = EventBuilder.text_note(post_text)
                await client.send_event_builder(builder)
                print(f"تم النشر بنجاح: {title}")
                save_history(post_id)

        except Exception as e:
            print(f"خطأ أثناء معالجة {feed_url}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
