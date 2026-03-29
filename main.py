from supabase import create_client, Client
from fastapi import FastAPI, Request
from anthropic import Anthropic
from dotenv import load_dotenv
import httpx
import os
import json

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
            if p.get('category'):
                catalog += f" [ক্যাটাগরি: {p['category']}]"
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


async def get_customer_orders(seller_id: str, customer_id: str) -> str:
    """Fetch recent orders for this customer to inject into context."""
    try:
        if not customer_id:
            return "কোনো অর্ডার নেই।"
        orders = supabase.table("orders").select("*").eq(
            "seller_id", seller_id
        ).eq(
            "customer_id", customer_id
        ).order("created_at", desc=True).limit(5).execute()

        if not orders.data:
            return "এই কাস্টমারের কোনো অর্ডার নেই।"

        order_text = ""
        for o in orders.data:
            status_map = {
                "new": "নতুন (অপেক্ষমাণ)",
                "confirmed": "কনফার্মড",
                "paid": "পেমেন্ট হয়েছে",
                "shipped": "শিপড",
                "delivered": "ডেলিভার্ড",
            }
            status = status_map.get(o.get("status", ""), o.get("status", ""))
            order_text += f"- অর্ডার: {o.get('product_name', 'N/A')} | "
            order_text += f"৳{o.get('amount', 0)} | "
            order_text += f"স্ট্যাটাস: {status} | "
            order_text += f"কাস্টমার: {o.get('customer_name', 'N/A')}"
            if o.get('notes'):
                order_text += f" | নোট: {o['notes']}"
            order_text += "\n"
        return order_text
    except Exception as e:
        print(f"Error fetching orders: {e}")
        return "অর্ডার তথ্য পাওয়া যাচ্ছে না।"


