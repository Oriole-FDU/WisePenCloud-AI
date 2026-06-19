import { getToolName } from "ai";
import type { ToolPart } from "../types/chat";

export type PreviewEntry = {
  key: string;
  value: string;
};

export type PreviewSection = {
  title: string;
  entries: PreviewEntry[];
};

type ToolMetadataRecord = Record<string, unknown>;

const PRIMARY_INPUT_KEYS = [
  "query",
  "url",
  "uri",
  "href",
  "document_url",
  "source_url",
  "repository",
  "path",
  "file_path",
  "filename",
  "title",
  "name",
  "id",
  "source",
  "candidate",
];

function truncate(value: string, maxLength = 96) {
  if (value.length <= maxLength) {
    return value;
  }

  return `${value.slice(0, maxLength - 1)}…`;
}

function formatPreviewValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "null";
  }

  if (typeof value === "string") {
    return truncate(value.replace(/\s+/g, " ").trim(), 120);
  }

  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }

  if (Array.isArray(value)) {
    if (value.length === 0) {
      return "[]";
    }

    if (value.every((item) => ["string", "number", "boolean"].includes(typeof item))) {
      return truncate(value.map((item) => String(item)).join(", "), 120);
    }

    return `${value.length} items`;
  }

  if (typeof value === "object") {
    const keys = Object.keys(value as Record<string, unknown>);
    if (keys.length === 0) {
      return "{}";
    }

    return `{ ${keys.slice(0, 4).join(", ")}${keys.length > 4 ? ", …" : ""} }`;
  }

  return truncate(String(value), 120);
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }

  return value as Record<string, unknown>;
}

function firstDefinedValue(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = record[key];
    if (value !== undefined && value !== null && String(value).trim() !== "") {
      return value;
    }
  }

  return undefined;
}

function deriveInputSummary(part: ToolPart): string {
  const inputRecord = asRecord(part.input);
  const toolName = getToolName(part);

  if (!inputRecord) {
    return toolName;
  }

  if (toolName === "web_search" && typeof inputRecord.query === "string") {
    return inputRecord.query;
  }

  if (toolName === "web_fetch") {
    const fetchTarget = firstDefinedValue(inputRecord, ["url", "href", "uri"]);
    if (fetchTarget) {
      return String(fetchTarget);
    }
  }

  if (toolName === "document_parse") {
    const documentTarget = firstDefinedValue(inputRecord, [
      "filename",
      "file_path",
      "document_url",
      "url",
      "path",
      "title",
    ]);
    if (documentTarget) {
      return String(documentTarget);
    }
  }

  if (toolName === "tool_content_read") {
    const contentTarget = firstDefinedValue(inputRecord, [
      "source",
      "candidate",
      "url",
      "title",
      "name",
      "id",
    ]);
    if (contentTarget) {
      return String(contentTarget);
    }
  }

  const primary = firstDefinedValue(inputRecord, PRIMARY_INPUT_KEYS);
  if (primary !== undefined) {
    return String(primary);
  }

  const [firstKey] = Object.keys(inputRecord);
  return firstKey ? `${firstKey}=${formatPreviewValue(inputRecord[firstKey])}` : toolName;
}

function deriveOutputPreview(output: unknown): unknown {
  const record = asRecord(output);

  if (!record) {
    return output;
  }

  if (record.debug_output !== undefined) {
    return record.debug_output;
  }

  if (record.output !== undefined) {
    return record.output;
  }

  const filteredEntries = Object.entries(record).filter(([key]) => key !== "model_consumed_xml");
  if (filteredEntries.length > 0) {
    return Object.fromEntries(filteredEntries);
  }

  return output;
}

function deriveMetadataEntries(part: ToolPart): PreviewEntry[] {
  const metadata = (part.toolMetadata ?? {}) as ToolMetadataRecord;
  const entries: PreviewEntry[] = [
    { key: "state", value: part.state },
    { key: "tool_call_id", value: part.toolCallId },
  ];

  if (typeof metadata.started_at === "string") {
    entries.push({ key: "started_at", value: metadata.started_at });
  }

  if (typeof metadata.ended_at === "string") {
    entries.push({ key: "ended_at", value: metadata.ended_at });
  }

  if (typeof metadata.duration_ms === "number") {
    entries.push({ key: "duration_ms", value: String(metadata.duration_ms) });
  }

  if (typeof metadata.providerExecuted === "boolean") {
    entries.push({ key: "provider_executed", value: String(metadata.providerExecuted) });
  }

  if ("providerExecuted" in part && typeof part.providerExecuted === "boolean") {
    const hasProviderExecutedEntry = entries.some((entry) => entry.key === "provider_executed");
    if (!hasProviderExecutedEntry) {
      entries.push({ key: "provider_executed", value: String(part.providerExecuted) });
    }
  }

  for (const [key, value] of Object.entries(metadata)) {
    if (["started_at", "ended_at", "duration_ms", "providerExecuted"].includes(key)) {
      continue;
    }

    entries.push({ key, value: formatPreviewValue(value) });
  }

  return entries;
}

function toPreviewEntries(value: unknown, maxEntries = 6): PreviewEntry[] {
  if (value === null || value === undefined) {
    return [];
  }

  if (Array.isArray(value)) {
    return value.slice(0, maxEntries).map((item, index) => ({
      key: `item_${index + 1}`,
      value: formatPreviewValue(item),
    }));
  }

  const record = asRecord(value);
  if (record) {
    return Object.entries(record).slice(0, maxEntries).map(([key, itemValue]) => ({
      key,
      value: formatPreviewValue(itemValue),
    }));
  }

  return [{ key: "value", value: formatPreviewValue(value) }];
}

export function getToolItemLabel(part: ToolPart) {
  return truncate(deriveInputSummary(part), 140);
}

export function getToolItemMeta(part: ToolPart) {
  const inputRecord = asRecord(part.input);

  if (!inputRecord) {
    return undefined;
  }

  const metaValue = firstDefinedValue(inputRecord, [
    "max_results",
    "method",
    "format",
    "page",
    "pages",
    "repository",
  ]);

  return metaValue === undefined ? undefined : formatPreviewValue(metaValue);
}

export function getToolPreviewSections(part: ToolPart): PreviewSection[] {
  const sections: PreviewSection[] = [];
  const inputEntries = toPreviewEntries(part.input);

  sections.push({
    title: "Input",
    entries: inputEntries.length > 0
      ? inputEntries
      : [{ key: "value", value: "empty" }],
  });

  if ("output" in part) {
    const outputEntries = toPreviewEntries(deriveOutputPreview(part.output));
    sections.push({
      title: "Output",
      entries: outputEntries.length > 0
        ? outputEntries
        : [{ key: "value", value: "empty" }],
    });
  } else {
    sections.push({
      title: "Output",
      entries: [{
        key: "error",
        value: formatPreviewValue(part.errorText ?? "unknown error"),
      }],
    });
  }

  sections.push({
    title: "Metadata",
    entries: deriveMetadataEntries(part),
  });

  return sections;
}
