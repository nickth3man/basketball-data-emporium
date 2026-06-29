/**
 * Shared `ColumnMeta` interface — mirrors `basketball_data_emporium.server.models.ColumnMeta`.
 *
 * Both the player-hub and team-hub Pydantic models emit a structurally
 * identical `ColumnMeta` shape (a per-dataset column descriptor), so the
 * shared `<DataTable>` reads from this canonical definition instead of
 * pulling a type out of a specific feature folder. The per-feature
 * `types.ts` files still re-declare `ColumnMeta` for codegen parity with
 * the server models — they are type-equivalent to this interface and
 * can be passed to `<DataTable>` without casts.
 */
export interface ColumnMeta {
  key: string;
  label: string;
  default_visible: boolean;
  numeric: boolean;
}