async def classify_and_reply(
    comment_text: str,
    seller_id: str,
    customer_id: str = None,
    customer_name: str = "Unknown"
) -> str:
    catalog = await get_product_catalog(seller_id)
    conversation_history = await get_conversation_history(customer_id)
    customer_orders = await get_customer_orders(seller_id, customer_id)

    system_prompt = f"""তুমি একটি বাংলাদেশি F-commerce দোকানের কাস্টমার সার্ভিস অ্যাসিস্ট্যান্ট। তোমার কাজ হলো কাস্টমারদের সাথে বাংলায় সহজ ও বন্ধুত্বপূর্ণভাবে কথা বলা। তুমি দোকান মালিকের হয়ে কাজ করো।

═══════════════════════════════════════════
কাস্টমারের তথ্য (ডেটাবেস থেকে — এটাই সত্য)
═══════════════════════════════════════════
কাস্টমারের নাম: {customer_name}
কাস্টমারের অর্ডার হিস্ট্রি:
{customer_orders}

এই তথ্য ডেটাবেস থেকে এসেছে — এটাই সত্য। যদি অর্ডার হিস্ট্রিতে অর্ডার থাকে, তাহলে কাস্টমার আগেই অর্ডার দিয়েছে। তাকে আবার জিজ্ঞেস করো না "কী অর্ডার করতে চান?" বা "কোন পণ্য নিতে চান?"

═══════════════════════════════════════════
প্রোডাক্ট ক্যাটালগ
═══════════════════════════════════════════
{catalog}

═══════════════════════════════════════════
কঠোর নিয়মাবলী — এগুলো কখনো ভাঙবে না
═══════════════════════════════════════════

নিয়ম ১ — অর্ডার কনফার্ম করবে না:
তোমার অর্ডার কনফার্ম করার ক্ষমতা নেই। শুধু দোকান মালিক অর্ডার কনফার্ম করতে পারে। সব তথ্য সংগ্রহ করার পরে বলো:
"আপনার অর্ডারের তথ্য পেয়েছি! দোকান থেকে শীঘ্রই কনফার্ম করা হবে।"
কখনো বলবে না: "অর্ডার কনফার্ম হয়েছে", "Your order is confirmed", "অর্ডার হয়ে গেছে" বা এরকম কিছু যা বোঝায় অর্ডার চূড়ান্ত হয়ে গেছে।

নিয়ম ২ — আগের অর্ডার ভুলবে না:
উপরের "কাস্টমারের অর্ডার হিস্ট্রি" চেক করো। যদি সেখানে অর্ডার থাকে:
- কাস্টমারকে আবার অর্ডার দিতে বলবে না
- কাস্টমার "আমি অর্ডার দিয়েছি" বলে তাকে বলো: "হ্যাঁ, আপনার অর্ডার প্রসেসে আছে! দোকান থেকে শীঘ্রই কনফার্ম করা হবে।"
- কাস্টমার ডেলিভারি চার্জ বা অন্য প্রশ্ন করলে, তার অর্ডার রেফারেন্স করে উত্তর দাও
- কখনো বলবে না "কোন পণ্য নিতে চান?" বা "What would you like to order?" যদি সে ইতিমধ্যে অর্ডার দিয়ে থাকে

নিয়ম ৩ — অর্ডারের জন্য সব তথ্য সংগ্রহ করো:
কাস্টমার অর্ডার করতে চাইলে, এই ৪টি তথ্য অবশ্যই সংগ্রহ করতে হবে:
  ১) কোন প্রোডাক্ট (নাম ও ভ্যারিয়েন্ট যদি থাকে)
  ২) কাস্টমারের নাম
  ৩) ফোন নম্বর
  ৪) ডেলিভারি ঠিকানা (এলাকা, রোড, বাসা নম্বর)
কোনো তথ্য বাদ দেবে না। সব তথ্য পাওয়ার আগে "অর্ডার পেয়েছি" বলবে না।
সব তথ্য পেলে, সারাংশ দেখাও:
"আপনার অর্ডারের তথ্য:
📦 প্রোডাক্ট: [নাম] — ৳[দাম]
👤 নাম: [নাম]
📱 ফোন: [নম্বর]
📍 ঠিকানা: [ঠিকানা]
🚚 ডেলিভারি: ঢাকার ভেতরে ২-৩ দিন / ঢাকার বাইরে ৩-৫ দিন

সব ঠিক আছে? 'হ্যাঁ' বলুন।"

কাস্টমার "হ্যাঁ" বললে বলো:
"আপনার অর্ডারের তথ্য পেয়েছি! দোকান থেকে শীঘ্রই কনফার্ম করা হবে এবং পেমেন্টের ডিটেইলস জানানো হবে। ধন্যবাদ! 🙏"

নিয়ম ৪ — অভিযোগ হ্যান্ডেলিং:
কাস্টমার যদি রাগান্বিত হয়, পণ্যে সমস্যা বলে, রিফান্ড চায়, বা কোনো অভিযোগ করে — তাহলে সমাধান করার চেষ্টা করবে না। শুধু বলো:
"আপনার সমস্যার জন্য দুঃখিত। দোকান থেকে এখনই বিষয়টি দেখা হচ্ছে, অনুগ্রহ করে একটু অপেক্ষা করুন। 🙏"

নিয়ম ৫ — দাম নিয়ে দরাদরি করবে না:
কাস্টমার দাম কমাতে চাইলে বলো:
"দাম ফিক্সড, তবে দোকান মালিককে জিজ্ঞেস করে জানাচ্ছি! একটু অপেক্ষা করুন।"
নিজে কোনো ছাড় দেবে না।

নিয়ম ৬ — লিঙ্গ নিরপেক্ষতা:
কাস্টমার ছেলে না মেয়ে জানো না। কখনো "ভাই" বা "আপু" বলে সম্বোধন করবে না। সবসময় শুধু "আপনি" ব্যবহার করো এবং কাস্টমারের নাম ব্যবহার করো। দোকান মালিককেও "আপু" বা "ভাই" বলবে না — "দোকান থেকে" বা "দোকান মালিক" বলো।

নিয়ম ৭ — পুনরাবৃত্তি নিষেধ:
একই তথ্য বারবার দেবে না। কথোপকথনের ইতিহাস চেক করো — আগে যা বলা হয়েছে তা আবার বলবে না, যদি না কাস্টমার নিজে জিজ্ঞেস করে।

নিয়ম ৮ — ছবি মেসেজ:
কাস্টমার ছবি পাঠালে তুমি দেখতে পাবে না। বলো:
"ছবিটি পেয়েছি! প্রোডাক্টের নাম বা কালার বলুন, আমি এখনই দাম জানাতে পারি। অথবা দোকান থেকে শীঘ্রই দেখে জানানো হবে।"

নিয়ম ৯ — AI পরিচয়:
নিজে থেকে বলবে না যে তুমি AI। কাস্টমার জিজ্ঞেস করলে বলো:
"আমি দোকানের হয়ে আপনাকে সাহায্য করছি! প্রোডাক্ট, দাম, ডেলিভারি — যেকোনো প্রশ্নে আমি আছি। 😊"

═══════════════════════════════════════════
টোন ও ভাষা
═══════════════════════════════════════════
- কাস্টমার যে ভাষায় লেখে, সেই ভাষায় উত্তর দাও (বাংলা হলে বাংলায়, English হলে English এ)
- Banglish (রোমান হরফে বাংলা) লিখলেও বাংলা হরফে উত্তর দাও
- সহজ, উষ্ণ, বন্ধুত্বপূর্ণ ভাষা ব্যবহার করো
- রিপ্লাই সংক্ষিপ্ত রাখো — সর্বোচ্চ ২-৪ বাক্য
- 😊 ইমোজি শুধু উপযুক্ত সময়ে (সর্বোচ্চ ১-২ টি)। অভিযোগের সময় কখনো ইমোজি দেবে না
- Markdown ফরম্যাটিং (**bold**, *italic*) ব্যবহার করবে না — Messenger এ এগুলো কাজ করে না

═══════════════════════════════════════════
ডেলিভারি তথ্য
═══════════════════════════════════════════
- ঢাকার ভেতরে: ২-৩ দিন
- ঢাকার বাইরে: ৩-৫ দিন

শুধুমাত্র বাংলায় উত্তর দাও (যদি না কাস্টমার English এ লেখে)।"""

    messages = conversation_history + [{"role": "user", "content": comment_text}]

    # Fix consecutive same-role messages
    fixed_messages = []
    for msg in messages:
        if fixed_messages and fixed_messages[-1]["role"] == msg["role"]:
            fixed_messages[-1]["content"] += "\n" + msg["content"]
        else:
            fixed_messages.append(msg)

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=400,
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


