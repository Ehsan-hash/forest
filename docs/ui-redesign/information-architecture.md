# FOREST information architecture

The project has five primary destinations. The sidebar gives access to the research workspace and its outputs; project-independent connections and resource settings remain at the bottom. Existing project URLs remain valid.

| Primary destination | Secondary destinations | Main task |
| --- | --- | --- |
| Workspace | Overview, Graph, Ideas, Theory | Set the Goal, inspect progress, intervene, and develop the persistent research graph |
| Library | References, search/import, saved source details | Collect and inspect research sources |
| Experiments & Results | Experiments, Runs, Data & analysis, Figures | Configure work, inspect execution, compare measured outputs, and inspect evidence |
| Paper | Full manuscript editor | Edit the same manuscript available beside the graph, review bindings, compile and export |
| Files | File tree, editor/preview, source explorer, repository input | Inspect and preserve exact project files and source provenance |
| Settings | Providers, hosts, Agent roles, defaults, system resources; appearance | Configure the application and its execution resources |

The project switcher searches actual projects and changes project scope. The command palette searches actual projects, graph nodes and runs, with keyboard selection and full object IDs in destinations. Missing requested entities receive a recoverable state rather than a substituted object. Public share URLs render selected read-only nodes without requesting owner settings or mounting the owner navigation.

Workspace starts with the graph visible and the run dock collapsed. Selecting a node opens a temporary inspector; its editor keeps the complete unsaved draft and reviewed revision when dismissed. Focus view is a deliberate graph navigation choice. Paper is an optional side panel over the same saved document and shared editing session. At compact widths the manuscript becomes a keyboard-contained overlay; desktop supports resize, pin and expand to the full editor.

High-frequency actions stay in context: select/edit/run a node, inspect the selected run, save/compile/export a manuscript, upload/download a file. Node and branch operations live in their relevant menus with impact preview. Advanced run configuration, checkpoint overrides, repository input, verification, statistics, publication requirements and evaluation protocols have named entries. They are not collected into a single catch-all menu.

Full `project`, `node`, `branch` and `run` identities remain the authority. Workspace query state records node, branch, view and run selection; manuscript visibility uses `paper=1`. Historical or cross-project run requests do not silently become the newest project run. Browser history restores meaningful selection without changing durable graph geometry or revision.

Reachability and preservation are indexed in [feature-inventory.md](feature-inventory.md) and [ui-api-coverage.md](ui-api-coverage.md). Actual journey counts and visual observations are recorded separately; navigation structure alone does not prove lower interaction cost.
