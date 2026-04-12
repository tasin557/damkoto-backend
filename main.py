from supabase import create_client, Client
from fastapi import FastAPI, Request
from anthropic import Anthropic
from dotenv import load_dotenv
import httpx
import json
import math
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


# ═══════════════════════════════════════════
# DATA FETCHING FUNCTIONS
# ═══════════════════════════════════════════

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


async def get_product_price(seller_id: str, product_name: str) -> float:
    """Look up a product's price by name (fuzzy match)."""
    try:
        products = supabase.table("products").select("name, price").eq(
            "seller_id", seller_id).eq("in_stock", True).execute()
        if not products.data:
            return 0
        for p in products.data:
            if p["name"].lower().strip() == product_name.lower().strip():
                return float(p["price"])
        for p in products.data:
            if product_name.lower().strip() in p["name"].lower().strip() or \
               p["name"].lower().strip() in product_name.lower().strip():
                return float(p["price"])
        return 0
    except Exception:
        return 0


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
            content = msg["content"]
            # Clean up any JSON that leaked into conversation history
            # (from the previous JSON-response version)
            if role == "assistant" and content.strip().startswith("{"):
                try:
                    parsed = json.loads(content)
                    content = parsed.get("reply", content)
                except (json.JSONDecodeError, TypeError):
                    pass
            messages.append({"role": role, "content": content})
        return messages
    except Exception as e:
        print(f"Error fetching conversation history: {e}")
        return []


async def get_customer_orders(seller_id: str, customer_id: str) -> str:
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
                "new": "নতুন (কনফার্মের অপেক্ষায়)",
                "confirmed": "কনফার্মড",
                "paid": "পেমেন্ট হয়েছে",
                "shipped": "শিপড (ডেলিভারির পথে)",
                "delivered": "ডেলিভার্ড",
                "cancelled": "ক্যান্সেলড",
            }
            status = status_map.get(o.get("status", ""), o.get("status", ""))
            order_text += f"- অর্ডার: {o.get('product_name', 'N/A')} | "
            order_text += f"৳{o.get('amount', 0)} | "
            order_text += f"স্ট্যাটাস: {status}"
            if o.get('notes'):
                order_text += f" | {o['notes']}"
            order_text += "\n"
        return order_text
    except Exception as e:
        print(f"Error fetching orders: {e}")
        return "অর্ডার তথ্য পাওয়া যাচ্ছে না।"


async def get_delivery_settings(seller_id: str) -> str:
    try:
        result = supabase.table("delivery_settings").select("*").eq(
            "seller_id", seller_id
        ).eq("is_enabled", True).execute()

        if not result.data:
            return "ডেলিভারি: ঢাকার ভেতরে ২-৩ দিন, ঢাকার বাইরে ৩-৫ দিন।"

        division_names = {
            "dhaka": "ঢাকা", "chittagong": "চট্টগ্রাম", "rajshahi": "রাজশাহী",
            "khulna": "খুলনা", "barishal": "বরিশাল", "sylhet": "সিলেট",
            "rangpur": "রংপুর", "mymensingh": "ময়মনসিংহ"
        }

        text = ""
        for d in result.data:
            div_name = division_names.get(d["division"], d["division"])
            text += f"- {div_name}: ৳{d['delivery_charge']}, {d['estimated_days_min']}-{d['estimated_days_max']} দিন\n"

        enabled_divisions = [d["division"] for d in result.data]
        all_divisions = list(division_names.keys())
        disabled = [division_names[d] for d in all_divisions if d not in enabled_divisions]
        if disabled:
            text += f"- ডেলিভারি হয় না: {', '.join(disabled)}\n"

        return text
    except Exception as e:
        print(f"Error fetching delivery settings: {e}")
        return "ডেলিভারি তথ্য পাওয়া যাচ্ছে না।"


