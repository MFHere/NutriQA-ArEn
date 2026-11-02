import os 
import re 
import yt_dlp
from pytube import YouTube
from youtube_transcript_api import YouTubeTranscriptApi


class YouTubeContentExtractor:
    def __init__(self):
        pass

    def extract_videoid(self,watch_url):
        match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", watch_url)
        if match:
            video_id = match.group(1)
            return video_id
        

    def get_watchurl(self,url):
        yt = YouTube(str(url))
        watch_url=yt.watch_url
        return watch_url
    


    def fetch_subtitle(self,video_id, languages=['ar']):
        try:
            return YouTubeTranscriptApi().fetch(video_id,languages=languages)
        except:
            return None
        


    def download_audio(self, watch_url, output_path=os.path.join(os.path.dirname(os.path.dirname(__file__)),'temp'), filename="my_audio"):
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': f'{output_path}/{filename}',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            # Reduce HTTP 403 from YouTube: use a browser-like user-agent and common extractor tweaks
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            },
            'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(watch_url, download=True)
            return {
                "file": os.path.abspath(f"{output_path}/{filename}.mp3"),
                "title": info.get("title"),
                "id": info.get("id"),
                "duration": info.get("duration"),
                "uploader": info.get("uploader"),
            }
        except Exception as e:
            return {"error": str(e)}
