import type React from "react";
import { SourceIcon } from "../icons/SourceIcon";

export type BreakdownPanelItem = {
  id: string;
  label: string;
  value: number;
  displayValue: string;
  color: string;
};

export type BreakdownPanelGroup = {
  source: string;
  label: string;
  totalDisplayValue: string;
  items: BreakdownPanelItem[];
};

export function BreakdownPanel({ title, items, groups = [] }: { title: string; items: BreakdownPanelItem[]; groups?: BreakdownPanelGroup[] }) {
  const total = items.reduce((sum, item) => sum + Math.max(0, item.value), 0);
  const barStyle = {
    "--bar-gradient": segmentedBarGradient(items, total)
  } as React.CSSProperties;

  return (
    <div className="panel breakdown-panel">
      <div className="breakdown-panel-copy">
        <h2>{title}</h2>
        <div className="list breakdown-scroll-list breakdown-scroll-list--compact-rows">
          {groups.length ? (
            <GroupedBreakdownRows groups={groups} />
          ) : items.length ? (
            <BreakdownRows items={items} />
          ) : (
            <p className="empty">No data yet.</p>
          )}
        </div>
      </div>
      <div className="breakdown-bar-row">
        <div className="breakdown-bar" style={barStyle} aria-hidden="true" />
        <strong>{total ? totalDisplayValue(items) : "-"}</strong>
      </div>
    </div>
  );
}

export function OvertimeBreakdownPanel({
  activityItems,
  savedItems,
  activityGroups = [],
  savedGroups = []
}: {
  activityItems: BreakdownPanelItem[];
  savedItems: BreakdownPanelItem[];
  activityGroups?: BreakdownPanelGroup[];
  savedGroups?: BreakdownPanelGroup[];
}) {
  return (
    <div className="panel breakdown-panel overtime-breakdown-panel">
      <h2>Overtime</h2>
      <MiniBreakdownList title="Activity Mix" items={activityItems} groups={activityGroups} emptyMessage="No overtime activity yet." />
      <MiniBreakdownList title="Saved Files" items={savedItems} groups={savedGroups} emptyMessage="No overtime saves yet." />
    </div>
  );
}

function MiniBreakdownList({
  title,
  items,
  groups,
  emptyMessage
}: {
  title: string;
  items: BreakdownPanelItem[];
  groups: BreakdownPanelGroup[];
  emptyMessage: string;
}) {
  return (
    <div className="mini-breakdown-list">
      <h3>{title}</h3>
      <div className="list breakdown-scroll-list breakdown-scroll-list--standard-rows">
        {groups.length ? (
          <GroupedBreakdownRows groups={groups} />
        ) : items.length ? (
          <BreakdownRows items={items} />
        ) : (
          <p className="empty">{emptyMessage}</p>
        )}
      </div>
    </div>
  );
}

function GroupedBreakdownRows({ groups }: { groups: BreakdownPanelGroup[] }) {
  return (
    <>
      {groups.map((group) => (
        <div className="breakdown-source-group" key={group.source}>
          <div className="breakdown-source-heading">
            <span><SourceIcon source={group.source} />{group.label}</span>
            <strong>{group.totalDisplayValue}</strong>
          </div>
          <BreakdownRows items={group.items} />
        </div>
      ))}
    </>
  );
}

function BreakdownRows({ items }: { items: BreakdownPanelItem[] }) {
  return (
    <>
      {items.map((item) => (
        <div className="row" key={item.id}>
          <span><i className="row-color" style={{ background: item.color }} />{item.label}</span>
          <strong>{item.displayValue}</strong>
        </div>
      ))}
    </>
  );
}

function segmentedBarGradient(items: BreakdownPanelItem[], total: number) {
  if (!items.length || total <= 0) {
    return "#edf2f7 0% 100%";
  }

  let cursor = 0;
  const segments = items.map((item) => {
    const start = cursor;
    const width = (Math.max(0, item.value) / total) * 100;
    cursor += width;
    return `${item.color} ${start}% ${cursor}%`;
  });

  return `linear-gradient(to right, ${segments.join(", ")})`;
}

function totalDisplayValue(items: BreakdownPanelItem[]) {
  const percentItems = items.every((item) => item.displayValue.endsWith("%"));

  if (percentItems) {
    return "100%";
  }

  return String(items.reduce((sum, item) => sum + Math.max(0, item.value), 0));
}
