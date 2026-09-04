import asyncio
import os
import re
import time
from threading import Thread
import requests
import urllib.parse

import discord
from discord import app_commands
from flask import Flask
from openai import AsyncOpenAI

# =========================
# HEALTH CHECK
# =========================
app = Flask(__name__)

@app.get("/")
def home():
    return "Tiến sĩ đá Senku đang bận làm thí nghiệm!"

def run_health_server():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

def keep_alive():
    Thread(target=run_health_server, daemon=True, name="health-server").start()

# =========================
# CẤU HÌNH API
# =========================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "minimax/minimax-m3:free").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1").strip().rstrip('/')

MAX_HISTORY_MESSAGES = 8
try: CHAT_CHANNEL_ID = int(os.getenv("CHAT_CHANNEL_ID", "0") or "0")
except ValueError: CHAT_CHANNEL_ID = 0

# KHỞI TẠO CLIENT OPENAI
aclient = AsyncOpenAI(
    base_url=OPENAI_BASE_URL,
    api_key=OPENAI_API_KEY,
    timeout=30.0 
)

# =========================
# TRA CỨU BÁCH KHOA TOÀN THƯ (WIKIPEDIA)
# =========================
def fetch_science_data(query):
    """Lấy tóm tắt từ Wikipedia tiếng Việt để Senku có thêm thông tin chính xác"""
    try:
        search_url = f"https://vi.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(query)}&utf8=&format=json"
        res = requests.get(search_url, timeout=3)
        search_data = res.json()
        if search_data.get("query", {}).get("search"):
            title = search_data["query"]["search"][0]["title"]
            summary_url = f"https://vi.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title)}"
            sum_res = requests.get(summary_url, timeout=3)
            if sum_res.status_code == 200:
                return sum_res.json().get("extract", "")
    except Exception as e:
        print(f"Lỗi tra cứu Wiki: {e}")
    return ""

# =========================
# TÍNH CÁCH SENKU ISHIGAMI
# =========================
BASE_SYSTEM_INSTRUCTION = """
BẠN ĐANG ĐÓNG VAI: Senku Ishigami từ anime/manga Dr. Stone.
VŨ TRỤ: Thế giới Đồ Đá (Stone World) - nơi nhân loại bị hóa đá 3700 năm và bạn đang xây dựng lại nền văn minh từ con số 0 bằng KHOA HỌC.

TÍNH CÁCH & ĐẶC ĐIỂM:
- Là một thiên tài khoa học 16-18 tuổi với kiến thức bách khoa toàn thư về Hóa học, Vật lý, Toán học, Sinh học, Kỹ thuật.
- Bạn là học sinh giỏi nhất của trường CHUYÊN TUYÊN QUANG.
- Tự tin, logic, thực dụng, đôi khi tỏ ra kiêu ngạo, xảo quyệt nhưng thực chất rất quan tâm đến bạn bè và nhân loại.
- CÂU CỬA MIỆNG BẮT BUỘC SỬ DỤNG: "10 tỷ phần trăm" (10 billion percent), "Khá khen đấy", "Thật phi lý", "Hí hứng rồi đây" (Get excited), "Khoa học sẽ giải quyết chuyện này".
- HÃY SỬ DỤNG HÀNH ĐỘNG/BIỂU CẢM: Đặt trong dấu * (VD: *cười khẩy*, *ngoáy tai*, *đổ hóa chất*, *gãi đầu*, *mắt sáng lên*).

NHIỆM VỤ KHOA HỌC:
- Giải thích các hiện tượng khoa học, công thức hóa học một cách rành mạch, dễ hiểu nhưng đầy tự hào.
- Nếu ai hỏi về phép thuật, tâm linh hay tôn giáo, hãy bác bỏ bằng khoa học "10 tỷ phần trăm" và gọi đó là điều ngu ngốc.
- Không bao giờ đoán mò. Mọi thứ đều tuân theo quy luật vật lý và hóa học. Nếu bạn được hệ thống mớm cho kiến thức bách khoa, hãy giả vờ đó là kiến thức nằm sẵn trong bộ não thiên tài của bạn.

QUY TẮC BẮT BUỘC:
1. XƯNG HÔ: Tự xưng là "ta", gọi đối phương là "ngươi", "cậu", "anh bạn" hoặc "tên ngốc". KHÔNG dùng "mình", "tôi", "em", "bạn".
2. Hành văn Tiếng Việt tự nhiên, ngầu, mang đậm chất khoa học.
3. KHÔNG tự xưng tên ở đầu câu.
"""

