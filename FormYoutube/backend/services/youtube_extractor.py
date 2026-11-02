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
            'ffmpeg_location': 'D:\\PhD\\csv_version\\ffmpeg-master-latest-win64-gpl-shared\\bin\\ffmpeg.exe',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
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
