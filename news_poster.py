import os
import re
import asyncio
import uuid
import requests
from bs4 import BeautifulSoup
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaWebPage, MessageEntityTextUrl, MessageEntityUrl
from nostr_sdk import Keys, Client, EventBuilder, NostrSigner, RelayUrl

# 1. قراءة المتغيرات وتفادي الأخطاء
api_id_env = os.environ.get("TELEGRAM_API_ID", "").strip()
api_hash_env = os.environ.get("TELEGRAM_API_HASH", "").strip()
session_string_env = os.environ.get("TELEGRAM_SESSION_STRING", "").strip()
nostr_nsec_env = os.environ.get("NOSTR_NSEC", "").strip()

missing = []
if not api_id_env: missing.append("TELEGRAM_API_ID")
if not api_hash_env: missing.append("TELEGRAM_API_HASH")
if not session_string_env: missing.append("TELEGRAM_SESSION_STRING")
if not nostr_nsec_env: missing.append("NOSTR_NSEC")

if missing:
    raise ValueError(f"المتغيرات التالية مفقودة في GitHub Secrets: {', '.join(missing)}")

API_ID = int(api_id_env)
API_HASH = api_hash_env
SESSION_STRING = session_string_env
NOSTR_NSEC = nostr_nsec_env

CHANNELS = [
    "DiscloseTv",
    "CoinDesk",
    "TechCrunch",
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

def extract_urls(msg):
    """استخراج جميع الروابط سواء كانت نصوصاً مجردة، روابط مدمجة، أو معاينة صفحة."""
    urls = []
    text = msg.text or ""
    
    # 1. استخراج الروابط المباشرة بالنص
    found_urls = re.findall(r'https?://[^\s\)]+', text)
    urls.extend(found_urls)
    
    # 2. استخراج الروابط المدمجة في الـ Entities
    if msg.entities:
        for entity in msg.entities:
            if isinstance(entity, MessageEntityTextUrl):
                urls.append(entity.url)
            elif isinstance(entity, MessageEntityUrl):
                offset = entity.offset
                length = entity.length
                urls.append(text[offset:offset+length])
                
    # 3. استخراج الرابط من معاينات تليجرام WebPage
    if msg.media and isinstance(msg.media, MessageMediaWebPage) and hasattr(msg.media.webpage, 'url'):
        urls.append(msg.media.webpage.url)

    # إزالة التكرار مع الحفاظ على الترتيب
    seen = set()
    unique_urls = []
    for u in urls:
        clean_u = u.strip("()[]\"'")
        if clean_u not in seen:
            seen.add(clean_u)
            unique_urls.append(clean_u)
            
    return unique_urls

def clean_and_format_text(text):
    if not text:
        return ""
    
    # 1. إصلاحMarkdown المشوه: [**Title**](url) -> Title
    cleaned = re.sub(r'\[(.*?)\]\((https?://[^\)]+)\)', r'\1', text)
    
    # 2. إزالة النجوم والرموز التنسيقية الزائدة
    cleaned = cleaned.replace("**", "").replace("[", "").replace("]", "")
    
    # 3. إزالة السطور المزعجة (إعلانات، روابط تليجرام، توقيع القناة، والمصادر المباشرة)
    lines = cleaned.split("\n")
    filtered_lines = []
    for line in lines:
        line_str = line.strip()
        if not line_str:
            continue
        
        lower_line = line_str.lower()
        if (
            "t.me/" in lower_line
            or lower_line.startswith("http://")
            or lower_line.startswith("https://")
            or lower_line.startswith("source:")
            or "subscribe" in lower_line
            or "join" in lower_line
            or line_str.startswith("@")
        ):
            continue
            
        filtered_lines.append(line_str)
        
    return "\n\n".join(filtered_lines).strip()

def get_og_image(url):
    """جلب صورة المعاينة من رابط الخبر عبر وسم og:image"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        resp = requests.get(url, headers=headers, timeout=6)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            og_image = soup.find('meta', property='og:image') or soup.find('meta', attrs={'name': 'og:image'})
            if og_image and og_image.get('content'):
                img_url = og_image['content']
                img_resp = requests.get(img_url, headers=headers, timeout=6)
                if img_resp.status_code == 200:
                    temp_filename = f"temp_og_{uuid.uuid4().hex[:8]}.jpg"
                    with open(temp_filename, "wb") as f:
                        f.write(img_resp.content)
                    return temp_filename
    except Exception as e:
        print(f"تعذر جلب الصورة من {url}: {e}")
    return None

def upload_to_nostr_build(file_path):
    try:
        with open(file_path, 'rb') as f:
            resp = requests.post('https://nostr.build/api/v2/upload/files', files={'file': f}, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if 'data' in data and len(data['data']) > 0:
                    return data['data'][0]['url']
    except Exception as e:
        print(f"فشل الرفع إلى nostr.build: {e}")
    return None

async def main():
    history = load_history()
    
    keys = Keys.parse(NOSTR_NSEC)
    signer = NostrSigner.keys(keys)
    client = Client(signer)

    await client.add_relay(RelayUrl.parse("wss://relay.damus.io"))
    await client.add_relay(RelayUrl.parse("wss://nos.lol"))
    await client.connect()

    tg_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await tg_client.start()
    
    async with tg_client:
        for channel in CHANNELS:
            try:
                messages = await tg_client.get_messages(channel, limit=1)
                if not messages:
                    continue
                
                msg = messages[0]
                post_unique_id = f"{channel}_{msg.id}"

                if post_unique_id in history:
                    continue

                raw_text = msg.text or ""
                urls = extract_urls(msg)
                text = clean_and_format_text(raw_text)
                media_url = None
                
                # 1. إذا كان المنشور يحوي صورة أو فيديو مرفق في تليجرام مباشرة
                if msg.media and not isinstance(msg.media, MessageMediaWebPage):
                    file_path = await tg_client.download_media(msg)
                    if file_path and os.path.exists(file_path):
                        media_url = upload_to_nostr_build(file_path)
                        os.remove(file_path)

                # 2. إن لم تكن هناك ميديا مرفقة وحصلنا على روابط، نجلب صورة og:image
                if not media_url and urls:
                    for target_url in urls:
                        og_path = get_og_image(target_url)
                        if og_path:
                            media_url = upload_to_nostr_build(og_path)
                            if os.path.exists(og_path):
                                os.remove(og_path)
                            break

                # 3. بناء نص المنشور النهائي
                full_content = text
                if media_url:
                    full_content = f"{full_content}\n\n{media_url}".strip()

                if full_content.strip():
                    builder = EventBuilder.text_note(full_content)
                    await client.send_event_builder(builder)
                    print(f"تم بنجاح نشر خبر أنيق ومصوّر من قناة: {channel}")
                    save_history(post_unique_id)

            except Exception as e:
                print(f"خطأ أثناء معالجة القناة {channel}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