conversation_history = {}
channel_locks = {}

# =========================
# GỌI API (SỬ DỤNG OPENAI SDK)
# =========================
async def call_openai_stream(messages):
    try:
        response = await aclient.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            stream=True,
            temperature=0.7,
            frequency_penalty=0.2,
            max_tokens=1000,
            extra_headers={
                "HTTP-Referer": "https://discord.com",
                "X-OpenRouter-Title": "Senku Discord Bot" 
            }
        )
        async for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    except Exception as e:
        err_msg = str(e)
        if "429" in err_msg or "rate limit" in err_msg.lower():
            raise RuntimeError("RATE_LIMIT")
        elif "timeout" in err_msg.lower(): 
            raise RuntimeError("TIMEOUT")
        raise RuntimeError(f"Lỗi mạng: {err_msg}")

# =========================
# LỊCH SỬ & TIN NHẮN
# =========================
def split_discord_message(text, limit=2000):
    return [text[i:i + limit] for i in range(0, max(1, len(text)), limit)]

def is_triggered(message):
    if client.user and client.user.mentioned_in(message): return True
    if CHAT_CHANNEL_ID and message.channel.id == CHAT_CHANNEL_ID: return True
    return bool(re.match(r"^\s*senku(?:\s+ơi)?(?:\s*[,!:：-])?(?:\s|$)", message.content or "", flags=re.IGNORECASE))

def extract_user_text(message):
    text = message.content or ""
    if client.user: text = re.sub(rf"<@!?{client.user.id}>", "", text)
    text = re.sub(r"^\s*senku(?:\s+ơi)?(?:\s*[,!:：-])?\s*", "", text, flags=re.IGNORECASE)
    return text.strip() or "Ngươi gọi ta có việc gì? Rảnh rỗi thì ra nung chảy sắt đi!"

def build_openai_messages(message, user_text):
    channel_id = message.channel.id
    history = conversation_history.get(channel_id, [])
    
    # KÍCH HOẠT KỸ NĂNG TRA CỨU NẾU CÓ TỪ KHÓA KHOA HỌC
    system_instruction = BASE_SYSTEM_INSTRUCTION
    science_keywords = ["là gì", "công thức", "tạo ra", "phản ứng", "chất", "hóa học", "làm sao", "chế tạo", "nguyên lý"]
    if any(k in user_text.lower() for k in science_keywords):
        wiki_summary = fetch_science_data(user_text)
        if wiki_summary:
            system_instruction += f"\n\n[DỮ LIỆU KHOA HỌC TRA CỨU ĐƯỢC TỪ TỪ ĐIỂN: {wiki_summary}]"
            print(f"Đã tra cứu dữ liệu cho Senku: {wiki_summary[:50]}...")

    messages = [{"role": "system", "content": system_instruction}]
    for msg in history[-MAX_HISTORY_MESSAGES:]: messages.append(msg)
    messages.append({"role": "user", "content": f"{message.author.display_name}: {user_text}"})
    return messages

def save_conversation(message, user_text, bot_reply):
    channel_id = message.channel.id
    history = conversation_history.setdefault(channel_id, [])
    history.extend([
        {"role": "user", "content": f"{message.author.display_name}: {user_text}"},
        {"role": "assistant", "content": bot_reply},
    ])
    conversation_history[channel_id] = history[-MAX_HISTORY_MESSAGES:]

# =========================
# KHỞI TẠO DISCORD BOT
# =========================
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

@tree.command(name="clearmem", description="Xóa trí nhớ của Senku trong kênh này")
async def clearmem(interaction: discord.Interaction):
    channel_id = interaction.channel.id
    if channel_id in conversation_history:
        conversation_history[channel_id] = []
    await interaction.response.send_message("*Gõ nhẹ vào đầu* Vừa nãy ta đang nghĩ gì nhỉ? Thôi kệ, bắt tay vào thí nghiệm mới nào! (Đã xóa lịch sử chat 🧪)")

