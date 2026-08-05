import os
import asyncio
import requests
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage
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

def clean_text(text):
    if not text:
        return ""
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        # إزالة روابط الترويج والاشتراك
        if "t.me/" in line or "join" in line.lower() or "subscribe" in line.lower():
            continue
        cleaned.append(line)
    
    result = "\n".join(cleaned).strip()
    # تنظيف الأقواس التنسيقية الزائدة إن وجدت
    result = result.replace("[**", "**").replace("**]", "**")
    return result

async def main():
    history = load_history()
    
    # 2. إعداد Nostr
    keys = Keys.parse(NOSTR_NSEC)
    signer = NostrSigner.keys(keys)
    client = Client(signer)

    await client.add_relay(RelayUrl.parse("wss://relay.damus.io"))
    await client.add_relay(RelayUrl.parse("wss://nos.lol"))
    await client.connect()

    # 3. الاتصال بـ Telegram عبر Userbot
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
                    print(f"منشور سابق تم نشره مسبقاً من قناة {channel}، تخطي...")
                    continue

                text = clean_text(msg.text or "")
                media_url = None
                
                # جلب الصور أو الفيديوهات أو معاينات الصفحات
                if msg.media:
                    file_path = await tg_client.download_media(msg)
                    if file_path and os.path.exists(file_path):
                        try:
                            with open(file_path, 'rb') as f:
                                resp = requests.post('https://nostr.build/api/v2/upload/files', files={'file': f})
                                if resp.status_code == 200:
                                    media_url = resp.json()['data'][0]['url']
                        except Exception as upload_err:
                            print(f"خطأ أثناء رفع الميديا: {upload_err}")
                        finally:
                            if os.path.exists(file_path):
                                os.remove(file_path)

                # تجميع محتوى المنشور النهائى
                full_content = text
                if media_url:
                    full_content = f"{full_content}\n\n{media_url}".strip()

                if full_content.strip():
                    builder = EventBuilder.text_note(full_content)
                    await client.send_event_builder(builder)
                    print(f"تم بنجاح نشر خبر جديد مع الميديا من قناة: {channel}")
                    save_history(post_unique_id)

            except Exception as e:
                print(f"خطأ أثناء فحص القناة {channel}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
