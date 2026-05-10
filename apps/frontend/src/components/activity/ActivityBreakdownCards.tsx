import type { AuthorRow, SavedPrefab } from "../../types/dashboard";
import { formatSource } from "../../utils/format";
import { activityColor, formatActivityType, paletteColor, savedFileLabel } from "../../pages/pageHelpers";
import { BreakdownPanel, OvertimeBreakdownPanel, type BreakdownPanelItem } from "./BreakdownPanels";

type ActivityBreakdownCardsProps = {
  author: AuthorRow;
};

export function ActivityBreakdownCards({ author }: ActivityBreakdownCardsProps) {
  const activityMixItems = (author.activityMix ?? []).map((item) => activityMixPanelItem(item.type, item.count, item.percent));
  const savedPrefabItems = (author.savedPrefabs ?? []).map((prefab, index) => savedPrefabPanelItem(prefab, index));
  const overtimeActivityMixItems = (author.overtimeActivityMix ?? []).map((item) => activityMixPanelItem(item.type, item.count, item.percent));
  const overtimeSavedPrefabItems = (author.overtimeSavedPrefabs ?? []).map((prefab, index) => savedPrefabPanelItem(prefab, index));
  const activityMixGroups = (author.activityMixBySource ?? []).map((group) => ({
    source: group.source,
    label: formatSource(group.source),
    totalDisplayValue: formatCompactSourceDuration(group.activeSeconds ?? 0),
    items: group.activityMix.map((item) => activityMixPanelItem(item.type, item.count, item.percent, group.source))
  }));
  const savedPrefabGroups = (author.savedPrefabsBySource ?? []).map((group) => ({
    source: group.source,
    label: formatSource(group.source),
    totalDisplayValue: String(group.totalSaveCount),
    items: group.savedPrefabs.map((prefab, index) => savedPrefabPanelItem(prefab, index, group.source))
  }));
  const overtimeActivityMixGroups = (author.overtimeActivityMixBySource ?? []).map((group) => ({
    source: group.source,
    label: formatSource(group.source),
    totalDisplayValue: formatCompactSourceDuration(group.activeSeconds ?? 0),
    items: group.activityMix.map((item) => activityMixPanelItem(item.type, item.count, item.percent, group.source))
  }));
  const overtimeSavedPrefabGroups = (author.overtimeSavedPrefabsBySource ?? []).map((group) => ({
    source: group.source,
    label: formatSource(group.source),
    totalDisplayValue: String(group.totalSaveCount),
    items: group.savedPrefabs.map((prefab, index) => savedPrefabPanelItem(prefab, index, group.source))
  }));

  return (
    <>
      <BreakdownPanel
        key={`${author.rawAuthor}-activity-mix`}
        title="Activity Mix"
        items={activityMixItems}
        groups={activityMixGroups}
      />
      <BreakdownPanel
        key={`${author.rawAuthor}-saved-files`}
        title="Saved Files"
        items={savedPrefabItems}
        groups={savedPrefabGroups}
      />
      <OvertimeBreakdownPanel
        key={`${author.rawAuthor}-overtime`}
        activityItems={overtimeActivityMixItems}
        savedItems={overtimeSavedPrefabItems}
        activityGroups={overtimeActivityMixGroups}
        savedGroups={overtimeSavedPrefabGroups}
      />
    </>
  );
}

function formatCompactSourceDuration(seconds: number) {
  const totalMinutes = Math.max(0, Math.round(seconds / 60));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;

  if (hours <= 0) {
    return `${minutes}m`;
  }

  return `${hours}h ${String(minutes).padStart(2, "0")}m`;
}

function activityMixPanelItem(type: string, count: number, percent: number, source?: string): BreakdownPanelItem {
  const itemId = source ? `${source}:${type}` : type;

  return {
    id: itemId,
    label: formatActivityType(type),
    value: percent,
    displayValue: `${percent}%`,
    color: activityColor(type || String(count))
  };
}

function savedPrefabPanelItem(prefab: SavedPrefab, index: number, source?: string): BreakdownPanelItem {
  return {
    id: source ? `${source}:${prefab.path || prefab.name}-${index}` : prefab.path || `${prefab.name}-${index}`,
    label: savedFileLabel(prefab),
    value: prefab.saveCount,
    displayValue: String(prefab.saveCount),
    color: paletteColor(index)
  };
}
