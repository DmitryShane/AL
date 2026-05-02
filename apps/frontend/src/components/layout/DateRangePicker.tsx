export type DateRange = {
  startDate: string;
  endDate: string;
  preset: "live" | "yesterday" | "custom";
};

type DateRangePickerProps = {
  value: DateRange;
  onChange: (range: DateRange) => void;
};

export function DateRangePicker({ value, onChange }: DateRangePickerProps) {
  function updateDateRange(next: Pick<DateRange, "startDate" | "endDate">) {
    onChange({ ...next, preset: "custom" });
  }

  return (
    <div className="date-range-group">
      <div className="date-presets" aria-label="Date presets">
        <button className={`live-preset-button ${value.preset === "live" ? "active" : ""}`.trim()} onClick={() => onChange(todayRange())}>
          <span className="live-preset-dot" aria-hidden="true" />
          Live
        </button>
        <button className={value.preset === "yesterday" ? "active" : undefined} onClick={() => onChange(yesterdayRange())}>Yesterday</button>
      </div>
      <div className="date-range-control">
        <input type="date" value={value.startDate} onChange={(event) => updateDateRange({ ...value, startDate: event.target.value })} />
        <span>to</span>
        <input type="date" value={value.endDate} onChange={(event) => updateDateRange({ ...value, endDate: event.target.value })} />
      </div>
    </div>
  );
}

function todayRange(): DateRange {
  const today = toDateInputValue(new Date());
  return { startDate: today, endDate: today, preset: "live" };
}

function yesterdayRange(): DateRange {
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  const date = toDateInputValue(yesterday);
  return { startDate: date, endDate: date, preset: "yesterday" };
}

function toDateInputValue(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
