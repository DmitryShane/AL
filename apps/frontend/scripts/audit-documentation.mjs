import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const srcRoot = path.join(root, "src");
const contentPath = path.join(srcRoot, "pages", "documentationContent.ts");
const content = readFileSync(contentPath, "utf8");
const source = readSource(srcRoot);
const documentationPage = readFileSync(path.join(srcRoot, "pages", "DocumentationPage.tsx"), "utf8");

const requiredCategories = [
  "overview",
  "navigation-login-date",
  "authors-status",
  "authors-page-complete",
  "activity-metrics",
  "activity-ui-complete",
  "hourly-chart",
  "workday",
  "afk-meeting",
  "reports-sources",
  "analytics-alerts",
  "analytics-calendar-complete",
  "alerts-complete",
  "calendar-complete",
  "calendar-overrides",
  "settings-maintenance",
  "settings-complete",
  "maintenance-dangerous-actions"
];

const requiredTerms = [
  "Login",
  "sidebar",
  "Live",
  "Yesterday",
  "custom",
  "future dates",
  "cached",
  "Search",
  "sorting",
  "Last Report",
  "Plugin",
  "floating strip",
  "Refresh",
  "snapshot preparation",
  "empty day",
  "Unavailable author",
  "OpenAI Stats",
  "archive",
  "summary instructions",
  "message format",
  "Disk Usage",
  "Services",
  "Server reboot",
  "full rebuild",
  "Delete all data",
  "Delete profile",
  "Bulk delete",
  "Device deletes",
  "Site user delete",
  "Range select",
  "Shift",
  "Reasons",
  "Mark days",
  "Clear marks",
  "empty state"
];

const requiredTargets = [
  "date-range-picker",
  "activity-floating-author-strip",
  "activity-refresh-author",
  "activity-snapshot-preparing",
  "activity-empty-day",
  "analytics-author-strip",
  "analytics-charts",
  "analytics-empty-state",
  "calendar-author-filter",
  "calendar-toolbar",
  "calendar-reasons",
  "calendar-stats",
  "calendar-month-grid",
  "calendar-empty-state",
  "settings-general-intervals",
  "settings-disk-usage",
  "settings-services",
  "settings-author-profiles-editor",
  "settings-github-avatars",
  "settings-database-maintenance",
  "settings-publisher-profiles-panel",
  "settings-device-profiles-panel",
  "settings-auto-break-panel",
  "settings-author-redirects-panel",
  "settings-discord-panel",
  "settings-telegram-panel",
  "settings-meeting-notification-panel",
  "settings-meeting-summary-controls",
  "settings-openai-stats",
  "settings-meeting-summary-today",
  "settings-meeting-summary-archive",
  "settings-summary-instructions",
  "settings-telegram-summary-format",
  "settings-snapshots-panel",
  "settings-snapshot-actions",
  "settings-snapshot-status",
  "settings-fake-online-panel",
  "settings-site-users-panel"
];

const categoryIds = [...content.matchAll(/id: "([^"]+)",\n    title:/g)].map((match) => match[1]);
const ruleCounts = categoryIds.map((id, index) => {
  const start = content.indexOf(`id: "${id}"`);
  const next = categoryIds[index + 1];
  const end = next ? content.indexOf(`id: "${next}"`, start) : content.indexOf("\n];", start);
  const chunk = content.slice(start, end);
  return { id, rules: (chunk.match(/title: \{ en:/g) ?? []).length - 1 };
});

const actualTargets = new Set(
  [...source.matchAll(/data-doc-target=(?:\{`([^`]+)`\}|"([^"]+)")/g)]
    .map((match) => match[1] ?? match[2])
    .flatMap((target) => target === "settings-${settingsTab}" ? settingsTargets() : [target])
);

for (const id of categoryIds) {
  actualTargets.add(id);
}

const referencedTargets = [...content.matchAll(/[?&]docTarget=([A-Za-z0-9_-]+)/g)].map((match) => match[1]);
const referencedAnchors = [...content.matchAll(/href: "\/documentation#([^"]+)"/g)].map((match) => match[1]);
const navCategoryIds = [...documentationPage.matchAll(/categoryId: "([^"]+)"/g)].map((match) => match[1]);

const failures = [];

for (const category of requiredCategories) {
  if (!categoryIds.includes(category)) {
    failures.push(`Missing category: ${category}`);
  }
}

for (const category of categoryIds) {
  if (!navCategoryIds.includes(category)) {
    failures.push(`Category is missing from documentation navigation: ${category}`);
  }
}

for (const category of navCategoryIds) {
  if (!categoryIds.includes(category)) {
    failures.push(`Navigation points to missing category: ${category}`);
  }
}

for (const category of navCategoryIds) {
  const occurrences = navCategoryIds.filter((id) => id === category).length;

  if (occurrences > 1) {
    failures.push(`Navigation category appears more than once: ${category}`);
  }
}

const navOrder = navCategoryIds.join(" > ");
const contentOrder = categoryIds.join(" > ");

if (navOrder !== contentOrder) {
  failures.push("Documentation navigation order does not match content section order.");
}

for (const term of requiredTerms) {
  if (!content.toLowerCase().includes(term.toLowerCase())) {
    failures.push(`Missing inventory term: ${term}`);
  }
}

for (const target of requiredTargets) {
  if (!actualTargets.has(target)) {
    failures.push(`Missing UI target: ${target}`);
  }
}

for (const target of referencedTargets) {
  if (!actualTargets.has(target)) {
    failures.push(`Documentation link points to missing docTarget: ${target}`);
  }
}

for (const anchor of referencedAnchors) {
  if (!categoryIds.includes(anchor)) {
    failures.push(`Documentation link points to missing category anchor: ${anchor}`);
  }
}

if (content.includes("```")) {
  failures.push("Documentation content contains a code fence.");
}

for (const { id, rules } of ruleCounts) {
  if (rules < 4) {
    failures.push(`Category ${id} has only ${rules} rules.`);
  }
}

console.log("Documentation category rule counts:");
for (const { id, rules } of ruleCounts) {
  console.log(`- ${id}: ${rules}`);
}

if (failures.length > 0) {
  console.error("\nDocumentation audit failed:");
  for (const failure of failures) {
    console.error(`- ${failure}`);
  }
  process.exit(1);
}

console.log(`\nDocumentation audit passed: ${categoryIds.length} categories, ${referencedTargets.length} docTarget links.`);

function readSource(dir) {
  let result = "";

  for (const entry of readdirSync(dir)) {
    const fullPath = path.join(dir, entry);
    const stat = statSync(fullPath);

    if (stat.isDirectory()) {
      result += readSource(fullPath);
    } else if (/\.(ts|tsx)$/.test(entry)) {
      result += `\n${readFileSync(fullPath, "utf8")}`;
    }
  }

  return result;
}

function settingsTargets() {
  const tabsPath = path.join(srcRoot, "components", "settings", "settingsTabs.ts");
  const tabs = readFileSync(tabsPath, "utf8");
  return [...tabs.matchAll(/\{ key: "([^"]+)"/g)].map((match) => `settings-${match[1]}`);
}
