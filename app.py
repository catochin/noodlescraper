import os
from quart import Quart, render_template, request, jsonify
from noodle import NoodleScraper
from datetime import datetime
import json

app = Quart(__name__)
app.config['SECRET_KEY'] = os.urandom(24).hex()
app.config['JSON_SORT_KEYS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB max upload size

# Initialize scraper
scraper = NoodleScraper()

# Add built-in functions to Jinja2 environment
app.jinja_env.globals.update(
    max=max,
    min=min,
    int=int,
    str=str,
    len=len,
    zip=zip
)

# Add custom filters
app.jinja_env.filters['tojson'] = lambda obj: json.dumps(obj)

@app.context_processor
def inject_now():
    return {'now': datetime.now()}

@app.route('/')
async def index():
    return await render_template('index.html')

@app.route('/search')
async def search():
    query = request.args.get('q', '')
    page = int(request.args.get('page', 1))
    
    if not query:
        return await render_template('index.html')
    
    results = await scraper.search_videos(query, page)
    return await render_template('search_results.html', 
                               results=results, 
                               query=query, 
                               page=page)

@app.route('/video/<video_id>')
async def video_detail(video_id):
    # We'll just render the template and let client-side JS handle loading data from localStorage
    return await render_template('video_detail.html', video_id=video_id)

@app.route('/api/search')
async def api_search():
    query = request.args.get('q', '')
    page = int(request.args.get('page', 1))
    
    if not query:
        return jsonify({"error": "No search query provided"}), 400
    
    results = await scraper.search_videos(query, page)
    return jsonify(results)

@app.route('/api/video/<video_id>')
async def api_video(video_id):
    video_data = scraper.get_video_by_id(video_id)
    if not video_data:
        return jsonify({"error": "Video not found"}), 404
    
    return jsonify(video_data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=6969, debug=True)
