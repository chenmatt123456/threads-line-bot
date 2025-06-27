# app.py (核心穩定版 - 只抓取主文)

import os
import asyncio
import re
from quart import Quart, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from playwright.async_api import async_playwright, TimeoutError

# --- 爬蟲核心邏輯 (只保留抓取主文的部分) ---
async def get_threads_main_post(main_content_area):
    """在指定的沙盒區域內，完美地抓取主文"""
    print("\n--- 正在沙盒內抓取主文 ---")
    post_container_selector = 'div[data-pressable-container="true"]'
    main_post_container = main_content_area.locator(post_container_selector).first
    await main_post_container.wait_for(state='visible', timeout=15000)
    print("✅ 成功鎖定主文容器！")
    try:
        more_button = main_post_container.get_by_role("button", name="more", exact=False)
        await more_button.click(timeout=3000)
        await asyncio.sleep(1)
    except TimeoutError:
        print("主文為短篇，無需展開。")
    
    all_texts = await main_post_container.locator('span[dir="auto"]').all_inner_texts()
    
    TIMESTAMP_REGEX = re.compile(r'^\d+\s*(?:秒|分鐘|小時|天|週|[smhdw])$', re.IGNORECASE)
    potential_content = [frag.strip().removesuffix("翻譯").strip() for frag in all_texts if frag.strip() and not frag.strip().isdigit() and not TIMESTAMP_REGEX.match(frag.strip())]
    
    # 移除作者名，返回純淨的主文
    return "\n".join(potential_content[1:]) if len(potential_content) > 1 else "\n".join(potential_content)


# --- 主爬蟲協調函式 (簡化版) ---
async def get_threads_content_stable(url: str):
    """主爬蟲協調函式，只呼叫主文抓取，穩定可靠"""
    max_retries = 3
    for attempt in range(max_retries):
        print(f"\n--- 主文抓取任務開始，第 {attempt + 1} / {max_retries} 次嘗試 ---")
        try:
            async with async_playwright() as p:
                # 使用最穩定的 Firefox 引擎
                browser = await p.firefox.launch(headless=True)
                page = await browser.new_page()
                try:
                    await page.goto(url, wait_until='networkidle', timeout=60000)
                    if "Threads" not in await page.title():
                        await browser.close()
                        continue
                    
                    main_content_area = page.locator('div[role="main"]').first
                    await main_content_area.wait_for(state='attached', timeout=20000)
                    
                    # 只抓取主文
                    main_post = await get_threads_main_post(main_content_area)
                    
                    await browser.close()
                    print(f"✅ 第 {attempt + 1} 次嘗試成功！")
                    return main_post
                finally:
                    if 'browser' in locals() and not browser.is_closed():
                        await browser.close()
        except Exception as e:
            print(f"第 {attempt + 1} 次嘗試失敗，錯誤: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(5)
            else:
                raise e

# --- Quart Web 應用 & LINE Bot 邏輯 (簡化版) ---
app = Quart(__name__)
CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.environ.get("CHANNEL_SECRET")
if not CHANNEL_ACCESS_TOKEN or not CHANNEL_SECRET: raise ValueError("環境變數未設定！")
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

@app.route("/callback", methods=['POST'])
async def callback():
    signature = request.headers['X-Line-Signature']
    body = await request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    if "threads." in event.message.text:
        asyncio.create_task(process_threads_url(event, event.message.text))
    else:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請貼上一個有效的 Threads 網址。"))

async def process_threads_url(event, url):
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="收到，正在為您擷取主文內容..."))
    try:
        # 呼叫簡化版的穩定函式
        main_post_content = await get_threads_content_stable(url)
        
        if main_post_content:
            # 直接回傳主文，不再有留言
            reply_text = main_post_content
            
            if len(reply_text) > 4950: 
                reply_text = reply_text[:4950] + "\n\n...(內容過長，已被截斷)"
            
            line_bot_api.push_message(event.source.user_id, TextSendMessage(text=reply_text))
        else:
            line_bot_api.push_message(event.source.user_id, TextSendMessage(text="抓取失敗，或該貼文沒有文字內容。"))
    except Exception as e:
         print(f"爬蟲任務最終失敗: {e}")
         line_bot_api.push_message(event.source.user_id, TextSendMessage(text=f"處理過程中發生嚴重錯誤，請聯繫管理員。"))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=7860) # 直接使用 Hugging Face 的端口