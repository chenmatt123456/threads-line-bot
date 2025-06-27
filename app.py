# app.py (最終加固版 - Final Fortified)

import os
import asyncio
import re
from quart import Quart, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from playwright.async_api import async_playwright, TimeoutError

# --- 爬蟲核心函式 ---
async def get_threads_main_post(main_content_area):
    print("\n--- 正在沙盒內抓取主文 ---")
    post_container_selector = 'div[data-pressable-container="true"]'
    # 加固1：延長主文容器的等待時間
    main_post_container = main_content_area.locator(post_container_selector).first
    await main_post_container.wait_for(state='visible', timeout=15000) 
    print("✅ 成功鎖定主文容器！")
    try:
        more_button = main_post_container.get_by_role("button", name="more", exact=False)
        await more_button.click(timeout=3000)
        await asyncio.sleep(1)
    except TimeoutError:
        pass
    all_texts = await main_post_container.locator('span[dir="auto"]').all_inner_texts()
    TIMESTAMP_REGEX = re.compile(r'^\d+\s*(?:秒|分鐘|小時|天|週|[smhdw])$', re.IGNORECASE)
    potential_content = [frag.strip().removesuffix("翻譯").strip() for frag in all_texts if frag.strip() and not frag.strip().isdigit() and not TIMESTAMP_REGEX.match(frag.strip())]
    return "\n".join(potential_content[1:]) if len(potential_content) > 1 else "\n".join(potential_content)

async def get_threads_comments(page, main_content_area):
    print("\n--- 正在抓取留言 ---")
    scroll_count = 2
    for i in range(scroll_count):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(2)
    post_container_selector = 'div[data-pressable-container="true"]'
    all_containers_in_sandbox = main_content_area.locator(post_container_selector)
    container_count = await all_containers_in_sandbox.count()
    if container_count <= 1: return []
    comment_containers = await all_containers_in_sandbox.all()
    comments = []
    for container in comment_containers[1:]:
        try:
            all_texts = await container.locator('span[dir="auto"]').all_inner_texts()
            TIMESTAMP_REGEX = re.compile(r'^\d+\s*(?:秒|分鐘|小時|天|週|[smhdw])$', re.IGNORECASE)
            cleaned_fragments = [frag.strip().removesuffix("翻譯").strip() for frag in all_texts if frag.strip() and not frag.strip().isdigit() and not TIMESTAMP_REGEX.match(frag.strip())]
            if len(cleaned_fragments) > 1:
                comments.append({"author": cleaned_fragments[0], "text": "\n".join(cleaned_fragments[1:])})
        except Exception: continue
    return comments

async def get_full_threads_content_resilient(url: str):
    max_retries = 3
    for attempt in range(max_retries):
        print(f"\n--- 爬蟲任務開始，第 {attempt + 1} / {max_retries} 次嘗試 ---")
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
                page = await browser.new_page()
                try:
                    await page.goto(url, wait_until='networkidle', timeout=60000)
                    if "Threads" not in await page.title():
                        await browser.close()
                        continue
                    
                    # 加固2：為沙盒定位提供最長的耐心
                    main_content_area = page.locator('div[role="main"]').first
                    await main_content_area.wait_for(state='attached', timeout=20000)
                    
                    main_post = await get_threads_main_post(main_content_area)
                    comments = await get_threads_comments(page, main_content_area)
                    await browser.close()
                    print(f"✅ 第 {attempt + 1} 次嘗試成功！")
                    return {"main_post": main_post, "comments": comments}
                finally:
                    if 'browser' in locals() and not browser.is_closed():
                        await browser.close()
        except Exception as e:
            print(f"第 {attempt + 1} 次嘗試失敗，錯誤: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(5)
            else:
                raise e

# --- Quart Web 應用 & LINE Bot 邏輯 ---
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
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="收到，正在為您建立知識筆記..."))
    try:
        result = await get_full_threads_content_resilient(url)
        if result:
            reply_text = f"[主文]\n{result['main_post']}"
            if result["comments"]:
                reply_text += "\n\n---\n[留言]"
                for i, comment in enumerate(result["comments"]):
                    reply_text += f"\n{i + 1}. [{comment['author']}]: {comment['text']}"
            if len(reply_text) > 4900: 
                reply_text = reply_text[:4900] + "\n\n...(內容過長，已被截斷)"
            line_bot_api.push_message(event.source.user_id, TextSendMessage(text=reply_text))
        else:
            line_bot_api.push_message(event.source.user_id, TextSendMessage(text="抓取失敗，所有重試均無效。"))
    except Exception as e:
         print(f"爬蟲任務最終失敗: {e}")
         line_bot_api.push_message(event.source.user_id, TextSendMessage(text=f"處理過程中發生嚴重錯誤，請聯繫管理員。"))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)