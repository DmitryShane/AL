from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from openai import OpenAI

from .settings import Settings


DEFAULT_MEETING_SUMMARY_PROMPT = """Write a concise work-only meeting summary.
Use only facts explicitly present in the transcript. Do not invent tasks, owners, deadlines, decisions, or context.
Ignore greetings, jokes, small talk, filler, repeated phrases, and off-topic conversation.
Include only work-relevant discussion: goals, problems discussed, decisions, action items, blockers, and open questions.
If a section has no real content, write None.
Never apologize or ask for a transcript. If the transcript has no usable work content, return the required sections with None.
For action items, include an owner or deadline only if explicitly mentioned.
Keep every bullet short and practical for a work Telegram chat."""


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
    prompt_template: str | None = None,
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
    sections = meeting_summary_sections(language)
    prompt = render_meeting_summary_prompt(
        prompt_template or DEFAULT_MEETING_SUMMARY_PROMPT,
        language=language,
        participants=participants,
        sections=sections,
        transcript=transcript,
    )
    response = client.responses.create(
        model=settings.openai_summary_model,
        input=prompt,
    )
    summary = str(getattr(response, "output_text", "") or "").strip()
    return MeetingSummaryResult(transcript=transcript, summary=summary)


def render_meeting_summary_prompt(
    instructions: str,
    *,
    language: str,
    participants: str,
    sections: dict[str, str],
    transcript: str,
) -> str:
    return (
        f"{instructions.strip()}\n\n"
        f"All section titles and all content must be in {language}.\n"
        f"If a section has no real content, write '{sections['none']}'.\n"
        f"Never apologize or ask for a transcript. If the transcript has no usable work content, return the required sections with '{sections['none']}'.\n\n"
        f"Expected participants: {participants}\n\n"
        "Return exactly these sections:\n"
        f"{sections['participants']}:\n"
        f"{sections['discussed']}:\n"
        f"{sections['decisions']}:\n"
        f"{sections['action_items']}:\n"
        f"{sections['open_questions']}:\n\n"
        f"Transcript:\n{transcript}"
    )


def meeting_summary_sections(language: str) -> dict[str, str]:
    if language.strip().lower() == "russian":
        return {
            "participants": "Участники",
            "discussed": "Обсудили",
            "decisions": "Решения",
            "action_items": "Задачи",
            "open_questions": "Открытые вопросы",
            "none": "Нет",
        }

    return {
        "participants": "Participants",
        "discussed": "Discussed",
        "decisions": "Decisions",
        "action_items": "Action items",
        "open_questions": "Open questions",
        "none": "None",
    }