async def get_payment_settings(seller_id: str) -> str:
    try:
        result = supabase.table("payment_settings").select("*").eq(
            "seller_id", seller_id
        ).eq("is_enabled", True).execute()

        if not result.data:
            return "পেমেন্ট সেটিংস এখনো কনফিগার করা হয়নি।"

        type_names = {
            "cod": "ক্যাশ অন ডেলিভারি (COD)",
            "bkash": "bKash", "nagad": "Nagad",
            "rocket": "Rocket", "bank_transfer": "ব্যাংক ট্রান্সফার"
        }

        text = ""
        for p in result.data:
            name = type_names.get(p["payment_type"], p["payment_type"])
            if p["payment_type"] == "cod":
                text += f"- {name}: চালু আছে\n"
            else:
                text += f"- {name}: নম্বর {p.get('account_number', 'N/A')}"
                if p.get("account_type"):
                    text += f" ({p['account_type']})"
                text += "\n"
                if p.get("instructions"):
                    text += f"  নির্দেশনা: {p['instructions']}\n"
        return text
    except Exception as e:
        print(f"Error fetching payment settings: {e}")
        return "পেমেন্ট তথ্য পাওয়া যাচ্ছে না।"


async def get_shop_settings(seller_id: str) -> dict:
    defaults = {
        "shop_name": None,
        "advance_payment_type": "none",
        "advance_percentage": 0,
        "free_delivery_enabled": False,
        "free_delivery_threshold": 0,
    }
    try:
        result = supabase.table("shop_settings").select("*").eq(
            "seller_id", seller_id
        ).single().execute()
        if result.data:
            return result.data
        return defaults
    except Exception:
        return defaults


# ═══════════════════════════════════════════
# ORDER CREATION
# ═══════════════════════════════════════════

async def create_order_from_ai(
    seller_id: str, customer_id: str, order_data: dict
) -> bool:
    try:
        product_name = order_data.get("product_name", "")
        customer_name = order_data.get("customer_name", "")
        customer_phone = order_data.get("customer_phone", "")
        delivery_address = order_data.get("delivery_address", "")
        amount = float(order_data.get("amount", 0))
        delivery_charge = float(order_data.get("delivery_charge", 0))

        if not amount and product_name:
            amount = await get_product_price(seller_id, product_name)

        total = amount + delivery_charge

        # Build notes with all customer details
        notes_parts = []
        if customer_phone:
            notes_parts.append(f"ফোন: {customer_phone}")
        if delivery_address:
            notes_parts.append(f"ঠিকানা: {delivery_address}")
        if delivery_charge:
            notes_parts.append(f"ডেলিভারি: ৳{int(delivery_charge)}")
        notes = " | ".join(notes_parts) if notes_parts else ""

        supabase.table("orders").insert({
            "seller_id": seller_id,
            "customer_id": customer_id,
            "customer_name": customer_name or "Unknown",
            "product_name": product_name or "Unknown",
            "amount": int(total),
            "status": "new",
            "notes": notes,
        }).execute()

        # Update customer phone if we got it
        if customer_phone and customer_id:
            supabase.table("customers").update({
                "phone": customer_phone
            }).eq("id", customer_id).execute()

        print(f"ORDER CREATED: {product_name} for {customer_name} - ৳{int(total)}")
        return True
    except Exception as e:
        print(f"Error creating order: {e}")
        return False


# ═══════════════════════════════════════════
# MESSENGER NOTIFICATION (for status updates)
# ═══════════════════════════════════════════

