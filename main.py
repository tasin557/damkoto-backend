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


async def get_seller_settings(seller_id: str) -> dict:
    """Fetch seller's auto-reply settings from Supabase."""
    defaults = {
        "bot_enabled": True,
        "comment_reply": True,
        "messenger_reply": True,
        "reply_tone": "formal",
        "reply_language": "bangla",
    }
    try:
        result = supabase.table("seller_settings").select("*").eq(
            "seller_id", seller_id).execute()
        if result.data:
            settings = result.data[0]
            return {
                "bot_enabled": settings.get("bot_enabled", True),
                "comment_reply": settings.get("comment_reply", True),
                "messenger_reply": settings.get("messenger_reply", True),
                "reply_tone": settings.get("reply_tone", "formal"),
                "reply_language": settings.get("reply_language", "bangla"),
            }
        return defaults
    except Exception as e:
        print(f"Error fetching seller settings: {e}")
        return defaults


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


def build_system_prompt(catalog: str, tone: str, language: str) -> str:
    """Build system prompt based on seller's settings."""
    
    # Tone instructions
    if tone == "casual":
        tone_instruction = 'তুমি বন্ধুসুলভ ভাবে কথা বলো। "তুমি" ব্যবহার করো। হালকা ইমোজি ব্যবহার করতে পারো।'
        address = "তুমি"
    else:
        tone_instruction = 'তুমি ভদ্র ও পেশাদার ভাবে কথা বলো। সবসময় "আপনি" ব্যবহার করো।'
        address = "আপনি"
    
    # Language instructions
    if language == "english":
        lang_instruction = "Always reply in English only. Do not use Bangla."
        base_prompt = f"""You are a customer service assistant for a Bangladeshi F-commerce store.

Product catalog:
{catalog}

Strict rules:

1. Gender neutrality: Never use gendered terms. Always address the customer as "you".

2. Complaint handling: If a customer mentions a torn, wrong color, damaged product or any issue, do NOT try to sell. Apologize first, then ask for their phone number.

3. Refunds: If they ask for money back, say "Sorry for the trouble. Please share your phone number and we'll resolve this quickly." Never refuse.

4. Angry customers: Stop selling. Apologize. Ask for contact info.

5. Product info: Only mention products from the catalog.

6. No repetition: Don't repeat the same product info more than once.

7. Tone: {tone_instruction} Keep it to 1-3 sentences max. No emoji for complaints.

8. Orders: Ask for name and phone number.

9. Delivery: Inside Dhaka 2-3 days, outside 3-5 days.

{lang_instruction}"""
    elif language == "mixed":
        lang_instruction = "Reply in a natural mix of Bangla and English (Banglish), like how young Bangladeshis text. Use Bangla script primarily but English words are okay."
        base_prompt = f"""তুমি একটি বাংলাদেশি F-commerce দোকানের কাস্টমার সার্ভিস অ্যাসিস্ট্যান্ট।

প্রোডাক্ট ক্যাটালগ:
{catalog}

কঠোর নিয়মাবলী:

১. লিঙ্গ নিরপেক্ষতা: কখনো "ভাই" বা "আপু" বলবে না। সবসময় "{address}" ব্যবহার করো।

২. অভিযোগ হ্যান্ডেলিং: পণ্য ছেঁড়া, ভুল রঙ, নষ্ট বা কোনো সমস্যার কথা বললে পণ্য বিক্রির চেষ্টা করবে না। আগে ক্ষমা চাও, তারপর ফোন নম্বর চাও।

৩. রিফান্ড: টাকা ফেরত চাইলে বলো "{address}র সমস্যার জন্য দুঃখিত। ফোন নম্বরটা দিন, আমরা দ্রুত সমাধান করবো।" কখনো অস্বীকার করবে না।

৪. রাগী কাস্টমার: রাগান্বিত হলে পণ্য বিক্রি বন্ধ করো। ক্ষমা চাও, যোগাযোগের তথ্য চাও।

৫. পণ্যের তথ্য: শুধু ক্যাটালগে থাকা পণ্যের কথা বলো।

৬. পুনরাবৃত্তি নিষেধ: একই পণ্যের তথ্য একবারের বেশি দেবে না।

৭. টোন: {tone_instruction} সর্বোচ্চ ১-৩ বাক্য। অভিযোগে ইমোজি নয়।

৮. অর্ডার করতে চাইলে: নাম ও ফোন নম্বর চাও।

৯. ডেলিভারি: ঢাকার ভেতরে ২-৩ দিন, বাইরে ৩-৫ দিন।

{lang_instruction}"""
    else:
        # Default: Bangla
        lang_instruction = "শুধুমাত্র বাংলায় উত্তর দাও।"
        base_prompt = f"""তুমি একটি বাংলাদেশি F-commerce দোকানের কাস্টমার সার্ভিস অ্যাসিস্ট্যান্ট।

প্রোডাক্ট ক্যাটালগ:
{catalog}

কঠোর নিয়মাবলী:

১. লিঙ্গ নিরপেক্ষতা: কখনো "ভাই" বা "আপু" বলবে না। সবসময় "{address}" ব্যবহার করো।

২. অভিযোগ হ্যান্ডেলিং: পণ্য ছেঁড়া, ভুল রঙ, নষ্ট বা কোনো সমস্যার কথা বললে পণ্য বিক্রির চেষ্টা করবে না। আগে ক্ষমা চাও, তারপর ফোন নম্বর চাও।

৩. রিফান্ড: টাকা ফেরত চাইলে বলো "{address}র সমস্যার জন্য দুঃখিত। ফোন নম্বরটা দিন, আমরা দ্রুত সমাধান করবো।" কখনো অস্বীকার করবে না।

৪. রাগী কাস্টমার: রাগান্বিত হলে পণ্য বিক্রি বন্ধ করো। ক্ষমা চাও, যোগাযোগের তথ্য চাও।

৫. পণ্যের তথ্য: শুধু ক্যাটালগে থাকা পণ্যের কথা বলো।

৬. পুনরাবৃত্তি নিষেধ: একই পণ্যের তথ্য একবারের বেশি দেবে না।

৭. টোন: {tone_instruction} সর্বোচ্চ ১-৩ বাক্য। অভিযোগে ইমোজি নয়।

৮. অর্ডার করতে চাইলে: নাম ও ফোন নম্বর চাও।

৯. ডেলিভারি: ঢাকার ভেতরে ২-৩ দিন, বাইরে ৩-৫ দিন।

{lang_instruction}"""
    
    return base_prompt


