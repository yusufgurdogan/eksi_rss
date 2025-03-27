from flask import Flask, Response, render_template, request, redirect, url_for
import cloudscraper
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator
import datetime
import pytz
import re
import time
import os
import json
import logging
from urllib.parse import quote
from flask_caching import Cache

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configure cache
cache_config = {
    "DEBUG": True,
    "CACHE_TYPE": "SimpleCache",
    "CACHE_DEFAULT_TIMEOUT": 900  # 15 minutes
}
app.config.from_mapping(cache_config)
cache = Cache(app)

# Data file for subscribed topics
SUBSCRIPTIONS_FILE = 'subscriptions.json'

def load_subscriptions():
    """Load the list of subscribed topics from file"""
    if os.path.exists(SUBSCRIPTIONS_FILE):
        with open(SUBSCRIPTIONS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_subscriptions(subscriptions):
    """Save the list of subscribed topics to file"""
    with open(SUBSCRIPTIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(subscriptions, f, ensure_ascii=False, indent=2)

def parse_topic_url(input_str):
    """Parse different input formats to get a valid Ekşi Sözlük topic URL and ID"""
    # If it's a full URL
    if input_str.startswith('http'):
        # Extract topic ID if present
        match = re.search(r'--(\d+)', input_str)
        if match:
            topic_id = match.group(1)
            return input_str, topic_id
        return input_str, None
    
    # If it's just a numeric ID
    if input_str.isdigit():
        return f"https://eksisozluk.com/baslik/{input_str}", input_str
    
    # If it's a slug with ID
    match = re.search(r'--(\d+)', input_str)
    if match:
        topic_id = match.group(1)
        return f"https://eksisozluk.com/{input_str}", topic_id
    
    # Otherwise, assume it's a search term
    encoded_term = quote(input_str)
    return f"https://eksisozluk.com/?q={encoded_term}", None

@cache.memoize(timeout=900)  # Cache for 15 minutes
def fetch_eksi_page(url):
    """Fetch an Ekşi Sözlük page with cloudscraper"""
    scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True
        },
        delay=5
    )
    
    try:
        logger.info(f"Fetching URL: {url}")
        # Create a new session and fetch the page
        response = scraper.get(url, allow_redirects=True)
        response.raise_for_status()
        
        # Return the response
        return response
    except Exception as e:
        logger.error(f"Error fetching page {url}: {e}")
        return None

def get_topic_info(url, topic_id=None):
    """Get topic information and handle redirects"""
    response = fetch_eksi_page(url)
    if not response:
        return None, None, None, None
    
    # If we got redirected, update the URL
    final_url = response.url
    logger.info(f"Final URL after redirects: {final_url}")
    
    # Parse the HTML
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Extract topic info
    title_element = soup.select_one('h1#title')
    if not title_element:
        logger.error("Could not find topic title - page structure may have changed")
        return None, None, None, None
    
    topic_title = title_element.get_text(strip=True)
    
    # If topic_id wasn't provided, try to extract it
    if not topic_id:
        topic_id = title_element.get('data-id')
        if not topic_id:
            # Try to extract it from the URL
            match = re.search(r'--(\d+)', final_url)
            if match:
                topic_id = match.group(1)
    
    # Extract topic slug
    topic_slug = None
    if "data-slug" in title_element.attrs:
        topic_slug = title_element.get('data-slug')
    else:
        # Try to extract from URL
        match = re.search(r'/(.*?)--\d+', final_url)
        if match:
            topic_slug = match.group(1)
    
    logger.info(f"Found topic: '{topic_title}' (ID: {topic_id})")
    return topic_title, topic_id, topic_slug, final_url