async def notify_customer_status_change(
    customer_facebook_id: str, order: dict, new_status: str,
    seller_id: str = None
):
    """Send Messenger notification on confirmed and shipped status changes."""
    product = order.get('product_name', 'আপনার অর্ডার')
    amount = order.get('amount', 0)
    notes = order.get('notes', '')

    if new_status == "confirmed":
        # Build payment info for the confirmation message
        payment_text = ""
        if seller_id:
            try:
                pay_res = supabase.table("payment_settings").select("*").eq(
                    "seller_id", seller_id
                ).eq("is_enabled", True).execute()
                if pay_res.data:
                    for p in pay_res.data:
                        if p["payment_type"] == "cod":
                            payment_text += "ক্যাশ অন ডেলিভারি (COD) তে পেমেন্ট করতে পারবেন।\n"
                        elif p["payment_type"] == "bkash" and p.get("account_number"):
                            payment_text += f"bKash: {p['account_number']} ({p.get('account_type', 'personal')}) এ Send Money করুন।\n"
                        elif p["payment_type"] == "nagad" and p.get("account_number"):
                            payment_text += f"Nagad: {p['account_number']} এ পেমেন্ট করুন।\n"
                        elif p["payment_type"] == "rocket" and p.get("account_number"):
                            payment_text += f"Rocket: {p['account_number']} এ পেমেন্ট করুন।\n"

                # Check advance payment
                shop_res = supabase.table("shop_settings").select("*").eq(
                    "seller_id", seller_id
                ).single().execute()
                if shop_res.data:
                    adv_type = shop_res.data.get("advance_payment_type", "none")
                    if adv_type == "full":
                        payment_text += f"\nপুরো ৳{amount} আগে পেমেন্ট করতে হবে।"
                    elif adv_type == "partial":
                        pct = shop_res.data.get("advance_percentage", 0)
                        adv_amount = math.ceil(float(amount) * pct / 100)
                        payment_text += f"\n৳{adv_amount} অ্যাডভান্স পেমেন্ট করুন ({pct}%)। বাকি ডেলিভারির সময়।"
            except Exception as e:
                print(f"Error fetching payment info for notification: {e}")

        message = f"আপনার অর্ডার কনফার্ম হয়েছে! 🎉\n\n"
        message += f"📦 {product}\n💰 মোট: ৳{amount}\n\n"
        if payment_text:
            message += f"পেমেন্ট:\n{payment_text}\n\n"
            message += "পেমেন্ট করার পর TrxID পাঠান।"
        else:
            message += "পেমেন্টের ডিটেইলস শীঘ্রই জানানো হবে।"

    elif new_status == "shipped":
        message = f"আপনার অর্ডার ({product}) শিপ করা হয়েছে! 🚚\n"
        message += f"মোট: ৳{amount}\n"
        message += "শীঘ্রই পেয়ে যাবেন!"

    elif new_status == "cancelled":
        message = f"দুঃখিত, আপনার অর্ডার ({product}) ক্যান্সেল করা হয়েছে। কোনো প্রশ্ন থাকলে জানান। 🙏"

    else:
        # Don't send notifications for other status changes
        return

    try:
        await send_messenger_reply(customer_facebook_id, message)
        print(f"Status notification sent: {new_status} to {customer_facebook_id}")
    except Exception as e:
        print(f"Error sending status notification: {e}")


# ═══════════════════════════════════════════
# AI CLASSIFICATION & REPLY
# ═══════════════════════════════════════════

