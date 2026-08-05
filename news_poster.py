import os
import asyncio
from telethon import TelegramClient
from nostr_sdk import Keys, Client, EventBuilder, Tag, NostrSigner, RelayUrl
import requests

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
NOSTR_NSEC = os.environ["NOSTR_NSEC"]

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
    
    # 1. إعداد Nostr
    keys = Keys.parse(NOSTR_NSEC)
    signer = NostrSigner.keys(keys)
    client = Client(signer)

    await client.add_relay(RelayUrl.parse("wss://relay.damus.io"))
    await client.add_relay(RelayUrl.parse("wss://nos.lol"))
    await client.connect()

    # 2. الاتصال بـ Telegram باستخدام البوت
    async with TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN) as tg_client:
        for channel in CHANNELS:
            try:
                messages = await tg_client.get_messages(channel, limit=1)
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