async def get_or_create_customer(seller_id: str, facebook_user_id: str) -> dict:
    """Returns full customer dict (not just id) so we can pass name to AI."""
    try:
        customer = supabase.table("customers").select("*").eq(
            "facebook_user_id", facebook_user_id).eq(
            "seller_id", seller_id).execute()
        if customer.data:
            customer_data = customer.data[0]
            supabase.table("customers").update({
                "message_count": customer_data["message_count"] + 1
            }).eq("id", customer_data["id"]).execute()
            return customer_data
        else:
            name = await get_facebook_user_name(facebook_user_id)
            new_customer = supabase.table("customers").insert({
                "seller_id": seller_id,
                "facebook_user_id": facebook_user_id,
                "name": name,
                "message_count": 1
            }).execute()
            return new_customer.data[0]
    except Exception as e:
        print(f"Error getting/creating customer: {e}")
        return {"id": None, "name": "Unknown"}


async def save_messages_to_db(
    seller_id: str,
    customer_id: str,
    incoming_text: str,
    reply_text: str
):
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
        # Handle comment replies
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
                        customer = await get_or_create_customer(seller_id, sender_id)
                        customer_id = customer.get("id")
                        customer_name = customer.get("name", "Unknown")
                        reply = await classify_and_reply(
                            comment_text, seller_id, customer_id, customer_name
                        )
                        await post_comment_reply(comment_id, reply)
                        await save_messages_to_db(
                            seller_id, customer_id, comment_text, reply
                        )

        # Handle Messenger messages
        for messaging in entry.get("messaging", []):
            message = messaging.get("message", {})
            sender_id = messaging.get("sender", {}).get("id", "")

            # Check for image attachments
            attachments = message.get("attachments", [])
            has_image = any(
                a.get("type") == "image" for a in attachments
            )

            comment_text = message.get("text", "")

            # If image sent with no text, set a placeholder
            if has_image and not comment_text:
                comment_text = "[কাস্টমার একটি ছবি পাঠিয়েছে]"

            if sender_id != PAGE_ID and comment_text:
                seller_id = await get_or_create_seller()
                if not seller_id:
                    continue
                customer = await get_or_create_customer(seller_id, sender_id)
                customer_id = customer.get("id")
                customer_name = customer.get("name", "Unknown")
                reply = await classify_and_reply(
                    comment_text, seller_id, customer_id, customer_name
                )
                await send_messenger_reply(sender_id, reply)
                await save_messages_to_db(
                    seller_id, customer_id, comment_text, reply
                )

    return {"status": "ok"}


@app.get("/health")
async def health_check():
    return {"status": "DamKoto backend is running"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
