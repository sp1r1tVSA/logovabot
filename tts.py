import io
import re
import logging
import edge_tts

logger = logging.getLogger(__name__)

# Male neural voice for Dmitry (Russian male voice suitable for sports commentator "Temshik")
DEFAULT_VOICE = "ru-RU-DmitryNeural"

def clean_text_for_tts(text: str) -> str:
    """
    Cleans text from Markdown, URLs, emojis, and formatting to prepare it for natural speech synthesis.
    """
    if not text:
        return ""

    # Remove Markdown headers, bold, italic, code
    text = re.sub(r'#+\s*', '', text)
    text = re.sub(r'\*+([^*]+)\*+', r'\1', text)
    text = re.sub(r'_+([^_]+)_+', r'\1', text)
    text = re.sub(r'`+([^`]+)`+', r'\1', text)
    
    # Remove Markdown links [text](url) -> text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    
    # Remove standalone URLs
    text = re.sub(r'https?://\S+', '', text)
    
    # Remove bullet points and special table symbols
    text = re.sub(r'[•\-—=─│|]', ' ', text)
    
    # Expand common abbreviations for football stats
    text = re.sub(r'\bvs\b', 'против', text, flags=re.IGNORECASE)
    text = re.sub(r'\bв:\b', 'побед:', text, flags=re.IGNORECASE)
    text = re.sub(r'\bн:\b', 'ничьих:', text, flags=re.IGNORECASE)
    text = re.sub(r'\bп:\b', 'поражений:', text, flags=re.IGNORECASE)

    # Remove extra spaces and newlines
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cleaned_text = ". ".join(lines)
    
    # Clean multiple spaces and dots
    cleaned_text = re.sub(r'\.\s*\.', '.', cleaned_text)
    cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()
    
    return cleaned_text

async def generate_voice_audio(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    """
    Asynchronously generates audio bytes (.mp3 / .ogg) for the given text using edge-tts.
    """
    cleaned = clean_text_for_tts(text)
    if not cleaned:
        raise ValueError("Text is empty after cleaning for TTS.")

    # Limit text length if too long for a single voice message
    if len(cleaned) > 2000:
        cleaned = cleaned[:2000] + "... И так далее!"

    communicate = edge_tts.Communicate(cleaned, voice)
    audio_buffer = io.BytesIO()

    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_buffer.write(chunk["data"])

    audio_buffer.seek(0)
    return audio_buffer.getvalue()
