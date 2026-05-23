import { Activity, Box, Smartphone } from "lucide-react";
import cursorIconUrl from "../../assets/cursor-icon.png";

type SourceIconProps = {
  source?: string;
};

export function SourceIcon({ source }: SourceIconProps) {
  if (source === "ual") {
    return <Box size={16} />;
  }

  if (source === "bal") {
    return <BlenderIcon />;
  }

  if (source === "fig" || source === "fch") {
    return <FigmaIcon />;
  }

  if (source === "vsc") {
    return <VSCodeIcon />;
  }

  if (source === "cur") {
    return <CursorIcon />;
  }

  if (source === "codex") {
    return <CodexIcon />;
  }

  if (source === "dev" || source === "dev-ios" || source === "dev-android" || source === "dev-editor") {
    return <Smartphone size={16} />;
  }

  if (source === "telegram") {
    return <TelegramIcon />;
  }

  if (source === "discord") {
    return <DiscordIcon />;
  }

  if (source === "status") {
    return <Activity size={16} />;
  }

  return <Activity size={16} />;
}

function BlenderIcon() {
  return (
    <svg className="source-icon blender-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path className="blender-icon-mark" d="M9.1 3.1c-.5-.5-.5-1.2 0-1.7.5-.5 1.2-.5 1.7 0l4.6 4.4h3.5c.7 0 1.2.5 1.2 1.2s-.5 1.2-1.2 1.2h-1.1l1.9 1.8c1 .9 1.5 2.1 1.5 3.5 0 4-4.3 7.2-9.5 7.2-4.8 0-8.7-2.6-8.7-5.9 0-2.7 2.6-5 6.1-5.7H2.5c-.7 0-1.2-.5-1.2-1.2s.5-1.2 1.2-1.2h8.3L9.1 5.2H5.6c-.7 0-1.2-.5-1.2-1.2s.5-1.2 1.2-1.2h3.1l.4.3Z" />
      <path className="blender-icon-core" d="M12.2 10.7c2.3 0 4.1 1.1 4.1 2.5s-1.8 2.5-4.1 2.5-4.1-1.1-4.1-2.5 1.8-2.5 4.1-2.5Zm0 1.4c-1.2 0-2.2.5-2.2 1.1s1 1.1 2.2 1.1 2.2-.5 2.2-1.1-1-1.1-2.2-1.1Z" />
    </svg>
  );
}

function FigmaIcon() {
  return (
    <svg className="source-icon figma-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path className="figma-red" d="M12 2H8.4a3.4 3.4 0 1 0 0 6.8H12V2Z" />
      <path className="figma-purple" d="M15.6 2H12v6.8h3.6a3.4 3.4 0 1 0 0-6.8Z" />
      <path className="figma-blue" d="M15.4 8.8A3.4 3.4 0 1 1 12 12.2a3.4 3.4 0 0 1 3.4-3.4Z" />
      <path className="figma-green" d="M8.4 15.6H12V19a3.4 3.4 0 1 1-3.6-3.4Z" />
      <path className="figma-orange" d="M8.4 8.8H12v6.8H8.4a3.4 3.4 0 1 1 0-6.8Z" />
    </svg>
  );
}

function VSCodeIcon() {
  return (
    <svg className="source-icon vscode-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M17.9 2.4 7.6 11.8 3.2 8.4 1.8 9.2v5.6l1.4.8 4.4-3.4 10.3 9.4 4.3-1.7V4.1l-4.3-1.7Zm-.4 5.7v7.8l-5.8-3.9 5.8-3.9Z" />
    </svg>
  );
}

function CursorIcon() {
  return <img className="source-icon cursor-icon" src={cursorIconUrl} alt="" aria-hidden="true" />;
}

function CodexIcon() {
  return (
    <svg className="source-icon codex-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path
        className="codex-icon-shell"
        d="M12 2.5 4.5 6.8v8.4l7.5 4.3 7.5-4.3V6.8L12 2.5Z"
      />
      <path
        className="codex-icon-mark"
        d="M8.4 9.1h2.1l1.5 3.7 1.5-3.7h2.1v5.8h-1.7v-3.1l-1.2 3.1h-1.4l-1.2-3.1v3.1H8.4V9.1Z"
      />
      <path className="codex-icon-line" d="M6.9 16.1h10.2" />
      <circle className="codex-icon-node" cx="7.2" cy="16.1" r="1.1" />
    </svg>
  );
}

function TelegramIcon() {
  return (
    <svg className="source-icon telegram-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M21.8 4.1 18.6 20c-.2 1.1-.9 1.4-1.8.9l-5-3.7-2.4 2.3c-.3.3-.5.5-1 .5l.4-5.1 9.3-8.4c.4-.4-.1-.6-.6-.2L6 13.5 1.1 12c-1.1-.3-1.1-1.1.2-1.6L20.4 3c.9-.3 1.7.2 1.4 1.1Z" />
    </svg>
  );
}

function DiscordIcon() {
  return (
    <svg className="source-icon discord-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M19.5 5.4A16 16 0 0 0 15.5 4l-.2.4c1.5.4 2.7 1 3.8 1.8a12.9 12.9 0 0 0-4.7-1.5 13.8 13.8 0 0 0-4.8 0 12.9 12.9 0 0 0-4.7 1.5A11.8 11.8 0 0 1 8.7 4.4L8.5 4a16 16 0 0 0-4 1.4C2 9.1 1.4 12.7 1.7 16.2A16.1 16.1 0 0 0 6.6 18.7l.6-.8a10.4 10.4 0 0 1-1.6-.8l.4-.3c3.1 1.4 6.5 1.4 9.6 0l.4.3c-.5.3-1 .6-1.6.8l.6.8a16.1 16.1 0 0 0 4.9-2.5c.4-4-.7-7.6-2.4-10.8ZM8.5 14.2c-.9 0-1.6-.8-1.6-1.8s.7-1.8 1.6-1.8 1.7.8 1.6 1.8c0 1-.7 1.8-1.6 1.8Zm7 0c-.9 0-1.6-.8-1.6-1.8s.7-1.8 1.6-1.8 1.7.8 1.6 1.8c0 1-.7 1.8-1.6 1.8Z" />
    </svg>
  );
}