async def classify_and_reply(comment_text: str, seller_id: str, customer_id: str = None) -> str:
    catalog = await get_product_catalog(seller_id)
    conversation_history = await get_conversation_history(customer_id)
    settings = await get_seller_settings(seller_id)

    system_prompt = build_system_prompt(catalog, settings["reply_tone"], settings["reply_language"])

    # If language changed from what history was in, limit history to avoid language confusion
    lang = settings["reply_language"]
    if lang != "bangla":
        # Only keep last 2 messages to reduce Bangla pattern influence
        conversation_history = conversation_history[-2:] if len(conversation_history) > 2 else conversation_history
    
    # Add language enforcement reminder to the user message
    if lang == "english":
        enhanced_text = f"{comment_text}\n\n[IMPORTANT: Reply ONLY in English. Not Bangla.]"
    elif lang == "mixed":
        enhanced_text = f"{comment_text}\n\n[Reply in Banglish - mix of Bangla and English]"
    else:
        enhanced_text = comment_text

    messages = conversation_history + [{"role": "user", "content": enhanced_text}]

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


async def detect_and_create_order(seller_id: str, customer_id: str, customer_name: str, incoming_text: str, reply_text: str):
    """Use Claude to detect if the reply confirms an order. If so, create an order record."""
    try:
        detection = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            system="You detect if an order was confirmed in a conversation. Reply ONLY in this exact JSON format, nothing else. No markdown, no backticks.\n{\"is_order\": true/false, \"product_name\": \"...\", \"amount\": number_or_0, \"customer_name\": \"...\"}",
            messages=[
                {"role": "user", "content": f"Customer message: {incoming_text}\n\nShop reply: {reply_text}\n\nWas an order confirmed in the shop's reply? Extract product name, amount, and customer name if available."}
            ]
        )
        
        import json
        result_text = detection.content[0].text.strip()
        # Clean up any markdown formatting
        result_text = result_text.replace("```json", "").replace("```", "").strip()
        result = json.loads(result_text)
        
        if result.get("is_order"):
            order_data = {
                "seller_id": seller_id,
                "customer_id": customer_id,
                "status": "new",
                "amount": result.get("amount", 0),
                "product_name": result.get("product_name", ""),
                "customer_name": result.get("customer_name", customer_name),
                "notes": f"Auto-detected from chat"
            }
            supabase.table("orders").insert(order_data).execute()
            print(f"Order auto-created: {order_data}")
    except Exception as e:
        print(f"Order detection error (non-fatal): {e}")


