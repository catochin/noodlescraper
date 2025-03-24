import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse
from cloudscraper import create_scraper as Session
from selectolax.parser import HTMLParser


class NoodleScraper:
    """A class-based API for scraping and processing videos from noodlemagazine.com"""

    def __init__(self):
        """Initialize the NoodleScraper with default settings"""
        self.scraper = Session()
        self._set_chrome_version()
        self.headers = {'User-Agent': self.user_agent}
        self.cookies = {'age_verification': '1'}
        self.base_url = 'https://noodlemagazine.com'

    def _set_chrome_version(self):
        """Get the latest stable Chrome version for the user agent"""
        try:
            data = self.scraper.get('https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions.json').json()
            stable_chrome_version = data['channels']['Stable']['version']
            self.user_agent = f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{stable_chrome_version} Safari/537.36'
        except Exception as e:
            # Fallback user agent if the API request fails
            self.user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            print(f"Error getting Chrome version: {e}")

    def fetch(self, url):
        """Fetch content from a URL with error handling
        
        Args:
            url (str): The URL to fetch
            
        Returns:
            str or None: The HTML content if successful, None otherwise
        """
        try:
            return self.scraper.get(url, headers=self.headers, cookies=self.cookies, timeout=10).text
        except Exception as e:
            print(f"Error loading {url}: {e}")
            return None

    def parse_playlist(self, html, base_url, download_url):
        """Extract playlist data from a video download page
        
        Args:
            html (str): The HTML content of the download page
            base_url (str): The base URL of the website
            download_url (str): The download URL of the video
            
        Returns:
            dict or None: The parsed playlist data if successful, None otherwise
        """
        tree = HTMLParser(html)
        script_tag = tree.css_first("script")
        if script_tag:
            script_text = script_tag.text()
            json_start = script_text.find('window.playlist = ') + len('window.playlist = ')
            json_end = script_text.find(';', json_start)
            playlist_json = script_text[json_start:json_end]

            try:
                playlist_data = json.loads(playlist_json)
                video_id = urlparse(download_url).path.split('/')[-1].split('?')[0]
                return {'id': video_id, **playlist_data}
            except json.JSONDecodeError:
                return None
        return None

    def process_video(self, base_url, video):
        """Process a single video to extract its details
        
        Args:
            base_url (str): The base URL of the website
            video: The video element from the search results
            
        Returns:
            dict or None: The processed video data if successful, None otherwise
        """
        video_url = urljoin(base_url, video.attributes.get('href'))
        video_html = self.fetch(video_url)
        if not video_html:
            return None

        video_tree = HTMLParser(video_html)

        og_video_tag = video_tree.css_first('meta[property="og:video"]')
        if not og_video_tag:
            return None
        og_video = urljoin(base_url, og_video_tag.attributes.get('content'))
        og_old_url = '{uri.scheme}://{uri.netloc}'.format(uri=urlparse(og_video))
        og_video = og_video.replace(og_old_url, base_url)
        download_url = og_video.replace('player', 'download')

        og_title_tag = video_tree.css_first('meta[property="og:title"]')
        title = og_title_tag.attributes.get('content') if og_title_tag else "Untitled"

        video_tags = [tag.strip() for content in video_tree.css('meta[property="video:tag"]') for tag in content.attributes.get('content', '').split(',')]

        download_html = self.fetch(download_url)
        if not download_html:
            return None

        playlist_data = self.parse_playlist(download_html, base_url, download_url)
        if playlist_data:
            playlist_data.update({"title": title, "tags": video_tags, 'player_url': og_video})
        return playlist_data

    async def search_videos(self, query, page=1):
        """Search for videos by query
        
        Args:
            query (str): The search query
            page (int, optional): The page number for pagination. Defaults to 1.
            
        Returns:
            dict: The search results including videos data or error message
        """
        r = self.fetch(f'{self.base_url}/video/{query}?p={page}')
        if not r:
            return {'error': 'Failed to load page'}

        base_url = '{uri.scheme}://{uri.netloc}'.format(uri=urlparse(f'{self.base_url}/video/{query}'))
        tree = HTMLParser(r)
        videos = tree.css('.list_videos .item_link')

        results = {'base_url': base_url, 'query': query, 'page': page, 'videos': []}

        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor() as pool:
            tasks = [loop.run_in_executor(pool, self.process_video, base_url, video) for video in videos]
            results['videos'] = [res for res in await asyncio.gather(*tasks) if res]

        return results

    def get_video_by_id(self, video_id):
        """Get a specific video by its ID
        
        Args:
            video_id (str): The ID of the video to fetch
            
        Returns:
            dict or None: The video data if successful, None otherwise
        """
        video_url = f'{self.base_url}/video/{video_id}'
        video_html = self.fetch(video_url)
        if not video_html:
            return None
        
        video_tree = HTMLParser(video_html)
        
        og_video_tag = video_tree.css_first('meta[property="og:video"]')
        if not og_video_tag:
            return None
            
        og_video = urljoin(self.base_url, og_video_tag.attributes.get('content'))
        download_url = og_video.replace('player', 'download')
        
        og_title_tag = video_tree.css_first('meta[property="og:title"]')
        title = og_title_tag.attributes.get('content') if og_title_tag else "Untitled"
        
        video_tags = [tag.strip() for content in video_tree.css('meta[property="video:tag"]') for tag in content.attributes.get('content', '').split(',')]
        
        download_html = self.fetch(download_url)
        if not download_html:
            return None
            
        playlist_data = self.parse_playlist(download_html, self.base_url, download_url)
        if playlist_data:
            playlist_data.update({"title": title, "tags": video_tags})
        return playlist_data
