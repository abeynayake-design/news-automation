import os
import json
import base64
import requests
import trafilatura
from difflib import SequenceMatcher
from google import genai
import gspread
from google.oauth2.service_account import Credentials

# Configuration from GitHub Secrets
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WP_URL = os.getenv("WP_URL")
WP_USER = os.getenv("WP_USER")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

HISTORY_FILE = "published_history.txt"

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_to_history(entry_id):
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"{entry_id}\n")

def fetch_urls_from_private_sheet():
    if not GOOGLE_CREDENTIALS:
        print("Missing GOOGLE_CREDENTIALS secret.")
        return []
        
    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly"
    ]
    
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    
    # Put your exact Google Sheet name here
    sheet_name = "News stories for MAKE"
    sheet = client.open(sheet_name).sheet1
    
    urls = []
    rows = sheet.get_all_values()
    for row in rows:
        for cell in row:
            cell_clean = cell.strip()
            if cell_clean.startswith("http://") or cell_clean.startswith("https://"):
                urls.append(cell_clean)
                
    print(f"Fetched {len(urls)} URLs from private Google Sheet.")
    return urls

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
        return None
    try:
        headers = {"Authorization": PEXELS_API_KEY}
        url = f"https://api.pexels.com/v1/search?query={search_query}&per_page=1&orientation=landscape"
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            photos = res.json().get("photos", [])
            if photos:
                return photos[0]["src"]["large"]
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
                "User-Agent": "Mozilla/5.0",
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": img_res.headers.get("Content-Type", "image/jpeg")
            }
            upload_res = requests.post(media_endpoint, headers=media_headers, data=img_res.content, timeout=15)
            if upload_res.status_code in [200, 201]:
                return upload_res.json().get("id")
    except Exception as e:
        print(f"Image upload error: {e}")
    return None

def rewrite_with_gemini(raw_text, target_url):
    client = genai.Client(api_key=GEMINI_API_KEY.strip())
    prompt = f"""
    You are an expert news editor. Rewrite the following article text extracted from URL ({target_url}) into a clear, professional, engaging news report.
    
    Formatting rules:
    - Return ONLY a raw JSON object. Do not include markdown tags like ```json.
    - Fields required:
      "title": "A compelling headline"
      "content": "<p>Introductory paragraph...</p><h2>Key Highlights</h2><ul><li>Point 1</li><li>Point 2</li></ul><p>Detailed body content...</p>"
      "image_query": "2 to 3 concise English keywords for stock photo search"

    Raw Article Text:
    {raw_text[:3500]}
    """
    response = client.models.generate_content(model="gemini-3.6-flash", contents=prompt)
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
        "User-Agent": "Mozilla/5.0"
    }
    body = {"title": title, "content": content_html, "status": "publish"}
    if featured_media_id:
        body["featured_media"] = featured_media_id
    res = requests.post(api_endpoint, headers=headers, json=body, timeout=10)
    return res.status_code in [200, 201]

def run_sheets_pipeline():
    history = load_history()
    urls = fetch_urls_from_private_sheet()
    
    processed_count = 0
    for url in urls:
        if processed_count >= 3:
            break
        if url in history:
            continue

        print(f"\nProcessing URL: {url}")
        raw_text = extract_article_content(url)
        if not raw_text or len(raw_text) < 100:
            save_to_history(url)
            continue

        try:
            article_data = rewrite_with_gemini(raw_text, url)
        except Exception as e:
            print(f"Gemini error: {e}")
            continue

        image_query = article_data.get("image_query", "sri lanka news")
        image_url = get_pexels_image_url(image_query)
        media_id = upload_image_to_wordpress(image_url) if image_url else None

        if post_to_wordpress(article_data["title"], article_data["content"], featured_media_id=media_id):
            save_to_history(url)
            processed_count += 1
            print(f"Published: {article_data['title']}")

if __name__ == "__main__":
    run_sheets_pipeline()