async def save_messages_to_db(seller_id: str, customer_id: str, incoming_text: str, reply_text: str):
    try:
        supabase.table("messages").insert({
            "seller_id": seller_id,
            "customer_id": customer_id,
            "direction": "incoming",
            "content": incoming_text
        }).execute()
        if reply_text:
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
                        
                        # Check if bot and comment replies are enabled
                        settings = await get_seller_settings(seller_id)
                        if not settings["bot_enabled"] or not settings["comment_reply"]:
                            print(f"Comment reply disabled for seller {seller_id}")
                            # Still save incoming message
                            customer_id = await get_or_create_customer(seller_id, sender_id)
                            await save_messages_to_db(seller_id, customer_id, comment_text, "")
                            continue
                        
                        customer_id = await get_or_create_customer(seller_id, sender_id)
                        reply = await classify_and_reply(comment_text, seller_id, customer_id)
                        await post_comment_reply(comment_id, reply)
                        await save_messages_to_db(seller_id, customer_id, comment_text, reply)
                        # Detect if order was confirmed
                        customer_data = supabase.table("customers").select("name").eq("id", customer_id).single().execute()
                        cname = customer_data.data.get("name", "Unknown") if customer_data.data else "Unknown"
                        await detect_and_create_order(seller_id, customer_id, cname, comment_text, reply)
                        print(f"Comment replied: '{comment_text}' → '{reply}'")

        for messaging in entry.get("messaging", []):
            message = messaging.get("message", {})
            comment_text = message.get("text", "")
            sender_id = messaging.get("sender", {}).get("id", "")
            if sender_id != PAGE_ID and comment_text:
                seller_id = await get_or_create_seller()
                if not seller_id:
                    continue
                
                # Check if bot and messenger replies are enabled
                settings = await get_seller_settings(seller_id)
                if not settings["bot_enabled"] or not settings["messenger_reply"]:
                    print(f"Messenger reply disabled for seller {seller_id}")
                    customer_id = await get_or_create_customer(seller_id, sender_id)
                    await save_messages_to_db(seller_id, customer_id, comment_text, "")
                    continue
                
                customer_id = await get_or_create_customer(seller_id, sender_id)
                reply = await classify_and_reply(comment_text, seller_id, customer_id)
                await send_messenger_reply(sender_id, reply)
                await save_messages_to_db(seller_id, customer_id, comment_text, reply)
                # Detect if order was confirmed
                customer_data = supabase.table("customers").select("name").eq("id", customer_id).single().execute()
                cname = customer_data.data.get("name", "Unknown") if customer_data.data else "Unknown"
                await detect_and_create_order(seller_id, customer_id, cname, comment_text, reply)
                print(f"Messenger replied: '{comment_text}' → '{reply}'")

    return {"status": "ok"}


@app.get("/health")
async def health_check():
    return {"status": "DamKoto backend is running"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
