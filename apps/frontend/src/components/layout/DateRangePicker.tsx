import { CalendarDays } from "lucide-react";
import { type MouseEvent, useRef, useState } from "react";

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
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [focused, setFocused] = useState(false);

  function updateSelectedDate(date: string) {
    onChange({ startDate: date, endDate: date, preset: "custom" });
  }

  function openDatePicker() {
    const input = inputRef.current;

    if (!input) {
      return;
    }

    input.focus();
    window.getSelection()?.removeAllRanges();
    input.showPicker?.();
  }

  function handleDateControlMouseDown(event: MouseEvent<HTMLDivElement>) {
    event.preventDefault();
    openDatePicker();
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
      <div className={focused ? "date-range-control focused" : "date-range-control"} onMouseDown={handleDateControlMouseDown}>
        <span className="date-range-value">{formatSelectedDate(value.startDate)}</span>
        <CalendarDays className="date-range-icon" size={16} aria-hidden="true" />
        <input
          ref={inputRef}
          className="date-native-input"
          type="date"
          value={value.startDate}
          onChange={(event) => updateSelectedDate(event.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          aria-label="Selected day"
        />
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

function formatSelectedDate(value: string) {
  const [year, month, day] = value.split("-");

  if (!year || !month || !day) {
    return value;
  }

  return `${day}-${month}-${year}`;
}
