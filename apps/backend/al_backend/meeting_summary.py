from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

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
    progress_callback: Callable[[str], None] | None = None,
) -> MeetingSummaryResult:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required for meeting summaries")

    client = OpenAI(api_key=settings.openai_api_key)

    if progress_callback:
        progress_callback("transcribing_openai")

    with open(audio_path, "rb") as audio_file:
        transcription = client.audio.transcriptions.create(
            model=settings.openai_transcription_model,
            file=audio_file,
        )

    transcript = str(getattr(transcription, "text", "") or "").strip()

    if not transcript:
        return MeetingSummaryResult(transcript="", summary="")

    if progress_callback:
        progress_callback("summarizing_openai")

    participants = ", ".join(participant_names) if participant_names else "Unknown participants"
    prompt = (
        f"Write a concise work-only meeting summary in {language}.\n"
        "Use only facts explicitly present in the transcript. Do not invent tasks, owners, deadlines, decisions, or context.\n"
        "Ignore greetings, jokes, small talk, filler, repeated phrases, and off-topic conversation.\n"
        "Include only work-relevant discussion: goals, problems discussed, decisions, action items, blockers, and open questions.\n"
        "If a section has no real content, write 'None'.\n"
        "For action items, include an owner or deadline only if explicitly mentioned.\n"
        "Keep every bullet short and practical for a work Telegram chat.\n\n"
        f"Expected participants: {participants}\n\n"
        "Return exactly these sections:\n"
        "Participants:\n"
        "Discussed:\n"
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
