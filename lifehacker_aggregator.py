import os
import json
import base64
import requests
import feedparser
from google import genai

# Configuration from GitHub Secrets
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WP_URL = os.getenv("WP_URL")
WP_USER = os.getenv("WP_USER")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

RSS_FEED_URL = "https://lifehacker.com/feed/rss"
HISTORY_FILE = "lifehacker_history.txt"
WP_CATEGORY_ID = 42  # Category 42 for Lifehacker Aggregation

def load_history():
    """Loads previously processed Lifehacker URLs."""
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_to_history(entry_url):
    """Saves published URL to local history log."""
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"{entry_url}\n")

def get_pexels_image_url(search_query):
    """Searches Pexels for a relevant landscape stock photo."""
    if not PEXELS_API_KEY:
        print("Missing PEXELS_API_KEY secret. Skipping Pexels image lookup.")
        return None

    try:
        headers = {"Authorization": PEXELS_API_KEY}
        url = f"https://api.pexels.com/v1/search?query={search_query}&per_page=1&orientation=landscape"
        res = requests.get(url, headers=headers, timeout=10)
        
        if res.status_code == 200:
            data = res.json()
            photos = data.get("photos", [])
            if photos:
                image_url = photos[0]["src"]["large"]
                print(f"Pexels image found for '{search_query}': {image_url}")
                return image_url
            else:
                print(f"No Pexels photos found for query: '{search_query}'")
        else:
            print(f"Pexels API error. Status: {res.status_code}, Response: {res.text}")
    except Exception as e:
        print(f"Pexels fetch error: {e}")
    return None

def upload_image_to_wordpress(image_url):
    """Uploads the Pexels image directly to WordPress Media Library."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        img_res = requests.get(image_url, headers=headers, timeout=10)
        if img_res.status_code == 200:
            filename = f"lh_pexels_{image_url.split('/')[-1].split('?')[0]}.jpg"
            
            credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
            token = base64.b64encode(credentials.encode()).decode("utf-8")
            
            media_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/media"
            media_headers = {
                "Authorization": f"Basic {token}",
                "User-Agent": "Mozilla/5.0",
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": img_res.headers.get("Content-Type", "image/jpeg")
            }
            
            upload_res = requests.post(media_endpoint, headers=media_headers, data=img_res.content, timeout=15)
            if upload_res.status_code in [200, 201]:
                media_id = upload_res.json().get("id")
                print(f"Uploaded photo to WP Media Library. Media ID: {media_id}")
                return media_id
            else:
                print(f"Failed to upload image. Status: {upload_res.status_code}, Response: {upload_res.text[:150]}")
    except Exception as e:
        print(f"Image upload exception: {e}")
    return None

def generate_teaser_and_image_prompt(original_title, original_summary):
    """Uses Gemini to generate an engaging short teaser and Pexels search keywords."""
    client = genai.Client(api_key=GEMINI_API_KEY.strip())
    
    prompt = f"""
    You are an editorial assistant aggregating content for BrunchPress.
    
    Article Title: {original_title}
    Article Summary: {original_summary[:1500]}
    
    Task:
    1. Write a short, captivating 2-sentence teaser summary to introduce this Lifehacker article.
    2. Provide 2 to 3 English search keywords for finding a high-quality stock photo on Pexels (e.g. "laptop productivity", "cooking tips", "smart phone").

    Return ONLY a raw JSON object without markdown tags:
    {{
      "teaser": "Engaging two-sentence intro...",
      "image_query": "2 to 3 search keywords"
    }}
    """
    
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt,
    )
    
    response_text = response.text.strip()
    if response_text.startswith("```json"):
        response_text = response_text[7:-3].strip()
    elif response_text.startswith("```"):
        response_text = response_text[3:-3].strip()
        
    return json.loads(response_text)

def post_to_wordpress(title, original_url, teaser_text, featured_media_id=None):
    """Posts aggregated story to Category 42 with prefixed headline and direct link button."""
    api_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts"
    credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
    token = base64.b64encode(credentials.encode()).decode("utf-8")
    
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"
    }
    
    # Enforces the mandatory title prefix
    prefixed_headline = f"From LifeHacker site: {title}"

    # Formats article body with teaser and direct link button to Lifehacker
    content_html = f"""
    <p>{teaser_text}</p>
    <p style="margin-top: 20px;">
        <a href="{original_url}" target="_blank" rel="noopener noreferrer" style="background-color: #111; color: #fff; padding: 10px 18px; text-decoration: none; border-radius: 4px; display: inline-block; font-weight: bold;">
            Read Full Story on Lifehacker &rarr;
        </a>
    </p>
    """
    
    body = {
        "title": prefixed_headline,
        "content": content_html,
        "status": "publish",
        "categories": [WP_CATEGORY_ID]
    }
    
    if featured_media_id:
        body["featured_media"] = featured_media_id
    
    res = requests.post(api_endpoint, headers=headers, json=body, timeout=10)
    if res.status_code in [200, 201]:
        print(f"Successfully published: {prefixed_headline}")
        return True
    else:
        print(f"Failed to publish. Status: {res.status_code}, Response: {res.text[:150]}")
        return False

def run_pipeline():
    history = load_history()
    feed = feedparser.parse(RSS_FEED_URL)
    
    print(f"Found {len(feed.entries)} items in Lifehacker feed.")
    
    processed_count = 0
    for entry in feed.entries:
        if processed_count >= 2:
            print("Batch limit reached. Ending run.")
            break

        original_url = entry.link
        entry_title = entry.title
        summary = getattr(entry, 'summary', '')

        # URL History Check
        if original_url in history:
            continue
            
        print(f"\nProcessing Lifehacker story: {entry_title}")

        try:
            ai_data = generate_teaser_and_image_prompt(entry_title, summary)
            teaser = ai_data.get("teaser", summary[:200])
            image_query = ai_data.get("image_query", "lifehacker technology")
        except Exception as e:
            print(f"Gemini processing error: {e}")
            teaser = summary[:200]
            image_query = "technology productivity"

        # Pexels photo lookup & WordPress media upload
        image_url = get_pexels_image_url(image_query)
        media_id = upload_image_to_wordpress(image_url) if image_url else None
            
        success = post_to_wordpress(entry_title, original_url, teaser, featured_media_id=media_id)
        if success:
            save_to_history(original_url)
            print("Successfully published aggregated story!")
            break 
            
        processed_count += 1

if __name__ == "__main__":
    run_pipeline()
