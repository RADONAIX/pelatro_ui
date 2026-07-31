import { api } from "@/lib/api";

export interface AssuranceTable {
  id: string;
  database_name: string;
  schema_name: string;
  table_name: string;
  label: string;
}

interface AssuranceColumn {
  name: string;
  dataType: string;
  ordinalPosition: number;
}

export async function fetchAssuranceTables(
  assurance: string,
): Promise<AssuranceTable[]> {
  const response = await api.get<AssuranceTable[]>("/metadata/tables", {
    params: { assurance },
  });
  return response.data;
}

/**
 * The AIR/SDP raw and processed file logs.
 *
 * Single-table rules (Sequence, Duplicate) are authored against these rather
 * than the assurance's source schemas — a sequence check asks whether every
 * file arrived, which is a question about the file log, not the records inside
 * it. The same four answer it for every assurance, so this takes no assurance.
 */
export async function fetchFileLogs(): Promise<AssuranceTable[]> {
  const response = await api.get<AssuranceTable[]>("/metadata/file-logs");
  return response.data;
}

export async function fetchFileLogColumns(tableId: string): Promise<string[]> {
  const qualified = tableId.includes(":") ? tableId.split(":")[1] : tableId;
  const separator = qualified.indexOf(".");
  if (separator < 1) return [];
  const schema = qualified.slice(0, separator);
  const table = qualified.slice(separator + 1);
  const response = await api.get<AssuranceColumn[]>(
    `/metadata/file-logs/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/columns`,
  );
  return response.data.map((column) => column.name);
}

export async function fetchTableColumns(
  assurance: string,
  tableId: string,
): Promise<string[]> {
  // Current IDs are database-qualified to disambiguate schemas that exist in
  // both rafms and rafms_rating. Keep accepting legacy schema.table values from
  // rules that were saved before live metadata was database-qualified.
  const databaseSeparator = tableId.indexOf(":");
  const database =
    databaseSeparator >= 0 ? tableId.slice(0, databaseSeparator) : undefined;
  const qualifiedTable =
    databaseSeparator >= 0 ? tableId.slice(databaseSeparator + 1) : tableId;
  const separator = qualifiedTable.indexOf(".");
  if (separator < 1) return [];

  const schema = qualifiedTable.slice(0, separator);
  const table = qualifiedTable.slice(separator + 1);
  const response = await api.get<AssuranceColumn[]>(
    `/metadata/tables/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/columns`,
    { params: { assurance, ...(database ? { database } : {}) } },
  );
  return response.data.map((column) => column.name);
}
