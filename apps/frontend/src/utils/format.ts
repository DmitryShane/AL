export function formatDuration(seconds: number) {
  const rounded = Math.max(0, Math.round(seconds));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  return `${hours}h ${String(minutes).padStart(2, "0")}m`;
}

export function formatMinutes(seconds: number) {
  const rounded = Math.max(0, Math.round(seconds));

  if (rounded < 3600) {
    return `${Math.round(rounded / 60)}m`;
  }

  return formatDuration(rounded);
}

export function formatSource(source?: string) {
  if (source === "ual") {
    return "Unity";
  }

  if (source === "bal") {
    return "Blender";
  }

  if (source === "fch") {
    return "FigmaWeb";
  }

  if (source === "fig") {
    return "FigmaApp";
  }

  if (source === "vsc") {
    return "VS Code";
  }

  if (source === "cur") {
    return "Cursor";
  }

  if (source === "dev") {
    return "Device";
  }

  if (source === "telegram") {
    return "Telegram";
  }

  if (source === "discord") {
    return "Discord";
  }

  if (source === "status") {
    return "Status";
  }

  return source ?? "-";
}
