from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs
from elevenlabs.play import play
from elevenlabs import VoiceSettings
from openai import OpenAI
import speech_recognition as sr
import os

from persona import REZE_SYSTEM_PROMPT as SYSTEM_PROMPT, VOICE_ID, VOICE_SETTINGS
from memory import trimmed_history

load_dotenv()

el_client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
deepseek = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
)
recognizer = sr.Recognizer()

conversation_history = []


def listen() -> str | None:
    with sr.Microphone() as source:
        recognizer.adjust_for_ambient_noise(source, duration=0.5)
        print("\n🎤 Escuchando...")
        try:
            audio = recognizer.listen(source, timeout=5, phrase_time_limit=15)
        except sr.WaitTimeoutError:
            return None

    try:
        text = recognizer.recognize_google(audio, language="es-AR")
        print(f"Tú: {text}")
        return text
    except sr.UnknownValueError:
        return None
    except sr.RequestError as e:
        print(f"Error STT: {e}")
        return None


def think(user_input: str) -> str:
    conversation_history.append({"role": "user", "content": user_input})

    response = deepseek.chat.completions.create(
        model="deepseek-chat",
        max_tokens=150,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, *trimmed_history(conversation_history)],
    )

    reply = response.choices[0].message.content
    conversation_history.append({"role": "assistant", "content": reply})
    return reply


def speak(text: str):
    print(f"Reze: {text}")
    audio = el_client.text_to_speech.convert(
        text=text,
        voice_id=VOICE_ID,
        model_id="eleven_flash_v2_5",
        output_format="mp3_44100_128",
        voice_settings=VoiceSettings(**VOICE_SETTINGS),
    )
    play(audio)


def main():
    print("=== Reze está lista! (Ctrl+C para salir) ===")
    speak("Hmph... apareciste por fin. Soy Reze. No esperes que sea amable, baka.")

    while True:
        try:
            user_input = listen()
            if not user_input:
                continue

            reply = think(user_input)
            speak(reply)

        except KeyboardInterrupt:
            speak("...Tch. Ya te vas. No me importa. Sayonara, baka.")
            break


if __name__ == "__main__":
    main()
