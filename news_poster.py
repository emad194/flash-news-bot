import os
import asyncio
import requests
from telethon import TelegramClient
from telethon.tl.functions.messages import GetHistoryRequest
from nostr_sdk import Keys, Client, EventBuilder, NostrSigner, RelayUrl

# 1. قراءة المتغيرات وتفادي خطأ ValueError في حال كانت فارغة
api_id_env = os.environ.get("TELEGRAM_API_ID", "").strip()
api_hash_env = os.environ.get("TELEGRAM_API_HASH", "").strip()
bot_token_env = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
nostr_nsec_env = os.environ.get("NOSTR_NSEC", "").strip()

if not api_id_env or not api_hash_env or not bot_token_env or not nostr_nsec_env:
    raise ValueError("إحدى المتغيرات البيئية (Secrets) مفقودة أو فارغة! تحقق من إعدادات GitHub Secrets.")

API_ID = int(api_id_env)
API_HASH = api_hash_env
BOT_TOKEN = bot_token_env
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
        if "t.me/" in line or "join" in line.lower() or "subscribe" in line.lower():
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()

async def main():
    history = load_history()
    
    # إعداد Nostr
    keys = Keys.parse(NOSTR_NSEC)
    signer = NostrSigner.keys(keys)
    client = Client(signer)

    await client.add_relay(RelayUrl.parse("wss://relay.damus.io"))
    await client.add_relay(RelayUrl.parse("wss://nos.lol"))
    await client.connect()

    # الاتصال بـ Telegram باستخدام Bot Token
    async with TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN) as tg_client:
        for channel in CHANNELS:
            try:
                # جلب الكيان الخاص بالقناة أولاً لضمان عدم رفض الوصول عبر البوت
                entity = await tg_client.get_entity(channel)
                history_resp = await tg_client(GetHistoryRequest(
                    peer=entity,
                    limit=1,
                    offset_date=None,
                    offset_id=0,
                    max_id=0,
                    min_id=0,
                    add_offset=0,
                    hash=0
                ))
                
                messages = history_resp.messages
                if not messages:
                    continue
                
                msg = messages[0]
                post_unique_id = f"{channel}_{msg.id}"

                if post_unique_id in history:
                    continue

                text = clean_text(msg.text or "")
                
                if text or msg.media:
                    full_content = text
                    
                    if msg.media and hasattr(msg.media, 'photo'):
                        file_path = await tg_client.download_media(msg)
                        with open(file_path, 'rb') as f:
                            resp = requests.post('https://nostr.build/api/v2/upload/files', files={'file': f})
                            if resp.status_code == 200:
                                media_url = resp.json()['data'][0]['url']
                                full_content += f"\n\n{media_url}"
                        if os.path.exists(file_path):
                            os.remove(file_path)

                    if full_content.strip():
                        builder = EventBuilder.text_note(full_content, [])
                        await client.send_event_builder(builder)
                        print(f"تم بنجاح نشر خبر جديد من قناة: {channel}")
                        save_history(post_unique_id)

            except Exception as e:
                print(f"خطأ أثناء فحص القناة {channel}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