def create_feed_for_topic(topic_url, topic_id=None, max_pages=3):
    """Create an RSS feed for entries in an Ekşi Sözlük topic with pagination and correct timezone"""
    # First get the topic info without date parameter
    topic_title, topic_id, topic_slug, final_url = get_topic_info(topic_url, topic_id)
    
    if not topic_title or not topic_id:
        logger.error(f"Failed to get topic info for {topic_url}")
        return None
    
    # Create feed
    fg = FeedGenerator()
    fg.id(final_url)
    fg.title(f'Ekşi - {topic_title}')
    fg.link(href=final_url, rel='alternate')
    fg.description(f'New entries for topic: {topic_title}')
    fg.language('tr')
    
    # Add feed level publication date (with Turkish timezone)
    istanbul_tz = pytz.timezone('Europe/Istanbul')
    fg.pubDate(datetime.datetime.now(istanbul_tz))
    
    # Append today's date parameter to the URL
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    
    # Process multiple pages
    entries_added = 0
    
    for page in range(1, max_pages + 1):
        # Construct URL with date and page parameters
        if '?' in final_url:
            page_url = f"{final_url}&day={today}"
        else:
            page_url = f"{final_url}?day={today}"
            
        # Add page parameter if not the first page
        if page > 1:
            page_url = f"{page_url}&p={page}"
        
        logger.info(f"Fetching page {page} with date parameter: {page_url}")
        
        # Fetch the page with the date parameter
        response = fetch_eksi_page(page_url)
        if not response:
            break
        
        # Parse the HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract entries
        entries = soup.select('ul#entry-item-list > li')
        if not entries:
            # No more entries found, stop pagination
            break
            
        logger.info(f"Found {len(entries)} entries on page {page} for topic: {topic_title}")
        
        for entry in entries:
            try:
                entry_id = entry.get('data-id')
                if not entry_id:
                    continue
                    
                author = entry.get('data-author')
                content_element = entry.select_one('div.content')
                if not content_element:
                    continue
                    
                content = content_element.decode_contents()
                date_element = entry.select_one('div.info a.entry-date')
                if not date_element:
                    continue
                    
                date_text = date_element.get_text(strip=True)
                permalink = date_element.get('href')
                
                # Create entry in feed
                fe = fg.add_entry()
                fe.id(f'https://eksisozluk.com{permalink}')
                
                # Use just the author name as the title
                fe.title(author)
                
                fe.link(href=f'https://eksisozluk.com{permalink}')
                fe.author(name=author)
                fe.content(content, type='html')
                
                # Parse date and make it timezone-aware with Turkish timezone
                date_match = re.search(r'(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2})', date_text)
                if date_match:
                    date_str = date_match.group(1)
                    try:
                        # Parse date and make it timezone-aware with Turkish timezone
                        entry_date = datetime.datetime.strptime(date_str, '%d.%m.%Y %H:%M')
                        entry_date = istanbul_tz.localize(entry_date)
                        fe.published(entry_date)
                    except ValueError as e:
                        logger.warning(f"Error parsing date '{date_str}': {e}")
                        # Use current time with Turkish timezone
                        fe.published(datetime.datetime.now(istanbul_tz))
                else:
                    # Use current time with Turkish timezone
                    fe.published(datetime.datetime.now(istanbul_tz))
                
                entries_added += 1
            except Exception as e:
                logger.error(f"Error processing entry: {e}")
        
        # Check if we should continue to the next page
        # If this page had fewer entries than expected, don't fetch more pages
        if len(entries) < 10:  # Assuming each page has around 10 entries
            break
    
    logger.info(f"Added {entries_added} entries in total for topic: {topic_title}")
    return fg

@app.route('/')
def index():
    """Home page showing available feeds"""
    subscriptions = load_subscriptions()
    return render_template('index.html', subscriptions=subscriptions)

@app.route('/add_feed', methods=['POST'])
def add_feed():
    """Add a new topic feed"""
    topic_input = request.form.get('topic', '')
    if not topic_input:
        return redirect(url_for('index'))
    
    # Parse the topic URL/identifier
    topic_url, topic_id = parse_topic_url(topic_input)
    
    # Get the topic info
    topic_title, topic_id, topic_slug, final_url = get_topic_info(topic_url, topic_id)
    
    if not topic_id or not topic_title:
        return render_template('error.html', message=f"Could not find topic: {topic_input}")
    
    # Add to subscriptions if not already there
    subscriptions = load_subscriptions()
    for sub in subscriptions:
        if sub.get('id') == topic_id:
            return redirect(url_for('index'))
    
    subscriptions.append({
        'id': topic_id,
        'title': topic_title,
        'url': final_url,
        'slug': topic_slug,
        'added': datetime.datetime.now().isoformat()
    })
    
    save_subscriptions(subscriptions)
    return redirect(url_for('index'))

@app.route('/remove_feed/<topic_id>')
def remove_feed(topic_id):
    """Remove a topic feed"""
    subscriptions = load_subscriptions()
    subscriptions = [sub for sub in subscriptions if sub.get('id') != topic_id]
    save_subscriptions(subscriptions)
    return redirect(url_for('index'))

@app.route('/feed/topic/<topic_id>.xml')
def feed_by_id(topic_id):
    """Serve an RSS feed for a specific topic by ID"""
    # Check if this is a known subscription
    subscriptions = load_subscriptions()
    topic_url = None
    
    for sub in subscriptions:
        if sub.get('id') == topic_id:
            topic_url = sub.get('url')
            break
    
    if not topic_url:
        topic_url = f"https://eksisozluk.com/baslik/{topic_id}"
    
    # Create feed with pagination (get up to 3 pages)
    fg = create_feed_for_topic(topic_url, topic_id, max_pages=3)
    
    if not fg:
        return "Failed to generate feed", 500
    
    # Generate RSS
    rss_feed = fg.rss_str(pretty=True)
    
    return Response(rss_feed, mimetype='application/xml')

