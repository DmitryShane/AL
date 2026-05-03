import { useEffect, useState } from "react";
import { API_URL } from "../api/client";
import { avatarStyle, initials } from "../utils/author";

export type AuthorAvatarProps = {
  displayName: string;
  authorColor?: string;
  avatarUrl?: string;
  variant?: "default" | "mini";
  className?: string;
};

function resolvedAvatarSrc(href: string): string {
  if (href.startsWith("/api/")) {
    return `${API_URL}${href}`;
  }

  return href;
}

export function AuthorAvatar({ displayName, authorColor, avatarUrl, variant = "default", className = "" }: AuthorAvatarProps) {
  const [imageFailed, setImageFailed] = useState(false);
  const trimmedUrl = (avatarUrl ?? "").trim();
  const imageSrc = trimmedUrl ? resolvedAvatarSrc(trimmedUrl) : "";
  const showImage = Boolean(imageSrc) && !imageFailed;

  useEffect(() => {
    setImageFailed(false);
  }, [trimmedUrl]);

  const suffix = className ? ` ${className}` : "";

  if (showImage) {
    const sizeClass = variant === "mini" ? "author-avatar-image author-avatar-image-mini" : "author-avatar-image";
    return <img className={`${sizeClass}${suffix}`.trim()} src={imageSrc} alt="" onError={() => setImageFailed(true)} />;
  }

  const spanClass = variant === "mini" ? `avatar mini-avatar${suffix}`.trim() : `avatar${suffix}`.trim();

  return (
    <span className={spanClass} style={avatarStyle(authorColor)}>
      {initials(displayName)}
    </span>
  );
}
