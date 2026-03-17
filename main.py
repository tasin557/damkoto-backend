from supabase import create_client, Client
from fastapi import FastAPI, Request
from anthropic import Anthropic
from dotenv import load_dotenv
import httpx
import os

load_dotenv()

app = FastAPI()
client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)
PAGE_ID = os.getenv("META_PAGE_ID")
VERIFY_TOKEN = "damkoto_webhook_token"

async def get_product_catalog(seller_id: str) -> str:
    try:
        products = supabase.table("products").select("*").eq(
            "seller_id", seller_id).eq("in_stock", True).execute()
        if not products.data:
            return "No products available"
        catalog = ""
        for p in products.data:
            catalog += f"- {p['name']}: BDT {p['price']}"
            if p.get('description'):
                catalog += f" ({p['description']})"
            catalog += "\n"
        return catalog
    except Exception as e:
        print(f"Error fetching products: {e}")
        return "No products available"

async def classify_and_reply(comment_text: str, seller_id: str) -> str:
    catalog = await get_product_catalog(seller_id)
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        system=f"""You are a friendly Bangladeshi F-commerce shop assistant.
        
Product catalog:
{catalog}

When a customer messages, reply in natural Bangla (the casual tone 
real F-commerce sellers use, not formal). Keep replies to 1-3 sentences.
If they ask about price, include the exact price from the catalog.
If they want to order, ask for their name and phone number.
If the item isn't in the catalog, say it's not available politely.""",
        messages=[{"role": "user", "content": comment_text}]
    )
    return response.content[0].text

async def post_comment_reply(comment_id: str, message: str):
    url = f"https://graph.facebook.com/v22.0/{comment_id}/comments"
    token = os.getenv("META_PAGE_ACCESS_TOKEN")
    async with httpx.AsyncClient() as http_client:
        resp = await http_client.post(url, params={
            "message": message,
            "access_token": token
        })
        print("Reply posted:", resp.json())
        return resp.json()

async def send_messenger_reply(sender_id: str, message: str):
    url = f"https://graph.facebook.com/v22.0/me/messages"
    token = os.getenv("META_PAGE_ACCESS_TOKEN")
    async with httpx.AsyncClient() as http_client:
        resp = await http_client.post(url, params={
            "access_token": token
        }, json={
            "recipient": {"id": sender_id},
            "message": {"text": message}
        })
        print("Messenger reply sent:", resp.json())
        return resp.json()

async def save_message_to_db(sender_id: str, message_text: str, reply_text: str):
    try:
        seller = supabase.table("sellers").select("*").eq(
            "facebook_page_id", PAGE_ID).execute()
        if not seller.data:
            seller = supabase.table("sellers").insert({
                "facebook_page_id": PAGE_ID,
                "page_name": "DamKoto Test Store"
            }).execute()
            seller_id = seller.data[0]["id"]
        else:
            seller_id = seller.data[0]["id"]

        customer = supabase.table("customers").select("*").eq(
            "facebook_user_id", sender_id).execute()
        if not customer.data:
            customer = supabase.table("customers").insert({
                "seller_id": seller_id,
                "facebook_user_id": sender_id,
                "message_count": 1
            }).execute()
            customer_id = customer.data[0]["id"]
        else:
            customer_id = customer.data[0]["id"]
            supabase.table("customers").update({
                "message_count": customer.data[0]["message_count"] + 1
            }).eq("id", customer_id).execute()

        supabase.table("messages").insert({
            "seller_id": seller_id,
            "customer_id": customer_id,
            "direction": "incoming",
            "content": message_text
        }).execute()

        supabase.table("messages").insert({
            "seller_id": seller_id,
            "customer_id": customer_id,
            "direction": "outgoing",
            "content": reply_text
        }).execute()

        print(f"Saved to database — customer: {sender_id}")

    except Exception as e:
        print(f"Database error: {e}")

@app.get("/webhook")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(params["hub.challenge"])
    return {"error": "Invalid verify token"}

@app.post("/webhook")
async def receive_webhook(request: Request):
    body = await request.json()
    print("Raw webhook body:", body)

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") == "feed":
                value = change.get("value", {})
                if value.get("item") == "comment" and value.get("verb") == "add":
                    comment_text = value.get("message", "")
                    comment_id = value.get("comment_id", "")
                    sender_id = value.get("from", {}).get("id", "")
                    if sender_id != PAGE_ID and comment_text:
                        print(f"New comment: '{comment_text}'")
                        seller = supabase.table("sellers").select("id").eq(
                            "facebook_page_id", PAGE_ID).execute()
                        seller_id = seller.data[0]["id"] if seller.data else None
                        reply = await classify_and_reply(comment_text, seller_id)
                        print(f"AI Reply: '{reply}'")
                        await post_comment_reply(comment_id, reply)

        for messaging in entry.get("messaging", []):
            message = messaging.get("message", {})
            comment_text = message.get("text", "")
            sender_id = messaging.get("sender", {}).get("id", "")
            if sender_id != PAGE_ID and comment_text:
                print(f"New message: '{comment_text}'")
                seller = supabase.table("sellers").select("id").eq(
                    "facebook_page_id", PAGE_ID).execute()
                seller_id = seller.data[0]["id"] if seller.data else None
                reply = await classify_and_reply(comment_text, seller_id)
                print(f"AI Reply: '{reply}'")
                await send_messenger_reply(sender_id, reply)
                await save_message_to_db(sender_id, comment_text, reply)

    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)