async def classify_and_reply(
    comment_text: str,
    seller_id: str,
    customer_id: str = None,
    customer_name: str = "Unknown"
) -> dict:
    catalog = await get_product_catalog(seller_id)
    conversation_history = await get_conversation_history(customer_id)
    customer_orders = await get_customer_orders(seller_id, customer_id)
    delivery_info = await get_delivery_settings(seller_id)
    payment_info = await get_payment_settings(seller_id)
    shop = await get_shop_settings(seller_id)

    advance_text = ""
    if shop.get("advance_payment_type") == "full":
        advance_text = "অর্ডার কনফার্ম হলে পুরো টাকা আগে পেমেন্ট করতে হবে।"
    elif shop.get("advance_payment_type") == "partial":
        pct = shop.get("advance_percentage", 0)
        advance_text = f"অর্ডার কনফার্ম হলে মোট মূল্যের {pct}% অ্যাডভান্স পেমেন্ট করতে হবে। বাকি টাকা ডেলিভারির সময় দিতে হবে। অ্যাডভান্স সবসময় পূর্ণ সংখ্যায় রাউন্ড আপ করো।"
    else:
        advance_text = "কোনো অ্যাডভান্স পেমেন্ট লাগবে না।"

    free_delivery_text = ""
    if shop.get("free_delivery_enabled") and shop.get("free_delivery_threshold", 0) > 0:
        free_delivery_text = f"৳{shop['free_delivery_threshold']} বা তার বেশি অর্ডারে ডেলিভারি ফ্রি।"

    shop_name = shop.get("shop_name") or "দোকান"

    system_prompt = f"""তুমি "{shop_name}" এর কাস্টমার সার্ভিস অ্যাসিস্ট্যান্ট — একটি বাংলাদেশি F-commerce দোকান। তুমি দোকান মালিকের হয়ে কাজ করো।

═══ কাস্টমারের তথ্য (ডেটাবেস থেকে — এটাই সত্য) ═══
কাস্টমারের নাম: {customer_name}
কাস্টমারের অর্ডার হিস্ট্রি:
{customer_orders}

এই তথ্য ডেটাবেস থেকে এসেছে — এটাই সত্য। যদি অর্ডার হিস্ট্রিতে অর্ডার থাকে, তাহলে কাস্টমার আগেই অর্ডার দিয়েছে।

═══ প্রোডাক্ট ক্যাটালগ ═══
{catalog}

═══ ডেলিভারি তথ্য ═══
{delivery_info}
{free_delivery_text}

═══ পেমেন্ট তথ্য ═══
{payment_info}
{advance_text}

সতর্কতা — পেমেন্ট নম্বর:
উপরে যে মোবাইল ব্যাংকিং নম্বর দেওয়া আছে, সেটাই কাস্টমারকে দাও। কখনো নিজে থেকে নম্বর বানিয়ে দেবে না।

═══ কঠোর নিয়মাবলী ═══

নিয়ম ১ — অর্ডার কনফার্ম করবে না:
শুধু দোকান মালিক অর্ডার কনফার্ম করতে পারে। বলো: "আপনার অর্ডারের তথ্য পেয়েছি! দোকান থেকে শীঘ্রই কনফার্ম করা হবে।"
কখনো বলবে না: "অর্ডার কনফার্ম হয়েছে" বা "অর্ডার হয়ে গেছে"।

নিয়ম ২ — আগের অর্ডার ভুলবে না:
উপরের অর্ডার হিস্ট্রি চেক করো। অর্ডার থাকলে:
- আবার অর্ডার দিতে বলবে না
- "আমি অর্ডার দিয়েছি" বললে বলো: "হ্যাঁ, আপনার অর্ডার প্রসেসে আছে!"
- অর্ডার স্ট্যাটাস জানতে চাইলে হিস্ট্রি থেকে সরাসরি স্ট্যাটাস বলো
- নতুন প্রোডাক্ট অর্ডার করতে চাইলে সেটা অনুমতি দাও

নিয়ম ৩ — অর্ডারের জন্য ৪টি তথ্য সংগ্রহ করো:
  ১) প্রোডাক্ট (নাম ও ভ্যারিয়েন্ট)
  ২) কাস্টমারের নাম
  ৩) ফোন নম্বর
  ৪) ডেলিভারি ঠিকানা
সব তথ্য পাওয়ার আগে "অর্ডার পেয়েছি" বলবে না।
সব তথ্য পেলে সারাংশ দেখাও এবং "সব ঠিক আছে? 'হ্যাঁ' বলুন।" বলো।

নিয়ম ৪ — পেমেন্ট নম্বর সঠিকভাবে দাও। ভুল নম্বর দিলে কাস্টমারের টাকা হারাবে।

নিয়ম ৫ — অভিযোগে বলো: "দোকান থেকে বিষয়টি দেখা হচ্ছে, একটু অপেক্ষা করুন। 🙏"

নিয়ম ৬ — দরাদরিতে বলো: "দাম ফিক্সড, দোকান মালিককে জিজ্ঞেস করে জানাচ্ছি!"

নিয়ম ৭ — কখনো "ভাই" বা "আপু" বলবে না। "আপনি" এবং কাস্টমারের নাম ব্যবহার করো। দোকান মালিককেও "আপু" বলবে না।

নিয়ম ৮ — একই তথ্য বারবার দেবে না।

নিয়ম ৯ — ছবি পাঠালে বলো: "ছবিটি পেয়েছি! প্রোডাক্টের নাম বলুন।"

নিয়ম ১০ — AI পরিচয় জিজ্ঞেস করলে বলো: "আমি দোকানের হয়ে সাহায্য করছি!"

নিয়ম ১১ — ডেলিভারি না হলে বলো: "দুঃখিত, এই এলাকায় ডেলিভারি সেবা নেই।"

নিয়ম ১২ — দোকান মালিকের কাছে হস্তান্তর:
নিচের যেকোনো পরিস্থিতিতে needs_seller = true করো এবং কাস্টমারকে বলো "দোকান মালিক শীঘ্রই যোগাযোগ করবে":
- অর্ডার ক্যান্সেল করতে চায়
- অর্ডার এডিট/পরিবর্তন করতে চায় (প্রোডাক্ট, সংখ্যা, ঠিকানা)
- রিটার্ন বা রিফান্ড চায়
- পণ্যে সমস্যা বা অভিযোগ
- সরাসরি দোকান মালিকের সাথে কথা বলতে চায় ("I want to talk to the owner", "মালিকের সাথে কথা বলতে চাই")
- এমন প্রশ্ন যার উত্তর তোমার কাছে নেই
- কাস্টমার হতাশ বা বিরক্ত হচ্ছে
- কাস্টম অর্ডার বা বিশেষ অনুরোধ
- দরাদরি করছে

বলো: "আপনার অনুরোধ পেয়েছি। দোকান মালিক শীঘ্রই আপনার সাথে যোগাযোগ করবে। একটু অপেক্ষা করুন। 🙏"

═══ টোন ═══
- কাস্টমারের ভাষায় উত্তর দাও
- সংক্ষিপ্ত: ২-৪ বাক্য
- Markdown ব্যবহার করবে না

═══ রেসপন্স ফরম্যাট (অবশ্যই মানতে হবে) ═══

তোমার রেসপন্স অবশ্যই এই JSON ফরম্যাটে হবে। শুধু JSON, অন্য কিছু নয়:

{{
  "reply": "কাস্টমারকে পাঠানো মেসেজ",
  "order_submitted": false,
  "order_data": null,
  "needs_seller": false
}}

শুধুমাত্র যখন কাস্টমার সারাংশ দেখার পরে "হ্যাঁ"/"yes"/"ok" বলে তখনই order_submitted = true করো:

{{
  "reply": "আপনার অর্ডারের তথ্য পেয়েছি! দোকান থেকে শীঘ্রই কনফার্ম করা হবে। ধন্যবাদ! 🙏",
  "order_submitted": true,
  "order_data": {{
    "product_name": "3x Pant, 1x Pink Shirt",
    "amount": 6800,
    "customer_name": "Tasin",
    "customer_phone": "01711328914",
    "delivery_address": "35/2 ahamedbagh, basabo",
    "delivery_charge": 70
  }},
  "needs_seller": false
}}

product_name: সব আইটেম ও সংখ্যা লেখো। যেমন: "3x Pant" বা "Baggy Pants, Pink Shirt, লাল কটন শাড়ি"।
amount: সব প্রোডাক্টের মোট দাম (ডেলিভারি ছাড়া)।
delivery_charge: আলাদা ফিল্ড।

needs_seller = true: যখন কাস্টমারের অনুরোধ দোকান মালিকের দরকার।

শুধু JSON দাও। কোনো ব্যাখ্যা দেবে না।"""

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
        max_tokens=500,
        system=system_prompt,
        messages=fixed_messages
    )

    raw_text = response.content[0].text.strip()
    print(f"AI raw response: {raw_text[:300]}")

    # Parse JSON response
    try:
        cleaned = raw_text
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        parsed = json.loads(cleaned)
        return {
            "reply_text": parsed.get("reply", raw_text),
            "order_submitted": parsed.get("order_submitted", False),
            "order_data": parsed.get("order_data", None),
            "needs_seller": parsed.get("needs_seller", False),
        }
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"JSON parse error: {e}, raw: {raw_text[:200]}")
        return {
            "reply_text": raw_text,
            "order_submitted": False,
            "order_data": None,
            "needs_seller": False,
        }


