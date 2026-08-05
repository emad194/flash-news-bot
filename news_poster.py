import os
import re
import asyncio
import requests
from bs4 import BeautifulSoup
from telethon import TelegramClient
from telethon.sessions import StringSession
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

def clean_and_format_text(text):
    if not text:
        return "", []
    
    # 1. استخراج الروابط قبل حذفها لجلب الصور منها
    urls = re.findall(r'https?://[^\s\)]+', text)
    
    # 2. تنظيف Markdown المشوه مثل [**Title**](url) وحذف الرابط
    def remove_md_link(match):
        return match.group(1).replace("**", "").replace("[", "").replace("]", "").strip()
    
    cleaned = re.sub(r'\[(.*?)\]\((https?://[^\)]+)\)', remove_md_link, text)
    
    # 3. تنظيف النجوم والأقواس الزائدة
    cleaned = cleaned.replace("**", "").replace("[", "").replace("]", "")
    
    # 4. إزالة روابط المصدر والإعلانات وروابط تليجرام
    lines = cleaned.split("\n")
    filtered_lines = []
    for line in lines:
        line_str = line.strip()
        # حذف السطور التي تحتوي على روابط أو ترويج أو كلمة Source
        if (
            "t.me/" in line_str.lower()
            or "http://" in line_str.lower()
            or "https://" in line_str.lower()
            or line_str.lower().startswith("source:")
            or "subscribe" in line_str.lower()
            or "join" in line_str.lower()
            or line_str.startswith("@")
        ):
            continue
        filtered_lines.append(line_str)
        
    return "\n".join(filtered_lines).strip(), urls

def get_og_image(url):
    """جلب صورة المعاينة الأصلية للمقال من الرابط"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            og_image = soup.find('meta', property='og:image') or soup.find('meta', attrs={'name': 'og:image'})
            if og_image and og_image.get('content'):
                img_url = og_image['content']
                img_resp = requests.get(img_url, headers=headers, timeout=5)
                if img_resp.status_code == 200:
                    temp_path = "temp_og.jpg"
                    with open(temp_path, "wb") as f:
                        f.write(img_resp.content)
                    return temp_path
    except Exception as e:
        print(f"تعذر جلب الصورة من {url}: {e}")
    return None

def upload_to_nostr_build(file_path):
    try:
        with open(file_path, 'rb') as f:
            resp = requests.post('https://nostr.build/api/v2/upload/files', files={'file': f})
            if resp.status_code == 200:
                return resp.json()['data'][0]['url']
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
                text, urls = clean_and_format_text(raw_text)
                media_url = None
                
                # 1. جلب الميديا المرفقة بالتليجرام إن وجدت
                if msg.media and not hasattr(msg.media, 'webpage'):
                    file_path = await tg_client.download_media(msg)
                    if file_path and os.path.exists(file_path):
                        media_url = upload_to_nostr_build(file_path)
                        os.remove(file_path)

                # 2. إن لم توجد ميديا، جلب صورة المعاينة من أول رابط في النص
                if not media_url and urls:
                    og_path = get_og_image(urls[0])
                    if og_path:
                        media_url = upload_to_nostr_build(og_path)
                        if os.path.exists(og_path):
                            os.remove(og_path)

                # 3. تجميع المنشور بدون أي روابط نصية داخل الخبر
                full_content = text
                if media_url:
                    full_content = f"{full_content}\n\n{media_url}".strip()

                if full_content.strip():
                    builder = EventBuilder.text_note(full_content)
                    await client.send_event_builder(builder)
                    print(f"تم نشر خبر نظيف ومصوّر بنجاح من قناة: {channel}")
                    save_history(post_unique_id)

            except Exception as e:
                print(f"خطأ في قناة {channel}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
