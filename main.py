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
            return "কোনো প্রোডাক্ট এখন স্টকে নেই।"
        catalog = ""
        for p in products.data:
            catalog += f"- {p['name']}: ৳{p['price']}"
            if p.get('description'):
                catalog += f" ({p['description']})"
            catalog += "\n"
        return catalog
    except Exception as e:
        print(f"Error fetching products: {e}")
        return "প্রোডাক্ট তথ্য পাওয়া যাচ্ছে না।"


async def get_conversation_history(customer_id: str) -> list:
    try:
        if not customer_id:
            return []
        history = supabase.table("messages")\
            .select("*")\
            .eq("customer_id", customer_id)\
            .order("created_at", desc=True)\
            .limit(10)\
            .execute()
        if not history.data:
            return []
        messages = []
        for msg in reversed(history.data):
            role = "user" if msg["direction"] == "incoming" else "assistant"
            messages.append({"role": role, "content": msg["content"]})
        return messages
    except Exception as e:
        print(f"Error fetching conversation history: {e}")
        return []


async def classify_and_reply(comment_text: str, seller_id: str, customer_id: str = None) -> str:
    catalog = await get_product_catalog(seller_id)
    conversation_history = await get_conversation_history(customer_id)

    system_prompt = f"""তুমি একটি বাংলাদেশি F-commerce দোকানের কাস্টমার সার্ভিস অ্যাসিস্ট্যান্ট।

প্রোডাক্ট ক্যাটালগ:
{catalog}

কঠোর নিয়মাবলী:

১. লিঙ্গ নিরপেক্ষতা: কখনো "ভাই" বা "আপু" বলবে না। সবসময় শুধু "আপনি" ব্যবহার করো।

২. অভিযোগ হ্যান্ডেলিং: পণ্য ছেঁড়া, ভুল রঙ, নষ্ট বা কোনো সমস্যার কথা বললে পণ্য বিক্রির চেষ্টা করবে না। আগে ক্ষমা চাও, তারপর ফোন নম্বর চাও।

৩. রিফান্ড: টাকা ফেরত চাইলে বলো "আপনার সমস্যার জন্য দুঃখিত। ফোন নম্বরটা দিন, আমরা দ্রুত সমাধান করবো।" কখনো অস্বীকার করবে না।

৪. রাগী কাস্টমার: রাগান্বিত হলে পণ্য বিক্রি বন্ধ করো। ক্ষমা চাও, যোগাযোগের তথ্য চাও।

৫. পণ্যের তথ্য: শুধু ক্যাটালগে থাকা পণ্যের কথা বলো।

৬. পুনরাবৃত্তি নিষেধ: একই পণ্যের তথ্য একবারের বেশি দেবে না।

৭. টোন: সহজ, উষ্ণ বাংলা। সর্বোচ্চ ১-৩ বাক্য। অভিযোগে ইমোজি নয়।

৮. অর্ডার করতে চাইলে: নাম ও ফোন নম্বর চাও।

৯. ডেলিভারি: ঢাকার ভেতরে ২-৩ দিন, বাইরে ৩-৫ দিন।

শুধুমাত্র বাংলায় উত্তর দাও।"""

    messages = conversation_history + [{"role": "user", "content": comment_text}]

    fixed_messages = []
    for msg in messages:
        if fixed_messages and fixed_messages[-1]["role"] == msg["role"]:
            fixed_messages[-1]["content"] += "\n" + msg["content"]
        else:
            fixed_messages.append(msg)

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        system=system_prompt,
        messages=fixed_messages
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
        print(f"Comment reply response: {resp.json()}")
        return resp.json()


async def send_messenger_reply(sender_id: str, message: str):
    url = "https://graph.facebook.com/v22.0/me/messages"
    token = os.getenv("META_PAGE_ACCESS_TOKEN")
    async with httpx.AsyncClient() as http_client:
        resp = await http_client.post(url, params={
            "access_token": token
        }, json={
            "recipient": {"id": sender_id},
            "message": {"text": message}
        })
        print(f"Messenger reply response: {resp.json()}")
        return resp.json()


async def get_or_create_seller() -> str | None:
    try:
        seller = supabase.table("sellers").select("id").eq(
            "facebook_page_id", PAGE_ID).execute()
        if seller.data:
            return seller.data[0]["id"]
        new_seller = supabase.table("sellers").insert({
            "facebook_page_id": PAGE_ID,
            "page_name": "DamKoto Test Store"
        }).execute()
        return new_seller.data[0]["id"]
    except Exception as e:
        print(f"Error getting/creating seller: {e}")
        return None


async def get_facebook_user_name(facebook_user_id: str) -> str:
    try:
        token = os.getenv("META_PAGE_ACCESS_TOKEN")
        url = f"https://graph.facebook.com/v22.0/{facebook_user_id}"
        async with httpx.AsyncClient() as http_client:
            resp = await http_client.get(url, params={
                "fields": "name",
                "access_token": token
            })
            data = resp.json()
            return data.get("name", "Unknown")
    except Exception as e:
        print(f"Error fetching user name: {e}")
        return "Unknown"


async def get_or_create_customer(seller_id: str, facebook_user_id: str) -> str | None:
    try:
        customer = supabase.table("customers").select("*").eq(
            "facebook_user_id", facebook_user_id).eq(
            "seller_id", seller_id).execute()
        if customer.data:
            customer_id = customer.data[0]["id"]
            supabase.table("customers").update({
                "message_count": customer.data[0]["message_count"] + 1
            }).eq("id", customer_id).execute()
            return customer_id
        else:
            name = await get_facebook_user_name(facebook_user_id)
            new_customer = supabase.table("customers").insert({
                "seller_id": seller_id,
                "facebook_user_id": facebook_user_id,
                "name": name,
                "message_count": 1
            }).execute()
            return new_customer.data[0]["id"]
    except Exception as e:
        print(f"Error getting/creating customer: {e}")
        return None


async def save_messages_to_db(seller_id: str, customer_id: str, incoming_text: str, reply_text: str):
    try:
        supabase.table("messages").insert({
            "seller_id": seller_id,
            "customer_id": customer_id,
            "direction": "incoming",
            "content": incoming_text
        }).execute()
        supabase.table("messages").insert({
            "seller_id": seller_id,
            "customer_id": customer_id,
            "direction": "outgoing",
            "content": reply_text
        }).execute()
    except Exception as e:
        print(f"Error saving messages: {e}")


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
                        seller_id = await get_or_create_seller()
                        if not seller_id:
                            continue
                        customer_id = await get_or_create_customer(seller_id, sender_id)
                        reply = await classify_and_reply(comment_text, seller_id, customer_id)
                        await post_comment_reply(comment_id, reply)
                        await save_messages_to_db(seller_id, customer_id, comment_text, reply)
                        print(f"Comment replied: '{comment_text}' → '{reply}'")

        for messaging in entry.get("messaging", []):
            message = messaging.get("message", {})
            comment_text = message.get("text", "")
            sender_id = messaging.get("sender", {}).get("id", "")
            if sender_id != PAGE_ID and comment_text:
                seller_id = await get_or_create_seller()
                if not seller_id:
                    continue
                customer_id = await get_or_create_customer(seller_id, sender_id)
                reply = await classify_and_reply(comment_text, seller_id, customer_id)
                await send_messenger_reply(sender_id, reply)
                await save_messages_to_db(seller_id, customer_id, comment_text, reply)
                print(f"Messenger replied: '{comment_text}' → '{reply}'")

    return {"status": "ok"}


@app.get("/health")
async def health_check():
    return {"status": "DamKoto backend is running"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