# ═══════════════════════════════════════════
# MESSENGER / COMMENT REPLY
# ═══════════════════════════════════════════

async def post_comment_reply(comment_id: str, message: str):
    url = f"https://graph.facebook.com/v22.0/{comment_id}/comments"
    token = os.getenv("META_PAGE_ACCESS_TOKEN")
    async with httpx.AsyncClient() as http_client:
        resp = await http_client.post(url, params={
            "message": message, "access_token": token
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


# ═══════════════════════════════════════════
# SELLER / CUSTOMER MANAGEMENT
# ═══════════════════════════════════════════

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
                "fields": "name", "access_token": token
            })
            data = resp.json()
            return data.get("name", "Unknown")
    except Exception as e:
        print(f"Error fetching user name: {e}")
        return "Unknown"


async def get_or_create_customer(seller_id: str, facebook_user_id: str) -> dict:
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
    seller_id: str, customer_id: str,
    incoming_text: str, reply_text: str
):
    """Save messages — only the reply text, NOT the raw JSON."""
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
            "content": reply_text  # This is the clean reply, not JSON
        }).execute()
    except Exception as e:
        print(f"Error saving messages: {e}")


# ═══════════════════════════════════════════
# MESSAGE HANDLER
# ═══════════════════════════════════════════

async def handle_message(
    seller_id: str, customer: dict,
    comment_text: str, reply_func, reply_target: str,
):
    customer_id = customer.get("id")
    customer_name = customer.get("name", "Unknown")

    # Check if seller is actively replying (pause AI)
    if await is_seller_active(customer_id):
        print(f"AI PAUSED: seller is active for customer {customer_name}")
        # Save incoming message but don't reply
        try:
            supabase.table("messages").insert({
                "seller_id": seller_id,
                "customer_id": customer_id,
                "direction": "incoming",
                "content": comment_text
            }).execute()
        except Exception:
            pass
        return

    result = await classify_and_reply(
        comment_text, seller_id, customer_id, customer_name
    )

    reply_text = result["reply_text"]

    # Save order if AI submitted one
    if result["order_submitted"] and result["order_data"]:
        order_data = result["order_data"]
        if not order_data.get("customer_name") or order_data["customer_name"] == "Unknown":
            order_data["customer_name"] = customer_name
        success = await create_order_from_ai(seller_id, customer_id, order_data)
        if success:
            print(f"ORDER SAVED: {order_data.get('product_name')} for {order_data.get('customer_name')}")
        else:
            print(f"ORDER SAVE FAILED for {order_data.get('product_name')}")

    # Flag for seller attention if needed — also pause AI for this customer
    if result.get("needs_seller"):
        print(f"SELLER ATTENTION NEEDED: customer={customer_name}, msg={comment_text[:100]}")
        try:
            supabase.table("messages").insert({
                "seller_id": seller_id,
                "customer_id": customer_id,
                "direction": "system",
                "content": f"⚠️ দোকান মালিকের মনোযোগ দরকার: {comment_text[:200]}"
            }).execute()
            # Pause AI for this customer so seller can handle it
            await pause_ai_for_customer(customer_id)
        except Exception as e:
            print(f"Error saving seller flag: {e}")

    await reply_func(reply_target, reply_text)
    await save_messages_to_db(seller_id, customer_id, comment_text, reply_text)


