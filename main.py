import os
import json
import base64
import requests
import feedparser
import trafilatura
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
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_to_history(article_url):
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{article_url}\n")

def resolve_google_url(google_url):
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        res = session.get(google_url, allow_redirects=True, timeout=8)
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

def rewrite_with_gemini(raw_text, original_title):
    client = genai.Client(api_key=GEMINI_API_KEY.strip())
    
    prompt = f"""
    You are an expert news editor. Rewrite the following raw news article/summary into a clear, professional, engaging news article.
    
    Formatting rules:
    - Return ONLY a raw JSON object. Do not include markdown tags like ```json.
    - Fields required:
      "title": "A compelling headline"
      "content": "<p>Introductory paragraph...</p><h2>Key Highlights</h2><ul><li>Point 1</li><li>Point 2</li></ul><p>Detailed body content...</p>"

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

def post_to_wordpress(title, content_html):
    api_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts"
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
    
    res = requests.post(api_endpoint, headers=headers, json=body, timeout=10)
    if res.status_code in [200, 201]:
        print(f"Successfully published: {title}")
        return True
    else:
        print(f"Failed to publish. Status: {res.status_code}, Response: {res.text}")
        return False

def run_pipeline():
    history = load_history()
    feed = feedparser.parse(RSS_FEED_URL)
    
    print(f"Found {len(feed.entries)} items in feed.")
    
    for entry in feed.entries:
        raw_url = entry.link
        if raw_url in history:
            continue
            
        print(f"\nProcessing: {entry.title}")
        target_url = resolve_google_url(raw_url)
        
        # Try full page extraction
        raw_text = extract_article_content(target_url)
        
        # Fallback to RSS summary or title if scraping fails or returns short text
        if not raw_text or len(raw_text) < 100:
            summary = getattr(entry, 'summary', '')
            raw_text = f"{entry.title}. {summary}"
            print("Using RSS summary fallback.")

        try:
            article_data = rewrite_with_gemini(raw_text, entry.title)
        except Exception as e:
            print(f"Gemini processing error: {e}")
            continue
            
        success = post_to_wordpress(article_data["title"], article_data["content"])
        if success:
            save_to_history(raw_url)
            print("Successfully published article!")
            break 

if __name__ == "__main__":
    run_pipeline()
