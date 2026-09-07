import os
import json
import base64
import requests
import feedparser
import trafilatura
from difflib import SequenceMatcher
from google import genai

# Configuration from GitHub Secrets
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WP_URL = os.getenv("WP_URL")
WP_USER = os.getenv("WP_USER")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

RSS_FEED_URL = "https://news.google.com/rss/search?q=Sri+Lanka+news&hl=en-US&gl=US&ceid=US:en"
HISTORY_FILE = "published_history.txt"

def load_history():
    """Loads previously processed URLs and titles."""
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_to_history(entry_id):
    """Saves URL or title identifier to local history log."""
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"{entry_id}\n")

def fetch_recent_wordpress_titles():
    """Fetches the last 30 published post titles directly from WordPress to prevent duplicate topics."""
    api_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts?per_page=30&_fields=title"
    credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
    token = base64.b64encode(credentials.encode()).decode("utf-8")
    headers = {
        "Authorization": f"Basic {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    try:
        res = requests.get(api_endpoint, headers=headers, timeout=10)
        if res.status_code == 200:
            posts = res.json()
            titles = [p['title']['rendered'] for p in posts]
            return titles
    except Exception as e:
        print(f"Failed to fetch recent WordPress titles: {e}")
    return []

def is_title_similar(new_title, recent_titles, threshold=0.65):
    """Fuzzy matching check against existing titles."""
    for existing in recent_titles:
        similarity = SequenceMatcher(None, new_title.lower(), existing.lower()).ratio()
        if similarity >= threshold:
            print(f"Duplicate detected via fuzzy match ({similarity:.2f}): '{new_title}' matches '{existing}'")
            return True
    return False

def is_semantic_duplicate(new_title, new_summary, recent_titles):
    """Asks Gemini Flash if the new story is a duplicate coverage of an already published event."""
    if not recent_titles:
        return False
        
    client = genai.Client(api_key=GEMINI_API_KEY.strip())
    
    recent_list_str = "\n".join([f"- {t}" for t in recent_titles[:20]])
    prompt = f"""
    You are a strict news editor screening incoming stories for duplicate coverage.
    
    Recently Published Headlines:
    {recent_list_str}
    
    Incoming Story Title: "{new_title}"
    Incoming Story Summary: "{new_summary[:300]}"
    
    Question: Is this incoming story reporting on the exact same underlying event, incident, or news story as any of the recently published headlines?
    
    Return ONLY a raw JSON object:
    {{
      "is_duplicate": true or false,
      "reason": "Brief explanation"
    }}
    """
    try:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
        )
        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:-3].strip()
        elif text.startswith("```"):
            text = text[3:-3].strip()
            
        data = json.loads(text)
        if data.get("is_duplicate"):
            print(f"Gemini Semantic Duplicate Check: REJECTED ('{new_title}') -> Reason: {data.get('reason')}")
            return True
    except Exception as e:
        print(f"Semantic check error: {e}")
    return False

def resolve_google_url(google_url):
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        res = session.get(google_url, allow_redirects=True, timeout=5)
        return res.url
    except Exception as e:
        print(f"URL resolve fallback used: {e}")
        return google_url

def extract_article_content(url):
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            return trafilatura.extract(downloaded)
    except Exception as e:
        print(f"Extraction error: {e}")
    return None

def get_pexels_image_url(search_query):
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
    try:
        img_res = requests.get(image_url, timeout=10)
        if img_res.status_code == 200:
            filename = f"pexels_{image_url.split('/')[-1].split('?')[0]}.jpg"
            
            credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
            token = base64.b64encode(credentials.encode()).decode("utf-8")
            
            media_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/media"
            media_headers = {
                "Authorization": f"Basic {token}",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": img_res.headers.get("Content-Type", "image/jpeg")
            }
            
            upload_res = requests.post(media_endpoint, headers=media_headers, data=img_res.content, timeout=15)
            if upload_res.status_code in [200, 201]:
                media_id = upload_res.json().get("id")
                print(f"Uploaded photo to WP Media Library. Media ID: {media_id}")
                return media_id
            else:
                print(f"Failed to upload image. Status: {upload_res.status_code}, Response: {upload_res.text}")
    except Exception as e:
        print(f"Image upload exception: {e}")
    return None

def rewrite_with_gemini(raw_text, original_title):
    client = genai.Client(api_key=GEMINI_API_KEY.strip())
    
    prompt = f"""
    You are an expert news editor. Rewrite the following raw news article/summary into a clear, professional, engaging news article.
    
    Formatting rules:
    - Return ONLY a raw JSON object. Do not include markdown tags like ```json.
    - Fields required:
      "title": "A compelling headline"
      "content": "<p>Introductory paragraph...</p><h2>Key Highlights</h2><ul><li>Point 1</li><li>Point 2</li></ul><p>Detailed body content...</p>"
      "image_query": "2 to 3 concise English keywords for stock photo search (e.g., 'tea estate', 'passenger plane', 'cricket match')"

    Original Title: {original_title}
    Raw Text:
    {raw_text[:3500]}
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

def post_to_wordpress(title, content_html, featured_media_id=None):
    api_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts"
    credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
    token = base64.b64encode(credentials.encode()).decode("utf-8")
    
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    body = {
        "title": title,
        "content": content_html,
        "status": "publish"
    }
    
    if featured_media_id:
        body["featured_media"] = featured_media_id
    
    res = requests.post(api_endpoint, headers=headers, json=body, timeout=10)
    if res.status_code in [200, 201]:
        print(f"Successfully published: {title}")
        return True
    else:
        print(f"Failed to publish. Status: {res.status_code}, Response: {res.text}")
        return False

def run_pipeline():
    history = load_history()
    recent_wp_titles = fetch_recent_wordpress_titles()
    feed = feedparser.parse(RSS_FEED_URL)
    
    print(f"Found {len(feed.entries)} items in feed. Fetched {len(recent_wp_titles)} recent titles from WordPress.")
    
    processed_count = 0
    for entry in feed.entries:
        if processed_count >= 3:
            print("Batch limit of 3 reached. Ending run.")
            break

        raw_url = entry.link
        entry_title = entry.title
        summary = getattr(entry, 'summary', '')

        # 1. URL History Check
        if raw_url in history:
            continue
            
        # 2. Fuzzy Title Match Check
        if is_title_similar(entry_title, recent_wp_titles):
            save_to_history(raw_url)
            continue

        # 3. Gemini Semantic Duplicate Gatekeeper
        if is_semantic_duplicate(entry_title, summary, recent_wp_titles):
            save_to_history(raw_url)
            continue
            
        print(f"\nProcessing unique story: {entry_title}")
        target_url = resolve_google_url(raw_url)
        
        raw_text = extract_article_content(target_url)
        if not raw_text or len(raw_text) < 100:
            raw_text = f"{entry_title}. {summary}"
            print("Using RSS summary fallback.")

        try:
            article_data = rewrite_with_gemini(raw_text, entry_title)
        except Exception as e:
            print(f"Gemini processing error: {e}")
            continue
            
        # Pexels photo lookup & WordPress media upload
        image_query = article_data.get("image_query", "sri lanka news")
        image_url = get_pexels_image_url(image_query)
        
        media_id = None
        if image_url:
            media_id = upload_image_to_wordpress(image_url)
            
        success = post_to_wordpress(article_data["title"], article_data["content"], featured_media_id=media_id)
        if success:
            save_to_history(raw_url)
            recent_wp_titles.insert(0, article_data["title"])  # Update local memory
            print("Successfully published unique article with Pexels photo!")
            break 
            
        processed_count += 1

if __name__ == "__main__":
    run_pipeline()
