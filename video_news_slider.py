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
HISTORY_FILE = "published_videos.json"

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_history(history_list):
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(history_list[-50:], f)
    except Exception as e:
        print(f"Error saving history: {e}")

def extract_youtube_id(url_or_guid_or_text):
    patterns = [
        r"(?:v=|\/vi\/|\/videos\/|\/embed\/|\/shorts\/|youtu\.be\/|\/v\/|yt:video:)([a-zA-Z0-9_-]{11})"
    ]
    for pattern in patterns:
        match = re.search(pattern, str(url_or_guid_or_text))
        if match:
            return match.group(1)
    return None

def get_youtube_thumbnail_url(video_id):
    """Checks for max-resolution YouTube thumbnail, falls back to high quality."""
    maxres_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
    try:
        res = requests.head(maxres_url, timeout=5)
        if res.status_code == 200:
            return maxres_url
    except Exception:
        pass
    return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"

def upload_thumbnail_to_wordpress(image_url, video_id):
    """Downloads YouTube CDN thumbnail and uploads it to WP Media Library."""
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
                print(f"=== Featured Image Uploaded: Media ID {media_id} ===")
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

    # Fetch YouTube CDN image and upload to WP Media Library
    thumb_url = get_youtube_thumbnail_url(video_id)
    featured_media_id = upload_thumbnail_to_wordpress(thumb_url, video_id)

    body = {
        "title": title,
        "slug": clean_slug,
        "content": f'<p><iframe width="100%" height="400" src="https://www.youtube.com/embed/{video_id}" frameborder="0" allowfullscreen></iframe></p>',
        "excerpt": clean_yt_link,  # Pure YouTube link for Smart Slider 5
        "status": "publish",
        "categories": [32]
    }

    if featured_media_id:
        body["featured_media"] = featured_media_id

    res = requests.post(api_endpoint, headers=headers, json=body, timeout=10)
    return res.status_code in [200, 201]

def run_video_pipeline():
    headers = {"User-Agent": "Mozilla/5.0"}
    history = load_history()
    
    try:
        response = requests.get(YOUTUBE_RSS_URL, headers=headers, timeout=12)
        if response.status_code == 200:
            feed = feedparser.parse(response.text)
        else:
            print(f"Error fetching feed: {response.status_code}")
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
        
        if not video_id or video_id in history:
            continue

        print(f"Posting Video with Featured Image: {entry_title} ({video_id})")
        
        if post_to_wordpress(entry_title, video_url):
            print(f"Successfully published: {entry_title}")
            history.append(video_id)
            save_history(history)
            break

if __name__ == "__main__":
    run_video_pipeline()
