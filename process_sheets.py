def run_sheets_pipeline():
    history = load_history()
    urls = fetch_urls_from_private_sheet()
    
    processed_count = 0
    for url in urls:
        if processed_count >= 3:
            print("Batch limit reached.")
            break
            
        if url in history:
            print(f"Skipping already processed URL: {url}")
            continue

        print(f"\nProcessing URL: {url}")
        raw_text = extract_article_content(url)
        
        # Check if extraction yielded content
        if not raw_text or len(raw_text) < 100:
            print(f"--> WARNING: Trafilatura could not extract text from {url}. Skipping without saving to history.")
            # DO NOT call save_to_history(url) here so you can retry later!
            continue

        print(f"--> Extracted {len(raw_text)} characters. Sending to Gemini...")

        try:
            article_data = rewrite_with_gemini(raw_text, url)
        except Exception as e:
            print(f"--> Gemini error: {e}")
            continue

        image_query = article_data.get("image_query", "sri lanka news")
        image_url = get_pexels_image_url(image_query)
        media_id = upload_image_to_wordpress(image_url) if image_url else None

        # Post to WordPress
        if post_to_wordpress(article_data["title"], article_data["content"], featured_media_id=media_id):
            save_to_history(url)
            processed_count += 1
            print(f"--> SUCCESS: Published '{article_data['title']}' to WordPress!")
        else:
            print(f"--> ERROR: WordPress rejected the post for {url}")
