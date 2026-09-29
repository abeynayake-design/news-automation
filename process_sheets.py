import os
import json
import base64
import requests
import trafilatura
from bs4 import BeautifulSoup
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
        print("=== STEP 1 ERROR: Missing GOOGLE_CREDENTIALS secret ===")
        return []
        
    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    
    # Sanitize private key formatting (handles escaped newlines \n cleanly)
    if "private_key" in creds_dict and "\\n" in creds_dict["private_key"]:
        creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly"
    ]
    
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    
    # Use direct sheet opening by key if available or exact sheet name
    sheet_name = "News stories for MAKE"
    sheet = client.open(sheet_name).sheet1
    
    urls = []
    rows = sheet.get_all_values()
    for row in rows:
        for cell in row:
            cell_clean = cell.strip()
            if cell_clean.startswith("http://") or cell_clean.startswith("https://"):
                clean_url = cell_clean.split("?")[0]
                urls.append(clean_url)
                
    print(f"=== STEP 1 DEBUG: Found {len(urls)} URLs in Google Sheet ===")
    return urls

def extract_article_content(url):
    """
    Robust scraper with custom BeautifulSoup fallback for ft.lk and adaderana.lk.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=12)
        
        if response.status_code == 200:
            extracted_text = trafilatura.extract(response.text)
            if extracted_text and len(extracted_text.strip()) > 150:
                print(f"=== STEP 2 DEBUG: Trafilatura extracted {len(extracted_text)} chars from {url} ===")
                return extracted_text

            soup = BeautifulSoup(response.text, 'html.parser')
            for element in soup(["script", "style", "iframe", "header", "footer", "nav", "aside"]):
                element.decompose()

            paragraphs = soup.find_all('p')
            body_text = "\n".join([p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 20])
            
            if len(body_text) > 150:
                print(f"=== STEP 2 DEBUG: BeautifulSoup extracted {len(body_text)} chars from {url} ===")
                return body_text
        else:
            print(f"=== STEP 2 DEBUG: HTTP {response.status_code} error fetching {url} ===")
            
        print(f"=== STEP 2 DEBUG: Extraction returned EMPTY text for {url} ===")
    except Exception as e:
        print(f"=== STEP 2 DEBUG: Extraction error for {url}: {e} ===")
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
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": img_res.headers.get("Content-Type", "image/jpeg")
            }
            upload_res = requests.post(media_endpoint, headers=media_headers, data=img_res.content, timeout=15)
            if upload_res.status_code in [200, 201]:
                return upload_res.json().get("id")
            else:
                print(f"WP Media Upload Error ({upload_res.status_code}): {upload_res.text}")
    except Exception as e:
        print(f"Image upload exception: {e}")
    return None

def rewrite_with_gemini(raw_text, target_url):
    client = genai.Client(api_key=GEMINI_API_KEY.strip())
    prompt = f"""
    You are an expert senior news editor writing for BrunchPress. Rewrite the following article text extracted from URL ({target_url}) into a clear, professional, engaging news report.

    EDITORIAL MANDATES:
    1. NO SOURCE ATTRIBUTION: Never write "according to [Website]" or mention external outlets/URLs.
    2. ZERO HALLUCINATIONS: Rely strictly on provided facts.
    3. COMPLETE ENTITY INTEGRATION: Include all full names, places, and facts mentioned.

    Formatting rules:
    - Return ONLY a raw JSON object. Do not include markdown tags like ```json.
    - Fields required:
      "title": "A compelling headline"
      "content": "<p>Comprehensive opening paragraph...</p><p>Detailed body paragraphs...</p><h2>Key Developments</h2><ul><li>Point 1</li><li>Point 2</li></ul><p>Concluding paragraph...</p>"
      "image_query": "2 to 3 concise English keywords for stock photo search"

    Raw Article Text:
    {raw_text[:4500]}
    """
    response = client.models.generate_content(model="gemini-3.8-flash", contents=prompt)
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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    body = {
        "title": title, 
        "content": content_html, 
        "status": "publish",
        "categories": [18]
    }
    if featured_media_id:
        body["featured_media"] = featured_media_id
        
    res = requests.post(api_endpoint, headers=headers, json=body, timeout=10)
    if res.status_code in [200, 201]:
        return True
    else:
        print(f"=== STEP 3 ERROR: WP Post Failed ({res.status_code}): {res.text} ===")
        return False

def run_sheets_pipeline():
    history = load_history()
    urls = fetch_urls_from_private_sheet()
    
    if not urls:
        print("=== STEP 1 DEBUG: No URLs retrieved. Exiting pipeline. ===")
        return

    processed_count = 0
    for url in urls:
        if processed_count >= 5:
            print("Batch target limit of 5 stories reached.")
            break
            
        if url in history:
            print(f"=== DEBUG: Skipping already processed URL: {url} ===")
            continue

        print(f"\nProcessing URL ({processed_count + 1}/5): {url}")
        raw_text = extract_article_content(url)
        
        if not raw_text or len(raw_text) < 100:
            print(f"=== STEP 2 WARNING: Could not extract readable text for {url}. Skipping without saving to history. ===")
            continue

        try:
            print("=== Sending extracted text to Gemini... ===")
            article_data = rewrite_with_gemini(raw_text, url)
        except Exception as e:
            print(f"=== Gemini Error for {url}: {e} ===")
            continue

        image_query = article_data.get("image_query", "sri lanka news")
        image_url = get_pexels_image_url(image_query)
        media_id = upload_image_to_wordpress(image_url) if image_url else None

        print(f"=== STEP 3 DEBUG: Attempting WordPress post for '{article_data['title']}' ===")
        if post_to_wordpress(article_data["title"], article_data["content"], featured_media_id=media_id):
            save_to_history(url)
            processed_count += 1
            print(f"=== STEP 3 SUCCESS: Published story {processed_count}/5 ('{article_data['title']}') to WordPress! ===")

if __name__ == "__main__":
    run_sheets_pipeline()