async def is_seller_active(customer_id: str) -> bool:
    """Check if AI is paused for this customer."""
    try:
        # Look for the LATEST ai_paused or ai_resumed message for this customer
        result = supabase.table("messages").select("direction").eq(
            "customer_id", customer_id
        ).in_("direction", ["ai_paused", "ai_resumed"]).order(
            "created_at", desc=True
        ).limit(1).execute()

        if not result.data:
            return False

        # If the most recent marker is "ai_paused", AI is paused
        # If it's "ai_resumed", AI is active
        return result.data[0]["direction"] == "ai_paused"
    except Exception as e:
        print(f"Error checking seller active: {e}")
        return False


async def pause_ai_for_customer(customer_id: str):
    """Mark AI as paused for this customer."""
    try:
        cust = supabase.table("customers").select("seller_id").eq(
            "id", customer_id
        ).single().execute()
        if cust.data:
            supabase.table("messages").insert({
                "seller_id": cust.data["seller_id"],
                "customer_id": customer_id,
                "direction": "ai_paused",
                "content": "AI paused — waiting for seller"
            }).execute()
            print(f"AI PAUSED for customer {customer_id}")
    except Exception as e:
        print(f"Error pausing AI: {e}")


# ═══════════════════════════════════════════
# WEBHOOK ENDPOINTS
# ═══════════════════════════════════════════

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
                        customer = await get_or_create_customer(seller_id, sender_id)
                        await handle_message(
                            seller_id, customer, comment_text,
                            post_comment_reply, comment_id
                        )

        for messaging in entry.get("messaging", []):
            message = messaging.get("message", {})
            sender_id = messaging.get("sender", {}).get("id", "")
            attachments = message.get("attachments", [])
            has_image = any(a.get("type") == "image" for a in attachments)
            comment_text = message.get("text", "")
            if has_image and not comment_text:
                comment_text = "[কাস্টমার একটি ছবি পাঠিয়েছে]"
            if sender_id != PAGE_ID and comment_text:
                seller_id = await get_or_create_seller()
                if not seller_id:
                    continue
                customer = await get_or_create_customer(seller_id, sender_id)
                await handle_message(
                    seller_id, customer, comment_text,
                    send_messenger_reply, sender_id
                )

    return {"status": "ok"}


# ═══════════════════════════════════════════
# ORDER STATUS UPDATE ENDPOINT (called by dashboard)
# ═══════════════════════════════════════════

