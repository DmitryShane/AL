from __future__ import annotations

from fastapi import Request

from .container import BackendContainer, BackendServices
from .settings import Settings


def get_container(request: Request) -> BackendContainer:
    return request.app.state.container


def get_settings(request: Request) -> Settings:
    return get_container(request).settings


def get_backend_services(request: Request) -> BackendServices:
    return get_container(request).services


def get_auth_service(request: Request) -> BackendServices:
    return get_container(request).auth


def get_author_service(request: Request) -> BackendServices:
    return get_container(request).authors


def get_settings_service(request: Request) -> BackendServices:
    return get_container(request).settings_service


def get_report_service(request: Request) -> BackendServices:
    return get_container(request).report_ingest


def get_summary_service(request: Request) -> BackendServices:
    return get_container(request).activity_summary


def get_calendar_service(request: Request) -> BackendServices:
    return get_container(request).calendar


def get_telegram_service(request: Request) -> BackendServices:
    return get_container(request).telegram_activity


def get_discord_service(request: Request) -> BackendServices:
    return get_container(request).discord_meetings
