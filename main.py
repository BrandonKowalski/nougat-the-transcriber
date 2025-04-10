import os
import shutil
import uuid
from pathlib import Path
from typing import List

import openai
from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydub import AudioSegment

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

UPLOAD_DIR = Path("uploads")
CHUNKS_DIR = Path("chunks")
TRANSCRIPTS_DIR = Path("transcripts")
UPLOAD_DIR.mkdir(exist_ok=True)
CHUNKS_DIR.mkdir(exist_ok=True)
TRANSCRIPTS_DIR.mkdir(exist_ok=True)


@app.get("/", response_class=FileResponse)
async def upload_form(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/upload")
async def upload_audio(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(('.mp3', '.wav', '.m4a')):
        raise HTTPException(status_code=400, detail="Invalid file type.")

    audio_id = str(uuid.uuid4())
    uploaded_path = UPLOAD_DIR / f"{audio_id}_{file.filename}"

    with open(uploaded_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    chunk_paths = split_audio(uploaded_path)
    transcriptions = [transcribe_audio(chunk) for chunk in chunk_paths]
    full_transcription = merge_transcripts(transcriptions)
    formatted_transcription = format_transcript_paragraphs(full_transcription)

    transcript_path = TRANSCRIPTS_DIR / f"nougat_{audio_id}.txt"
    with open(transcript_path, "w") as f:
        f.write(formatted_transcription)

    headers = {"X-Transcript-Filename": f"nougat_{audio_id}.txt"}
    return FileResponse(
        transcript_path,
        media_type='text/plain',
        filename=f"nougat_{audio_id}_transcript.txt",
        headers=headers
    )


@app.get("/download/{audio_id}", response_class=FileResponse)
async def download_transcript(audio_id: str):
    transcript_path = TRANSCRIPTS_DIR / f"nougat_{audio_id}.txt"
    if not transcript_path.exists():
        raise HTTPException(status_code=404, detail="Transcript not found.")

    headers = {"X-Transcript-Filename": f"nougat_{audio_id}.txt"}
    return FileResponse(transcript_path, media_type='text/plain', filename=f"nougat_{audio_id}_transcript.txt", headers=headers)


def split_audio(filepath: Path, chunk_length_ms: int = 10 * 60 * 1000, overlap_ms: int = 15000) -> List[Path]:
    audio = AudioSegment.from_file(filepath)
    chunks = []
    start = 0
    i = 0

    while start < len(audio):
        end = start + chunk_length_ms
        chunk = audio[start:end]
        chunk_filename = CHUNKS_DIR / f"chunk_{i}_{filepath.stem}.mp3"
        chunk.export(chunk_filename, format="mp3")
        chunks.append(chunk_filename)
        start += chunk_length_ms - overlap_ms
        i += 1

    return chunks


def merge_transcripts(transcripts: List[str], overlap_words: int = 20) -> str:
    if not transcripts:
        return ""
    merged = transcripts[0]
    for next_transcript in transcripts[1:]:
        prev_end = merged.split()[-overlap_words:]
        next_start = next_transcript.split()
        for i in range(overlap_words, 0, -1):
            if prev_end[-i:] == next_start[:i]:
                merged += " " + " ".join(next_start[i:])
                break
        else:
            merged += " " + next_transcript
    return merged


def transcribe_audio(filepath: Path) -> str:
    with open(filepath, "rb") as audio_file:
        transcript = openai.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=audio_file,
        )
    return transcript.text


def format_transcript_paragraphs(text: str) -> str:
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Split this into paragraphs without changing any of the input words"},
            {"role": "user", "content": text},
        ],
    )
    return response.choices[0].message.content.strip()