@client.event
async def on_ready():
    print(f"=====================================", flush=True)
    print(f"Tiến sĩ đá {client.user} đã sẵn sàng 10 tỷ phần trăm!", flush=True)
    print(f"=====================================", flush=True)
    try: await tree.sync()
    except Exception: pass

# =========================
# XỬ LÝ CHAT
# =========================
@client.event
async def on_message(message):
    if message.author.bot or not is_triggered(message): return

    lock = channel_locks.setdefault(message.channel.id, asyncio.Lock())
    async with lock:
        try:
            user_text = extract_user_text(message)
            # Chạy hàm build_openai_messages (có chứa requests đồng bộ) ở một thread riêng để không block bot
            messages = await asyncio.to_thread(build_openai_messages, message, user_text)

            raw_bot_reply = ""
            reply_message = None
            last_edit_time = 0
            edit_interval = 2.0 

            async with message.channel.typing():
                async for chunk in call_openai_stream(messages):
                    raw_bot_reply += chunk
                    
                    filtered_reply = re.sub(r'<think>.*?(?:</think>|$)', '', raw_bot_reply, flags=re.DOTALL|re.IGNORECASE).strip()
                    filtered_reply = re.sub(r'(?i)User Safety:.*', '', filtered_reply).strip()
                    filtered_reply = re.sub(r'(?i)Response Safety:.*', '', filtered_reply).strip()

                    now = time.time()
                    if now - last_edit_time > edit_interval:
                        display_text = filtered_reply
                        if not display_text:
                            display_text = "*(Đang tính toán công thức hóa học...)*"
                        display_text += " ✍️"
                        if len(display_text) < 1950:
                            if not reply_message:
                                reply_message = await message.reply(display_text, mention_author=False)
                            else:
                                try: await reply_message.edit(content=display_text)
                                except discord.DiscordException: pass
                        last_edit_time = now

            final_reply = re.sub(r'<think>.*?(?:</think>|$)', '', raw_bot_reply, flags=re.DOTALL|re.IGNORECASE).strip()
            final_reply = re.sub(r'(?i)User Safety:.*', '', final_reply).strip()
            final_reply = re.sub(r'(?i)Response Safety:.*', '', final_reply).strip()

            if not final_reply:
                final_reply = "*Ngoáy tai* Ngươi hỏi cái gì thiếu logic vậy? Giải thích lại xem nào."

            if final_reply:
                save_conversation(message, user_text, final_reply)
                if reply_message:
                    if len(final_reply) <= 2000:
                        await reply_message.edit(content=final_reply)
                    else:
                        await reply_message.edit(content=final_reply[:2000])
                        for chunk_str in split_discord_message(final_reply[2000:]):
                            await message.reply(chunk_str, mention_author=False)
                else:
                    for chunk_str in split_discord_message(final_reply):
                        await message.reply(chunk_str, mention_author=False)

        except Exception as error:
            err_str = str(error)
            if "RATE_LIMIT" in err_str:
                err_msg = "*Cười khẩy* Lượng điện năng cung cấp bị nghẽn rồi. Đợi máy phát điện chạy lại đã!"
            elif "TIMEOUT" in err_str:
                err_msg = "*Gãi đầu* Đường truyền bị đứt gãy rồi. Chắc tên ngốc Taiju lại làm đổ cột ăng-ten. Lát gọi lại nhé!"
            else:
                err_msg = f"*Mắt sáng lên* Ồ, một lỗi logic hệ thống! Khá khen đấy, mã lỗi đây: `{err_str[:200]}`"
            
            try:
                if 'reply_message' in locals() and reply_message:
                    await reply_message.edit(content=err_msg)
                else:
                    await message.reply(err_msg, mention_author=False)
            except discord.DiscordException: pass

# =========================
# VÒNG LẶP CHỐNG CRASH VÀ ÉP LỖI HIỆN LÊN LOG
# =========================
discord.utils.setup_logging()

if __name__ == "__main__":
    keep_alive()
    
    while True:
        try:
            print("Đang khởi động cỗ máy thời đại đá...", flush=True)
            client.run(DISCORD_TOKEN, log_handler=None) 
        except Exception as e:
            print(f">>> LỖI CRASH RỒI: {repr(e)}", flush=True)
            print("Đang chờ 30s để khởi động lại máy phát điện...", flush=True) 
            time.sleep(30)
