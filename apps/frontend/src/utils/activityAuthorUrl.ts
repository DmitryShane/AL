import type { AuthorRow } from "../types/dashboard";

export type ActivityAuthorSlugLookup = {
  ambiguous: boolean;
  rawAuthor: string | null;
};

export function authorUrlSlug(author: AuthorRow) {
  return author.displayName.trim().replace(/\s+/g, " ").toLocaleLowerCase();
}

export function readActivityAuthorSlugFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const author = params.get("author")?.trim();

  return author ? normalizeAuthorSlug(author) : null;
}

export function writeActivityAuthorSlugToUrl(slug: string) {
  const normalized = normalizeAuthorSlug(slug);

  if (!normalized) {
    return;
  }

  const url = new URL(window.location.href);

  if (url.searchParams.get("author") === normalized) {
    return;
  }

  url.searchParams.set("author", normalized);
  window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
}

export function activityAuthorSlugForRawAuthor(authors: AuthorRow[], rawAuthor: string) {
  const author = authors.find((item) => item.rawAuthor === rawAuthor);
  return author ? authorUrlSlug(author) : null;
}

export function rawAuthorForActivityAuthorSlug(authors: AuthorRow[], slug: string | null): ActivityAuthorSlugLookup {
  const normalized = normalizeAuthorSlug(slug ?? "");

  if (!normalized) {
    return { ambiguous: false, rawAuthor: null };
  }

  const matches = authors.filter((author) => authorUrlSlug(author) === normalized);

  if (matches.length !== 1) {
    return { ambiguous: matches.length > 1, rawAuthor: null };
  }

  return { ambiguous: false, rawAuthor: matches[0].rawAuthor };
}

function normalizeAuthorSlug(value: string) {
  return value.trim().replace(/\s+/g, " ").toLocaleLowerCase();
}