@app.post("/api/orders/{order_id}/status")
async def update_order_status(order_id: str, request: Request):
    """
    Dashboard calls this when seller changes order status.
    Automatically notifies the customer via Messenger.
    """
    try:
        body = await request.json()
        new_status = body.get("status")

        if not new_status:
            return {"error": "status is required"}

        valid_statuses = ["new", "confirmed", "paid", "shipped", "delivered", "cancelled"]
        if new_status not in valid_statuses:
            return {"error": f"Invalid status. Must be one of: {valid_statuses}"}

        # Get the order
        order_res = supabase.table("orders").select("*").eq("id", order_id).single().execute()
        if not order_res.data:
            return {"error": "Order not found"}

        order = order_res.data
        old_status = order.get("status")

        # Update the order status
        supabase.table("orders").update({
            "status": new_status
        }).eq("id", order_id).execute()

        # Notify customer via Messenger
        customer_id = order.get("customer_id")
        if customer_id:
            # Get customer's facebook_user_id
            cust_res = supabase.table("customers").select("facebook_user_id").eq(
                "id", customer_id
            ).single().execute()
            if cust_res.data and cust_res.data.get("facebook_user_id"):
                fb_id = cust_res.data["facebook_user_id"]
                await notify_customer_status_change(
                    fb_id, order, new_status,
                    seller_id=order.get("seller_id")
                )

        return {
            "success": True,
            "order_id": order_id,
            "old_status": old_status,
            "new_status": new_status,
            "customer_notified": True
        }
    except Exception as e:
        print(f"Error updating order status: {e}")
        return {"error": str(e)}


@app.get("/health")
async def health_check():
    return {"status": "DamKoto backend is running"}


# ═══════════════════════════════════════════
# SELLER REPLY ENDPOINT (called by dashboard)
# ═══════════════════════════════════════════

@app.post("/api/messages/reply")
async def seller_reply(request: Request):
    """
    Seller sends a manual reply to a customer from the dashboard.
    This pauses AI for 10 minutes and sends via Messenger.
    """
    try:
        body = await request.json()
        customer_id = body.get("customer_id")
        message_text = body.get("message")
        seller_id = body.get("seller_id")

        if not customer_id or not message_text:
            return {"error": "customer_id and message are required"}

        # Get customer's facebook_user_id
        cust_res = supabase.table("customers").select("facebook_user_id").eq(
            "id", customer_id
        ).single().execute()

        if not cust_res.data or not cust_res.data.get("facebook_user_id"):
            return {"error": "Customer not found or no Facebook ID"}

        fb_id = cust_res.data["facebook_user_id"]

        # Send via Messenger
        await send_messenger_reply(fb_id, message_text)

        # Save as seller_reply (this also pauses AI for 10 min)
        supabase.table("messages").insert({
            "seller_id": seller_id,
            "customer_id": customer_id,
            "direction": "seller_reply",
            "content": message_text
        }).execute()

        print(f"Seller reply sent to customer {customer_id}: {message_text[:100]}")
        return {"success": True, "customer_notified": True}

    except Exception as e:
        print(f"Error sending seller reply: {e}")
        return {"error": str(e)}


@app.post("/api/messages/pause-ai")
async def pause_ai_endpoint(request: Request):
    """Seller manually pauses AI for a customer."""
    try:
        body = await request.json()
        customer_id = body.get("customer_id")
        if not customer_id:
            return {"error": "customer_id is required"}
        await pause_ai_for_customer(customer_id)
        return {"success": True}
    except Exception as e:
        print(f"Error pausing AI: {e}")
        return {"error": str(e)}


@app.post("/api/messages/resume-ai")
async def resume_ai(request: Request):
    """Seller signals they're done — resume AI for this customer."""
    try:
        body = await request.json()
        customer_id = body.get("customer_id")

        if not customer_id:
            return {"error": "customer_id is required"}

        cust = supabase.table("customers").select("seller_id").eq(
            "id", customer_id
        ).single().execute()

        if cust.data:
            # Insert ai_resumed marker — is_seller_active checks the LATEST
            # marker, so this overrides any previous ai_paused
            supabase.table("messages").insert({
                "seller_id": cust.data["seller_id"],
                "customer_id": customer_id,
                "direction": "ai_resumed",
                "content": "✅ AI আবার চালু হয়েছে"
            }).execute()

        print(f"AI RESUMED for customer {customer_id}")
        return {"success": True}

    except Exception as e:
        print(f"Error resuming AI: {e}")
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
