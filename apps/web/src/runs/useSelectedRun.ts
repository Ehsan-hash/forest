import type { Run } from "../api";
import { useLoad } from "../ui";

export function useSelectedRun(projectId: string | undefined, runId: string) {
  const detail = useLoad<Run | null>(
    projectId && runId ? `/runs/${encodeURIComponent(runId)}` : null,
    null,
  );
  const wrongProject = detail.data && detail.data.project_id !== projectId;
  return {
    run: wrongProject ? null : detail.data,
    loading: Boolean(runId) && detail.loading,
    error: wrongProject
      ? "Selected run belongs to another project. Choose a run from this project."
      : detail.error,
    reload: detail.reload,
  };
}
