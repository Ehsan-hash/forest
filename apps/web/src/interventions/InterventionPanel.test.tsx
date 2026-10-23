import { beforeEach, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { UIContext } from "../ui";
import { InterventionPanel } from "./InterventionPanel";

const reads = vi.hoisted(() => [] as (string | null)[]);
vi.mock("../ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../ui")>();
  return {
    ...actual,
    useLoad: (path: string | null) => {
      reads.push(path);
      const data = path?.includes("/decisions?status=actionable")
        ? [
            {
              id: "pending",
              project_id: "project",
              run_id: "pending-run",
              status: "pending",
              proposed: { tool: "old-pending-tool" },
              created_at: "2020-01-01T00:00:00Z",
            },
            {
              id: "answered",
              project_id: "project",
              run_id: "answered-run",
              status: "accepted",
              proposed: { tool: "saved-answer-tool" },
              can_resume: true,
              created_at: "2020-01-02T00:00:00Z",
            },
            {
              id: "foreign",
              project_id: "other",
              run_id: "foreign-run",
              status: "pending",
              proposed: { tool: "foreign-tool" },
              created_at: "2020-01-01T00:00:00Z",
            },
          ]
        : [];
      return {
        data,
        error: "",
        loading: false,
        reload: vi.fn(),
        setData: vi.fn(),
      };
    },
  };
});

beforeEach(() => reads.splice(0));

it("requests actionable decisions and retains explicit review and continuation controls", () => {
  const html = renderToStaticMarkup(
    <UIContext.Provider
      value={{
        lang: "en",
        theme: "light",
        t: (_zh, en) => en,
        notify: vi.fn(),
        action: async (fn) => fn(),
      }}
    >
      <MemoryRouter>
        <InterventionPanel projectId="project" />
      </MemoryRouter>
    </UIContext.Provider>,
  );
  expect(reads).toContain(
    "/projects/project/decisions?status=actionable&limit=50",
  );
  expect(html).toContain("old-pending-tool");
  expect(html).toContain("Review action");
  expect(html).toContain("saved-answer-tool");
  expect(html).toContain("Continue saved decision");
  expect(html).not.toContain("foreign-tool");
});
