from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs
from elevenlabs.play import play
from elevenlabs import VoiceSettings
from persona import VOICE_ID, VOICE_SETTINGS
import os

load_dotenv()

client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))

audio = client.text_to_speech.convert(
    text="Hmph... so you finally decided to talk to me? Took you long enough, baka. ...Well, I guess I'll allow it.",
    voice_id=VOICE_ID,
    model_id="eleven_v3",
    output_format="mp3_44100_128",
    voice_settings=VoiceSettings(**VOICE_SETTINGS),
)

play(audio)
