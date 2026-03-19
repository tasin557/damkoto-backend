import asyncio
import httpx
import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)
TOKEN = os.getenv("META_PAGE_ACCESS_TOKEN")


async def get_facebook_name(user_id: str) -> str:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://graph.facebook.com/v22.0/{user_id}",
                params={"fields": "name", "access_token": TOKEN}
            )
            data = resp.json()
            name = data.get("name")
            if name:
                return name
            print(f"  No name found for {user_id}: {data}")
            return None
    except Exception as e:
        print(f"  Error fetching {user_id}: {e}")
        return None


async def fix_names():
    # Get all customers with unknown or missing names
    customers = supabase.table("customers")\
        .select("id, facebook_user_id, name")\
        .or_("name.eq.Unknown,name.is.null")\
        .execute()

    if not customers.data:
        print("No customers need fixing!")
        return

    print(f"Found {len(customers.data)} customers to fix...")

    for customer in customers.data:
        user_id = customer["facebook_user_id"]
        print(f"Fetching name for {user_id}...")
        name = await get_facebook_name(user_id)
        if name:
            supabase.table("customers").update(
                {"name": name}
            ).eq("id", customer["id"]).execute()
            print(f"  Updated: {name}")
        else:
            print(f"  Skipped — could not fetch name")

    print("Done!")

asyncio.run(fix_names())
