# app.py (Quart 終極版 - 完整程式碼)

import os
import asyncio
import re
from quart import Quart, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from playwright.async_api import async_playwright, TimeoutError

# ==========================================================================================
# 爬蟲核心邏輯
# ==========================================================================================

async def get_threads_main_post(main_content_area):
    """在指定的沙盒區域內抓取主文"""
    print("\n--- 正在沙盒內抓取主文 ---")
    post_container_selector = 'div[data-pressable-container="true"]'
    main_post_container = main_content_area.locator(post_container_selector).first
    await main_post_container.wait_for(state='visible', timeout=5000)
    print("✅ 成功鎖定主文容器！")

    try:
        more_button = main_post_container.get_by_role("button", name="more", exact=False)
        await more_button.click(timeout=2000)
        print("✅ 主文「查看更多」按鈕已點擊。")
        await asyncio.sleep(1)
    except TimeoutError:
        print("主文為短篇，無需展開。")

    all_texts = await main_post_container.locator('span[dir="auto"]').all_inner_texts()
    
    TIMESTAMP_REGEX = re.compile(r'^\d+\s*(?:秒|分鐘|小時|天|週|[smhdw])$', re.IGNORECASE)
    potential_content = []
    for text in all_texts:
        fragment = text.strip().removesuffix("翻譯").strip()
        if fragment and not fragment.isdigit() and not TIMESTAMP_REGEX.match(fragment):
            potential_content.append(fragment)
    
    # 移除作者名
    return "\n".join(potential_content[1:]) if len(potential_content) > 1 else "\n".join(potential_content)


async def get_threads_comments(page, main_content_area):
    """在指定的沙盒區域內抓取留言"""
    print("\n--- 正在沙盒內抓取留言 ---")
    
    scroll_count = 3
    print(f"將模擬向下捲動 {scroll_count} 次以載入留言...")
    for i in range(scroll_count):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(2)

    post_container_selector = 'div[data-pressable-container="true"]'
    all_containers_in_sandbox = main_content_area.locator(post_container_selector)
    
    container_count = await all_containers_in_sandbox.count()
    print(f"✅ 在沙盒內共鎖定 {container_count} 個容器（包含主文）。")

    if container_count <= 1:
        return []

    comment_containers = await all_containers_in_sandbox.all()
    comments = []
    
    print(f"正在分析 {len(comment_containers) - 1} 則留言...")
    for i, container in enumerate(comment_containers[1:]):
        try:
            all_texts = await container.locator('span[dir="auto"]').all_inner_texts()
            
            TIMESTAMP_REGEX = re.compile(r'^\d+\s*(?:秒|分鐘|小時|天|週|[smhdw])$', re.IGNORECASE)
            cleaned_fragments = []
            for text in all_texts:
                fragment = text.strip().removesuffix("翻譯").strip()
                if fragment and not fragment.isdigit() and not TIMESTAMP_REGEX.match(fragment):
                    cleaned_fragments.append(fragment)

            if len(cleaned_fragments) > 1:
                author = cleaned_fragments[0]
                text = "\n".join(cleaned_fragments[1:])
                comments.append({"author": author, "text": text})
        except Exception:
            continue
            
    return comments


async def get_full_threads_content_sandboxed(url: str):
    """主爬蟲協調函式"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until='networkidle')
            
            main_content_area = page.locator('div[role="main"]').first
            await main_content_area.wait_for(state='attached', timeout=7000)

            main_post = await get_threads_main_post(main_content_area)
            comments = await get_threads_comments(page, main_content_area)
            
            return {"main_post": main_post, "comments": comments}
        finally:
            await browser.close()


# ==========================================================================================
# Quart Web 應用 & LINE Bot 邏輯
# ==========================================================================================

app = Quart(__name__)

# 從環境變數讀取您的 LINE Bot 鑰匙
# 為了方便本地測試，如果找不到環境變數，就使用您直接貼上的值
CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.environ.get("CHANNEL_SECRET", "")

# 驗證金鑰是否已填寫
if "請在這裡貼上" in CHANNEL_ACCESS_TOKEN or "請在這裡貼上" in CHANNEL_SECRET:
    raise ValueError("錯誤：請先在 app.py 檔案中填寫您的 Channel Access Token 和 Channel Secret！")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# 接收 LINE 平台請求的進入點
@app.route("/callback", methods=['POST'])
async def callback():
    signature = request.headers['X-Line-Signature']
    body = await request.get_data(as_text=True)
    
    print(f"Request body: {body}")
    print(f"Signature: {signature}")

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("簽名驗證失敗！請立即檢查您的 Channel Secret。")
        abort(400)
    except Exception as e:
        print(f"處理請求時發生錯誤: {e}")
        abort(500)
    return 'OK'

# 處理收到的文字訊息
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text
    print(f"收到用戶訊息: {user_message}")

    if "threads.net" in user_message:
        print("偵測到 Threads 網址，正在建立背景任務...")
        asyncio.create_task(process_threads_url(event, user_message))
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="這看起來不是一個有效的 Threads.net 網址，請重新貼上。")
        )

# 執行爬蟲並回傳結果的非同步函式
async def process_threads_url(event, url):
    # 先發送一個「處理中」的訊息，優化使用者體驗
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="收到網址，正在啟動擷取引擎...請稍候約30-60秒。")
    )
    
    try:
        result = await get_full_threads_content_sandboxed(url)
        
        if result:
            reply_text = f"✅ 主文內容：\n---------------------\n{result['main_post']}"
            
            if result["comments"]:
                reply_text += f"\n\n✅ 留言列表 ({len(result['comments'])} 則)：\n---------------------"
                for i, comment in enumerate(result["comments"]):
                    reply_text += f"\n\n--- 留言 {i+1} | {comment['author']} ---\n{comment['text']}"
            
            if len(reply_text) > 4900:
                reply_text = reply_text[:4900] + "\n\n...(內容過長，已被截斷)"

            line_bot_api.push_message(event.source.user_id, TextSendMessage(text=reply_text))
        else:
            line_bot_api.push_message(event.source.user_id, TextSendMessage(text="抓取失敗，可能是無效網址、私密貼文或頁面結構無法識別。"))

    except Exception as e:
         print(f"爬蟲任務執行失敗: {e}")
         line_bot_api.push_message(event.source.user_id, TextSendMessage(text=f"處理過程中發生嚴重錯誤，請聯繫管理員。錯誤詳情: {e}"))

# 應用程式的啟動點
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)