@app.route('/feed/search/<path:search_term>.xml')
def feed_by_search(search_term):
    """Serve an RSS feed for a specific search term"""
    encoded_term = quote(search_term)
    topic_url = f"https://eksisozluk.com/?q={encoded_term}"
    
    # Create feed
    fg = create_feed_for_topic(topic_url)
    
    if not fg:
        return "Failed to generate feed", 500
    
    # Generate RSS
    rss_feed = fg.rss_str(pretty=True)
    
    return Response(rss_feed, mimetype='application/xml')

@app.route('/all.xml')
def all_feeds():
    """Serve a combined feed of all subscribed topics"""
    subscriptions = load_subscriptions()
    
    # Create combined feed
    fg = FeedGenerator()
    fg.id(request.url)
    fg.title('Ekşi - All Subscribed Topics')
    fg.link(href=request.url, rel='self')
    fg.description('Combined feed of all subscribed Ekşi Sözlük topics')
    fg.language('tr')
    
    # Add feed level publication date (with timezone)
    fg.pubDate(datetime.datetime.now(pytz.UTC))
    
    # Limit to the most recent 10 topics for performance
    for sub in subscriptions[:10]:
        topic_id = sub.get('id')
        topic_url = sub.get('url')
        
        if topic_url:
            topic_feed = create_feed_for_topic(topic_url, topic_id)
            if topic_feed:
                # Add entries from this feed to the combined feed
                for entry in topic_feed.entry():
                    fg.add_entry(entry)
    
    # Generate RSS
    rss_feed = fg.rss_str(pretty=True)
    
    return Response(rss_feed, mimetype='application/xml')

# Create template files
def create_template_files():
    if not os.path.exists('templates'):
        os.makedirs('templates')
        
    index_template = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Ekşi Sözlük RSS Service</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
            h1 { color: #76a912; }
            .feed-list { margin-top: 20px; }
            .feed-item { padding: 10px; border-bottom: 1px solid #eee; }
            .feed-title { font-weight: bold; }
            .feed-url { color: #666; font-size: 0.9em; word-break: break-all; }
            .feed-actions { margin-top: 5px; }
            .feed-actions a { color: #76a912; text-decoration: none; }
            form { margin-top: 20px; padding: 15px; background: #f5f5f5; border-radius: 5px; }
            input[type="text"] { width: 70%; padding: 8px; }
            button { padding: 8px 15px; background: #76a912; color: white; border: none; cursor: pointer; }
            .combined-feed { margin-top: 20px; padding: 10px; background: #f0f9e8; border-radius: 5px; }
        </style>
    </head>
    <body>
        <h1>Ekşi Sözlük RSS Service</h1>
        
        <form action="/add_feed" method="post">
            <h3>Add a New Feed</h3>
            <input type="text" name="topic" placeholder="Topic URL, ID, or search term" required>
            <button type="submit">Add</button>
        </form>
        
        <div class="combined-feed">
            <h3>Combined Feed</h3>
            <p>Subscribe to all topics in one feed: <a href="/all.xml">/all.xml</a></p>
        </div>
        
        <div class="feed-list">
            <h3>Subscribed Topics</h3>
            {% if subscriptions %}
                {% for sub in subscriptions %}
                    <div class="feed-item">
                        <div class="feed-title">{{ sub.title }}</div>
                        <div class="feed-url">{{ sub.url }}</div>
                        <div class="feed-actions">
                            <a href="/feed/topic/{{ sub.id }}.xml">View RSS</a> | 
                            <a href="/remove_feed/{{ sub.id }}">Remove</a>
                        </div>
                    </div>
                {% endfor %}
            {% else %}
                <p>No subscriptions yet. Add some topics!</p>
            {% endif %}
        </div>
    </body>
    </html>
    '''
    
    error_template = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Error - Ekşi Sözlük RSS Service</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
            h1 { color: #d9534f; }
            .error-box { padding: 15px; background: #f2dede; border: 1px solid #ebccd1; color: #a94442; border-radius: 5px; }
            a { color: #76a912; text-decoration: none; }
        </style>
    </head>
    <body>
        <h1>Error</h1>
        <div class="error-box">
            <p>{{ message }}</p>
        </div>
        <p><a href="/">Back to home</a></p>
    </body>
    </html>
    '''
    
    # Write the template files
    with open(os.path.join('templates', 'index.html'), 'w', encoding='utf-8') as f:
        f.write(index_template)
    
    with open(os.path.join('templates', 'error.html'), 'w', encoding='utf-8') as f:
        f.write(error_template)

if __name__ == '__main__':
    # Create subscription file if it doesn't exist
    if not os.path.exists(SUBSCRIPTIONS_FILE):
        save_subscriptions([])
    
    # Create template files
    create_template_files()
    
    # Run the app
    app.run(host='0.0.0.0', port=5000, debug=False)