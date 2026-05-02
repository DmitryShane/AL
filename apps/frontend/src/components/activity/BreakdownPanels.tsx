import type React from "react";

export type BreakdownPanelItem = {
  id: string;
  label: string;
  value: number;
  displayValue: string;
  color: string;
};

export function BreakdownPanel({ title, items }: { title: string; items: BreakdownPanelItem[] }) {
  const total = items.reduce((sum, item) => sum + Math.max(0, item.value), 0);
  const barStyle = {
    "--bar-gradient": segmentedBarGradient(items, total)
  } as React.CSSProperties;

  return (
    <div className="panel breakdown-panel">
      <div className="breakdown-panel-copy">
        <h2>{title}</h2>
        <div className="list breakdown-scroll-list breakdown-scroll-list--compact-rows">
          {items.length ? (
            items.map((item) => (
              <div className="row" key={item.id}>
                <span><i className="row-color" style={{ background: item.color }} />{item.label}</span>
                <strong>{item.displayValue}</strong>
              </div>
            ))
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

export function OvertimeBreakdownPanel({ activityItems, savedItems }: { activityItems: BreakdownPanelItem[]; savedItems: BreakdownPanelItem[] }) {
  return (
    <div className="panel breakdown-panel overtime-breakdown-panel">
      <h2>Overtime</h2>
      <MiniBreakdownList title="Activity Mix" items={activityItems} emptyMessage="No overtime activity yet." />
      <MiniBreakdownList title="Saved Files" items={savedItems} emptyMessage="No overtime saves yet." />
    </div>
  );
}

function MiniBreakdownList({ title, items, emptyMessage }: { title: string; items: BreakdownPanelItem[]; emptyMessage: string }) {
  return (
    <div className="mini-breakdown-list">
      <h3>{title}</h3>
      <div className="list breakdown-scroll-list breakdown-scroll-list--standard-rows">
        {items.length ? (
          items.map((item) => (
            <div className="row" key={item.id}>
              <span><i className="row-color" style={{ background: item.color }} />{item.label}</span>
              <strong>{item.displayValue}</strong>
            </div>
          ))
        ) : (
          <p className="empty">{emptyMessage}</p>
        )}
      </div>
    </div>
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
