import os
import re
import json
import base64
import requests
import feedparser

WP_URL = os.getenv("WP_URL")
WP_USER = os.getenv("WP_USER")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD")

YOUTUBE_RSS_URL = "https://rss.app/feeds/E2WvJe9Ayyma7Zzn.xml"

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
    """Fetches recent posts from WP and checks title, content, excerpt, and slug for the video ID."""
    if not WP_URL or not WP_USER or not WP_APP_PASSWORD:
        print("ERROR: Missing WordPress credentials. Aborting to prevent duplicate.")
        return True # Fail-safe: do not post if credentials missing
    
    api_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts"
    credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
    token = base64.b64encode(credentials.encode()).decode("utf-8")
    headers = {
        "Authorization": f"Basic {token}",
        "User-Agent": "Mozilla/5.0"
    }
    
    # Query last 30 posts across all statuses in Cat 32
    params = {
        "categories": 32,
        "per_page": 30,
        "status": "publish,draft,future,private"
    }
    
    try:
        res = requests.get(api_endpoint, headers=headers, params=params, timeout=12)
        if res.status_code == 200:
            posts = res.json()
            print(f"Checking video ID '{video_id}' against {len(posts)} recent WP posts...")
            for post in posts:
                title = post.get("title", {}).get("rendered", "")
                content = post.get("content", {}).get("rendered", "")
                excerpt = post.get("excerpt", {}).get("rendered", "")
                
                # Direct string lookup for 11-char YouTube ID
                if video_id in content or video_id in excerpt or video_id in title:
                    print(f"MATCH FOUND: Video '{video_id}' already exists in WP Post ID {post.get('id')}.")
                    return True
            print(f"NO MATCH: Video '{video_id}' is clear to publish.")
            return False
        else:
            print(f"WP API Error {res.status_code}: {res.text}")
            # Fail safe: assume it exists so we don't post duplicates when WP API fails
            return True
    except Exception as e:
        print(f"Exception while checking WP history: {e}")
        return True # Fail safe

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
            filename = f"yt_cover_{video_id}.jpg"
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
                return media_id
    except Exception as e:
        print(f"Thumbnail upload exception: {e}")
    return None

def post_to_wordpress(title, video_url):
    api_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts"
    credentials = f"{WP_USER}:{WP_APP_PASSWORD}"
    token = base64.b64encode(credentials.encode()).decode("utf-8")
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"
    }
    
    video_id = extract_youtube_id(video_url)
    clean_yt_link = f"https://www.youtube.com/watch?v={video_id}"

    clean_slug = re.sub(r'[^a-zA-Z0-9\s-]', '', title).strip().lower()
    clean_slug = re.sub(r'[\s-]+', '-', clean_slug)[:60]

    thumb_url = get_youtube_thumbnail_url(video_id)
    featured_media_id = upload_thumbnail_to_wordpress(thumb_url, video_id)

    body = {
        "title": title,
        "slug": clean_slug,
        "content": f'<p><iframe width="100%" height="400" src="https://www.youtube.com/embed/{video_id}" frameborder="0" allowfullscreen></iframe></p>',
        "excerpt": clean_yt_link,
        "status": "publish",
        "categories": [32]
    }

    if featured_media_id:
        body["featured_media"] = featured_media_id

    res = requests.post(api_endpoint, headers=headers, json=body, timeout=10)
    return res.status_code in [200, 201]

def run_video_pipeline():
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        response = requests.get(YOUTUBE_RSS_URL, headers=headers, timeout=12)
        if response.status_code == 200:
            feed = feedparser.parse(response.text)
        else:
            print(f"Error fetching RSS feed: {response.status_code}")
            return
    except Exception as e:
        print(f"Exception fetching feed: {e}")
        return

    for entry in feed.entries:
        video_url = entry.link
        entry_title = entry.title
        summary = getattr(entry, 'summary', '')
        guid = getattr(entry, 'id', video_url)

        video_id = extract_youtube_id(video_url) or extract_youtube_id(guid) or extract_youtube_id(summary)
        
        if not video_id:
            continue

        # STRICT DUPLICATE CHECK WITH FAIL-SAFE
        if is_already_published_in_wp(video_id):
            print(f"SKIPPING: '{entry_title}' ({video_id}) is already published or check failed.")
            continue

        print(f"POSTING NEW VIDEO: {entry_title} ({video_id})")
        
        if post_to_wordpress(entry_title, video_url):
            print(f"Successfully published: {entry_title}")
            break

if __name__ == "__main__":
    run_video_pipeline()
