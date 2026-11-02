from openai import OpenAI

def Speech_to_text(audio_file_path,stt_name,stt_api_key):
    client = OpenAI(api_key=stt_api_key)
    with open(audio_file_path, "rb") as f:
        transcript = client.audio.transcriptions.create(
            model=stt_name,
            file=f
        )
    return transcript