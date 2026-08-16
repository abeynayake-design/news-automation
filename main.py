import os
import re
import json
import base64
import requests
import feedparser
import trafilatura
from googlenewsdecoder import gnewsdecoder
import google.generativeai as genai

# Configuration from Environment Variables (GitHub Secrets)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WP_URL = os.getenv("WP_URL")  # e.g., https://yourdomain.com
WP_USER = os.getenv("WP_USER")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

# RSS Feed Source (Google News RSS or Direct Publisher RSS)
RSS_FEED_URL = "https://news.google.com/rss/search?q=Sri+Lanka+news&hl=en-US&gl=US&ceid=US:en"

HISTORY_FILE = "published_history.txt"

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_to_history(article_url):
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{article_url}\n")

def resolve_google_url(google_url):
    """Unwraps Google News encrypted redirect URLs to original target site."""
    try:
        decoded_data = gnewsdecoder(google_url)
        if isinstance(decoded_data, dict) and "decoded_url" in decoded_data:
            return decoded_data["decoded_url"]
        elif isinstance(decoded_data, str):
            return decoded_data
    except Exception as e:
        print(f"Decoder fallback triggered: {e}")
    
    # Fallback to direct HTTP head request if decoder misses
    try:
        res = requests.head(google_url, allow_redirects=True, timeout=10)
        return res.url
    except Exception:
        return google_url

def extract_article_content(url):
    """Scrapes raw article text using free open-source trafilatura library."""
    downloaded = trafilatura.fetch_url(url)
    if downloaded:
        text = trafilatura.extract(downloaded)
        return text
    return None

def fetch_pexels_image(keyword):
    """Fetches a free featured image from Pexels API."""
    if not PEXELS_API_KEY:
        return None
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": keyword, "per_page": 1, "orientation": "landscape"}
    try:
        res = requests.get("https://api.pexels.com/v1/search", headers=headers, params=params, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if data.get("photos"):
                return data["photos"][0]["src"]["large"]
    except Exception as e:
        print(f"Pexels fetch error: {e}")
    return None

def rewrite_with_gemini(raw_text, original_title):
    """Rewrites article content into clean publication HTML using Gemini 2.5 Flash API."""
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-2.5-flash")
    
    prompt = f"""
    You are an expert journalist. Rewrite the following raw news article into a comprehensive, engaging news story.
    
    Requirements:
    1. Create a catchy, factual headline.
    2. Format the body strictly in HTML using <h2> headings, <p> paragraphs, and <ul>/<li> key takeaway bullet points. Do NOT include <html> or <body> tags.
    3. Return your response as a JSON object with two fields:
       - "title": "The New Headline"
       - "content": "<p>Article HTML content...</p>"

    Original Title: {original_title}
    Raw Article Content:
    {raw_text[:4000]}
    """
    
    response = model.generate_content(prompt)
    response_text = response.text.strip()
    
    # Clean JSON markdown blocks if present
    if response_text.startswith("```json"):
        response_text = response_text[7:-3].strip()
    elif response_text.startswith("```"):
        response_text = response_text[3:-3].strip()
        
    return json.loads(response_text)

def post_to_wordpress(title, content_html, featured_image_url=None):
    """Posts generated article to WordPress via REST API."""
    api_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts"
    
    # Prepare Basic Auth token
    credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
    token = base64.b64encode(credentials.encode()).decode("utf-8")
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json"
    }
    
    body = {
        "title": title,
        "content": content_html,
        "status": "publish"
    }
    
    res = requests.post(api_endpoint, headers=headers, json=body, timeout=15)
    if res.status_code in [200, 201]:
        print(f"Successfully published: {title}")
        return True
    else:
        print(f"Failed to publish to WP. Status: {res.status_code}, Response: {res.text}")
        return False

def run_pipeline():
    history = load_history()
    feed = feedparser.parse(RSS_FEED_URL)
    
    print(f"Found {len(feed.entries)} items in feed.")
    
    for entry in feed.entries:
        raw_url = entry.link
        if raw_url in history:
            continue
            
        print(f"\nProcessing new story: {entry.title}")
        
        # 1. Unwrap URL
        target_url = resolve_google_url(raw_url)
        print(f"Decoded URL: {target_url}")
        
        # 2. Extract Text
        raw_text = extract_article_content(target_url)
        if not raw_text or len(raw_text) < 200:
            print("Skipping: Content extraction yielded insufficient text.")
            save_to_history(raw_url)
            continue
            
        # 3. Rewrite Content
        try:
            article_data = rewrite_with_gemini(raw_text, entry.title)
        except Exception as e:
            print(f"Gemini processing error: {e}")
            continue
            
        # 4. Optional Featured Image
        image_url = fetch_pexels_image(entry.title)
        
        # 5. Post to WP
        success = post_to_wordpress(article_data["title"], article_data["content"], image_url)
        if success:
            save_to_history(raw_url)
            print("Finished processing article. Exiting cycle.")
            break  # Processes one article per execution run

if __name__ == "__main__":
    run_pipeline()
