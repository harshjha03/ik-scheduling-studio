import type { ExportRow } from "./types";

export const EXPORT_COLUMNS: (keyof ExportRow)[] = [
  "week", "date", "time_ist", "batch", "subject", "sub_specialty", "session_type", "sme_name", "status", "flags",
];

/**
 * Adapter interface for pushing an approved schedule somewhere. The CSV exporter below is the only
 * implementation in this prototype; a Google Sheets adapter would implement the same signature
 * (rows already match the Sheets column layout) using the Sheets API `spreadsheets.values.update`.
 * Live Calendar/Sheets sync is intentionally out of scope.
 */
export interface ScheduleExporter {
  export(rows: ExportRow[], name: string): Promise<void>;
}

function csvCell(v: unknown): string {
  const s = String(v ?? "");
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCsv(rows: ExportRow[]): string {
  const lines = [EXPORT_COLUMNS.join(",")];
  for (const r of rows) lines.push(EXPORT_COLUMNS.map((c) => csvCell(r[c])).join(","));
  return lines.join("\r\n") + "\r\n";
}

export const csvExporter: ScheduleExporter = {
  async export(rows, name) {
    const blob = new Blob([toCsv(rows)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement("a"), { href: url, download: `${name}.csv` });
    a.click();
    URL.revokeObjectURL(url);
  },
};
