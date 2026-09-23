import os
import re
import json
import base64
import requests
import feedparser

WP_URL = os.getenv("WP_URL")
WP_USER = os.getenv("WP_USER")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD")

# Hero Slider (Scenic Feature Videos) RSS Feed
YOUTUBE_RSS_URL = "https://rss.app/feeds/nMj6We403j7SPWZp.xml"
HERO_CATEGORY_ID = 30  # Category 30 for Hero Slider

def extract_youtube_id(url_or_guid_or_text):
    patterns = [
        r"(?:v=|\/vi\/|\/videos\/|\/embed\/|\/shorts\/|youtu\.be\/|\/v\/|yt:video:)([a-zA-Z0-9_-]{11})"
    ]
    for pattern in patterns:
        match = re.search(pattern, str(url_or_guid_or_text))
        if match:
            return match.group(1)
    return None

def is_already_published_in_wp(video_id):
    """Fetches Category 30 posts and checks content/excerpt/title for video_id."""
    if not WP_URL or not WP_USER or not WP_APP_PASSWORD:
        print("CRITICAL: Missing WordPress credentials in environment variables!")
        return True  # BLOCK POSTING

    api_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts"
    credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
    token = base64.b64encode(credentials.encode()).decode("utf-8")
    headers = {
        "Authorization": f"Basic {token}",
        "User-Agent": "Mozilla/5.0"
    }
    params = {
        "categories": HERO_CATEGORY_ID,
        "per_page": 30,
        "status": "publish,draft,future,private"
    }
    
    try:
        res = requests.get(api_endpoint, headers=headers, params=params, timeout=12)
        if res.status_code == 200:
            posts = res.json()
            for post in posts:
                post_id = post.get("id")
                title = post.get("title", {}).get("rendered", "")
                content = post.get("content", {}).get("rendered", "")
                excerpt = post.get("excerpt", {}).get("rendered", "")
                
                if video_id in content or video_id in excerpt or video_id in title:
                    print(f"==> HERO SLIDER MATCH FOUND! Video ID '{video_id}' exists in WP Post ID {post_id} ('{title}').")
                    return True  # BLOCK POSTING
            
            print(f"==> NO MATCH FOUND for Hero Video ID '{video_id}'. Safe to publish.")
            return False  # SAFE TO POST
        else:
            print(f"CRITICAL ERROR: WP API returned status {res.status_code}.")
            return True  # BLOCK POSTING
            
    except Exception as e:
        print(f"CRITICAL EXCEPTION connecting to WP: {e}")
        return True  # BLOCK POSTING

def get_youtube_thumbnail_url(video_id):
    maxres_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
    try:
        res = requests.head(maxres_url, timeout=5)
        if res.status_code == 200:
            return maxres_url
    except Exception:
        pass
    return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"

def upload_thumbnail_to_wordpress(image_url, video_id):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        img_res = requests.get(image_url, headers=headers, timeout=10)
        if img_res.status_code == 200:
            filename = f"yt_hero_{video_id}.jpg"
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
                print(f"=== Hero Featured Image Uploaded: Media ID {media_id} ===")
                return media_id
            else:
                print(f"Media Upload Error {upload_res.status_code}: {upload_res.text[:150]}")
    except Exception as e:
        print(f"Thumbnail upload exception: {e}")
    return None

def post_to_wordpress(title, video_id):
    api_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts"
    credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
    token = base64.b64encode(credentials.encode()).decode("utf-8")
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"
    }
    
    clean_yt_link = f"https://www.youtube.com/watch?v={video_id}"

    clean_slug = re.sub(r'[^a-zA-Z0-9\s-]', '', title).strip().lower()
    clean_slug = re.sub(r'[\s-]+', '-', clean_slug)[:60]

    thumb_url = get_youtube_thumbnail_url(video_id)
    featured_media_id = upload_thumbnail_to_wordpress(thumb_url, video_id)

    body = {
        "title": title,
        "slug": f"hero-{clean_slug}-{video_id.lower()}",
        "content": f'<!-- YT:{video_id} --><p><iframe width="100%" height="400" src="https://www.youtube.com/embed/{video_id}" frameborder="0" allowfullscreen></iframe></p>',
        "excerpt": clean_yt_link,
        "status": "publish",
        "categories": [HERO_CATEGORY_ID]
    }

    if featured_media_id:
        body["featured_media"] = featured_media_id

    res = requests.post(api_endpoint, headers=headers, json=body, timeout=10)
    return res.status_code in [200, 201]

def run_hero_pipeline():
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        response = requests.get(YOUTUBE_RSS_URL, headers=headers, timeout=12)
        if response.status_code == 200:
            feed = feedparser.parse(response.text)
        else:
            print(f"Error fetching Hero RSS feed: {response.status_code}")
            return
    except Exception as e:
        print(f"Exception fetching feed: {e}")
        return

    for entry in feed.entries:
        video_url = getattr(entry, 'link', '')
        entry_title = getattr(entry, 'title', '')
        summary = getattr(entry, 'summary', '')
        guid = getattr(entry, 'id', '')

        video_id = extract_youtube_id(video_url) or extract_youtube_id(guid) or extract_youtube_id(summary)
        
        if not video_id:
            continue

        if is_already_published_in_wp(video_id):
            print(f"SKIPPING HERO VIDEO: '{entry_title}' ({video_id}) is already published in Category 30.")
            continue

        print(f"POSTING NEW HERO VIDEO: {entry_title} ({video_id})")
        
        if post_to_wordpress(entry_title, video_id):
            print(f"Successfully published to Hero Slider: {entry_title}")
            break

if __name__ == "__main__":
    run_hero_pipeline()
