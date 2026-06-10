from __future__ import annotations


class TelegramSettingsMixin:
    def get_telegram_online_prompt_delay_seconds(self) -> int:
        settings = self.get_interval_settings()
        return int(settings["telegramOnlinePromptDelayMinutes"]) * 60
