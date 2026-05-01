from __future__ import annotations

from dataclasses import dataclass

from openai import OpenAI

from .settings import Settings


@dataclass(frozen=True)
class MeetingSummaryResult:
    transcript: str
    summary: str


def generate_meeting_summary(
    settings: Settings,
    audio_path: str,
    *,
    participant_names: list[str],
    language: str,
) -> MeetingSummaryResult:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required for meeting summaries")

    client = OpenAI(api_key=settings.openai_api_key)

    with open(audio_path, "rb") as audio_file:
        transcription = client.audio.transcriptions.create(
            model=settings.openai_transcription_model,
            file=audio_file,
        )

    transcript = str(getattr(transcription, "text", "") or "").strip()

    if not transcript:
        return MeetingSummaryResult(transcript="", summary="")

    participants = ", ".join(participant_names) if participant_names else "Unknown participants"
    prompt = (
        f"Write a very concise meeting summary in {language}. "
        "Use only facts from the transcript. Do not invent tasks. "
        "Keep it short and practical for a work Telegram chat.\n\n"
        f"Participants: {participants}\n\n"
        "Return exactly these sections:\n"
        "Participants:\n"
        "Decisions:\n"
        "Action items:\n"
        "Open questions:\n\n"
        f"Transcript:\n{transcript}"
    )
    response = client.responses.create(
        model=settings.openai_summary_model,
        input=prompt,
    )
    summary = str(getattr(response, "output_text", "") or "").strip()
    return MeetingSummaryResult(transcript=transcript, summary=summary)
