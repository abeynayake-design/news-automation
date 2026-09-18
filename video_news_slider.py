import os
import re
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

# Your RSS.app Feed URL
YOUTUBE_RSS_URL = "https://rss.app/feeds/E2WvJe9Ayyma7Zzn.xml"

def extract_youtube_id(url_or_guid):
    """Extracts 11-character YouTube video ID from links or descriptions."""
    patterns = [
        r"(?:v=|\/vi\/|\/videos\/|\/embed\/|\/shorts\/|youtu\.be\/|\/v\/|yt:video:)([a-zA-Z0-9_-]{11})"
    ]
    for pattern in patterns:
        match = re.search(pattern, url_or_guid)
        if match:
            return match.group(1)
    return None

def get_youtube_thumbnail_url(video_id):
    """Guarantees official YouTube thumbnail directly from YouTube CDN."""
    maxres_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
    try:
        res = requests.head(maxres_url, timeout=5)
        if res.status_code == 200:
            return maxres_url
    except Exception:
        pass
    # Fallback to standard high-definition YouTube thumbnail
    return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"

def upload_image_to_wordpress(image_url, title):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        img_res = requests.get(image_url, headers=headers, timeout=10)
        if img_res.status_code == 200:
            clean_title = re.sub(r'[^a-zA-Z0-9]', '_', title)[:20]
            filename = f"yt_thumb_{clean_title}.jpg"
            credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
            token = base64.b64encode(credentials.encode()).decode("utf-8")
            media_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/media"
            media_headers = {
                "Authorization": f"Basic {token}",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": img_res.headers.get("Content-Type", "image/jpeg")
            }
            upload_res = requests.post(media_endpoint, headers=media_headers, data=img_res.content, timeout=15)
            if upload_res.status_code in [200, 201]:
                media_id = upload_res.json().get("id")
                print(f"=== STEP 2 DEBUG: Uploaded thumbnail to WP Media. ID: {media_id} ===")
                return media_id
    except Exception as e:
        print(f"Thumbnail upload exception: {e}")
    return None

def rewrite_with_gemini(raw_title, raw_summary):
    client = genai.Client(api_key=GEMINI_API_KEY.strip())
    prompt = f"""
    You are a video content editor. Rewrite the following video headline and description into an engaging web summary for a site overlay slider.
    
    Formatting rules:
    - Return ONLY a raw JSON object. Do not include markdown tags like ```json.
    - Fields required:
      "title": "A captivating, clean headline"
      "content": "<p>A concise, compelling overview of the video content...</p>"
      "excerpt": "A short 1-2 sentence plain text summary for slider overlays."

    Original Video Title: {raw_title}
    Video Summary: {raw_summary[:1500]}
    """
    response = client.models.generate_content(model="gemini-3.6-flash", contents=prompt)
    response_text = response.text.strip()
    if response_text.startswith("```json"):
        response_text = response_text[7:-3].strip()
    elif response_text.startswith("```"):
        response_text = response_text[3:-3].strip()
    return json.loads(response_text)

def post_to_wordpress(title, content_html, excerpt_text, featured_media_id, video_url):
    api_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts"
    credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
    token = base64.b64encode(credentials.encode()).decode("utf-8")
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    
    video_id = extract_youtube_id(video_url)
    full_content = f'<p><iframe width="100%" height="400" src="[https://www.youtube.com/embed/](https://www.youtube.com/embed/){video_id}" frameborder="0" allowfullscreen></iframe></p>{content_html}'
    
    clean_slug = re.sub(r'[^a-zA-Z0-9\s-]', '', title).strip().lower()
    clean_slug = re.sub(r'[\s-]+', '-', clean_slug)[:60]

    body = {
        "title": title,
        "slug": clean_slug,
        "content": full_content,
        "excerpt": excerpt_text,
        "status": "publish",
        "categories": [32]  # News Videos Category ID
    }
    if featured_media_id:
        body["featured_media"] = featured_media_id

    res = requests.post(api_endpoint, headers=headers, json=body, timeout=10)
    return res.status_code in [200, 201]

def run_video_pipeline():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        response = requests.get(YOUTUBE_RSS_URL, headers=headers, timeout=12)
        if response.status_code == 200:
            feed = feedparser.parse(response.text)
        else:
            print(f"=== STEP 1 ERROR: Failed to fetch feed. Status Code: {response.status_code} ===")
            return
    except Exception as e:
        print(f"=== STEP 1 EXCEPTION: {e} ===")
        return

    print(f"=== STEP 1 DEBUG: Found {len(feed.entries)} items in video feed ===")

    for entry in feed.entries:
        video_url = entry.link
        entry_title = entry.title
        summary = getattr(entry, 'summary', '')
        guid = getattr(entry, 'id', video_url)

        # Force YouTube ID extraction from link, GUID, or description
        video_id = extract_youtube_id(video_url) or extract_youtube_id(guid) or extract_youtube_id(summary)
        if not video_id:
            continue

        print(f"\nProcessing Video: {entry_title} (ID: {video_id})")
        
        # ALWAYS pull direct YouTube thumbnail image
        thumbnail_url = get_youtube_thumbnail_url(video_id)

        try:
            article_data = rewrite_with_gemini(entry_title, summary)
        except Exception as e:
            print(f"Gemini processing error: {e}")
            continue

        media_id = upload_image_to_wordpress(thumbnail_url, article_data["title"])
        excerpt_val = article_data.get("excerpt", summary[:150])
        
        if post_to_wordpress(article_data["title"], article_data["content"], excerpt_val, media_id, video_url):
            print(f"SUCCESS: Published video post '{article_data['title']}' with clean YouTube thumbnail!")
            break

if __name__ == "__main__":
    run_video_pipeline()
