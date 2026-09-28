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

# Set the target WordPress Category ID for Features / Tourism (e.g., Change 25 to your category ID)
WP_CATEGORY_ID = 25 

HISTORY_FILE = "published_features_history.txt"

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
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly"
    ]
    
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    
    # Target Google Sheet using exact Spreadsheet ID to bypass title lookup errors
    SPREADSHEET_ID = "1prryBCnTg8f3p3EihipqzJdngcKrokjf77sTvbMkIwo"
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
    
    urls = []
    rows = sheet.get_all_values()
    for row in rows:
        for cell in row:
            cell_clean = cell.strip()
            if cell_clean.startswith("http://") or cell_clean.startswith("https://"):
                # Clean tracking parameters
                clean_url = cell_clean.split("?")[0]
                urls.append(clean_url)
                
    print(f"=== STEP 1 DEBUG: Found {len(urls)} URLs in Google Sheet ID '{SPREADSHEET_ID}' ===")
    return urls

def extract_article_content(url):
    """
    Scrapes article text using Trafilatura with a BeautifulSoup fallback.
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
            filename = f"feature_{image_url.split('/')[-1].split('?')[0]}.jpg"
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

def write_colour_feature_with_gemini(raw_text, target_url):
    """
    Uses Gemini to craft a rich, narrative-driven colour feature article.
    """
    client = genai.Client(api_key=GEMINI_API_KEY.strip())
    
    prompt = f"""
    You are a celebrated senior feature writer and master columnist. Transform the factual news report extracted from URL ({target_url}) into a deeply engaging, narrative-driven COLOUR FEATURE.

    EDITORIAL & STYLISTIC GUIDELINES:
    1. STYLE & TONE: Write with descriptive flair, atmospheric narrative, and reflective insight. Rather than straight news reportage, present a rich, analytical human-interest feature piece.
    2. OPENING HOOK: Start with an evocative opening scene, vivid description, or compelling quote—not a dry news summary.
    3. STRUCTURE: Organize the story using thematic headings (<h2> tags) that reflect key narrative themes, background context, and broader social or policy implications.
    4. NO SOURCE ATTRIBUTION: Never write "according to [Website]", "reports indicate", or cite source URLs. Present the story seamlessly in your own editorial voice.
    5. ZERO HALLUCINATIONS: Maintain absolute accuracy for all factual names, figures, locations, and events mentioned in the raw text.

    FORMATTING REQUIREMENTS:
    - Return ONLY a raw JSON object. Do not include markdown tags like ```json.
    - Fields required:
      "title": "An evocative, literary headline suitable for a magazine or feature section"
      "content": "<p>Vivid, narrative opening paragraph setting the scene...</p><p>Exploratory narrative paragraphs detailing the background...</p><h2>Subheading 1</h2><p>Analytical and descriptive commentary...</p><h2>Subheading 2</h2><p>Broader impacts and perspective...</p><p>Reflective concluding paragraph...</p>"
      "image_query": "2 to 3 evocative English keywords for stock photo search (e.g. 'sri lanka beach resort', 'colombo cityscape sunset')"

    Raw Source Material:
    {raw_text[:6000]}
    """
    
    response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
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
        "categories": [WP_CATEGORY_ID]
    }
    if featured_media_id:
        body["featured_media"] = featured_media_id
        
    res = requests.post(api_endpoint, headers=headers, json=body, timeout=10)
    if res.status_code in [200, 201]:
        return True
    else:
        print(f"=== STEP 3 ERROR: WP Post Failed ({res.status_code}): {res.text} ===")
        return False

def run_feature_pipeline():
    history = load_history()
    urls = fetch_urls_from_private_sheet()
    
    if not urls:
        print("=== STEP 1 DEBUG: No URLs retrieved. Exiting feature pipeline. ===")
        return

    processed_count = 0
    for url in urls:
        if processed_count >= 5:
            print("Batch target limit of 5 feature stories reached.")
            break
            
        if url in history:
            print(f"=== DEBUG: Skipping already processed URL: {url} ===")
            continue

        print(f"\nProcessing Feature URL ({processed_count + 1}/5): {url}")
        raw_text = extract_article_content(url)
        
        if not raw_text or len(raw_text) < 100:
            print(f"=== STEP 2 WARNING: Insufficient text extracted from {url}. Skipping without saving. ===")
            continue

        try:
            print("=== Drafting colour feature with Gemini... ===")
            article_data = write_colour_feature_with_gemini(raw_text, url)
        except Exception as e:
            print(f"=== Gemini Feature Writing Error for {url}: {e} ===")
            continue

        image_query = article_data.get("image_query", "sri lanka feature")
        image_url = get_pexels_image_url(image_query)
        media_id = upload_image_to_wordpress(image_url) if image_url else None

        print(f"=== STEP 3 DEBUG: Attempting WordPress post for '{article_data['title']}' ===")
        if post_to_wordpress(article_data["title"], article_data["content"], featured_media_id=media_id):
            save_to_history(url)
            processed_count += 1
            print(f"=== STEP 3 SUCCESS: Published feature {processed_count}/5 ('{article_data['title']}') to WordPress! ===")

if __name__ == "__main__":
    run_feature_pipeline()
